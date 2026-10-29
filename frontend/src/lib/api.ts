// Empty = same origin (/api/* is proxied to the backend by next.config.ts). Set NEXT_PUBLIC_API_BASE only to bypass the proxy.
export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE || "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

type Params = Record<string, string | number | boolean | null | undefined>;

export function url(path: string, params?: Params): string {
  const origin = API_BASE || (typeof window !== "undefined" ? window.location.origin : "http://localhost:3000");
  const u = new URL(`${origin}${path}`);
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") u.searchParams.set(k, String(v));
  });
  return u.toString();
}

// Hosted deployments use a password sign-in; an expired session sends the browser back to /login.
function toLogin(r: Response): void {
  if (r.status === 401 && typeof window !== "undefined") {
    // /login is served by the API, not by Next.js, so this must be a full page load.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
  }
}

export async function get<T>(path: string, params?: Params): Promise<T> {
  const r = await fetch(url(path, params), { cache: "no-store" });
  toLogin(r);
  if (!r.ok) {
    let msg = r.statusText;
    try {
      msg = (await r.json()).detail || msg;
    } catch {}
    throw new ApiError(r.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}

export async function send<T>(path: string, method: "POST" | "PUT", body?: unknown, params?: Params): Promise<T> {
  const r = await fetch(url(path, params), {
    method,
    headers: body instanceof FormData ? undefined : { "content-type": "application/json" },
    body: body instanceof FormData ? body : JSON.stringify(body ?? {}),
  });
  toLogin(r);
  if (!r.ok) {
    let msg = r.statusText;
    try {
      msg = (await r.json()).detail || msg;
    } catch {}
    throw new ApiError(r.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}

/* ---------------- types (mirror the FastAPI responses) ---------------- */

export interface Health {
  status: string;
  version: string;
  llm_provider: string;
  llm_enabled: boolean;
  llm_model?: string;
  auth_required?: boolean;
  counts: { assets: number; studies: number; endpoints: number; documents: number };
}

export interface AssetRow {
  id: number;
  generic: string;
  brand: string | null;
  label: string;
  company: string | null;
  spl_set_id: string | null;
  label_effective_date: string | null;
  initial_us_approval_year: number | null;
  n_studies: number;
  n_endpoints: number;
}

export interface IndicationRow {
  id: number;
  name: string;
  key: string;
  icd10: string | null;
  n_drivers: number;
  n_studies: number;
}

export interface Quote {
  source_id: string;
  quote: string;
  value?: number | null;
  metric?: string | null;
  design?: string | null;
  attribute_text?: string | null;
  kind?: string;
}

export interface Driver {
  id: number;
  key: string;
  name: string;
  category: "EFFICACY" | "FUNCTION_QOL" | "SAFETY_TOLERABILITY" | "EXECUTION_DOSING";
  evidence_score: number;
  override_score: number | null;
  effective_score: number;
  score_source: string;
  method: string;
  evidence_count: number;
  components: Record<string, number | null> | null;
  supporting_ids: string[];
  verbatim_evidence: Quote[];
  kind: string;
  rows: string[];
}

export interface MvdResponse {
  indication: { id: number; name: string; key: string };
  persona: string;
  personas: Record<string, string>;
  drivers: Driver[];
  method: string;
}

export interface Metric {
  metric: string;
  estimate: number;
  se?: number;
  lo?: number;
  hi?: number;
  pp?: number;
  k_studies?: number;
  tier: string | null;
  display: string;
  quotes?: Quote[];
}

export interface MatrixNode {
  id: string;
  mvd_key: string;
  mvd_name: string;
  category: Driver["category"];
  kind: string;
  comparator: string;
  comparator_label: string;
  x: number | null;
  x_raw: number | null;
  y: number;
  tier: string | null;
  tier_weight: number;
  quadrant: "TR" | "TL" | "BR" | "BL";
  quadrant_label: string;
  competitor_advantage: boolean;
  uncertain: boolean;
  insufficient: boolean;
  explanation: string;
  a: Metric | null;
  b: Metric | null;
}

export interface MatrixResponse {
  indication: { id: number; name: string; key: string };
  asset_a: { id: number; generic: string; label: string };
  comparators: { id: number; generic: string; label: string }[];
  thresholds: { x: number; y: number };
  quadrants: Record<string, string>;
  nodes: MatrixNode[];
}

export interface StudyBrief {
  id: number;
  source: string;
  ref: string;
  nct_id: string | null;
  pmid: string | null;
  title: string | null;
  sponsor: string | null;
  sponsor_class: string | null;
  phase: string | null;
  design: string | null;
  n: number | null;
  duration_weeks: number | null;
  start_year: number | null;
  status: string | null;
  pct_us_sites: number | null;
  evidence_tier: string | null;
  validity_level: number | null;
  url: string | null;
  baseline?: Record<string, { mean: number | null }> | null;
  dropout?: { overall?: number } | null;
}

export interface Endpoint {
  id: number;
  scale: string;
  scale_label: string;
  endpoint_label: string | null;
  timepoint_weeks: number | null;
  arm: string | null;
  comparator: string | null;
  effect_size_type: string;
  value: number | null;
  ci_95_lower: number | null;
  ci_95_upper: number | null;
  p_value: number | null;
  p_value_text: string | null;
  is_primary: boolean | null;
  is_post_hoc: boolean | null;
  is_multiplicity_controlled: boolean | null;
  n_treatment: number | null;
  n_comparator: number | null;
  cohens_d: number | null;
  risk_treatment_pct: number | null;
  risk_comparator_pct: number | null;
  risk_difference_pp: number | null;
  nnt_nnh: number | null;
  favorable: boolean | null;
  significant: boolean | null;
  label_confirmed: boolean | null;
  evidence_tier: string | null;
  validity_level: number | null;
  column_label: string | null;
  verbatim_quote: string;
  source_id: string;
  source_type: string;
  source_url: string | null;
  extraction_method: string;
  study: StudyBrief;
}

export interface ConfounderItem {
  dimension: string;
  a: string;
  b: string;
  metric: number | null;
  threshold: string;
  triggered: boolean;
  caveat: string;
}

export interface ObjectionCard {
  pillar_statement: string;
  fair_balance: string[];
  challenges: { severity: "high" | "medium" | "low"; type: string; challenge: string; response: string }[];
  claim_tier: string | null;
  status: string;
  disclaimer: string;
}

export interface RegRow {
  attribute: string;
  a: string | number | null;
  b: string | number | null;
  a_quote: string | null;
  b_quote: string | null;
  advantage: "A" | "B" | null;
}

export interface NodeDetail {
  mvd: { key: string; name: string; category: string; kind: string };
  asset_a: string;
  asset_b: string;
  x: number | null;
  y: number;
  quadrant: string;
  quadrant_label: string;
  claim_tier: string | null;
  differentiation: { explanation: string; uncertain?: boolean; insufficient?: boolean; delta?: number; delta_unit?: string };
  a: Metric | null;
  b: Metric | null;
  evidence_a: Endpoint[];
  evidence_b: Endpoint[];
  regulatory_diff: RegRow[];
  confounder_alert: { alert: boolean; applicable: boolean; items: ConfounderItem[]; caveat: string | null; note?: string };
  objection_card: ObjectionCard | null;
}

export interface HeatCell {
  state: "evidence" | "white_space" | "registered";
  validity: number | null;
  tier: string | null;
  color: string;
  n_endpoints: number;
  n_studies: number;
  summary: string;
  significant?: boolean;
  refs?: string[];
  registered_ncts?: string[];
  mvd_key: string | null;
  mvd_score: number | null;
}

export interface HeatColumn {
  key: string;
  label: string;
  indication_key: string;
  indication_label: string;
  population: string;
  timepoint: string;
  adjunctive: boolean;
  approved: boolean;
  pipeline: boolean;
  active_trials: string[];
}

export interface HeatmapResponse {
  asset: { id: number; generic: string; brand: string | null };
  columns: HeatColumn[];
  rows: { key: string; label: string; kind: string; mvd_key: string | null; mvd_name: string | null }[];
  cells: Record<string, Record<string, HeatCell>>;
  legend: { colors: Record<string, string>; validity: Record<string, string> };
}

export interface CellEvidence {
  row: { key: string; label: string; kind: string | null };
  column: { key: string; label: string };
  endpoints: Endpoint[];
  registered_trials: (StudyBrief & { registered_measure: string; time_frame: string; outcome_type: string })[];
}

export interface StudyConcept {
  id: number;
  markdown: string;
  gap: { row: string; row_label: string; column: string; column_label: string; cell_state: string };
  design: { type: string; title: string; rationale: string; arms: string[]; blinding: string; primary_analysis: string };
  duration_weeks: number;
  primary_endpoint: string;
  secondary_endpoints: { scale: string; label: string; n_precedents: number }[];
  sample_size: { basis: string; assumption: string; n_per_arm: number | null; n_per_arm_with_dropout: number | null; dropout_assumed: number; total_n: number | null; note?: string };
  population: { age_range: [number, number]; severity_thresholds: { scale: string; typical_min: number; n_precedents: number }[] };
  inclusion: { criterion: string; pct_precedents: number; example: string; example_nct: string }[];
  exclusion: { criterion: string; pct_precedents: number; example: string; example_nct: string }[];
  precedents: { nct_id: string; sponsor: string | null; sponsor_class: string; phase: string; design: string; n: number | null; competitor_trial: boolean;
    outcomes: { type: string; measure: string; time_frame: string; weeks: number | null }[] }[];
  n_precedents: number;
}

export interface GuidelineCheck {
  rule: string;
  label: string;
  target: string;
  passed: boolean;
  blocking_cells: { row: string | null; column: string | null; column_label?: string; state: string; validity?: number | null; detail?: string }[];
}

export interface GuidelinesResponse {
  indication: string;
  assets: {
    asset: string;
    generic: string;
    status: { status: string; line: string | null; best: { body: string; year: number; quote: string; source_id: string; url: string | null; level_of_evidence: string | null } | null };
    gap_to_guideline: { current: unknown; target: string; checks: GuidelineCheck[]; summary: string; rules_note: string };
  }[];
  n_recommendations: number;
  documents: { id: string; pmid: string | null; title: string; year: number | null; journal: string | null; full_text: boolean }[];
}

export interface Job {
  id: string;
  status: "queued" | "running" | "done" | "error";
  log: { t: string; msg: string }[];
  summary: Record<string, unknown> | null;
  error: string | null;
}
