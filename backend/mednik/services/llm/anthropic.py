"""Anthropic Claude provider (Messages API, forced tool use for structured output)."""

from __future__ import annotations

from typing import Any

from .base import LlmError, LlmProvider, http

API = "https://api.anthropic.com/v1/messages"


class AnthropicProvider(LlmProvider):
    name = "anthropic"

    @property
    def model(self) -> str:
        return self.settings.anthropic_model

    async def _call(self, system: str, prompt: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
        body = {
            "model": self.model,
            "max_tokens": 8000,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [{"name": schema_name, "description": f"Record the {schema_name} extraction.", "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": schema_name},
        }
        r = await http().post(API, json=body, headers={
            "x-api-key": self.settings.anthropic_api_key or "", "anthropic-version": "2023-06-01", "content-type": "application/json"})
        if r.status_code >= 400:
            raise LlmError(f"{r.status_code} {r.text[:500]}")
        for block in r.json().get("content", []):
            if block.get("type") == "tool_use":
                return block.get("input") or {}
        raise LlmError("anthropic: no tool_use block in response")
