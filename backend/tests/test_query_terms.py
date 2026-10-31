"""Indication names typed by users must become search phrases the literature actually uses."""

from mednik.services.guidelines import guideline_pubmed_term
from mednik.services.literature_ingest import epmc_asset_query, pubmed_asset_term
from mednik.services.mvd import mvd_pubmed_term
from mednik.services.query_terms import condition_terms


def test_setting_words_and_parentheticals_are_dropped():
    for name in ("adjunct Major Depressive Disorder", "Major Depressive Disorder (adjunct)", "MDD augmentation, adults"):
        terms = condition_terms(name)
        assert terms[0] == "major depressive disorder" and "major depression" in terms, name
        assert not any("adjunct" in t or "(" in t for t in terms)


def test_unknown_indication_falls_back_to_cleaned_name_and_terms_are_unique():
    assert condition_terms("Tourette syndrome (pediatric)") == ["tourette syndrome"]
    t = condition_terms("Bipolar I Depression")
    assert len(t) == len(set(t))


def test_queries_use_the_expanded_terms():
    q = pubmed_asset_term(["cariprazine", "Vraylar"], "adjunct Major Depressive Disorder")
    assert '"major depressive disorder"[tiab]' in q and "adjunct" not in q
    assert '"major depression"' in epmc_asset_query(["cariprazine"], "adjunct Major Depressive Disorder")
    assert '"major depressive disorder"[mh]' in mvd_pubmed_term("adjunct Major Depressive Disorder")
    assert "unipolar depression" in guideline_pubmed_term("Major Depressive Disorder (adjunct)")
