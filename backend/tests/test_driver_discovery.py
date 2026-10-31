"""Market value drivers are discovered from guidelines (primary) and preference studies, not from a preset list."""

import asyncio
import re

import pytest

from mednik.config import Settings
from mednik.db import session_scope
from mednik.models import Asset, Document, DocumentLink, Indication, MarketValueDriver
from mednik.services import extraction, mvd
from mednik.services.llm import base as llm_base
from mednik.services.matrix import build_matrix

G1 = ("Clinicians should favour agents with minimal weight gain and metabolic burden. Cost and insurance coverage often determine access "
      "to treatment. A rapid onset of action is preferred in acute episodes.")
G2 = "Avoid agents causing prominent sedation or drowsiness. Weight gain is a key consideration when choosing between agents."
P1 = ("Psychiatrists rated reducing hospital readmission highest (relative importance 40%), followed by metabolic side effects (30%) "
      "and once-daily dosing (30%).")

CANDIDATES = {
    "G1": [("minimal weight gain and metabolic burden", "STRONG_EMPHASIS", None), ("cost and insurance coverage", "QUALITATIVE_MENTION", None),
           ("rapid onset of action", "QUALITATIVE_MENTION", None)],
    "G2": [("prominent sedation or drowsiness", "QUALITATIVE_MENTION", None), ("Weight gain is a key consideration", "STRONG_EMPHASIS", None)],
    "P1": [("reducing hospital readmission", "RELATIVE_IMPORTANCE_PCT", 40.0), ("metabolic side effects", "RELATIVE_IMPORTANCE_PCT", 30.0),
           ("once-daily dosing", "RELATIVE_IMPORTANCE_PCT", 30.0)],
}
QUOTES = {  # a quantitative importance value must appear in its quote (the citation check enforces it)
    "reducing hospital readmission": "reducing hospital readmission highest (relative importance 40%)",
    "metabolic side effects": "metabolic side effects (30%)", "once-daily dosing": "once-daily dosing (30%)",
}
TEXT = {"G1": G1, "G2": G2, "P1": P1}
GROUPS = [("Weight gain and metabolic burden", "SAFETY_TOLERABILITY", "weight_metabolic", ("weight", "metabolic")),
          ("Sedation", "SAFETY_TOLERABILITY", "sedation", ("sedation",)),
          ("Cost and access", "OTHER", "NONE", ("cost",)),
          ("Rapid onset", "EFFICACY", "rapid_onset", ("onset",)),
          ("Relapse and readmission", "EFFICACY", "relapse_prevention", ("readmission",)),
          ("Once-daily dosing", "EXECUTION_DOSING", "dosing_convenience", ("once-daily",))]


class FakeLlm(llm_base.LlmProvider):
    name = "fake-drivers"

    @property
    def model(self):
        return "fake-drivers-1"

    async def _call(self, system, prompt, schema, schema_name):
        if schema_name == "driver_candidates":
            sid = re.search(r"source_id=(\w+)", prompt).group(1)
            return {"study_design": "SURVEY" if sid == "P1" else "GUIDELINE", "n_respondents": 100 if sid == "P1" else None,
                    "attributes": [{"attribute_text": t, "importance_metric": m, "importance_value": v, "verbatim_quote": QUOTES.get(t, t),
                                    "source_id": sid} for t, m, v in CANDIDATES[sid]]}
        if schema_name == "driver_grouping":
            labels = [re.match(r"(\d+) \| (\w+) \| (.*)", ln) for ln in prompt.split("Labels (id | source kind | label):\n")[1].splitlines()]
            drivers = []
            for name, cat, mk, words in GROUPS:
                ids = [int(m.group(1)) for m in labels if m and any(w in m.group(3).lower() for w in words)]
                if ids:
                    drivers.append({"name": name, "category": cat, "measure_key": mk, "member_ids": ids})
            return {"drivers": drivers}
        raise AssertionError(schema_name)


@pytest.fixture
def fake(monkeypatch):
    prov = FakeLlm(Settings(llm_max_concurrency=2))
    monkeypatch.setattr(mvd, "get_provider", lambda: prov)
    monkeypatch.setattr(extraction, "get_provider", lambda: prov)
    llm_base.reset_usage()
    return prov


def _seed(name: str, key: str, docs: dict[str, tuple[str, list[str], str]]) -> int:
    with session_scope() as s:
        ind = Indication(name=name, key=key)
        s.add(ind)
        s.flush()
        for sid, (text, tags, role) in docs.items():
            d = Document(sha256=f"{key}-{sid}".ljust(64, "0"), source_type="pubmed", source_id=sid, pmid=sid, title=f"Doc {sid}", year=2022,
                         abstract=text, evidence_tags_json=tags)
            s.add(d)
            s.flush()
            s.add(DocumentLink(document_id=d.id, indication_id=ind.id, role=role))
        return ind.id


def test_drivers_come_from_the_sources_and_guidelines_outweigh_preferences(fake):
    ind_id = _seed("Fictional Disorder", "fictional", {"G1": (G1, ["GUIDELINE"], "guideline"), "G2": (G2, ["GUIDELINE"], "guideline"),
                                                         "P1": (P1, ["SURVEY"], "mvd_preference")})
    with session_scope() as s:
        out = asyncio.run(mvd.compute_mvds(s, s.get(Indication, ind_id)))
        rows = {d.attribute_name: d for d in out}
        assert set(rows) == {g[0] for g in GROUPS}  # one driver per group of sources, named from the sources
        # differently worded labels ("minimal weight gain and metabolic burden", "Weight gain is a key...", "metabolic side effects") are one driver
        w = rows["Weight gain and metabolic burden"]
        assert w.mvd_impact_score == 1.0 and w.method == "llm_guideline+preference"
        assert w.component_scores_json["n_guideline_citing"] == 2 and w.component_scores_json["n_pref_studies"] == 1
        assert {q["kind"] for q in w.verbatim_evidence} == {"guideline", "preference"}
        # a driver only a preference study names is capped at the 0.35 preference weight, however high it ranks there:
        # raw 0.35 against 0.65 + 0.35 × (30/40) = 0.9125 for the driver both sources cite
        rel = rows["Relapse and readmission"]
        assert rel.method == "llm_preference" and rel.component_scores_json["weight_guideline"] == 0.65
        assert abs(rel.mvd_impact_score - (0.10 + 0.90 * 0.35 / 0.9125)) < 1e-3 and rel.mvd_impact_score < w.mvd_impact_score
        assert rows["Cost and access"].component_scores_json["_measure_key"] is None and rows["Cost and access"].category == "OTHER"
        assert min(d.mvd_impact_score for d in out) > 0.10 - 1e-9


def test_unmeasured_driver_is_listed_as_insufficient_and_sources_are_required(fake):
    ind_id = _seed("Fictional Disorder Two", "fictional2", {"G1": (G1, ["GUIDELINE"], "guideline")})
    with session_scope() as s:
        asyncio.run(mvd.compute_mvds(s, s.get(Indication, ind_id)))
        a = Asset(generic_name="alphafic", brand_name="Alphafic")
        b = Asset(generic_name="betafic", brand_name="Betafic")
        s.add_all([a, b])
        s.flush()
        mx = build_matrix(s, s.get(Indication, ind_id), a, [b])
        node = next(n for n in mx["nodes"] if n["mvd_name"] == "Cost and access")
        assert node["x"] is None and node["kind"] == "unmeasured" and "no trial, label or safety measure" in node["explanation"].lower()
        assert node["y"] > 0
    empty_id = _seed("Fictional Disorder Three", "fictional3", {})
    with session_scope() as s:
        assert asyncio.run(mvd.compute_mvds(s, s.get(Indication, empty_id))) == []  # no sources → no drivers, no placeholders
        assert s.query(MarketValueDriver).filter_by(indication_id=empty_id).count() == 0
