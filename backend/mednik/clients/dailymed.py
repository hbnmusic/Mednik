"""NLM DailyMed REST API v2 client."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .base import BaseApiClient


class DailyMedClient(BaseApiClient):
    source = "dailymed"
    base_url = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
    min_interval_s = 0.25

    async def search_spls(self, drug_name: str, force_refresh: bool = False) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while page <= 5:
            data = await self.get_json("/spls.json", {"drug_name": drug_name, "pagesize": 100, "page": page},
                                       force_refresh=force_refresh)
            if not data:
                break
            out += data.get("data", []) or []
            meta = data.get("metadata", {}) or {}
            total_pages = int(meta.get("total_pages") or 1)
            if page >= total_pages:
                break
            page += 1
        return out

    async def find_setid(self, generic: str, brand: str | None = None, force_refresh: bool = False) -> dict[str, Any] | None:
        rows = await self.search_spls(brand or generic, force_refresh)
        if not rows and brand:
            rows = await self.search_spls(generic, force_refresh)
        if not rows:
            return None
        return max(rows, key=lambda r: _spl_rank(r, generic, brand))

    async def get_spl_xml(self, setid: str, force_refresh: bool = False) -> str | None:
        return await self.get_text(f"/spls/{setid}.xml", force_refresh=force_refresh)


def _parse_date(s: str | None) -> datetime:
    for fmt in ("%b %d, %Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s or "", fmt)
        except ValueError:
            continue
    return datetime(1900, 1, 1)


def _spl_rank(row: dict[str, Any], generic: str, brand: str | None) -> tuple:
    title = (row.get("title") or "").upper()
    brand_start = bool(brand) and title.startswith(brand.upper())
    brand_any = bool(brand) and brand.upper() in title
    generic_any = generic.upper().split(" AND ")[0] in title
    # repackager / relabeler titles usually carry a bracketed labeler that differs; prefer shorter titles
    repack = bool(re.search(r"REPACKAG|RELABEL|PHYSICIANS TOTAL CARE|BRYANT RANCH|A-S MEDICATION|QUALITY CARE|HJ HARKINS|NUCARE|DIRECT RX|PROFICIENT RX|HENRY SCHEIN", title))
    return (brand_start, brand_any, not repack, generic_any, _parse_date(row.get("published_date")), -len(title))
