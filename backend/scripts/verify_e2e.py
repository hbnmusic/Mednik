#!/usr/bin/env python
"""Step 6 — mandatory end-to-end verification against the LIVE public APIs.

Default run: Schizophrenia, lumateperone (Caplyta) vs cariprazine (Vraylar) [+ xanomeline and trospium (Cobenfy)].

    cd backend && .venv/bin/python scripts/verify_e2e.py                       # schizophrenia, 3 assets
    .venv/bin/python scripts/verify_e2e.py --indication "Bipolar I Depression" --no-cobenfy

Checks
  1. USPI §7 (LOINC 34073-7) CYP3A4 parsing: dose-adjustment vs avoid vs contraindication, with verbatim text
  2. ClinicalTrials.gov sponsor split: INDUSTRY vs OTHER/NIH/NETWORK/FED, re-verified against raw leadSponsor.class
  3. 2×2 matrix populated; node evidence cites real NCT IDs
  4. Evidence heatmap populated with L1–L5 badges, real NCT IDs, and white space
  5. Cross-trial confounder alert computed for an efficacy comparison
  6. Study concept generator returns precedent NCT IDs from a live registry query
  7. Literature layer stored PubMed records (PMIDs) and links them to evidence / drivers / guidelines

Writes verification_report.json and verification_report.md next to this script's parent (gitignored).
Exit code 0 = all hard checks passed (WARN lines are informational).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from mednik.api.main import app  # noqa: E402

NCT = re.compile(r"NCT\d{8}")
PMID = re.compile(r"^\d{6,9}$")

# Expected USPI positions as understood when mednik was written (soft checks → WARN on mismatch, verify against the quotes).
EXPECTED_CYP = {
    "lumateperone": {"strong_cyp3a4_inhibitor": "DOSE_ADJUSTMENT", "moderate_cyp3a4_inhibitor": "DOSE_ADJUSTMENT", "cyp3a4_inducer": "AVOID"},
    "cariprazine": {"strong_cyp3a4_inhibitor": "DOSE_ADJUSTMENT", "cyp3a4_inducer": "AVOID"},
}


class Report:
    def __init__(self):
        self.rows: list[dict] = []

    def check(self, name: str, ok: bool, detail: str = "", hard: bool = True):
        status = "PASS" if ok else ("FAIL" if hard else "WARN")
        self.rows.append({"check": name, "status": status, "detail": detail})
        print(f"[{status}] {name}" + (f" — {detail}" if detail else ""), flush=True)

    @property
    def failed(self) -> bool:
        return any(r["status"] == "FAIL" for r in self.rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--indication", default="Schizophrenia")
    ap.add_argument("--no-cobenfy", action="store_true")
    ap.add_argument("--force-refresh", action="store_true")
    ap.add_argument("--skip-literature", action="store_true", help="faster: skip PubMed/EPMC asset literature extraction")
    args = ap.parse_args()

    assets = [{"generic": "lumateperone", "brand": "Caplyta"}, {"generic": "cariprazine", "brand": "Vraylar"}]
    if not args.no_cobenfy:
        assets.append({"generic": "xanomeline and trospium", "brand": "Cobenfy"})
    rep = Report()
    out: dict = {"indication": args.indication, "assets": assets}
    t0 = time.time()
    with TestClient(app) as c:
        h = c.get("/api/v1/health").json()
        print(f"mednik {h['version']} · LLM: {h['llm_provider']} (enabled={h['llm_enabled']}, model={h['llm_model']})")
        print(f"Ingesting live data for {args.indication}: {', '.join(a['brand'] for a in assets)} … (first run takes several minutes)")
        r = c.post("/api/v1/ingest", json={"indication": args.indication, "assets": assets, "force_refresh": args.force_refresh,
                                           "include_literature": not args.skip_literature})
        jid = r.json()["job_id"]
        seen = 0
        while True:
            job = c.get(f"/api/v1/jobs/{jid}").json()
            for line in job["log"][seen:]:
                print("   ·", line["msg"], flush=True)
            seen = len(job["log"])
            if job["status"] in ("done", "error"):
                break
            time.sleep(2)
        out["ingest_summary"] = job["summary"]
        rep.check("Ingest job completed", job["status"] == "done", job.get("error") or f"{time.time() - t0:.0f}s")
        if job["status"] != "done":
            return finish(rep, out)

        # 1. USPI §7 CYP3A4
        for a in assets:
            g = a["generic"]
            reg = c.get(f"/api/v1/regulatory/{g}").json()
            loincs = {s["loinc"] for s in reg["sections"]}
            rep.check(f"{a['brand']}: USPI sections parsed by LOINC", {"34073-7", "34068-7", "34084-4", "34092-7"} <= loincs,
                      f"set_id {reg['spl_set_id']} eff {reg['effective_date']}; LOINC {sorted(loincs)}")
            cyp = (reg.get("parsed") or {}).get("cyp") or {}
            kc = cyp.get("key_classes", {})
            rules = [r for r in cyp.get("summary", []) if r["enzyme"] == "CYP3A4" and not r.get("inherited")]
            rep.check(f"{a['brand']}: §7 CYP3A4 rules extracted with verbatim text", bool(rules) and all(r["verbatim"] for r in rules),
                      "; ".join(f"{r['label']} → {r['action']}" + (f" [{r['dose_instruction']}]" if r.get("dose_instruction") else "")
                                + "".join(f" ({n['population']}: {n['action']})" for n in r.get("population_rules") or []) for r in rules))
            out.setdefault("cyp", {})[g] = {"key_classes": kc, "rules": [{"label": r["label"], "action": r["action"], "dose": r.get("dose_instruction"),
                                                                          "verbatim": r["verbatim"], "population_rules": r.get("population_rules") or []} for r in rules]}
            for cls, exp in EXPECTED_CYP.get(g, {}).items():
                rep.check(f"{a['brand']}: {cls} = {exp}", kc.get(cls) == exp, f"parsed {kc.get(cls)}", hard=False)
        actions = {r["action"] for g in out["cyp"].values() for r in g["rules"]}
        rep.check("Dose adjustment distinguished from avoid / contraindication", "DOSE_ADJUSTMENT" in actions and bool(actions & {"AVOID", "CONTRAINDICATED"}),
                  f"actions seen: {sorted(actions)}")

        # 2. CT.gov sponsor split
        from sqlalchemy import select

        from mednik.clients.base import BaseApiClient  # noqa: F401
        from mednik.db import aux_scope
        from mednik.models import ApiCache

        raw_class: dict[str, str] = {}
        with aux_scope() as s:
            for row in s.execute(select(ApiCache).where(ApiCache.source == "ctgov")).scalars().all():
                try:
                    for st in json.loads(row.body).get("studies", []):
                        ps = st.get("protocolSection", {})
                        raw_class[ps.get("identificationModule", {}).get("nctId")] = ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("class")
                except Exception:  # noqa: BLE001
                    continue
        for a in assets:
            t = c.get("/api/v1/trials", params={"asset": a["generic"]}).json()
            ind_t = c.get("/api/v1/trials", params={"asset": a["generic"], "indication": args.indication}).json()
            mism = [x["nct_id"] for x in t["trials"] if (raw_class.get(x["nct_id"]) == "INDUSTRY") != (x["sponsor_class"] == "INDUSTRY")]
            ind3 = [x for x in ind_t["trials"] if x["sponsor_class"] == "INDUSTRY" and "PHASE3" in (x["phase"] or "")]
            aca = [x for x in t["trials"] if x["sponsor_class"] == "ACADEMIC_IIS"]
            rep.check(f"{a['brand']}: sponsor class matches raw leadSponsor.class for all {t['n']} trials", not mism and t["n"] > 0,
                      f"mismatches: {mism[:5]}" if mism else f"{len(ind3)} INDUSTRY Phase 3 in {args.indication}; {len(aca)} academic/IIS overall")
            rep.check(f"{a['brand']}: ≥1 INDUSTRY Phase 3 trial in {args.indication}", len(ind3) > 0, ", ".join(x["nct_id"] for x in ind3[:6]), hard=False)
            out.setdefault("trials", {})[a["generic"]] = {"n": t["n"], "split": t["split"], "industry_phase3": [x["nct_id"] for x in ind3],
                                                          "academic_iis": [(x["nct_id"], x["sponsor"], x.get("sponsor_class_raw")) for x in aca][:25]}

        # 3. matrix
        a_name, comps = assets[0]["generic"], ",".join(a["generic"] for a in assets[1:])
        mx = c.get("/api/v1/matrix", params={"indication": args.indication, "asset_a": a_name, "comparators": comps}).json()
        placed = [n for n in mx["nodes"] if n["x"] is not None]
        rep.check("2×2 matrix populated", len(placed) >= 3, f"{len(placed)}/{len(mx['nodes'])} nodes with a differentiation score; "
                  + ", ".join(f"{n['mvd_key']} vs {n['comparator']}: {n['quadrant']}" for n in placed[:6]))
        eff_nodes = [n for n in placed if n["kind"] in ("effect", "risk")]
        nct_cited = False
        conf_ok = False
        for n in eff_nodes[:6]:
            det = c.get("/api/v1/matrix/node", params={"indication": args.indication, "asset_a": a_name, "asset_b": n["comparator"], "mvd_key": n["mvd_key"]}).json()
            refs = {e["study"]["nct_id"] for e in det["evidence_a"] + det["evidence_b"] if e["study"]["nct_id"]} | \
                   {e["source_id"] for e in det["evidence_a"] + det["evidence_b"] if NCT.match(e["source_id"] or "")}
            if refs:
                nct_cited = True
            if det["confounder_alert"].get("applicable"):
                conf_ok = True
                out["confounder_example"] = {"node": n["id"], "alert": det["confounder_alert"]["alert"],
                                             "items": [(i["dimension"], i["a"], i["b"], i["triggered"]) for i in det["confounder_alert"]["items"]],
                                             "caveat": det["confounder_alert"]["caveat"], "refs": sorted(refs)[:10]}
                rep.check("Cross-trial confounder alert computed", True,
                          f"{n['mvd_key']} vs {n['comparator']}: alert={det['confounder_alert']['alert']}; "
                          + "; ".join(f"{i['dimension']} {i['a']} vs {i['b']}{' ⚠' if i['triggered'] else ''}" for i in det["confounder_alert"]["items"]))
                if det.get("objection_card"):
                    out["objection_card_example"] = det["objection_card"]
                break
        rep.check("Matrix evidence cites real NCT IDs", nct_cited)
        if not conf_ok:
            rep.check("Cross-trial confounder alert computed", False, "no efficacy/safety node had trial-level evidence on both sides")
        out["matrix"] = [{k: n[k] for k in ("mvd_key", "comparator", "x", "y", "tier", "quadrant", "explanation")} for n in mx["nodes"]]

        # 4. heatmap
        hm = c.get("/api/v1/heatmap", params={"asset": a_name}).json()
        cells = [(r, col, cell) for r, cols in hm["cells"].items() for col, cell in cols.items()]
        ev = [x for x in cells if x[2]["state"] == "evidence"]
        ws = [x for x in cells if x[2]["state"] in ("white_space", "registered")]
        badges = sorted({x[2]["validity"] for x in ev if x[2]["validity"]})
        nct_cells = [x for x in ev if any(NCT.match(r) for r in x[2].get("refs", []))]
        rep.check("Evidence heatmap populated", bool(ev) and bool(ws),
                  f"{len(hm['rows'])} rows × {len(hm['columns'])} columns; {len(ev)} evidence cells (badges L{badges}), {len(ws)} white-space/registered")
        rep.check("Heatmap cells cite real NCT IDs", bool(nct_cells), f"{len(nct_cells)} cells")
        out["heatmap_columns"] = [c_["label"] for c_ in hm["columns"]]

        # 6. study concept on a high-MVD white-space / low-validity row
        target = None
        for r, col, cell in sorted(cells, key=lambda x: -(x[2].get("mvd_score") or 0)):
            if (cell["state"] in ("white_space", "registered") or (cell.get("validity") or 0) <= 2) and (cell.get("mvd_score") or 0) >= 0.3:
                target = (r, col, cell["state"])
                break
        target = target or (ws[0][0], ws[0][1], ws[0][2]["state"]) if ws else target
        if target:
            sc = c.post("/api/v1/study-concept", json={"asset": a_name, "row": target[0], "column": target[1], "cell_state": target[2]}).json()
            precs = [p["nct_id"] for p in sc.get("precedents", [])]
            rep.check("Study concept generator uses live precedent NCT IDs", bool(precs) and all(NCT.match(p) for p in precs),
                      f"{target[0]} @ {target[1]}: {sc.get('design', {}).get('type')}; N/arm {sc.get('sample_size', {}).get('n_per_arm_with_dropout')}; "
                      f"precedents {precs[:6]}")
            out["study_concept"] = {k: sc.get(k) for k in ("gap", "design", "duration_weeks", "sample_size", "n_precedents")} | {"precedents": precs[:15]}
        else:
            rep.check("Study concept generator uses live precedent NCT IDs", False, "no white-space cell found")

        # 7. PMIDs
        from mednik.db import session_scope
        from mednik.models import Document, ExtractedEndpoint, GuidelineRecommendation, MarketValueDriver

        with session_scope() as s:
            n_docs = len([d for d in s.execute(select(Document)).scalars().all() if d.pmid])
            ep_pmids = {e.source_id for e in s.execute(select(ExtractedEndpoint).where(ExtractedEndpoint.source_type.in_(("pubmed", "epmc")))).scalars().all()}
            mvd_pmids = {p for d in s.execute(select(MarketValueDriver)).scalars().all() for p in (d.supporting_pmids_json or [])}
            gl_pmids = {g.source_id for g in s.execute(select(GuidelineRecommendation)).scalars().all()}
        all_p = {p for p in ep_pmids | mvd_pmids | gl_pmids if PMID.match(str(p))}
        rep.check("PubMed records ingested", n_docs > 0, f"{n_docs} documents with PMIDs")
        rep.check("Evidence / drivers / guidelines cite real PMIDs", bool(all_p),
                  f"endpoints {len(ep_pmids)}, MVD {len(mvd_pmids)}, guideline {len(gl_pmids)}; e.g. {sorted(all_p)[:6]}")
        g = c.get("/api/v1/guidelines", params={"indication": args.indication}).json()
        out["guideline_status"] = {x["asset"]: x["status"]["status"] for x in g["assets"]}
        print("Guideline status:", json.dumps(out["guideline_status"], indent=1))
        audit = c.get("/api/v1/audit").json()
        out["citation_audit"] = {"accepted": audit["accepted"], "rejected": audit["rejected"]}
        rep.check("Citation validator active", audit["accepted"] + audit["rejected"] > 0 or args.skip_literature,
                  f"{audit['accepted']} accepted / {audit['rejected']} rejected extractions", hard=False)
        dossier = c.get("/api/v1/export/dossier", params={"indication": args.indication, "asset_a": a_name, "comparators": comps})
        (ROOT / "verification_dossier.md").write_text(dossier.text, encoding="utf-8")
        rep.check("Dossier export", dossier.status_code == 200 and bool(NCT.search(dossier.text)), "backend/verification_dossier.md")
    return finish(rep, out)


def finish(rep: Report, out: dict) -> int:
    out["checks"] = rep.rows
    (ROOT / "verification_report.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    md = ["# mednik — live verification report", "", "| Status | Check | Detail |", "|---|---|---|"]
    md += [f"| {r['status']} | {r['check']} | {r['detail'].replace('|', '/')[:400]} |" for r in rep.rows]
    (ROOT / "verification_report.md").write_text("\n".join(md), encoding="utf-8")
    n_fail = sum(r["status"] == "FAIL" for r in rep.rows)
    n_warn = sum(r["status"] == "WARN" for r in rep.rows)
    print(f"\n{len(rep.rows) - n_fail - n_warn} passed, {n_warn} warnings, {n_fail} failed → backend/verification_report.md")
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
