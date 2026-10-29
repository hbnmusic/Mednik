"""NCBI Entrez E-utilities client (esearch + efetch, PubMed XML)."""

from __future__ import annotations

from typing import Any

from .base import BaseApiClient


class PubMedClient(BaseApiClient):
    source = "pubmed"
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def __init__(self) -> None:
        super().__init__()
        # NCBI: 3 req/s without a key, 10 req/s with one
        self.limiter.min_interval_s = 0.12 if self.settings.ncbi_api_key else 0.36

    def default_params(self) -> dict[str, Any]:
        p: dict[str, Any] = {"tool": "mednik", "email": self.settings.contact_email}
        if self.settings.ncbi_api_key:
            p["api_key"] = self.settings.ncbi_api_key
        return p

    async def esearch(self, term: str, retmax: int = 100, sort: str = "relevance", force_refresh: bool = False) -> list[str]:
        data = await self.get_json(
            "/esearch.fcgi",
            {"db": "pubmed", "term": term, "retmax": retmax, "retmode": "json", "sort": sort},
            force_refresh=force_refresh,
        )
        if not data:
            return []
        return list(data.get("esearchresult", {}).get("idlist", []) or [])

    async def efetch_xml(self, pmids: list[str], force_refresh: bool = False) -> list[str]:
        """Fetch PubMed XML in batches of 100 (POST keeps long id lists out of the URL)."""
        out: list[str] = []
        for i in range(0, len(pmids), 100):
            batch = pmids[i : i + 100]
            resp = await self.request(
                "/efetch.fcgi",
                method="POST",
                data={**self.default_params(), "db": "pubmed", "id": ",".join(batch), "retmode": "xml"},
                force_refresh=force_refresh,
            )
            if resp.ok:
                out.append(resp.text)
        return out
