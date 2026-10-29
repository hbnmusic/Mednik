"""openFDA Drug Label API client (https://api.fda.gov/drug/label.json)."""

from __future__ import annotations

from typing import Any

from .base import BaseApiClient

# openFDA label field → HL7 LOINC section code
OPENFDA_FIELD_TO_LOINC: dict[str, str] = {
    "boxed_warning": "34066-1",
    "indications_and_usage": "34067-9",
    "dosage_and_administration": "34068-7",
    "contraindications": "34070-3",
    "warnings_and_cautions": "43685-7",
    "adverse_reactions": "34084-4",
    "drug_interactions": "34073-7",
    "clinical_studies": "34092-7",
}


class OpenFdaClient(BaseApiClient):
    source = "openfda"
    base_url = "https://api.fda.gov"
    min_interval_s = 0.3  # 240 req/min without key

    def default_params(self) -> dict[str, Any]:
        return {"api_key": self.settings.openfda_api_key} if self.settings.openfda_api_key else {}

    async def search_labels(self, search: str, limit: int = 10, force_refresh: bool = False) -> list[dict[str, Any]]:
        data = await self.get_json("/drug/label.json", {"search": search, "limit": limit}, force_refresh=force_refresh)
        if not data:
            return []
        return data.get("results", []) or []

    async def find_label(self, generic: str, brand: str | None = None, set_id: str | None = None,
                         force_refresh: bool = False) -> dict[str, Any] | None:
        """Return the best-matching current label record for an asset.

        Search order: set_id (exact) → openfda.generic_name / brand_name → spl_product_data_elements.
        """
        candidates: list[dict[str, Any]] = []
        if set_id:
            candidates = await self.search_labels(f'set_id:"{set_id}"', 1, force_refresh)
        if not candidates:
            terms = [f'openfda.generic_name:"{generic}"']
            if brand:
                terms.append(f'openfda.brand_name:"{brand}"')
            candidates = await self.search_labels(" ".join(terms), 25, force_refresh)
        if not candidates:
            q = brand or generic
            candidates = await self.search_labels(f'spl_product_data_elements:"{q}"', 25, force_refresh)
        if not candidates:
            return None
        return max(candidates, key=lambda r: _label_rank(r, generic, brand))


def _label_rank(rec: dict[str, Any], generic: str, brand: str | None) -> tuple:
    of = rec.get("openfda") or {}
    brands = [b.lower() for b in of.get("brand_name", [])]
    generics = [g.lower() for g in of.get("generic_name", [])]
    pde = " ".join(rec.get("spl_product_data_elements", [])).lower()
    brand_hit = bool(brand) and (brand.lower() in brands or brand.lower() in pde)
    generic_hit = any(generic.lower() in g for g in generics) or generic.lower().split(" and ")[0] in pde
    has_studies = "clinical_studies" in rec
    return (brand_hit, generic_hit, has_studies, rec.get("effective_time", ""))


def label_sections(rec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map an openFDA label record onto {loinc: {title, text, tables_html}}."""
    out: dict[str, dict[str, Any]] = {}
    for field, loinc in OPENFDA_FIELD_TO_LOINC.items():
        if field not in rec:
            continue
        text = "\n".join(rec.get(field) or [])
        out[loinc] = {
            "title": field.replace("_", " ").title(),
            "text": text,
            "tables_html": rec.get(f"{field}_table") or [],
        }
    return out
