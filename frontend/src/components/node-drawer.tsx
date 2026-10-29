"use client";

import { AlertTriangle, ShieldCheck } from "lucide-react";
import { useState } from "react";

import type { MatrixNode, Metric, NodeDetail } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { brandOf, cn, fmt } from "@/lib/utils";

import { EndpointList, QuoteList } from "./evidence";
import { Segmented, Sheet, TierChip } from "./ui";

function MetricBlock({ label, m }: { label: string; m: Metric | null }) {
  return (
    <div className="min-w-0 flex-1 rounded-md bg-raised px-3 py-2.5">
      <div className="text-xs text-ink-2">{label}</div>
      {m ? (
        <>
          <div className="mt-1 font-cond text-base font-medium tnum text-ink">{m.display}</div>
          <div className="mt-1"><TierChip tier={m.tier} /></div>
        </>
      ) : (
        <div className="mt-1 text-sm text-ink-3">No qualifying evidence</div>
      )}
    </div>
  );
}

export function NodeDrawer({ node, params, onClose }: {
  node: MatrixNode | null; params: { indication: string; asset_a: string; persona: string; tier_filter: string; overrides?: string }; onClose: () => void;
}) {
  const [side, setSide] = useState("a");
  const q = useApi<NodeDetail>(node ? ["/api/v1/matrix/node", { ...params, asset_b: node.comparator, mvd_key: node.mvd_key }, 0] : null);
  const d = q.loading ? null : q.data;
  const err = q.error;

  const aName = d ? brandOf(d.asset_a) : "";
  const bName = d ? brandOf(d.asset_b) : node ? brandOf(node.comparator_label) : "";
  return (
    <Sheet open={!!node} onOpenChange={(o) => !o && onClose()} title={node?.mvd_name ?? ""}
      subtitle={node ? <>{node.quadrant_label}: {aName || params.asset_a} vs {bName}</> : null}>
      {err && <p className="text-sm text-critical">{err}</p>}
      {!d && !err && <p className="text-sm text-ink-3">Loading evidence…</p>}
      {d && (
        <div className="space-y-6">
          <section>
            <div className="flex gap-2">
              <MetricBlock label={aName} m={d.a} />
              <MetricBlock label={bName} m={d.b} />
            </div>
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-ink-3">Differentiation</dt>
              <dd className="font-cond tnum">{d.x === null ? "Not computable" : fmt(d.x, 1, true)} <span className="text-ink-3">on −100…+100, tier-weighted ({d.claim_tier ? `T${d.claim_tier}` : "no tier"})</span></dd>
              <dt className="text-ink-3">HCP impact</dt>
              <dd className="font-cond tnum">{fmt(d.y, 0)} <span className="text-ink-3">/ 100</span></dd>
              <dt className="text-ink-3">Basis</dt>
              <dd>{d.differentiation.explanation}{d.differentiation.uncertain && d.x !== null ? " — indirect confidence interval includes no difference." : ""}</dd>
            </dl>
          </section>

          {d.confounder_alert.applicable ? (
            <section className={cn("rounded-md border px-3.5 py-3", d.confounder_alert.alert ? "border-serious/60 bg-serious/10" : "border-line bg-raised")}>
              <h3 className="flex items-center gap-2 text-sm font-semibold">
                {d.confounder_alert.alert ? <AlertTriangle className="h-4 w-4 text-serious" aria-hidden /> : <ShieldCheck className="h-4 w-4 text-good" aria-hidden />}
                {d.confounder_alert.alert ? "Cross-trial confounder alert" : "No major cross-trial confounders detected"}
              </h3>
              <table className="mt-2 w-full font-cond text-xs tnum">
                <thead className="text-left text-ink-3"><tr><th className="py-1 font-normal">Dimension</th><th className="font-normal">{aName}</th><th className="font-normal">{bName}</th><th className="font-normal">Rule</th></tr></thead>
                <tbody>
                  {d.confounder_alert.items.map((i) => (
                    <tr key={i.dimension} className="border-t border-line/70 align-top">
                      <td className={cn("py-1 pr-2 font-sans", i.triggered && "font-medium text-ink")}>{i.triggered ? "⚠ " : ""}{i.dimension}</td>
                      <td className="pr-2">{i.a}</td><td className="pr-2">{i.b}</td><td className="text-ink-3">{i.threshold}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {d.confounder_alert.caveat && <p className="mt-2 text-xs leading-relaxed text-ink-2"><span className="font-medium text-ink">Fair-balance caveat: </span>{d.confounder_alert.caveat}</p>}
            </section>
          ) : (
            d.confounder_alert.note && <p className="text-xs text-ink-3">{d.confounder_alert.note}</p>
          )}

          {d.objection_card && (
            <section>
              <div className="flex items-baseline justify-between gap-3">
                <h3 className="text-sm font-semibold">MSL objection handler</h3>
                <span className={cn("text-xs font-medium", d.objection_card.status.startsWith("Proactive") ? "text-good" : "text-serious")}>{d.objection_card.status}</span>
              </div>
              <p className="mt-2 text-sm leading-relaxed">{d.objection_card.pillar_statement}</p>
              <ol className="mt-3 space-y-3">
                {d.objection_card.challenges.map((c, i) => (
                  <li key={i} className="border-l-2 pl-3" style={{ borderColor: c.severity === "high" ? "var(--critical)" : c.severity === "medium" ? "var(--serious)" : "var(--line-strong)" }}>
                    <div className="text-xs text-ink-3">{c.type} — {c.severity} risk</div>
                    <p className="mt-0.5 text-sm"><span className="font-medium">Challenge.</span> {c.challenge}</p>
                    <p className="mt-1 text-sm text-ink-2"><span className="font-medium text-ink">Response.</span> {c.response}</p>
                  </li>
                ))}
              </ol>
              {d.objection_card.fair_balance.length > 0 && (
                <p className="mt-3 text-xs text-ink-2"><span className="font-medium text-ink">Comparator leads on: </span>{d.objection_card.fair_balance.join("; ")}</p>
              )}
              <p className="mt-2 text-2xs text-ink-3">{d.objection_card.disclaimer}</p>
            </section>
          )}

          <section>
            <h3 className="text-sm font-semibold">USPI regulatory differences</h3>
            <table className="mt-2 w-full text-xs">
              <thead className="text-left text-ink-3"><tr><th className="py-1 font-normal">Attribute</th><th className="font-normal">{aName}</th><th className="font-normal">{bName}</th></tr></thead>
              <tbody>
                {d.regulatory_diff.map((r) => (
                  <tr key={r.attribute} className="border-t border-line align-top">
                    <td className="py-1.5 pr-2 text-ink-2">{r.attribute}</td>
                    {(["a", "b"] as const).map((k) => (
                      <td key={k} className={cn("py-1.5 pr-2", r.advantage === k.toUpperCase() && "font-medium text-ink")} title={(k === "a" ? r.a_quote : r.b_quote) || undefined}>
                        {String(r[k] ?? "—")}
                        {r.advantage === k.toUpperCase() && <span className="ml-1 text-good" aria-label="advantage">●</span>}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-1 text-2xs text-ink-3">Hover a cell for the verbatim USPI sentence. ● marks the less restrictive label position.</p>
          </section>

          <section>
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold">Supporting evidence</h3>
              <Segmented label="Asset" value={side} onValueChange={setSide} options={[{ value: "a", label: aName }, { value: "b", label: bName }]} />
            </div>
            <div className="mt-1">
              {(side === "a" ? d.a : d.b)?.quotes ? (
                <div className="pt-2"><QuoteList quotes={(side === "a" ? d.a : d.b)!.quotes!} max={6} /></div>
              ) : (
                <EndpointList items={side === "a" ? d.evidence_a : d.evidence_b} />
              )}
            </div>
          </section>
        </div>
      )}
    </Sheet>
  );
}
