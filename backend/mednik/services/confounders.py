"""ADJACENCY A — Statistical apples-to-apples normaliser (cross-trial confounder detector).

Compares the trial-design metadata behind an indirect Product A vs Product B comparison
and raises a Cross-Trial Confounder Alert when baseline severity differs by >10 %
or trial architectures diverge, with the fair-balance caveat text to use.
"""

from __future__ import annotations

import statistics
from typing import Any

from ..models import ClinicalStudy
from ..taxonomy import SCALE_BY_KEY

BASELINE_REL_THRESHOLD = 0.10
PLACEBO_REL_THRESHOLD = 0.25
US_SHARE_THRESHOLD = 0.30
ERA_GAP_YEARS = 6
DURATION_GAP_WEEKS = 2.0


def _median(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(statistics.median(vals), 2) if vals else None


def _baseline(studies: list[ClinicalStudy], scale: str | None) -> float | None:
    vals = []
    for s in studies:
        b = s.baseline_severity_json or {}
        if scale and scale in b and b[scale].get("mean") is not None:
            vals.append(b[scale]["mean"])
        elif not scale and b:
            first = next(iter(b.values()))
            if first.get("mean") is not None:
                vals.append(first["mean"])
    return _median(vals)


def _placebo_change(studies: list[ClinicalStudy], scale: str | None) -> float | None:
    vals = []
    for s in studies:
        pr = s.placebo_response_json or {}
        if scale and scale in pr and pr[scale].get("placebo_change") is not None:
            vals.append(pr[scale]["placebo_change"])
    return _median(vals)


def _ref(s: ClinicalStudy) -> str:
    return s.nct_id or s.pmid or s.source_ref


def compare_designs(asset_a: str, asset_b: str, studies_a: list[ClinicalStudy], studies_b: list[ClinicalStudy],
                    scale: str | None) -> dict[str, Any]:
    trials_a = [s for s in studies_a if s.source in ("ctgov", "uspi", "pubmed", "epmc", "pdf")]
    trials_b = [s for s in studies_b if s.source in ("ctgov", "uspi", "pubmed", "epmc", "pdf")]
    if not trials_a or not trials_b:
        return {"alert": False, "applicable": False, "items": [], "caveat": None,
                "note": "Structural / label-derived comparison — no trial-design confounding to assess."}
    items: list[dict[str, Any]] = []
    scale_label = SCALE_BY_KEY[scale].label if scale and scale in SCALE_BY_KEY else "primary scale"

    ba, bb = _baseline(trials_a, scale), _baseline(trials_b, scale)
    if ba is not None and bb is not None:
        rel = abs(ba - bb) / ((ba + bb) / 2)
        items.append({"dimension": "Baseline severity", "a": f"{ba} ({scale_label})", "b": f"{bb} ({scale_label})",
                      "metric": round(rel * 100, 1), "threshold": "> 10% relative difference", "triggered": rel > BASELINE_REL_THRESHOLD,
                      "caveat": f"Mean baseline {scale_label} differed ({ba} vs {bb}); effect sizes are sensitive to baseline severity."})

    arch_a = sorted({s.design_archetype for s in trials_a if s.design_archetype})
    arch_b = sorted({s.design_archetype for s in trials_b if s.design_archetype})
    norm = lambda xs: {("ACUTE_PARALLEL_PLACEBO" if x == "LABEL_PIVOTAL" else x) for x in xs if x not in ("LABEL_POOLED_SAFETY",)}  # noqa: E731
    if arch_a and arch_b:
        diverge = norm(arch_a) != norm(arch_b)
        items.append({"dimension": "Trial architecture", "a": ", ".join(arch_a), "b": ", ".join(arch_b), "metric": None,
                      "threshold": "different design archetypes", "triggered": diverge,
                      "caveat": "Designs differ (e.g. acute parallel-group vs randomized withdrawal); enriched or withdrawal designs are not comparable to acute placebo-controlled effects."})

    ri_a = any(s.placebo_run_in for s in trials_a)
    ri_b = any(s.placebo_run_in for s in trials_b)
    if ri_a != ri_b:
        items.append({"dimension": "Placebo run-in", "a": "yes" if ri_a else "no", "b": "yes" if ri_b else "no", "metric": None,
                      "threshold": "present in one programme only", "triggered": True,
                      "caveat": "Placebo run-in (or lead-in) was used in only one programme, which lowers placebo response and can inflate separation."})

    fx_a = any(s.flexible_dose for s in trials_a)
    fx_b = any(s.flexible_dose for s in trials_b)
    if fx_a != fx_b:
        items.append({"dimension": "Dosing scheme", "a": "flexible" if fx_a else "fixed", "b": "flexible" if fx_b else "fixed", "metric": None,
                      "threshold": "fixed vs flexible dosing", "triggered": True,
                      "caveat": "Fixed- vs flexible-dose designs differ in dose exposure and titration confounding."})

    da, db = _median([s.duration_weeks for s in trials_a]), _median([s.duration_weeks for s in trials_b])
    if da is not None and db is not None:
        trig = abs(da - db) > DURATION_GAP_WEEKS
        items.append({"dimension": "Primary endpoint timing", "a": f"{da} wk", "b": f"{db} wk", "metric": abs(da - db),
                      "threshold": f"> {DURATION_GAP_WEEKS} weeks", "triggered": trig,
                      "caveat": f"Primary endpoints were assessed at different timepoints ({da} vs {db} weeks)."})

    ua = [s.pct_us_sites for s in trials_a if s.pct_us_sites is not None]
    ub = [s.pct_us_sites for s in trials_b if s.pct_us_sites is not None]
    if ua and ub:
        ma, mb = statistics.mean(ua), statistics.mean(ub)
        trig = abs(ma - mb) > US_SHARE_THRESHOLD
        items.append({"dimension": "Geography (US share of sites)", "a": f"{ma:.0%}", "b": f"{mb:.0%}", "metric": round(abs(ma - mb) * 100, 1),
                      "threshold": "> 30 percentage points", "triggered": trig,
                      "caveat": "Site geography differed; region is a recognised moderator of placebo response in psychiatric trials."})

    ya, yb = _median([s.start_year for s in trials_a]), _median([s.start_year for s in trials_b])
    if ya is not None and yb is not None:
        trig = abs(ya - yb) >= ERA_GAP_YEARS
        items.append({"dimension": "Trial era", "a": str(int(ya)), "b": str(int(yb)), "metric": abs(ya - yb),
                      "threshold": f">= {ERA_GAP_YEARS} years apart", "triggered": trig,
                      "caveat": "Programmes were run in different eras; placebo response in psychiatric trials varies by era."})

    pa, pb = _placebo_change(trials_a, scale), _placebo_change(trials_b, scale)
    if pa is not None and pb is not None and (pa or pb):
        rel = abs(pa - pb) / max(abs(pa), abs(pb))
        items.append({"dimension": "Placebo response", "a": f"{pa} ({scale_label} change)", "b": f"{pb} ({scale_label} change)",
                      "metric": round(rel * 100, 1), "threshold": "> 25% relative difference", "triggered": rel > PLACEBO_REL_THRESHOLD,
                      "caveat": f"Placebo-arm change differed ({pa} vs {pb}); drug–placebo differences are not directly transferable across programmes."})

    triggered = [i for i in items if i["triggered"]]
    alert = bool(triggered)
    refs_a = ", ".join(sorted({_ref(s) for s in trials_a})[:6])
    refs_b = ", ".join(sorted({_ref(s) for s in trials_b})[:6])
    caveat = None
    if alert:
        caveat = (f"No head-to-head trial compares {asset_a} with {asset_b}; the comparison is indirect across separate placebo-controlled "
                  f"programmes ({asset_a}: {refs_a}; {asset_b}: {refs_b}). " + " ".join(i["caveat"] for i in triggered) +
                  " Differences should be presented as descriptive and not as evidence of comparative efficacy or safety.")
    else:
        caveat = (f"Indirect comparison across separate placebo-controlled programmes ({asset_a}: {refs_a}; {asset_b}: {refs_b}); "
                  "no major design divergence detected on the checked dimensions, but cross-trial comparisons remain non-randomized.")
    return {"alert": alert, "applicable": True, "items": items, "caveat": caveat, "n_trials_a": len(trials_a), "n_trials_b": len(trials_b)}
