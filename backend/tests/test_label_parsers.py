from mednik.services.label_parsers import (
    SplSection,
    parse_all,
    parse_cyp_interactions,
    parse_dosing,
    parse_spl_xml,
)

from .fixtures_data import spl_xml


def _doc(**kw):
    defaults = dict(brand="Alphyl", generic="alphazine", set_id="11111111-aaaa-bbbb-cccc-000000000001", boxed=True, titration=True,
                    cyp_action="reduce", diff1="-8.0* (-11.6, -4.4)", diff2="-7.0* (-10.2, -3.8)", akathisia=(4, 12), weight=(2, 3))
    defaults.update(kw)
    return parse_spl_xml(spl_xml(**defaults))


def test_spl_sections_by_loinc():
    doc = _doc()
    assert doc.set_id == "11111111-aaaa-bbbb-cccc-000000000001"
    assert doc.effective_date == "2025-03-15"
    assert doc.initial_us_approval_year == 2019
    for loinc in ("34066-1", "34067-9", "34068-7", "34070-3", "43685-7", "34084-4", "34073-7", "34092-7"):
        assert loinc in doc.sections, loinc
    assert doc.sections["34092-7"].tables, "clinical studies table parsed"


def test_cyp3a4_dose_adjustment_vs_avoid_vs_contraindication():
    parsed = parse_all(_doc().sections, ["alphazine", "Alphyl"])
    kc = parsed["cyp"]["key_classes"]
    assert kc["strong_cyp3a4_inhibitor"] == "DOSE_ADJUSTMENT"
    assert kc["cyp3a4_inducer"] == "AVOID"
    parsed_c = parse_all(_doc(cyp_action="contra").sections, ["alphazine", "Alphyl"])
    assert parsed_c["cyp"]["key_classes"]["strong_cyp3a4_inhibitor"] == "CONTRAINDICATED"
    assert parsed_c["cyp"]["ddi_score"] < parsed["cyp"]["ddi_score"]


def test_cyp_highlights_style_text():
    sec = SplSection("34073-7", "DI", "CYP3A4 inducers: Avoid concomitant use with DRUGX. ( 7.1 ) Strong CYP3A4 inhibitors: Recommended dosage is "
                                     "10 mg once daily. ( 2.2 , 7.1 ) Moderate CYP3A4 inhibitors: Recommended dosage is 20 mg once daily.")
    r = parse_cyp_interactions({"34073-7": sec})
    assert r["key_classes"]["strong_cyp3a4_inhibitor"] == "DOSE_ADJUSTMENT"
    assert r["key_classes"]["moderate_cyp3a4_inhibitor"] == "DOSE_ADJUSTMENT"
    assert r["key_classes"]["cyp3a4_inducer"] == "AVOID"
    doses = {x["strength"]: x["dose_instruction"] for x in r["summary"] if x["enzyme"] == "CYP3A4" and x["modulator"] == "inhibitor"}
    assert doses["strong"].startswith("10 mg")


def test_dosing_titration_food_hepatic():
    d = parse_dosing(_doc(titration=False).sections)
    assert d["titration"]["required"] is False
    assert d["food"]["requirement"] == "none"
    assert d["frequency"] == "once_daily"
    assert d["hepatic"]["action"] == "DOSE_ADJUSTMENT" and "B" in d["hepatic"]["child_pugh"]
    assert parse_dosing(_doc(titration=True).sections)["titration"]["required"] is True


def test_boxed_warning_ae_and_efficacy_tables():
    parsed = parse_all(_doc().sections, ["alphazine", "Alphyl"])
    assert parsed["boxed_warning"]["present"] and "dementia_related_psychosis_mortality" in parsed["boxed_warning"]["topics"]
    ae = parsed["ae_tables"][0]
    assert ae["indication_key"] == "schizophrenia"
    aka = next(r for r in ae["rows"] if r["term"] == "Akathisia")
    pbo_col = next(a["col"] for a in ae["arms"] if a["is_placebo"])
    assert aka["values"][pbo_col] == 4
    rows = parsed["clinical_studies"]["efficacy_rows"]
    drug_rows = [r for r in rows if not r["is_placebo"]]
    assert {r["study_label"] for r in drug_rows} == {"Study 1", "Study 2"}
    r1 = next(r for r in drug_rows if r["study_label"] == "Study 1")
    assert r1["diff"] == -8.0 and r1["ci_low"] == -11.6 and r1["ci_high"] == -4.4 and r1["flag_significant"]
    assert r1["scale"] == "PANSS_TOTAL" and r1["n"] == 150 and r1["multiplicity_footnote"]
    assert parsed["clinical_studies"]["onset"][0]["weeks"] == 1.0
    assert [i["indication_key"] for i in parsed["indications"]] == ["schizophrenia", "bipolar_depression"]
