"""LLM structured extraction with the Zero-Hallucination Citation Constraint.

Every extracted claim must carry a ``verbatim_quote`` that is found (after
whitespace / dash / quote normalisation) in the retrieved source text, and a
``source_id`` that belongs to the documents supplied in the prompt. Every
numeric field must appear inside its own quote; numbers that do not are set
to null. Items whose quote cannot be found are discarded. All decisions are
written to ``extraction_audit``.

When no LLM is configured, deterministic regex fallbacks produce the same item
shapes (with the matched sentence as the quote).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..db import aux_scope
from ..models import ExtractionAudit
from ..taxonomy import MVD_BY_KEY, classify_scale, map_text_to_mvd, mvd_profile
from ..textutil import normalize_text, parse_number, parse_p_value, split_sentences
from .llm.base import LlmError, LlmProvider, get_provider

log = logging.getLogger("mednik.extraction")

NUM = {"type": ["number", "null"]}
INT = {"type": ["integer", "null"]}
STR = {"type": ["string", "null"]}
BOOL = {"type": ["boolean", "null"]}

ENDPOINT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "endpoint_scale_name": {"type": "string", "description": "Endpoint / scale exactly as named in the text"},
                    "timepoint_weeks": NUM,
                    "arm_name": STR,
                    "comparator_arm": STR,
                    "comparator_is_placebo": BOOL,
                    "effect_size_type": {"type": "string", "enum": ["LS_MEAN_DIFF", "COHENS_D", "NNT", "NNH", "INCIDENCE_PCT", "OR", "HR", "RD"]},
                    "value": NUM,
                    "comparator_value": NUM,
                    "ci_95_lower": NUM,
                    "ci_95_upper": NUM,
                    "p_value": STR,
                    "n_treatment": INT,
                    "n_comparator": INT,
                    "is_primary": BOOL,
                    "is_post_hoc": BOOL,
                    "verbatim_quote": {"type": "string"},
                    "source_id": {"type": "string"},
                },
                "required": ["endpoint_scale_name", "effect_size_type", "verbatim_quote", "source_id"],
            },
        }
    },
    "required": ["items"],
}


def mvd_schema(indication_key: str | None) -> dict[str, Any]:
    keys = list(mvd_profile(indication_key)) + ["OTHER"]
    return {
        "type": "object",
        "properties": {
            "study_design": {"type": "string", "enum": ["DCE", "CONJOINT", "BEST_WORST", "SURVEY", "GUIDELINE", "REVIEW", "OTHER"]},
            "respondent_type": STR,
            "n_respondents": INT,
            "attributes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "attribute_text": {"type": "string"},
                        "canonical_attribute": {"type": "string", "enum": keys},
                        "importance_value": NUM,
                        "importance_metric": {"type": "string", "enum": [
                            "RELATIVE_IMPORTANCE_PCT", "COEFFICIENT", "RANK", "PERCENT_RATING_IMPORTANT", "WILLINGNESS_TO_TRADE",
                            "QUALITATIVE_HIGH", "QUALITATIVE_MENTION"]},
                        "verbatim_quote": {"type": "string"},
                        "source_id": {"type": "string"},
                    },
                    "required": ["attribute_text", "canonical_attribute", "importance_metric", "verbatim_quote", "source_id"],
                },
            },
        },
        "required": ["study_design", "attributes"],
    }


GUIDELINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "guideline_body": STR,
        "publication_year": INT,
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "asset_mention": {"type": "string"},
                    "indication_phase": {"type": "string", "enum": ["ACUTE", "MAINTENANCE", "UNSPECIFIED"]},
                    "line": {"type": "string", "enum": ["FIRST", "SECOND", "THIRD", "NOT_RECOMMENDED", "INSUFFICIENT", "UNCLEAR"]},
                    "role": {"type": "string", "enum": ["MONOTHERAPY", "ADJUNCTIVE", "EITHER", "UNSPECIFIED"]},
                    "level_of_evidence": STR,
                    "verbatim_quote": {"type": "string"},
                    "source_id": {"type": "string"},
                },
                "required": ["asset_mention", "line", "verbatim_quote", "source_id"],
            },
        },
    },
    "required": ["recommendations"],
}

SYSTEM_PROMPT = (
    "You are a meticulous medical-affairs evidence extractor. Extract ONLY what is explicitly stated in the supplied "
    "documents. Rules: (1) every item must include `verbatim_quote`, copied character-for-character from ONE document "
    "(a contiguous span, 1–3 sentences or one table row; do not paraphrase, merge, or fix typos); (2) `source_id` must be "
    "the id shown in that document's header; (3) every number you report must appear inside your own quote — if it does "
    "not, return null for that field; (4) never compute, convert, infer or estimate values; (5) if nothing qualifies, "
    "return an empty list."
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _num_forms(v: float) -> list[str]:
    forms = set()
    for x in (v, -v, abs(v)):
        s = f"{x:.6f}".rstrip("0").rstrip(".")
        forms.add(s)
        if s.startswith("0."):
            forms.add(s[1:])
        if s.startswith("-0."):
            forms.add("-" + s[2:])
        if float(x).is_integer():
            forms.add(str(int(x)))
        for nd in (1, 2, 3):
            forms.add(f"{x:.{nd}f}")
    return [f for f in forms if f not in ("", "-")]


def number_in_text(v: float | int | None, text_norm: str) -> bool:
    if v is None:
        return True
    for f in _num_forms(float(v)):
        f2 = re.escape(f.lstrip("-"))
        if re.search(rf"(?<![\d.]){f2}(?![\d])", text_norm):
            return True
    return False


def validate_items(items: list[dict[str, Any]], sources: dict[str, str], *, numeric_fields: tuple[str, ...],
                   schema_name: str, provider: str | None, require_value: str | None = None,
                   context_numeric_fields: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Enforce the citation constraint. ``sources`` maps source_id → full retrieved text."""
    norm_sources = {sid: normalize_text(txt) for sid, txt in sources.items()}
    accepted: list[dict[str, Any]] = []
    audits: list[ExtractionAudit] = []
    for it in items or []:
        sid = str(it.get("source_id") or "").strip()
        quote = it.get("verbatim_quote") or ""
        qn = normalize_text(quote)
        reason = None
        if sid not in norm_sources:
            # tolerate "PMID: 123" style ids
            m = re.search(r"\d{5,}|[0-9a-f]{64}|PMC\d+|NCT\d{8}", sid)
            sid = next((k for k in norm_sources if m and m.group(0) in k), sid)
        if sid not in norm_sources:
            reason = f"unknown source_id {it.get('source_id')!r}"
        elif len(qn) < 8:
            reason = "quote too short"
        elif qn not in norm_sources[sid]:
            reason = "verbatim_quote not found in source text"
        if reason:
            audits.append(ExtractionAudit(source_id=sid or "?", schema_name=schema_name, provider=provider, item_json=it,
                                          accepted=False, reason=reason))
            continue
        it = dict(it)
        it["source_id"] = sid
        nulled = []
        for f in numeric_fields:
            if it.get(f) is not None and not number_in_text(it[f], qn):
                nulled.append(f)
                it[f] = None
        for f in context_numeric_fields:  # e.g. timepoint: must appear somewhere in the source document
            if it.get(f) is not None and not number_in_text(it[f], norm_sources[sid]):
                nulled.append(f)
                it[f] = None
        if "p_value" in it and it.get("p_value"):
            pv, _ = parse_p_value(it["p_value"])
            if pv is None or not number_in_text(pv, qn):
                nulled.append("p_value")
                it["p_value"] = None
        if require_value and it.get(require_value) is None:
            audits.append(ExtractionAudit(source_id=sid, schema_name=schema_name, provider=provider, item_json=it, accepted=False,
                                          reason=f"{require_value} not present in quote (nulled: {nulled})"))
            continue
        audits.append(ExtractionAudit(source_id=sid, schema_name=schema_name, provider=provider, item_json=it, accepted=True,
                                      reason=f"nulled fields: {nulled}" if nulled else None))
        accepted.append(it)
    try:
        with aux_scope() as s:
            s.add_all(audits)
    except Exception:  # noqa: BLE001 - audit is best-effort
        log.exception("audit write failed")
    return accepted


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _docs_block(docs: list[tuple[str, str]], max_chars: int = 60000) -> str:
    out, used = [], 0
    for sid, text in docs:
        t = text[: max(0, max_chars - used)]
        if not t:
            break
        out.append(f"=== DOCUMENT source_id={sid} ===\n{t}\n=== END {sid} ===")
        used += len(t)
    return "\n\n".join(out)


async def _run(provider: LlmProvider, prompt: str, schema: dict, name: str) -> dict[str, Any]:
    try:
        return await provider.generate_json(SYSTEM_PROMPT, prompt, schema, name)
    except (LlmError, ValueError) as exc:
        log.warning("LLM extraction failed (%s): %s", name, exc)
        return {}


async def extract_endpoints(docs: list[tuple[str, str]], asset_terms: list[str], indication_label: str,
                            provider: LlmProvider | None = None) -> list[dict[str, Any]]:
    """Effect sizes for the asset vs comparator from abstracts / full text / PDFs."""
    provider = provider or get_provider()
    sources = dict(docs)
    if provider is None:
        return regex_endpoints(docs)
    items: list[dict[str, Any]] = []
    # batch small documents (abstracts) together, large ones alone
    batch: list[tuple[str, str]] = []
    size = 0
    batches: list[list[tuple[str, str]]] = []
    for d in docs:
        if size + len(d[1]) > 24000 and batch:
            batches.append(batch)
            batch, size = [], 0
        batch.append(d)
        size += len(d[1])
    if batch:
        batches.append(batch)
    for b in batches:
        prompt = (
            f"Asset of interest: {', '.join(asset_terms)}. Indication context: {indication_label}.\n"
            "Extract every quantitative efficacy or safety result comparing the asset of interest with placebo or an active "
            "comparator (LS-mean differences, Cohen's d / effect sizes, NNT, NNH, response / remission / adverse-event incidence "
            "percentages, odds ratios, hazard ratios, risk differences). For incidence percentages put the asset arm in `value` and "
            "the comparator arm in `comparator_value`. Mark `is_post_hoc` true only if the text says post hoc / pooled / exploratory.\n\n"
            + _docs_block(b)
        )
        out = await _run(provider, prompt, ENDPOINT_SCHEMA, "endpoint_extraction")
        items += out.get("items") or []
    return validate_items(items, sources, numeric_fields=("value", "comparator_value", "ci_95_lower", "ci_95_upper", "n_treatment", "n_comparator"),
                          context_numeric_fields=("timepoint_weeks",), schema_name="endpoint_extraction",
                          provider=provider.name, require_value="value")


async def extract_mvd_preferences(docs: list[tuple[str, str]], indication_key: str | None, indication_label: str,
                                  provider: LlmProvider | None = None) -> list[dict[str, Any]]:
    provider = provider or get_provider()
    sources = dict(docs)
    if provider is None:
        return lexical_mvd_mentions(docs, indication_key)
    profile = mvd_profile(indication_key)
    guide = "\n".join(f"- {k}: {MVD_BY_KEY[k].name}" for k in profile)
    results: list[dict[str, Any]] = []
    for sid, text in docs:
        prompt = (
            f"Indication: {indication_label}. Identify treatment attributes that clinicians (or patients) value when choosing "
            "a treatment, as reported in this study (discrete choice experiment, conjoint, best-worst scaling, survey, or "
            "guideline). Map each attribute to one canonical attribute key below (use OTHER if none fits). Report the "
            "importance value exactly as given (relative importance %, coefficient, rank, % rating important) or a qualitative "
            f"flag when no number is reported.\nCanonical attribute keys:\n{guide}\n\n" + _docs_block([(sid, text)], 30000)
        )
        out = await _run(provider, prompt, mvd_schema(indication_key), "mvd_preference_extraction")
        attrs = out.get("attributes") or []
        for a in attrs:
            a.setdefault("source_id", sid)
            a["_study_design"] = out.get("study_design")
            a["_n_respondents"] = out.get("n_respondents")
            a["_respondent_type"] = out.get("respondent_type")
        results += validate_items(attrs, sources, numeric_fields=("importance_value",), schema_name="mvd_preference_extraction",
                                  provider=provider.name)
    for r in results:
        if r.get("canonical_attribute") == "OTHER" or r.get("canonical_attribute") not in profile:
            r["canonical_attribute"] = map_text_to_mvd(r.get("attribute_text", ""), indication_key) or "OTHER"
    return [r for r in results if r["canonical_attribute"] != "OTHER"]


async def extract_guideline_recs(docs: list[tuple[str, str]], asset_terms_by_asset: dict[str, list[str]], indication_label: str,
                                 provider: LlmProvider | None = None) -> list[dict[str, Any]]:
    provider = provider or get_provider()
    sources = dict(docs)
    if provider is None:
        return lexical_guideline_recs(docs, asset_terms_by_asset)
    names = "; ".join(f"{k} ({', '.join(v)})" for k, v in asset_terms_by_asset.items())
    results: list[dict[str, Any]] = []
    for sid, text in docs:
        prompt = (
            f"Indication: {indication_label}. Assets of interest: {names}.\n"
            "For each asset of interest that this guideline / consensus document explicitly positions, record the recommendation "
            "line (first / second / third-line, not recommended, insufficient evidence), treatment phase (acute vs maintenance), "
            "role (monotherapy / adjunctive) and the level-of-evidence label exactly as written. Also give the guideline body "
            "(e.g. CANMAT/ISBD, APA, NICE, WFSBP, VA/DoD) and publication year if stated.\n\n" + _docs_block([(sid, text)], 50000)
        )
        out = await _run(provider, prompt, GUIDELINE_SCHEMA, "guideline_extraction")
        recs = out.get("recommendations") or []
        for r in recs:
            r.setdefault("source_id", sid)
            r["_guideline_body"] = out.get("guideline_body")
            r["_publication_year"] = out.get("publication_year")
        results += validate_items(recs, sources, numeric_fields=(), schema_name="guideline_extraction", provider=provider.name)
    return results


# ---------------------------------------------------------------------------
# Deterministic fallbacks (no LLM configured)
# ---------------------------------------------------------------------------

_LSMD_RE = re.compile(
    r"(?:LS[-\s]?mean|least[-\s]squares?\s+mean|mean)\s+difference[^.;]{0,80}?([-−–]?\d+(?:\.\d+)?)[^.;]{0,30}?95%\s*CI[,:\s=]*\(?\s*([-−–]?\d+(?:\.\d+)?)\s*(?:to|,|;|–|−|-)\s*([-−–]?\d+(?:\.\d+)?)",
    re.I,
)
_D_RE = re.compile(r"(?:cohen'?s\s+d|effect\s+size|\bES\b|\bd\b)\s*[=:]?\s*([-−]?\d?\.\d+)", re.I)
_NNT_RE = re.compile(r"\b(NNT|NNH|number\s+needed\s+to\s+(?:treat|harm))\b[^.;]{0,30}?(\d+(?:\.\d+)?)", re.I)
_P_RE = re.compile(r"\bp\s*([<=>≤])\s*(0?\.\d+)", re.I)


def regex_endpoints(docs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sid, text in docs:
        for s in split_sentences(text):
            scale_hint = classify_scale(s)
            pm = _P_RE.search(s)
            p_txt = f"{pm.group(1)}{pm.group(2)}" if pm else None
            m = _LSMD_RE.search(s)
            if m:
                out.append({"endpoint_scale_name": scale_hint or "unspecified", "effect_size_type": "LS_MEAN_DIFF",
                            "value": parse_number(m.group(1)), "ci_95_lower": parse_number(m.group(2)), "ci_95_upper": parse_number(m.group(3)),
                            "p_value": p_txt, "verbatim_quote": s, "source_id": sid, "is_post_hoc": bool(re.search(r"post[-\s]?hoc|pooled", text, re.I))})
                continue
            m = _D_RE.search(s)
            if m and scale_hint:
                out.append({"endpoint_scale_name": scale_hint, "effect_size_type": "COHENS_D", "value": parse_number(m.group(1)),
                            "p_value": p_txt, "verbatim_quote": s, "source_id": sid})
                continue
            m = _NNT_RE.search(s)
            if m:
                kind = "NNH" if "h" in m.group(1).lower()[-1:] or "harm" in m.group(1).lower() else "NNT"
                out.append({"endpoint_scale_name": scale_hint or "unspecified", "effect_size_type": kind, "value": parse_number(m.group(2)),
                            "verbatim_quote": s, "source_id": sid})
    return validate_items(out, dict(docs), numeric_fields=("value", "ci_95_lower", "ci_95_upper"), schema_name="endpoint_regex",
                          provider="regex", require_value="value")


_IMPORTANCE_RE = re.compile(r"(most\s+important|relative\s+importance|highest\s+(?:priority|importance|weight)|strongest\s+(?:preference|predictor)|valued|prioriti[sz]ed|preferred|key\s+(?:attribute|driver|factor))", re.I)


def lexical_mvd_mentions(docs: list[tuple[str, str]], indication_key: str | None) -> list[dict[str, Any]]:
    """Fallback: sentences that state importance/preference and mention an MVD lexicon term."""
    out = []
    for sid, text in docs:
        for s in split_sentences(text):
            if not _IMPORTANCE_RE.search(s):
                continue
            # "efficacy had the highest relative importance (32%), followed by weight gain (21%), akathisia (15%)…"
            frags = [f for f in re.split(r",|;|\bfollowed\s+by\b|\band\b", s) if f.strip()]
            seen: set[str] = set()
            for frag in frags:
                key = map_text_to_mvd(frag, indication_key)
                if not key or key in seen:
                    continue
                seen.add(key)
                pct = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", frag)
                quant = bool(pct) and bool(re.search(r"importance|weight(ed|s)?\b|preference", s, re.I))
                out.append({"attribute_text": frag.strip()[:200], "canonical_attribute": key,
                            "importance_value": parse_number(pct.group(1)) if quant else None,
                            "importance_metric": "RELATIVE_IMPORTANCE_PCT" if quant else "QUALITATIVE_HIGH",
                            "verbatim_quote": s, "source_id": sid, "_study_design": "OTHER"})
    return validate_items(out, dict(docs), numeric_fields=("importance_value",), schema_name="mvd_lexical", provider="lexical")


_LINE_RE = re.compile(r"\b(first|second|third)[-\s]line\b|\bnot\s+recommended\b|insufficient\s+evidence", re.I)


def lexical_guideline_recs(docs: list[tuple[str, str]], asset_terms_by_asset: dict[str, list[str]]) -> list[dict[str, Any]]:
    from ..taxonomy import text_mentions_asset

    out = []
    for sid, text in docs:
        for s in split_sentences(text):
            lm = _LINE_RE.search(s)
            if not lm:
                continue
            for name, terms in asset_terms_by_asset.items():
                if text_mentions_asset(s, terms):
                    word = (lm.group(1) or "").lower()
                    line = {"first": "FIRST", "second": "SECOND", "third": "THIRD"}.get(word) or (
                        "NOT_RECOMMENDED" if "not" in lm.group(0).lower() else "INSUFFICIENT")
                    out.append({"asset_mention": name, "line": line,
                                "indication_phase": "MAINTENANCE" if re.search(r"maintenance", s, re.I) else ("ACUTE" if re.search(r"acute", s, re.I) else "UNSPECIFIED"),
                                "role": "ADJUNCTIVE" if re.search(r"adjunct", s, re.I) else "UNSPECIFIED",
                                "level_of_evidence": None, "verbatim_quote": s, "source_id": sid, "_lexical": True})
    return validate_items(out, dict(docs), numeric_fields=(), schema_name="guideline_lexical", provider="lexical")
