"""ADJACENCY B — Scientific platform & lexicon stress-tester (MSL objection handler).

For a Top-Right (Core Scientific Platform Pillar) node, run a deterministic adversarial audit of
Product A's supporting evidence and produce a Medical Communications Objection Handler Card:
pillar statement (fair-balance, citation-anchored) → anticipated challenges → reactive responses.
Every number in the card is taken from the stored evidence rows and cited by NCT / PMID / SPL Set ID.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from ..models import Asset, ClinicalStudy, Indication
from ..taxonomy import MvdDef, mvd_profile, MVD_BY_KEY
from .tiering import TIER_RULES


def _refs(ev: list[dict[str, Any]], n: int = 4) -> str:
    refs = []
    for e in ev:
        r = e["study"]["nct_id"] or e["study"]["pmid"] or e["source_id"]
        if r and r not in refs:
            refs.append(r)
    return ", ".join(refs[:n]) + (" et al." if len(refs) > n else "")


def _quote_refs(m: dict[str, Any] | None) -> str:
    if not m:
        return ""
    return ", ".join(sorted({q["source_id"] for q in m.get("quotes", []) if q.get("source_id")})) or "USPI"


def objection_card(session: Session, indication: Indication, a: Asset, b: Asset, mdef: MvdDef, ma: dict[str, Any] | None,
                   mb: dict[str, Any] | None, diff: dict[str, Any], conf: dict[str, Any], studies_a: list[ClinicalStudy],
                   ev_a: list[dict[str, Any]], claim_tier: str | None) -> dict[str, Any]:
    an = a.brand_name or a.generic_name
    bn = b.brand_name or b.generic_name
    challenges: list[dict[str, Any]] = []

    # ---- pillar statement --------------------------------------------------------
    if mdef.kind in ("structural", "onset"):
        pillar = (f"Per the current USPI ({_quote_refs(ma)}), {an}: {ma['display'] if ma else 'n/r'}; {bn} USPI ({_quote_refs(mb)}): "
                  f"{mb['display'] if mb else 'n/r'}. Labeling differences are presented in the context of each product's full "
                  f"Prescribing Information, including Boxed Warning and Warnings and Precautions.")
    else:
        pillar = (f"In placebo-controlled studies in {indication.name} ({_refs(ev_a)}), {an} showed {ma['display'] if ma else 'n/r'} "
                  f"on {mdef.name.lower()}; the corresponding estimate for {bn} from its own placebo-controlled programme was "
                  f"{mb['display'] if mb else 'n/r'}. No head-to-head trial has compared the two agents; differences are descriptive.")

    # ---- adversarial audit ---------------------------------------------------------
    h2h = any(s.is_head_to_head for s in studies_a)
    if mdef.kind not in ("structural", "onset") and not h2h:
        resp = conf.get("caveat") or "Comparison is indirect."
        challenges.append({
            "severity": "high" if conf.get("alert") else "medium", "type": "Cross-trial comparison",
            "challenge": f"There is no head-to-head trial versus {bn}; cross-trial comparisons are confounded by design, population and era.",
            "response": resp + (" Where an alert is shown, lead with each agent's placebo-adjusted effect within its own programme rather than a numerical contrast."
                                if conf.get("alert") else ""),
        })
    nominal = [e for e in ev_a if not e.get("is_primary") and e.get("is_multiplicity_controlled") is not True and e["effect_size_type"] != "INCIDENCE_PCT"]
    controlled = [e for e in ev_a if e.get("is_primary") or e.get("is_multiplicity_controlled") is True]
    if nominal:
        challenges.append({
            "severity": "high" if not controlled else "medium", "type": "Multiplicity",
            "challenge": "Part of the supporting evidence comes from secondary endpoints without type-I-error control; nominal p-values are hypothesis-generating.",
            "response": (f"Anchor on the primary / multiplicity-controlled results ({_refs(controlled)}); present nominal secondary findings "
                         f"({_refs(nominal)}) as supportive only." if controlled else
                         f"Acknowledge the endpoints are not multiplicity-controlled ({_refs(nominal)}); position as hypothesis-generating and "
                         f"flag as an evidence-generation priority."),
        })
    drop = [(s, s.dropout_json) for s in studies_a if s.dropout_json and s.dropout_json.get("overall") is not None]
    high_drop = [(s, d) for s, d in drop if d["overall"] > 0.30 or (len(d.get("by_arm", {})) > 1 and
                 max(d["by_arm"].values()) - min(d["by_arm"].values()) > 0.10)]
    if high_drop:
        mmrm = any(re.search(r"mixed\s+model|mmrm|multiple\s+imputation|pattern[-\s]mixture|tipping", e["verbatim_quote"], re.I) for e in ev_a)
        details = "; ".join(f"{s.nct_id or s.source_ref}: overall {d['overall']:.0%}" for s, d in high_drop[:4])
        challenges.append({
            "severity": "medium", "type": "Attrition bias",
            "challenge": f"Discontinuation was high or imbalanced across arms ({details}), which can bias completer-based estimates.",
            "response": ("Primary analyses used likelihood-based mixed models (MMRM), which are valid under missing-at-random; cite the "
                         "registry statistical method and any published sensitivity analyses." if mmrm else
                         "Registry records do not state the missing-data approach; request / cite sensitivity analyses (e.g. MMRM, multiple "
                         "imputation, tipping-point) before using this pillar externally."),
        })
    k = (ma or {}).get("k_studies")
    if mdef.kind == "effect" and k == 1:
        challenges.append({
            "severity": "medium", "type": "Replication",
            "challenge": "The effect rests on a single study and has not been replicated.",
            "response": f"State that the finding comes from one study ({_refs(ev_a)}); cite consistency across doses / subgroups if reported and prioritise replication.",
        })
    if mdef.kind == "effect" and ma and ma.get("estimate") is not None and ma["estimate"] < 0.3:
        challenges.append({
            "severity": "low", "type": "Clinical relevance",
            "challenge": f"Standardized effect vs placebo is small (d = {ma['estimate']:+.2f}).",
            "response": "Translate to absolute terms (LS-mean difference on the native scale, responder NNT where available) and contextualise "
                        "against the range typical for the class in the same indication.",
        })
    if any(s.flexible_dose for s in studies_a):
        challenges.append({
            "severity": "low", "type": "Dose / titration confounding",
            "challenge": "Flexible-dose designs confound dose with response and tolerability.",
            "response": "Present fixed-dose arm results where available and describe titration schedules from the USPI Dosage and Administration section.",
        })
    if claim_tier in ("2", "3"):
        challenges.append({
            "severity": "high", "type": "Evidence tier",
            "challenge": f"The weakest arm of this comparison is Tier {claim_tier} evidence ({TIER_RULES[claim_tier]}).",
            "response": "Do not use as a proactive pillar until Tier 1 evidence exists; reactive use only with explicit limitations.",
        })

    # ---- fair balance: where does the comparator lead? ----------------------------------
    fair: list[str] = []
    from .matrix import asset_metric, differentiation
    from .regulatory_ingest import load_parsed_label

    pa, pb = load_parsed_label(session, a), load_parsed_label(session, b)
    for key in mvd_profile(indication.key):
        if key == mdef.key:
            continue
        d2 = MVD_BY_KEY[key]
        if d2.category != "SAFETY_TOLERABILITY":
            continue
        m1, m2 = asset_metric(session, a, key, indication, pa, "all"), asset_metric(session, b, key, indication, pb, "all")
        df = differentiation(d2.kind, m1, m2)
        if df.get("x_raw") is not None and df["x_raw"] <= -30:
            fair.append(f"{d2.name}: {df['explanation']}")
    sev_order = {"high": 0, "medium": 1, "low": 2}
    challenges.sort(key=lambda c: sev_order[c["severity"]])
    return {
        "pillar_statement": pillar,
        "fair_balance": fair,
        "challenges": challenges,
        "claim_tier": claim_tier,
        "status": "Proactive-ready" if claim_tier in ("1A", "1B") and not any(c["severity"] == "high" for c in challenges) else "Reactive use only",
        "disclaimer": "Draft for internal medical review; not approved promotional or medical-information content.",
    }
