"""Deterministic USPI / SPL parsers (spec §3.1).

Input: SPL XML (DailyMed) or openFDA label JSON. Output: per-LOINC section text +
tables, and structured extractions — CYP450 interaction rules (dose adjustment vs
avoid vs contraindication), titration / food / hepatic / renal rules, boxed
warning topics, adverse-reaction incidence tables and §14 efficacy tables.
Every structured item carries the verbatim sentence or table row it came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from ..taxonomy import classify_indication, classify_scale
from ..textutil import (
    clean_ws,
    grid_from_table_element,
    merge_header_rows,
    parse_number,
    parse_weeks,
    split_clauses,
    split_sentences,
    strip_html,
    table_to_text_rows,
    tables_from_html,
)

V3 = "urn:hl7-org:v3"
NS = {"v3": V3}

TARGET_LOINC: dict[str, str] = {
    "34066-1": "Boxed Warning",
    "34067-9": "Indications and Usage",
    "34068-7": "Dosage and Administration",
    "34070-3": "Contraindications",
    "43685-7": "Warnings and Precautions",
    "34084-4": "Adverse Reactions",
    "34073-7": "Drug Interactions",
    "34092-7": "Clinical Studies",
    # auxiliary sections used by the parsers
    "43684-0": "Use in Specific Populations",
    "43679-0": "Mechanism of Action",
}

# ---------------------------------------------------------------------------
# SPL XML
# ---------------------------------------------------------------------------


@dataclass
class SplSection:
    loinc: str
    title: str
    text: str
    tables: list[dict[str, Any]] = field(default_factory=list)
    subsections: list[str] = field(default_factory=list)


@dataclass
class SplDocument:
    set_id: str | None
    version: str | None
    effective_date: str | None
    title: str | None
    labeler: str | None
    initial_us_approval_year: int | None
    sections: dict[str, SplSection]


_BLOCK_TAGS = {f"{{{V3}}}{t}" for t in ("paragraph", "title", "item", "tr", "caption", "list", "table", "br")}


def _block_text(el) -> str:
    """itertext with line breaks at block elements and ' | ' between table cells; skips <excerpt> (highlights)."""
    out: list[str] = []

    def walk(node):
        if not isinstance(node.tag, str):
            return
        if node.tag == f"{{{V3}}}excerpt":
            if node.tail:
                out.append(node.tail)
            return
        if node.text:
            out.append(node.text)
        for ch in node:
            walk(ch)
        if node.tag in (f"{{{V3}}}td", f"{{{V3}}}th"):
            out.append(" | ")
        if node.tag in _BLOCK_TAGS:
            out.append("\n")
        if node.tail:
            out.append(node.tail)

    walk(el)
    txt = "".join(out)
    txt = re.sub(r"[ \t]*\|\s*\n", "\n", txt)
    return clean_ws(txt)


def _section_tables(sec, context_title: str) -> list[dict[str, Any]]:
    tables = []
    for t in sec.iter(f"{{{V3}}}table"):
        # skip tables inside highlights
        if any(a.tag == f"{{{V3}}}excerpt" for a in t.iterancestors()):
            continue
        grid = grid_from_table_element(t, V3)
        # nearest enclosing section title (e.g. "14.1 Schizophrenia") gives indication context
        sub_title = ""
        for anc in t.iterancestors(f"{{{V3}}}section"):
            te = anc.find("v3:title", NS)
            if te is not None:
                sub_title = clean_ws("".join(te.itertext()))
                break
        grid["context"] = sub_title or context_title
        tables.append(grid)
    return tables


def parse_spl_xml(xml_text: str) -> SplDocument:
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False, no_network=True)
    root = etree.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text, parser)

    def first_attr(path: str, attr: str) -> str | None:
        el = root.find(path, NS)
        return el.get(attr) if el is not None else None

    set_id = first_attr("v3:setId", "root")
    version = first_attr("v3:versionNumber", "value")
    eff = first_attr("v3:effectiveTime", "value")
    eff_date = f"{eff[:4]}-{eff[4:6]}-{eff[6:8]}" if eff and len(eff) >= 8 else None
    title_el = root.find("v3:title", NS)
    title = clean_ws(" ".join(title_el.itertext())) if title_el is not None else None
    lab_el = root.find(".//v3:author//v3:representedOrganization/v3:name", NS)
    labeler = clean_ws(lab_el.text) if lab_el is not None and lab_el.text else None
    all_text = " ".join(root.itertext())
    m = re.search(r"Initial\s+U\.?\s*S\.?\s+Approval\s*:?\s*(\d{4})", all_text)
    approval_year = int(m.group(1)) if m else None

    sections: dict[str, SplSection] = {}
    for sec in root.iter(f"{{{V3}}}section"):
        code_el = sec.find("v3:code", NS)
        if code_el is None:
            continue
        loinc = code_el.get("code")
        if loinc not in TARGET_LOINC or loinc in sections:
            continue
        te = sec.find("v3:title", NS)
        stitle = clean_ws(" ".join(te.itertext())) if te is not None else TARGET_LOINC[loinc]
        subs = []
        for sub in sec.iterfind(".//v3:section/v3:title", NS):
            st = clean_ws(" ".join(sub.itertext()))
            if st:
                subs.append(st)
        sections[loinc] = SplSection(loinc, stitle, _block_text(sec), _section_tables(sec, stitle), subs)
    return SplDocument(set_id, version, eff_date, title, labeler, approval_year, sections)


def sections_from_openfda(rec: dict[str, Any]) -> dict[str, SplSection]:
    from ..clients.openfda import OPENFDA_FIELD_TO_LOINC

    out: dict[str, SplSection] = {}
    for fld, loinc in {**OPENFDA_FIELD_TO_LOINC, "use_in_specific_populations": "43684-0", "mechanism_of_action": "43679-0"}.items():
        if fld not in rec:
            continue
        text = "\n".join(rec.get(fld) or [])
        tables: list[dict[str, Any]] = []
        for frag in rec.get(f"{fld}_table") or []:
            for t in tables_from_html(frag):
                t["context"] = TARGET_LOINC.get(loinc, fld)
                tables.append(t)
        out[loinc] = SplSection(loinc, TARGET_LOINC.get(loinc, fld), clean_ws(strip_html(text) if "<" in text else text), tables)
    return out


# ---------------------------------------------------------------------------
# CYP450 / transporter interaction rules (USPI §7 + §2 + §4)
# ---------------------------------------------------------------------------

ENZYME_RE = re.compile(
    r"\b(CYP\s?(?:3A4|3A5|3A|2D6|1A2|2C9|2C19|2B6|2C8)|P[-\s]?gp|P-glycoprotein|UGT(?:\d[A-Z0-9]*)?|BCRP|OATP\d?[A-Z0-9]*|OCT\d?|MATE\d?(?:-?K)?)\b", re.I
)
STRENGTH_RE = re.compile(r"\b(strong|moderate|weak|potent)\b(?:\s*(?:or|and|/|to|-)\s*(strong|moderate|weak))?", re.I)
ROLE_AFTER_RE = re.compile(r"^[\s\w/,-]{0,40}?\b(inhibitor|inducer|substrate)s?\b", re.I)
ROLE_VERB_RE = re.compile(r"\b(inhibit|induc)\w*\b", re.I)

ACTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("CONTRAINDICATED", re.compile(r"contraindicat", re.I)),
    ("AVOID", re.compile(r"\bavoid\b|not\s+recommended|should\s+not\s+be\s+(used|administered|co-?administered|combined|taken)|do\s+not\s+(use|co-?administer|combine)", re.I)),
    ("NO_ADJUSTMENT", re.compile(r"no\s+dos(e|age)\s+adjustment|(not|n't)\s+(necessary|required|needed)\s+to\s+adjust|adjustment\s+is\s+not\s+(needed|necessary|required)|no\s+clinically\s+(significant|relevant|meaningful)", re.I)),
    ("DOSE_ADJUSTMENT", re.compile(r"\breduc|\bdecreas\w*\s+(the\s+)?dos|lower\s+(the\s+)?dos|\badjust|one[-\s]?half|\bhalf\b|recommended\s+dos(e|age)\s+is|every\s+other\s+day|dos(e|age)\s+(of|to)\s+\d|\d+(?:\.\d+)?\s?mg\b|increase\s+(the\s+)?dos", re.I)),
    ("MONITOR", re.compile(r"\bmonitor", re.I)),
]
ACTION_RANK = {"CONTRAINDICATED": 0, "AVOID": 1, "DOSE_ADJUSTMENT": 2, "MONITOR": 3, "NO_ADJUSTMENT": 4, "MENTION_ONLY": 5}
ACTION_SCORE = {"CONTRAINDICATED": 0.0, "AVOID": 1.0, "DOSE_ADJUSTMENT": 2.0, "MONITOR": 2.5, "NO_ADJUSTMENT": 3.0, "MENTION_ONLY": 3.0}
DOSE_RE = re.compile(r"(\d+(?:\.\d+)?\s?mg(?:\s?/\s?\d+(?:\.\d+)?\s?mg)?(?:\s?/\s?day)?(?:\s+(?:once|twice)\s+daily|\s+every\s+other\s+day|\s+daily)?)", re.I)
REF_RE = re.compile(r"\(\s*(\d+(?:\.\d+)?(?:\s*,\s*\d+(?:\.\d+)?)*)\s*\)")


def _norm_enzyme(e: str) -> str:
    e = e.upper().replace(" ", "")
    if e.startswith("P") and ("GP" in e or "GLYCO" in e):
        return "P-gp"
    if e == "CYP3A":
        return "CYP3A4"
    return e


def _classify_action(clause: str, section_loinc: str) -> str:
    if section_loinc == "34070-3":
        return "CONTRAINDICATED"
    for name, pat in ACTION_PATTERNS:
        if pat.search(clause):
            return name
    return "MENTION_ONLY"


def parse_cyp_interactions(sections: dict[str, SplSection], drug_names: list[str] | None = None) -> dict[str, Any]:
    """Return {'rules': [...], 'summary': {...}, 'ddi_score': float}.

    A rule = enzyme × modulator role × strength × action, with the verbatim clause.
    Sections scanned: 34073-7 (DI), 34068-7 (D&A), 34070-3 (contraindications), 43684-0 (populations).
    """
    rules: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for loinc in ("34073-7", "34068-7", "34070-3", "43684-0"):
        sec = sections.get(loinc)
        if not sec:
            continue
        units: list[str] = []
        for sent in split_sentences(sec.text):
            units += split_clauses(sent)
        for tbl in sec.tables:
            units += table_to_text_rows(tbl)
        prev_context: list[tuple[str, str, list[str]]] = []
        for unit in units:
            enzymes = list(ENZYME_RE.finditer(unit))
            action = _classify_action(unit, loinc)
            found: list[tuple[str, str, list[str]]] = []
            for m in enzymes:
                enz = _norm_enzyme(m.group(1))
                after = unit[m.end():]
                rm = ROLE_AFTER_RE.match(after)
                role = rm.group(1).lower() if rm else None
                if role is None:
                    vm = ROLE_VERB_RE.search(unit[max(0, m.start() - 40): m.start()])
                    if vm:
                        role = "inhibitor" if vm.group(1).lower().startswith("inhibit") else "inducer"
                if role is None:
                    continue
                before = unit[max(0, m.start() - 40): m.start()]
                strengths: list[str] = []
                sm = None
                for sm in STRENGTH_RE.finditer(before):
                    pass
                if sm:
                    strengths = [g.lower().replace("potent", "strong") for g in sm.groups() if g]
                found.append((enz, role, strengths or ["unspecified"]))
            # A bare "Intervention: reduce dosage" row following a heading row inherits the heading's enzyme.
            if (not enzymes and action != "MENTION_ONLY" and prev_context
                    and re.match(r"\s*(intervention|recommendation)", unit, re.I)):
                found = prev_context
            if found:
                prev_context = found
            elif enzymes:
                prev_context = []  # a different (unresolved) enzyme heading breaks inheritance
            for enz, role, strengths in found:
                for st in strengths:
                    dose_m = DOSE_RE.search(unit) if action == "DOSE_ADJUSTMENT" else None
                    refs = REF_RE.findall(unit)
                    key = (enz, role, st, action, loinc)
                    if key in seen:
                        continue
                    seen.add(key)
                    rules.append({
                        "enzyme": enz, "modulator": role, "strength": st, "action": action,
                        "dose_instruction": dose_m.group(1) if dose_m else None,
                        "section_loinc": loinc, "section_refs": refs, "verbatim": unit.strip()[:600],
                    })

    summary: dict[str, dict[str, Any]] = {}
    for r in rules:
        k = f"{r['enzyme']}|{r['modulator']}|{r['strength']}"
        cur = summary.get(k)
        if cur is None or ACTION_RANK[r["action"]] < ACTION_RANK[cur["action"]]:
            summary[k] = {**r, "label": f"{r['strength'].title()} {r['enzyme']} {r['modulator']}s" if r["strength"] != "unspecified" else f"{r['enzyme']} {r['modulator']}s"}
    # inherit 'unspecified' strength rules into strong/moderate if those are missing
    for enz in {r["enzyme"] for r in rules if r["enzyme"].startswith("CYP")}:
        for role in ("inhibitor", "inducer"):
            uns = summary.get(f"{enz}|{role}|unspecified")
            if uns:
                for st in ("strong", "moderate"):
                    summary.setdefault(f"{enz}|{role}|{st}", {**uns, "strength": st, "inherited": True,
                                                              "label": f"{st.title()} {enz} {role}s"})

    def action_for(enz: str, role: str, st: str) -> str:
        r = summary.get(f"{enz}|{role}|{st}")
        return r["action"] if r else "NOT_MENTIONED"

    key_classes = {
        "strong_cyp3a4_inhibitor": action_for("CYP3A4", "inhibitor", "strong"),
        "moderate_cyp3a4_inhibitor": action_for("CYP3A4", "inhibitor", "moderate"),
        "cyp3a4_inducer": min((action_for("CYP3A4", "inducer", s) for s in ("strong", "moderate", "unspecified")),
                              key=lambda a: ACTION_RANK.get(a, 9)),
        "strong_cyp2d6_inhibitor": action_for("CYP2D6", "inhibitor", "strong"),
    }
    scores = [ACTION_SCORE.get(a, 3.0) for a in key_classes.values()]
    return {
        "rules": rules,
        "summary": sorted(summary.values(), key=lambda r: (r["enzyme"], r["modulator"], r["strength"])),
        "key_classes": key_classes,
        "ddi_score": round(sum(scores) / len(scores), 3),  # 0 (contraindicated everywhere) .. 3 (no restriction)
    }


# ---------------------------------------------------------------------------
# Dosing (§2) — titration, frequency, food, hepatic / renal
# ---------------------------------------------------------------------------

NO_TITRATION_RE = re.compile(r"(no|without)\s+(dose\s+|dosage\s+)?titration|titration\s+is\s+not\s+(required|necessary|needed)|does\s+not\s+require\s+(dose\s+)?titration|no\s+need\s+(for|to)\s+titrat", re.I)
TITRATION_RE = re.compile(r"titrat|start(ing)?\s+dos(e|age)|initial\s+dos(e|age)|increas\w*\s+(the\s+)?(dose|dosage)?\s*(to|by|in)\b|on\s+day\s+\d|day\s+\d+\s+(through|to|and)|increments?\s+of", re.I)


def _impairment_rule(sentences: list[str], organ: str) -> dict[str, Any]:
    hits = [s for s in sentences if re.search(rf"{organ}\w*\s+impairment|{organ}\s+(disease|insufficiency)", s, re.I)]
    best: dict[str, Any] | None = None
    for s in hits:
        if re.search(r"contraindicat", s, re.I):
            a = "CONTRAINDICATED"
        elif re.search(r"not\s+recommended|\bavoid\b|should\s+not", s, re.I):
            a = "AVOID"
        elif re.search(r"no\s+dos(e|age)\s+adjustment|same\s+as\s+(those\s+)?with\s+normal|not\s+(necessary|required)", s, re.I):
            a = "NO_ADJUSTMENT"
        elif re.search(r"reduc|lower|decreas|recommended\s+dos(e|age)\s+(is|in)|\d+(?:\.\d+)?\s?mg", s, re.I):
            a = "DOSE_ADJUSTMENT"
        else:
            continue
        severity = sorted({x.lower() for x in re.findall(r"\b(mild|moderate|severe)\b", s, re.I)}) or ["unspecified"]
        cand = {"action": a, "severity": severity, "child_pugh": sorted(set(re.findall(r"Child[-\s]Pugh\s*(?:class\s*)?([ABC])", s, re.I))),
                "verbatim": s[:500]}
        if best is None or ACTION_RANK[a] < ACTION_RANK[best["action"]]:
            best = cand
    return best or {"action": "NOT_MENTIONED", "severity": [], "child_pugh": [], "verbatim": None}


def parse_dosing(sections: dict[str, SplSection]) -> dict[str, Any]:
    da = sections.get("34068-7")
    text = da.text if da else ""
    sents = split_sentences(text)
    pop_sents = split_sentences(sections["43684-0"].text) if "43684-0" in sections else []
    contra_sents = split_sentences(sections["34070-3"].text) if "34070-3" in sections else []

    titration: dict[str, Any] = {"required": None, "verbatim": None}
    for s in sents:
        if NO_TITRATION_RE.search(s):
            titration = {"required": False, "verbatim": s[:400]}
            break
    if titration["required"] is None:
        for s in sents:
            if TITRATION_RE.search(s) and not re.search(r"\bhepatic|renal|cyp|inhibitor|inducer\b", s, re.I):
                titration = {"required": True, "verbatim": s[:400]}
                break

    freq = None
    freq_verbatim = None
    for s in sents:
        if re.search(r"every\s+(\d+|two|four|eight)\s+(weeks|months)|once\s+monthly|long[-\s]acting\s+injectable", s, re.I):
            freq, freq_verbatim = "long_acting_injectable", s
            break
    if freq is None:
        for s in sents:
            if re.search(r"twice\s+daily|two\s+times\s+(a|per)\s+day|\bb\.?i\.?d\b", s, re.I):
                freq, freq_verbatim = "twice_daily", s
                break
            if re.search(r"once\s+daily|once\s+a\s+day|\bq\.?d\b", s, re.I) and freq is None:
                freq, freq_verbatim = "once_daily", s
    food = {"requirement": None, "verbatim": None}
    for s in sents:
        if re.search(r"with\s+or\s+without\s+(food|meals?)", s, re.I):
            if food["requirement"] is None:
                food = {"requirement": "none", "verbatim": s[:400]}
            continue
        if re.search(r"empty\s+stomach|at\s+least\s+(one|1|two|2)\s+hours?\s+(before|after)\s+(a\s+)?meal|\bwithout\s+food\b", s, re.I):
            food = {"requirement": "fasting_required", "verbatim": s[:400]}
            break
        if re.search(r"\bwith\s+(food|a\s+meal)\b|at\s+least\s+\d+\s+(calories|kcal)", s, re.I):
            food = {"requirement": "with_food_required", "verbatim": s[:400]}
            break

    start = None
    m = re.search(r"(starting|initial)\s+dos(e|age)[^.]{0,80}?(\d+(?:\.\d+)?\s?mg[^.;]{0,40})", text, re.I)
    if m:
        start = clean_ws(m.group(3))
    rec = None
    m = re.search(r"recommended\s+dos(e|age)(\s+range)?\s+(of\s+\w+\s+)?is\s+([^.;]{3,90})", text, re.I)
    if m:
        rec = clean_ws(m.group(4))
    mx = None
    m = re.search(r"maximum\s+(recommended\s+)?dos(e|age)[^.]{0,40}?(\d+(?:\.\d+)?\s?mg[^.;]{0,30})", text, re.I)
    if m:
        mx = clean_ws(m.group(3))

    all_s = sents + pop_sents + contra_sents
    hepatic = _impairment_rule(all_s, "hepatic")
    renal = _impairment_rule(all_s, "renal")

    # structural sub-scores (0..3, higher = fewer constraints)
    titration_score = {False: 3.0, True: 1.0, None: 2.0}[titration["required"]]
    freq_score = {"once_daily": 3.0, "long_acting_injectable": 3.0, "twice_daily": 1.5, None: 2.0}.get(freq, 2.0)
    food_score = {"none": 3.0, None: 2.5, "with_food_required": 1.5, "fasting_required": 1.0}.get(food["requirement"], 2.5)
    return {
        "titration": titration,
        "starting_dose": start,
        "recommended_dose": rec,
        "max_dose": mx,
        "frequency": freq,
        "frequency_verbatim": freq_verbatim[:400] if freq_verbatim else None,
        "food": food,
        "hepatic": hepatic,
        "renal": renal,
        "scores": {
            "titration": titration_score,
            "dosing_convenience": round((freq_score + food_score) / 2, 3),
            "hepatic": ACTION_SCORE.get(hepatic["action"], 3.0),
            "renal": ACTION_SCORE.get(renal["action"], 3.0),
        },
    }


# ---------------------------------------------------------------------------
# Boxed warning, contraindications, indications, monitoring
# ---------------------------------------------------------------------------

BOXED_TOPICS = {
    "dementia_related_psychosis_mortality": r"increased\s+mortality\s+in\s+elderly\s+patients\s+with\s+dementia",
    "suicidal_thoughts_behaviors": r"suicidal\s+thoughts?\s+and\s+behaviou?rs?|suicidality",
    "neuroleptic_malignant_syndrome": r"neuroleptic\s+malignant",
    "qt_prolongation": r"qt\s+prolongation|torsades",
    "respiratory_depression": r"respiratory\s+depression",
    "abuse_misuse": r"abuse,?\s+misuse|addiction",
    "sedation_delirium": r"post[-\s]injection\s+delirium|sedation\s+syndrome",
}


def parse_boxed_warning(sections: dict[str, SplSection]) -> dict[str, Any]:
    sec = sections.get("34066-1")
    if not sec or not sec.text.strip():
        return {"present": False, "topics": [], "verbatim": None}
    topics = [k for k, p in BOXED_TOPICS.items() if re.search(p, sec.text, re.I)]
    first = next((s for s in split_sentences(sec.text) if len(s) > 30), sec.text[:300])
    return {"present": True, "topics": topics, "verbatim": first[:500]}


def parse_contraindications(sections: dict[str, SplSection]) -> dict[str, Any]:
    sec = sections.get("34070-3")
    if not sec:
        return {"items": [], "non_hypersensitivity_count": 0}
    items = []
    for s in split_sentences(sec.text):
        if re.match(r"^\s*(4\s+)?contraindications\s*$", s, re.I):
            continue
        if len(s) < 12:
            continue
        hyper = bool(re.search(r"hypersensitivity|allerg", s, re.I))
        items.append({"verbatim": s[:400], "hypersensitivity": hyper})
    non_h = sum(1 for i in items if not i["hypersensitivity"])
    return {"items": items, "non_hypersensitivity_count": non_h}


def parse_indications(sections: dict[str, SplSection]) -> list[dict[str, Any]]:
    sec = sections.get("34067-9")
    if not sec:
        return []
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, bool]] = set()
    for s in split_sentences(sec.text):
        key = classify_indication(s)
        if not key:
            continue
        adj = bool(re.search(r"adjunct", s, re.I))
        if (key, adj) in seen:
            continue
        seen.add((key, adj))
        pop = "pediatric" if re.search(r"pediatric|adolescent|children|ages?\s+1[0-7]", s, re.I) else "adult"
        out.append({"indication_key": key, "adjunctive": adj, "population": pop, "verbatim": s[:400]})
    return out


def parse_monitoring(sections: dict[str, SplSection]) -> dict[str, Any]:
    sec = sections.get("43685-7")
    if not sec:
        return {"items": [], "rems": False}
    items = []
    for s in split_sentences(sec.text):
        if re.search(r"\bmonitor", s, re.I) and re.search(
            r"glucose|lipid|weight|complete\s+blood|\bcbc\b|liver|hepatic|ecg|electrocardiogram|heart\s+rate|urinary|blood\s+pressure|prolactin|vital", s, re.I
        ):
            items.append(s[:300])
    rems = bool(re.search(r"\bREMS\b|risk\s+evaluation\s+and\s+mitigation", sec.text))
    return {"items": items[:20], "rems": rems}


def label_safety_score(boxed: dict[str, Any], contra: dict[str, Any], monitoring: dict[str, Any]) -> float:
    score = 3.0
    if boxed.get("present"):
        score -= 1.5
    score -= 0.4 * min(3, contra.get("non_hypersensitivity_count", 0))
    if monitoring.get("rems"):
        score -= 0.5
    return round(max(0.0, score), 3)


# ---------------------------------------------------------------------------
# §6 adverse reaction incidence tables
# ---------------------------------------------------------------------------

_N_RE = re.compile(r"\bN\s*=\s*(\d[\d,]*)", re.I)


def _is_numeric_cell(v: str) -> bool:
    v = v.strip()
    return bool(v) and bool(re.fullmatch(r"[<>≤≥]?\s*[-−]?\d+(\.\d+)?\s*%?\s*\*?|[-–—]|<\s*1", v))


def _split_header(tbl: dict[str, Any]) -> tuple[list[str], list[list[str]]]:
    headers = list(tbl.get("header_rows") or [])
    rows = list(tbl.get("rows") or [])
    if not headers:
        # header rows = leading rows until a row whose non-first cells are mostly numeric
        while rows:
            r = rows[0]
            cells = [c for c in r[1:] if c]
            if cells and sum(_is_numeric_cell(c) for c in cells) >= max(1, len(cells) // 2):
                break
            headers.append(rows.pop(0))
            if len(headers) >= 3:
                break
    return merge_header_rows(headers), rows


def parse_ae_tables(sections: dict[str, SplSection], drug_terms: list[str]) -> list[dict[str, Any]]:
    sec = sections.get("34084-4")
    if not sec:
        return []
    out: list[dict[str, Any]] = []
    drug_re = re.compile("|".join(re.escape(t) for t in drug_terms), re.I) if drug_terms else None
    for t_i, tbl in enumerate(sec.tables):
        headers, rows = _split_header(tbl)
        if not headers or not rows:
            continue
        arms = []
        for c, h in enumerate(headers):
            if c == 0 or not h:
                continue
            is_pbo = bool(re.search(r"placebo", h, re.I))
            is_drug = bool(drug_re and drug_re.search(h)) or bool(re.search(r"\bmg\b", h, re.I))
            if not (is_pbo or is_drug):
                continue
            nm = _N_RE.search(h)
            arms.append({"col": c, "name": h, "n": int(nm.group(1).replace(",", "")) if nm else None, "is_placebo": is_pbo})
        if not arms or not any(a["is_placebo"] for a in arms):
            continue
        caption = tbl.get("caption") or ""
        context = f"{caption} {tbl.get('context', '')}"
        parsed_rows = []
        soc = None
        for r in rows:
            term = (r[0] if r else "").strip()
            vals = {}
            for a in arms:
                v = r[a["col"]] if a["col"] < len(r) else ""
                if re.search(r"<\s*1", v):
                    vals[a["col"]] = 0.5
                else:
                    num = parse_number(v)
                    vals[a["col"]] = num if num is not None and _is_numeric_cell(v) else None
            if term and all(v is None for v in vals.values()):
                soc = term
                continue
            if not term:
                continue
            parsed_rows.append({"term": re.sub(r"\s*[*†‡§¶#]+$|\s+[a-d]$", "", term).strip(), "soc": soc, "values": vals,
                                "verbatim": " | ".join(x for x in r if x)[:400]})
        if parsed_rows:
            out.append({
                "table_index": t_i, "caption": caption, "context": tbl.get("context"),
                "indication_key": classify_indication(context), "arms": arms, "rows": parsed_rows,
                "footnotes": tbl.get("footnotes", ""),
            })
    return out


# ---------------------------------------------------------------------------
# §14 clinical studies
# ---------------------------------------------------------------------------

_MEAN_PAREN_RE = re.compile(r"([-−–]?\d+(?:\.\d+)?)\s*(\*+|†|‡)?\s*\(\s*([-−–]?\d+(?:\.\d+)?)\s*(?:,|to)?\s*([-−–]?\d+(?:\.\d+)?)?\s*\)")


def _parse_paren(cell: str) -> dict[str, Any] | None:
    m = _MEAN_PAREN_RE.search(cell or "")
    if not m:
        n = parse_number(cell)
        if n is None or not re.search(r"\d", cell or ""):
            return None
        return {"value": n, "a": None, "b": None, "flag": "*" in (cell or "")}
    val = parse_number(m.group(1))
    a = parse_number(m.group(3))
    b = parse_number(m.group(4)) if m.group(4) else None
    return {"value": val, "a": a, "b": b, "flag": bool(m.group(2)) or "*" in cell}


def parse_clinical_studies(sections: dict[str, SplSection]) -> dict[str, Any]:
    sec = sections.get("34092-7")
    if not sec:
        return {"nct_ids": [], "efficacy_rows": [], "study_durations": {}, "onset": [], "key_secondary": [], "text_present": False}
    text = sec.text
    nct_ids = sorted(set(re.findall(r"NCT\d{8}", text)))
    sents = split_sentences(text)

    # Study N → duration (weeks), indication context
    durations: dict[str, float] = {}
    for s in sents:
        for m in re.finditer(r"Stud(?:y|ies)\s+(\d+(?:\s*(?:,|and)\s*\d+)*)", s):
            wk = parse_weeks(s)
            if wk:
                for num in re.findall(r"\d+", m.group(1)):
                    durations.setdefault(f"Study {num}", wk)
    onset = []
    for s in sents:
        mm = re.search(r"as\s+early\s+as\s+(day|week)\s*(\d+)", s, re.I)
        if mm:
            wk = float(mm.group(2)) / (7.0 if mm.group(1).lower() == "day" else 1.0)
            onset.append({"weeks": round(wk, 2), "verbatim": s[:400], "indication_key": classify_indication(s)})
    key_secondary = []
    for s in sents:
        if re.search(r"key\s+secondary", s, re.I):
            key_secondary.append({"scale": classify_scale(s), "verbatim": s[:400]})

    rows_out: list[dict[str, Any]] = []
    for t_i, tbl in enumerate(sec.tables):
        headers, rows = _split_header(tbl)
        hl = [h.lower() for h in headers]

        def col(*pats: str) -> int | None:
            for i, h in enumerate(hl):
                if any(re.search(p, h) for p in pats):
                    return i
            return None

        c_study = col(r"\bstudy\b", r"trial")
        c_group = col(r"treatment", r"group", r"\bdose\b", r"arm")
        c_base = col(r"baseline")
        c_change = col(r"ls\s*mean\s*change", r"change\s+from\s+baseline", r"ls\s*mean")
        c_diff = col(r"placebo[-\s]*subtracted", r"difference", r"\bdiff")
        if c_diff is None or (c_change is None and c_base is None):
            continue
        caption = tbl.get("caption") or ""
        context = tbl.get("context") or ""
        scale = classify_scale(caption, " ".join(headers)) or classify_scale(context)
        ind = classify_indication(caption, context)
        foot = tbl.get("footnotes", "") + " " + caption
        multiplicity = bool(re.search(r"multiplicity|multiple\s+comparisons|adjusted\s+for|type\s+i\s+error", foot, re.I))
        tp = parse_weeks(caption) or parse_weeks(" ".join(headers))
        current_study = None
        for r in rows:
            if c_study is not None and c_study < len(r) and r[c_study].strip():
                current_study = r[c_study].strip()
            grp = r[c_group].strip() if c_group is not None and c_group < len(r) else ""
            if not grp:
                continue
            diff = _parse_paren(r[c_diff]) if c_diff < len(r) else None
            base = _parse_paren(r[c_base]) if c_base is not None and c_base < len(r) else None
            chg = _parse_paren(r[c_change]) if c_change is not None and c_change < len(r) else None
            nm = _N_RE.search(grp)
            study_label = re.sub(r"\s+", " ", (current_study or f"Table {t_i + 1}")).strip()
            sm = re.search(r"Study\s*\d+", study_label)
            rows_out.append({
                "table_index": t_i, "study_label": sm.group(0) if sm else study_label, "study_cell": study_label,
                "arm": grp, "is_placebo": bool(re.search(r"placebo", grp, re.I)),
                "n": int(nm.group(1).replace(",", "")) if nm else None,
                "baseline_mean": base["value"] if base else None, "baseline_sd": base["a"] if base else None,
                "ls_change": chg["value"] if chg else None, "ls_change_se": chg["a"] if chg else None,
                "diff": diff["value"] if diff else None,
                "ci_low": diff["a"] if diff else None, "ci_high": diff["b"] if diff else None,
                "flag_significant": bool(diff and diff["flag"]),
                "multiplicity_footnote": multiplicity,
                "scale": scale, "indication_key": ind, "timepoint_weeks": tp,
                "caption": caption, "verbatim": " | ".join(x for x in r if x)[:400],
            })
    for row in rows_out:
        if row["timepoint_weeks"] is None:
            row["timepoint_weeks"] = durations.get(row["study_label"])
    return {"nct_ids": nct_ids, "efficacy_rows": rows_out, "study_durations": durations, "onset": onset,
            "key_secondary": key_secondary, "text_present": True}


def parse_mechanism(sections: dict[str, SplSection]) -> str | None:
    sec = sections.get("43679-0")
    if not sec:
        return None
    sents = [s for s in split_sentences(sec.text) if not re.match(r"^\s*12\.1", s)]
    return " ".join(sents[:3])[:800] if sents else None


def parse_all(sections: dict[str, SplSection], drug_terms: list[str]) -> dict[str, Any]:
    boxed = parse_boxed_warning(sections)
    contra = parse_contraindications(sections)
    monitoring = parse_monitoring(sections)
    return {
        "boxed_warning": boxed,
        "contraindications": contra,
        "indications": parse_indications(sections),
        "dosing": parse_dosing(sections),
        "cyp": parse_cyp_interactions(sections, drug_terms),
        "monitoring": monitoring,
        "label_safety_score": label_safety_score(boxed, contra, monitoring),
        "ae_tables": parse_ae_tables(sections, drug_terms),
        "clinical_studies": parse_clinical_studies(sections),
        "mechanism": parse_mechanism(sections),
    }
