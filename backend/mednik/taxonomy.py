"""Controlled vocabularies used by the deterministic layers.

Everything here is *configuration*, not evidence: indication lexicons, clinical
scale lexicons (heatmap rows), the Market Value Driver (MVD) taxonomy, adverse
event groupings and asset synonym seeds. Evidence values (weights, effect sizes,
guideline positions) are always computed from ingested source documents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Indications
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndicationDef:
    key: str
    label: str
    icd10: str
    patterns: tuple[str, ...]
    primary_efficacy_mvd: str
    primary_scale_rows: tuple[str, ...]


INDICATIONS: tuple[IndicationDef, ...] = (
    IndicationDef(
        "bipolar_depression", "Bipolar I Depression", "F31.3",
        (r"bipolar\s*(i|1|ii|2)?\s*(disorder)?[\s,-]*(major\s+)?depress", r"depress\w*\s+(episodes?\s+)?(associated\s+with|in|of)\s+bipolar",
         r"bipolar\s+(i\s+|1\s+)?depression", r"depressive\s+episodes?\s+of\s+bipolar"),
        "depressive_symptoms", ("MADRS", "HAMD"),
    ),
    IndicationDef(
        "bipolar_mania", "Bipolar I Mania / Mixed", "F31.1",
        (r"bipolar.{0,40}(mani|mixed)", r"\bmani(a|c)\b", r"acute\s+mani"),
        "manic_symptoms", ("YMRS",),
    ),
    IndicationDef(
        "schizoaffective", "Schizoaffective Disorder", "F25",
        (r"schizoaffective",),
        "positive_symptoms", ("PANSS_TOTAL",),
    ),
    IndicationDef(
        "schizophrenia", "Schizophrenia", "F20",
        (r"schizophreni",),
        "positive_symptoms", ("PANSS_TOTAL",),
    ),
    IndicationDef(
        "mdd", "Major Depressive Disorder", "F33",
        (r"major\s+depress", r"\bmdd\b", r"treatment[-\s]resistant\s+depression", r"unipolar\s+depress"),
        "depressive_symptoms", ("MADRS", "HAMD"),
    ),
    IndicationDef(
        "bipolar_unspecified", "Bipolar Disorder (episode unspecified)", "F31.9",
        (r"bipolar",),
        "manic_symptoms", ("YMRS", "MADRS"),
    ),
    IndicationDef(
        "dementia_psychosis_agitation", "Dementia-related Psychosis / Agitation", "F03.91",
        (r"(alzheimer|dementia).{0,60}(psychos|agitat)", r"(psychos|agitat).{0,60}(alzheimer|dementia)"),
        "positive_symptoms", ("NPI", "CMAI"),
    ),
    IndicationDef(
        "autism_irritability", "Irritability Associated with Autism", "F84.0",
        (r"autis.{0,40}irritab", r"irritab.{0,40}autis"),
        "positive_symptoms", ("ABC_I",),
    ),
    IndicationDef(
        "gad", "Generalized Anxiety Disorder", "F41.1",
        (r"generali[sz]ed\s+anxiety",),
        "anxiety_symptoms", ("HAMA",),
    ),
    IndicationDef(
        "ptsd", "Post-traumatic Stress Disorder", "F43.1",
        (r"post[-\s]?traumatic|\bptsd\b",),
        "anxiety_symptoms", ("CAPS",),
    ),
)

INDICATION_BY_KEY = {d.key: d for d in INDICATIONS}


def classify_indication(*texts: str | None) -> str | None:
    """Return the first indication key whose pattern matches the joined text.

    Order of ``INDICATIONS`` encodes priority (e.g. bipolar depression before
    generic bipolar)."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    for d in INDICATIONS:
        if any(re.search(p, blob) for p in d.patterns):
            return d.key
    return None


def indication_label(key: str | None) -> str:
    if not key:
        return "Unclassified"
    d = INDICATION_BY_KEY.get(key)
    return d.label if d else key.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Clinical scales / endpoint families (= heatmap rows)
# ---------------------------------------------------------------------------

# kind: continuous_efficacy | binary_efficacy | time_to_event | binary_safety | continuous_safety


@dataclass(frozen=True)
class ScaleDef:
    key: str
    label: str
    kind: str
    patterns: tuple[str, ...]
    lower_is_better: bool = True
    mvd_key: str | None = None
    ae_terms: tuple[str, ...] = ()


SCALES: tuple[ScaleDef, ...] = (
    # --- psychosis ---------------------------------------------------------
    ScaleDef("PANSS_NEG_MARDER", "PANSS Marder Negative Factor", "continuous_efficacy",
             (r"marder", r"panss[-\s]?fsns", r"factor\s+score\s+for\s+negative", r"negative\s+symptom\s+factor"), True, "negative_symptoms"),
    ScaleDef("PANSS_NEGATIVE", "PANSS Negative Subscale", "continuous_efficacy",
             (r"panss.{0,40}negative", r"negative\s+subscale"), True, "negative_symptoms"),
    ScaleDef("NEG_OTHER", "Negative Symptoms (NSA-16 / BNSS / SANS)", "continuous_efficacy",
             (r"nsa-?16", r"negative\s+symptom\s+assessment", r"\bbnss\b", r"brief\s+negative\s+symptom", r"\bsans\b"), True, "negative_symptoms"),
    ScaleDef("PANSS_POSITIVE", "PANSS Positive Subscale", "continuous_efficacy",
             (r"panss.{0,40}positive", r"positive\s+subscale"), True, "positive_symptoms"),
    ScaleDef("PANSS_TOTAL", "PANSS Total", "continuous_efficacy",
             (r"panss", r"positive\s+and\s+negative\s+syndrome\s+scale", r"\bbprs\b"), True, "positive_symptoms"),
    # --- mood ------------------------------------------------------------------
    ScaleDef("MADRS", "MADRS Total", "continuous_efficacy",
             (r"madrs", r"montgomery[-\s]*(å|a)sberg"), True, "depressive_symptoms"),
    ScaleDef("HAMD", "HAM-D Total", "continuous_efficacy",
             (r"ham-?d\b", r"hamd-?\d*", r"hamilton\s+(rating\s+scale\s+for\s+)?depression", r"\bhdrs\b", r"\bqids\b"), True, "depressive_symptoms"),
    ScaleDef("YMRS", "YMRS Total", "continuous_efficacy", (r"ymrs", r"young\s+mania"), True, "manic_symptoms"),
    ScaleDef("HAMA", "HAM-A / Anxiety", "continuous_efficacy",
             (r"ham-?a\b", r"hamilton\s+(rating\s+scale\s+for\s+)?anxiety"), True, "anxiety_symptoms"),
    # --- global ----------------------------------------------------------------
    ScaleDef("CGI_S", "CGI-Severity", "continuous_efficacy",
             (r"cgi[-\s]?(bp[-\s]?)?s\b", r"clinical\s+global\s+impressions?[-\s,]+(of\s+)?(bipolar\s+version\s*[-–]?\s*)?severity"), True, "positive_symptoms"),
    ScaleDef("CGI_I", "CGI-Improvement", "continuous_efficacy",
             (r"cgi[-\s]?i\b", r"clinical\s+global\s+impressions?[-\s,]+(of\s+)?improvement"), True, "positive_symptoms"),
    # --- function / QoL / cognition ---------------------------------------------
    ScaleDef("PSP", "PSP (Personal & Social Performance)", "continuous_efficacy",
             (r"\bpsp\b", r"personal\s+and\s+social\s+performance"), False, "psychosocial_functioning"),
    ScaleDef("SDS", "SDS (Sheehan Disability)", "continuous_efficacy", (r"\bsds\b", r"sheehan\s+disability"), True, "psychosocial_functioning"),
    ScaleDef("FAST", "FAST / Functioning", "continuous_efficacy",
             (r"functioning\s+assessment\s+short\s+test", r"\bfast\b\s+(total|score)", r"\bsofas\b", r"\bgaf\b", r"global\s+assessment\s+of\s+functioning"), True, "psychosocial_functioning"),
    ScaleDef("COGNITION", "Cognition (BACS / MCCB / CogState)", "continuous_efficacy",
             (r"\bbacs\b", r"\bmccb\b", r"cogstate", r"cognit"), False, "cognition"),
    ScaleDef("QOL", "Quality of Life (Q-LES-Q / SF-36 / EQ-5D)", "continuous_efficacy",
             (r"q-?les-?q", r"quality\s+of\s+life", r"sf-?36", r"eq-?5d", r"well[-\s]?being"), False, "quality_of_life"),
    # --- binary efficacy / maintenance -----------------------------------------
    ScaleDef("RELAPSE", "Relapse / Time to Relapse", "time_to_event",
             (r"relapse", r"recurrence", r"impending\s+relapse", r"maintenance\s+of\s+(effect|response|efficacy)"), True, "relapse_prevention"),
    ScaleDef("REMISSION", "Remission Rate", "binary_efficacy", (r"remission", r"remitter"), False, None),
    ScaleDef("RESPONSE", "Response Rate", "binary_efficacy", (r"\brespon(se|der)s?\b",), False, None),
    # --- safety ---------------------------------------------------------------------
    ScaleDef("AKATHISIA", "Akathisia (incidence / BARS)", "binary_safety",
             (r"akathisia", r"\bbars\b", r"barnes\s+akathisia"), True, "akathisia_eps", ("akathisia",)),
    ScaleDef("EPS", "EPS (excl. akathisia) / SAS / AIMS", "binary_safety",
             (r"extrapyramidal", r"\beps\b", r"parkinsonism", r"simpson[-\s]angus", r"\bsas\b", r"\baims\b", r"abnormal\s+involuntary", r"dystonia", r"dyskinesia"),
             True, "akathisia_eps",
             ("extrapyramidal disorder", "parkinsonism", "tremor", "dystonia", "dyskinesia", "muscle rigidity", "bradykinesia",
              "drooling", "cogwheel rigidity", "musculoskeletal stiffness", "salivary hypersecretion", "tardive dyskinesia")),
    ScaleDef("SOMNOLENCE", "Somnolence / Sedation", "binary_safety",
             (r"somnolen", r"sedation", r"hypersomnia", r"drowsiness"), True, "sedation",
             ("somnolence", "sedation", "hypersomnia")),
    ScaleDef("WEIGHT", "Weight gain (≥7% / kg change)", "continuous_safety",
             (r"weight", r"body\s+mass\s+index", r"\bbmi\b"), True, "weight_metabolic", ("weight increased",)),
    ScaleDef("METABOLIC", "Metabolic (glucose / lipids)", "continuous_safety",
             (r"glucose", r"hba1c", r"cholesterol", r"triglycerid", r"lipid", r"insulin", r"metabolic\s+syndrome"), True, "weight_metabolic",
             ("hyperglycaemia", "hyperglycemia", "blood glucose increased", "hypertriglyceridaemia", "blood triglycerides increased",
              "dyslipidaemia", "hypercholesterolaemia")),
    ScaleDef("PROLACTIN", "Prolactin / Sexual dysfunction", "continuous_safety",
             (r"prolactin", r"galactorr", r"sexual\s+dysfunction"), True, "prolactin",
             ("blood prolactin increased", "hyperprolactinaemia", "hyperprolactinemia", "galactorrhoea", "galactorrhea")),
    ScaleDef("ANTICHOLINERGIC", "Anticholinergic (constipation / dry mouth / urinary)", "binary_safety",
             (r"constipation", r"dry\s+mouth", r"urinary\s+retention", r"anticholinergic", r"vision\s+blurred"), True, "anticholinergic_gi",
             ("constipation", "dry mouth", "urinary retention", "vision blurred", "tachycardia")),
    ScaleDef("GI", "GI (nausea / vomiting / dyspepsia)", "binary_safety",
             (r"nausea", r"vomit", r"dyspepsia", r"diarrh", r"gastro"), True, "anticholinergic_gi",
             ("nausea", "vomiting", "dyspepsia", "diarrhoea", "diarrhea", "abdominal pain", "abdominal pain upper", "gastrooesophageal reflux disease")),
    ScaleDef("QTC", "QTc / Cardiac", "continuous_safety", (r"\bqtc?\b", r"qt\s+interval"), True, "label_safety",
             ("electrocardiogram qt prolonged",)),
    ScaleDef("MANIC_SWITCH", "Treatment-emergent mania / hypomania", "binary_safety",
             (r"treatment[-\s]emergent\s+(mania|hypomania)", r"manic\s+switch", r"switch\s+to\s+mania"), True, "manic_switch",
             ("mania", "hypomania")),
    ScaleDef("SUICIDALITY", "Suicidality (C-SSRS)", "binary_safety",
             (r"c-?ssrs", r"columbia\s+suicide", r"suicid"), True, "label_safety", ("suicidal ideation", "suicide attempt")),
    ScaleDef("DISCONTINUATION_AE", "Discontinuation due to AEs", "binary_safety",
             (r"discontinu\w*\s+(due\s+to|because\s+of)\s+(an?\s+)?(ae|adverse)", r"withdraw\w*\s+due\s+to\s+(an?\s+)?adverse"), True, "label_safety"),
)

SCALE_BY_KEY = {s.key: s for s in SCALES}

# Scales whose patterns are broad and must not win over a more specific one.
_SCALE_ORDER = [s.key for s in SCALES]


def classify_scale(*texts: str | None) -> str | None:
    """Map free text (outcome-measure title, table caption, sentence) to a scale key."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    # discontinuation-due-to-AE and relapse wording frequently also mention other scales; check first.
    for key in ("DISCONTINUATION_AE", "MANIC_SWITCH"):
        if any(re.search(p, blob) for p in SCALE_BY_KEY[key].patterns):
            return key
    # response / remission definitions usually cite the underlying scale ("≥30% PANSS reduction")
    for key in ("REMISSION", "RESPONSE", "RELAPSE"):
        if any(re.search(p, blob) for p in SCALE_BY_KEY[key].patterns):
            return key
    for key in _SCALE_ORDER:
        if key in ("DISCONTINUATION_AE", "MANIC_SWITCH", "REMISSION", "RESPONSE", "RELAPSE"):
            continue
        if any(re.search(p, blob) for p in SCALE_BY_KEY[key].patterns):
            return key
    return None


def classify_ae_term(term: str) -> str | None:
    t = term.strip().lower()
    for s in SCALES:
        if s.ae_terms and t in s.ae_terms:
            return s.key
    return None


# ---------------------------------------------------------------------------
# Market Value Driver taxonomy (Module 1)
# ---------------------------------------------------------------------------

# kind: effect (continuous efficacy, Cohen's d) | risk (AE incidence, risk difference)
#       | structural (label-derived ordinal) | onset (earliest separation week)


@dataclass(frozen=True)
class MvdDef:
    key: str
    name: str
    category: str
    kind: str
    rows: tuple[str, ...]
    lexicon: tuple[str, ...]
    description: str = ""


MVDS: tuple[MvdDef, ...] = (
    # EFFICACY
    MvdDef("positive_symptoms", "Positive symptom control", "EFFICACY", "effect", ("PANSS_TOTAL", "PANSS_POSITIVE", "CGI_S"),
           ("positive symptom", "psychotic symptom", "hallucination", "delusion", "psychosis control", "total symptom")),
    MvdDef("negative_symptoms", "Negative symptoms / anhedonia", "EFFICACY", "effect", ("PANSS_NEG_MARDER", "PANSS_NEGATIVE", "NEG_OTHER"),
           ("negative symptom", "anhedonia", "avolition", "apathy", "blunted affect", "amotivation", "social withdrawal")),
    MvdDef("depressive_symptoms", "Depressive symptom improvement", "EFFICACY", "effect", ("MADRS", "HAMD"),
           ("depressive symptom", "depression improvement", "mood improvement", "antidepressant effect")),
    MvdDef("manic_symptoms", "Manic symptom control", "EFFICACY", "effect", ("YMRS",),
           ("manic symptom", "mania control", "antimanic")),
    MvdDef("anxiety_symptoms", "Anxiety symptom relief", "EFFICACY", "effect", ("HAMA",), ("anxiety", "anxious distress")),
    MvdDef("rapid_onset", "Rapid onset of effect", "EFFICACY", "onset", (),
           ("onset", "speed of response", "rapid", "time to response", "fast-acting", "early improvement")),
    MvdDef("relapse_prevention", "Relapse prevention / maintenance", "EFFICACY", "effect", ("RELAPSE",),
           ("relapse", "recurrence", "maintenance", "long-term efficacy", "durability", "hospitali")),
    # FUNCTION / QOL
    MvdDef("psychosocial_functioning", "Psychosocial functioning (PSP / SDS / FAST)", "FUNCTION_QOL", "effect", ("PSP", "SDS", "FAST"),
           ("functioning", "functional", "social function", "work function", "psychosocial", "disability", "recovery")),
    MvdDef("cognition", "Cognitive restoration", "FUNCTION_QOL", "effect", ("COGNITION",),
           ("cognition", "cognitive", "memory", "attention", "executive function")),
    MvdDef("quality_of_life", "Quality of life", "FUNCTION_QOL", "effect", ("QOL",),
           ("quality of life", "qol", "wellbeing", "well-being", "life satisfaction")),
    # SAFETY / TOLERABILITY
    MvdDef("akathisia_eps", "Low akathisia / EPS", "SAFETY_TOLERABILITY", "risk", ("AKATHISIA", "EPS"),
           ("akathisia", "extrapyramidal", "eps", "movement disorder", "tardive", "restlessness", "parkinsonism")),
    MvdDef("weight_metabolic", "Weight & cardiometabolic neutrality", "SAFETY_TOLERABILITY", "risk", ("WEIGHT", "METABOLIC"),
           ("weight", "metabolic", "cardiometabolic", "diabetes", "glucose", "lipid", "obesity")),
    MvdDef("sedation", "Low somnolence / sedation", "SAFETY_TOLERABILITY", "risk", ("SOMNOLENCE",),
           ("sedation", "somnolence", "drowsiness", "sleepiness", "fatigue")),
    MvdDef("prolactin", "Prolactin neutrality / sexual function", "SAFETY_TOLERABILITY", "risk", ("PROLACTIN",),
           ("prolactin", "sexual dysfunction", "sexual side effect", "galactorrhea")),
    MvdDef("anticholinergic_gi", "Low anticholinergic / GI burden", "SAFETY_TOLERABILITY", "risk", ("ANTICHOLINERGIC", "GI"),
           ("anticholinergic", "constipation", "dry mouth", "nausea", "vomiting", "gastrointestinal", "urinary")),
    MvdDef("manic_switch", "Low treatment-emergent mania risk", "SAFETY_TOLERABILITY", "risk", ("MANIC_SWITCH",),
           ("manic switch", "treatment-emergent mania", "hypomania", "mood destabili")),
    MvdDef("label_safety", "Label safety profile (boxed warning, contraindications)", "SAFETY_TOLERABILITY", "structural", ("QTC", "SUICIDALITY", "DISCONTINUATION_AE"),
           ("boxed warning", "black box", "safety profile", "overall safety", "tolerability", "side effect", "adverse effect", "discontinuation")),
    # EXECUTION / DOSING
    MvdDef("no_titration", "No titration required", "EXECUTION_DOSING", "structural", (),
           ("titration", "starting dose", "effective dose from day 1", "dose adjustment")),
    MvdDef("ddi_flexibility", "No / limited CYP3A4 & CYP2D6 restrictions", "EXECUTION_DOSING", "structural", (),
           ("drug interaction", "drug-drug", "cyp", "concomitant medication", "polypharmacy")),
    MvdDef("dosing_convenience", "Once-daily, food-independent / LAI convenience", "EXECUTION_DOSING", "structural", (),
           ("once daily", "once-daily", "dosing frequency", "administration", "long-acting", "injectable", "lai", "food", "convenience", "route")),
)

MVD_BY_KEY = {m.key: m for m in MVDS}

INDICATION_MVD_PROFILES: dict[str, tuple[str, ...]] = {
    "schizophrenia": ("positive_symptoms", "negative_symptoms", "rapid_onset", "relapse_prevention", "psychosocial_functioning",
                      "cognition", "akathisia_eps", "weight_metabolic", "sedation", "prolactin", "anticholinergic_gi",
                      "label_safety", "no_titration", "ddi_flexibility", "dosing_convenience"),
    "schizoaffective": ("positive_symptoms", "negative_symptoms", "depressive_symptoms", "relapse_prevention", "psychosocial_functioning",
                        "akathisia_eps", "weight_metabolic", "sedation", "prolactin", "label_safety", "no_titration", "ddi_flexibility",
                        "dosing_convenience"),
    "bipolar_depression": ("depressive_symptoms", "rapid_onset", "relapse_prevention", "anxiety_symptoms", "psychosocial_functioning",
                           "quality_of_life", "akathisia_eps", "weight_metabolic", "sedation", "manic_switch", "label_safety",
                           "no_titration", "ddi_flexibility", "dosing_convenience"),
    "bipolar_mania": ("manic_symptoms", "rapid_onset", "relapse_prevention", "depressive_symptoms", "psychosocial_functioning",
                      "akathisia_eps", "weight_metabolic", "sedation", "prolactin", "label_safety", "no_titration", "ddi_flexibility",
                      "dosing_convenience"),
    "mdd": ("depressive_symptoms", "rapid_onset", "relapse_prevention", "anxiety_symptoms", "psychosocial_functioning", "quality_of_life",
            "akathisia_eps", "weight_metabolic", "sedation", "label_safety", "no_titration", "ddi_flexibility", "dosing_convenience"),
}
DEFAULT_MVD_PROFILE: tuple[str, ...] = ("positive_symptoms", "depressive_symptoms", "rapid_onset", "relapse_prevention",
                                        "psychosocial_functioning", "akathisia_eps", "weight_metabolic", "sedation", "label_safety",
                                        "no_titration", "ddi_flexibility", "dosing_convenience")


def mvd_profile(indication_key: str | None) -> tuple[str, ...]:
    return INDICATION_MVD_PROFILES.get(indication_key or "", DEFAULT_MVD_PROFILE)


def map_text_to_mvd(text: str, indication_key: str | None) -> str | None:
    """Map a free-text attribute (e.g. a DCE attribute label) onto the indication's MVD profile."""
    t = text.lower()
    profile = mvd_profile(indication_key)
    best: tuple[int, str] | None = None
    for key in profile:
        m = MVD_BY_KEY[key]
        hits = sum(1 for term in m.lexicon if term in t)
        if hits and (best is None or hits > best[0]):
            best = (hits, key)
    if best:
        return best[1]
    # generic efficacy wording → the indication's primary efficacy driver
    if re.search(r"\b(efficacy|effectiveness|symptom (improvement|reduction|control)|clinical response)\b", t):
        ind = INDICATION_BY_KEY.get(indication_key or "")
        return ind.primary_efficacy_mvd if ind else profile[0]
    return None


# ---------------------------------------------------------------------------
# HCP personas (Module 1 user override). These multipliers are *user-adjustable
# heuristics*, explicitly labelled as such in the UI — not extracted evidence.
# ---------------------------------------------------------------------------

PERSONAS: dict[str, dict] = {
    "evidence": {
        "label": "Evidence-derived (no persona adjustment)",
        "multipliers": {},
    },
    "inpatient_acute": {
        "label": "Inpatient Acute Psychiatrist",
        "multipliers": {
            "positive_symptoms": 1.25, "manic_symptoms": 1.25, "rapid_onset": 1.35, "no_titration": 1.3,
            "sedation": 0.85, "weight_metabolic": 0.8, "psychosocial_functioning": 0.75, "cognition": 0.75,
            "quality_of_life": 0.75, "dosing_convenience": 0.8, "relapse_prevention": 0.85,
        },
    },
    "outpatient_community": {
        "label": "Outpatient Community Psychiatrist",
        "multipliers": {
            "weight_metabolic": 1.25, "sedation": 1.2, "psychosocial_functioning": 1.25, "relapse_prevention": 1.2,
            "dosing_convenience": 1.2, "akathisia_eps": 1.1, "prolactin": 1.1, "rapid_onset": 0.85, "cognition": 1.1,
            "quality_of_life": 1.15, "ddi_flexibility": 1.1,
        },
    },
    "primary_care": {
        "label": "Primary Care / Collaborative Care",
        "multipliers": {
            "weight_metabolic": 1.3, "ddi_flexibility": 1.35, "no_titration": 1.25, "dosing_convenience": 1.2,
            "label_safety": 1.2, "sedation": 1.15, "negative_symptoms": 0.8, "cognition": 0.8,
        },
    },
}


# ---------------------------------------------------------------------------
# Asset synonyms (development codes). Seed list; extend per ingest request.
# ---------------------------------------------------------------------------

ASSET_SYNONYM_SEEDS: dict[str, list[str]] = {
    "cariprazine": ["Vraylar", "Reagila", "RGH-188"],
    "lumateperone": ["Caplyta", "ITI-007", "lumateperone tosylate"],
    "xanomeline and trospium": ["Cobenfy", "KarXT", "xanomeline-trospium", "xanomeline/trospium", "xanomeline and trospium chloride"],
    "brexpiprazole": ["Rexulti", "OPC-34712"],
    "aripiprazole": ["Abilify", "Abilify Maintena", "Aristada"],
    "lurasidone": ["Latuda", "SM-13496"],
    "quetiapine": ["Seroquel", "Seroquel XR"],
    "olanzapine and samidorphan": ["Lybalvi", "ALKS 3831"],
    "olanzapine": ["Zyprexa"],
    "risperidone": ["Risperdal", "Uzedy", "Perseris"],
    "paliperidone": ["Invega", "Invega Sustenna"],
    "emraclidine": ["CVL-231"],
    "ulotaront": ["SEP-363856"],
}


def asset_terms(generic: str, brand: str | None = None, extra: list[str] | None = None) -> list[str]:
    terms: list[str] = [generic]
    if brand:
        terms.append(brand)
    terms += ASSET_SYNONYM_SEEDS.get(generic.lower(), [])
    terms += extra or []
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        k = t.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(t.strip())
    return out


def text_mentions_asset(text: str | None, terms: list[str]) -> bool:
    if not text:
        return False
    low = text.lower()
    for t in terms:
        tl = t.lower()
        if " and " in tl:
            # combination products: require each component
            parts = [p.strip() for p in tl.split(" and ") if p.strip()]
            if all(re.search(rf"\b{re.escape(p)}", low) for p in parts):
                return True
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(tl)}(?![a-z0-9])", low):
            return True
    return False


@dataclass
class ColumnKey:
    indication_key: str
    population: str = "adult"
    timepoint: str = "acute"
    adjunctive: bool = False
    extra: dict = field(default_factory=dict)

    def encode(self) -> str:
        pop = self.population + ("+adjunctive" if self.adjunctive else "")
        return f"{self.indication_key}|{pop}|{self.timepoint}"

    @staticmethod
    def decode(s: str) -> ColumnKey:
        ind, pop, tp = (s.split("|") + ["adult", "acute"])[:3]
        adj = pop.endswith("+adjunctive")
        return ColumnKey(ind, pop.replace("+adjunctive", ""), tp, adj)


def timepoint_bucket(weeks: float | None) -> str:
    if weeks is None:
        return "acute"
    if weeks <= 8.5:
        return "acute"
    if weeks <= 26:
        return "intermediate"
    return "maintenance"


TIMEPOINT_LABELS = {"acute": "Acute (≤8 wk)", "intermediate": "9–26 wk", "maintenance": "Maintenance (>26 wk)"}
POPULATION_LABELS = {"adult": "Adult", "pediatric": "Pediatric/Adolescent", "elderly": "Older adult"}


def column_label(col: str) -> str:
    ck = ColumnKey.decode(col)
    parts = [indication_label(ck.indication_key)]
    if ck.population != "adult":
        parts.append(POPULATION_LABELS.get(ck.population, ck.population))
    if ck.adjunctive:
        parts.append("Adjunctive")
    parts.append(TIMEPOINT_LABELS.get(ck.timepoint, ck.timepoint))
    return " · ".join(parts)
