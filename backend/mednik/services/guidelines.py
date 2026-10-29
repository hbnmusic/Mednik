"""ADJACENCY D — Clinical guideline & consensus trajectory tracker.

* Ingests guideline / consensus publications (PubMed + Europe PMC, open-access full text where available,
  plus guideline PDFs dropped by the user) and extracts citation-validated positioning per asset.
* Reports each asset's Guideline Tier Status (or "Unlisted / approved post-guideline").
* Gap-to-Guideline Linker: checks the asset's evidence heatmap against an editable rule set and links
  the missing / low-validity cells that block a higher tier.

The rule set below is mednik's default heuristic, modelled on common level-of-evidence frameworks
(replicated double-blind RCTs for top-tier efficacy, maintenance RCTs for maintenance positioning,
tolerability considered for line of therapy). It is configuration, not extracted evidence — edit it to
mirror the exact criteria of the guideline body you are targeting.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from lxml import etree
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..clients import EuropePmcClient
from ..models import Asset, Document, DocumentLink, GuidelineRecommendation, Indication
from ..taxonomy import INDICATION_BY_KEY, asset_terms, column_label, text_mentions_asset
from ..textutil import clean_ws
from .extraction import extract_guideline_recs
from .literature_ingest import doc_text, search_corpus, store_corpus

log = logging.getLogger("mednik.guidelines")

LINE_RANK = {"FIRST": 0, "SECOND": 1, "THIRD": 2, "NOT_RECOMMENDED": 3, "INSUFFICIENT": 4, "UNCLEAR": 5}
LINE_LABEL = {"FIRST": "1st-line", "SECOND": "2nd-line", "THIRD": "3rd-line", "NOT_RECOMMENDED": "Not recommended",
              "INSUFFICIENT": "Insufficient evidence", "UNCLEAR": "Mentioned (line unclear)"}
BODY_RE = re.compile(r"\b(CANMAT(?:/ISBD)?|ISBD|WFSBP|NICE|APA|American Psychiatric Association|VA/DoD|BAP|British Association for Psychopharmacology|RANZCP|Maudsley|SFBSP|Florida)\b", re.I)

DEFAULT_RULES: list[dict[str, Any]] = [
    {"id": "acute_efficacy", "label": "Replicated acute placebo-controlled efficacy (USPI §14 or ≥2 positive Phase 3 primaries)",
     "rows": "primary", "timepoint": "acute", "min_validity": 5, "target": "FIRST"},
    {"id": "maintenance", "label": "Randomized maintenance / relapse-prevention RCT (≥26 weeks)",
     "rows": ["RELAPSE"], "timepoint": "maintenance", "min_validity": 4, "target": "FIRST"},
    {"id": "tolerability", "label": "No major tolerability signal on weight / akathisia / EPS / sedation (excess incidence < 10 pp)",
     "rows": ["WEIGHT", "AKATHISIA", "EPS", "SOMNOLENCE"], "timepoint": "acute", "max_harm_pp": 10.0, "target": "FIRST"},
    {"id": "function", "label": "Functional / quality-of-life outcome data (validity ≥ L3)",
     "rows": ["PSP", "SDS", "FAST", "QOL"], "timepoint": "any", "min_validity": 3, "target": "PREFERRED"},
    {"id": "comparative", "label": "Head-to-head or network meta-analysis comparative data",
     "rows": "any", "check": "comparative", "target": "PREFERRED"},
]


def guideline_pubmed_term(indication_name: str) -> str:
    return (f'("{indication_name}"[tiab] OR "{indication_name}"[mh]) AND ("Practice Guideline"[pt] OR "Guideline"[pt] OR guideline*[ti] '
            f'OR CANMAT[tiab] OR WFSBP[tiab] OR consensus[ti] OR "treatment algorithm"[ti] OR recommendations[ti]) AND 2012:3000[dp]')


def guideline_epmc_query(indication_name: str) -> str:
    return f'"{indication_name}" AND (TITLE:"guideline" OR TITLE:"guidelines" OR TITLE:"consensus" OR CANMAT OR WFSBP) AND PUB_YEAR:[2012 TO 2030]'


def jats_to_text(xml: str) -> str:
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False, no_network=True)
    root = etree.fromstring(xml.encode("utf-8"), parser)
    if root is None:
        return ""
    parts: list[str] = []
    body = root.find(".//body")
    if body is not None:
        for el in body.iter():
            if el.tag in ("p", "title"):
                parts.append(clean_ws(" ".join(el.itertext())))
    for tw in root.iter("table-wrap"):
        cap = clean_ws(" ".join(tw.find("caption").itertext())) if tw.find("caption") is not None else ""
        parts.append(f"[TABLE] {cap}")
        for tr in tw.iter("tr"):
            cells = [clean_ws(" ".join(c.itertext())) for c in tr if c.tag in ("td", "th")]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(p for p in parts if p)


async def ingest_guidelines(session: Session, indication: Indication, assets: list[Asset], *, max_docs: int = 12,
                            force_refresh: bool = False, say: Callable[[str], None] = log.info) -> dict[str, Any]:
    corpus = await search_corpus(guideline_pubmed_term(indication.name), guideline_epmc_query(indication.name), max_pubmed=60, max_epmc=60,
                                 force_refresh=force_refresh, say=say)
    docs = store_corpus(session, corpus, "guideline", None, indication.id)
    docs = [d for d in docs if set(d.evidence_tags_json or []) & {"GUIDELINE", "CONSENSUS", "GUIDELINE_LIKE"}]
    docs.sort(key=lambda d: (-(d.year or 0)))
    docs = docs[:max_docs]
    ep = EuropePmcClient()
    n_full = 0
    for d in docs:
        if d.pmcid and d.is_open_access and not d.full_text:
            try:
                xml = await ep.full_text_xml(d.pmcid, force_refresh=force_refresh)
                if xml:
                    d.full_text = jats_to_text(xml)[:400000]
                    n_full += 1
            except Exception as exc:  # noqa: BLE001
                say(f"Europe PMC full text {d.pmcid}: {exc}")
    # user-uploaded guideline PDFs for this indication
    pdfs = session.execute(select(Document).join(DocumentLink).where(
        DocumentLink.indication_id == indication.id, DocumentLink.role == "guideline", Document.source_type == "pdf")).scalars().unique().all()
    docs += [p for p in pdfs if p not in docs]
    session.flush()
    say(f"Guidelines: {len(docs)} documents ({n_full} open-access full texts) for {indication.name}")
    return await extract_and_store_guideline_recs(session, indication, assets, docs)


async def extract_and_store_guideline_recs(session: Session, indication: Indication, assets: list[Asset], docs: list[Document]) -> dict[str, Any]:
    terms_by_asset = {a.generic_name: asset_terms(a.generic_name, a.brand_name, a.synonyms_json) for a in assets}
    # only send documents that mention at least one asset of interest
    inputs = []
    for d in docs:
        txt = doc_text(d, 120000)
        if any(text_mentions_asset(txt, t) for t in terms_by_asset.values()):
            inputs.append((d.pmid or d.source_id, txt))
    recs = await extract_guideline_recs(inputs, terms_by_asset, indication.name) if inputs else []
    session.execute(delete(GuidelineRecommendation).where(GuidelineRecommendation.indication_id == indication.id,
                                                          GuidelineRecommendation.source_type != "user_curated"))
    by_sid = {(d.pmid or d.source_id): d for d in docs}
    n = 0
    for r in recs:
        mention = r.get("asset_mention") or ""
        asset = next((a for a in assets if a.generic_name == mention or text_mentions_asset(mention, terms_by_asset[a.generic_name])
                      or text_mentions_asset(r["verbatim_quote"], terms_by_asset[a.generic_name])), None)
        d = by_sid.get(r["source_id"])
        body = r.get("_guideline_body")
        if not body and d is not None:
            m = BODY_RE.search(f"{d.title} {d.journal}")
            body = m.group(1) if m else (d.journal or "")[:60]
        year = r.get("_publication_year") or (d.year if d else None)
        session.add(GuidelineRecommendation(
            indication_id=indication.id, asset_id=asset.id if asset else None, asset_mention=mention, guideline_body=body,
            publication_year=year, indication_phase=r.get("indication_phase") or "UNSPECIFIED", line=r.get("line") or "UNCLEAR",
            role=r.get("role") or "UNSPECIFIED", level_of_evidence=r.get("level_of_evidence"), verbatim_quote=r["verbatim_quote"],
            source_id=r["source_id"], source_type="llm_extraction" if not r.get("_lexical") else "lexical"))
        n += 1
    session.flush()
    return {"documents": len(docs), "documents_mentioning_assets": len(inputs), "recommendations": n}


def guideline_status(session: Session, indication: Indication, asset: Asset) -> dict[str, Any]:
    recs = session.execute(select(GuidelineRecommendation).where(GuidelineRecommendation.indication_id == indication.id,
                                                                 GuidelineRecommendation.asset_id == asset.id)).scalars().all()
    all_years = [y for (y,) in session.execute(select(Document.year).join(DocumentLink).where(
        DocumentLink.indication_id == indication.id, DocumentLink.role == "guideline")).all() if y]
    latest_guideline_year = max(all_years) if all_years else None
    if recs:
        by_body: dict[str, GuidelineRecommendation] = {}
        for r in sorted(recs, key=lambda r: (-(r.publication_year or 0), LINE_RANK.get(r.line or "UNCLEAR", 9))):
            by_body.setdefault(r.guideline_body or "Unspecified", r)
        best = min(by_body.values(), key=lambda r: (LINE_RANK.get(r.line or "UNCLEAR", 9), -(r.publication_year or 0)))
        role = "" if best.role in (None, "UNSPECIFIED", "EITHER") else f" {best.role.lower()}"
        phase = "" if best.indication_phase in (None, "UNSPECIFIED") else f", {best.indication_phase.lower()}"
        status = f"{LINE_LABEL.get(best.line or 'UNCLEAR')}{role}{phase} — {best.guideline_body or 'guideline'} {best.publication_year or ''}".strip()
        return {"status": status, "line": best.line, "best": _rec(best), "by_guideline": [_rec(r) for r in by_body.values()],
                "latest_guideline_year": latest_guideline_year}
    if asset.initial_us_approval_year and latest_guideline_year and asset.initial_us_approval_year >= latest_guideline_year:
        return {"status": f"Unlisted / approved post-guideline (approved {asset.initial_us_approval_year}; latest ingested guideline {latest_guideline_year})",
                "line": None, "best": None, "by_guideline": [], "latest_guideline_year": latest_guideline_year}
    return {"status": "Not positioned in ingested guidelines" if latest_guideline_year else "No guideline documents ingested",
            "line": None, "best": None, "by_guideline": [], "latest_guideline_year": latest_guideline_year}


def _rec(r: GuidelineRecommendation) -> dict[str, Any]:
    from .evidence import source_url

    return {"id": r.id, "body": r.guideline_body, "year": r.publication_year, "line": r.line, "line_label": LINE_LABEL.get(r.line or "UNCLEAR"),
            "role": r.role, "phase": r.indication_phase, "level_of_evidence": r.level_of_evidence, "quote": r.verbatim_quote,
            "source_id": r.source_id, "source_type": r.source_type,
            "url": source_url("pubmed" if (r.source_id or "").isdigit() else None, r.source_id)}


def gap_to_guideline(session: Session, indication: Indication, asset: Asset, heatmap: dict[str, Any], rules: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rules = rules or DEFAULT_RULES
    ind_def = INDICATION_BY_KEY.get(indication.key)
    primary_rows = list(ind_def.primary_scale_rows) if ind_def else []
    cols = [c for c in heatmap["columns"] if c["indication_key"] == indication.key and c["population"] == "adult"]
    status = guideline_status(session, indication, asset)
    checks = []
    for rule in rules:
        rows = primary_rows if rule.get("rows") == "primary" else ([r["key"] for r in heatmap["rows"]] if rule.get("rows") == "any" else rule["rows"])
        tcols = [c for c in cols if rule.get("timepoint") in (None, "any") or c["timepoint"] == rule["timepoint"]]
        linked: list[dict[str, Any]] = []
        passed = False
        if rule.get("check") == "comparative":
            from ..models import ClinicalStudy, Document as Doc

            h2h = session.execute(select(ClinicalStudy).where(ClinicalStudy.asset_id == asset.id, ClinicalStudy.is_head_to_head.is_(True),
                                                              ClinicalStudy.indication_key == indication.key)).scalars().first()
            nma = session.execute(select(Doc).join(DocumentLink).where(DocumentLink.asset_id == asset.id)).scalars().all()
            nma_hit = next((d for d in nma if "NMA" in (d.evidence_tags_json or [])), None)
            passed = bool(h2h or nma_hit)
            linked.append({"row": None, "column": None, "state": "present" if passed else "missing",
                           "detail": (h2h.nct_id if h2h else None) or (nma_hit.pmid if nma_hit else None) or "No H2H trial or NMA found"})
        else:
            if not tcols:
                want = "maintenance" if rule.get("timepoint") == "maintenance" else "acute"
                linked.append({"row": rows[0] if rows else None, "column": f"{indication.key}|adult|{want}",
                               "column_label": column_label(f"{indication.key}|adult|{want}"), "state": "white_space", "validity": None})
            if "max_harm_pp" in rule:
                with_data, signals, gaps = 0, [], []
                for c in tcols:
                    for r in rows:
                        cell = heatmap["cells"].get(r, {}).get(c["key"])
                        if cell is None:
                            continue
                        pp = (cell.get("risk") or {}).get("pp")
                        entry = {"row": r, "column": c["key"], "column_label": c["label"], "state": cell["state"],
                                 "validity": cell.get("validity"), "detail": f"{pp:+.1f} pp vs placebo" if pp is not None else cell.get("summary")}
                        if cell["state"] == "evidence":
                            with_data += 1
                            if pp is not None and pp >= rule["max_harm_pp"]:
                                signals.append(entry)
                        else:
                            gaps.append(entry)
                passed = with_data > 0 and not signals
                linked += signals or gaps
            else:
                for c in tcols:
                    for r in rows:
                        cell = heatmap["cells"].get(r, {}).get(c["key"])
                        if cell is None:
                            continue
                        v = cell.get("validity") or 0
                        if v >= rule["min_validity"] and cell.get("significant", True):
                            passed = True
                        else:
                            linked.append({"row": r, "column": c["key"], "column_label": c["label"], "state": cell["state"],
                                           "validity": cell.get("validity"), "detail": cell.get("summary")})
        checks.append({"rule": rule["id"], "label": rule["label"], "target": rule["target"], "passed": passed,
                       "blocking_cells": [] if passed else linked[:8]})
    blocking = [c for c in checks if not c["passed"] and c["target"] == "FIRST"]
    return {"asset": asset.generic_name, "indication": indication.name, "current": status,
            "target": "1st-line" if status.get("line") != "FIRST" else "Maintain 1st-line / preferred",
            "checks": checks, "blocking_count": len(blocking),
            "summary": ("All first-line evidence checks pass; upgrade depends on guideline update cycle." if not blocking else
                        "Blocking for higher tier: " + "; ".join(c["label"] for c in blocking)),
            "rules_note": "mednik default rule set (configuration) — edit DEFAULT_RULES to mirror the target guideline's criteria."}
