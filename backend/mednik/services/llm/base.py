"""Provider-agnostic structured-output LLM interface with response caching."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

import httpx
from sqlalchemy import select

from ...config import Settings, get_settings
from ...db import aux_scope
from ...models import ApiCache

log = logging.getLogger("mednik.llm")


class LlmError(RuntimeError):
    pass


class LlmProvider:
    name = "base"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._sem = asyncio.Semaphore(max(1, settings.llm_max_concurrency))

    @property
    def model(self) -> str:  # pragma: no cover - overridden
        return "none"

    async def _call(self, system: str, prompt: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    async def generate_json(self, system: str, prompt: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
        key = hashlib.sha256(json.dumps([self.name, self.model, system, prompt, schema], sort_keys=True).encode()).hexdigest()
        with aux_scope() as s:
            row = s.execute(select(ApiCache).where(ApiCache.cache_key == key)).scalar_one_or_none()
            if row is not None:
                try:
                    return json.loads(row.body)
                except json.JSONDecodeError:
                    pass
        async with self._sem:
            last: Exception | None = None
            out: dict[str, Any] | None = None
            for attempt in range(4):
                try:
                    out = await self._call(system, prompt, schema, schema_name)
                    break
                except (httpx.TransportError, httpx.TimeoutException) as exc:
                    last = exc
                except LlmError as exc:
                    msg = str(exc)
                    if not (msg.startswith("429") or msg.startswith("5") or any(
                            k in msg.lower() for k in ("overloaded", "resource_exhausted", "unavailable", "rate"))):
                        raise
                    last = exc
                await asyncio.sleep(2 ** attempt * 2)
            if out is None:
                raise LlmError(f"{self.name}: failed after retries: {last}")
        with aux_scope() as s:
            s.add(ApiCache(cache_key=key, source=f"llm:{self.name}", url=self.model, params_json={"schema": schema_name},
                           status_code=200, content_type="application/json", body=json.dumps(out)))
        return out


_client: httpx.AsyncClient | None = None


def http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=20.0))
    return _client


def parse_json_text(text: str) -> dict[str, Any]:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):]
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < 0:
        raise LlmError(f"no JSON object in model output: {text[:200]}")
    return json.loads(t[start: end + 1])


def get_provider(settings: Settings | None = None) -> LlmProvider | None:
    s = settings or get_settings()
    if not s.llm_enabled:
        return None
    if s.llm_provider == "gemini":
        from .gemini import GeminiProvider

        return GeminiProvider(s)
    if s.llm_provider == "anthropic":
        from .anthropic import AnthropicProvider

        return AnthropicProvider(s)
    if s.llm_provider == "openai_compat":
        from .openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(s)
    return None
