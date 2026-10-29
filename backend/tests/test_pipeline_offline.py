"""End-to-end pipeline test with HTTP mocked at the transport layer (synthetic, format-faithful payloads).

Exercises: /ingest → labels, registry, label linkage, literature, MVDs, guidelines → /matrix,
/matrix/node (confounder alert + objection card), /heatmap, /heatmap/cell, /study-concept,
/guidelines, /export/dossier. The live equivalent is scripts/verify_e2e.py.
"""

import json
import re
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from .fixtures_data import ctgov_page, ctgov_study, pubmed_xml, spl_xml

SET_A, SET_B = "aaaaaaaa-0000-0000-0000-00000000000a", "bbbbbbbb-0000-0000-0000-00000000000b"

TRIALS = {
    "alphazine": [
        ctgov_study("NCT00000011", "A Phase 3 Study of Alphazine in Acute Schizophrenia", "Alphazine", diff=-8.0, ci=(-11.0, -5.0), baseline=95.0),
        ctgov_study("NCT00000012", "A Second Phase 3 Study of Alphazine in Schizophrenia", "Alphazine", diff=-7.0, ci=(-10.0, -4.0), baseline=94.0),
        ctgov_study("NCT00000013", "Alphazine Cognition Pilot (Investigator-Initiated)", "Alphazine", sponsor_class="OTHER", phase="PHASE4", n=60, results=False),
    ],
    "betapride": [
        ctgov_study("NCT00000021", "A Phase 3 Study of Betapride in Schizophrenia", "Betapride", diff=-6.0, ci=(-9.0, -3.0), baseline=82.0,
                    start="2012-01", us_share=0.3, akathisia=(6, 9)),
        ctgov_study("NCT00000022", "A Phase 2 Study of Betapride", "Betapride", phase="PHASE2", diff=-5.0, ci=(-9.0, -1.0), baseline=83.0, start="2010-05"),
    ],
}

PUBS = {
    "39000001": {"pmid": "39000001", "title": "Alphazine in acute schizophrenia: a randomized, placebo-controlled phase 3 trial", "nct": "NCT00000011",
                 "pub_types": ["Clinical Trial, Phase III", "Randomized Controlled Trial"], "affiliation": "Fictional Pharma Inc.",
                 "abstract": "In this 6-week trial of alphazine, the LS mean difference versus placebo in PANSS total score was -8.0 (95% CI -11.0 to -5.0; P<0.001)."},
    "39000002": {"pmid": "39000002", "title": "Negative symptoms with betapride: a post hoc pooled analysis",
                 "pub_types": ["Journal Article"], "abstract": "In this post hoc pooled analysis of betapride, the LS mean difference in PANSS Marder negative factor was -1.5 (95% CI -2.4 to -0.6; P=0.001)."},
    "39000101": {"pmid": "39000101", "title": "Psychiatrist preferences for schizophrenia treatment attributes: a discrete choice experiment",
                 "pub_types": ["Journal Article"], "year": 2023,
                 "abstract": "Psychiatrists (N=250) completed a discrete choice experiment. Efficacy on positive symptoms had the highest relative importance (32%), followed by weight gain (21%), akathisia (15%), sedation (12%), and once-daily dosing (8%)."},
    "39000201": {"pmid": "39000201", "title": "Clinical practice guideline for the pharmacological treatment of schizophrenia", "year": 2024,
                 "pub_types": ["Practice Guideline"],
                 "abstract": "Alphazine is recommended as a first-line monotherapy option for acute schizophrenia. Betapride is a second-line option given tolerability considerations. Clinicians should consider weight gain and akathisia when selecting treatment."},
}


def _ctgov(request: httpx.Request) -> httpx.Response:
    q = parse_qs(request.url.query.decode())
    intr = (q.get("query.intr") or [""])[0].lower()
    studies = []
    for name, sts in TRIALS.items():
        if name in intr or not intr:
            studies += sts
    return httpx.Response(200, text=ctgov_page(studies))


def _esearch(request: httpx.Request) -> httpx.Response:
    term = parse_qs(request.url.query.decode())["term"][0].lower()
    if "discrete choice" in term:
        ids = ["39000101", "39000201"]
    elif "guideline" in term and "alphazine" not in term:
        ids = ["39000201"]
    else:
        ids = ["39000001", "39000002"]
    return httpx.Response(200, json={"esearchresult": {"count": str(len(ids)), "idlist": ids}})


def _efetch(request: httpx.Request) -> httpx.Response:
    ids = parse_qs(request.content.decode())["id"][0].split(",")
    return httpx.Response(200, text=pubmed_xml([PUBS[i] for i in ids if i in PUBS]))


def _dailymed_list(request: httpx.Request) -> httpx.Response:
    name = parse_qs(request.url.query.decode())["drug_name"][0].lower()
    rows = []
    if name in ("alphyl", "alphazine"):
        rows = [{"setid": SET_A, "title": "ALPHYL- alphazine capsule [Fictional Pharma Inc.]", "published_date": "Mar 15, 2025"}]
    if name in ("betyx", "betapride"):
        rows = [{"setid": SET_B, "title": "BETYX- betapride tablet [Other Pharma Inc.]", "published_date": "Jan 02, 2024"}]
    return httpx.Response(200, json={"data": rows, "metadata": {"total_pages": 1}})


def _dailymed_xml(request: httpx.Request) -> httpx.Response:
    if SET_A in request.url.path:
        return httpx.Response(200, text=spl_xml("Alphyl", "alphazine", SET_A, boxed=False, titration=False, cyp_action="reduce",
                                                diff1="-8.0* (-11.0, -5.0)", diff2="-7.0* (-10.0, -4.0)", akathisia=(4, 7), weight=(2, 3)))
    return httpx.Response(200, text=spl_xml("Betyx", "betapride", SET_B, boxed=True, titration=True, cyp_action="contra",
                                            diff1="-6.0* (-9.0, -3.0)", diff2="-5.0* (-8.5, -1.5)", akathisia=(4, 14), weight=(2, 9), base=(82.5, 83.0)))


@pytest.fixture()
def client():
    from mednik.api.main import app

    with respx.mock(assert_all_called=False) as m:
        m.get(re.compile(r"https://dailymed\.nlm\.nih\.gov/.*/spls\.json.*")).mock(side_effect=_dailymed_list)
        m.get(re.compile(r"https://dailymed\.nlm\.nih\.gov/.*/spls/.*\.xml")).mock(side_effect=_dailymed_xml)
        m.get(re.compile(r"https://api\.fda\.gov/.*")).mock(return_value=httpx.Response(404, json={"error": {"code": "NOT_FOUND"}}))
        m.get(re.compile(r"https://clinicaltrials\.gov/api/v2/studies.*")).mock(side_effect=_ctgov)
        m.get(re.compile(r"https://eutils\.ncbi\.nlm\.nih\.gov/.*/esearch\.fcgi.*")).mock(side_effect=_esearch)
        m.post(re.compile(r"https://eutils\.ncbi\.nlm\.nih\.gov/.*/efetch\.fcgi.*")).mock(side_effect=_efetch)
        m.get(re.compile(r"https://www\.ebi\.ac\.uk/.*")).mock(return_value=httpx.Response(200, json={"resultList": {"result": []}}))
        with TestClient(app) as c:
            yield c


def test_full_pipeline_offline(client):
    r = client.post("/api/v1/ingest?wait=true", json={
        "indication": "Schizophrenia",
        "assets": [{"generic": "alphazine", "brand": "Alphyl"}, {"generic": "betapride", "brand": "Betyx"}],
    })
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    job = client.get(f"/api/v1/jobs/{jid}").json()
    assert job["status"] == "done", job["error"]
    summ = job["summary"]
    assert summ["assets"]["alphazine"]["label"]["cyp_key_classes"]["strong_cyp3a4_inhibitor"] == "DOSE_ADJUSTMENT"
    assert summ["assets"]["betapride"]["label"]["cyp_key_classes"]["strong_cyp3a4_inhibitor"] == "CONTRAINDICATED"
    assert summ["assets"]["alphazine"]["trials"]["split"] == {"industry_phase3plus": 2, "industry_other": 0, "academic_iis": 1}
    assert summ["assets"]["alphazine"]["trials"]["label_linked_results"] >= 1

    # sponsor split endpoint
    t = client.get("/api/v1/trials", params={"asset": "alphazine", "indication": "Schizophrenia"}).json()
    classes = {x["nct_id"]: x["sponsor_class"] for x in t["trials"]}
    assert classes["NCT00000013"] == "ACADEMIC_IIS" and classes["NCT00000011"] == "INDUSTRY"

    # Module 1
    mvd = client.get("/api/v1/mvd", params={"indication": "Schizophrenia"}).json()
    top = mvd["drivers"][0]
    assert top["key"] == "positive_symptoms" and top["effective_score"] == 1.0
    assert any(d["key"] == "cognition" and d["method"] == "evidence_floor" for d in mvd["drivers"])

    # Module 2
    mx = client.get("/api/v1/matrix", params={"indication": "Schizophrenia", "asset_a": "alphazine", "comparators": "betapride"}).json()
    nodes = {n["mvd_key"]: n for n in mx["nodes"]}
    assert nodes["positive_symptoms"]["x"] is not None and nodes["positive_symptoms"]["x"] > 0
    assert nodes["no_titration"]["x"] > 0 and nodes["no_titration"]["tier"] == "1A"
    assert nodes["akathisia_eps"]["x"] > 0  # alphazine lower excess akathisia
    assert nodes["cognition"]["insufficient"] and nodes["cognition"]["quadrant"] in ("TL", "BL")
    det = client.get("/api/v1/matrix/node", params={"indication": "Schizophrenia", "asset_a": "alphazine", "asset_b": "betapride",
                                                    "mvd_key": "positive_symptoms"}).json()
    assert det["confounder_alert"]["alert"]  # baseline PANSS 94.5 vs 82.5 → >10 %
    assert any(i["dimension"] == "Baseline severity" and i["triggered"] for i in det["confounder_alert"]["items"])
    assert any(e["study"]["nct_id"] == "NCT00000011" for e in det["evidence_a"])
    if det["quadrant"] == "TR":
        assert det["objection_card"]["challenges"]
    reg = {r["attribute"]: r for r in det["regulatory_diff"]}
    assert reg["Strong CYP3A4 inhibitors"]["a"] == "Dose adjustment" and reg["Strong CYP3A4 inhibitors"]["b"] == "Contraindicated"
    assert reg["Boxed warning"]["advantage"] == "A"

    # Module 3
    hm = client.get("/api/v1/heatmap", params={"asset": "alphazine"}).json()
    col = "schizophrenia|adult|acute"
    assert hm["cells"]["PANSS_TOTAL"][col]["validity"] == 5
    assert hm["cells"]["COGNITION"][col]["state"] in ("white_space", "registered")
    cell = client.get("/api/v1/heatmap/cell", params={"asset": "alphazine", "row": "PANSS_TOTAL", "column": col}).json()
    assert cell["endpoints"] and all(e["verbatim_quote"] for e in cell["endpoints"])

    # Adjacency C
    sc = client.post("/api/v1/study-concept", json={"asset": "alphazine", "row": "PSP", "column": col, "cell_state": "white_space"}).json()
    assert sc["sample_size"]["n_per_arm"] and sc["precedents"] and sc["precedents"][0]["nct_id"].startswith("NCT")
    assert any(i["criterion"].startswith("Substance") for i in sc["exclusion"])

    # Adjacency D
    g = client.get("/api/v1/guidelines", params={"indication": "Schizophrenia"}).json()
    st = {a["generic"]: a for a in g["assets"]}
    assert st["alphazine"]["status"]["line"] == "FIRST"
    assert st["betapride"]["status"]["line"] == "SECOND"
    assert st["alphazine"]["gap_to_guideline"]["checks"]

    # export
    md = client.get("/api/v1/export/dossier", params={"indication": "Schizophrenia", "asset_a": "alphazine", "comparators": "betapride"})
    assert md.status_code == 200 and "Medical Strategy Briefing" in md.text and "NCT00000011" in md.text
    pdf = client.get("/api/v1/export/dossier", params={"indication": "Schizophrenia", "asset_a": "alphazine", "comparators": "betapride", "format": "pdf"})
    assert pdf.content[:5] == b"%PDF-"

    audit = client.get("/api/v1/audit").json()
    assert audit["accepted"] > 0
    json.dumps(mx)  # serialisable
