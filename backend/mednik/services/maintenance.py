"""Housekeeping for long-running (hosted) deployments.

* Raw API payloads past the cache TTL are deleted, and an optional size cap (MEDNIK_CACHE_MAX_MB) keeps a
  free-tier database (Supabase Free: 500 MB) well clear of its limit. LLM responses are never purged: they
  are small and re-creating them costs API quota.
* Ingest jobs left 'queued' / 'running' by a process that stopped (e.g. a free host spinning down) are marked
  as interrupted so the dashboard does not wait on them forever.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update

from ..config import get_settings
from ..db import aux_scope
from ..models import ApiCache, IngestJob

log = logging.getLogger("mednik.maintenance")


def _not_llm():
    return ~ApiCache.source.like("llm:%")


def purge_api_cache(now: datetime | None = None) -> dict[str, int]:
    st = get_settings()
    now = now or datetime.now(timezone.utc)
    out = {"expired": 0, "over_cap": 0}
    with aux_scope() as s:
        cutoff = now - timedelta(hours=st.cache_ttl_hours)
        out["expired"] = s.execute(delete(ApiCache).where(_not_llm(), ApiCache.fetched_at < cutoff)).rowcount or 0
    if st.cache_max_mb > 0:
        cap = st.cache_max_mb * 1024 * 1024
        with aux_scope() as s:
            total = s.scalar(select(func.coalesce(func.sum(func.length(ApiCache.body)), 0)).where(_not_llm())) or 0
            if total > cap:
                drop: list[int] = []
                rows = s.execute(select(ApiCache.id, func.length(ApiCache.body)).where(_not_llm())
                                 .order_by(ApiCache.fetched_at.asc())).all()
                for rid, size in rows:
                    if total <= cap * 0.8:  # free some headroom so the purge does not run on every job
                        break
                    drop.append(rid)
                    total -= size or 0
                for i in range(0, len(drop), 500):
                    s.execute(delete(ApiCache).where(ApiCache.id.in_(drop[i:i + 500])))
                out["over_cap"] = len(drop)
    if any(out.values()):
        log.info("API cache purge: %s", out)
    return out


def mark_interrupted_jobs() -> int:
    with aux_scope() as s:
        n = s.execute(
            update(IngestJob)
            .where(IngestJob.status.in_(("queued", "running")))
            .values(status="error", error="Interrupted: the server restarted before this sync finished. Run the sync again.",
                    finished_at=datetime.now(timezone.utc))
        ).rowcount or 0
    if n:
        log.info("Marked %d interrupted ingest job(s)", n)
    return n
