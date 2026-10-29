"""Format-faithful SYNTHETIC fixtures for offline tests.

These mimic the structure of DailyMed SPL XML, ClinicalTrials.gov v2 JSON, PubMed EFetch XML and
Europe PMC JSON so parsers can be tested without network access. Drug names ("alphazine" /
"Alphyl", "betapride" / "Betyx") and all numbers are fictional. They are never used at runtime;
the application only reads live API payloads (and their cache).
"""

from __future__ import annotations

import json


def spl_xml(brand: str, generic: str, set_id: str, *, boxed: bool, titration: bool, cyp_action: str, diff1: str, diff2: str,
            akathisia: tuple[int, int], weight: tuple[int, int], base: tuple[float, float] = (96.0, 94.0)) -> str:
    bw = """
    <component><section><code code="34066-1" codeSystem="2.16.840.1.113883.6.1" displayName="BOXED WARNING SECTION"/>
      <title>WARNING: INCREASED MORTALITY IN ELDERLY PATIENTS WITH DEMENTIA-RELATED PSYCHOSIS</title>
      <text><paragraph>Elderly patients with dementia-related psychosis treated with antipsychotic drugs are at an increased risk of death.</paragraph></text>
    </section></component>""" if boxed else ""
    tit = (f"<paragraph>The starting dosage of {brand} is 1 mg once daily. The dosage can be increased to 2 mg on Day 2.</paragraph>"
           if titration else f"<paragraph>The recommended dosage of {brand} is 40 mg once daily. Dose titration is not required.</paragraph>")
    cyp_text = {
        "reduce": f"If {brand} is used with a strong CYP3A4 inhibitor, reduce the {brand} dosage to one-half.",
        "contra": f"{brand} is contraindicated with strong CYP3A4 inhibitors.",
    }[cyp_action]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<document xmlns="urn:hl7-org:v3">
  <id root="00000000-0000-0000-0000-000000000000"/>
  <setId root="{set_id}"/><versionNumber value="7"/><effectiveTime value="20250315"/>
  <title>These highlights do not include all the information needed to use {brand.upper()} safely. {brand.upper()} ({generic}) capsules, for oral use Initial U.S. Approval: 2019</title>
  <author><assignedEntity><representedOrganization><name>Fictional Pharma Inc.</name></representedOrganization></assignedEntity></author>
  <component><structuredBody>
    {bw}
    <component><section><code code="34067-9" displayName="INDICATIONS &amp; USAGE SECTION"/><title>1 INDICATIONS AND USAGE</title>
      <text><list><item>{brand} is indicated for the treatment of schizophrenia in adults.</item>
      <item>{brand} is indicated for depressive episodes associated with bipolar I disorder (bipolar depression) in adults.</item></list></text>
    </section></component>
    <component><section><code code="34068-7" displayName="DOSAGE &amp; ADMINISTRATION SECTION"/><title>2 DOSAGE AND ADMINISTRATION</title>
      <text>{tit}<paragraph>{brand} can be taken with or without food.</paragraph></text>
      <component><section><code code="42229-5"/><title>2.3 Dosage Modifications for Hepatic Impairment</title>
        <text><paragraph>For patients with moderate or severe hepatic impairment (Child-Pugh class B or C), the recommended dosage is 20 mg once daily.</paragraph></text>
      </section></component>
    </section></component>
    <component><section><code code="34070-3" displayName="CONTRAINDICATIONS SECTION"/><title>4 CONTRAINDICATIONS</title>
      <text><paragraph>{brand} is contraindicated in patients with known hypersensitivity to {generic}.</paragraph></text>
    </section></component>
    <component><section><code code="43685-7" displayName="WARNINGS AND PRECAUTIONS SECTION"/><title>5 WARNINGS AND PRECAUTIONS</title>
      <text><paragraph>Monitor fasting plasma glucose and lipids at baseline and periodically during treatment.</paragraph></text>
    </section></component>
    <component><section><code code="34084-4" displayName="ADVERSE REACTIONS SECTION"/><title>6 ADVERSE REACTIONS</title>
      <component><section><code code="42229-5"/><title>6.1 Clinical Trials Experience</title><text>
        <table><caption>Table 3: Adverse Reactions in 2% or More of {brand}-Treated Patients in Schizophrenia Trials</caption>
          <thead><tr><th>Adverse Reaction</th><th>Placebo (N=300)</th><th>{brand} (N=600)</th></tr></thead>
          <tbody>
            <tr><td>Nervous System Disorders</td><td></td><td></td></tr>
            <tr><td>Akathisia</td><td>{akathisia[0]}</td><td>{akathisia[1]}</td></tr>
            <tr><td>Somnolence</td><td>5</td><td>9</td></tr>
            <tr><td>Weight increased</td><td>{weight[0]}</td><td>{weight[1]}</td></tr>
          </tbody></table></text></section></component>
    </section></component>
    <component><section><code code="34073-7" displayName="DRUG INTERACTIONS SECTION"/><title>7 DRUG INTERACTIONS</title>
      <text><table><caption>Table 4: Clinically Important Drug Interactions with {brand}</caption><tbody>
        <tr><td>Strong CYP3A4 Inhibitors</td><td></td></tr>
        <tr><td>Clinical Impact:</td><td>Concomitant use increased exposure of {generic}.</td></tr>
        <tr><td>Intervention:</td><td>{cyp_text}</td></tr>
        <tr><td>CYP3A4 Inducers</td><td></td></tr>
        <tr><td>Intervention:</td><td>Concomitant use of {brand} with CYP3A4 inducers is not recommended.</td></tr>
      </tbody></table></text>
    </section></component>
    <component><section><code code="34092-7" displayName="CLINICAL STUDIES SECTION"/><title>14 CLINICAL STUDIES</title>
      <component><section><code code="42229-5"/><title>14.1 Schizophrenia</title>
        <text><paragraph>The efficacy of {brand} was established in two 6-week, randomized, double-blind, placebo-controlled trials (Study 1 and Study 2). {brand} separated from placebo as early as Week 1.</paragraph>
        <table><caption>Table 10: Primary Efficacy Results for Change from Baseline in PANSS Total Score at Week 6</caption>
          <thead><tr><th>Study Number</th><th>Treatment Group</th><th>Mean Baseline Score (SD)</th><th>LS Mean Change from Baseline (SE)</th><th>Placebo-subtracted Difference (95% CI)</th></tr></thead>
          <tbody>
            <tr><td>Study 1</td><td>{brand} (N=150)</td><td>{base[0]} (8.0)</td><td>-20.0 (1.5)</td><td>{diff1}</td></tr>
            <tr><td></td><td>Placebo (N=150)</td><td>{base[0]} (8.2)</td><td>-12.0 (1.5)</td><td>--</td></tr>
            <tr><td>Study 2</td><td>{brand} (N=200)</td><td>{base[1]} (9.0)</td><td>-18.0 (1.3)</td><td>{diff2}</td></tr>
            <tr><td></td><td>Placebo (N=200)</td><td>{base[1]} (8.8)</td><td>-11.0 (1.3)</td><td>--</td></tr>
          </tbody>
          <tfoot><tr><td>* Doses statistically significantly superior to placebo after adjustment for multiplicity.</td></tr></tfoot></table>
        </text></section></component>
    </section></component>
  </structuredBody></component>
</document>"""


def ctgov_study(nct: str, title: str, drug: str, *, sponsor_class: str = "INDUSTRY", phase: str = "PHASE3", n: int = 300,
                diff: float = -8.0, ci: tuple[float, float] = (-11.0, -5.0), p: str = "<0.001", baseline: float = 95.0,
                start: str = "2019-03", us_share: float = 1.0, akathisia: tuple[int, int] = (6, 18), results: bool = True,
                condition: str = "Schizophrenia", status: str = "COMPLETED") -> dict:
    half = n // 2
    n_us = int(round(10 * us_share))
    locs = [{"country": "United States"}] * n_us + [{"country": "Ukraine"}] * (10 - n_us)
    study = {
        "protocolSection": {
            "identificationModule": {"nctId": nct, "briefTitle": title, "officialTitle": title},
            "statusModule": {"overallStatus": status, "startDateStruct": {"date": start}},
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Fictional Pharma" if sponsor_class == "INDUSTRY" else "State University",
                                                           "class": sponsor_class}},
            "conditionsModule": {"conditions": [condition]},
            "designModule": {"studyType": "INTERVENTIONAL", "phases": [phase],
                             "designInfo": {"allocation": "RANDOMIZED", "interventionModel": "PARALLEL", "primaryPurpose": "TREATMENT",
                                            "maskingInfo": {"masking": "QUADRUPLE"}},
                             "enrollmentInfo": {"count": n, "type": "ACTUAL"}},
            "armsInterventionsModule": {
                "armGroups": [{"label": drug, "type": "EXPERIMENTAL", "interventionNames": [f"Drug: {drug}"]},
                              {"label": "Placebo", "type": "PLACEBO_COMPARATOR", "interventionNames": ["Drug: Placebo"]}],
                "interventions": [{"type": "DRUG", "name": drug}, {"type": "DRUG", "name": "Placebo"}]},
            "outcomesModule": {"primaryOutcomes": [{"measure": "Change From Baseline in PANSS Total Score at Week 6", "timeFrame": "Baseline to Week 6"}],
                               "secondaryOutcomes": [{"measure": "Change From Baseline in CGI-S Score", "timeFrame": "Baseline to Week 6"},
                                                     {"measure": "Change From Baseline in PSP Total Score", "timeFrame": "Baseline to Week 6"}]},
            "eligibilityModule": {"eligibilityCriteria": "Inclusion Criteria:\n* DSM-5 diagnosis of schizophrenia\n* PANSS total score ≥ 80\n* CGI-S score ≥ 4\n\nExclusion Criteria:\n* Substance use disorder within 6 months\n* Significant risk of suicide (C-SSRS)\n* Treatment-resistant schizophrenia or clozapine use",
                                  "minimumAge": "18 Years", "maximumAge": "65 Years", "sex": "ALL"},
            "contactsLocationsModule": {"locations": locs},
            "referencesModule": {"references": [{"pmid": "39000001", "type": "RESULT"}]},
        },
        "hasResults": results,
    }
    if results:
        study["resultsSection"] = {
            "participantFlowModule": {"groups": [{"id": "FG000", "title": drug}, {"id": "FG001", "title": "Placebo"}],
                                      "periods": [{"milestones": [
                                          {"type": "STARTED", "achievements": [{"groupId": "FG000", "numSubjects": str(half)}, {"groupId": "FG001", "numSubjects": str(half)}]},
                                          {"type": "COMPLETED", "achievements": [{"groupId": "FG000", "numSubjects": str(int(half * 0.8))}, {"groupId": "FG001", "numSubjects": str(int(half * 0.7))}]}]}]},
            "baselineCharacteristicsModule": {
                "groups": [{"id": "BG000", "title": drug}, {"id": "BG001", "title": "Placebo"}, {"id": "BG002", "title": "Total"}],
                "measures": [{"title": "PANSS Total Score", "paramType": "MEAN", "dispersionType": "STANDARD_DEVIATION",
                              "classes": [{"categories": [{"measurements": [{"groupId": "BG000", "value": str(baseline), "spread": "8"},
                                                                            {"groupId": "BG001", "value": str(baseline), "spread": "8"},
                                                                            {"groupId": "BG002", "value": str(baseline), "spread": "8"}]}]}]}]},
            "outcomeMeasuresModule": {"outcomeMeasures": [
                {"type": "PRIMARY", "title": "Change From Baseline in PANSS Total Score at Week 6", "timeFrame": "Baseline to Week 6",
                 "paramType": "LEAST_SQUARES_MEAN", "dispersionType": "STANDARD_ERROR", "unitOfMeasure": "units on a scale",
                 "groups": [{"id": "OG000", "title": drug}, {"id": "OG001", "title": "Placebo"}],
                 "denoms": [{"units": "Participants", "counts": [{"groupId": "OG000", "value": str(half)}, {"groupId": "OG001", "value": str(half)}]}],
                 "classes": [{"categories": [{"measurements": [{"groupId": "OG000", "value": str(-12 + diff), "spread": "1.4"},
                                                               {"groupId": "OG001", "value": "-12.0", "spread": "1.4"}]}]}],
                 "analyses": [{"groupIds": ["OG000", "OG001"], "paramType": "LS Mean Difference", "paramValue": str(diff), "ciPctValue": "95",
                               "ciLowerLimit": str(ci[0]), "ciUpperLimit": str(ci[1]), "pValue": p, "statisticalMethod": "Mixed Models Analysis"}]},
                {"type": "SECONDARY", "title": "Change From Baseline in PSP Total Score", "timeFrame": "Baseline to Week 6",
                 "paramType": "LEAST_SQUARES_MEAN", "dispersionType": "STANDARD_ERROR", "unitOfMeasure": "units on a scale",
                 "groups": [{"id": "OG000", "title": drug}, {"id": "OG001", "title": "Placebo"}],
                 "denoms": [{"units": "Participants", "counts": [{"groupId": "OG000", "value": str(half)}, {"groupId": "OG001", "value": str(half)}]}],
                 "classes": [{"categories": [{"measurements": [{"groupId": "OG000", "value": "6.0", "spread": "0.9"},
                                                               {"groupId": "OG001", "value": "3.5", "spread": "0.9"}]}]}]},
            ]},
            "adverseEventsModule": {"frequencyThreshold": "5", "eventGroups": [
                {"id": "EG000", "title": drug, "otherNumAtRisk": half}, {"id": "EG001", "title": "Placebo", "otherNumAtRisk": half}],
                "otherEvents": [{"term": "Akathisia", "stats": [{"groupId": "EG000", "numAffected": akathisia[1], "numAtRisk": half},
                                                               {"groupId": "EG001", "numAffected": akathisia[0], "numAtRisk": half}]},
                                {"term": "Somnolence", "stats": [{"groupId": "EG000", "numAffected": 12, "numAtRisk": half},
                                                                 {"groupId": "EG001", "numAffected": 8, "numAtRisk": half}]}]},
        }
    return study


def ctgov_page(studies: list[dict]) -> str:
    return json.dumps({"studies": studies, "totalCount": len(studies)})


def pubmed_xml(records: list[dict]) -> str:
    arts = []
    for r in records:
        pts = "".join(f"<PublicationType>{p}</PublicationType>" for p in r.get("pub_types", ["Journal Article"]))
        db = ""
        if r.get("nct"):
            db = f"<DataBankList><DataBank><DataBankName>ClinicalTrials.gov</DataBankName><AccessionNumberList><AccessionNumber>{r['nct']}</AccessionNumber></AccessionNumberList></DataBank></DataBankList>"
        arts.append(f"""<PubmedArticle><MedlineCitation><PMID>{r['pmid']}</PMID><Article><Journal><Title>J Fict Psychiatry</Title>
<JournalIssue><PubDate><Year>{r.get('year', 2022)}</Year></PubDate></JournalIssue></Journal><ArticleTitle>{r['title']}</ArticleTitle>
<Abstract><AbstractText>{r['abstract']}</AbstractText></Abstract><AuthorList><Author><LastName>Doe</LastName><Initials>J</Initials>
<AffiliationInfo><Affiliation>{r.get('affiliation', 'State University')}</Affiliation></AffiliationInfo></Author></AuthorList>
<PublicationTypeList>{pts}</PublicationTypeList>{db}</Article></MedlineCitation></PubmedArticle>""")
    return f"<?xml version='1.0'?><PubmedArticleSet>{''.join(arts)}</PubmedArticleSet>"
