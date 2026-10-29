"""Relational schema (spec §6 core tables + supporting tables).

Core tables required by the spec:
  indications, market_value_drivers, assets, regulatory_sections,
  clinical_studies, extracted_endpoints
Supporting tables:
  documents / document_links (literature + PDF corpus), guideline_recommendations,
  api_cache (live-payload persistence), extraction_audit (zero-hallucination log),
  ingest_jobs, study_concepts
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import AuxBase, Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Core tables
# ---------------------------------------------------------------------------


class Indication(Base):
    __tablename__ = "indications"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    key: Mapped[str] = mapped_column(String(80), index=True)  # normalized indication key
    icd10_code: Mapped[str | None] = mapped_column(String(20))
    guideline_summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    drivers: Mapped[list[MarketValueDriver]] = relationship(back_populates="indication", cascade="all, delete-orphan")


class MarketValueDriver(Base):
    __tablename__ = "market_value_drivers"
    __table_args__ = (UniqueConstraint("indication_id", "attribute_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    indication_id: Mapped[int] = mapped_column(ForeignKey("indications.id", ondelete="CASCADE"), index=True)
    attribute_key: Mapped[str] = mapped_column(String(60))
    attribute_name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(30))  # EFFICACY | FUNCTION_QOL | SAFETY_TOLERABILITY | EXECUTION_DOSING
    mvd_impact_score: Mapped[float] = mapped_column(Float)  # 0.10–1.00, evidence-derived (before overrides)
    user_override_score: Mapped[float | None] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(40))  # llm_dce_extraction | lexical_fallback | evidence_floor
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    component_scores_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    supporting_pmids_json: Mapped[list[str] | None] = mapped_column(JSON)
    verbatim_evidence: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)  # [{source_id, quote, value, metric}]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    indication: Mapped[Indication] = relationship(back_populates="drivers")

    @property
    def effective_score(self) -> float:
        return self.user_override_score if self.user_override_score is not None else self.mvd_impact_score


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    generic_name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    brand_name: Mapped[str | None] = mapped_column(String(200))
    company: Mapped[str | None] = mapped_column(String(300))
    mechanism_of_action: Mapped[str | None] = mapped_column(Text)
    spl_set_id: Mapped[str | None] = mapped_column(String(64))
    synonyms_json: Mapped[list[str] | None] = mapped_column(JSON)
    initial_us_approval_year: Mapped[int | None] = mapped_column(Integer)
    label_effective_date: Mapped[str | None] = mapped_column(String(10))
    label_title: Mapped[str | None] = mapped_column(Text)
    approved_indications_json: Mapped[list[str] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sections: Mapped[list[RegulatorySection]] = relationship(back_populates="asset", cascade="all, delete-orphan")


class RegulatorySection(Base):
    __tablename__ = "regulatory_sections"
    __table_args__ = (UniqueConstraint("asset_id", "loinc_code", "source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    loinc_code: Mapped[str] = mapped_column(String(20))
    section_title: Mapped[str | None] = mapped_column(String(300))
    raw_text: Mapped[str] = mapped_column(Text)
    structured_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    tables_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    effective_date: Mapped[str | None] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(20))  # dailymed | openfda
    spl_set_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    asset: Mapped[Asset] = relationship(back_populates="sections")


class ClinicalStudy(Base):
    __tablename__ = "clinical_studies"
    __table_args__ = (UniqueConstraint("asset_id", "source", "source_ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    nct_id: Mapped[str | None] = mapped_column(String(20), index=True)
    pmid: Mapped[str | None] = mapped_column(String(20), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    indication_id: Mapped[int | None] = mapped_column(ForeignKey("indications.id", ondelete="SET NULL"), index=True)
    source: Mapped[str] = mapped_column(String(20))  # ctgov | uspi | pubmed | epmc | pdf
    source_ref: Mapped[str] = mapped_column(String(120))  # NCT id, "USPI §14 Study 1", PMID, sha256
    title: Mapped[str | None] = mapped_column(Text)
    sponsor_name: Mapped[str | None] = mapped_column(String(300))
    sponsor_class: Mapped[str | None] = mapped_column(String(20))  # INDUSTRY | ACADEMIC_IIS
    sponsor_class_raw: Mapped[str | None] = mapped_column(String(20))
    industry_collaborator: Mapped[bool | None] = mapped_column(Boolean)
    phase: Mapped[str | None] = mapped_column(String(40))
    phase_max: Mapped[int | None] = mapped_column(Integer)
    study_type: Mapped[str | None] = mapped_column(String(30))
    design_model: Mapped[str | None] = mapped_column(String(60))
    allocation: Mapped[str | None] = mapped_column(String(30))
    masking: Mapped[str | None] = mapped_column(String(30))
    design_archetype: Mapped[str | None] = mapped_column(String(40))
    placebo_run_in: Mapped[bool | None] = mapped_column(Boolean)
    flexible_dose: Mapped[bool | None] = mapped_column(Boolean)
    has_placebo: Mapped[bool | None] = mapped_column(Boolean)
    active_comparators_json: Mapped[list[str] | None] = mapped_column(JSON)
    is_head_to_head: Mapped[bool | None] = mapped_column(Boolean)
    sample_size: Mapped[int | None] = mapped_column(Integer)
    duration_weeks: Mapped[float | None] = mapped_column(Float)
    start_year: Mapped[int | None] = mapped_column(Integer)
    overall_status: Mapped[str | None] = mapped_column(String(40))
    has_results: Mapped[bool | None] = mapped_column(Boolean)
    countries_json: Mapped[dict[str, int] | None] = mapped_column(JSON)
    pct_us_sites: Mapped[float | None] = mapped_column(Float)
    baseline_severity_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    placebo_response_rate: Mapped[float | None] = mapped_column(Float)  # placebo change from baseline on primary scale
    placebo_response_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    dropout_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    eligibility_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    registered_outcomes_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    arms_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    conditions_json: Mapped[list[str] | None] = mapped_column(JSON)
    indication_key: Mapped[str | None] = mapped_column(String(80))
    population: Mapped[str | None] = mapped_column(String(40))
    linked_pmids_json: Mapped[list[str] | None] = mapped_column(JSON)
    candidate_nct_ids_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)  # label-study → registry matches
    evidence_tier: Mapped[str | None] = mapped_column(String(4))  # 1A | 1B | 2 | 3 (best tier for the study)
    validity_level: Mapped[int | None] = mapped_column(Integer)  # 1..5 (best level for the study)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    endpoints: Mapped[list[ExtractedEndpoint]] = relationship(back_populates="study", cascade="all, delete-orphan")


class ExtractedEndpoint(Base):
    __tablename__ = "extracted_endpoints"

    id: Mapped[int] = mapped_column(primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("clinical_studies.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    indication_id: Mapped[int | None] = mapped_column(ForeignKey("indications.id", ondelete="SET NULL"), index=True)
    mvd_id: Mapped[int | None] = mapped_column(ForeignKey("market_value_drivers.id", ondelete="SET NULL"))
    mvd_key: Mapped[str | None] = mapped_column(String(60), index=True)
    endpoint_scale_name: Mapped[str] = mapped_column(String(60), index=True)  # canonical heatmap row key
    endpoint_label: Mapped[str | None] = mapped_column(Text)  # raw registry / label / paper wording
    timepoint_weeks: Mapped[float | None] = mapped_column(Float)
    arm_name: Mapped[str | None] = mapped_column(String(300))
    comparator_arm: Mapped[str | None] = mapped_column(String(300))
    effect_size_type: Mapped[str] = mapped_column(String(20))  # LS_MEAN_DIFF | COHENS_D | NNT | NNH | INCIDENCE_PCT | OR | HR | RD
    value: Mapped[float | None] = mapped_column(Float)
    ci_95_lower: Mapped[float | None] = mapped_column(Float)
    ci_95_upper: Mapped[float | None] = mapped_column(Float)
    p_value: Mapped[float | None] = mapped_column(Float)
    p_value_text: Mapped[str | None] = mapped_column(String(40))
    is_multiplicity_controlled: Mapped[bool | None] = mapped_column(Boolean)  # None = not reported
    is_primary: Mapped[bool | None] = mapped_column(Boolean)
    is_post_hoc: Mapped[bool | None] = mapped_column(Boolean)
    n_treatment: Mapped[int | None] = mapped_column(Integer)
    n_comparator: Mapped[int | None] = mapped_column(Integer)
    cohens_d: Mapped[float | None] = mapped_column(Float)  # signed so that + = favourable to treatment
    cohens_d_var: Mapped[float | None] = mapped_column(Float)
    risk_treatment: Mapped[float | None] = mapped_column(Float)  # proportions 0..1
    risk_comparator: Mapped[float | None] = mapped_column(Float)
    risk_difference: Mapped[float | None] = mapped_column(Float)  # treatment − comparator (proportion)
    nnt_nnh: Mapped[float | None] = mapped_column(Float)
    favorable: Mapped[bool | None] = mapped_column(Boolean)
    significant: Mapped[bool | None] = mapped_column(Boolean)
    label_confirmed: Mapped[bool | None] = mapped_column(Boolean)  # registry result matched to a USPI §14 row
    column_key: Mapped[str | None] = mapped_column(String(160), index=True)
    verbatim_quote: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(String(120))  # NCTId | PMID | SPL SetID | Document SHA256
    source_type: Mapped[str] = mapped_column(String(20))  # ctgov | uspi | pubmed | pdf
    extraction_method: Mapped[str] = mapped_column(String(40))
    evidence_tier: Mapped[str | None] = mapped_column(String(4))
    validity_level: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    study: Mapped[ClinicalStudy] = relationship(back_populates="endpoints")


# ---------------------------------------------------------------------------
# Supporting tables
# ---------------------------------------------------------------------------


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(20))  # pubmed | epmc | pdf
    source_id: Mapped[str] = mapped_column(String(120), index=True)  # PMID | PMCID | sha256
    pmid: Mapped[str | None] = mapped_column(String(20), index=True)
    pmcid: Mapped[str | None] = mapped_column(String(20))
    doi: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(Text)
    journal: Mapped[str | None] = mapped_column(String(400))
    year: Mapped[int | None] = mapped_column(Integer)
    authors: Mapped[str | None] = mapped_column(Text)
    affiliations: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    full_text: Mapped[str | None] = mapped_column(Text)
    is_open_access: Mapped[bool | None] = mapped_column(Boolean)
    pub_types_json: Mapped[list[str] | None] = mapped_column(JSON)
    evidence_tags_json: Mapped[list[str] | None] = mapped_column(JSON)
    mesh_json: Mapped[list[str] | None] = mapped_column(JSON)
    linked_nct_ids_json: Mapped[list[str] | None] = mapped_column(JSON)
    chunks_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)  # IMRaD chunks
    tables_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    filename: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocumentLink(Base):
    __tablename__ = "document_links"
    __table_args__ = (UniqueConstraint("document_id", "asset_id", "indication_id", "role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    indication_id: Mapped[int | None] = mapped_column(ForeignKey("indications.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(30))  # asset_evidence | mvd_preference | guideline


class GuidelineRecommendation(Base):
    __tablename__ = "guideline_recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    indication_id: Mapped[int] = mapped_column(ForeignKey("indications.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    asset_mention: Mapped[str | None] = mapped_column(String(200))
    guideline_body: Mapped[str | None] = mapped_column(String(120))
    publication_year: Mapped[int | None] = mapped_column(Integer)
    indication_phase: Mapped[str | None] = mapped_column(String(40))  # ACUTE | MAINTENANCE | UNSPECIFIED
    line: Mapped[str | None] = mapped_column(String(30))  # FIRST | SECOND | THIRD | NOT_RECOMMENDED | INSUFFICIENT
    role: Mapped[str | None] = mapped_column(String(30))  # MONOTHERAPY | ADJUNCTIVE | EITHER
    level_of_evidence: Mapped[str | None] = mapped_column(String(40))
    verbatim_quote: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(String(120))
    source_type: Mapped[str] = mapped_column(String(30))  # llm_extraction | lexical | user_curated
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApiCache(AuxBase):
    __tablename__ = "api_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(30))
    url: Mapped[str] = mapped_column(Text)
    params_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status_code: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExtractionAudit(AuxBase):
    __tablename__ = "extraction_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String(120), index=True)
    schema_name: Mapped[str] = mapped_column(String(60))
    provider: Mapped[str | None] = mapped_column(String(40))
    item_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    accepted: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IngestJob(AuxBase):
    __tablename__ = "ingest_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(20))  # queued | running | done | error
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    log_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudyConcept(Base):
    __tablename__ = "study_concepts"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    indication_id: Mapped[int | None] = mapped_column(ForeignKey("indications.id", ondelete="SET NULL"))
    row_key: Mapped[str] = mapped_column(String(60))
    column_key: Mapped[str] = mapped_column(String(160))
    synopsis_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    markdown: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
