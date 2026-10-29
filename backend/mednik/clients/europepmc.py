"""Europe PMC REST API client (search + open-access full text)."""

from __future__ import annotations

from typing import Any

from .base import BaseApiClient


class EuropePmcClient(BaseApiClient):
    source = "europepmc"
    base_url = "https://www.ebi.ac.uk/europepmc/webservices/rest"
    min_interval_s = 0.15

    async def search(self, query: str, max_results: int = 150, force_refresh: bool = False) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        cursor = "*"
        page_size = min(100, max_results)
        while len(out) < max_results:
            data = await self.get_json(
                "/search",
                {"query": query, "format": "json", "resultType": "core", "pageSize": page_size,
                 "cursorMark": cursor, "email": self.settings.contact_email},
                force_refresh=force_refresh,
            )
            if not data:
                break
            batch = (data.get("resultList") or {}).get("result", []) or []
            out += batch
            nxt = data.get("nextCursorMark")
            if not batch or not nxt or nxt == cursor:
                break
            cursor = nxt
        return out[:max_results]

    async def full_text_xml(self, pmcid: str, force_refresh: bool = False) -> str | None:
        pmcid = pmcid if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"
        return await self.get_text(f"/{pmcid}/fullTextXML", force_refresh=force_refresh)
