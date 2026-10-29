"""Runtime configuration, loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(ROOT.parent / ".env"), str(ROOT / ".env")),
        env_prefix="MEDNIK_",
        extra="ignore",
    )

    # --- storage -----------------------------------------------------------
    database_url: str = Field(
        default=f"sqlite:///{ROOT / 'mednik.db'}", validation_alias=AliasChoices("MEDNIK_DATABASE_URL", "DATABASE_URL")
    )
    aux_database_url: str | None = None  # cache / audit / job log; defaults to a sibling SQLite file
    db_schema: str = "mednik"  # PostgreSQL only: tables live here, outside Supabase's API-exposed `public` schema
    db_pool_size: int = 3  # PostgreSQL only: keep well under the Supabase pooler's connection limit
    db_max_overflow: int = 2
    upload_dir: Path = ROOT / "uploads"
    cache_ttl_hours: int = 24 * 7  # live API payloads are re-fetched after this
    cache_max_mb: int = 0  # >0 caps stored raw API payloads (LLM responses are kept); 0 = TTL purge only

    # --- hosting -------------------------------------------------------------------
    app_password: str | None = None  # when set, every page and API call needs a login (hosted deployments)
    static_dir: Path | None = None  # built dashboard (frontend/out) served by the API itself

    # --- public API politeness --------------------------------------------
    contact_email: str = "mednik@example.org"  # sent to NCBI / Europe PMC as `email`
    ncbi_api_key: str | None = Field(  # raises NCBI limit from 3 to 10 req/s
        default=None, validation_alias=AliasChoices("MEDNIK_NCBI_API_KEY", "NCBI_API_KEY")
    )
    openfda_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("MEDNIK_OPENFDA_API_KEY", "OPENFDA_API_KEY")
    )
    http_timeout_s: float = 45.0
    http_max_retries: int = 5

    # --- ingestion limits ----------------------------------------------------
    max_trials_per_query: int = 300
    max_pubmed_per_query: int = 150
    max_epmc_per_query: int = 150

    # --- LLM structured extraction --------------------------------------------
    llm_provider: Literal["gemini", "anthropic", "openai_compat", "none"] = "gemini"
    llm_max_concurrency: int = 4
    # Per-sync caps on documents sent to the LLM (each document is one call, or a share of one for short abstracts).
    llm_max_asset_docs: int = 25  # per asset, literature extraction (was 40)
    llm_max_mvd_docs: int = 20  # preference studies for the value-driver blueprint (was 40)
    llm_max_guideline_docs: int = 8  # most recent guideline / consensus documents (was 12)
    gemini_thinking_level: str = "low"  # Gemini 3.x reasoning effort for extraction ("" = model default)
    gemini_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("MEDNIK_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
    )
    gemini_model: str = "gemini-3.8-flash"
    anthropic_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("MEDNIK_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
    )
    anthropic_model: str = "claude-sonnet-5-5"
    openai_compat_base_url: str = "http://localhost:11434/v1"
    openai_compat_api_key: str | None = None
    openai_compat_model: str = "llama3.1:8b-instruct-q4_K_M"

    # --- CORS --------------------------------------------------------------------
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgres://", "postgresql"))

    @property
    def aux_url(self) -> str:
        if self.aux_database_url:
            return self.aux_database_url
        if self.database_url.startswith("sqlite:///"):
            p = self.database_url[len("sqlite:///"):]
            stem = p[:-3] if p.endswith(".db") else p
            return f"sqlite:///{stem}_cache.db"
        return self.database_url

    @property
    def llm_enabled(self) -> bool:
        if self.llm_provider == "gemini":
            return bool(self.gemini_api_key)
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        if self.llm_provider == "openai_compat":
            return bool(self.openai_compat_base_url)
        return False


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.upload_dir.mkdir(parents=True, exist_ok=True)
    return s
