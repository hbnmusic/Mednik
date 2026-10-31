"""Search terms for an indication.

The name a user types ("adjunct Major Depressive Disorder", "MDD (adjunct)") is a description of a study
population, not a phrase that appears in papers; quoted verbatim it matches nothing in PubMed. These helpers
strip setting words and parentheticals and expand known indications to the phrases the literature uses.
"""

from __future__ import annotations

import re

from ..taxonomy import INDICATION_BY_KEY, classify_indication

_QUALIFIERS = re.compile(
    r"\b(adjunct(?:ive|ively)?|adjuvant|augment(?:ation|ing)?|add[-\s]?on|acute|maintenance|monotherapy|relapse\s+prevention|"
    r"adults?|pediatric|paediatric|adolescents?|children|elderly|geriatric)\b", re.I)

_SYNONYMS: dict[str, tuple[str, ...]] = {
    "mdd": ("major depressive disorder", "major depression", "unipolar depression", "depression"),
    "bipolar_depression": ("bipolar depression", "bipolar I depression", "depressive episodes associated with bipolar disorder"),
    "bipolar_mania": ("bipolar mania", "manic episode", "bipolar I disorder"),
    "schizophrenia": ("schizophrenia",),
    "gad": ("generalized anxiety disorder",),
    "ptsd": ("post-traumatic stress disorder", "posttraumatic stress disorder"),
}


def condition_terms(name: str) -> list[str]:
    """Phrases to search for, most specific first."""
    base = re.sub(r"\([^)]*\)", " ", name or "")
    base = re.sub(r"\s+", " ", _QUALIFIERS.sub(" ", base)).strip(" ,;-/")
    key = classify_indication(base) or classify_indication(name)
    terms: list[str] = []
    if key in INDICATION_BY_KEY:
        terms.append(INDICATION_BY_KEY[key].label.lower())
        terms += list(_SYNONYMS.get(key, ()))
    if base:
        terms.append(base.lower())
    out: list[str] = []
    for t in terms:
        t = t.replace('"', "").strip().lower()
        if t and t not in out:
            out.append(t)
    return out or [(name or "").replace('"', "").strip().lower()]


def pubmed_condition(name: str) -> str:
    return "(" + " OR ".join(f'"{t}"[tiab] OR "{t}"[mh]' for t in condition_terms(name)) + ")"


def epmc_condition(name: str) -> str:
    return "(" + " OR ".join(f'"{t}"' for t in condition_terms(name)) + ")"
