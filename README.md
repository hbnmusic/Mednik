# mednik

Medical Affairs strategic intelligence engine. mednik pulls live regulatory, registry and literature data, stratifies it deterministically, and turns it into three working views for evidence generation, competitive differentiation and scientific communication strategy:

1. **Value drivers** — a product-agnostic, ranked blueprint of what prescribers weigh in an indication (from discrete-choice / conjoint / survey studies and guidelines), with persona toggles and weight sliders, plus each asset's guideline position and the evidence gaps blocking a higher tier.
2. **Differentiation matrix** — Product A vs up to three comparators on every driver: X = tier-weighted magnitude of differentiation, Y = HCP decision impact. Each node opens the USPI regulatory diff, a cross-trial confounder alert and (for platform pillars) an MSL objection-handler card.
3. **Evidence map** — endpoint × indication × timepoint heatmap with effect-size colour, L1–L5 validity badges and hatched white space. Gap cells draft a Phase 4 / RWE / IIS study concept from how competitors designed their trials.

Every number traces to a source: a ClinicalTrials.gov registry field, a USPI section (by LOINC code) or a verbatim quote validated against the retrieved text (PMID, NCT ID, SPL Set ID or PDF SHA-256).

## Quick start

Requires Python 3.11+ and Node.js 20+.

```bash
python mednik.py setup     # venv + pip + npm, creates .env
python mednik.py dev       # API http://localhost:8000/docs, dashboard http://localhost:3000
```

In the dashboard, choose **Sync live data** (a preset loads Schizophrenia: Caplyta, Vraylar, Cobenfy). The first sync of three assets takes several minutes (registry pagination and NCBI rate limits); later syncs read the local cache.

On macOS/Linux `make setup`, `make dev`, `make test`, `make verify` do the same.

### LLM extraction (optional)

Deterministic parsing (labels, registry, statistics, tiering) needs no key. LLM structured extraction from abstracts, open-access full text and PDFs is used for effect sizes in papers, preference weights and guideline positions. Set one provider in `.env`:

| Provider | Settings |
|---|---|
| Gemini (default) | `GEMINI_API_KEY`, `MEDNIK_GEMINI_MODEL` (default `gemini-3.8-flash`) |
| Claude | `MEDNIK_LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY` |
| Local / on-prem (Ollama, vLLM) | `MEDNIK_LLM_PROVIDER=openai_compat`, `MEDNIK_OPENAI_COMPAT_BASE_URL`, `MEDNIK_OPENAI_COMPAT_MODEL` |

Without a key, regex / lexical fallbacks run instead and are labelled as such in the UI.

**Zero-hallucination constraint.** Every extracted item must carry a `verbatim_quote` and a `source_id`. The quote must be found in the retrieved source text (whitespace / dash / quote-normalised); each number must appear inside its own quote or it is set to `null`; items failing either check are discarded. Accept / reject decisions are logged (`GET /api/v1/audit`).

### Live verification (spec step 6)

```bash
python mednik.py verify                     # Schizophrenia: lumateperone vs cariprazine (+ Cobenfy)
python mednik.py verify --indication "Bipolar I Depression" --no-cobenfy
```

Checks USPI §7 CYP3A4 parsing (dose adjustment vs avoid vs contraindication, with the verbatim sentences), INDUSTRY vs OTHER/NIH/NETWORK/FED sponsor separation against raw registry fields, and that the matrix, heatmap, confounder alerts and study-concept generator populate with real NCT IDs and PMIDs. Writes `backend/verification_report.md` and a sample dossier.

## Architecture

```
backend/                     FastAPI · SQLAlchemy (SQLite, PostgreSQL-ready) · httpx · lxml · scipy · PyMuPDF
  mednik/clients/            async API clients — retry / backoff / per-source rate limits / payload cache
    openfda.py dailymed.py clinicaltrials.py pubmed.py europepmc.py
  mednik/services/
    label_parsers.py         SPL sections by LOINC; CYP450 rule classifier; titration, food, hepatic/renal;
                             boxed warning; §6 incidence tables; §14 efficacy tables
    regulatory_ingest.py     DailyMed (primary) + openFDA (fallback) label ingestion
    label_evidence.py        §14/§6 → Tier 1A rows; registry results matched to label claims; replication
    trials_ingest.py         ClinicalTrials.gov v2: sponsor class, phase, design archetype, eligibility,
                             posted results → effect sizes (Cohen's d, RD, NNT/NNH, HR)
    literature_ingest.py     PubMed E-utilities + Europe PMC; MeSH publication-type tagging
    pdf_ingest.py            PDF dropzone: PyMuPDF text + tables, IMRaD chunking
    extraction.py            LLM structured extraction + citation validator + deterministic fallbacks
    llm/                     Gemini, Claude, OpenAI-compatible providers (response-cached)
    stats.py                 d from CI / means / p, pooling, risk differences, NNT/NNH, sample size
    tiering.py               evidence tiers 1A–3 and validity levels L1–L5 (pure functions)
    mvd.py                   Module 1 — value-driver weights
    matrix.py                Module 2 — 2×2 differentiation engine + USPI diff
    heatmap.py               Module 3 — evidence heatmap
    confounders.py           Adjacency A — cross-trial confounder detector
    objections.py            Adjacency B — MSL objection handler
    study_concepts.py        Adjacency C — gap → study concept
    guidelines.py            Adjacency D — guideline tier tracker + gap-to-guideline linker
    dossier.py               Markdown / PDF briefing export
  scripts/verify_e2e.py      live end-to-end verification
  tests/                     offline tests on synthetic, format-faithful fixtures (fictional drugs)
frontend/                    Next.js 16 (App Router) · Tailwind v4 · Radix · ECharts
```

Two SQLite files are created in `backend/`: `mednik.db` (domain data) and `mednik_cache.db` (live-payload cache, LLM cache, citation audit, job log). Both are gitignored, as are `.env` and `backend/uploads/`. Set `MEDNIK_DATABASE_URL` to a PostgreSQL URL to move off SQLite.

## Method summary

**Evidence tiers** (applied to every claim; X is multiplied by the weight):
1A (1.0) USPI claim, registry result matched to USPI §14, or head-to-head Phase 3/4 superiority ·
1B (0.85) industry Phase 3/4 RCT, primary or multiplicity-controlled endpoint ·
2 (0.60) industry Phase 2 RCT, Phase 3 nominal secondary, post-hoc / pooled, (N)MA ·
3 (0.30) academic / IIS, observational, exploratory.
For an indirect comparison the claim tier is the weaker of the two sides' best tiers.

**Differentiation (X)**: effect drivers `100·tanh(Δd/0.25)`; risk drivers `100·tanh(ΔRD/5 pp)`; label-structural drivers `100·ΔS/3` (S = 0–3 label score: titration, CYP3A4/2D6 handling, dosing convenience, boxed warning / contraindications); onset `100·tanh(Δweeks/1.5)`.

**Impact (Y)**: driver score × 100, where score = 0.10 + 0.90 × (0.75 · preference index + 0.25 · guideline mention share) / max. Drivers with no preference or guideline evidence sit at the 0.10 floor and are labelled. Persona multipliers and slider overrides are explicit user adjustments and never overwrite the evidence score.

**Validity (L1–L5)**: L5 USPI §14 / replicated positive Phase 3 primary · L4 single Phase 3 primary, controlled key secondary, large Phase 4 RCT · L3 Phase 3 nominal secondary / post-hoc, MA, RWE N>500 · L2 Phase 2 RCT, academic N 50–500 · L1 small IIS, case series, biomarker.

Outputs are drafts for internal medical review, not promotional or medical-information content.
