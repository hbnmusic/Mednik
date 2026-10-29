"""Turn parsed USPI structures into Tier 1A evidence rows, and link registry results to label claims.

* §14 efficacy tables → ``clinical_studies`` (source='uspi') + LS-mean-difference endpoints (Tier 1A / L5)
* §6 incidence tables → adverse-event incidence endpoints with risk differences vs placebo
* Registry primary results whose effect matches a §14 row are flagged ``label_confirmed`` (Tier 1A)
* Phase 3 primary endpoints positive in ≥2 trials are flagged replicated (validity L5)
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Asset, ClinicalStudy, ExtractedEndpoint, Indication
from ..taxonomy import SCALE_BY_KEY, classify_ae_term, classify_scale
from . import stats
from .trials_ingest import column_for


def _ae_scale(term: str) -> str | None:
    sk = classify_ae_term(term)
    if sk:
        return sk
    sk = classify_scale(term)
    if sk and SCALE_BY_KEY[sk].kind in ("binary_safety", "continuous_safety"):
        return sk
    return None


def persist_label_evidence(session: Session, asset: Asset, parsed: dict[str, Any], indication_by_key: dict[str, Indication]) -> dict[str, int]:
    session.execute(delete(ClinicalStudy).where(ClinicalStudy.asset_id == asset.id, ClinicalStudy.source == "uspi"))
    session.flush()
    set_id = asset.spl_set_id or "USPI"
    n_eff = n_ae = 0

    # ---- §14 efficacy rows ----------------------------------------------------
    rows = parsed.get("clinical_studies", {}).get("efficacy_rows", [])
    by_study: dict[tuple[str | None, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_study[(r.get("indication_key"), r["study_label"])].append(r)
    for (ind_key, label), group in by_study.items():
        ind = indication_by_key.get(ind_key or "")
        pbo = next((g for g in group if g["is_placebo"]), None)
        cs = ClinicalStudy(
            asset_id=asset.id, indication_id=ind.id if ind else None, source="uspi", source_ref=f"{set_id}:§14:{ind_key}:{label}",
            title=f"USPI §14 — {label} ({group[0].get('caption') or ''})"[:500], sponsor_name=asset.company, sponsor_class="INDUSTRY",
            phase="PHASE3", phase_max=3, study_type="INTERVENTIONAL", allocation="RANDOMIZED", design_archetype="LABEL_PIVOTAL",
            has_placebo=pbo is not None, duration_weeks=group[0].get("timepoint_weeks"), indication_key=ind_key, population="adult",
            sample_size=sum(g["n"] for g in group if g.get("n")) or None,
            baseline_severity_json={group[0]["scale"]: {"mean": pbo["baseline_mean"] if pbo else group[0].get("baseline_mean"),
                                                         "by_arm": {g["arm"]: g.get("baseline_mean") for g in group}}} if group[0].get("scale") else None,
            placebo_response_rate=pbo.get("ls_change") if pbo else None,
            placebo_response_json={group[0]["scale"]: {"placebo_change": pbo.get("ls_change"), "baseline": pbo.get("baseline_mean")}} if pbo and group[0].get("scale") else None,
            evidence_tier="1A", validity_level=5,
        )
        session.add(cs)
        session.flush()
        for g in group:
            if g["is_placebo"] or g.get("diff") is None or not g.get("scale"):
                continue
            sdef = SCALE_BY_KEY[g["scale"]]
            d = dvar = None
            if g.get("n") and pbo and pbo.get("n") and g.get("ci_low") is not None and g.get("ci_high") is not None:
                r = stats.cohens_d_from_ci(g["diff"], g["ci_low"], g["ci_high"], g["n"], pbo["n"], lower_is_better=sdef.lower_is_better)
                if r:
                    d, dvar = r
            sig = g.get("flag_significant") or (g.get("ci_low") is not None and g.get("ci_high") is not None and (g["ci_high"] < 0 or g["ci_low"] > 0))
            session.add(ExtractedEndpoint(
                study_id=cs.id, asset_id=asset.id, indication_id=cs.indication_id, mvd_key=sdef.mvd_key, endpoint_scale_name=g["scale"],
                endpoint_label=g.get("caption"), timepoint_weeks=g.get("timepoint_weeks"), arm_name=g["arm"],
                comparator_arm=pbo["arm"] if pbo else "Placebo", effect_size_type="LS_MEAN_DIFF", value=g["diff"],
                ci_95_lower=g.get("ci_low"), ci_95_upper=g.get("ci_high"), is_multiplicity_controlled=g.get("multiplicity_footnote") or None,
                is_primary=True, is_post_hoc=False, n_treatment=g.get("n"), n_comparator=pbo.get("n") if pbo else None,
                cohens_d=d, cohens_d_var=dvar, favorable=(g["diff"] < 0) if sdef.lower_is_better else (g["diff"] > 0), significant=bool(sig),
                label_confirmed=True, column_key=column_for(ind_key, "adult", bool(g.get("adjunctive")), g.get("timepoint_weeks")),
                verbatim_quote=g["verbatim"], source_id=set_id, source_type="uspi", extraction_method="deterministic_label_table",
                evidence_tier="1A", validity_level=5,
            ))
            n_eff += 1

    # ---- §6 adverse-reaction incidence tables ----------------------------------
    for tbl in parsed.get("ae_tables", []):
        ind_key = tbl.get("indication_key")
        ind = indication_by_key.get(ind_key or "")
        pbo_arm = next(a for a in tbl["arms"] if a["is_placebo"])
        cs = ClinicalStudy(
            asset_id=asset.id, indication_id=ind.id if ind else None, source="uspi",
            source_ref=f"{set_id}:§6:{tbl['table_index']}", title=f"USPI §6 — {tbl.get('caption') or 'Adverse reactions table'}"[:500],
            sponsor_name=asset.company, sponsor_class="INDUSTRY", phase="PHASE3", phase_max=3, study_type="INTERVENTIONAL",
            allocation="RANDOMIZED", design_archetype="LABEL_POOLED_SAFETY", indication_key=ind_key, population="adult",
            sample_size=sum(a["n"] for a in tbl["arms"] if a.get("n")) or None, evidence_tier="1A", validity_level=5,
        )
        session.add(cs)
        session.flush()
        for row in tbl["rows"]:
            sk = _ae_scale(row["term"])
            if not sk:
                continue
            pc = row["values"].get(pbo_arm["col"])
            if pc is None:
                continue
            for arm in tbl["arms"]:
                if arm["is_placebo"]:
                    continue
                pt = row["values"].get(arm["col"])
                if pt is None:
                    continue
                rd = (pt - pc) / 100.0
                rdlo = rdhi = None
                if arm.get("n") and pbo_arm.get("n"):
                    rdx = stats.risk_difference(round(pt / 100 * arm["n"]), arm["n"], round(pc / 100 * pbo_arm["n"]), pbo_arm["n"])
                    rdlo, rdhi = rdx["rd_lo"], rdx["rd_hi"]
                session.add(ExtractedEndpoint(
                    study_id=cs.id, asset_id=asset.id, indication_id=cs.indication_id, mvd_key=SCALE_BY_KEY[sk].mvd_key,
                    endpoint_scale_name=sk, endpoint_label=f"USPI AE: {row['term']}", timepoint_weeks=None, arm_name=arm["name"],
                    comparator_arm=pbo_arm["name"], effect_size_type="INCIDENCE_PCT", value=pt, risk_treatment=pt / 100, risk_comparator=pc / 100,
                    risk_difference=rd, nnt_nnh=stats.nnt_from_rd(rd), ci_95_lower=rdlo, ci_95_upper=rdhi,
                    n_treatment=arm.get("n"), n_comparator=pbo_arm.get("n"), favorable=rd <= 0.02, significant=(rdlo is not None and rdlo > 0),
                    is_primary=False, label_confirmed=True,
                    column_key=column_for(ind_key, "adult", bool(tbl.get("adjunctive")), 6.0),
                    verbatim_quote=row["verbatim"], source_id=set_id, source_type="uspi", extraction_method="deterministic_label_table",
                    evidence_tier="1A", validity_level=5,
                ))
                n_ae += 1
    session.flush()
    return {"label_efficacy_endpoints": n_eff, "label_ae_endpoints": n_ae}


def link_registry_to_label(session: Session, asset: Asset) -> int:
    """Flag registry primary results that reproduce a §14 row (same scale & indication, |Δ| ≤ 0.3 points)."""
    label_eps = session.execute(select(ExtractedEndpoint).join(ClinicalStudy).where(
        ExtractedEndpoint.asset_id == asset.id, ClinicalStudy.source == "uspi", ExtractedEndpoint.effect_size_type == "LS_MEAN_DIFF")).scalars().all()
    reg_eps = session.execute(select(ExtractedEndpoint).join(ClinicalStudy).where(
        ExtractedEndpoint.asset_id == asset.id, ClinicalStudy.source == "ctgov", ExtractedEndpoint.effect_size_type == "LS_MEAN_DIFF",
        ExtractedEndpoint.is_primary.is_(True))).scalars().all()
    n = 0
    for le in label_eps:
        lstudy = session.get(ClinicalStudy, le.study_id)
        cands = []
        for re_ in reg_eps:
            rstudy = session.get(ClinicalStudy, re_.study_id)
            if re_.endpoint_scale_name != le.endpoint_scale_name or re_.value is None or le.value is None:
                continue
            if lstudy.indication_key and rstudy.indication_key and lstudy.indication_key != rstudy.indication_key:
                continue
            if (rstudy.phase_max or 0) < 3:  # §14 pivotal studies are Phase 3 (incl. Phase 2/3)
                continue
            delta = abs(re_.value - le.value)
            if delta <= 0.3:
                cands.append((delta, re_, rstudy))
        if not cands:
            continue
        cands.sort(key=lambda c: c[0])
        delta, best, rstudy = cands[0]
        best.label_confirmed = True
        best.evidence_tier = "1A"
        best.validity_level = 5
        # fill missing Cohen's d on the label row from the matched registry result
        if le.cohens_d is None and best.cohens_d is not None:
            le.cohens_d, le.cohens_d_var = best.cohens_d, best.cohens_d_var
            le.n_treatment, le.n_comparator = best.n_treatment, best.n_comparator
            le.extraction_method = "deterministic_label_table+registry_d"  # duplicate of the registry row; excluded from pooling
        elif le.cohens_d is not None:
            best.extraction_method = "deterministic_ctgov_results:dup_of_label"  # label row already carries d
        links = list(lstudy.candidate_nct_ids_json or [])
        if rstudy.nct_id not in [c["nct_id"] for c in links]:
            links.append({"nct_id": rstudy.nct_id, "match": f"{le.endpoint_scale_name} LS-mean diff {best.value} vs label {le.value} (|Δ|={delta:.2f})",
                          "arm": best.arm_name})
        lstudy.candidate_nct_ids_json = links
        if not lstudy.nct_id:
            lstudy.nct_id = rstudy.nct_id
        rstudy.evidence_tier = "1A"
        rstudy.validity_level = 5
        n += 1
    session.flush()
    return n


def mark_replication(session: Session, asset: Asset) -> int:
    eps = session.execute(select(ExtractedEndpoint).join(ClinicalStudy).where(
        ExtractedEndpoint.asset_id == asset.id, ClinicalStudy.source == "ctgov", ExtractedEndpoint.is_primary.is_(True),
        ExtractedEndpoint.significant.is_(True), ExtractedEndpoint.favorable.is_(True), ClinicalStudy.phase_max >= 3)).scalars().all()
    groups: dict[tuple, set[int]] = defaultdict(set)
    for e in eps:
        st = session.get(ClinicalStudy, e.study_id)
        groups[(st.indication_key, e.endpoint_scale_name)].add(e.study_id)
    n = 0
    for e in eps:
        st = session.get(ClinicalStudy, e.study_id)
        if len(groups[(st.indication_key, e.endpoint_scale_name)]) >= 2 and (e.validity_level or 0) < 5:
            e.validity_level = 5
            n += 1
    session.flush()
    return n
