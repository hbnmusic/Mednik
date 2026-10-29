"""PDF dropzone ingestion (spec §3.3): full-text papers, congress posters, data-on-file tables.

Text and tables are extracted with PyMuPDF, chunked by IMRaD headings, and the
resulting text is passed through the same citation-validated extraction used
for PubMed / Europe PMC. The document's SHA-256 is its ``source_id``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import pymupdf
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Asset, Document, Indication
from ..taxonomy import asset_terms, text_mentions_asset
from ..textutil import clean_ws, sha256_text
from .literature_ingest import evidence_tags, link

log = logging.getLogger("mednik.pdf")

HEADING_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s*)?(abstract|introduction|background|objectives?|aims?|methods?|materials\s+and\s+methods|patients\s+and\s+methods|"
    r"study\s+design|results|findings|discussion|conclusions?|limitations|references|acknowledg\w*|disclosures?)\s*:?\s*$",
    re.I,
)
SECTION_MAP = {"abstract": "ABSTRACT", "introduction": "INTRODUCTION", "background": "INTRODUCTION", "objective": "INTRODUCTION",
               "objectives": "INTRODUCTION", "aim": "INTRODUCTION", "aims": "INTRODUCTION", "method": "METHODS", "methods": "METHODS",
               "materials and methods": "METHODS", "patients and methods": "METHODS", "study design": "METHODS", "results": "RESULTS",
               "findings": "RESULTS", "discussion": "DISCUSSION", "conclusion": "DISCUSSION", "conclusions": "DISCUSSION",
               "limitations": "DISCUSSION", "references": "REFERENCES", "disclosure": "OTHER", "disclosures": "OTHER"}


def extract_pdf(data: bytes) -> dict[str, Any]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages: list[str] = []
    tables: list[dict[str, Any]] = []
    for pno, page in enumerate(doc):
        pages.append(page.get_text("text"))
        try:
            found = page.find_tables()
            for t_i, tab in enumerate(found.tables if hasattr(found, "tables") else found):
                rows = [[clean_ws(c or "") for c in r] for r in (tab.extract() or [])]
                rows = [r for r in rows if any(r)]
                if len(rows) >= 2:
                    tables.append({"page": pno + 1, "index": t_i, "caption": "", "header_rows": [rows[0]], "rows": rows[1:], "footnotes": ""})
        except Exception:  # noqa: BLE001 - table finder is best-effort
            log.debug("table extraction failed on page %s", pno + 1)
    meta = doc.metadata or {}
    doc.close()
    full = "\n".join(pages)
    chunks: list[dict[str, Any]] = []
    current = {"section": "FRONT", "text": []}
    for line in full.splitlines():
        m = HEADING_RE.match(line)
        if m and len(line.strip()) < 45:
            if current["text"]:
                chunks.append({"section": current["section"], "text": clean_ws("\n".join(current["text"]))})
            key = re.sub(r"\s+", " ", m.group(1).lower())
            current = {"section": SECTION_MAP.get(key, key.upper()), "text": []}
            continue
        current["text"].append(line)
    if current["text"]:
        chunks.append({"section": current["section"], "text": clean_ws("\n".join(current["text"]))})
    if len(chunks) <= 1:
        chunks = [{"section": "BODY", "text": clean_ws(full)}]
    title = (meta.get("title") or "").strip() or next((l.strip() for l in full.splitlines() if len(l.strip()) > 20), "Uploaded PDF")
    return {"title": title[:400], "full_text": clean_ws(full), "chunks": chunks, "tables": tables, "n_pages": len(pages)}


def table_text(t: dict[str, Any]) -> str:
    rows = (t.get("header_rows") or []) + (t.get("rows") or [])
    return f"[Table p.{t.get('page')}]\n" + "\n".join(" | ".join(r) for r in rows)


def extraction_text(doc: Document) -> str:
    """Results + abstract + tables first (where endpoints live), then the rest."""
    chunks = doc.chunks_json or []
    order = {"ABSTRACT": 0, "RESULTS": 1, "BODY": 1, "FRONT": 2, "METHODS": 3, "DISCUSSION": 4}
    parts = [c["text"] for c in sorted(chunks, key=lambda c: order.get(c["section"], 5)) if c["section"] not in ("REFERENCES", "OTHER")]
    tabs = [table_text(t) for t in doc.tables_json or []]
    return "\n\n".join(parts[:3] + tabs + parts[3:])


def store_pdf(session: Session, data: bytes, filename: str, role: str, indication: Indication | None,
              assets: list[Asset]) -> tuple[Document, list[Asset]]:
    sha = sha256_text(data)
    doc = session.execute(select(Document).where(Document.sha256 == sha)).scalar_one_or_none()
    ext = extract_pdf(data)
    if doc is None:
        doc = Document(sha256=sha, source_type="pdf", source_id=sha)
        session.add(doc)
    doc.filename = filename
    doc.title = ext["title"]
    doc.full_text = ext["full_text"]
    doc.chunks_json = ext["chunks"]
    doc.tables_json = ext["tables"]
    abstract = next((c["text"] for c in ext["chunks"] if c["section"] == "ABSTRACT"), ext["full_text"][:3000])
    doc.abstract = abstract
    doc.evidence_tags_json = evidence_tags([], ext["title"], ext["full_text"][:4000])
    doc.linked_nct_ids_json = sorted(set(re.findall(r"NCT\d{8}", ext["full_text"])))
    m = re.search(r"\b(20[0-3]\d|19[89]\d)\b", ext["full_text"][:3000])
    doc.year = int(m.group(1)) if m else None
    session.flush()
    mentioned = [a for a in assets if text_mentions_asset(ext["full_text"], asset_terms(a.generic_name, a.brand_name, a.synonyms_json))]
    for a in mentioned or ([assets[0]] if len(assets) == 1 else []):
        link(session, doc, role, a.id, indication.id if indication else None)
    if not mentioned and role in ("mvd_preference", "guideline") and indication:
        link(session, doc, role, None, indication.id)
    session.flush()
    return doc, mentioned
