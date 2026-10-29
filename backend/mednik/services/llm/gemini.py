"""Google Gemini API provider (generateContent with JSON-schema constrained output)."""

from __future__ import annotations

import copy
from typing import Any

from . import base as llm_base
from .base import LlmError, LlmProvider, http, parse_json_text

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def to_openapi_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema → Gemini legacy `responseSchema` (OpenAPI subset: no type unions, `nullable`)."""
    s = copy.deepcopy(schema)

    def conv(node: Any) -> Any:
        if isinstance(node, dict):
            node.pop("additionalProperties", None)
            node.pop("$schema", None)
            t = node.get("type")
            if isinstance(t, list):
                non_null = [x for x in t if x != "null"]
                node["type"] = (non_null[0] if non_null else "string").upper()
                if "null" in t:
                    node["nullable"] = True
            elif isinstance(t, str):
                node["type"] = t.upper()
            for k, v in list(node.items()):
                node[k] = conv(v)
        elif isinstance(node, list):
            return [conv(x) for x in node]
        return node

    return conv(s)


class GeminiProvider(LlmProvider):
    name = "gemini"
    paced = True
    _thinking_ok = True

    @property
    def model(self) -> str:
        return self.settings.gemini_model

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        r = await http().post(API.format(model=self.model), json=body,
                              headers={"x-goog-api-key": self.settings.gemini_api_key or "", "content-type": "application/json"})
        if r.status_code >= 400:
            raise LlmError(f"{r.status_code} {r.text[:500]}")
        return r.json()

    async def _call(self, system: str, prompt: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
        base = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        attempts = [
            ("responseJsonSchema", schema),
            ("responseSchema", to_openapi_schema(schema)),
            (None, None),
        ]
        last: Exception | None = None
        for field, sch in attempts:
            body = copy.deepcopy(base)
            if field:
                body["generationConfig"][field] = sch
            else:
                body["contents"][0]["parts"][0]["text"] = f"{prompt}\n\nReturn ONLY a JSON object matching this JSON Schema:\n{schema}"
            level = self.settings.gemini_thinking_level
            if level and self._thinking_ok:
                body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": level}
            try:
                data = await self._post(body)
            except LlmError as exc:
                last = exc
                if str(exc).startswith("400"):
                    if "thinking" in str(exc).lower() and self._thinking_ok:
                        self._thinking_ok = False  # this model rejects the setting: drop it for the rest of the run
                        try:
                            body["generationConfig"].pop("thinkingConfig", None)
                            data = await self._post(body)
                        except LlmError as exc2:
                            last = exc2
                            if str(exc2).startswith("400"):
                                continue
                            raise
                    else:
                        continue  # schema dialect not accepted by this model → try next form
                else:
                    raise
            um = data.get("usageMetadata") or {}
            llm_base.USAGE.prompt_tokens += int(um.get("promptTokenCount") or 0)
            llm_base.USAGE.output_tokens += int(um.get("candidatesTokenCount") or 0) + int(um.get("thoughtsTokenCount") or 0)
            cands = data.get("candidates") or []
            if not cands:
                raise LlmError(f"gemini returned no candidates: {str(data)[:300]}")
            parts = (cands[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            return parse_json_text(text)
        raise LlmError(f"gemini: all schema modes rejected: {last}")
