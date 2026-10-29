"""MODULE 2 — Competitive Differentiation & Impact Matrix (2×2 engine).

X (Magnitude of Differentiation, −100…+100, + = favours Product A):
  effect drivers    X_raw = 100·tanh(Δd / 0.25)            Δd = pooled Cohen's d(A) − d(B) vs placebo
  risk drivers      X_raw = 100·tanh(ΔRD / 5 pp)           ΔRD = RD(B) − RD(A), excess incidence vs placebo
  structural        X_raw = 100·(S_A − S_B) / 3            S = label-derived ordinal score 0…3
  onset             X_raw = 100·tanh(Δweeks / 1.5)         Δweeks = earliest separation(B) − (A)
  X = X_raw × tier weight of the claim (1A 1.0 · 1B 0.85 · 2 0.60 · 3 0.30), where the claim tier
  is the weaker of the two sides' best available tiers (an indirect comparison is only as strong as
  its weakest arm).
Y (HCP Decision Impact, 0…100) = effective MVD impact score × 100.
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Asset, ClinicalStudy, Indication, MarketValueDriver
from ..taxonomy import MVD_BY_KEY, SCALE_BY_KEY, asset_terms, mvd_profile
from .confounders import compare_designs
from .evidence import endpoint_pairs, serialize_endpoint, summarize_effect, summarize_risk
from .mvd import effective_scores
from .regulatory_ingest import load_parsed_label
from .tiering import TIER_WEIGHTS, weaker_tier

X_THRESHOLD = 15.0
Y_THRESHOLD = 50.0
QUADRANTS = {
    "TR": "Core Scientific Platform Pillars",
    "TL": "Priority Evidence Generation Gaps",
    "BR": "Phenotype / Niche Tailoring",
    "BL": "Deprioritized Noise",
}

ACTION_LABEL = {"CONTRAINDICATED": "Contraindicated", "AVOID": "Avoid / not recommended", "DOSE_ADJUSTMENT": "Dose adjustment",
                "MONITOR": "Monitor", "NO_ADJUSTMENT": "No adjustment", "MENTION_ONLY": "Mentioned (no action)", "NOT_MENTIONED": "Not mentioned"}


def asset_label(a: Asset) -> str:
    return f"{a.brand_name} ({a.generic_name})" if a.brand_name else a.generic_name


# ---------------------------------------------------------------------------
# Structural (label-derived) metrics
# ---------------------------------------------------------------------------


def structural_metric(key: str, parsed: dict[str, Any], indication_key: str | None) -> dict[str, Any] | None:
    if not parsed:
        return None
    meta = parsed.get("_meta", {})
    src = {"source_id": meta.get("set_id"), "source_type": "uspi"}
    if key == "no_titration":
        t = parsed["dosing"]["titration"]
        if t["required"] is None:
            return None
        return {"metric": "structural", "estimate": parsed["dosing"]["scores"]["titration"], "tier": "1A",
                "display": "No titration" if t["required"] is False else "Titration required", "quotes": [{**src, "quote": t["verbatim"]}]}
    if key == "ddi_flexibility":
        cyp = parsed["cyp"]
        kc = cyp["key_classes"]
        quotes = [{**src, "quote": r["verbatim"]} for r in cyp["summary"] if r["enzyme"] in ("CYP3A4", "CYP2D6") and not r.get("inherited")][:4]
        disp = "; ".join(f"{k.replace('_', ' ')}: {ACTION_LABEL.get(v, v)}" for k, v in kc.items())
        return {"metric": "structural", "estimate": cyp["ddi_score"], "tier": "1A", "display": disp, "quotes": quotes, "detail": kc}
    if key == "dosing_convenience":
        d = parsed["dosing"]
        disp = f"{(d['frequency'] or 'frequency n/r').replace('_', ' ')}; food: {(d['food']['requirement'] or 'n/r').replace('_', ' ')}"
        quotes = [q for q in ({**src, "quote": d["frequency_verbatim"]}, {**src, "quote": d["food"]["verbatim"]}) if q["quote"]]
        return {"metric": "structural", "estimate": d["scores"]["dosing_convenience"], "tier": "1A", "display": disp, "quotes": quotes}
    if key == "label_safety":
        bw = parsed["boxed_warning"]
        ci = parsed["contraindications"]
        disp = ("Boxed warning: " + (", ".join(bw["topics"]) or "yes") if bw["present"] else "No boxed warning") + \
               f"; {ci['non_hypersensitivity_count']} non-hypersensitivity contraindication(s)"
        quotes = ([{**src, "quote": bw["verbatim"]}] if bw.get("verbatim") else []) + \
                 [{**src, "quote": i["verbatim"]} for i in ci["items"] if not i["hypersensitivity"]][:2]
        return {"metric": "structural", "estimate": parsed["label_safety_score"], "tier": "1A", "display": disp, "quotes": quotes}
    if key == "rapid_onset":
        ons = [o for o in parsed.get("clinical_studies", {}).get("onset", []) if not indication_key or o.get("indication_key") in (None, indication_key)]
        if not ons:
            return None
        best = min(ons, key=lambda o: o["weeks"])
        return {"metric": "onset_weeks", "estimate": best["weeks"], "tier": "1A", "display": f"separation as early as {best['weeks']:g} wk",
                "quotes": [{**src, "quote": best["verbatim"]}]}
    return None


# ---------------------------------------------------------------------------
# Per-asset metric for one driver
# ---------------------------------------------------------------------------


def asset_metric(session: Session, asset: Asset, key: str, indication: Indication, parsed: dict[str, Any], tier_filter: str) -> dict[str, Any] | None:
    mdef = MVD_BY_KEY[key]
    if mdef.kind in ("structural", "onset"):
        m = structural_metric(key, parsed, indication.key)
        if m and tier_filter == "tier1" and m["tier"] not in ("1A", "1B"):
            return None
        return m
    pairs = endpoint_pairs(session, asset.id, indication_key=indication.key, rows=mdef.rows)
    if mdef.kind == "effect":
        buckets = ("maintenance", "intermediate") if key == "relapse_prevention" else ("acute",)
        if key in ("psychosocial_functioning", "cognition", "quality_of_life"):
            buckets = ("acute", "intermediate", "maintenance")
        m = summarize_effect(pairs, tier_filter=tier_filter, buckets=buckets)
        if m:
            m["display"] = f"d = {m['estimate']:+.2f} (95% CI {m['lo']:+.2f} to {m['hi']:+.2f}; k={m['k_studies']})"
        return m
    # risk drivers: average excess incidence across the driver's rows (e.g. akathisia & EPS)
    per_row = []
    for row in mdef.rows:
        r = summarize_risk([(e, s) for e, s in pairs if e.endpoint_scale_name == row], tier_filter=tier_filter)
        if r:
            per_row.append((row, r))
    if per_row:
        tiers = [r["tier"] for _, r in per_row]
        worst = max(tiers, key=["1A", "1B", "2", "3"].index)
        est = sum(r["estimate"] for _, r in per_row) / len(per_row)
        return {"metric": "risk_difference", "estimate": round(est, 4), "pp": round(est * 100, 2), "tier": worst,
                "rows": {row: r for row, r in per_row}, "endpoint_ids": [i for _, r in per_row for i in r["endpoint_ids"]],
                "study_ids": sorted({i for _, r in per_row for i in r["study_ids"]}),
                "display": "; ".join(f"{SCALE_BY_KEY[row].label.split(' (')[0]}: {r['pp']:+.1f} pp vs placebo" for row, r in per_row)}
    # continuous safety (e.g. weight kg / prolactin) → harm in d units
    m = summarize_effect(pairs, tier_filter=tier_filter, buckets=("acute", "intermediate"))
    if m:
        m["metric"] = "safety_d"
        m["display"] = f"d = {m['estimate']:+.2f} vs placebo (positive = less harm)"
    return m


def differentiation(kind: str, a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    if not a or not b:
        return {"x_raw": None, "insufficient": True, "uncertain": True, "explanation": "Insufficient evidence on " + ("both sides" if not a and not b else ("Product A" if not a else "comparator"))}
    ma, mb = a["metric"], b["metric"]
    if ma == "structural" and mb == "structural":
        delta = a["estimate"] - b["estimate"]
        return {"x_raw": 100 * delta / 3, "delta": round(delta, 3), "delta_unit": "label score (0–3)", "uncertain": False, "insufficient": False,
                "explanation": f"Label score {a['estimate']:.2f} vs {b['estimate']:.2f} (Δ {delta:+.2f} on 0–3 scale)"}
    if ma == "onset_weeks" and mb == "onset_weeks":
        delta = b["estimate"] - a["estimate"]
        return {"x_raw": 100 * math.tanh(delta / 1.5), "delta": round(delta, 2), "delta_unit": "weeks earlier", "uncertain": False, "insufficient": False,
                "explanation": f"Earliest labelled separation {a['estimate']:g} vs {b['estimate']:g} weeks"}
    if ma == "risk_difference" and mb == "risk_difference":
        delta_pp = (b["estimate"] - a["estimate"]) * 100
        return {"x_raw": 100 * math.tanh(delta_pp / 5.0), "delta": round(delta_pp, 2), "delta_unit": "pp less excess incidence",
                "uncertain": abs(delta_pp) < 2.0, "insufficient": False,
                "explanation": f"Excess incidence vs placebo {a['pp']:+.1f} pp (A) vs {b['pp']:+.1f} pp (B) → Δ {delta_pp:+.1f} pp"}
    if ma in ("cohens_d", "safety_d") and mb in ("cohens_d", "safety_d"):
        delta = a["estimate"] - b["estimate"]
        se = math.sqrt(a.get("se", 0) ** 2 + b.get("se", 0) ** 2)
        return {"x_raw": 100 * math.tanh(delta / 0.25), "delta": round(delta, 3), "delta_unit": "Δ Cohen's d",
                "delta_ci": [round(delta - 1.96 * se, 3), round(delta + 1.96 * se, 3)] if se else None,
                "uncertain": bool(se) and abs(delta) < 1.96 * se, "insufficient": False,
                "explanation": f"d {a['estimate']:+.2f} vs {b['estimate']:+.2f} → Δd {delta:+.2f}" + (f" (indirect 95% CI {delta - 1.96 * se:+.2f} to {delta + 1.96 * se:+.2f})" if se else "")}
    return {"x_raw": None, "insufficient": True, "uncertain": True, "explanation": f"Metrics not comparable ({ma} vs {mb})"}


def quadrant(x: float | None, y: float) -> str:
    right = x is not None and x >= X_THRESHOLD
    top = y >= Y_THRESHOLD
    return ("T" if top else "B") + ("R" if right else "L")


def build_matrix(session: Session, indication: Indication, asset_a: Asset, comparators: list[Asset], *, persona: str = "evidence",
                 overrides: dict[str, float] | None = None, tier_filter: str = "all") -> dict[str, Any]:
    drivers = session.execute(select(MarketValueDriver).where(MarketValueDriver.indication_id == indication.id)).scalars().all()
    eff = effective_scores(drivers, persona, overrides)
    profile = [k for k in mvd_profile(indication.key)]
    parsed_a = load_parsed_label(session, asset_a)
    nodes = []
    for b in comparators:
        parsed_b = load_parsed_label(session, b)
        for key in profile:
            mdef = MVD_BY_KEY[key]
            y = 100 * eff.get(key, {"score": 0.10})["score"]
            ma = asset_metric(session, asset_a, key, indication, parsed_a, tier_filter)
            mb = asset_metric(session, b, key, indication, parsed_b, tier_filter)
            diff = differentiation(mdef.kind, ma, mb)
            claim_tier = weaker_tier(ma["tier"] if ma else None, mb["tier"] if mb else None)
            w = TIER_WEIGHTS.get(claim_tier or "", 0.0)
            x = round(max(-100.0, min(100.0, diff["x_raw"] * w)), 2) if diff["x_raw"] is not None else None
            q = quadrant(x, y)
            nodes.append({
                "id": f"{key}::{b.generic_name}", "mvd_key": key, "mvd_name": mdef.name, "category": mdef.category, "kind": mdef.kind,
                "comparator": b.generic_name, "comparator_label": asset_label(b), "x": x,
                "x_raw": round(diff["x_raw"], 2) if diff["x_raw"] is not None else None, "y": round(y, 1),
                "tier": claim_tier, "tier_weight": w, "quadrant": q, "quadrant_label": QUADRANTS[q],
                "competitor_advantage": x is not None and x <= -X_THRESHOLD, "uncertain": diff.get("uncertain", False),
                "insufficient": diff.get("insufficient", False), "explanation": diff["explanation"], "delta": diff.get("delta"),
                "delta_unit": diff.get("delta_unit"), "delta_ci": diff.get("delta_ci"),
                "a": _metric_public(ma), "b": _metric_public(mb), "score_source": eff.get(key, {}).get("source"),
            })
    return {
        "indication": {"id": indication.id, "name": indication.name, "key": indication.key},
        "asset_a": {"id": asset_a.id, "generic": asset_a.generic_name, "label": asset_label(asset_a)},
        "comparators": [{"id": c.id, "generic": c.generic_name, "label": asset_label(c)} for c in comparators],
        "persona": persona, "tier_filter": tier_filter, "thresholds": {"x": X_THRESHOLD, "y": Y_THRESHOLD}, "quadrants": QUADRANTS,
        "nodes": nodes,
        "method": __doc__,
    }


def _metric_public(m: dict[str, Any] | None) -> dict[str, Any] | None:
    if not m:
        return None
    return {k: v for k, v in m.items() if k not in ("rows",)} | (
        {"rows": {r: {kk: vv for kk, vv in d.items() if kk != "endpoint_ids"} for r, d in m.get("rows", {}).items()}} if m.get("rows") else {})


# ---------------------------------------------------------------------------
# Node drill-down: USPI diff, confounder alert, objection handler
# ---------------------------------------------------------------------------


def regulatory_diff(a: Asset, pa: dict[str, Any], b: Asset, pb: dict[str, Any]) -> list[dict[str, Any]]:
    def row(name, va, vb, qa=None, qb=None, better=None):
        return {"attribute": name, "a": va, "b": vb, "a_quote": qa, "b_quote": qb, "advantage": better}

    rows = []
    if not pa or not pb:
        return rows
    bwa, bwb = pa["boxed_warning"], pb["boxed_warning"]
    rows.append(row("Boxed warning", ", ".join(bwa["topics"]) or ("Yes" if bwa["present"] else "None"),
                    ", ".join(bwb["topics"]) or ("Yes" if bwb["present"] else "None"), bwa.get("verbatim"), bwb.get("verbatim"),
                    "A" if (not bwa["present"] and bwb["present"]) else "B" if (bwa["present"] and not bwb["present"]) else None))
    ia = "; ".join(f"{i['indication_key']}{' (adjunctive)' if i['adjunctive'] else ''}" for i in pa["indications"])
    ib = "; ".join(f"{i['indication_key']}{' (adjunctive)' if i['adjunctive'] else ''}" for i in pb["indications"])
    rows.append(row("Approved indications", ia or "n/r", ib or "n/r"))
    rows.append(row("Initial U.S. approval", a.initial_us_approval_year, b.initial_us_approval_year))
    ta, tb = pa["dosing"]["titration"], pb["dosing"]["titration"]
    fmt_t = lambda t: "Not required" if t["required"] is False else ("Required" if t["required"] else "n/r")  # noqa: E731
    rows.append(row("Titration", fmt_t(ta), fmt_t(tb), ta["verbatim"], tb["verbatim"],
                    "A" if (ta["required"] is False and tb["required"]) else "B" if (tb["required"] is False and ta["required"]) else None))
    rows.append(row("Starting / recommended dose", f"{pa['dosing']['starting_dose'] or '—'} / {pa['dosing']['recommended_dose'] or '—'}",
                    f"{pb['dosing']['starting_dose'] or '—'} / {pb['dosing']['recommended_dose'] or '—'}"))
    fa, fb = pa["dosing"]["frequency"], pb["dosing"]["frequency"]
    rows.append(row("Dosing frequency", (fa or "n/r").replace("_", " "), (fb or "n/r").replace("_", " "),
                    pa["dosing"]["frequency_verbatim"], pb["dosing"]["frequency_verbatim"]))
    fda, fdb = pa["dosing"]["food"], pb["dosing"]["food"]
    rows.append(row("Food requirement", (fda["requirement"] or "n/r").replace("_", " "), (fdb["requirement"] or "n/r").replace("_", " "),
                    fda["verbatim"], fdb["verbatim"],
                    "A" if (fda["requirement"] == "none" and fdb["requirement"] not in (None, "none")) else
                    "B" if (fdb["requirement"] == "none" and fda["requirement"] not in (None, "none")) else None))
    rank = {"CONTRAINDICATED": 0, "AVOID": 1, "DOSE_ADJUSTMENT": 2, "MONITOR": 3, "NO_ADJUSTMENT": 4, "MENTION_ONLY": 4, "NOT_MENTIONED": 5}
    for cls, label in (("strong_cyp3a4_inhibitor", "Strong CYP3A4 inhibitors"), ("moderate_cyp3a4_inhibitor", "Moderate CYP3A4 inhibitors"),
                       ("cyp3a4_inducer", "CYP3A4 inducers"), ("strong_cyp2d6_inhibitor", "Strong CYP2D6 inhibitors")):
        va, vb = pa["cyp"]["key_classes"][cls], pb["cyp"]["key_classes"][cls]

        def rule_for(p, c, v):
            enz = "CYP2D6" if "2d6" in c else "CYP3A4"
            role = "inducer" if "inducer" in c else "inhibitor"
            st = "strong" if c.startswith("strong") else "moderate" if c.startswith("moderate") else None
            cands = [r for r in p["cyp"]["summary"]
                     if r["enzyme"] == enz and r["modulator"] == role and (st is None or r["strength"] == st)]
            return next((r for r in cands if r["action"] == v), cands[0] if cands else None)

        def quote(r):
            if not r:
                return None
            q = r["verbatim"] + (f" [dose: {r['dose_instruction']}]" if r.get("dose_instruction") else "")
            for n in r.get("population_rules") or []:
                if n["verbatim"] != r["verbatim"]:
                    q += f" | {n['population'].title()}: {n['verbatim']}"
            return q

        def value(v, r):
            out = ACTION_LABEL.get(v, v)
            if r and r.get("population", "general") != "general":
                return f"{out} ({r['population']} only)"
            notes = [n for n in (r or {}).get("population_rules") or [] if n["action"] != v]
            if notes:
                out += "; " + "; ".join(f"{n['population']}: {ACTION_LABEL.get(n['action'], n['action']).lower()}" for n in notes)
            return out

        ra, rb = rule_for(pa, cls, va), rule_for(pb, cls, vb)
        rows.append(row(label, value(va, ra), value(vb, rb), quote(ra), quote(rb),
                        "A" if rank[va] > rank[vb] else "B" if rank[vb] > rank[va] else None))
    for organ in ("hepatic", "renal"):
        ha, hb = pa["dosing"][organ], pb["dosing"][organ]
        fmt = lambda h: f"{ACTION_LABEL.get(h['action'], h['action'])}{(' (' + '/'.join(h['severity']) + ')') if h['severity'] else ''}"  # noqa: E731
        rows.append(row(f"{organ.title()} impairment", fmt(ha), fmt(hb), ha["verbatim"], hb["verbatim"],
                        "A" if rank.get(ha["action"], 5) > rank.get(hb["action"], 5) else "B" if rank.get(hb["action"], 5) > rank.get(ha["action"], 5) else None))
    ca, cb = pa["contraindications"], pb["contraindications"]
    rows.append(row("Contraindications (excl. hypersensitivity)", ca["non_hypersensitivity_count"], cb["non_hypersensitivity_count"],
                    "; ".join(i["verbatim"] for i in ca["items"] if not i["hypersensitivity"])[:600] or None,
                    "; ".join(i["verbatim"] for i in cb["items"] if not i["hypersensitivity"])[:600] or None,
                    "A" if ca["non_hypersensitivity_count"] < cb["non_hypersensitivity_count"] else "B" if cb["non_hypersensitivity_count"] < ca["non_hypersensitivity_count"] else None))
    rows.append(row("REMS", "Yes" if pa["monitoring"]["rems"] else "No", "Yes" if pb["monitoring"]["rems"] else "No"))
    return rows


def with_linked_registry(session: Session, asset: Asset, studies: list[ClinicalStudy | None]) -> list[ClinicalStudy]:
    """Label (§14) studies carry no design metadata; add the registry trials they were matched to."""
    out = [s for s in studies if s]
    seen = {s.id for s in out}
    for s in list(out):
        for c in s.candidate_nct_ids_json or []:
            reg = session.execute(select(ClinicalStudy).where(ClinicalStudy.asset_id == asset.id, ClinicalStudy.source == "ctgov",
                                                              ClinicalStudy.nct_id == c["nct_id"])).scalars().first()
            if reg and reg.id not in seen:
                out.append(reg)
                seen.add(reg.id)
    return out


def node_detail(session: Session, indication: Indication, asset_a: Asset, asset_b: Asset, mvd_key: str, *, persona: str = "evidence",
                overrides: dict[str, float] | None = None, tier_filter: str = "all") -> dict[str, Any]:
    from .objections import objection_card

    mdef = MVD_BY_KEY[mvd_key]
    pa, pb = load_parsed_label(session, asset_a), load_parsed_label(session, asset_b)
    ma = asset_metric(session, asset_a, mvd_key, indication, pa, tier_filter)
    mb = asset_metric(session, asset_b, mvd_key, indication, pb, tier_filter)
    diff = differentiation(mdef.kind, ma, mb)
    drivers = session.execute(select(MarketValueDriver).where(MarketValueDriver.indication_id == indication.id)).scalars().all()
    eff = effective_scores(drivers, persona, overrides)
    y = 100 * eff.get(mvd_key, {"score": 0.1})["score"]
    claim_tier = weaker_tier(ma["tier"] if ma else None, mb["tier"] if mb else None)
    x = round(diff["x_raw"] * TIER_WEIGHTS.get(claim_tier or "", 0), 2) if diff["x_raw"] is not None else None
    q = quadrant(x, y)

    def evidence_list(asset: Asset, m: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not m or "endpoint_ids" not in m:
            return []
        ids = set(m["endpoint_ids"])
        return [serialize_endpoint(e, s) for e, s in endpoint_pairs(session, asset.id, indication_key=indication.key, rows=mdef.rows) if e.id in ids]

    ev_a, ev_b = evidence_list(asset_a, ma), evidence_list(asset_b, mb)
    studies_a = with_linked_registry(session, asset_a, [session.get(ClinicalStudy, i) for i in (ma or {}).get("study_ids", [])])
    studies_b = with_linked_registry(session, asset_b, [session.get(ClinicalStudy, i) for i in (mb or {}).get("study_ids", [])])
    scale = mdef.rows[0] if mdef.rows else None
    conf = compare_designs(asset_label(asset_a), asset_label(asset_b), [s for s in studies_a if s], [s for s in studies_b if s], scale)
    card = objection_card(session, indication, asset_a, asset_b, mdef, ma, mb, diff, conf, [s for s in studies_a if s], ev_a, claim_tier) if q == "TR" else None
    return {
        "mvd": {"key": mvd_key, "name": mdef.name, "category": mdef.category, "kind": mdef.kind},
        "asset_a": asset_label(asset_a), "asset_b": asset_label(asset_b), "x": x, "y": round(y, 1), "quadrant": q, "quadrant_label": QUADRANTS[q],
        "claim_tier": claim_tier, "differentiation": diff, "a": _metric_public(ma), "b": _metric_public(mb),
        "evidence_a": ev_a, "evidence_b": ev_b, "regulatory_diff": regulatory_diff(asset_a, pa, asset_b, pb),
        "confounder_alert": conf, "objection_card": card,
        "terms_a": asset_terms(asset_a.generic_name, asset_a.brand_name, asset_a.synonyms_json),
    }
