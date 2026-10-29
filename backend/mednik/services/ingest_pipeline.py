"""/api/v1/ingest orchestration: labels → registry → label linkage → literature → MVDs → guidelines."""

from __future__ import annotations

import logging
import re
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import aux_scope, session_scope
from ..models import Asset, ClinicalStudy, Document, Indication, IngestJob
from ..taxonomy import INDICATION_BY_KEY, asset_terms, classify_indication
from .extraction import extract_endpoints
from .guidelines import ingest_guidelines
from .llm import base as llm_base
from .label_evidence import link_registry_to_label, mark_replication, persist_label_evidence
from .literature_ingest import (
    asset_doc_is_extractable,
    doc_sponsor_class,
    doc_text,
    epmc_asset_query,
    persist_literature_endpoints,
    pubmed_asset_term,
    search_corpus,
    store_corpus,
)
from .mvd import compute_mvds, ingest_mvd_literature
from .regulatory_ingest import fetch_label, persist_label
from .trials_ingest import ingest_trials_for_asset

log = logging.getLogger("mednik.pipeline")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def ensure_indication(session: Session, name: str) -> Indication:
    key = classify_indication(name) or slug(name)
    ind = session.execute(select(Indication).where(Indication.key == key)).scalars().first()
    if ind is None:
        ind = session.execute(select(Indication).where(Indication.name == name)).scalar_one_or_none()
    if ind is None:
        d = INDICATION_BY_KEY.get(key)
        ind = Indication(name=name.strip(), key=key, icd10_code=d.icd10 if d else None)
        session.add(ind)
        session.flush()
    return ind


def ensure_asset(session: Session, generic: str, brand: str | None, synonyms: list[str] | None) -> Asset:
    g = generic.strip().lower()
    a = session.execute(select(Asset).where(Asset.generic_name == g)).scalar_one_or_none()
    if a is None:
        a = Asset(generic_name=g, brand_name=brand.strip() if brand else None, synonyms_json=synonyms or [])
        session.add(a)
        session.flush()
    else:
        if brand and not a.brand_name:
            a.brand_name = brand.strip()
        if synonyms:
            a.synonyms_json = sorted(set((a.synonyms_json or []) + synonyms))
    return a


class JobLogger:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.lines: list[dict[str, Any]] = []

    def __call__(self, msg: str) -> None:
        log.info("[%s] %s", self.job_id[:8], msg)
        self.lines.append({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "msg": msg})
        with aux_scope() as s:
            job = s.get(IngestJob, self.job_id)
            if job:
                job.log_json = list(self.lines)


def create_job(request: dict[str, Any]) -> str:
    jid = str(uuid.uuid4())
    with aux_scope() as s:
        s.add(IngestJob(id=jid, status="queued", request_json=request, log_json=[]))
    return jid


async def run_ingest(job_id: str, request: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    say = JobLogger(job_id)
    with aux_scope() as s:
        job = s.get(IngestJob, job_id)
        job.status = "running"
    llm_base.reset_usage()
    summary: dict[str, Any] = {"assets": {}, "llm_provider": settings.llm_provider if settings.llm_enabled else "none (deterministic only)"}
    force = bool(request.get("force_refresh"))
    try:
        with session_scope() as s:
            ind = ensure_indication(s, request["indication"])
            assets = [ensure_asset(s, a["generic"], a.get("brand"), a.get("synonyms")) for a in request["assets"]]
            ind_id, asset_ids = ind.id, [a.id for a in assets]
            # every indication key we may meet in registry records gets a row so studies can be linked
            for d in INDICATION_BY_KEY.values():
                if not s.execute(select(Indication).where(Indication.key == d.key)).scalars().first():
                    s.add(Indication(name=d.label, key=d.key, icd10_code=d.icd10))
        say(f"Indication: {request['indication']} → key '{ind.key}'. Assets: {', '.join(a['generic'] for a in request['assets'])}")
        say(f"LLM extraction: {summary['llm_provider']}")

        # ---- 1. regulatory labels --------------------------------------------------
        for aid in asset_ids:
            with session_scope() as s:
                a = s.get(Asset, aid)
                generic, brand = a.generic_name, a.brand_name
            label = await fetch_label(generic, brand, force_refresh=force, say=say)
            with session_scope() as s:
                a = s.get(Asset, aid)
                if not label["sections"]:
                    say(f"⚠ no label found for {generic}")
                    summary["assets"][generic] = {"label": None}
                    continue
                parsed = persist_label(s, a, label)
                inds = {i.key: i for i in s.execute(select(Indication)).scalars().all()}
                le = persist_label_evidence(s, a, parsed, inds)
                kc = parsed["cyp"]["key_classes"]
                summary["assets"][generic] = {
                    "label": {"source": label["source"], "set_id": label["set_id"], "effective_date": label["effective_date"],
                              "sections": sorted(label["sections"].keys()), "cyp_key_classes": kc,
                              "boxed_warning": parsed["boxed_warning"]["present"], "approved_indications": [i["indication_key"] for i in parsed["indications"]],
                              **le},
                }
                say(f"USPI {brand or generic}: {len(label['sections'])} sections; CYP3A4 strong inhibitor → {kc['strong_cyp3a4_inhibitor']}, "
                    f"inducer → {kc['cyp3a4_inducer']}; {le['label_efficacy_endpoints']} §14 + {le['label_ae_endpoints']} §6 endpoints")

        # ---- 2. registry -------------------------------------------------------------
        with session_scope() as s:
            all_assets = s.execute(select(Asset).where(Asset.id.in_(asset_ids))).scalars().all()
            terms_by_asset = {a.generic_name: asset_terms(a.generic_name, a.brand_name, a.synonyms_json) for a in all_assets}
        for aid in asset_ids:
            with session_scope() as s:
                a = s.get(Asset, aid)
                inds = {i.key: i for i in s.execute(select(Indication)).scalars().all()}
                res = await ingest_trials_for_asset(s, a, terms_by_asset[a.generic_name], terms_by_asset, inds,
                                                    max_trials=request.get("max_trials") or settings.max_trials_per_query, force_refresh=force, say=say)
                n_link = link_registry_to_label(s, a)
                n_rep = mark_replication(s, a)
                summary["assets"].setdefault(a.generic_name, {})["trials"] = {**res, "label_linked_results": n_link, "replicated_primary_endpoints": n_rep}
                say(f"  {a.generic_name}: {n_link} registry results matched to USPI §14; {n_rep} replicated Phase 3 primary endpoints")

        # ---- 3. literature per asset ---------------------------------------------------
        if request.get("include_literature", True):
            for aid in asset_ids:
                with session_scope() as s:
                    a = s.get(Asset, aid)
                    terms = terms_by_asset[a.generic_name]
                    company = a.company
                corpus = await search_corpus(pubmed_asset_term(terms[:6], ind.name), epmc_asset_query(terms[:6], ind.name),
                                             max_pubmed=request.get("max_pubs") or settings.max_pubmed_per_query,
                                             max_epmc=settings.max_epmc_per_query, force_refresh=force, say=say)
                with session_scope() as s:
                    a = s.get(Asset, aid)
                    docs = store_corpus(s, corpus, "asset_evidence", a.id, ind_id)
                    trial_sponsors = {st.nct_id: st.sponsor_class for st in s.execute(select(ClinicalStudy).where(
                        ClinicalStudy.asset_id == a.id, ClinicalStudy.source == "ctgov")).scalars().all() if st.nct_id}
                    extractable = [d for d in docs if asset_doc_is_extractable(d, terms)]
                    extractable.sort(key=lambda d: (-len(set(d.evidence_tags_json or []) & {"RCT", "PHASE3", "POST_HOC", "POOLED", "META_ANALYSIS", "NMA", "RWE"}), -(d.year or 0)))
                    extractable = extractable[: int(request.get("max_extract_docs") or (settings.llm_max_asset_docs if settings.llm_enabled else 40))]
                    payload = [(d.pmid or d.source_id, doc_text(d)) for d in extractable]
                    sponsor_by_doc = {d.id: doc_sponsor_class(d, company, trial_sponsors) for d in extractable}
                    docs_by_sid = {(d.pmid or d.source_id): d for d in extractable}
                    # remove previously extracted literature endpoints for this asset/indication (idempotent re-run)
                    old = s.execute(select(ClinicalStudy).where(ClinicalStudy.asset_id == a.id, ClinicalStudy.source.in_(("pubmed", "epmc")),
                                                                ClinicalStudy.indication_id == ind_id)).scalars().all()
                    for st in old:
                        s.delete(st)
                    s.flush()
                    n_docs = len(docs)
                items = await extract_endpoints(payload, terms, ind.name) if payload else []
                with session_scope() as s:
                    docs_by_sid = {sid: s.get(Document, d.id) for sid, d in docs_by_sid.items()}
                    n_ep = persist_literature_endpoints(s, aid, ind_id, items, docs_by_sid, sponsor_by_doc)
                summary["assets"].setdefault(terms[0], {})["literature"] = {"documents": n_docs, "extracted_docs": len(payload), "endpoints": n_ep}
                say(f"  {terms[0]}: {n_docs} publications stored; {n_ep} citation-validated endpoints from {len(payload)} documents")

        # ---- 4. MVD blueprint ----------------------------------------------------------
        if request.get("include_mvd", True):
            with session_scope() as s:
                ind_o = s.get(Indication, ind_id)
                await ingest_mvd_literature(s, ind_o, max_pubmed=80, max_epmc=80, force_refresh=force, say=say)
            with session_scope() as s:
                ind_o = s.get(Indication, ind_id)
                drivers = await compute_mvds(s, ind_o, say=say, max_docs=request.get("max_mvd_docs"))
                summary["mvd"] = {d.attribute_key: d.mvd_impact_score for d in drivers}
                say("MVD blueprint: " + ", ".join(f"{d.attribute_key}={d.mvd_impact_score:.2f}" for d in sorted(drivers, key=lambda d: -d.mvd_impact_score)[:6]) + " …")

        # ---- 5. guidelines -----------------------------------------------------------------
        if request.get("include_guidelines", True):
            with session_scope() as s:
                ind_o = s.get(Indication, ind_id)
                assets_o = s.execute(select(Asset).where(Asset.id.in_(asset_ids))).scalars().all()
                g = await ingest_guidelines(s, ind_o, assets_o, max_docs=request.get("max_guideline_docs"), force_refresh=force, say=say)
                summary["guidelines"] = g
                say(f"Guidelines: {g['recommendations']} positioning statements extracted from {g['documents_mentioning_assets']} documents")

        summary["llm_usage"] = llm_base.usage_snapshot()
        u = summary["llm_usage"]
        if u["daily_quota_exhausted"]:
            say("Gemini's daily request limit was reached, so the remaining documents were skipped. Finished requests are cached: "
                "run the sync again after midnight Pacific time (3 a.m. Eastern) and it will continue from there, or use a model with a higher limit.")
        if u["calls"] or u["cache_hits"] or u["failed"]:
            say(f"LLM usage: {u['calls']} requests sent ({u['cache_hits']} answered from cache), ~{u['approx_input_tokens']:,} input tokens, "
                f"{u['rate_limited']} rate-limit waits, {u['failed']} extractions given up")
        with aux_scope() as s:
            job = s.get(IngestJob, job_id)
            job.status = "done"
            job.summary_json = summary
            job.finished_at = datetime.now(timezone.utc)
        say("Done.")
        return summary
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        log.error(tb)
        say(f"ERROR: {exc}")
        summary["llm_usage"] = llm_base.usage_snapshot()
        with aux_scope() as s:
            job = s.get(IngestJob, job_id)
            job.status = "error"
            job.error = tb[-4000:]
            job.summary_json = summary
            job.finished_at = datetime.now(timezone.utc)
        return summary
