"""OpenAI-compatible chat-completions provider (Ollama, vLLM, LM Studio, …) for on-prem extraction."""

from __future__ import annotations

import json
from typing import Any

from .base import LlmError, LlmProvider, http, parse_json_text


class OpenAICompatProvider(LlmProvider):
    name = "openai_compat"

    @property
    def model(self) -> str:
        return self.settings.openai_compat_model

    async def _call(self, system: str, prompt: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": f"{system}\n\nReturn ONLY a JSON object matching this JSON Schema ({schema_name}):\n{json.dumps(schema)}"},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"content-type": "application/json"}
        if self.settings.openai_compat_api_key:
            headers["authorization"] = f"Bearer {self.settings.openai_compat_api_key}"
        r = await http().post(self.settings.openai_compat_base_url.rstrip("/") + "/chat/completions", json=body, headers=headers)
        if r.status_code >= 400:
            raise LlmError(f"{r.status_code} {r.text[:500]}")
        choices = r.json().get("choices") or []
        if not choices:
            raise LlmError("openai_compat: empty choices")
        return parse_json_text(choices[0]["message"]["content"])
