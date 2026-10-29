"""Deterministic evidence tiering (Module 2) and validity levels (Module 3).

Both are pure functions of registry / label / publication metadata so every
badge in the UI can be explained by the rule that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass

TIER_WEIGHTS: dict[str, float] = {"1A": 1.0, "1B": 0.85, "2": 0.60, "3": 0.30}
TIER_ORDER = ["1A", "1B", "2", "3"]

TIER_RULES = {
    "1A": "FDA USPI label claim, registry result matched to a USPI §14 row, or head-to-head Phase 3/4 superiority",
    "1B": "Industry-sponsored registrational Phase 3/4 RCT — primary or multiplicity-controlled key secondary endpoint",
    "2": "Industry Phase 2 RCT, Phase 3 nominal (non-controlled) secondary, post-hoc / pooled analysis, or (network) meta-analysis",
    "3": "Academic / investigator-initiated study, observational cohort, case series, or exploratory endpoint",
}

VALIDITY_RULES = {
    5: "USPI §14 label claim, or Phase 3 primary endpoint positive (p<0.05) in ≥2 trials",
    4: "Single Phase 3 primary endpoint, Phase 3 multiplicity-controlled key secondary, or large (N≥300) Phase 4 RCT",
    3: "Phase 3 nominal secondary / post-hoc, meta-analysis, or RWE cohort with N>500",
    2: "Phase 2 RCT, or academic trial with N 50–500",
    1: "Small IIS (N<50), case series, exploratory biomarker, or unclassifiable",
}


@dataclass
class EvidenceContext:
    source: str  # uspi | ctgov | pubmed | epmc | pdf
    sponsor_class: str | None = None  # INDUSTRY | ACADEMIC_IIS
    phase_max: int | None = None
    randomized: bool | None = None
    study_type: str | None = None  # INTERVENTIONAL | OBSERVATIONAL
    sample_size: int | None = None
    is_primary: bool | None = None
    is_multiplicity_controlled: bool | None = None
    is_post_hoc: bool | None = None
    significant: bool | None = None
    favorable: bool | None = None
    is_head_to_head: bool = False
    is_meta_analysis: bool = False
    is_case_series: bool = False
    is_biomarker: bool = False
    label_confirmed: bool = False
    replicated: bool = False


def evidence_tier(c: EvidenceContext) -> tuple[str, str]:
    if c.source == "uspi" or c.label_confirmed:
        return "1A", "USPI label claim" if c.source == "uspi" else "Registry result matched to USPI §14"
    if c.is_head_to_head and (c.phase_max or 0) >= 3 and c.significant and c.favorable:
        return "1A", "Head-to-head Phase 3/4 superiority"
    industry = c.sponsor_class == "INDUSTRY"
    rct = bool(c.randomized) and (c.study_type in (None, "INTERVENTIONAL"))
    if c.is_meta_analysis:
        return "2", "Meta-analysis / NMA"
    if c.is_post_hoc:
        return ("2", "Post-hoc / pooled analysis (industry)") if industry else ("3", "Post-hoc analysis (non-industry)")
    if industry and rct and (c.phase_max or 0) >= 3:
        if c.is_primary or c.is_multiplicity_controlled is True:
            return "1B", "Industry Phase 3/4 RCT, primary or multiplicity-controlled endpoint"
        return "2", "Industry Phase 3/4 RCT, nominal secondary endpoint"
    if industry and rct and (c.phase_max or 0) == 2:
        return "2", "Industry Phase 2 RCT"
    if not industry and rct and (c.phase_max or 0) >= 3 and c.is_primary and (c.sample_size or 0) >= 200:
        # large academic RCTs (e.g. NIMH-funded) are still IIS per spec, but flagged in the reason
        return "3", "Academic / IIS RCT (large; consider manual upgrade)"
    return "3", "Academic / IIS, observational, or exploratory"


def validity_level(c: EvidenceContext) -> tuple[int, str]:
    phase = c.phase_max or 0
    n = c.sample_size or 0
    if c.source == "uspi" or c.label_confirmed:
        return 5, "USPI §14 label claim"
    if c.is_primary and phase >= 3 and c.significant and c.replicated:
        return 5, "Replicated Phase 3 primary endpoint (p<0.05)"
    if c.is_primary and phase >= 3 and (c.randomized or c.study_type == "INTERVENTIONAL"):
        return 4, "Phase 3 primary endpoint (single trial)"
    if phase >= 3 and c.is_multiplicity_controlled is True:
        return 4, "Phase 3 multiplicity-controlled key secondary"
    if phase == 4 and c.randomized and n >= 300:
        return 4, "Large Phase 4 RCT"
    if c.is_meta_analysis:
        return 3, "Meta-analysis / NMA"
    if phase >= 3 and (c.significant or c.is_post_hoc or not c.is_primary):
        return 3, "Phase 3 nominal secondary / post-hoc"
    if c.study_type == "OBSERVATIONAL" and n > 500:
        return 3, "Large RWE cohort (N>500)"
    if phase == 2 and c.randomized:
        return 2, "Phase 2 RCT"
    if 50 <= n <= 500 and not c.is_case_series:
        return 2, "Moderate trial / cohort (N 50–500)"
    if c.is_case_series or c.is_biomarker or n < 50:
        return 1, "Small IIS / case series / exploratory"
    return 1, "Unclassified"


def best_tier(tiers: list[str | None]) -> str | None:
    present = [t for t in tiers if t in TIER_WEIGHTS]
    return min(present, key=TIER_ORDER.index) if present else None


def weaker_tier(a: str | None, b: str | None) -> str | None:
    if a is None or b is None:
        return None
    return max(a, b, key=TIER_ORDER.index)
