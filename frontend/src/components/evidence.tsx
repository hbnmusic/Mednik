"use client";

import { ExternalLink } from "lucide-react";

import type { Endpoint, Quote } from "@/lib/api";
import { cn, fmt } from "@/lib/utils";

import { TierChip, ValidityChip } from "./ui";

export function SourceLink({ id, url, className }: { id: string; url?: string | null; className?: string }) {
  const href = url || (id.startsWith("NCT") ? `https://clinicaltrials.gov/study/${id}` : /^\d{6,9}$/.test(id) ? `https://pubmed.ncbi.nlm.nih.gov/${id}/`
    : /^[0-9a-f-]{36}$/.test(id) ? `https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=${id}` : null);
  const label = /^[0-9a-f-]{36}$/.test(id) ? `USPI ${id.slice(0, 8)}` : /^[0-9a-f]{64}$/.test(id) ? `PDF ${id.slice(0, 8)}` : /^\d{6,9}$/.test(id) ? `PMID ${id}` : id;
  if (!href) return <span className={cn("font-cond text-xs text-ink-2 tnum", className)}>{label}</span>;
  return (
    <a href={href} target="_blank" rel="noreferrer" className={cn("inline-flex items-center gap-0.5 font-cond text-xs text-accent tnum hover:underline", className)}>
      {label}
      <ExternalLink className="h-3 w-3" aria-hidden />
    </a>
  );
}

function effectText(e: Endpoint): string {
  switch (e.effect_size_type) {
    case "LS_MEAN_DIFF":
      return `LS-mean Δ ${fmt(e.value, 1, true)}${e.ci_95_lower !== null ? ` (95% CI ${fmt(e.ci_95_lower, 1)} to ${fmt(e.ci_95_upper, 1)})` : ""}`;
    case "INCIDENCE_PCT":
      return `${fmt(e.risk_treatment_pct, 1)}% vs ${fmt(e.risk_comparator_pct, 1)}%${e.risk_difference_pp !== null ? ` (${fmt(e.risk_difference_pp, 1, true)} pp)` : ""}`;
    case "HR":
    case "OR":
      return `${e.effect_size_type} ${fmt(e.value, 2)}${e.ci_95_lower !== null ? ` (${fmt(e.ci_95_lower, 2)}–${fmt(e.ci_95_upper, 2)})` : ""}`;
    case "COHENS_D":
      return `d ${fmt(e.value, 2, true)}`;
    default:
      return `${e.effect_size_type} ${fmt(e.value, 2)}`;
  }
}

export function EndpointRow({ e }: { e: Endpoint }) {
  const flags = [
    e.is_primary ? "primary" : null,
    e.is_multiplicity_controlled ? "multiplicity-controlled" : null,
    e.is_post_hoc ? "post hoc" : null,
    e.label_confirmed ? "matches USPI §14" : null,
  ].filter(Boolean);
  return (
    <li className="border-b border-line py-3 last:border-0">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <TierChip tier={e.evidence_tier} />
        <ValidityChip level={e.validity_level} />
        <span className="text-sm font-medium">{e.scale_label}</span>
        {e.timepoint_weeks ? <span className="text-xs text-ink-3">week {fmt(e.timepoint_weeks, 0)}</span> : null}
        <span className="ml-auto"><SourceLink id={e.study.nct_id || e.source_id} url={e.study.nct_id ? null : e.source_url} /></span>
      </div>
      <div className="mt-1 font-cond text-sm tnum text-ink">
        {effectText(e)}
        {e.cohens_d !== null && e.effect_size_type !== "COHENS_D" && <span className="text-ink-2">; d {fmt(e.cohens_d, 2, true)}</span>}
        {e.p_value_text && <span className="text-ink-2">; p {e.p_value_text}</span>}
        {e.nnt_nnh && e.effect_size_type === "INCIDENCE_PCT" && (e.risk_difference_pp || 0) > 0 && <span className="text-ink-2">; NNH {e.nnt_nnh}</span>}
      </div>
      <div className="mt-0.5 text-xs text-ink-3">
        {e.arm}{e.comparator ? ` vs ${e.comparator}` : ""}
        {e.n_treatment ? ` (n=${e.n_treatment}/${e.n_comparator ?? "?"})` : ""}
        {flags.length ? ` — ${flags.join(", ")}` : ""}
      </div>
      <div className="mt-1 text-xs text-ink-3">
        {e.study.sponsor_class === "INDUSTRY" ? "Industry" : e.study.sponsor_class === "ACADEMIC_IIS" ? "Academic / IIS" : ""} {e.study.phase?.replaceAll("PHASE", "Ph ").replace("/", "/")}
        {e.study.n ? `, N=${e.study.n}` : ""}{e.study.start_year ? `, started ${e.study.start_year}` : ""}
      </div>
      <blockquote className="quote mt-2 text-xs leading-relaxed">{e.verbatim_quote}</blockquote>
    </li>
  );
}

export function EndpointList({ items, empty }: { items: Endpoint[]; empty?: string }) {
  if (!items.length) return <p className="py-2 text-sm text-ink-3">{empty || "No endpoint-level evidence."}</p>;
  return <ul>{items.map((e) => <EndpointRow key={e.id} e={e} />)}</ul>;
}

export function QuoteList({ quotes, max = 4 }: { quotes: Quote[]; max?: number }) {
  if (!quotes?.length) return null;
  return (
    <ul className="space-y-2">
      {quotes.slice(0, max).map((q, i) => (
        <li key={i}>
          <blockquote className="quote text-xs leading-relaxed">{q.quote}</blockquote>
          <div className="mt-0.5 pl-3 text-2xs text-ink-3">
            <SourceLink id={q.source_id} />
            {q.value !== null && q.value !== undefined && <span className="ml-2">{q.metric?.toLowerCase().replaceAll("_", " ")}: {q.value}</span>}
          </div>
        </li>
      ))}
    </ul>
  );
}
