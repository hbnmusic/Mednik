"""MODULE 1 — Indication-level Market Value Driver (MVD) engine.

The list of drivers is not defined in advance. It is discovered from the literature:

  1. Guidelines / consensus statements (primary source) and preference studies (DCE, conjoint, survey) are read
     and every consideration they say should guide treatment choice is extracted with a verbatim quote.
  2. An LLM groups those labels into drivers, however differently the sources word them, and says whether a driver
     corresponds to a measure mednik can compute from trials and labels (otherwise it stays "unmeasured").
  3. Each driver is scored from its sources:
       guideline component  G_k = share of analysed guideline documents that cite it (explicit emphasis counts 1.0,
                                  a mention 0.6)
       preference component P_k = weighted relative importance across preference studies (w_i = √N respondents,
                                  qualitative × 0.4)
       raw_k                    = 0.65 · G_k / max(G) + 0.35 · P_k / max(P)   (a single source type is used alone)
       mvd_impact_score_k       = 0.10 + 0.90 · raw_k / max(raw)
     A driver that no source supports does not exist. Without an LLM key, drivers are limited to considerations
     whose words match a built-in lexicon (method 'lexical_fallback').
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
from ..taxonomy import MVD_BY_KEY, PERSONAS, map_text_to_mvd, mvd_profile
from ..textutil import split_sentences
from .extraction import extract_driver_candidates, group_driver_candidates, lexical_mvd_mentions
from .llm.base import get_provider
from .literature_ingest import doc_text, search_corpus, store_corpus
from .query_terms import epmc_condition, pubmed_condition

log = logging.getLogger("mednik.mvd")

PREF_WEIGHT, GUIDE_WEIGHT = 0.35, 0.65  # guidelines are the primary source
MAX_DRIVERS = 20
GUIDE_TAGS = {"GUIDELINE", "GUIDELINE_LIKE", "CONSENSUS"}
_GUIDE_CONSIDER_RE = re.compile(r"consider|select|choice|choos|priorit|important|key|tolerab|side[-\s]effect|adverse|individuali[sz]|profile|monitor", re.I)


def mvd_pubmed_term(indication_name: str) -> str:
    return (f'{pubmed_condition(indication_name)} AND ("discrete choice experiment"[tiab] OR "discrete choice"[tiab] '
            f'OR "conjoint analysis"[tiab] OR "best-worst"[tiab] OR "treatment preference"[tiab] OR "treatment preferences"[tiab] '
            f'OR "prescriber survey"[tiab] OR "psychiatrist survey"[tiab] OR "clinical practice guideline"[tiab] OR "Practice Guideline"[pt])')


def mvd_epmc_query(indication_name: str) -> str:
    return (f'{epmc_condition(indication_name)} AND ("discrete choice experiment" OR "conjoint analysis" OR "treatment preference" '
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


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:56] or "driver"


def measure_key_of(d: MarketValueDriver) -> str | None:
    """Key of the trial / label measure a driver corresponds to, if any."""
    comp = d.component_scores_json or {}
    if "_measure_key" in comp:
        return comp["_measure_key"] or None
    return d.attribute_key if d.attribute_key in MVD_BY_KEY else None  # rows written before drivers were discovered


def _preference_index(items: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, set[str]]]:
    """Weighted preference index per driver from items that carry ``canonical_attribute`` = driver key."""
    by_study: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in items:
        by_study[a["source_id"]].append(a)
    num: dict[str, float] = defaultdict(float)
    den: dict[str, float] = defaultdict(float)
    ids: dict[str, set[str]] = defaultdict(set)
    for sid, rows in by_study.items():
        shares, k_attr, quant = _study_shares(rows)
        n_resp = next((a.get("_n_respondents") for a in rows if a.get("_n_respondents")), None)
        w = (math.sqrt(n_resp) if n_resp else 5.0) * (1.0 if quant else 0.4)
        for key, sh in shares.items():
            num[key] += w * sh * k_attr
            den[key] += w
            ids[key].add(sid)
    return {k: num[k] / den[k] for k in num if den[k]}, ids


def _quote(a: dict[str, Any], kind: str) -> dict[str, Any]:
    return {"source_id": a["source_id"], "quote": a["verbatim_quote"][:400], "value": a.get("importance_value"), "metric": a.get("importance_metric"),
            "design": a.get("_study_design"), "attribute_text": a.get("attribute_text"), "kind": kind}


async def _discover_llm(guide_in: list[tuple[str, str]], pref_in: list[tuple[str, str]], measurable: list[tuple[str, str]],
                        indication: Indication, say: Callable[[str], None]) -> list[dict[str, Any]]:
    docs = [(sid, t, "guideline") for sid, t in guide_in] + [(sid, t, "preference") for sid, t in pref_in]
    cands = await extract_driver_candidates(docs, indication.name)
    say(f"MVD: {len(cands)} treatment-selection considerations extracted from {len(guide_in)} guideline and {len(pref_in)} preference documents")
    if not cands:
        return []
    # one label per distinct wording keeps the grouping request small; guideline wording comes first
    uniq: dict[str, list[int]] = {}
    labels: list[tuple[int, str, str]] = []
    order = sorted(range(len(cands)), key=lambda i: (cands[i]["_kind"] != "guideline", i))
    for i in order:
        txt = re.sub(r"\s+", " ", cands[i]["attribute_text"]).strip()
        low = txt.lower()
        if low not in uniq:
            if len(labels) >= 320:
                continue
            uniq[low] = []
            labels.append((len(labels), txt[:140], cands[i]["_kind"]))
        uniq[low].append(i)
    label_members = {lid: uniq[txt.lower()] for lid, txt, _ in labels}
    clusters = await group_driver_candidates(labels, indication.name, measurable)
    if not clusters:  # grouping unavailable: fall back to the lexicon so the run still yields drivers
        by_key: dict[str, list[int]] = defaultdict(list)
        for lid, txt, _ in labels:
            k = map_text_to_mvd(txt, indication.key)
            if k:
                by_key[k] += label_members[lid]
        clusters = [{"name": MVD_BY_KEY[k].name, "category": MVD_BY_KEY[k].category, "measure_key": k, "member_ids": [], "_idx": v}
                    for k, v in by_key.items()]
    specs = []
    for c in clusters:
        idx = c.get("_idx") or [i for lid in c["member_ids"] for i in label_members.get(lid, [])]
        if idx:
            specs.append({"name": c["name"], "category": c["category"], "measure_key": c["measure_key"], "cands": [cands[i] for i in idx]})
    return specs


def _lexical_specs(guide_docs: list[Document], pref_in: list[tuple[str, str]], indication: Indication) -> list[dict[str, Any]]:
    pref = lexical_mvd_mentions(pref_in, indication.key)
    gcomp, gquotes = guideline_component(guide_docs, mvd_profile(indication.key))
    specs = []
    for k in mvd_profile(indication.key):
        cands = [a for a in pref if a["canonical_attribute"] == k]
        if not cands and not gcomp.get(k):
            continue
        for a in cands:
            a["_kind"] = "preference"
        specs.append({"fixed_key": k, "name": MVD_BY_KEY[k].name, "category": MVD_BY_KEY[k].category, "measure_key": k, "cands": cands,
                      "lex_guideline": gcomp.get(k, 0.0), "lex_quotes": gquotes.get(k, [])})
    return specs


async def compute_mvds(session: Session, indication: Indication, *, say: Callable[[str], None] = log.info,
                       max_docs: int | None = None) -> list[MarketValueDriver]:
    st = get_settings()
    pref_docs = session.execute(select(Document).join(DocumentLink).where(
        DocumentLink.indication_id == indication.id, DocumentLink.role == "mvd_preference")).scalars().unique().all()
    guide_docs = session.execute(select(Document).join(DocumentLink).where(
        DocumentLink.indication_id == indication.id, DocumentLink.role == "guideline")).scalars().unique().all()
    guide_docs = [d for d in guide_docs if (set(d.evidence_tags_json or []) & GUIDE_TAGS) or d.source_type == "pdf"]
    pref_docs = [d for d in pref_docs if d not in guide_docs and (set(d.evidence_tags_json or []) & {"DCE", "SURVEY"} or re.search(
        r"preference|choice|conjoint|survey|attribute", f"{d.title} {d.abstract}", re.I))]
    say(f"MVD: {len(guide_docs)} guideline documents and {len(pref_docs)} preference studies available for {indication.name}")

    llm_used = get_provider() is not None
    # discrete-choice / conjoint studies first, then the newest; guidelines newest first (caps limit LLM quota only)
    pref_docs.sort(key=lambda d: (-len(set(d.evidence_tags_json or []) & {"DCE", "SURVEY"}), -(d.year or 0)))
    guide_docs.sort(key=lambda d: -(d.year or 0))
    pref_cap = int(max_docs or (st.llm_max_mvd_docs if llm_used else 40))
    guide_cap = st.llm_max_driver_guideline_docs if llm_used else 40
    guide_use, pref_use = guide_docs[:guide_cap], pref_docs[:pref_cap]
    guide_in = [(d.pmid or d.source_id, doc_text(d, 40000)) for d in guide_use if d.abstract or d.full_text]
    pref_in = [(d.pmid or d.source_id, doc_text(d, 30000)) for d in pref_use if d.abstract or d.full_text]
    measurable = [(k, MVD_BY_KEY[k].name) for k in mvd_profile(indication.key)]

    specs = await _discover_llm(guide_in, pref_in, measurable, indication, say) if llm_used else _lexical_specs(guide_use, pref_in, indication)

    # ---- per-driver components --------------------------------------------------------
    used_keys: set[str] = set()
    items: list[dict[str, Any]] = []
    for sp in specs:
        base = sp.get("fixed_key") or _slug(sp["name"])
        key, n = base, 2
        while key in used_keys:
            key, n = f"{base[:52]}_{n}", n + 1
        used_keys.add(key)
        sp["key"] = key
        for a in sp["cands"]:
            if a["_kind"] == "preference":
                items.append({**a, "canonical_attribute": key})
    pref_idx, pref_ids = _preference_index(items)
    n_guide = max(1, len(guide_in) if llm_used else len(guide_use))
    for sp in specs:
        g_docs: dict[str, float] = {}
        for a in sp["cands"]:
            if a["_kind"] == "guideline":
                w = 1.0 if a.get("importance_metric") == "STRONG_EMPHASIS" else 0.6
                g_docs[a["source_id"]] = max(g_docs.get(a["source_id"], 0.0), w)
        sp["g"] = sum(g_docs.values()) / n_guide if llm_used else sp.get("lex_guideline", 0.0)
        sp["g_docs"] = len(g_docs) if llm_used else int(round(sp.get("lex_guideline", 0.0) * n_guide))
        sp["p"] = pref_idx.get(sp["key"])
        sp["p_ids"] = sorted(pref_ids.get(sp["key"], set()))

    gmax = max((sp["g"] for sp in specs), default=0.0)
    pmax = max((sp["p"] or 0.0 for sp in specs), default=0.0)
    wg, wp = (GUIDE_WEIGHT, PREF_WEIGHT) if gmax and pmax else ((1.0, 0.0) if gmax else (0.0, 1.0))
    for sp in specs:
        sp["raw"] = wg * (sp["g"] / gmax if gmax else 0.0) + wp * ((sp["p"] or 0.0) / pmax if pmax else 0.0)
    specs = sorted((sp for sp in specs if sp["raw"] > 0), key=lambda sp: -sp["raw"])[:MAX_DRIVERS]
    rmax = max((sp["raw"] for sp in specs), default=0.0)

    existing = {m.attribute_key: m for m in session.execute(select(MarketValueDriver).where(
        MarketValueDriver.indication_id == indication.id)).scalars().all()}
    out: list[MarketValueDriver] = []
    for sp in specs:
        score = 0.10 + 0.90 * sp["raw"] / rmax if rmax else 0.10
        has_g, has_p = sp["g"] > 0, bool(sp["p"])
        prefix = "llm" if llm_used else "lexical"
        method = f"{prefix}_guideline+preference" if has_g and has_p else f"{prefix}_guideline" if has_g else f"{prefix}_preference"
        row = existing.get(sp["key"]) or MarketValueDriver(indication_id=indication.id, attribute_key=sp["key"])
        row.attribute_name, row.category = sp["name"], sp["category"]
        row.mvd_impact_score = round(score, 4)
        row.method = method
        quotes = [_quote(a, a["_kind"]) for a in sorted(sp["cands"], key=lambda a: (a["_kind"] != "guideline", a.get("importance_metric") != "STRONG_EMPHASIS"))]
        quotes = quotes[:6] + [q for q in sp.get("lex_quotes", [])][:3]
        row.evidence_count = sp["g_docs"] + len(sp["p_ids"])
        row.component_scores_json = {
            "_measure_key": sp["measure_key"], "guideline_share": round(sp["g"], 4), "guideline_norm": round(sp["g"] / gmax, 4) if gmax else None,
            "n_guideline_citing": sp["g_docs"], "n_guideline_docs": n_guide, "preference_index": round(sp["p"], 4) if sp["p"] else None,
            "preference_norm": round(sp["p"] / pmax, 4) if sp["p"] and pmax else None, "n_pref_studies": len(sp["p_ids"]),
            "weight_guideline": wg, "weight_preference": wp,
        }
        row.supporting_pmids_json = sorted({a["source_id"] for a in sp["cands"]} | {q["source_id"] for q in sp.get("lex_quotes", [])})
        row.verbatim_evidence = quotes
        if row.id is None:
            session.add(row)
        out.append(row)
    for k, row in existing.items():
        if k not in {sp["key"] for sp in specs}:
            session.delete(row)
    session.flush()
    if out:
        say("MVD drivers (guidelines weighted 0.65, preference studies 0.35): " + "; ".join(f"{d.attribute_name} {d.mvd_impact_score:.2f}" for d in sorted(out, key=lambda d: -d.mvd_impact_score)[:8]))
    else:
        say("MVD: no guideline or preference source named a treatment-selection consideration, so no drivers were created.")
    return out


def effective_scores(drivers: list[MarketValueDriver], persona: str = "evidence", overrides: dict[str, float] | None = None) -> dict[str, dict[str, Any]]:
    mult = PERSONAS.get(persona, PERSONAS["evidence"])["multipliers"]
    out = {}
    for d in drivers:
        base = d.mvd_impact_score
        persona_score = min(1.0, max(0.10, base * mult.get(measure_key_of(d) or d.attribute_key, 1.0)))
        score = persona_score
        src = "evidence" if persona == "evidence" else "persona"
        if d.user_override_score is not None:
            score, src = d.user_override_score, "saved_override"
        if overrides and d.attribute_key in overrides:
            score, src = float(overrides[d.attribute_key]), "session_override"
        out[d.attribute_key] = {"score": round(min(1.0, max(0.10, score)), 4), "evidence_score": base,
                                "persona_score": round(persona_score, 4), "source": src}
    return out


def measure_scores(drivers: list[MarketValueDriver], eff: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Highest effective score among the drivers that correspond to each measurable endpoint group."""
    out: dict[str, float] = {}
    for d in drivers:
        mk = measure_key_of(d)
        if mk:
            out[mk] = max(out.get(mk, 0.0), eff[d.attribute_key]["score"])
    return out


def serialize_driver(d: MarketValueDriver, eff: dict[str, Any] | None = None) -> dict[str, Any]:
    mk = measure_key_of(d)
    mdef = MVD_BY_KEY.get(mk) if mk else None
    comps = {k: v for k, v in (d.component_scores_json or {}).items() if not k.startswith("_")}
    return {
        "id": d.id, "key": d.attribute_key, "name": d.attribute_name, "category": d.category,
        "evidence_score": d.mvd_impact_score, "override_score": d.user_override_score,
        "effective_score": (eff or {}).get("score", d.effective_score), "score_source": (eff or {}).get("source", "evidence"),
        "method": d.method, "evidence_count": d.evidence_count, "components": comps,
        "supporting_ids": d.supporting_pmids_json or [], "verbatim_evidence": d.verbatim_evidence or [],
        "kind": mdef.kind if mdef else "unmeasured", "rows": list(mdef.rows) if mdef else [],
        "measure_key": mk, "measure_name": mdef.name if mdef else None,
    }
