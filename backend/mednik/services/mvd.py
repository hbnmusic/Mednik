"""MODULE 1 — Indication-level Market Value Driver (MVD) engine.

Pipeline: literature query (DCE / conjoint / preference / prescriber survey / guideline)
→ citation-validated attribute extraction → per-study normalisation → evidence
weighting → ``mvd_impact_score`` in [0.10, 1.00].

Scoring (documented in the UI):
  per-study share   s_ik  = importance of attribute k within study i (relative-importance %,
                            |coefficient|, Borda-converted rank, % rating important), summed
                            over attributes mapped to the same driver
  relative index    r_ik  = s_ik × K_i                (K_i = attributes in study i; 1.0 = average)
  preference P_k          = Σ_i w_i r_ik / Σ_i w_i   (w_i = √N respondents, qualitative × 0.4)
  guideline G_k           = share of ingested guideline documents citing driver k as a
                            selection consideration
  raw_k                   = 0.75 · P_k / max(P) + 0.25 · G_k
  mvd_impact_score_k      = 0.10 + 0.90 · raw_k / max(raw)   (drivers with no evidence = 0.10 floor)
Persona multipliers and slider overrides are applied on read, never written over the evidence score.
"""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Document, DocumentLink, Indication, MarketValueDriver
from ..taxonomy import MVD_BY_KEY, PERSONAS, mvd_profile
from ..textutil import split_sentences
from .extraction import extract_mvd_preferences
from .llm.base import get_provider
from .literature_ingest import doc_text, search_corpus, store_corpus

log = logging.getLogger("mednik.mvd")

PREF_WEIGHT, GUIDE_WEIGHT = 0.75, 0.25
_GUIDE_CONSIDER_RE = re.compile(r"consider|select|choice|choos|priorit|important|key|tolerab|side[-\s]effect|adverse|individuali[sz]|profile|monitor", re.I)


def mvd_pubmed_term(indication_name: str) -> str:
    return (f'("{indication_name}"[tiab] OR "{indication_name}"[mh]) AND ("discrete choice experiment"[tiab] OR "discrete choice"[tiab] '
            f'OR "conjoint analysis"[tiab] OR "best-worst"[tiab] OR "treatment preference"[tiab] OR "treatment preferences"[tiab] '
            f'OR "prescriber survey"[tiab] OR "psychiatrist survey"[tiab] OR "clinical practice guideline"[tiab] OR "Practice Guideline"[pt])')


def mvd_epmc_query(indication_name: str) -> str:
    return (f'"{indication_name}" AND ("discrete choice experiment" OR "conjoint analysis" OR "treatment preference" '
            f'OR "prescriber survey" OR "clinical practice guideline")')


async def ingest_mvd_literature(session: Session, indication: Indication, *, max_pubmed: int, max_epmc: int,
                                force_refresh: bool = False, say: Callable[[str], None] = log.info) -> list[Document]:
    corpus = await search_corpus(mvd_pubmed_term(indication.name), mvd_epmc_query(indication.name), max_pubmed=max_pubmed,
                                 max_epmc=max_epmc, force_refresh=force_refresh, say=say)
    docs = store_corpus(session, corpus, "mvd_preference", None, indication.id)
    for d in docs:
        tags = set(d.evidence_tags_json or [])
        if tags & {"GUIDELINE", "GUIDELINE_LIKE", "CONSENSUS"}:
            from .literature_ingest import link

            link(session, d, "guideline", None, indication.id)
    session.flush()
    return docs


def _study_shares(attrs: list[dict[str, Any]]) -> tuple[dict[str, float], int, bool]:
    """Return ({driver: share}, K attributes, quantitative?) for one study."""
    quant = [a for a in attrs if a.get("importance_value") is not None and a.get("importance_metric") in
             ("RELATIVE_IMPORTANCE_PCT", "COEFFICIENT", "RANK", "PERCENT_RATING_IMPORTANT", "WILLINGNESS_TO_TRADE")]
    shares: dict[str, float] = defaultdict(float)
    if quant:
        metric = max(("RELATIVE_IMPORTANCE_PCT", "COEFFICIENT", "RANK", "PERCENT_RATING_IMPORTANT", "WILLINGNESS_TO_TRADE"),
                     key=lambda m: sum(1 for a in quant if a["importance_metric"] == m))
        rows = [a for a in quant if a["importance_metric"] == metric]
        if metric == "RANK":
            kmax = max(a["importance_value"] for a in rows)
            vals = [(a["canonical_attribute"], kmax - a["importance_value"] + 1) for a in rows]
        else:
            vals = [(a["canonical_attribute"], abs(a["importance_value"])) for a in rows]
        tot = sum(v for _, v in vals) or 1.0
        for k, v in vals:
            shares[k] += v / tot
        return dict(shares), max(1, len({k for k, _ in vals})), True
    qual = [a for a in attrs if a.get("importance_metric") in ("QUALITATIVE_HIGH", "QUALITATIVE_MENTION")]
    for a in qual:
        shares[a["canonical_attribute"]] += 1.0 if a["importance_metric"] == "QUALITATIVE_HIGH" else 0.5
    tot = sum(shares.values()) or 1.0
    return {k: v / tot for k, v in shares.items()}, max(1, len(shares)), False


def guideline_component(docs: list[Document], profile: tuple[str, ...]) -> tuple[dict[str, float], dict[str, list[dict[str, str]]]]:
    if not docs:
        return {}, {}
    hits: dict[str, int] = defaultdict(int)
    quotes: dict[str, list[dict[str, str]]] = defaultdict(list)
    for d in docs:
        seen = set()
        for s in split_sentences(doc_text(d, 40000)):
            if not _GUIDE_CONSIDER_RE.search(s):
                continue
            low = s.lower()
            for k in profile:
                if k in seen:
                    continue
                if any(term in low for term in MVD_BY_KEY[k].lexicon):
                    seen.add(k)
                    hits[k] += 1
                    if len(quotes[k]) < 3:
                        quotes[k].append({"source_id": d.pmid or d.source_id, "quote": s[:400], "kind": "guideline"})
    return {k: hits[k] / len(docs) for k in profile}, quotes


async def compute_mvds(session: Session, indication: Indication, *, say: Callable[[str], None] = log.info,
                       max_docs: int | None = None) -> list[MarketValueDriver]:
    profile = mvd_profile(indication.key)
    pref_docs = session.execute(select(Document).join(DocumentLink).where(
        DocumentLink.indication_id == indication.id, DocumentLink.role == "mvd_preference")).scalars().unique().all()
    guide_docs = session.execute(select(Document).join(DocumentLink).where(
        DocumentLink.indication_id == indication.id, DocumentLink.role == "guideline")).scalars().unique().all()
    pref_docs = [d for d in pref_docs if set(d.evidence_tags_json or []) & {"DCE", "SURVEY"} or re.search(
        r"preference|choice|conjoint|survey|attribute", f"{d.title} {d.abstract}", re.I)]
    say(f"MVD: {len(pref_docs)} preference studies, {len(guide_docs)} guideline documents for {indication.name}")

    # Cap the documents sent to the LLM: discrete-choice / conjoint studies first, then the newest.
    pref_docs.sort(key=lambda d: (-len(set(d.evidence_tags_json or []) & {"DCE", "SURVEY"}), -(d.year or 0)))
    st = get_settings()
    cap = int(max_docs or (st.llm_max_mvd_docs if st.llm_enabled else 40))
    docs_in = [(d.pmid or d.source_id, doc_text(d, 30000)) for d in pref_docs[:cap] if d.abstract or d.full_text]
    llm_used = get_provider() is not None
    attrs = await extract_mvd_preferences(docs_in, indication.key, indication.name) if docs_in else []
    by_study: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in attrs:
        by_study[a["source_id"]].append(a)

    num: dict[str, float] = defaultdict(float)
    den: dict[str, float] = defaultdict(float)
    evid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pmids: dict[str, set[str]] = defaultdict(set)
    for sid, items in by_study.items():
        shares, k_attr, quant = _study_shares(items)
        n_resp = next((a.get("_n_respondents") for a in items if a.get("_n_respondents")), None)
        w = math.sqrt(n_resp) if n_resp else 5.0
        w *= 1.0 if quant else 0.4
        for key, sh in shares.items():
            num[key] += w * sh * k_attr
            den[key] += w
            pmids[key].add(sid)
        for a in items:
            if len(evid[a["canonical_attribute"]]) < 6:
                evid[a["canonical_attribute"]].append({"source_id": sid, "quote": a["verbatim_quote"][:400],
                                                       "value": a.get("importance_value"), "metric": a.get("importance_metric"),
                                                       "design": a.get("_study_design"), "attribute_text": a.get("attribute_text")})
    pref = {k: num[k] / den[k] for k in profile if den.get(k)}
    gcomp, gquotes = guideline_component(guide_docs, profile)
    pmax = max(pref.values(), default=0.0) or 1.0
    raw: dict[str, float] = {}
    for k in profile:
        p = pref.get(k)
        g = gcomp.get(k, 0.0)
        if p is None and g == 0:
            continue
        raw[k] = PREF_WEIGHT * ((p or 0.0) / pmax) + GUIDE_WEIGHT * g
    rmax = max(raw.values(), default=0.0)

    existing = {m.attribute_key: m for m in session.execute(select(MarketValueDriver).where(
        MarketValueDriver.indication_id == indication.id)).scalars().all()}
    out = []
    for k in profile:
        mdef = MVD_BY_KEY[k]
        if k in raw and rmax > 0:
            score = 0.10 + 0.90 * raw[k] / rmax
            if k in pref:
                method = "llm_preference_extraction" if llm_used else "lexical_fallback"
            else:
                method = "guideline_only"
        else:
            score, method = 0.10, "evidence_floor"
        row = existing.get(k) or MarketValueDriver(indication_id=indication.id, attribute_key=k)
        row.attribute_name, row.category = mdef.name, mdef.category
        row.mvd_impact_score = round(score, 4)
        row.method = method
        row.evidence_count = len(pmids.get(k, ())) + int(gcomp.get(k, 0) * len(guide_docs))
        row.component_scores_json = {"preference_index": round(pref[k], 4) if k in pref else None,
                                     "preference_norm": round(pref[k] / pmax, 4) if k in pref else None,
                                     "guideline_share": round(gcomp.get(k, 0.0), 4), "n_pref_studies": len(pmids.get(k, ())),
                                     "n_guideline_docs": len(guide_docs)}
        row.supporting_pmids_json = sorted(pmids.get(k, set()) | {q["source_id"] for q in gquotes.get(k, [])})
        row.verbatim_evidence = evid.get(k, []) + gquotes.get(k, [])
        if k not in existing:
            session.add(row)
        out.append(row)
    for k, row in existing.items():
        if k not in profile:
            session.delete(row)
    session.flush()
    return out


def effective_scores(drivers: list[MarketValueDriver], persona: str = "evidence", overrides: dict[str, float] | None = None) -> dict[str, dict[str, Any]]:
    mult = PERSONAS.get(persona, PERSONAS["evidence"])["multipliers"]
    out = {}
    for d in drivers:
        base = d.mvd_impact_score
        persona_score = min(1.0, max(0.10, base * mult.get(d.attribute_key, 1.0)))
        score = persona_score
        src = "evidence" if persona == "evidence" else "persona"
        if d.user_override_score is not None:
            score, src = d.user_override_score, "saved_override"
        if overrides and d.attribute_key in overrides:
            score, src = float(overrides[d.attribute_key]), "session_override"
        out[d.attribute_key] = {"score": round(min(1.0, max(0.10, score)), 4), "evidence_score": base,
                                "persona_score": round(persona_score, 4), "source": src}
    return out


def serialize_driver(d: MarketValueDriver, eff: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": d.id, "key": d.attribute_key, "name": d.attribute_name, "category": d.category,
        "evidence_score": d.mvd_impact_score, "override_score": d.user_override_score,
        "effective_score": (eff or {}).get("score", d.effective_score), "score_source": (eff or {}).get("source", "evidence"),
        "method": d.method, "evidence_count": d.evidence_count, "components": d.component_scores_json,
        "supporting_ids": d.supporting_pmids_json or [], "verbatim_evidence": d.verbatim_evidence or [],
        "kind": MVD_BY_KEY[d.attribute_key].kind, "rows": list(MVD_BY_KEY[d.attribute_key].rows),
    }
