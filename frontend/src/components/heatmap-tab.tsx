"use client";

import type { HeatCell, HeatColumn, HeatmapResponse } from "@/lib/api";
import { cn } from "@/lib/utils";

import { Empty, Tip, ValidityChip } from "./ui";

export const CELL_BG: Record<string, string> = {
  g3: "var(--fav-3)", g2: "var(--fav-2)", g1: "var(--fav-1)", neutral: "var(--neutral-cell)",
  a1: "var(--harm-1)", a2: "var(--harm-2)", r2: "var(--harm-3)", r1: "var(--unfav)",
};
const LEGEND: { key: string; label: string }[] = [
  { key: "g3", label: "Strong benefit (d ≥ 0.5)" }, { key: "g2", label: "Moderate (d 0.3–0.5)" }, { key: "g1", label: "Small benefit / low harm" },
  { key: "neutral", label: "Not significant" }, { key: "a1", label: "Excess harm 2–5 pp" }, { key: "a2", label: "5–10 pp" }, { key: "r2", label: "≥ 10 pp safety signal" },
  { key: "r1", label: "Unfavourable efficacy" },
];
const TP_LABEL: Record<string, string> = { acute: "Acute ≤8 wk", intermediate: "9–26 wk", maintenance: "Maintenance" };
const POP_LABEL: Record<string, string> = { adult: "", pediatric: "Pediatric", elderly: "Older adult" };

export function isGap(cell: HeatCell): boolean {
  return cell.state !== "evidence" || (cell.validity ?? 0) <= 2;
}

function groupColumns(cols: HeatColumn[]) {
  const groups: { label: string; sub: string; span: number; approved: boolean; pipeline: boolean }[] = [];
  cols.forEach((c) => {
    const sub = [POP_LABEL[c.population] || "", c.adjunctive ? "Adjunctive" : ""].filter(Boolean).join(", ");
    const last = groups[groups.length - 1];
    if (last && last.label === c.indication_label && last.sub === sub) last.span += 1;
    else groups.push({ label: c.indication_label, sub, span: 1, approved: c.approved, pipeline: c.pipeline });
  });
  return groups;
}

export function HeatmapTab({ data, loading, onOpen, highlightIndication }: {
  data: HeatmapResponse | null; loading: boolean; onOpen: (row: string, col: HeatColumn, cell: HeatCell) => void; highlightIndication?: string;
}) {
  if (!data) return loading ? <Empty title="Building the evidence map…" /> : <Empty title="Select a primary asset">The evidence map shows every endpoint × indication × timepoint for the selected asset.</Empty>;
  if (!data.columns.length) return <Empty title="No evidence ingested for this asset yet">Run “Sync live data” for an indication that includes this asset.</Empty>;
  const groups = groupColumns(data.columns);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-b border-line bg-panel px-4 py-2 text-xs text-ink-2">
        {LEGEND.map((l) => (
          <span key={l.key} className="inline-flex items-center gap-1.5">
            <span className="h-3 w-4 rounded-[2px] ring-1 ring-ink/10" style={{ background: CELL_BG[l.key] }} />{l.label}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5"><span className="hatch h-3 w-4 rounded-[2px] ring-1 ring-ink/10" />No evidence (white space)</span>
        <Tip content={<ul className="space-y-0.5">{Object.entries(data.legend.validity).map(([k, v]) => <li key={k}><b>{k}</b> {v}</li>)}</ul>}>
          <span className="inline-flex cursor-help items-center gap-1.5 underline decoration-dotted underline-offset-2"><ValidityChip level={5} />Validity L1–L5</span>
        </Tip>
        <span className="inline-flex items-center gap-1.5"><span className="h-3 w-4 rounded-[2px] outline-2 -outline-offset-2 outline-accent" />High-impact gap — click to draft a study</span>
      </div>
      <div className="scroll-thin min-h-0 flex-1 overflow-auto">
        <table className="border-separate border-spacing-0 font-cond text-2xs tnum">
          <thead className="sticky top-0 z-20 bg-page">
            <tr>
              <th rowSpan={2} className="sticky left-0 z-30 w-56 min-w-56 border-b border-r border-line bg-page px-3 py-2 text-left align-bottom font-sans text-xs font-medium text-ink-2">
                Endpoint
              </th>
              {groups.map((g, i) => (
                <th key={i} colSpan={g.span} className={cn("border-b border-l border-line px-2 pt-2 pb-1 text-left align-bottom font-sans text-xs font-semibold",
                  highlightIndication && g.label === highlightIndication ? "text-ink" : "text-ink-2")}>
                  <div className="truncate" title={g.label}>{g.label}</div>
                  <div className="text-2xs font-normal text-ink-3">{[g.sub, g.approved ? "Approved" : g.pipeline ? "Pipeline / investigational" : ""].filter(Boolean).join(", ")}</div>
                </th>
              ))}
            </tr>
            <tr>
              {data.columns.map((c) => (
                <th key={c.key} className="w-[92px] min-w-[92px] border-b border-l border-line px-2 py-1 text-left font-normal text-ink-3">
                  {TP_LABEL[c.timepoint] || c.timepoint}
                  {c.active_trials.length > 0 && <Tip content={`Active trials: ${c.active_trials.join(", ")}`}><span className="ml-1 cursor-help text-accent">●</span></Tip>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => {
              const rowScores = Object.values(data.cells[r.key] || {}).map((c) => c.mvd_score ?? 0);
              const impact = Math.max(0, ...rowScores);
              return (
                <tr key={r.key}>
                  <th className="sticky left-0 z-10 border-b border-r border-line bg-panel px-3 py-1.5 text-left font-sans font-normal">
                    <div className="text-xs text-ink">{r.label}</div>
                    {r.mvd_name && (
                      <div className="mt-0.5 flex items-center gap-1.5 text-2xs text-ink-3" title={`Highest HCP impact for this driver across shown indications: ${(impact * 100).toFixed(0)}/100`}>
                        <span className="relative h-[3px] w-10 rounded-full bg-sunken"><span className="absolute inset-y-0 left-0 rounded-full bg-ink-3" style={{ width: `${impact * 100}%` }} /></span>
                        <span className="truncate">{r.mvd_name}</span>
                      </div>
                    )}
                  </th>
                  {data.columns.map((c) => {
                    const cell = data.cells[r.key]?.[c.key];
                    if (!cell) return <td key={c.key} className="border-b border-l border-line" />;
                    const gap = isGap(cell) && (cell.mvd_score ?? 0) >= 0.5;
                    const ev = cell.state === "evidence";
                    return (
                      <td key={c.key} className="border-b border-l border-line p-0">
                        <button type="button" onClick={() => onOpen(r.key, c, cell)}
                          aria-label={`${r.label}, ${c.label}: ${ev ? `${cell.summary}, validity L${cell.validity}` : cell.summary}`}
                          className={cn("relative block h-[42px] w-full px-1.5 text-left hover:brightness-95 hover:ring-2 hover:ring-inset hover:ring-ink/25", !ev && "hatch",
                            gap && "outline-2 -outline-offset-2 outline-accent")}
                          style={ev ? { background: CELL_BG[cell.color] || "var(--neutral-cell)" } : undefined}>
                          {ev ? (
                            <>
                              <span className={cn("block truncate pr-5 pt-1 text-[11px] leading-tight", ["g3", "r2"].includes(cell.color) ? "text-ink font-medium" : "text-ink")}>{cell.summary}</span>
                              <span className="block truncate text-[10px] leading-tight text-ink-2">{cell.n_studies} stud{cell.n_studies === 1 ? "y" : "ies"}</span>
                              <ValidityChip level={cell.validity} className="absolute right-0.5 top-0.5" />
                            </>
                          ) : cell.state === "registered" ? (
                            <span className="block truncate pt-1 text-[10px] leading-tight text-ink-2">Registered, no results ({cell.registered_ncts?.length})</span>
                          ) : null}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
