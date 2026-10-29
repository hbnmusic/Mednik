from mednik.services.extraction import regex_endpoints, validate_items

SRC = {"12345678": "In this 6-week trial, the LS mean difference versus placebo in PANSS total score was −7.6 (95% CI −10.9 to −4.3; P<0.001)."}


def test_quote_must_exist_and_numbers_must_be_in_quote():
    items = [
        {"endpoint_scale_name": "PANSS", "effect_size_type": "LS_MEAN_DIFF", "value": -7.6, "ci_95_lower": -10.9, "ci_95_upper": -4.3,
         "p_value": "<0.001", "verbatim_quote": "LS mean difference versus placebo in PANSS total score was -7.6 (95% CI -10.9 to -4.3; P<0.001)",
         "source_id": "12345678"},
        {"endpoint_scale_name": "PANSS", "effect_size_type": "LS_MEAN_DIFF", "value": -9.9, "verbatim_quote": "PANSS total score improved dramatically",
         "source_id": "12345678"},  # fabricated quote
        {"endpoint_scale_name": "PANSS", "effect_size_type": "LS_MEAN_DIFF", "value": -7.6, "n_treatment": 250,
         "verbatim_quote": "LS mean difference versus placebo in PANSS total score was −7.6", "source_id": "12345678"},  # N not in quote
        {"endpoint_scale_name": "PANSS", "effect_size_type": "LS_MEAN_DIFF", "value": -7.6,
         "verbatim_quote": "LS mean difference versus placebo in PANSS total score was −7.6", "source_id": "99999999"},  # wrong source
    ]
    out = validate_items(items, SRC, numeric_fields=("value", "ci_95_lower", "ci_95_upper", "n_treatment"), schema_name="t", provider="test",
                         require_value="value")
    assert len(out) == 2
    assert out[0]["value"] == -7.6 and out[0]["p_value"] == "<0.001"
    assert out[1]["n_treatment"] is None  # nulled: not present in its quote


def test_regex_fallback_extracts_with_verbatim_sentence():
    out = regex_endpoints(list(SRC.items()))
    assert out and out[0]["effect_size_type"] == "LS_MEAN_DIFF" and out[0]["value"] == -7.6 and out[0]["ci_95_upper"] == -4.3
    assert out[0]["source_id"] == "12345678"
