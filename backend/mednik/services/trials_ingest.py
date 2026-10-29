"""ClinicalTrials.gov registry ingestion + deterministic classification (spec §3.2).

Sponsor class, phase, design, eligibility and posted results are read from
explicit ``protocolSection`` / ``resultsSection`` fields. Posted results become
``extracted_endpoints`` rows whose ``verbatim_quote`` cites the exact registry
field values they were computed from.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..clients import ClinicalTrialsClient
from ..models import Asset, ClinicalStudy, ExtractedEndpoint, Indication
from ..taxonomy import (
    SCALE_BY_KEY,
    ColumnKey,
    classify_ae_term,
    classify_indication,
    classify_scale,
    text_mentions_asset,
    timepoint_bucket,
)
from ..textutil import parse_age_years, parse_number, parse_p_value, parse_weeks
from . import stats
from .tiering import EvidenceContext, evidence_tier, validity_level

log = logging.getLogger("mednik.trials")

PHASE_NUM = {"EARLY_PHASE1": 1, "PHASE1": 1, "PHASE2": 2, "PHASE3": 3, "PHASE4": 4}
ACADEMIC_CLASSES = {"OTHER", "NIH", "NETWORK", "FED", "OTHER_GOV", "INDIV", "AMBIG", "UNKNOWN"}
MULTIPLICITY_RE = re.compile(r"multiplic|hierarch|fixed[-\s]sequence|gatekeep|hochberg|bonferroni|hommel|holm|adjusted\s+p|alpha\s+(was\s+)?(controlled|preserved|spending)|type\s+i\s+error", re.I)
SEVERITY_RE = re.compile(
    r"\b(PANSS|MADRS|HAM-?D\w*|HDRS\w*|YMRS|CGI-?S|CGI-?BP-?S|BPRS|HAM-?A|NSA-?16|BNSS)\b[^.;\n]{0,80}?(≥|>=|>|at\s+least|between|greater\s+than\s+or\s+equal\s+to|of)\s*(\d{1,3})(?:\s*(?:and|to|-|–)\s*(\d{1,3}))?",
    re.I,
)


# ---------------------------------------------------------------------------
# Protocol classification
# ---------------------------------------------------------------------------


def _g(d: dict | None, *path, default=None):
    cur: Any = d or {}
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


def parse_eligibility(elig: dict[str, Any]) -> dict[str, Any]:
    text = elig.get("eligibilityCriteria") or ""
    inc, exc = [], []
    mode = None
    for raw in re.split(r"\n+", text):
        line = raw.strip(" *-•\t")
        if not line:
            continue
        low = line.lower()
        if re.match(r"(key\s+)?inclusion\s+criteria", low):
            mode = "inc"
            continue
        if re.match(r"(key\s+)?exclusion\s+criteria", low):
            mode = "exc"
            continue
        if mode == "inc":
            inc.append(line)
        elif mode == "exc":
            exc.append(line)
    cutoffs = []
    for m in SEVERITY_RE.finditer(text):
        cutoffs.append({"scale": m.group(1).upper().replace("HAMD", "HAM-D"), "operator": m.group(2), "min": int(m.group(3)),
                        "max": int(m.group(4)) if m.group(4) else None, "verbatim": text[max(0, m.start() - 20): m.end() + 20].strip()})
    return {
        "min_age": parse_age_years(elig.get("minimumAge")), "max_age": parse_age_years(elig.get("maximumAge")),
        "sex": elig.get("sex"), "healthy_volunteers": elig.get("healthyVolunteers"), "std_ages": elig.get("stdAges"),
        "inclusion": inc[:60], "exclusion": exc[:80], "severity_cutoffs": cutoffs,
    }


def parse_ctgov_study(study: dict[str, Any], asset_term_list: list[str], competitor_terms: dict[str, list[str]] | None = None) -> dict[str, Any]:
    ps = study.get("protocolSection", {}) or {}
    ident = ps.get("identificationModule", {}) or {}
    status = ps.get("statusModule", {}) or {}
    spons = ps.get("sponsorCollaboratorsModule", {}) or {}
    cond = ps.get("conditionsModule", {}) or {}
    design = ps.get("designModule", {}) or {}
    arms_mod = ps.get("armsInterventionsModule", {}) or {}
    outcomes = ps.get("outcomesModule", {}) or {}
    elig = ps.get("eligibilityModule", {}) or {}
    locs = _g(ps, "contactsLocationsModule", "locations", default=[]) or []
    refs = _g(ps, "referencesModule", "references", default=[]) or []
    desc = ps.get("descriptionModule", {}) or {}

    nct = ident.get("nctId")
    title = ident.get("officialTitle") or ident.get("briefTitle")
    lead = spons.get("leadSponsor", {}) or {}
    raw_class = (lead.get("class") or "UNKNOWN").upper()
    sponsor_class = "INDUSTRY" if raw_class == "INDUSTRY" else "ACADEMIC_IIS"
    industry_collab = any((c.get("class") or "").upper() == "INDUSTRY" for c in spons.get("collaborators", []) or [])

    phases = design.get("phases") or []
    phase_max = max((PHASE_NUM.get(p, 0) for p in phases), default=0) or None
    dinfo = design.get("designInfo", {}) or {}
    allocation = dinfo.get("allocation")
    model = dinfo.get("interventionModel") or dinfo.get("observationalModel")
    masking = _g(dinfo, "maskingInfo", "masking")
    study_type = design.get("studyType")
    enrollment = _g(design, "enrollmentInfo", "count")

    arms = []
    for a in arms_mod.get("armGroups", []) or []:
        arms.append({"label": a.get("label"), "type": a.get("type"), "description": a.get("description"),
                     "interventions": a.get("interventionNames") or []})
    interventions = arms_mod.get("interventions", []) or []
    has_placebo = any((a.get("type") or "") == "PLACEBO_COMPARATOR" for a in arms) or any(
        "placebo" in (i.get("name") or "").lower() for i in interventions)
    active_comps: list[str] = []
    for a in arms:
        if (a.get("type") or "") == "ACTIVE_COMPARATOR":
            for nm in a["interventions"] or [a.get("label") or ""]:
                nm2 = re.sub(r"^(Drug|Biological|Other|Device|Behavioral):\s*", "", nm)
                if nm2 and not text_mentions_asset(nm2, asset_term_list) and "placebo" not in nm2.lower():
                    active_comps.append(nm2)
    h2h_with: list[str] = []
    for comp_name, terms in (competitor_terms or {}).items():
        names = " ; ".join(active_comps + [i.get("name") or "" for i in interventions] +
                           [" ".join(i.get("otherNames") or []) for i in interventions])
        if text_mentions_asset(names, terms):
            h2h_with.append(comp_name)

    blob = " ".join(filter(None, [title, ident.get("briefTitle"), desc.get("briefSummary"), (desc.get("detailedDescription") or "")[:4000],
                                  " ".join((a.get("description") or "") for a in arms)])).lower()
    prim = outcomes.get("primaryOutcomes", []) or []
    sec = outcomes.get("secondaryOutcomes", []) or []
    duration = max((w for w in (parse_weeks(o.get("timeFrame")) for o in prim) if w), default=None)

    if re.search(r"randomi[sz]ed[-\s]withdrawal|relapse[-\s]prevention|prevention\s+of\s+(relapse|recurrence)|time\s+to\s+(first\s+)?(relapse|recurrence)|maintenance\s+(of\s+)?(effect|efficacy|treatment)|double[-\s]blind\s+withdrawal", blob):
        archetype = "RANDOMIZED_WITHDRAWAL"
    elif study_type == "OBSERVATIONAL":
        archetype = "OBSERVATIONAL"
    elif model == "SINGLE_GROUP" and (masking in (None, "NONE")):
        archetype = "OPEN_LABEL_SINGLE_ARM"
    elif model == "CROSSOVER":
        archetype = "CROSSOVER"
    elif model == "PARALLEL" and has_placebo:
        archetype = "ACUTE_PARALLEL_PLACEBO" if (duration or 0) <= 12 else "LONG_TERM_PARALLEL_PLACEBO"
    elif model == "PARALLEL":
        archetype = "PARALLEL_ACTIVE"
    else:
        archetype = "OTHER"
    adjunctive = bool(re.search(r"adjunct|add[-\s]on|augment", blob))
    placebo_run_in = bool(re.search(r"placebo\s+(run[-\s]?in|lead[-\s]?in|washout)|single[-\s]blind\s+placebo", blob + " " + (elig.get("eligibilityCriteria") or "").lower()))
    flexible = bool(re.search(r"flexibl\w*[-\s]dos|flexible\s+dose|dose\s+range\s+of", blob))

    elig_p = parse_eligibility(elig)
    pop = "adult"
    if (elig_p["max_age"] is not None and elig_p["max_age"] < 18) or re.search(r"adolescen|pediatric|paediatric|child", (title or "").lower()):
        pop = "pediatric"
    elif elig_p["min_age"] is not None and elig_p["min_age"] >= 60:
        pop = "elderly"

    conditions = cond.get("conditions", []) or []
    ind_key = classify_indication(" ; ".join(conditions)) or classify_indication(title, ident.get("briefTitle"))
    countries = Counter((l.get("country") or "Unknown") for l in locs)
    pct_us = (countries.get("United States", 0) / sum(countries.values())) if countries else None
    start = _g(status, "startDateStruct", "date")
    start_year = int(start[:4]) if start and start[:4].isdigit() else None

    def outcome_list(items, kind):
        return [{"type": kind, "measure": o.get("measure"), "description": (o.get("description") or "")[:600],
                 "timeFrame": o.get("timeFrame"), "weeks": parse_weeks(o.get("timeFrame")),
                 "scale": classify_scale(o.get("measure"), o.get("description"))} for o in items]

    pmids_result = [r.get("pmid") for r in refs if r.get("pmid") and (r.get("type") or "") in ("RESULT", "DERIVED")]
    pmids_all = [r.get("pmid") for r in refs if r.get("pmid")]
    return {
        "nct_id": nct, "title": title, "brief_title": ident.get("briefTitle"), "acronym": ident.get("acronym"),
        "overall_status": status.get("overallStatus"), "start_year": start_year,
        "sponsor_name": lead.get("name"), "sponsor_class": sponsor_class, "sponsor_class_raw": raw_class,
        "industry_collaborator": industry_collab, "phase": "/".join(phases) if phases else "NA", "phase_max": phase_max,
        "study_type": study_type, "allocation": allocation, "design_model": model, "masking": masking,
        "randomized": allocation == "RANDOMIZED", "design_archetype": archetype, "adjunctive": adjunctive,
        "placebo_run_in": placebo_run_in, "flexible_dose": flexible, "has_placebo": has_placebo,
        "active_comparators": active_comps, "head_to_head_with": h2h_with, "sample_size": enrollment,
        "duration_weeks": duration, "conditions": conditions, "indication_key": ind_key, "population": pop,
        "countries": dict(countries), "pct_us_sites": pct_us, "eligibility": elig_p, "arms": arms,
        "registered_outcomes": {"primary": outcome_list(prim, "PRIMARY"), "secondary": outcome_list(sec, "SECONDARY")},
        "has_results": bool(study.get("hasResults")), "linked_pmids": sorted(set(pmids_all)), "result_pmids": pmids_result,
    }


# ---------------------------------------------------------------------------
# Results parsing
# ---------------------------------------------------------------------------


def _group_role(title: str | None, desc: str | None, asset_terms: list[str], single_asset_trial: bool) -> str:
    t = f"{title or ''} {desc or ''}"
    if re.search(r"placebo", title or "", re.I) and not text_mentions_asset(title, asset_terms):
        return "placebo"
    if text_mentions_asset(t, asset_terms):
        return "asset"
    if re.search(r"placebo", t, re.I):
        return "placebo"
    if re.search(r"\btotal\b|overall|all\s+participants", title or "", re.I):
        return "total"
    return "asset" if single_asset_trial else "other"


def _pick_measurements(measure: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str | None]:
    classes = measure.get("classes") or []
    if not classes:
        return {}, None
    chosen = classes[-1] if len(classes) > 1 else classes[0]
    cats = chosen.get("categories") or []
    if not cats:
        return {}, chosen.get("title")
    cat = cats[0]
    for c in cats:
        if re.search(r"overall|total|week", c.get("title") or "", re.I):
            cat = c
    out = {}
    for m in cat.get("measurements") or []:
        out[m.get("groupId")] = m
    return out, chosen.get("title")


def _denoms(measure: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in measure.get("denoms") or []:
        if (d.get("units") or "").lower() in ("participants", "participant"):
            for c in d.get("counts") or []:
                v = parse_number(c.get("value"))
                if v is not None:
                    out[c.get("groupId")] = int(v)
    if not out and measure.get("denoms"):
        for c in measure["denoms"][0].get("counts") or []:
            v = parse_number(c.get("value"))
            if v is not None:
                out[c.get("groupId")] = int(v)
    return out


def _fieldcite(nct: str, **fields: Any) -> str:
    parts = [f'{k}="{v}"' for k, v in fields.items() if v not in (None, "")]
    return f"{nct} resultsSection: " + "; ".join(parts)


def parse_results(study: dict[str, Any], meta: dict[str, Any], asset_terms: list[str]) -> dict[str, Any]:
    rs = study.get("resultsSection") or {}
    nct = meta["nct_id"]
    single_asset = not meta["active_comparators"]
    endpoints: list[dict[str, Any]] = []

    # --- participant flow → dropout ---------------------------------------
    dropout: dict[str, Any] = {}
    pf = rs.get("participantFlowModule") or {}
    gtitles = {g.get("id"): g.get("title") for g in pf.get("groups") or []}
    periods = pf.get("periods") or []
    if periods:
        started, completed = {}, {}
        for ms in periods[0].get("milestones") or []:
            tgt = started if (ms.get("type") or "").upper() == "STARTED" else completed if (ms.get("type") or "").upper() == "COMPLETED" else None
            if tgt is None:
                continue
            for a in ms.get("achievements") or []:
                v = parse_number(a.get("numSubjects"))
                if v is not None:
                    tgt[a.get("groupId")] = v
        per = {}
        for gid, s in started.items():
            c = completed.get(gid)
            if s and c is not None:
                per[gtitles.get(gid) or gid] = round(1 - c / s, 4)
        if per:
            tot_s = sum(started.values())
            tot_c = sum(completed.get(g, 0) for g in started)
            dropout = {"by_arm": per, "overall": round(1 - tot_c / tot_s, 4) if tot_s else None}

    # --- baseline severity --------------------------------------------------
    baseline: dict[str, Any] = {}
    bm = rs.get("baselineCharacteristicsModule") or {}
    btitles = {g.get("id"): g.get("title") for g in bm.get("groups") or []}
    for meas in bm.get("measures") or []:
        sk = classify_scale(meas.get("title"), meas.get("description"))
        if not sk or SCALE_BY_KEY[sk].kind not in ("continuous_efficacy",):
            continue
        vals, _ = _pick_measurements(meas)
        groups = {btitles.get(g) or g: parse_number(m.get("value")) for g, m in vals.items()}
        total = next((v for t, v in groups.items() if re.search(r"total", t or "", re.I)), None)
        if total is None and groups:
            nums = [v for v in groups.values() if v is not None]
            total = sum(nums) / len(nums) if nums else None
        baseline.setdefault(sk, {"mean": total, "by_arm": groups, "measure": meas.get("title"),
                                 "dispersion": meas.get("dispersionType")})

    # --- outcome measures ---------------------------------------------------------
    placebo_response: dict[str, Any] = {}
    om = rs.get("outcomeMeasuresModule") or {}
    for meas in om.get("outcomeMeasures") or []:
        mtype = (meas.get("type") or "").upper()
        title = meas.get("title") or ""
        sk = classify_scale(title, meas.get("description"))
        if not sk:
            continue
        sdef = SCALE_BY_KEY[sk]
        tp = parse_weeks(meas.get("timeFrame")) or meas_weeks_fallback(meta)
        groups = {g.get("id"): g for g in meas.get("groups") or []}
        roles = {gid: _group_role(g.get("title"), g.get("description"), asset_terms, single_asset) for gid, g in groups.items()}
        vals, class_title = _pick_measurements(meas)
        denoms = _denoms(meas)
        param = (meas.get("paramType") or "").upper().replace(" ", "_")
        disp = (meas.get("dispersionType") or "").upper().replace(" ", "_")  # registry mixes "Standard Error" / STANDARD_ERROR
        unit = (meas.get("unitOfMeasure") or "").lower()
        is_count = param in ("COUNT_OF_PARTICIPANTS",) or ("participant" in unit and param in ("NUMBER", "COUNT_OF_PARTICIPANTS"))
        is_pct = "percent" in unit or "%" in unit
        pbo_gid = next((g for g, r in roles.items() if r == "placebo"), None)

        if mtype == "PRIMARY" and pbo_gid and pbo_gid in vals and not is_count:
            pv = parse_number(vals[pbo_gid].get("value"))
            if pv is not None and sk not in placebo_response:
                b = baseline.get(sk, {}).get("mean")
                placebo_response[sk] = {"placebo_change": pv, "baseline": b,
                                        "pct_change": round(100 * pv / b, 1) if b else None, "timepoint_weeks": tp}

        multiplicity_text = " ".join(
            filter(None, [meas.get("description"), *[(a.get("statisticalComment") or "") + " " + (a.get("estimateComment") or "") + " " +
                                                     (a.get("groupDescription") or "") + " " + (a.get("methodComment") or "")
                                                     for a in meas.get("analyses") or []]]))
        mult = True if mtype == "PRIMARY" else (True if MULTIPLICITY_RE.search(multiplicity_text or "") else None)
        common = {
            "endpoint_scale_name": sk, "endpoint_label": title[:500], "timepoint_weeks": tp,
            "is_primary": mtype == "PRIMARY", "is_post_hoc": mtype == "POST_HOC",
            "is_multiplicity_controlled": mult, "source_id": nct, "source_type": "ctgov",
        }
        used_pairs: set[tuple[str, str]] = set()
        for an in meas.get("analyses") or []:
            gids = an.get("groupIds") or []
            a_g = [g for g in gids if roles.get(g) == "asset"]
            c_g = [g for g in gids if roles.get(g) in ("placebo", "other")]
            if len(a_g) != 1 or len(c_g) != 1:
                continue
            tg, cg = a_g[0], c_g[0]
            used_pairs.add((tg, cg))
            ptype = (an.get("paramType") or "").lower()
            val = parse_number(an.get("paramValue"))
            lo, hi = parse_number(an.get("ciLowerLimit")), parse_number(an.get("ciUpperLimit"))
            ci_pct = parse_number(an.get("ciPctValue")) or 95.0
            p, ptxt = parse_p_value(an.get("pValue"))
            n_t, n_c = denoms.get(tg), denoms.get(cg)
            rec = {**common, "arm_name": groups[tg].get("title"), "comparator_arm": groups[cg].get("title"),
                   "comparator_is_placebo": roles.get(cg) == "placebo", "value": val, "ci_95_lower": lo, "ci_95_upper": hi,
                   "p_value": p, "p_value_text": ptxt, "n_treatment": n_t, "n_comparator": n_c}
            cite = _fieldcite(nct, title=title, groups=f"{groups[tg].get('title')} vs {groups[cg].get('title')}",
                               paramType=an.get("paramType"), paramValue=an.get("paramValue"),
                               ciLowerLimit=an.get("ciLowerLimit"), ciUpperLimit=an.get("ciUpperLimit"), pValue=an.get("pValue"),
                               statisticalMethod=an.get("statisticalMethod"))
            rec["verbatim_quote"] = cite
            if "hazard" in ptype:
                rec["effect_size_type"] = "HR"
                rec["favorable"] = (val is not None and val < 1) if sdef.lower_is_better else (val is not None and val > 1)
                rec["significant"] = (hi is not None and hi < 1) or (lo is not None and lo > 1) or (p is not None and p < 0.05)
                lh = stats.log_hr_from_ci(val, lo, hi) if val and lo and hi else None
                if lh:
                    rec["cohens_d"] = -lh[0] * 0.5513 if sdef.lower_is_better else lh[0] * 0.5513  # Chinn (2000) logOR→d; approx for logHR
                    rec["cohens_d_var"] = lh[1] * 0.5513 ** 2
            elif "odds" in ptype:
                rec["effect_size_type"] = "OR"
                rec["significant"] = (lo is not None and lo > 1) or (hi is not None and hi < 1) or (p is not None and p < 0.05)
                rec["favorable"] = (val is not None and val > 1) if not sdef.kind.endswith("safety") else (val is not None and val < 1)
            elif "risk diff" in ptype:
                rec["effect_size_type"] = "RD"
                rec["risk_difference"] = val / 100 if val is not None and abs(val) > 1 else val
                rec["nnt_nnh"] = stats.nnt_from_rd(rec["risk_difference"])
                rec["significant"] = (lo is not None and hi is not None and (lo > 0 or hi < 0)) or (p is not None and p < 0.05)
                rec["favorable"] = (rec["risk_difference"] or 0) > 0 if sdef.kind == "binary_efficacy" else (rec["risk_difference"] or 0) < 0
            else:
                rec["effect_size_type"] = "LS_MEAN_DIFF"
                d = None
                if val is not None and lo is not None and hi is not None and n_t and n_c:
                    d = stats.cohens_d_from_ci(val, lo, hi, n_t, n_c, ci_pct=ci_pct, lower_is_better=sdef.lower_is_better)
                if d is None and val is not None and p is not None and n_t and n_c:
                    d = stats.cohens_d_from_p((-val if sdef.lower_is_better else val), p, n_t, n_c)
                if d:
                    rec["cohens_d"], rec["cohens_d_var"] = d
                rec["significant"] = (p is not None and p < 0.05) or (lo is not None and hi is not None and (lo > 0 or hi < 0))
                if val is not None:
                    rec["favorable"] = (val < 0) if sdef.lower_is_better else (val > 0)
                if sdef.kind == "continuous_safety" and val is not None:
                    # weight / prolactin / metabolic: increase vs comparator = harm
                    rec["favorable"] = val <= 0
            endpoints.append(rec)

        # groups without an analysis: compute from arm-level summaries
        for tg in [g for g, r in roles.items() if r == "asset"]:
            if not pbo_gid or (tg, pbo_gid) in used_pairs or tg not in vals or pbo_gid not in vals:
                continue
            mt, mc = vals[tg], vals[pbo_gid]
            vt, vc = parse_number(mt.get("value")), parse_number(mc.get("value"))
            n_t, n_c = denoms.get(tg), denoms.get(pbo_gid)
            if vt is None or vc is None:
                continue
            cite = _fieldcite(nct, title=title, timepoint=class_title, paramType=meas.get("paramType"), unit=meas.get("unitOfMeasure"),
                               treatment=f"{groups[tg].get('title')}: {mt.get('value')} ({mt.get('spread') or ''})",
                               placebo=f"{groups[pbo_gid].get('title')}: {mc.get('value')} ({mc.get('spread') or ''})",
                               dispersionType=meas.get("dispersionType"))
            rec = {**common, "arm_name": groups[tg].get("title"), "comparator_arm": groups[pbo_gid].get("title"),
                   "comparator_is_placebo": True, "n_treatment": n_t, "n_comparator": n_c, "verbatim_quote": cite,
                   "p_value": None, "p_value_text": None}
            if is_count or is_pct or sdef.kind in ("binary_efficacy", "binary_safety"):
                if is_count:
                    if not (n_t and n_c):
                        continue
                    pt, pc = vt / n_t, vc / n_c
                elif is_pct or vt > 1 or vc > 1:
                    pt, pc = vt / 100, vc / 100
                else:
                    pt, pc = vt, vc
                if not (0 <= pt <= 1 and 0 <= pc <= 1):
                    continue
                rd = pt - pc
                rec.update(effect_size_type="INCIDENCE_PCT", value=round(pt * 100, 2), risk_treatment=pt, risk_comparator=pc,
                           risk_difference=rd, nnt_nnh=stats.nnt_from_rd(rd),
                           favorable=(rd > 0) if sdef.kind == "binary_efficacy" else (rd <= 0))
                if n_t and n_c:
                    rdx = stats.risk_difference(round(pt * n_t), n_t, round(pc * n_c), n_c)
                    rec["ci_95_lower"], rec["ci_95_upper"] = rdx["rd_lo"], rdx["rd_hi"]
                    rec["significant"] = rdx["rd_lo"] is not None and (rdx["rd_lo"] > 0 or rdx["rd_hi"] < 0)
            else:
                st, sc = parse_number(mt.get("spread")), parse_number(mc.get("spread"))
                if "STANDARD_ERROR" in disp:
                    st, sc = stats.sd_from_se(st, n_t), stats.sd_from_se(sc, n_c)
                elif "STANDARD_DEVIATION" not in disp:
                    st = sc = None
                diff = vt - vc
                rec.update(effect_size_type="LS_MEAN_DIFF" if "LEAST_SQUARES" in param else "LS_MEAN_DIFF", value=round(diff, 4))
                if st and sc and n_t and n_c:
                    d = stats.cohens_d_from_means(vt, vc, st, sc, n_t, n_c, lower_is_better=sdef.lower_is_better)
                    if d:
                        rec["cohens_d"], rec["cohens_d_var"] = d
                        se = (d[1]) ** 0.5
                        rec["significant"] = abs(d[0]) > 1.96 * se
                rec["favorable"] = (diff < 0) if sdef.lower_is_better else (diff > 0)
                if sdef.kind == "continuous_safety":
                    rec["favorable"] = diff <= 0
            endpoints.append(rec)

    # --- adverse events ---------------------------------------------------------------
    ae = rs.get("adverseEventsModule") or {}
    eg = {g.get("id"): g for g in ae.get("eventGroups") or []}
    eroles = {gid: _group_role(g.get("title"), g.get("description"), asset_terms, single_asset) for gid, g in eg.items()}
    pbo_eg = next((g for g, r in eroles.items() if r == "placebo"), None)
    cat_inc: dict[str, dict[str, tuple[float, int, int, str]]] = {}
    for ev in (ae.get("otherEvents") or []) + (ae.get("seriousEvents") or []):
        sk = classify_ae_term(ev.get("term") or "")
        if not sk:
            continue
        for st in ev.get("stats") or []:
            gid = st.get("groupId")
            aff, risk = parse_number(st.get("numAffected")), parse_number(st.get("numAtRisk"))
            if aff is None or not risk:
                continue
            p = aff / risk
            cur = cat_inc.setdefault(sk, {}).get(gid)
            if cur is None or p > cur[0]:
                cat_inc[sk][gid] = (p, int(aff), int(risk), ev.get("term"))
    if pbo_eg:
        for sk, by_g in cat_inc.items():
            pc_t = by_g.get(pbo_eg)
            for gid, r in eroles.items():
                if r != "asset" or gid not in by_g:
                    continue
                pt, xt, nt, term = by_g[gid]
                pc, xc, nc = (pc_t[0], pc_t[1], pc_t[2]) if pc_t else (0.0, 0, int(eg[pbo_eg].get("otherNumAtRisk") or 0) or None)
                if not nc:
                    continue
                rdx = stats.risk_difference(xt, nt, xc, nc)
                cite = _fieldcite(nct, adverseEvent=term, group=f"{eg[gid].get('title')}: {xt}/{nt}",
                                  placebo=f"{eg[pbo_eg].get('title')}: {xc}/{nc}", frequencyThreshold=ae.get("frequencyThreshold"))
                endpoints.append({
                    "endpoint_scale_name": sk, "endpoint_label": f"AE: {term} (category max)", "timepoint_weeks": meta.get("duration_weeks"),
                    "is_primary": False, "is_post_hoc": False, "is_multiplicity_controlled": None,
                    "arm_name": eg[gid].get("title"), "comparator_arm": eg[pbo_eg].get("title"), "comparator_is_placebo": True,
                    "effect_size_type": "INCIDENCE_PCT", "value": round(pt * 100, 2), "risk_treatment": pt, "risk_comparator": pc,
                    "risk_difference": rdx["rd"], "nnt_nnh": rdx["nnt"], "ci_95_lower": rdx["rd_lo"], "ci_95_upper": rdx["rd_hi"],
                    "n_treatment": nt, "n_comparator": nc, "favorable": (rdx["rd"] or 0) <= 0.02,
                    "significant": rdx["rd_lo"] is not None and rdx["rd_lo"] > 0,
                    "p_value": None, "p_value_text": None, "verbatim_quote": cite, "source_id": nct, "source_type": "ctgov",
                })
    placebo_rate = None
    if placebo_response:
        first = next(iter(placebo_response.values()))
        placebo_rate = first.get("placebo_change")
    return {"endpoints": endpoints, "dropout": dropout, "baseline": baseline, "placebo_response": placebo_response,
            "placebo_response_rate": placebo_rate}


def meas_weeks_fallback(meta: dict[str, Any]) -> float | None:
    return meta.get("duration_weeks")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def endpoint_tier_and_validity(study: ClinicalStudy, ep: dict[str, Any]) -> tuple[str, str, int, str]:
    ctx = EvidenceContext(
        source=study.source, sponsor_class=study.sponsor_class, phase_max=study.phase_max,
        randomized=(study.allocation == "RANDOMIZED") if study.allocation else None, study_type=study.study_type,
        sample_size=study.sample_size, is_primary=ep.get("is_primary"), is_multiplicity_controlled=ep.get("is_multiplicity_controlled"),
        is_post_hoc=ep.get("is_post_hoc"), significant=ep.get("significant"), favorable=ep.get("favorable"),
        is_head_to_head=bool(study.is_head_to_head) and not ep.get("comparator_is_placebo", True),
        label_confirmed=bool(ep.get("label_confirmed")), replicated=bool(ep.get("replicated")),
    )
    tier, tier_reason = evidence_tier(ctx)
    lvl, lvl_reason = validity_level(ctx)
    return tier, tier_reason, lvl, lvl_reason


def column_for(indication_key: str | None, population: str | None, adjunctive: bool, weeks: float | None) -> str:
    return ColumnKey(indication_key or "other", population or "adult", timepoint_bucket(weeks), adjunctive).encode()


async def ingest_trials_for_asset(session: Session, asset: Asset, terms: list[str], competitor_terms: dict[str, list[str]],
                                  indication_by_key: dict[str, Indication], *, max_trials: int, force_refresh: bool = False,
                                  say: Callable[[str], None] = log.info) -> dict[str, Any]:
    client = ClinicalTrialsClient()
    studies = await client.search_studies(intervention_terms=terms, max_studies=max_trials, force_refresh=force_refresh)
    say(f"ClinicalTrials.gov: {len(studies)} registry records for {asset.generic_name}")
    session.execute(delete(ClinicalStudy).where(ClinicalStudy.asset_id == asset.id, ClinicalStudy.source == "ctgov"))
    session.flush()
    counts = Counter()
    n_endpoints = 0
    for st in studies:
        meta = parse_ctgov_study(st, terms, {k: v for k, v in competitor_terms.items() if k != asset.generic_name})
        # registry search is full-text; require the asset to appear in interventions / arms / title
        iv = " ".join([" ".join(a["interventions"]) for a in meta["arms"]] + [meta["title"] or ""] +
                      [i.get("name", "") + " " + " ".join(i.get("otherNames") or []) for i in
                       (st.get("protocolSection", {}).get("armsInterventionsModule", {}).get("interventions") or [])])
        if not text_mentions_asset(iv, terms):
            continue
        res = parse_results(st, meta, terms) if meta["has_results"] else {"endpoints": [], "dropout": {}, "baseline": {},
                                                                          "placebo_response": {}, "placebo_response_rate": None}
        ind = indication_by_key.get(meta["indication_key"] or "")
        cs = ClinicalStudy(
            nct_id=meta["nct_id"], pmid=(meta["result_pmids"] or meta["linked_pmids"] or [None])[0], asset_id=asset.id,
            indication_id=ind.id if ind else None, source="ctgov", source_ref=meta["nct_id"], title=meta["title"],
            sponsor_name=meta["sponsor_name"], sponsor_class=meta["sponsor_class"], sponsor_class_raw=meta["sponsor_class_raw"],
            industry_collaborator=meta["industry_collaborator"], phase=meta["phase"], phase_max=meta["phase_max"],
            study_type=meta["study_type"], design_model=meta["design_model"], allocation=meta["allocation"], masking=meta["masking"],
            design_archetype=meta["design_archetype"], placebo_run_in=meta["placebo_run_in"], flexible_dose=meta["flexible_dose"],
            has_placebo=meta["has_placebo"], active_comparators_json=meta["active_comparators"],
            is_head_to_head=bool(meta["head_to_head_with"]), sample_size=meta["sample_size"], duration_weeks=meta["duration_weeks"],
            start_year=meta["start_year"], overall_status=meta["overall_status"], has_results=meta["has_results"],
            countries_json=meta["countries"], pct_us_sites=meta["pct_us_sites"], baseline_severity_json=res["baseline"] or None,
            placebo_response_rate=res["placebo_response_rate"], placebo_response_json=res["placebo_response"] or None,
            dropout_json=res["dropout"] or None,
            eligibility_json=meta["eligibility"], registered_outcomes_json={**meta["registered_outcomes"], "head_to_head_with": meta["head_to_head_with"],
                                                                            "adjunctive": meta["adjunctive"]},
            arms_json=meta["arms"], conditions_json=meta["conditions"], indication_key=meta["indication_key"], population=meta["population"],
            linked_pmids_json=meta["linked_pmids"],
        )
        session.add(cs)
        session.flush()
        counts[(meta["sponsor_class"], meta["phase_max"])] += 1
        best_t, best_v = None, 0
        for ep in res["endpoints"]:
            tier, _, lvl, _ = endpoint_tier_and_validity(cs, ep)
            col = column_for(meta["indication_key"], meta["population"], meta["adjunctive"], ep.get("timepoint_weeks"))
            sdef = SCALE_BY_KEY.get(ep["endpoint_scale_name"])
            session.add(ExtractedEndpoint(
                study_id=cs.id, asset_id=asset.id, indication_id=cs.indication_id, mvd_key=sdef.mvd_key if sdef else None,
                endpoint_scale_name=ep["endpoint_scale_name"], endpoint_label=ep.get("endpoint_label"), timepoint_weeks=ep.get("timepoint_weeks"),
                arm_name=ep.get("arm_name"), comparator_arm=ep.get("comparator_arm"), effect_size_type=ep["effect_size_type"],
                value=ep.get("value"), ci_95_lower=ep.get("ci_95_lower"), ci_95_upper=ep.get("ci_95_upper"), p_value=ep.get("p_value"),
                p_value_text=ep.get("p_value_text"), is_multiplicity_controlled=ep.get("is_multiplicity_controlled"),
                is_primary=ep.get("is_primary"), is_post_hoc=ep.get("is_post_hoc"), n_treatment=ep.get("n_treatment"),
                n_comparator=ep.get("n_comparator"), cohens_d=ep.get("cohens_d"), cohens_d_var=ep.get("cohens_d_var"),
                risk_treatment=ep.get("risk_treatment"), risk_comparator=ep.get("risk_comparator"), risk_difference=ep.get("risk_difference"),
                nnt_nnh=ep.get("nnt_nnh"), favorable=ep.get("favorable"), significant=ep.get("significant"), column_key=col,
                verbatim_quote=ep["verbatim_quote"], source_id=ep["source_id"], source_type="ctgov",
                extraction_method="deterministic_ctgov_results", evidence_tier=tier, validity_level=lvl,
            ))
            n_endpoints += 1
            best_t = tier if best_t is None or ["1A", "1B", "2", "3"].index(tier) < ["1A", "1B", "2", "3"].index(best_t) else best_t
            best_v = max(best_v, lvl)
        if best_t is None:
            tier, _, lvl, _ = endpoint_tier_and_validity(cs, {"is_primary": True})
            best_t, best_v = tier, min(lvl, 4)
        cs.evidence_tier, cs.validity_level = best_t, best_v
    session.flush()
    split = {
        "industry_phase3plus": sum(v for (c, p), v in counts.items() if c == "INDUSTRY" and (p or 0) >= 3),
        "industry_other": sum(v for (c, p), v in counts.items() if c == "INDUSTRY" and (p or 0) < 3),
        "academic_iis": sum(v for (c, _), v in counts.items() if c == "ACADEMIC_IIS"),
    }
    say(f"  kept {sum(counts.values())} trials ({split}); {n_endpoints} result endpoints")
    return {"trials": sum(counts.values()), "endpoints": n_endpoints, "split": split}


def get_existing_trial_rows(session: Session, asset_id: int) -> list[ClinicalStudy]:
    return session.execute(select(ClinicalStudy).where(ClinicalStudy.asset_id == asset_id)).scalars().all()
