"""ClinicalTrials.gov REST API v2 client (handles nextPageToken pagination)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from .base import BaseApiClient


def essie_or(terms: list[str]) -> str:
    out = []
    for t in terms:
        t = t.strip()
        if not t:
            continue
        out.append(f'"{t}"' if (" " in t or "-" in t or "/" in t) else t)
    return " OR ".join(out)


class ClinicalTrialsClient(BaseApiClient):
    source = "ctgov"
    base_url = "https://clinicaltrials.gov/api/v2"
    min_interval_s = 0.7  # registry asks for ~50 req/min

    async def iter_study_pages(
        self,
        *,
        condition: str | None = None,
        intervention_terms: list[str] | None = None,
        term: str | None = None,
        max_studies: int = 300,
        page_size: int | None = None,
        force_refresh: bool = False,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Yield one page of registry records at a time so callers can process and discard them (a page of
        studies with posted results is large; holding 300 of them at once needs hundreds of MB)."""
        size = page_size or self.settings.ctgov_page_size
        params: dict[str, Any] = {"format": "json", "pageSize": size, "countTotal": "true"}
        if condition:
            params["query.cond"] = condition
        if intervention_terms:
            params["query.intr"] = essie_or(intervention_terms)
        if term:
            params["query.term"] = term
        token: str | None = None
        seen = 0
        while True:
            p = dict(params)
            if token:
                p["pageToken"] = token
            data = await self.get_json("/studies", p, force_refresh=force_refresh)
            if not data:
                break
            page = (data.get("studies") or [])[: max(0, max_studies - seen)]
            token = data.get("nextPageToken")
            del data
            seen += len(page)
            if page:
                yield page
            if not token or seen >= max_studies:
                break

    async def search_studies(
        self,
        *,
        condition: str | None = None,
        intervention_terms: list[str] | None = None,
        term: str | None = None,
        max_studies: int = 300,
        page_size: int | None = None,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:
        studies: list[dict[str, Any]] = []
        async for page in self.iter_study_pages(condition=condition, intervention_terms=intervention_terms, term=term,
                                                max_studies=max_studies, page_size=page_size, force_refresh=force_refresh):
            studies += page
        return studies[:max_studies]

    async def get_study(self, nct_id: str, force_refresh: bool = False) -> dict[str, Any] | None:
        return await self.get_json(f"/studies/{nct_id}", {"format": "json"}, force_refresh=force_refresh)
