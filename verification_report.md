# mednik — live verification report

| Status | Check | Detail |
|---|---|---|
| PASS | Ingest job completed | 5s |
| PASS | Gemini usage for the sync | 0 requests sent, 29 from cache, ~0 input tokens, 0 output tokens, 0 rate-limit waits, 0 extractions given up |
| PASS | Caplyta: USPI sections parsed by LOINC | set_id db730b06-6351-47fd-8183-e61e61bbead5 eff 2025-06-20; LOINC ['34066-1', '34067-9', '34068-7', '34070-3', '34073-7', '34084-4', '34092-7', '43679-0', '43684-0', '43685-7'] |
| PASS | Caplyta: §7 CYP3A4 rules extracted with verbatim text | CYP3A4 inducers → AVOID; Moderate CYP3A4 inhibitors → DOSE_ADJUSTMENT [21 mg once daily]; Strong CYP3A4 inhibitors → DOSE_ADJUSTMENT [10.5 mg once daily] |
| PASS | Caplyta: strong_cyp3a4_inhibitor = DOSE_ADJUSTMENT | parsed DOSE_ADJUSTMENT |
| PASS | Caplyta: moderate_cyp3a4_inhibitor = DOSE_ADJUSTMENT | parsed DOSE_ADJUSTMENT |
| PASS | Caplyta: cyp3a4_inducer = AVOID | parsed AVOID |
| PASS | Vraylar: USPI sections parsed by LOINC | set_id 4b5f7c65-aa2d-452a-b3db-bc85c06ff12f eff 2025-12-18; LOINC ['34066-1', '34067-9', '34068-7', '34070-3', '34073-7', '34084-4', '34092-7', '43679-0', '43684-0', '43685-7'] |
| PASS | Vraylar: §7 CYP3A4 rules extracted with verbatim text | CYP3A4 inducers → AVOID; Moderate CYP3A4 inhibitors → DOSE_ADJUSTMENT (pediatric: AVOID); Strong CYP3A4 inhibitors → DOSE_ADJUSTMENT (pediatric: AVOID); CYP3A4 inhibitors → MENTION_ONLY |
| PASS | Vraylar: strong_cyp3a4_inhibitor = DOSE_ADJUSTMENT | parsed DOSE_ADJUSTMENT |
| PASS | Vraylar: cyp3a4_inducer = AVOID | parsed AVOID |
| PASS | Cobenfy: USPI sections parsed by LOINC | set_id 8f0e73bf-6025-44f6-ab64-0983322de0df eff 2026-01-16; LOINC ['34067-9', '34068-7', '34070-3', '34073-7', '34084-4', '34092-7', '43679-0', '43684-0', '43685-7'] |
| PASS | Cobenfy: §7 CYP3A4 rules extracted with verbatim text | CYP3A4 substrates → MONITOR |
| PASS | Dose adjustment distinguished from avoid / contraindication | actions seen: ['AVOID', 'DOSE_ADJUSTMENT', 'MENTION_ONLY', 'MONITOR'] |
| PASS | Caplyta: sponsor class matches raw leadSponsor.class for all 38 trials | 4 INDUSTRY Phase 3 in Schizophrenia; 12 academic/IIS overall |
| PASS | Caplyta: ≥1 INDUSTRY Phase 3 trial in Schizophrenia | NCT06229210, NCT04959032, NCT02469155, NCT02282761 |
| PASS | Vraylar: sponsor class matches raw leadSponsor.class for all 53 trials | 10 INDUSTRY Phase 3 in Schizophrenia; 15 academic/IIS overall |
| PASS | Vraylar: ≥1 INDUSTRY Phase 3 trial in Schizophrenia | NCT07722728, NCT05368558, NCT04578756, NCT05168007, NCT03817502, NCT03593213 |
| PASS | Cobenfy: sponsor class matches raw leadSponsor.class for all 52 trials | 13 INDUSTRY Phase 3 in Schizophrenia; 7 academic/IIS overall |
| PASS | Cobenfy: ≥1 INDUSTRY Phase 3 trial in Schizophrenia | NCT07288567, NCT07424404, NCT07686263, NCT06882785, NCT06572449, NCT05919823 |
| PASS | 2×2 matrix populated | 19/30 nodes with a differentiation score; positive_symptoms vs cariprazine: TL, relapse_prevention vs cariprazine: TR, akathisia_eps vs cariprazine: BR, weight_metabolic vs cariprazine: TR, sedation vs cariprazine: TL, anticholinergic_gi vs cariprazine: BL |
| PASS | Cross-trial confounder alert computed | positive_symptoms vs cariprazine: alert=True; Baseline severity 89.0 (PANSS Total) vs 96.6 (PANSS Total); Trial architecture acute parallel, placebo-controlled vs acute parallel, placebo-controlled; Primary endpoint timing 4.0 wk vs 6.0 wk; Placebo response -10.3 (PANSS Total change) vs -14.3 (PANSS Total change) ⚠ |
| PASS | Matrix evidence cites real NCT IDs |  |
| PASS | Evidence heatmap populated | 29 rows × 16 columns; 35 evidence cells (badges L[2, 3, 4, 5]), 429 white-space/registered |
| PASS | Heatmap cells cite real NCT IDs | 33 cells |
| PASS | Study concept generator uses live precedent NCT IDs | WEIGHT @ schizophrenia/adult/intermediate: Phase 4 randomized active-controlled; N/arm 603; precedents ['NCT04820309', 'NCT06585787', 'NCT06947941', 'NCT04578756', 'NCT04738123', 'NCT06882785'] |
| PASS | PubMed records ingested | 686 documents with PMIDs |
| PASS | Evidence / drivers / guidelines cite real PMIDs | endpoints 10, MVD 23, guideline 0; e.g. ['18642975', '24828964', '27106681', '27485931', '27514778', '28038919'] |
| PASS | Citation validator active | 65 accepted / 10 rejected extractions |
| PASS | Dossier export | backend/verification_dossier.md |
| PASS | Dossier PDF export | 662 KB |