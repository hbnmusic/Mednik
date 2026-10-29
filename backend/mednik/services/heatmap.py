"""MODULE 3 — Multi-dimensional evidence heatmap.

Rows    = standardized scales / endpoint families (PANSS Total, Marder negative factor, MADRS, CGI-S,
          akathisia, weight, somnolence, PSP/SDS, …)
Columns = indication × population × timepoint bucket (acute ≤8 wk · 9–26 wk · maintenance >26 wk),
          drawn from the label, registry and literature for the asset (approved + pipeline).
Cell    = background colour from standardized effect / harm (green = favourable, amber/red = safety
          signal, grey hatch = evidence white space) + corner validity badge L1–L5 (best evidence).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Asset, ClinicalStudy, Indication, MarketValueDriver
from ..taxonomy import SCALES, SCALE_BY_KEY, ColumnKey, column_label, indication_label, mvd_profile, MVD_BY_KEY
from .evidence import endpoint_pairs, serialize_endpoint, serialize_study_brief, summarize_effect, summarize_risk
from .mvd import effective_scores
from .trials_ingest import column_for

ACTIVE_STATUSES = {"RECRUITING", "NOT_YET_RECRUITING", "ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION"}
TP_ORDER = {"acute": 0, "intermediate": 1, "maintenance": 2}
MAX_COLUMNS = 16


def _color(kind: str, eff: dict[str, Any] | None, risk: dict[str, Any] | None, significant: bool, any_unfavorable_sig: bool) -> str:
    if kind in ("binary_safety", "continuous_safety"):
        if risk:
            pp = risk["pp"]
            return "g1" if pp <= 2 else "a1" if pp <= 5 else "a2" if pp < 10 else "r2"
        if eff:
            d = eff["estimate"]  # + = less harm than placebo
            return "g1" if d > -0.2 else "a2" if d > -0.5 else "r2"
        return "neutral"
    if any_unfavorable_sig:
        return "r1"
    if eff:
        d = eff["estimate"]
        if not significant and eff["lo"] <= 0:
            return "neutral"
        return "g3" if d >= 0.5 else "g2" if d >= 0.3 else "g1" if d > 0 else "neutral"
    if risk:
        pp = risk["pp"]
        if not significant:
            return "neutral"
        return "g3" if pp >= 15 else "g2" if pp >= 8 else "g1" if pp > 0 else "neutral"
    return "g1" if significant else "neutral"


def _study_column(st: ClinicalStudy) -> str:
    adj = bool((st.registered_outcomes_json or {}).get("adjunctive"))
    return column_for(st.indication_key, st.population, adj, st.duration_weeks)


def build_heatmap(session: Session, asset: Asset, *, indication_keys: list[str] | None = None, persona: str = "evidence") -> dict[str, Any]:
    pairs = endpoint_pairs(session, asset.id)
    studies = session.execute(select(ClinicalStudy).where(ClinicalStudy.asset_id == asset.id)).scalars().all()
    approved = {(i["indication_key"], i.get("adjunctive", False)) for i in (asset.approved_indications_json or [])}
    approved_keys = {k for k, _ in approved}

    # --- columns ----------------------------------------------------------------
    col_weight: dict[str, int] = defaultdict(int)
    col_meta: dict[str, dict[str, Any]] = {}
    for e, s in pairs:
        if e.column_key:
            col_weight[e.column_key] += 3
    for st in studies:
        if st.source == "ctgov" and st.indication_key:
            col_weight[_study_column(st)] += 1
    for k, adj in approved:
        col_weight[column_for(k, "adult", adj, 6)] += 5
        if any((s.registered_outcomes_json or {}).get("adjunctive") == adj and s.indication_key == k and (s.duration_weeks or 0) > 26 for s in studies):
            col_weight[column_for(k, "adult", adj, 52)] += 1
    cols = [c for c in col_weight if not c.startswith("other|")]
    if indication_keys:
        cols = [c for c in cols if ColumnKey.decode(c).indication_key in indication_keys]
    cols = sorted(cols, key=lambda c: (-col_weight[c]))[:MAX_COLUMNS]
    cols = sorted(cols, key=lambda c: (ColumnKey.decode(c).indication_key not in approved_keys, ColumnKey.decode(c).indication_key,
                                       ColumnKey.decode(c).population != "adult", ColumnKey.decode(c).adjunctive,
                                       TP_ORDER.get(ColumnKey.decode(c).timepoint, 9)))
    for c in cols:
        ck = ColumnKey.decode(c)
        active = [s for s in studies if s.source == "ctgov" and _study_column(s) == c and (s.overall_status or "") in ACTIVE_STATUSES]
        col_meta[c] = {"key": c, "label": column_label(c), "indication_key": ck.indication_key, "indication_label": indication_label(ck.indication_key),
                       "population": ck.population, "timepoint": ck.timepoint, "adjunctive": ck.adjunctive,
                       "approved": (ck.indication_key, ck.adjunctive) in approved or ck.indication_key in approved_keys,
                       "pipeline": ck.indication_key not in approved_keys, "active_trials": [s.nct_id for s in active][:10]}

    # --- MVD weights per indication (for "high-MVD row" flag) ------------------------
    mvd_scores: dict[str, dict[str, float]] = {}
    for ind in session.execute(select(Indication)).scalars().all():
        drivers = session.execute(select(MarketValueDriver).where(MarketValueDriver.indication_id == ind.id)).scalars().all()
        if drivers:
            mvd_scores[ind.key] = {k: v["score"] for k, v in effective_scores(drivers, persona).items()}

    # --- rows -----------------------------------------------------------------------
    used_rows = {e.endpoint_scale_name for e, _ in pairs if e.column_key in col_meta}
    relevant_rows: set[str] = set()
    for c in col_meta.values():
        for mk in mvd_profile(c["indication_key"]):
            relevant_rows |= set(MVD_BY_KEY[mk].rows)
    rows = [s for s in SCALES if s.key in used_rows or s.key in relevant_rows]

    # --- cells --------------------------------------------------------------------------
    grouped: dict[tuple[str, str], list] = defaultdict(list)
    for e, s in pairs:
        if e.column_key in col_meta:
            grouped[(e.endpoint_scale_name, e.column_key)].append((e, s))
    registered: dict[tuple[str, str], list[str]] = defaultdict(list)
    for st in studies:
        if st.source != "ctgov":
            continue
        c = _study_column(st)
        if c not in col_meta:
            continue
        ro = st.registered_outcomes_json or {}
        for o in (ro.get("primary") or []) + (ro.get("secondary") or []):
            if o.get("scale"):
                registered[(o["scale"], c)].append(st.nct_id)

    cells: dict[str, dict[str, Any]] = {}
    for r in rows:
        cells[r.key] = {}
        for c in col_meta:
            cp = grouped.get((r.key, c), [])
            ind_key = col_meta[c]["indication_key"]
            mvd_score = mvd_scores.get(ind_key, {}).get(r.mvd_key or "", None)
            if not cp:
                regs = sorted(set(registered.get((r.key, c), [])))
                cells[r.key][c] = {"state": "registered" if regs else "white_space", "validity": None, "tier": None, "color": "hatch",
                                   "n_endpoints": 0, "n_studies": 0, "registered_ncts": regs[:8], "summary": "Registered — no results posted" if regs else "No evidence",
                                   "mvd_key": r.mvd_key, "mvd_score": mvd_score}
                continue
            eff = summarize_effect(cp, buckets=None) if r.kind in ("continuous_efficacy", "time_to_event", "continuous_safety") else None
            risk = summarize_risk(cp, max_weeks=None) if r.kind in ("binary_safety", "binary_efficacy", "continuous_safety") else None
            sig = any(e.significant and e.favorable for e, _ in cp)
            unfav = any(e.significant and e.favorable is False for e, _ in cp) and r.kind in ("continuous_efficacy", "binary_efficacy", "time_to_event")
            validity = max((e.validity_level or 0) for e, _ in cp) or None
            tiers = [e.evidence_tier for e, _ in cp if e.evidence_tier]
            tier = min(tiers, key=["1A", "1B", "2", "3"].index) if tiers else None
            if eff:
                summary = f"d {eff['estimate']:+.2f} (k={eff['k_studies']})"
            elif risk:
                summary = f"{risk['pp']:+.1f} pp vs PBO" + (f", NNH {risk['nnh']:.0f}" if risk.get("nnh") else "")
            else:
                best = max(cp, key=lambda p: (p[0].validity_level or 0))[0]
                unit = {"LS_MEAN_DIFF": "LSΔ", "HR": "HR", "OR": "OR", "NNT": "NNT", "NNH": "NNH", "INCIDENCE_PCT": "%", "RD": "RD",
                        "COHENS_D": "d"}.get(best.effect_size_type, best.effect_size_type)
                summary = f"{unit} {best.value:g}" if best.value is not None else "reported"
            studies_here = {s.id for _, s in cp}
            cells[r.key][c] = {
                "state": "evidence", "validity": validity, "tier": tier, "color": _color(r.kind, eff, risk, sig, unfav),
                "n_endpoints": len(cp), "n_studies": len(studies_here), "summary": summary, "significant": sig,
                "effect": eff and {k: eff[k] for k in ("estimate", "lo", "hi", "k_studies")}, "risk": risk and {k: risk[k] for k in ("pp", "nnh", "k_studies")},
                "refs": sorted({(s.nct_id or s.pmid or s.source_ref.split(':')[0]) for _, s in cp})[:6],
                "mvd_key": r.mvd_key, "mvd_score": mvd_score,
            }
    return {
        "asset": {"id": asset.id, "generic": asset.generic_name, "brand": asset.brand_name},
        "columns": list(col_meta.values()),
        "rows": [{"key": r.key, "label": r.label, "kind": r.kind, "mvd_key": r.mvd_key,
                  "mvd_name": MVD_BY_KEY[r.mvd_key].name if r.mvd_key else None} for r in rows],
        "cells": cells,
        "legend": {
            "colors": {"g3": "Strong favourable effect (d≥0.5 / RD≥15pp)", "g2": "Moderate (d 0.3–0.5)", "g1": "Small favourable / low harm (≤2pp)",
                       "neutral": "Not significant / unclear", "a1": "Excess harm 2–5 pp", "a2": "Excess harm 5–10 pp / d −0.2 to −0.5",
                       "r2": "Safety signal ≥10 pp / d ≤ −0.5", "r1": "Significantly unfavourable efficacy", "hatch": "Evidence white space"},
            "validity": {"L5": "USPI §14 / replicated Phase 3 primary", "L4": "Phase 3 primary or controlled key secondary / large Ph4 RCT",
                         "L3": "Phase 3 nominal secondary / post-hoc / RWE N>500", "L2": "Phase 2 RCT / academic N 50–500",
                         "L1": "Small IIS / case series / biomarker"},
        },
        "method": __doc__,
    }


def cell_evidence(session: Session, asset: Asset, row_key: str, col_key: str) -> dict[str, Any]:
    pairs = [(e, s) for e, s in endpoint_pairs(session, asset.id, rows=[row_key]) if e.column_key == col_key]
    pairs.sort(key=lambda p: (-(p[0].validity_level or 0), p[0].evidence_tier or "9"))
    studies = session.execute(select(ClinicalStudy).where(ClinicalStudy.asset_id == asset.id, ClinicalStudy.source == "ctgov")).scalars().all()
    regs = []
    for st in studies:
        if _study_column(st) != col_key:
            continue
        ro = st.registered_outcomes_json or {}
        for o in (ro.get("primary") or []) + (ro.get("secondary") or []):
            if o.get("scale") == row_key:
                regs.append({**serialize_study_brief(st), "registered_measure": o.get("measure"), "time_frame": o.get("timeFrame"),
                             "outcome_type": o.get("type")})
                break
    sdef = SCALE_BY_KEY.get(row_key)
    return {"row": {"key": row_key, "label": sdef.label if sdef else row_key, "kind": sdef.kind if sdef else None},
            "column": {"key": col_key, "label": column_label(col_key)},
            "endpoints": [serialize_endpoint(e, s) for e, s in pairs], "registered_trials": regs}
