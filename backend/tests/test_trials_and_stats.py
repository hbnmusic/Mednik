import math

from mednik.services import stats
from mednik.services.trials_ingest import parse_ctgov_study, parse_results

from .fixtures_data import ctgov_study


def test_sponsor_phase_design_classification():
    ind = parse_ctgov_study(ctgov_study("NCT00000001", "A Study of Alphazine in Schizophrenia", "Alphazine"), ["alphazine"])
    assert ind["sponsor_class"] == "INDUSTRY" and ind["phase_max"] == 3 and ind["randomized"]
    assert ind["design_archetype"] == "ACUTE_PARALLEL_PLACEBO" and ind["indication_key"] == "schizophrenia"
    assert ind["duration_weeks"] == 6 and ind["pct_us_sites"] == 1.0
    cut = ind["eligibility"]["severity_cutoffs"]
    assert any(c["scale"] == "PANSS" and c["min"] == 80 for c in cut)
    for cls in ("OTHER", "NIH", "NETWORK", "FED"):
        aca = parse_ctgov_study(ctgov_study("NCT00000002", "IIS of Alphazine", "Alphazine", sponsor_class=cls, phase="PHASE4"), ["alphazine"])
        assert aca["sponsor_class"] == "ACADEMIC_IIS" and aca["sponsor_class_raw"] == cls


def test_results_to_endpoints_with_cohens_d_and_nnh():
    st = ctgov_study("NCT00000003", "Alphazine Phase 3", "Alphazine", n=300, diff=-8.0, ci=(-11.0, -5.0))
    meta = parse_ctgov_study(st, ["alphazine"])
    res = parse_results(st, meta, ["alphazine"])
    prim = next(e for e in res["endpoints"] if e["endpoint_scale_name"] == "PANSS_TOTAL")
    # SE = 6/(2*1.96)=1.531; SDp = 1.531/sqrt(2/150)=13.26; d = 8/13.26 = 0.603 (favourable → positive)
    assert math.isclose(prim["cohens_d"], 0.603, abs_tol=0.01)
    assert prim["is_primary"] and prim["significant"] and prim["is_multiplicity_controlled"]
    aka = next(e for e in res["endpoints"] if e["endpoint_scale_name"] == "AKATHISIA")
    assert math.isclose(aka["risk_difference"], 12 / 150, abs_tol=1e-6) and aka["nnt_nnh"] == 13
    assert res["dropout"]["overall"] == 0.25
    assert res["baseline"]["PANSS_TOTAL"]["mean"] == 95.0
    psp = next(e for e in res["endpoints"] if e["endpoint_scale_name"] == "PSP")
    assert psp["is_multiplicity_controlled"] is None and psp["favorable"]


def test_sample_size_and_pooling():
    assert stats.n_per_arm_continuous(0.5) == 64  # matches standard two-sample t-test planning value
    assert stats.n_per_arm_proportions(0.4, 0.25) in range(150, 160)
    pooled = stats.pool_inverse_variance([(0.5, 0.02), (0.3, 0.02)])
    assert math.isclose(pooled["estimate"], 0.4, abs_tol=1e-9)
    assert stats.nnt_from_rd(0.08) == 13
    assert stats.inflate_for_dropout(100, 0.2) == 125
