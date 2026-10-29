"""FastAPI application — mednik backend (all endpoints under /api/v1)."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import __version__
from ..clients import aclose_shared_client
from ..config import get_settings
from ..db import get_aux_session, get_session, init_db, session_scope
from ..models import (
    Asset,
    ClinicalStudy,
    Document,
    DocumentLink,
    ExtractedEndpoint,
    ExtractionAudit,
    GuidelineRecommendation,
    Indication,
    IngestJob,
    MarketValueDriver,
    RegulatorySection,
    StudyConcept,
)
from ..services.dossier import build_dossier, markdown_to_pdf
from ..services.evidence import serialize_study_brief
from ..services.guidelines import DEFAULT_RULES, extract_and_store_guideline_recs, gap_to_guideline, guideline_status
from ..services.heatmap import build_heatmap, cell_evidence
from ..services.ingest_pipeline import create_job, ensure_asset, ensure_indication, run_ingest
from ..services.literature_ingest import doc_sponsor_class, persist_literature_endpoints
from ..services.maintenance import mark_interrupted_jobs, purge_api_cache
from ..services.matrix import asset_label, build_matrix, node_detail
from ..services.mvd import compute_mvds, effective_scores, serialize_driver
from ..services.pdf_ingest import extraction_text, store_pdf
from ..services.regulatory_ingest import load_parsed_label
from ..services.study_concepts import generate_study_concept
from ..services.tiering import TIER_RULES, TIER_WEIGHTS, VALIDITY_RULES
from ..taxonomy import INDICATIONS, PERSONAS, SCALE_BY_KEY, asset_terms, mvd_profile
from . import auth

log = logging.getLogger("mednik.api")
_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    try:
        mark_interrupted_jobs()
        purge_api_cache()
    except Exception:  # noqa: BLE001 - housekeeping must never block startup
        log.exception("startup housekeeping failed")
    yield
    await aclose_shared_client()


app = FastAPI(title="mednik API", version=__version__, lifespan=lifespan,
              description="Medical Affairs strategic intelligence engine — live FDA / NLM / CT.gov / NCBI / Europe PMC evidence synthesis.")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.middleware("http")(auth.require_login)
app.include_router(auth.router)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def get_indication(s: Session, name_or_key: str) -> Indication:
    ind = s.execute(select(Indication).where((Indication.key == name_or_key) | (Indication.name == name_or_key))).scalars().first()
    if ind is None:
        from ..taxonomy import classify_indication

        k = classify_indication(name_or_key)
        ind = s.execute(select(Indication).where(Indication.key == k)).scalars().first() if k else None
    if ind is None:
        raise HTTPException(404, f"Indication '{name_or_key}' not ingested yet — run /api/v1/ingest first")
    return ind


def get_asset(s: Session, name: str) -> Asset:
    n = name.strip().lower()
    a = s.execute(select(Asset).where((Asset.generic_name == n) | (func.lower(Asset.brand_name) == n))).scalars().first()
    if a is None:
        raise HTTPException(404, f"Asset '{name}' not ingested yet — run /api/v1/ingest first")
    return a


def parse_overrides(raw: str | None) -> dict[str, float] | None:
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return {k: float(v) for k, v in d.items()}
    except (ValueError, AttributeError) as exc:
        raise HTTPException(400, f"overrides must be a JSON object of driver→score: {exc}") from exc


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------


@app.get("/api/v1/health")
def health(s: Session = Depends(get_session)) -> dict[str, Any]:
    st = get_settings()
    return {"status": "ok", "version": __version__, "llm_provider": st.llm_provider, "llm_enabled": st.llm_enabled,
            "auth_required": bool(st.app_password),
            "llm_model": {"gemini": st.gemini_model, "anthropic": st.anthropic_model, "openai_compat": st.openai_compat_model}.get(st.llm_provider),
            "counts": {"assets": s.scalar(select(func.count(Asset.id))), "studies": s.scalar(select(func.count(ClinicalStudy.id))),
                       "endpoints": s.scalar(select(func.count(ExtractedEndpoint.id))), "documents": s.scalar(select(func.count(Document.id)))}}


@app.get("/api/v1/meta")
def meta() -> dict[str, Any]:
    return {"indications": [{"key": d.key, "label": d.label, "icd10": d.icd10} for d in INDICATIONS],
            "personas": {k: v["label"] for k, v in PERSONAS.items()}, "tiers": {k: {"weight": TIER_WEIGHTS[k], "rule": v} for k, v in TIER_RULES.items()},
            "validity_levels": VALIDITY_RULES, "scales": {k: {"label": v.label, "kind": v.kind} for k, v in SCALE_BY_KEY.items()},
            "guideline_rules": DEFAULT_RULES}


@app.get("/api/v1/indications")
def list_indications(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    out = []
    for ind in s.execute(select(Indication).order_by(Indication.name)).scalars().all():
        n_mvd = s.scalar(select(func.count(MarketValueDriver.id)).where(MarketValueDriver.indication_id == ind.id))
        n_st = s.scalar(select(func.count(ClinicalStudy.id)).where(ClinicalStudy.indication_key == ind.key))
        if n_mvd or n_st:
            out.append({"id": ind.id, "name": ind.name, "key": ind.key, "icd10": ind.icd10_code, "n_drivers": n_mvd, "n_studies": n_st})
    return out


@app.get("/api/v1/assets")
def list_assets(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    out = []
    for a in s.execute(select(Asset).order_by(Asset.generic_name)).scalars().all():
        out.append({"id": a.id, "generic": a.generic_name, "brand": a.brand_name, "label": asset_label(a), "company": a.company,
                    "spl_set_id": a.spl_set_id, "label_effective_date": a.label_effective_date, "initial_us_approval_year": a.initial_us_approval_year,
                    "approved_indications": a.approved_indications_json, "mechanism": a.mechanism_of_action,
                    "n_studies": s.scalar(select(func.count(ClinicalStudy.id)).where(ClinicalStudy.asset_id == a.id)),
                    "n_endpoints": s.scalar(select(func.count(ExtractedEndpoint.id)).where(ExtractedEndpoint.asset_id == a.id))})
    return out


# ---------------------------------------------------------------------------
# ingestion
# ---------------------------------------------------------------------------


class AssetIn(BaseModel):
    generic: str
    brand: str | None = None
    synonyms: list[str] | None = None


class IngestIn(BaseModel):
    indication: str = Field(examples=["Schizophrenia"])
    assets: list[AssetIn] = Field(min_length=1)
    force_refresh: bool = False
    include_literature: bool = True
    include_mvd: bool = True
    include_guidelines: bool = True
    max_trials: int | None = None
    max_pubs: int | None = None
    max_extract_docs: int | None = 40


@app.post("/api/v1/ingest")
async def ingest(body: IngestIn, wait: bool = Query(False, description="Run synchronously and return the summary")) -> dict[str, Any]:
    req = body.model_dump()
    jid = create_job(req)
    if wait:
        summary = await run_ingest(jid, req)
        return {"job_id": jid, "summary": summary}
    t = asyncio.create_task(_ingest_then_tidy(jid, req))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return {"job_id": jid, "status": "queued"}


async def _ingest_then_tidy(jid: str, req: dict[str, Any]) -> dict[str, Any]:
    try:
        return await run_ingest(jid, req)
    finally:
        try:
            await asyncio.to_thread(purge_api_cache)
        except Exception:  # noqa: BLE001
            log.exception("cache purge failed")


@app.get("/api/v1/jobs/{job_id}")
def job_status(job_id: str, s: Session = Depends(get_aux_session)) -> dict[str, Any]:
    job = s.get(IngestJob, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return {"id": job.id, "status": job.status, "request": job.request_json, "log": job.log_json or [], "summary": job.summary_json,
            "error": job.error, "started_at": job.started_at, "finished_at": job.finished_at}


@app.get("/api/v1/jobs")
def jobs(s: Session = Depends(get_aux_session)) -> list[dict[str, Any]]:
    rows = s.execute(select(IngestJob).order_by(IngestJob.started_at.desc()).limit(20)).scalars().all()
    return [{"id": j.id, "status": j.status, "request": j.request_json, "started_at": j.started_at, "finished_at": j.finished_at} for j in rows]


@app.post("/api/v1/ingest/pdf")
async def ingest_pdf(file: UploadFile = File(...), indication: str = Form(...), assets: str = Form("", description="comma-separated generic names"),
                     role: Literal["asset_evidence", "mvd_preference", "guideline"] = Form("asset_evidence")) -> dict[str, Any]:
    data = await file.read()
    if not data[:5] == b"%PDF-":
        raise HTTPException(400, "Not a PDF")
    with session_scope() as s:
        ind = ensure_indication(s, indication)
        names = [a.strip() for a in assets.split(",") if a.strip()]
        asset_objs = [ensure_asset(s, n, None, None) for n in names] or s.execute(select(Asset)).scalars().all()
        doc, mentioned = store_pdf(s, data, file.filename or "upload.pdf", role, ind, asset_objs)
        doc_id, sha, ind_id = doc.id, doc.sha256, ind.id
        text = extraction_text(doc)
        targets = [(a.id, a.generic_name, asset_terms(a.generic_name, a.brand_name, a.synonyms_json), a.company) for a in (mentioned or asset_objs[:1])]
        info = {"document_id": doc_id, "sha256": sha, "title": doc.title, "chunks": [c["section"] for c in doc.chunks_json or []],
                "tables": len(doc.tables_json or []), "assets_mentioned": [a.generic_name for a in mentioned], "role": role}
    from ..services.extraction import extract_endpoints

    if role == "asset_evidence":
        total = 0
        for aid, generic, terms, company in targets:
            items = await extract_endpoints([(sha, text)], terms, indication)
            for it in items:
                it["_method"] = "llm_pdf" if get_settings().llm_enabled else "regex_pdf"
            with session_scope() as s:
                d = s.get(Document, doc_id)
                total += persist_literature_endpoints(s, aid, ind_id, items, {sha: d}, {d.id: doc_sponsor_class(d, company, {})})
        info["endpoints_extracted"] = total
    elif role == "mvd_preference":
        with session_scope() as s:
            drivers = await compute_mvds(s, s.get(Indication, ind_id))
            info["drivers_recomputed"] = len(drivers)
    elif role == "guideline":
        with session_scope() as s:
            ind_o = s.get(Indication, ind_id)
            res = await extract_and_store_guideline_recs(s, ind_o, s.execute(select(Asset)).scalars().all(), [s.get(Document, doc_id)])
            info["guideline"] = res
    return info


# ---------------------------------------------------------------------------
# Module 1 — MVD blueprint
# ---------------------------------------------------------------------------


@app.get("/api/v1/mvd")
def get_mvd(indication: str, persona: str = "evidence", overrides: str | None = None, s: Session = Depends(get_session)) -> dict[str, Any]:
    ind = get_indication(s, indication)
    drivers = s.execute(select(MarketValueDriver).where(MarketValueDriver.indication_id == ind.id)).scalars().all()
    eff = effective_scores(drivers, persona, parse_overrides(overrides))
    items = sorted((serialize_driver(d, eff[d.attribute_key]) for d in drivers), key=lambda x: -x["effective_score"])
    return {"indication": {"id": ind.id, "name": ind.name, "key": ind.key}, "persona": persona,
            "personas": {k: v["label"] for k, v in PERSONAS.items()}, "drivers": items,
            "profile": list(mvd_profile(ind.key)),
            "method": "Preference index from DCE/conjoint/survey extraction (75%) + guideline mention share (25%), rescaled to 0.10–1.00."}


class OverrideIn(BaseModel):
    score: float | None = Field(default=None, ge=0.1, le=1.0, description="null clears the saved override")


@app.put("/api/v1/mvd/{driver_id}")
def set_override(driver_id: int, body: OverrideIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    d = s.get(MarketValueDriver, driver_id)
    if d is None:
        raise HTTPException(404, "driver not found")
    d.user_override_score = body.score
    return serialize_driver(d)


@app.post("/api/v1/mvd/recompute")
async def recompute_mvd(indication: str) -> dict[str, Any]:
    with session_scope() as s:
        ind = get_indication(s, indication)
        drivers = await compute_mvds(s, ind)
        return {"drivers": [serialize_driver(d) for d in drivers]}


# ---------------------------------------------------------------------------
# Module 2 — matrix (+ Adjacency A & B in node detail)
# ---------------------------------------------------------------------------


@app.get("/api/v1/matrix")
def matrix(indication: str, asset_a: str, comparators: str, persona: str = "evidence", overrides: str | None = None,
           tier_filter: Literal["all", "tier1"] = "all", s: Session = Depends(get_session)) -> dict[str, Any]:
    ind = get_indication(s, indication)
    a = get_asset(s, asset_a)
    comps = [get_asset(s, c) for c in comparators.split(",") if c.strip() and c.strip().lower() != a.generic_name]
    if not comps:
        raise HTTPException(400, "at least one comparator required")
    return build_matrix(s, ind, a, comps, persona=persona, overrides=parse_overrides(overrides), tier_filter=tier_filter)


@app.get("/api/v1/matrix/node")
def matrix_node(indication: str, asset_a: str, asset_b: str, mvd_key: str, persona: str = "evidence", overrides: str | None = None,
                tier_filter: Literal["all", "tier1"] = "all", s: Session = Depends(get_session)) -> dict[str, Any]:
    ind = get_indication(s, indication)
    return node_detail(s, ind, get_asset(s, asset_a), get_asset(s, asset_b), mvd_key, persona=persona,
                       overrides=parse_overrides(overrides), tier_filter=tier_filter)


@app.get("/api/v1/regulatory/{asset}")
def regulatory(asset: str, s: Session = Depends(get_session)) -> dict[str, Any]:
    a = get_asset(s, asset)
    secs = s.execute(select(RegulatorySection).where(RegulatorySection.asset_id == a.id)).scalars().all()
    parsed = load_parsed_label(s, a)
    return {"asset": asset_label(a), "spl_set_id": a.spl_set_id, "effective_date": a.label_effective_date,
            "sections": [{"loinc": x.loinc_code, "title": x.section_title, "source": x.source, "chars": len(x.raw_text),
                          "tables": len(x.tables_json or []), "structured": x.structured_json} for x in secs],
            "parsed": {k: v for k, v in parsed.items() if k not in ("ae_tables",)} if parsed else None}


@app.get("/api/v1/regulatory/{asset}/section/{loinc}")
def regulatory_section(asset: str, loinc: str, s: Session = Depends(get_session)) -> dict[str, Any]:
    a = get_asset(s, asset)
    x = s.execute(select(RegulatorySection).where(RegulatorySection.asset_id == a.id, RegulatorySection.loinc_code == loinc)).scalars().first()
    if x is None:
        raise HTTPException(404, "section not found")
    return {"loinc": x.loinc_code, "title": x.section_title, "text": x.raw_text, "tables": x.tables_json, "structured": x.structured_json}


@app.get("/api/v1/trials")
def trials(asset: str, indication: str | None = None, sponsor_class: str | None = None, phase_min: int | None = None,
           s: Session = Depends(get_session)) -> dict[str, Any]:
    a = get_asset(s, asset)
    q = select(ClinicalStudy).where(ClinicalStudy.asset_id == a.id, ClinicalStudy.source == "ctgov")
    if indication:
        q = q.where(ClinicalStudy.indication_key == get_indication(s, indication).key)
    if sponsor_class:
        q = q.where(ClinicalStudy.sponsor_class == sponsor_class)
    if phase_min:
        q = q.where(ClinicalStudy.phase_max >= phase_min)
    rows = s.execute(q.order_by(ClinicalStudy.phase_max.desc(), ClinicalStudy.start_year.desc())).scalars().all()
    split: dict[str, int] = {}
    for r in rows:
        k = f"{r.sponsor_class}|{r.phase or 'NA'}"
        split[k] = split.get(k, 0) + 1
    return {"asset": asset_label(a), "n": len(rows), "split": split, "trials": [serialize_study_brief(r) | {"sponsor_class_raw": r.sponsor_class_raw} for r in rows]}


# ---------------------------------------------------------------------------
# Module 3 — heatmap (+ Adjacency C)
# ---------------------------------------------------------------------------


@app.get("/api/v1/heatmap")
def heatmap(asset: str, indication: str | None = None, persona: str = "evidence", s: Session = Depends(get_session)) -> dict[str, Any]:
    a = get_asset(s, asset)
    keys = [get_indication(s, indication).key] if indication else None
    return build_heatmap(s, a, indication_keys=keys, persona=persona)


@app.get("/api/v1/heatmap/cell")
def heatmap_cell(asset: str, row: str, column: str, s: Session = Depends(get_session)) -> dict[str, Any]:
    return cell_evidence(s, get_asset(s, asset), row, column)


class ConceptIn(BaseModel):
    asset: str
    row: str
    column: str
    cell_state: str = "white_space"
    force_refresh: bool = False


@app.post("/api/v1/study-concept")
async def study_concept(body: ConceptIn) -> dict[str, Any]:
    with session_scope() as s:
        a = get_asset(s, body.asset)
        comps = [x for x in s.execute(select(Asset)).scalars().all() if x.id != a.id]
        return await generate_study_concept(s, a, body.row, body.column, comps, cell_state=body.cell_state, force_refresh=body.force_refresh)


@app.get("/api/v1/study-concept/{concept_id}.md", response_class=PlainTextResponse)
def study_concept_md(concept_id: int, s: Session = Depends(get_session)) -> Response:
    c = s.get(StudyConcept, concept_id)
    if c is None:
        raise HTTPException(404, "concept not found")
    return PlainTextResponse(c.markdown, headers={"Content-Disposition": f'attachment; filename="mednik-study-concept-{concept_id}.md"'})


# ---------------------------------------------------------------------------
# Adjacency D — guidelines
# ---------------------------------------------------------------------------


@app.get("/api/v1/guidelines")
def guidelines(indication: str, asset: str | None = None, s: Session = Depends(get_session)) -> dict[str, Any]:
    ind = get_indication(s, indication)
    assets = [get_asset(s, asset)] if asset else s.execute(select(Asset)).scalars().all()
    out = []
    for a in assets:
        hm = build_heatmap(s, a, indication_keys=[ind.key])
        out.append({"asset": asset_label(a), "generic": a.generic_name, "status": guideline_status(s, ind, a), "gap_to_guideline": gap_to_guideline(s, ind, a, hm)})
    recs = s.execute(select(GuidelineRecommendation).where(GuidelineRecommendation.indication_id == ind.id)).scalars().all()
    docs = s.execute(select(Document).join(DocumentLink).where(DocumentLink.indication_id == ind.id, DocumentLink.role == "guideline")).scalars().unique().all()
    return {"indication": ind.name, "assets": out, "n_recommendations": len(recs),
            "documents": sorted([{"id": d.source_id, "pmid": d.pmid, "title": d.title, "year": d.year, "journal": d.journal, "full_text": bool(d.full_text),
                                  "source_type": d.source_type} for d in docs], key=lambda d: -(d["year"] or 0))[:30]}


class ManualGuidelineIn(BaseModel):
    indication: str
    asset: str
    guideline_body: str
    publication_year: int
    line: Literal["FIRST", "SECOND", "THIRD", "NOT_RECOMMENDED", "INSUFFICIENT", "UNCLEAR"]
    role: Literal["MONOTHERAPY", "ADJUNCTIVE", "EITHER", "UNSPECIFIED"] = "UNSPECIFIED"
    indication_phase: Literal["ACUTE", "MAINTENANCE", "UNSPECIFIED"] = "UNSPECIFIED"
    level_of_evidence: str | None = None
    verbatim_quote: str = Field(min_length=15, description="Exact text from the guideline (required — citation constraint)")
    source_id: str = Field(description="PMID, DOI or document reference for the quote")


@app.post("/api/v1/guidelines/manual")
def guideline_manual(body: ManualGuidelineIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    ind = get_indication(s, body.indication)
    a = get_asset(s, body.asset)
    r = GuidelineRecommendation(indication_id=ind.id, asset_id=a.id, asset_mention=a.generic_name, guideline_body=body.guideline_body,
                                publication_year=body.publication_year, indication_phase=body.indication_phase, line=body.line, role=body.role,
                                level_of_evidence=body.level_of_evidence, verbatim_quote=body.verbatim_quote, source_id=body.source_id,
                                source_type="user_curated")
    s.add(r)
    s.flush()
    return {"id": r.id, "status": guideline_status(s, ind, a)}


# ---------------------------------------------------------------------------
# audit + export
# ---------------------------------------------------------------------------


@app.get("/api/v1/audit")
def audit(limit: int = 200, s: Session = Depends(get_aux_session)) -> dict[str, Any]:
    rows = s.execute(select(ExtractionAudit).order_by(ExtractionAudit.id.desc()).limit(limit)).scalars().all()
    acc = s.scalar(select(func.count(ExtractionAudit.id)).where(ExtractionAudit.accepted.is_(True)))
    rej = s.scalar(select(func.count(ExtractionAudit.id)).where(ExtractionAudit.accepted.is_(False)))
    return {"accepted": acc, "rejected": rej,
            "recent": [{"source_id": r.source_id, "schema": r.schema_name, "provider": r.provider, "accepted": r.accepted, "reason": r.reason,
                        "quote": (r.item_json or {}).get("verbatim_quote", "")[:300]} for r in rows]}


@app.get("/api/v1/export/dossier")
def export_dossier(indication: str, asset_a: str, comparators: str, format: Literal["md", "pdf"] = "md", persona: str = "evidence",
                   overrides: str | None = None, tier_filter: Literal["all", "tier1"] = "all", s: Session = Depends(get_session)) -> Response:
    ind = get_indication(s, indication)
    a = get_asset(s, asset_a)
    comps = [get_asset(s, c) for c in comparators.split(",") if c.strip() and c.strip().lower() != a.generic_name]
    md = build_dossier(s, ind, a, comps, persona=persona, overrides=parse_overrides(overrides), tier_filter=tier_filter)
    fname = f"mednik-dossier-{a.generic_name.replace(' ', '-')}-{ind.key}"
    if format == "pdf":
        return Response(markdown_to_pdf(md), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{fname}.pdf"'})
    return PlainTextResponse(md, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="{fname}.md"'})


@app.exception_handler(Exception)
async def unhandled(_, exc: Exception):  # pragma: no cover
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


# The built dashboard (frontend/out) is served from the same origin when MEDNIK_STATIC_DIR is set (container
# deployments). Mounted last so every /api route above takes precedence.
_static = get_settings().static_dir
if _static and _static.is_dir():
    app.mount("/", StaticFiles(directory=str(_static), html=True), name="dashboard")
