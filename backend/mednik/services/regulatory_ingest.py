"""Regulatory label ingestion (spec §3.1): DailyMed SPL XML (primary) + openFDA (fallback / cross-check)."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..clients import DailyMedClient, OpenFdaClient
from ..models import Asset, RegulatorySection
from ..taxonomy import asset_terms
from .label_parsers import TARGET_LOINC, SplSection, parse_all, parse_spl_xml, sections_from_openfda

log = logging.getLogger("mednik.regulatory")

Logger = Callable[[str], None]


async def fetch_label(generic: str, brand: str | None, *, force_refresh: bool = False, say: Logger = log.info) -> dict[str, Any]:
    """Fetch the current USPI for an asset. Returns parsed sections + provenance."""
    dm, ofda = DailyMedClient(), OpenFdaClient()
    result: dict[str, Any] = {"source": None, "set_id": None, "sections": {}, "effective_date": None, "title": None,
                              "labeler": None, "initial_us_approval_year": None, "openfda": None, "errors": []}
    try:
        spl = await dm.find_setid(generic, brand, force_refresh=force_refresh)
        if spl:
            say(f"DailyMed: {spl.get('title', '')[:90]} (setid {spl.get('setid')})")
            xml = await dm.get_spl_xml(spl["setid"], force_refresh=force_refresh)
            if xml:
                doc = parse_spl_xml(xml)
                result.update(source="dailymed", set_id=doc.set_id or spl["setid"], sections=doc.sections,
                              effective_date=doc.effective_date, title=doc.title or spl.get("title"),
                              labeler=doc.labeler, initial_us_approval_year=doc.initial_us_approval_year)
    except Exception as exc:  # noqa: BLE001 - surface and fall back to openFDA
        result["errors"].append(f"dailymed: {exc}")
        say(f"DailyMed error: {exc}")

    try:
        rec = await ofda.find_label(generic, brand, set_id=result["set_id"], force_refresh=force_refresh)
        if rec:
            of = rec.get("openfda") or {}
            result["openfda"] = {
                "set_id": rec.get("set_id"), "effective_time": rec.get("effective_time"),
                "brand_name": of.get("brand_name"), "generic_name": of.get("generic_name"),
                "manufacturer_name": of.get("manufacturer_name"),
            }
            say(f"openFDA: label set_id {rec.get('set_id')} effective {rec.get('effective_time')}")
            if not result["sections"]:
                result.update(source="openfda", set_id=rec.get("set_id"), sections=sections_from_openfda(rec))
                et = rec.get("effective_time")
                result["effective_date"] = f"{et[:4]}-{et[4:6]}-{et[6:8]}" if et and len(et) >= 8 else None
                result["labeler"] = (of.get("manufacturer_name") or [None])[0]
            else:
                # fill any target section DailyMed lacked
                for loinc, sec in sections_from_openfda(rec).items():
                    result["sections"].setdefault(loinc, sec)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"openfda: {exc}")
        say(f"openFDA error: {exc}")
    return result


def persist_label(session: Session, asset: Asset, label: dict[str, Any]) -> dict[str, Any]:
    sections: dict[str, SplSection] = label["sections"]
    terms = asset_terms(asset.generic_name, asset.brand_name, asset.synonyms_json)
    parsed = parse_all(sections, terms)

    asset.spl_set_id = label.get("set_id")
    asset.label_effective_date = label.get("effective_date")
    asset.label_title = label.get("title")
    asset.initial_us_approval_year = label.get("initial_us_approval_year") or asset.initial_us_approval_year
    if label.get("labeler") and not asset.company:
        asset.company = re.sub(r"\s+", " ", label["labeler"]).strip()
    if parsed["mechanism"]:
        asset.mechanism_of_action = parsed["mechanism"]
    asset.approved_indications_json = parsed["indications"]

    session.execute(delete(RegulatorySection).where(RegulatorySection.asset_id == asset.id))
    structured_by_loinc: dict[str, dict[str, Any]] = {
        "34066-1": {"boxed_warning": parsed["boxed_warning"]},
        "34067-9": {"indications": parsed["indications"]},
        "34068-7": {"dosing": parsed["dosing"], "cyp_rules_in_section": [r for r in parsed["cyp"]["rules"] if r["section_loinc"] == "34068-7"]},
        "34070-3": {"contraindications": parsed["contraindications"],
                    "cyp_rules_in_section": [r for r in parsed["cyp"]["rules"] if r["section_loinc"] == "34070-3"]},
        "43685-7": {"monitoring": parsed["monitoring"]},
        "34084-4": {"ae_tables": parsed["ae_tables"]},
        "34073-7": {"cyp": parsed["cyp"]},
        "34092-7": {"clinical_studies": parsed["clinical_studies"]},
    }
    for loinc, sec in sections.items():
        session.add(RegulatorySection(
            asset_id=asset.id, loinc_code=loinc, section_title=sec.title or TARGET_LOINC.get(loinc),
            raw_text=sec.text, structured_json=structured_by_loinc.get(loinc), tables_json=sec.tables,
            effective_date=label.get("effective_date"), source=label.get("source") or "unknown",
            spl_set_id=label.get("set_id"),
        ))
    session.flush()
    return parsed


def load_parsed_label(session: Session, asset: Asset) -> dict[str, Any]:
    """Re-hydrate parsed structures from stored sections (no network)."""
    rows = session.execute(select(RegulatorySection).where(RegulatorySection.asset_id == asset.id)).scalars().all()
    sections = {r.loinc_code: SplSection(r.loinc_code, r.section_title or "", r.raw_text, r.tables_json or []) for r in rows}
    if not sections:
        return {}
    terms = asset_terms(asset.generic_name, asset.brand_name, asset.synonyms_json)
    parsed = parse_all(sections, terms)
    parsed["_meta"] = {"set_id": asset.spl_set_id, "effective_date": asset.label_effective_date,
                       "source": rows[0].source if rows else None}
    return parsed
