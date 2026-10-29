"use client";

import { Check, ChevronRight, CircleAlert, X } from "lucide-react";
import { useState } from "react";

import type { Driver, GuidelinesResponse, MvdResponse } from "@/lib/api";
import { cn, fmt } from "@/lib/utils";

import { QuoteList, SourceLink } from "./evidence";
import { Button, Empty, Slider } from "./ui";

export const DOMAIN: Record<Driver["category"], { label: string; color: string }> = {
  EFFICACY: { label: "Efficacy", color: "var(--series-1)" },
  SAFETY_TOLERABILITY: { label: "Safety & tolerability", color: "var(--series-2)" },
  FUNCTION_QOL: { label: "Function & QoL", color: "var(--series-3)" },
  EXECUTION_DOSING: { label: "Execution & dosing", color: "var(--series-4)" },
};
const METHOD: Record<string, string> = {
  llm_preference_extraction: "DCE / survey extraction",
  lexical_fallback: "Lexical extraction (no LLM key)",
  guideline_only: "Guideline mentions only",
  evidence_floor: "No preference evidence found (floor)",
};

function DriverRow({ d, rank, value, onChange, onCommit }: { d: Driver; rank: number; value: number; onChange: (v: number) => void; onCommit: (v: number) => void }) {
  const [open, setOpen] = useState(false);
  const dom = DOMAIN[d.category];
  const changed = Math.abs(value - d.evidence_score) > 0.005;
  return (
    <li className="border-b border-line">
      <div className="grid grid-cols-[28px_minmax(180px,1.3fr)_minmax(160px,1.6fr)_140px_44px_24px] items-center gap-3 px-4 py-2">
        <span className="text-right font-cond text-xs text-ink-3 tnum">{rank}</span>
        <div className="min-w-0">
          <div className="truncate text-sm text-ink">{d.name}</div>
          <div className="text-2xs text-ink-3">{dom.label}</div>
        </div>
        <div className="relative h-[14px]" aria-hidden>
          <div className="absolute inset-y-[4px] left-0 right-0 rounded-full bg-sunken" />
          <div className="absolute inset-y-[3px] left-0 rounded-r-[4px] rounded-l-[2px]" style={{ width: `${value * 100}%`, background: dom.color }} />
          {changed && <div className="absolute inset-y-0 w-[2px] bg-ink" style={{ left: `calc(${d.evidence_score * 100}% - 1px)` }} title="Evidence-derived score" />}
        </div>
        <Slider label={`Weight for ${d.name}`} value={value} onValueChange={onChange} onValueCommit={onCommit} />
        <span className={cn("text-right font-cond text-sm tnum", changed ? "font-semibold text-accent" : "text-ink")}>{fmt(value, 2)}</span>
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-label={`Evidence for ${d.name}`} className="rounded p-0.5 text-ink-3 hover:bg-raised hover:text-ink">
          <ChevronRight className={cn("h-4 w-4 transition-transform", open && "rotate-90")} />
        </button>
      </div>
      {open && (
        <div className="grid grid-cols-[28px_1fr] gap-3 px-4 pb-3">
          <span />
          <div>
            <p className="text-xs text-ink-2">
              {METHOD[d.method] || d.method}. Evidence score {fmt(d.evidence_score, 2)}
              {d.components?.preference_norm != null && <>; preference index {fmt(d.components.preference_norm as number, 2)} of max from {d.components.n_pref_studies} stud{d.components.n_pref_studies === 1 ? "y" : "ies"}</>}
              {d.components?.guideline_share != null && <>; cited in {fmt((d.components.guideline_share as number) * 100, 0)}% of {d.components.n_guideline_docs} guideline documents</>}.
            </p>
            {d.verbatim_evidence.length > 0 ? <div className="mt-2"><QuoteList quotes={d.verbatim_evidence} max={5} /></div>
              : <p className="mt-1 text-xs text-ink-3">No preference study or guideline sentence names this driver. Treat the weight as an assumption; adjust it with the slider.</p>}
          </div>
        </div>
      )}
    </li>
  );
}

export function MvdTab({ data, guidelines, overrides, setOverride, resetOverrides, saveOverrides, saving, loading }: {
  data: MvdResponse | null; guidelines: GuidelinesResponse | null; overrides: Record<string, number>;
  setOverride: (k: string, v: number) => void; resetOverrides: () => void; saveOverrides: () => void; saving: boolean; loading: boolean;
}) {
  const [draft, setDraft] = useState<Record<string, number>>({});
  if (!data) return loading ? <Empty title="Loading value drivers…" /> : <Empty title="No value drivers for this indication yet">Run “Sync live data” to build the driver blueprint from preference studies and guidelines.</Empty>;
  const val = (d: Driver) => draft[d.key] ?? overrides[d.key] ?? d.effective_score;
  const drivers = [...data.drivers].sort((a, b) => val(b) - val(a));
  const nOver = Object.keys(overrides).length;
  return (
    <div className="flex h-full min-h-0">
      <div className="scroll-thin min-w-0 flex-1 overflow-y-auto">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-panel px-4 py-3">
          <div>
            <h2 className="text-base font-semibold">What prescribers weigh in {data.indication.name}</h2>
            <p className="text-xs text-ink-2">Ranked by HCP decision impact (0.10–1.00). {data.method}</p>
          </div>
          <div className="flex items-center gap-2">
            {nOver > 0 && <span className="text-xs text-ink-2">{nOver} weight{nOver === 1 ? "" : "s"} adjusted this session</span>}
            <Button size="sm" variant="quiet" onClick={() => { setDraft({}); resetOverrides(); }} disabled={!nOver}>Reset</Button>
            <Button size="sm" onClick={saveOverrides} disabled={!nOver || saving}>{saving ? "Saving…" : "Save weights"}</Button>
          </div>
        </div>
        <ul>
          {drivers.map((d, i) => (
            <DriverRow key={d.key} d={d} rank={i + 1} value={val(d)}
              onChange={(v) => setDraft((x) => ({ ...x, [d.key]: v }))}
              onCommit={(v) => { setOverride(d.key, v); setDraft((x) => { const y = { ...x }; delete y[d.key]; return y; }); }} />
          ))}
        </ul>
        <div className="flex flex-wrap gap-x-5 gap-y-1 px-4 py-3 text-xs text-ink-2">
          {Object.values(DOMAIN).map((dm) => <span key={dm.label} className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: dm.color }} />{dm.label}</span>)}
          <span className="inline-flex items-center gap-1.5"><span className="h-3 w-[2px] bg-ink" />Evidence-derived score (shown when adjusted)</span>
        </div>
      </div>
      <aside className="scroll-thin w-[420px] shrink-0 overflow-y-auto border-l border-line bg-panel">
        <div className="border-b border-line px-4 py-3">
          <h2 className="text-base font-semibold">Guideline position</h2>
          <p className="text-xs text-ink-2">From {guidelines?.documents.length ?? 0} guideline / consensus documents; {guidelines?.n_recommendations ?? 0} positioning statements extracted with verbatim quotes.</p>
        </div>
        {!guidelines && <p className="px-4 py-3 text-sm text-ink-3">Loading guideline status…</p>}
        {guidelines?.assets.map((a) => (
          <section key={a.generic} className="border-b border-line px-4 py-3">
            <h3 className="text-sm font-semibold">{a.asset}</h3>
            <p className="mt-0.5 text-sm text-ink">{a.status.status}</p>
            {a.status.best && (
              <div className="mt-2">
                <blockquote className="quote text-xs leading-relaxed">{a.status.best.quote}</blockquote>
                <div className="mt-0.5 pl-3"><SourceLink id={a.status.best.source_id} url={a.status.best.url} /></div>
              </div>
            )}
            <h4 className="mt-3 text-xs font-medium text-ink-2">What stands between this asset and {a.gap_to_guideline.target}</h4>
            <ul className="mt-1 space-y-1.5">
              {a.gap_to_guideline.checks.map((c) => (
                <li key={c.rule} className="flex gap-2 text-xs">
                  {c.passed ? <Check className="mt-px h-3.5 w-3.5 shrink-0 text-good" aria-label="met" /> : c.target === "FIRST" ? <X className="mt-px h-3.5 w-3.5 shrink-0 text-critical" aria-label="blocking" /> : <CircleAlert className="mt-px h-3.5 w-3.5 shrink-0 text-ink-3" aria-label="not met (differentiator)" />}
                  <div>
                    <div className={cn(c.passed ? "text-ink-2" : "text-ink")}>{c.label}</div>
                    {!c.passed && c.blocking_cells.length > 0 && (
                      <div className="text-ink-3">
                        {c.blocking_cells.slice(0, 3).map((b, i) => <div key={i}>{b.row ? `${b.row} ` : ""}{b.column_label ? `in ${b.column_label}` : ""}: {b.state === "white_space" ? "no evidence" : b.detail || b.state}{b.validity ? ` (L${b.validity})` : ""}</div>)}
                      </div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </section>
        ))}
        {guidelines && guidelines.assets[0] && <p className="px-4 py-3 text-2xs text-ink-3">{guidelines.assets[0].gap_to_guideline.rules_note}</p>}
      </aside>
    </div>
  );
}
