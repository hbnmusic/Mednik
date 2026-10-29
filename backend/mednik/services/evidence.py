"""Shared evidence queries, pooling and serialisation used by the matrix, heatmap and adjacencies."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ClinicalStudy, ExtractedEndpoint
from ..taxonomy import SCALE_BY_KEY, column_label, timepoint_bucket
from . import stats
from .tiering import TIER_ORDER, TIER_WEIGHTS

Pair = tuple[ExtractedEndpoint, ClinicalStudy]

# rows that duplicate another row's data (label ↔ registry match); kept for display, excluded from pooling
DUPLICATE_METHODS = {"deterministic_label_table+registry_d", "deterministic_ctgov_results:dup_of_label"}


def endpoint_pairs(session: Session, asset_id: int, *, indication_key: str | None = None, rows: Iterable[str] | None = None) -> list[Pair]:
    q = select(ExtractedEndpoint, ClinicalStudy).join(ClinicalStudy, ExtractedEndpoint.study_id == ClinicalStudy.id).where(
        ExtractedEndpoint.asset_id == asset_id)
    if rows:
        q = q.where(ExtractedEndpoint.endpoint_scale_name.in_(list(rows)))
    if indication_key:
        q = q.where(ClinicalStudy.indication_key == indication_key)
    return [(e, s) for e, s in session.execute(q).all()]


def is_placebo_comparison(ep: ExtractedEndpoint) -> bool:
    return bool(ep.comparator_arm and re.search(r"placebo|vehicle|sham", ep.comparator_arm, re.I)) or ep.source_type == "uspi"


def study_ref(st: ClinicalStudy) -> str:
    return st.nct_id or st.pmid or st.source_ref


def source_url(source_type: str | None, source_id: str | None) -> str | None:
    if not source_id:
        return None
    if source_type == "ctgov" or (source_id or "").startswith("NCT"):
        return f"https://clinicaltrials.gov/study/{source_id}"
    if source_type == "pubmed" or (source_id.isdigit() and len(source_id) <= 9):
        return f"https://pubmed.ncbi.nlm.nih.gov/{source_id}/"
    if source_type == "uspi":
        return f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={source_id}"
    if source_type == "epmc":
        return f"https://europepmc.org/search?query={source_id}"
    return None


def _filter_tier(pairs: list[Pair], tier_filter: str) -> list[Pair]:
    if tier_filter == "tier1":
        return [(e, s) for e, s in pairs if e.evidence_tier in ("1A", "1B")]
    return pairs


def _best_tier_subset(pairs: list[Pair]) -> tuple[list[Pair], str | None]:
    tiers = [e.evidence_tier for e, _ in pairs if e.evidence_tier]
    if not tiers:
        return pairs, None
    best = min(tiers, key=TIER_ORDER.index)
    return [(e, s) for e, s in pairs if e.evidence_tier == best], best


def summarize_effect(pairs: list[Pair], *, tier_filter: str = "all", buckets: tuple[str, ...] | None = ("acute",)) -> dict[str, Any] | None:
    cand = [(e, s) for e, s in _filter_tier(pairs, tier_filter)
            if e.cohens_d is not None and e.cohens_d_var and is_placebo_comparison(e)
            and e.extraction_method not in DUPLICATE_METHODS
            and (buckets is None or timepoint_bucket(e.timepoint_weeks or s.duration_weeks) in buckets)]
    if not cand:
        return None
    sub, tier = _best_tier_subset(cand)
    by_study: dict[int, list[ExtractedEndpoint]] = defaultdict(list)
    for e, s in sub:
        by_study[s.id].append(e)
    per_study = []
    for eps in by_study.values():
        d = sum(e.cohens_d for e in eps) / len(eps)
        v = sum(e.cohens_d_var for e in eps) / len(eps)
        per_study.append((d, v))
    pooled = stats.pool_inverse_variance(per_study)
    if not pooled:
        return None
    return {"metric": "cohens_d", "estimate": round(pooled["estimate"], 4), "se": round(pooled["se"], 4), "lo": round(pooled["lo"], 4),
            "hi": round(pooled["hi"], 4), "k_studies": pooled["k"], "i2": round(pooled["i2"], 3), "tier": tier,
            "endpoint_ids": [e.id for e, _ in sub], "study_ids": sorted({s.id for _, s in sub})}


def summarize_risk(pairs: list[Pair], *, tier_filter: str = "all", max_weeks: float | None = 12.5) -> dict[str, Any] | None:
    cand = [(e, s) for e, s in _filter_tier(pairs, tier_filter)
            if e.risk_difference is not None and is_placebo_comparison(e)
            and (max_weeks is None or (e.timepoint_weeks or s.duration_weeks or 6) <= max_weeks)]
    if not cand:
        return None
    sub, tier = _best_tier_subset(cand)
    by_study: dict[int, list[ExtractedEndpoint]] = defaultdict(list)
    for e, s in sub:
        by_study[s.id].append(e)
    items = []
    for eps in by_study.values():
        rd = sum(e.risk_difference for e in eps) / len(eps)
        nt = sum(e.n_treatment or 0 for e in eps) or 100
        nc = max((e.n_comparator or 0) for e in eps) or 100
        items.append((rd, nt, nc))
    pooled = stats.pool_risk_differences(items)
    if not pooled:
        return None
    rd = pooled["estimate"]
    rt = [e.risk_treatment for e, _ in sub if e.risk_treatment is not None]
    rc = [e.risk_comparator for e, _ in sub if e.risk_comparator is not None]
    return {"metric": "risk_difference", "estimate": round(rd, 4), "pp": round(rd * 100, 2), "nnh": stats.nnt_from_rd(rd) if rd > 0 else None,
            "incidence_treatment_pct": round(100 * sum(rt) / len(rt), 1) if rt else None,
            "incidence_placebo_pct": round(100 * sum(rc) / len(rc), 1) if rc else None, "k_studies": pooled["k"], "tier": tier,
            "endpoint_ids": [e.id for e, _ in sub], "study_ids": sorted({s.id for _, s in sub})}


def serialize_endpoint(e: ExtractedEndpoint, s: ClinicalStudy) -> dict[str, Any]:
    sdef = SCALE_BY_KEY.get(e.endpoint_scale_name)
    return {
        "id": e.id, "scale": e.endpoint_scale_name, "scale_label": sdef.label if sdef else e.endpoint_scale_name,
        "endpoint_label": e.endpoint_label, "timepoint_weeks": e.timepoint_weeks, "arm": e.arm_name, "comparator": e.comparator_arm,
        "effect_size_type": e.effect_size_type, "value": e.value, "ci_95_lower": e.ci_95_lower, "ci_95_upper": e.ci_95_upper,
        "p_value": e.p_value, "p_value_text": e.p_value_text, "is_primary": e.is_primary, "is_post_hoc": e.is_post_hoc,
        "is_multiplicity_controlled": e.is_multiplicity_controlled, "n_treatment": e.n_treatment, "n_comparator": e.n_comparator,
        "cohens_d": round(e.cohens_d, 3) if e.cohens_d is not None else None,
        "risk_treatment_pct": round(100 * e.risk_treatment, 2) if e.risk_treatment is not None else None,
        "risk_comparator_pct": round(100 * e.risk_comparator, 2) if e.risk_comparator is not None else None,
        "risk_difference_pp": round(100 * e.risk_difference, 2) if e.risk_difference is not None else None,
        "nnt_nnh": e.nnt_nnh, "favorable": e.favorable, "significant": e.significant, "label_confirmed": e.label_confirmed,
        "evidence_tier": e.evidence_tier, "tier_weight": TIER_WEIGHTS.get(e.evidence_tier or "", None), "validity_level": e.validity_level,
        "column_key": e.column_key, "column_label": column_label(e.column_key) if e.column_key else None,
        "verbatim_quote": e.verbatim_quote, "source_id": e.source_id, "source_type": e.source_type,
        "source_url": source_url(e.source_type, e.source_id), "extraction_method": e.extraction_method,
        "study": serialize_study_brief(s),
    }


def serialize_study_brief(s: ClinicalStudy) -> dict[str, Any]:
    return {
        "id": s.id, "source": s.source, "ref": study_ref(s), "nct_id": s.nct_id, "pmid": s.pmid, "title": s.title,
        "sponsor": s.sponsor_name, "sponsor_class": s.sponsor_class, "sponsor_class_raw": s.sponsor_class_raw, "phase": s.phase,
        "design": s.design_archetype, "allocation": s.allocation, "masking": s.masking, "n": s.sample_size,
        "duration_weeks": s.duration_weeks, "start_year": s.start_year, "status": s.overall_status, "pct_us_sites": s.pct_us_sites,
        "baseline": s.baseline_severity_json, "placebo_response": s.placebo_response_json, "dropout": s.dropout_json,
        "placebo_run_in": s.placebo_run_in, "flexible_dose": s.flexible_dose, "is_head_to_head": s.is_head_to_head,
        "evidence_tier": s.evidence_tier, "validity_level": s.validity_level, "indication_key": s.indication_key,
        "population": s.population, "candidate_nct_ids": s.candidate_nct_ids_json,
        "url": source_url("ctgov", s.nct_id) if s.nct_id else source_url(s.source, s.pmid or s.source_ref.split(":")[0]),
    }
