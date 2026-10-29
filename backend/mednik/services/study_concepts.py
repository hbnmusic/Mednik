"""ADJACENCY C — Evidence gap → Phase 4 / RWE / IIS study concept translator.

Triggered from a white-space or low-validity (L1/L2) heatmap cell. Queries ClinicalTrials.gov for
how competitors (and the wider field) designed trials on the same endpoint in the same indication,
then assembles a synopsis: recommended design, precedent scales + timepoints (with NCT IDs),
sample size for 80 % power at α = 0.05 from historical effect sizes, and inclusion / exclusion
criteria ranked by how often precedent trials used them.
"""

from __future__ import annotations

import logging
import re
import statistics
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients import ClinicalTrialsClient
from ..models import Asset, ClinicalStudy, ExtractedEndpoint, StudyConcept
from ..taxonomy import SCALE_BY_KEY, ColumnKey, asset_terms, column_label, indication_label
from . import stats
from .trials_ingest import parse_ctgov_study

log = logging.getLogger("mednik.concepts")

SCALE_SEARCH_TERMS = {
    "PANSS_TOTAL": "PANSS", "PANSS_POSITIVE": "PANSS positive", "PANSS_NEGATIVE": "PANSS negative", "PANSS_NEG_MARDER": "negative symptoms",
    "NEG_OTHER": "negative symptoms", "MADRS": "MADRS", "HAMD": "Hamilton Depression", "YMRS": "Young Mania Rating Scale",
    "HAMA": "Hamilton Anxiety", "CGI_S": "CGI-S", "CGI_I": "CGI-I", "PSP": "Personal and Social Performance", "SDS": "Sheehan Disability Scale",
    "FAST": "functioning", "COGNITION": "cognition", "QOL": "quality of life", "RELAPSE": "relapse", "REMISSION": "remission",
    "RESPONSE": "response", "AKATHISIA": "akathisia", "EPS": "extrapyramidal", "SOMNOLENCE": "somnolence", "WEIGHT": "weight gain",
    "METABOLIC": "metabolic", "PROLACTIN": "prolactin", "ANTICHOLINERGIC": "anticholinergic", "GI": "nausea", "QTC": "QTc",
    "MANIC_SWITCH": "treatment-emergent mania", "SUICIDALITY": "C-SSRS", "DISCONTINUATION_AE": "discontinuation",
}

INCLUSION_FAMILIES = [
    ("DSM-5 / DSM-IV-TR confirmed diagnosis", r"\bdsm"),
    ("Structured diagnostic interview (MINI / SCID)", r"\bm\.?i\.?n\.?i\b|scid|structured\s+clinical\s+interview"),
    ("Baseline CGI-S threshold", r"cgi-?s"),
    ("Acute exacerbation / recent hospitalization", r"acute\s+exacerbat|hospitali[sz]"),
    ("Clinically stable / stable treatment", r"\bstable\b"),
    ("Inadequate response to prior / current treatment", r"inadequate\s+response|partial\s+response|insufficient\s+response"),
    ("Effective contraception", r"contracept"),
    ("Reliable caregiver / informant", r"caregiver|informant"),
    ("Body mass index range", r"\bbmi\b|body\s+mass\s+index"),
]
EXCLUSION_FAMILIES = [
    ("Substance / alcohol use disorder", r"substance|alcohol|drug\s+(abuse|dependence|use\s+disorder)"),
    ("Suicide risk (C-SSRS / recent attempt)", r"suicid|c-?ssrs"),
    ("Treatment resistance / clozapine", r"treatment[-\s]resist|clozapine"),
    ("Other primary psychiatric diagnosis", r"schizoaffective|personality\s+disorder|primary\s+diagnosis\s+other|other\s+(axis|psychiatric)|bipolar|autism"),
    ("Unstable / clinically significant medical illness", r"unstable|clinically\s+significant\s+(medical|cardiac|hepatic|renal|abnormal)"),
    ("QTc prolongation / ECG abnormality", r"qtc|qt\s+interval|ecg"),
    ("Pregnancy / lactation", r"pregnan|breast[-\s]?feed|lactat"),
    ("Recent long-acting injectable antipsychotic", r"long[-\s]acting|depot|injectable"),
    ("Recent ECT / neuromodulation", r"electroconvulsive|\bect\b|tms|transcranial"),
    ("Strong CYP3A4 / CYP2D6 inhibitors or inducers", r"cyp\s?3a4|cyp\s?2d6|strong\s+inhibitor|inducer"),
    ("Hepatic impairment / abnormal LFTs", r"hepatic|liver|alt\b|ast\b"),
    ("Tardive dyskinesia / movement disorder", r"tardive|movement\s+disorder|parkinson"),
    ("Neuroleptic malignant syndrome history", r"neuroleptic\s+malignant"),
]


def _concept_design(row_kind: str, row_key: str, ck: ColumnKey, cell_state: str, precedents: list[dict[str, Any]], weeks: float) -> dict[str, Any]:
    h2h = sum(1 for p in precedents if p.get("active_comparators"))
    if row_key == "RELAPSE" or ck.timepoint == "maintenance":
        return {"type": "Phase 4 randomized withdrawal", "title": f"Randomized, double-blind, placebo-controlled withdrawal study ({int(weeks)}-week double-blind phase)",
                "rationale": "Maintenance / relapse-prevention evidence is typically generated with open-label stabilisation followed by randomised withdrawal.",
                "arms": ["Continue study drug", "Switch to placebo"], "blinding": "Double-blind", "primary_analysis": "Time to relapse (Cox model / log-rank)"}
    if row_kind in ("binary_safety", "continuous_safety"):
        if h2h:
            return {"type": "Phase 4 randomized active-controlled", "title": f"Randomized, rater-blinded, active-controlled {int(weeks)}-week tolerability study",
                    "rationale": f"{h2h} precedent trial(s) used an active comparator for this endpoint; a comparative design gives Tier 1A potential.",
                    "arms": ["Study drug (label dose)", "Active comparator (label dose)"], "blinding": "Rater-blinded", "primary_analysis": "Between-group difference (ANCOVA / logistic regression)"}
        return {"type": "Prospective open-label switch + RWE", "title": f"{int(weeks)}-week prospective open-label switch study with standardised {SCALE_BY_KEY[row_key].label} assessment",
                "rationale": "Tolerability white space; a pragmatic switch study plus a claims/EHR cohort provides real-world incidence quickly.",
                "arms": ["Switch from prior antipsychotic to study drug"], "blinding": "Open-label (blinded rater where feasible)", "primary_analysis": "Change from baseline / incidence (95% CI)"}
    if cell_state == "white_space" and ck.population != "adult":
        return {"type": "Phase 3b/4 population-specific RCT", "title": f"Randomized, double-blind, placebo-controlled {int(weeks)}-week study in {ck.population} patients",
                "rationale": "No evidence exists in this population; a dedicated controlled study is required for labeling or guideline consideration.",
                "arms": ["Study drug", "Placebo"], "blinding": "Double-blind", "primary_analysis": "MMRM change from baseline"}
    return {"type": "Phase 4 / IIS randomized placebo-controlled", "title": f"Randomized, double-blind, placebo-controlled {int(weeks)}-week study with {SCALE_BY_KEY[row_key].label} as primary endpoint",
            "rationale": "Efficacy / function white space or low-validity cell; a controlled design with this scale as primary endpoint is needed to move validity to L4.",
            "arms": ["Study drug", "Placebo"] + (["Active reference arm (optional)"] if h2h else []), "blinding": "Double-blind",
            "primary_analysis": "MMRM change from baseline (multiplicity-controlled key secondaries)"}


def _families(precedents: list[dict[str, Any]], key: str, families: list[tuple[str, str]]) -> list[dict[str, Any]]:
    out = []
    n = max(1, len(precedents))
    for label, pat in families:
        hits = []
        for p in precedents:
            for line in p["eligibility"].get(key, []):
                if re.search(pat, line, re.I):
                    hits.append((p["nct_id"], line))
                    break
        if hits:
            out.append({"criterion": label, "pct_precedents": round(100 * len(hits) / n), "n": len(hits),
                        "example": hits[0][1][:300], "example_nct": hits[0][0], "ncts": [h[0] for h in hits][:8]})
    return sorted(out, key=lambda x: -x["n"])


async def generate_study_concept(session: Session, asset: Asset, row_key: str, col_key: str, competitors: list[Asset], *,
                                 cell_state: str = "white_space", force_refresh: bool = False) -> dict[str, Any]:
    ck = ColumnKey.decode(col_key)
    sdef = SCALE_BY_KEY[row_key]
    ind_label = indication_label(ck.indication_key)
    cond = re.sub(r"\s*\(.*?\)|\s*/.*$", "", ind_label).replace("Bipolar I ", "Bipolar ")
    client = ClinicalTrialsClient()
    raw: dict[str, dict[str, Any]] = {}
    comp_terms: list[str] = []
    for c in competitors:
        comp_terms += asset_terms(c.generic_name, c.brand_name, c.synonyms_json)
    queries = []
    if comp_terms:
        queries.append({"condition": cond, "intervention_terms": comp_terms, "max_studies": 200})
    queries.append({"condition": cond, "term": SCALE_SEARCH_TERMS.get(row_key, sdef.label), "max_studies": 150})
    for q in queries:
        try:
            for st in await client.search_studies(force_refresh=force_refresh, **q):
                nct = st.get("protocolSection", {}).get("identificationModule", {}).get("nctId")
                if nct:
                    raw[nct] = st
        except Exception as exc:  # noqa: BLE001
            log.warning("precedent query failed: %s", exc)
    precedents = []
    for nct, st in raw.items():
        meta = parse_ctgov_study(st, [])
        outs = [o for o in meta["registered_outcomes"]["primary"] + meta["registered_outcomes"]["secondary"] if o.get("scale") == row_key]
        if not outs:
            continue
        ivs = sorted({i.get("name") for i in st.get("protocolSection", {}).get("armsInterventionsModule", {}).get("interventions", []) or [] if i.get("name")})
        is_comp = any(t.lower() in " ".join(ivs).lower() for t in comp_terms)
        precedents.append({**meta, "matched_outcomes": outs, "interventions": ivs, "is_competitor_trial": is_comp})
    precedents.sort(key=lambda p: (not p["is_competitor_trial"], -(p["phase_max"] or 0), -(p["sample_size"] or 0)))

    # timepoint
    wks = [o["weeks"] for p in precedents for o in p["matched_outcomes"] if o.get("weeks")]
    default_w = {"acute": 6.0, "intermediate": 12.0, "maintenance": 52.0}[ck.timepoint]
    in_bucket = [w for w in wks if (w <= 8.5) == (ck.timepoint == "acute") and (w > 26) == (ck.timepoint == "maintenance")]
    weeks = float(statistics.median(in_bucket)) if in_bucket else default_w

    # historical effect sizes (any asset in DB, same scale & indication, placebo comparisons)
    eps = session.execute(select(ExtractedEndpoint, ClinicalStudy).join(ClinicalStudy, ExtractedEndpoint.study_id == ClinicalStudy.id).where(
        ExtractedEndpoint.endpoint_scale_name == row_key, ClinicalStudy.indication_key == ck.indication_key)).all()
    ds = [abs(e.cohens_d) for e, _ in eps if e.cohens_d is not None and e.evidence_tier in ("1A", "1B", "2")]
    rds = [(e.risk_comparator, e.risk_difference) for e, _ in eps if e.risk_difference is not None and e.risk_comparator is not None]
    drops = [s.dropout_json["overall"] for _, s in eps if s.dropout_json and s.dropout_json.get("overall") is not None]
    drops += [p.get("dropout_overall") for p in precedents if p.get("dropout_overall")]
    dropout = float(statistics.median(drops)) if drops else 0.25
    sample: dict[str, Any]
    if sdef.kind in ("binary_safety", "binary_efficacy") and rds:
        pc = statistics.median(r[0] for r in rds)
        rd = statistics.median(r[1] for r in rds) * 0.8
        pt = min(0.99, max(0.01, pc + rd))
        n = stats.n_per_arm_proportions(pt, pc)
        sample = {"basis": "two-proportion test", "assumption": f"comparator rate {pc:.1%}, target difference {rd * 100:+.1f} pp (80% of historical median, k={len(rds)})",
                  "n_per_arm": n, "n_per_arm_with_dropout": stats.inflate_for_dropout(n, dropout)}
    else:
        if ds:
            d = statistics.median(ds) * 0.8
            basis = f"d = {d:.2f} (80% of historical median |d| {statistics.median(ds):.2f}, k={len(ds)} endpoints in {ind_label})"
        else:
            d = 0.35
            basis = "d = 0.35 (no historical standardized effect for this scale in the database — default assumption; refine before use)"
        n = stats.n_per_arm_continuous(d)
        sample = {"basis": "two-sample comparison of means (MMRM-equivalent)", "assumption": basis, "n_per_arm": n,
                  "n_per_arm_with_dropout": stats.inflate_for_dropout(n, dropout)}
    sample.update({"alpha": 0.05, "power": 0.80, "dropout_assumed": round(dropout, 3),
                   "total_n": (sample["n_per_arm_with_dropout"] or 0) * 2 if sample.get("n_per_arm_with_dropout") else None})

    design = _concept_design(sdef.kind, row_key, ck, cell_state, precedents, weeks)
    if design["type"].startswith("Prospective open-label") and sample.get("n_per_arm_with_dropout"):
        sample["note"] = "Single-arm design: N shown is the per-arm figure from the two-arm calculation as a conservative planning anchor."

    cutoffs = defaultdict(list)
    for p in precedents:
        for c in p["eligibility"].get("severity_cutoffs", []):
            cutoffs[c["scale"]].append(c["min"])
    severity = [{"scale": k, "typical_min": Counter(v).most_common(1)[0][0], "n_precedents": len(v)} for k, v in cutoffs.items()]
    ages_min = [p["eligibility"].get("min_age") for p in precedents if p["eligibility"].get("min_age") is not None]
    ages_max = [p["eligibility"].get("max_age") for p in precedents if p["eligibility"].get("max_age") is not None]

    precedent_rows = [{
        "nct_id": p["nct_id"], "sponsor": p["sponsor_name"], "sponsor_class": p["sponsor_class"], "phase": p["phase"],
        "design": p["design_archetype"], "n": p["sample_size"], "status": p["overall_status"], "competitor_trial": p["is_competitor_trial"],
        "interventions": p["interventions"][:4], "outcomes": [{"type": o["type"], "measure": o["measure"], "time_frame": o["timeFrame"], "weeks": o["weeks"]}
                                                              for o in p["matched_outcomes"]][:3],
    } for p in precedents[:25]]
    secondary_scales = Counter()
    for p in precedents:
        for o in p["registered_outcomes"]["primary"] + p["registered_outcomes"]["secondary"]:
            if o.get("scale") and o["scale"] != row_key:
                secondary_scales[o["scale"]] += 1
    synopsis = {
        "asset": {"generic": asset.generic_name, "brand": asset.brand_name},
        "gap": {"row": row_key, "row_label": sdef.label, "column": col_key, "column_label": column_label(col_key), "cell_state": cell_state},
        "design": design, "duration_weeks": weeks,
        "primary_endpoint": f"{sdef.label} — change from baseline to week {weeks:g}" if sdef.kind.startswith("continuous") else f"{sdef.label} at week {weeks:g}",
        "secondary_endpoints": [{"scale": k, "label": SCALE_BY_KEY[k].label, "n_precedents": v} for k, v in secondary_scales.most_common(6)],
        "sample_size": sample,
        "population": {"age_range": [min(Counter(ages_min).most_common(1)[0][0], 99) if ages_min else 18,
                                     Counter(ages_max).most_common(1)[0][0] if ages_max else 65],
                       "severity_thresholds": severity},
        "inclusion": _families(precedents, "inclusion", INCLUSION_FAMILIES),
        "exclusion": _families(precedents, "exclusion", EXCLUSION_FAMILIES),
        "precedents": precedent_rows, "n_precedents": len(precedents),
        "queries": [{k: (v if not isinstance(v, list) else ", ".join(v)) for k, v in q.items()} for q in queries],
    }
    md = concept_markdown(synopsis)
    sc = StudyConcept(asset_id=asset.id, indication_id=None, row_key=row_key, column_key=col_key, synopsis_json=synopsis, markdown=md)
    session.add(sc)
    session.flush()
    synopsis["id"] = sc.id
    synopsis["markdown"] = md
    return synopsis


def concept_markdown(s: dict[str, Any]) -> str:
    a = s["asset"]
    name = f"{a['brand']} ({a['generic']})" if a.get("brand") else a["generic"]
    d = s["design"]
    ss = s["sample_size"]
    lines = [
        f"# Study Concept Synopsis — {name}",
        f"**Evidence gap:** {s['gap']['row_label']} · {s['gap']['column_label']} ({s['gap']['cell_state'].replace('_', ' ')})",
        "",
        "## Recommended design",
        f"**{d['type']}** — {d['title']}",
        f"- Rationale: {d['rationale']}",
        f"- Arms: {', '.join(d['arms'])}",
        f"- Blinding: {d['blinding']}",
        f"- Duration: {s['duration_weeks']:g} weeks",
        f"- Primary endpoint: {s['primary_endpoint']}",
        f"- Primary analysis: {d['primary_analysis']}",
        "",
        "## Secondary endpoints (frequency in precedent trials)",
        *[f"- {x['label']} ({x['n_precedents']} precedent trials)" for x in s["secondary_endpoints"]],
        "",
        "## Sample size (80% power, two-sided α = 0.05)",
        f"- Method: {ss['basis']}",
        f"- Assumption: {ss['assumption']}",
        f"- N per arm: {ss['n_per_arm']} → {ss['n_per_arm_with_dropout']} after {ss['dropout_assumed']:.0%} dropout; total ≈ {ss.get('total_n')}",
        *( [f"- Note: {ss['note']}"] if ss.get("note") else []),
        "",
        "## Population",
        f"- Age: {s['population']['age_range'][0]:g}–{s['population']['age_range'][1]:g} years (modal range in precedents)",
        *[f"- Typical {t['scale']} entry threshold ≥ {t['typical_min']} ({t['n_precedents']} precedents)" for t in s["population"]["severity_thresholds"]],
        "",
        "### Key inclusion criteria (share of precedent trials)",
        *[f"- {i['criterion']} — {i['pct_precedents']}% (e.g. {i['example_nct']}: “{i['example']}”)" for i in s["inclusion"][:8]],
        "",
        "### Key exclusion criteria (share of precedent trials)",
        *[f"- {i['criterion']} — {i['pct_precedents']}% (e.g. {i['example_nct']}: “{i['example']}”)" for i in s["exclusion"][:10]],
        "",
        f"## Precedent trials ({s['n_precedents']} on ClinicalTrials.gov using this endpoint)",
        "| NCT | Sponsor | Phase | Design | N | Endpoint (time frame) |",
        "|---|---|---|---|---|---|",
        *[f"| [{p['nct_id']}](https://clinicaltrials.gov/study/{p['nct_id']}) | {p['sponsor'] or ''} ({p['sponsor_class']}) | {p['phase']} | {p['design']} | {p['n'] or ''} | "
          f"{(p['outcomes'][0]['measure'] or '')[:80]} ({p['outcomes'][0]['time_frame'] or ''}) |" for p in s["precedents"][:15] if p["outcomes"]],
        "",
        "_Generated by mednik from live ClinicalTrials.gov records. Draft concept for internal medical review; assumptions must be validated by biostatistics._",
    ]
    return "\n".join(lines)
