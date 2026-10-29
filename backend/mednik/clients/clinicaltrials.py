"""ClinicalTrials.gov REST API v2 client (handles nextPageToken pagination)."""

from __future__ import annotations

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

    async def search_studies(
        self,
        *,
        condition: str | None = None,
        intervention_terms: list[str] | None = None,
        term: str | None = None,
        max_studies: int = 300,
        page_size: int = 100,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"format": "json", "pageSize": page_size, "countTotal": "true"}
        if condition:
            params["query.cond"] = condition
        if intervention_terms:
            params["query.intr"] = essie_or(intervention_terms)
        if term:
            params["query.term"] = term
        studies: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            p = dict(params)
            if token:
                p["pageToken"] = token
            data = await self.get_json("/studies", p, force_refresh=force_refresh)
            if not data:
                break
            studies += data.get("studies", []) or []
            token = data.get("nextPageToken")
            if not token or len(studies) >= max_studies:
                break
        return studies[:max_studies]

    async def get_study(self, nct_id: str, force_refresh: bool = False) -> dict[str, Any] | None:
        return await self.get_json(f"/studies/{nct_id}", {"format": "json"}, force_refresh=force_refresh)
