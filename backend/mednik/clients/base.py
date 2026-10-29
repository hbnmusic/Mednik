"""Async HTTP client with retry/backoff, per-source rate limiting and SQLite payload cache.

Every live payload fetched from a federal / literature API is persisted in the
``api_cache`` table so the UI can re-render instantly and work offline after the
first sync (spec §1, rate-limiting / offline-resilience exception).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..db import aux_scope
from ..models import ApiCache

log = logging.getLogger("mednik.http")

_shared_client: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        s = get_settings()
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(s.http_timeout_s, connect=20.0),
            follow_redirects=True,
            headers={"User-Agent": f"mednik/0.1 (medical-affairs evidence prototype; mailto:{s.contact_email})"},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _shared_client


async def aclose_shared_client() -> None:
    global _shared_client
    if _shared_client is not None:
        await _shared_client.aclose()
        _shared_client = None


class ApiError(RuntimeError):
    def __init__(self, source: str, url: str, status: int | None, detail: str):
        super().__init__(f"[{source}] {status} {url}: {detail[:300]}")
        self.source, self.url, self.status, self.detail = source, url, status, detail


@dataclass
class ApiResponse:
    source: str
    url: str
    status: int
    text: str
    content_type: str | None
    from_cache: bool

    def json(self) -> Any:
        return json.loads(self.text)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class RateLimiter:
    def __init__(self, min_interval_s: float):
        self.min_interval_s = min_interval_s
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delta = self.min_interval_s - (now - self._last)
            if delta > 0:
                await asyncio.sleep(delta)
            self._last = time.monotonic()


def cache_key(source: str, url: str, params: dict[str, Any] | None, body: Any | None = None) -> str:
    payload = json.dumps({"s": source, "u": url, "p": sorted((params or {}).items()), "b": body}, default=str, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


class BaseApiClient:
    source: str = "generic"
    base_url: str = ""
    min_interval_s: float = 0.25
    cacheable_statuses: tuple[int, ...] = (200, 404)

    def __init__(self) -> None:
        self.settings = get_settings()
        self.limiter = RateLimiter(self.min_interval_s)

    # -- cache ----------------------------------------------------------------
    def _cache_get(self, key: str) -> ApiResponse | None:
        ttl = timedelta(hours=self.settings.cache_ttl_hours)
        with aux_scope() as s:
            row = s.execute(select(ApiCache).where(ApiCache.cache_key == key)).scalar_one_or_none()
            if row is None:
                return None
            fetched = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - fetched > ttl:
                return None
            return ApiResponse(self.source, row.url, row.status_code, row.body, row.content_type, True)

    def _cache_put(self, key: str, url: str, params: dict | None, resp: ApiResponse) -> None:
        with aux_scope() as s:
            row = s.execute(select(ApiCache).where(ApiCache.cache_key == key)).scalar_one_or_none()
            if row is None:
                row = ApiCache(cache_key=key, source=self.source, url=url, params_json=params or {},
                               status_code=resp.status, content_type=resp.content_type, body=resp.text)
                s.add(row)
            else:
                row.status_code, row.body, row.content_type = resp.status, resp.text, resp.content_type
                row.fetched_at = datetime.now(timezone.utc)

    def cached_only(self, url: str, params: dict | None = None) -> ApiResponse | None:
        return self._cache_get(cache_key(self.source, url, params))

    # -- request --------------------------------------------------------------
    def default_params(self) -> dict[str, Any]:
        return {}

    async def request(
        self,
        url_or_path: str,
        params: dict[str, Any] | None = None,
        *,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        force_refresh: bool = False,
        use_cache: bool = True,
    ) -> ApiResponse:
        url = url_or_path if url_or_path.startswith("http") else f"{self.base_url}{url_or_path}"
        merged = {**self.default_params(), **(params or {})}
        merged = {k: v for k, v in merged.items() if v is not None}
        # never let credentials become part of the cache key
        key_params = {k: v for k, v in merged.items() if k not in ("api_key", "key")}
        key_body = {k: v for k, v in data.items() if k not in ("api_key", "key")} if data else None
        key = cache_key(self.source, url, key_params, key_body)
        if use_cache and not force_refresh:
            hit = self._cache_get(key)
            if hit is not None:
                return hit

        attempts = self.settings.http_max_retries
        last_exc: Exception | None = None
        for attempt in range(attempts):
            await self.limiter.wait()
            try:
                if method == "POST":
                    r = await _client().post(url, data=data if data is not None else merged)
                else:
                    r = await _client().get(url, params=merged)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                await self._backoff(attempt, None)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last_exc = ApiError(self.source, str(r.url), r.status_code, r.text)
                await self._backoff(attempt, r.headers.get("Retry-After"))
                continue
            resp = ApiResponse(self.source, str(r.url), r.status_code, r.text, r.headers.get("content-type"), False)
            if use_cache and r.status_code in self.cacheable_statuses:
                self._cache_put(key, url, key_params, resp)
            return resp
        raise ApiError(self.source, url, getattr(last_exc, "status", None), f"gave up after {attempts} attempts: {last_exc!r}")

    async def _backoff(self, attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = 5.0
        else:
            delay = min(30.0, (2 ** attempt) * 1.0) + random.uniform(0, 0.75)
        log.warning("%s: backing off %.1fs (attempt %d)", self.source, delay, attempt + 1)
        await asyncio.sleep(delay)

    async def get_json(self, url_or_path: str, params: dict[str, Any] | None = None, **kw) -> Any:
        resp = await self.request(url_or_path, params, **kw)
        if resp.status == 404:
            return None
        if not resp.ok:
            raise ApiError(self.source, resp.url, resp.status, resp.text)
        return resp.json()

    async def get_text(self, url_or_path: str, params: dict[str, Any] | None = None, **kw) -> str | None:
        resp = await self.request(url_or_path, params, **kw)
        if resp.status == 404:
            return None
        if not resp.ok:
            raise ApiError(self.source, resp.url, resp.status, resp.text)
        return resp.text
