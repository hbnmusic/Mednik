"""Regression tests built from the layouts of the current DailyMed SPLs (Vraylar, Caplyta, Cobenfy; Oct 2026)
and a ClinicalTrials.gov v2 results record (NCT01104779). Table grids mirror what grid_from_table_element
produces (colspans expanded); text is abridged from the public labels."""

from mednik.services.label_parsers import SplSection, parse_ae_tables, parse_clinical_studies, parse_cyp_interactions
from mednik.services.trials_ingest import parse_ctgov_study, parse_results


def _tbl(rows, caption="", context=""):
    return {"caption": caption, "header_rows": [], "rows": rows, "footnotes": "", "context": context}


def test_vraylar_di_table_strong_or_moderate_inhibitors():
    t = _tbl([
        ["Strong or Moderate CYP3A4 Inhibitors", "Strong or Moderate CYP3A4 Inhibitors"],
        ["Clinical Impact:", "Concomitant use of VRAYLAR with a strong or moderate CYP3A4 inhibitor increases the exposures of cariprazine."],
        ["Intervention:", "If VRAYLAR is used with a strong or moderate CYP3A4 inhibitor, reduce VRAYLAR dosage [see Dosage and Administration ( 2.6 ) ]."],
        ["CYP3A4 Inducers", "CYP3A4 Inducers"],
        ["Clinical Impact:", "The effect of CYP3A4 inducers on the exposure of VRAYLAR has not been evaluated, and the net effect is unclear."],
        ["Intervention:", "Concomitant use of VRAYLAR with a CYP3A4 inducer is not recommended [see Dosage and Administration ( 2.1 , 2.6 ) ]."],
    ], "Table 15. Clinically Significant Drug Interactions with VRAYLAR")
    kc = parse_cyp_interactions({"34073-7": SplSection("34073-7", "7 DRUG INTERACTIONS", "7 DRUG INTERACTIONS", [t])})["key_classes"]
    assert kc["strong_cyp3a4_inhibitor"] == "DOSE_ADJUSTMENT"
    assert kc["moderate_cyp3a4_inhibitor"] == "DOSE_ADJUSTMENT"
    assert kc["cyp3a4_inducer"] == "AVOID"


def test_caplyta_prevention_or_management_rows():
    t = _tbl([
        ["CYP3A4 Inducers*", "CYP3A4 Inducers*"],
        ["Prevention or Management", "Avoid concomitant use of CAPLYTA with CYP3A4 inducers."],
        ["Moderate or Strong CYP3A4 Inhibitors*", "Moderate or Strong CYP3A4 Inhibitors*"],
        ["Prevention or Management", "Reduce the CAPLYTA dosage when used concomitantly with moderate or strong CYP3A4 inhibitors [see Dosage and Administration ( 2.2 ) ]."],
    ])
    da = SplSection("34068-7", "2", "2.2 Dosage Recommendations\nThe recommended CAPLYTA dosage in patients who receive [see Drug Interactions ( 7.1 ) ]:\n"
                                    "Strong CYP3A4 inhibitors is 10.5 mg once daily.\nModerate CYP3A4 inhibitors is 21 mg once daily.")
    r = parse_cyp_interactions({"34073-7": SplSection("34073-7", "7", "7 DRUG INTERACTIONS", [t]), "34068-7": da})
    assert r["key_classes"]["strong_cyp3a4_inhibitor"] == "DOSE_ADJUSTMENT"
    assert r["key_classes"]["cyp3a4_inducer"] == "AVOID"
    doses = {x["strength"]: x["dose_instruction"] for x in r["summary"] if x["modulator"] == "inhibitor" and x["dose_instruction"]}
    assert doses["strong"].startswith("10.5 mg") and doses["moderate"].startswith("21 mg")


def test_cobenfy_perpetrator_language_is_not_a_victim_rule():
    t = _tbl([
        ["Strong Inhibitors of CYP2D6", "Strong Inhibitors of CYP2D6"],
        ["Prevention or Management:", "Monitor patients for increased frequency and/or severity of adverse reactions related to COBENFY in patients taking COBENFY with strong inhibitors of CYP2D6."],
        ["Oral Drugs That Are Sensitive Substrates of CYP3A4", "Oral Drugs That Are Sensitive Substrates of CYP3A4"],
        ["Clinical Implication:", "Xanomeline, a component of COBENFY, transiently inhibits CYP3A4 locally in the gut but not systemically."],
    ])
    kc = parse_cyp_interactions({"34073-7": SplSection("34073-7", "7", "7 DRUG INTERACTIONS", [t])})["key_classes"]
    assert kc["strong_cyp2d6_inhibitor"] == "MONITOR"
    assert kc["strong_cyp3a4_inhibitor"] == "NOT_MENTIONED"


def test_vraylar_section14_multi_arm_cells_and_two_row_header():
    hdr0 = ["Study Number", "Treatment Group (# ITT patients)", "Primary Efficacy Endpoint: MADRS Total", "Primary Efficacy Endpoint: MADRS Total", "Primary Efficacy Endpoint: MADRS Total"]
    hdr1 = ["", "", "Mean Baseline Score (SD)", "LS Mean Change from Baseline (SE)", "Placebo-subtracted Difference a (95% CI)"]
    t = _tbl([
        ["Table 18. Primary Analysis Results from Bipolar Depression Trials"] * 5, hdr0, hdr1,
        ["Study 7", "VRAYLAR (1.5 mg/day)* (n=145) VRAYLAR (3 mg/day)(n=145) Placebo(n=141)", "30.3 (4.4) 30.6 (4.7) 30.4 (4.6)",
         "-15.1 (0.8) -13.7 (0.9) -11.1 (0.9)", "-4.0 (-6.3, -1.6) -2.5 (-4.9, -0.1)"],
        ["Study 8", "VRAYLAR (1.5 mg/day)* (n=154)", "30.7 (4.3)", "-15.1 (0.8)", "-2.5 (-4.6, -0.4)"],
        ["Study 8", "Placebo (n=147)", "30.2 (4.6)", "-12.6 (0.8)", "--"],
    ], context="14.3 Depressive Episodes Associated with Bipolar I Disorder (Bipolar Depression)")
    rows = parse_clinical_studies({"34092-7": SplSection("34092-7", "14", "14 CLINICAL STUDIES", [t])})["efficacy_rows"]
    s7 = [r for r in rows if r["study_label"] == "Study 7"]
    assert len(s7) == 3
    hi = next(r for r in s7 if "3 mg" in r["arm"])
    assert hi["diff"] == -2.5 and hi["ci_low"] == -4.9 and hi["n"] == 145 and hi["baseline_mean"] == 30.6
    lo = next(r for r in s7 if "1.5 mg" in r["arm"])
    assert lo["flag_significant"] and lo["diff"] == -4.0
    assert all(r["scale"] == "MADRS" and r["indication_key"] == "bipolar_depression" for r in rows)


def test_caplyta_section14_numeric_study_and_n_column():
    hdr0 = ["", "", "", "Primary Efficacy Endpoint: PANSS Total Score", "Primary Efficacy Endpoint: PANSS Total Score", "Primary Efficacy Endpoint: PANSS Total Score"]
    hdr1 = ["Study Number", "Treatment Group", "N", "Mean Baseline Score (SD)", "LS Mean Change from Baseline (SE)", "Placebo-subtracted Difference (95% CI)"]
    t = _tbl([hdr0, hdr1,
              ["1", "CAPLYTA (42 mg once daily)*", "84", "88.1 (11.0)", "-13.2 (1.7)", "-5.8 (-10.5, -1.1) a"],
              ["1", "Placebo", "85", "86.3 (13.1)", "-7.4 (1.7)", "--"]],
             "Table 9: Primary Efficacy Results for Change from Baseline in PANSS Total Score in Adult Patients with Schizophrenia (Studies 1 and 2)",
             "14.1 Schizophrenia")
    rows = parse_clinical_studies({"34092-7": SplSection("34092-7", "14", "14 CLINICAL STUDIES", [t])})["efficacy_rows"]
    drug = next(r for r in rows if not r["is_placebo"])
    assert drug["study_label"] == "Study 1" and drug["n"] == 84 and drug["diff"] == -5.8 and drug["scale"] == "PANSS_TOTAL"
    assert next(r for r in rows if r["is_placebo"])["n"] == 85


def test_vraylar_ae_table_drops_above_label_dose_arm():
    hdr0 = ["", "", "VRAYLAR", "VRAYLAR", "VRAYLAR"]
    hdr1 = ["System Organ Class / Preferred Term", "Placebo (N= 584)(%)", "1.5 to 3 mg/day (N=539)(%)", "4.5 to 6 mg/day (N=575)(%)", "9 to 12 mg/day ⸰ (N=203)(%)"]
    t = _tbl([hdr0, hdr1, ["Nervous System Disorders"] * 5, ["Akathisia", "4", "9", "13", "14"], ["Somnolence", "<1", "5", "6", "7"]],
             "Table 8. Adverse Reactions Occurring in ≥ 2% of VRAYLAR-treated Patients and > Placebo-treated Adult Patients in 6-Week Schizophrenia Trials")
    da = SplSection("34068-7", "2", "The maximum recommended dosage is 6 mg orally once daily.")
    out = parse_ae_tables({"34084-4": SplSection("34084-4", "6", "6", [t]), "34068-7": da}, ["cariprazine", "Vraylar"])[0]
    assert out["indication_key"] == "schizophrenia" and len(out["arms"]) == 3 and out["excluded_arms"]
    aka = next(r for r in out["rows"] if r["term"] == "Akathisia")
    assert sorted(v for v in aka["values"].values()) == [4, 9, 13]


def test_ctgov_mean_difference_final_values_and_text_dispersion():
    st = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT01104779", "briefTitle": "Safety and Efficacy of Cariprazine in Schizophrenia"},
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Forest Laboratories", "class": "INDUSTRY"}},
            "conditionsModule": {"conditions": ["Schizophrenia"]},
            "designModule": {"studyType": "INTERVENTIONAL", "phases": ["PHASE3"], "designInfo": {"allocation": "RANDOMIZED", "interventionModel": "PARALLEL"}},
            "armsInterventionsModule": {"armGroups": [{"label": "Cariprazine (3-6 mg/day)", "type": "EXPERIMENTAL", "interventionNames": ["Drug: Cariprazine"]},
                                                      {"label": "Placebo", "type": "PLACEBO_COMPARATOR", "interventionNames": ["Drug: Placebo"]}],
                                        "interventions": [{"type": "DRUG", "name": "Cariprazine", "otherNames": ["RGH-188"]}, {"type": "DRUG", "name": "Placebo"}]},
            "outcomesModule": {"primaryOutcomes": [{"measure": "Change From Baseline in PANSS Total Score", "timeFrame": "Baseline to Week 6"}]},
        },
        "hasResults": True,
        "resultsSection": {"outcomeMeasuresModule": {"outcomeMeasures": [
            {"type": "PRIMARY", "title": "Measurement of Schizophrenia Symptoms: Change From Baseline in Positive and Negative Syndrome Scale (PANSS) Total Score",
             "paramType": "LEAST_SQUARES_MEAN", "dispersionType": "Standard Error", "timeFrame": "Baseline to Week 6",
             "groups": [{"id": "OG000", "title": "Placebo"}, {"id": "OG001", "title": "Cariprazine (3-6 mg/Day)"}],
             "denoms": [{"units": "Participants", "counts": [{"groupId": "OG000", "value": "145"}, {"groupId": "OG001", "value": "147"}]}],
             "classes": [{"categories": [{"measurements": [{"groupId": "OG000", "value": "-16.0", "spread": "1.6"}, {"groupId": "OG001", "value": "-22.8", "spread": "1.6"}]}]}],
             "analyses": [{"groupIds": ["OG000", "OG001"], "paramType": "Mean Difference (Final Values)", "paramValue": "-6.8", "ciPctValue": "95",
                           "ciLowerLimit": "-11.3", "ciUpperLimit": "-2.4", "pValue": "0.0029", "statisticalMethod": "ANCOVA"}]},
            {"type": "SECONDARY", "title": "Change From Baseline in Clinical Global Impression-Severity (CGI-S)", "paramType": "LEAST_SQUARES_MEAN",
             "dispersionType": "Standard Error", "timeFrame": "Baseline to Week 6",
             "groups": [{"id": "OG000", "title": "Placebo"}, {"id": "OG001", "title": "Cariprazine (3-6 mg/Day)"}],
             "denoms": [{"units": "Participants", "counts": [{"groupId": "OG000", "value": "145"}, {"groupId": "OG001", "value": "147"}]}],
             "classes": [{"categories": [{"measurements": [{"groupId": "OG000", "value": "-0.9", "spread": "0.1"}, {"groupId": "OG001", "value": "-1.4", "spread": "0.1"}]}]}]},
        ]}},
    }
    meta = parse_ctgov_study(st, ["cariprazine", "Vraylar", "RGH-188"])
    eps = parse_results(st, meta, ["cariprazine", "Vraylar", "RGH-188"])["endpoints"]
    prim = next(e for e in eps if e["endpoint_scale_name"] == "PANSS_TOTAL")
    assert prim["effect_size_type"] == "LS_MEAN_DIFF" and prim["value"] == -6.8 and prim["cohens_d"] > 0.3 and prim["significant"]
    cgi = next(e for e in eps if e["endpoint_scale_name"] == "CGI_S")
    assert cgi["cohens_d"] is not None and cgi["favorable"]  # SE → SD conversion with text dispersion type
