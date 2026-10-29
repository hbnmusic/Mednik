"use client";

import { Download, FlaskConical } from "lucide-react";
import { useState } from "react";

import { send, url, type CellEvidence, type HeatCell, type HeatColumn, type StudyConcept } from "@/lib/api";
import { useApi } from "@/lib/hooks";

import { EndpointList, SourceLink } from "./evidence";
import { isGap } from "./heatmap-tab";
import { Button, Sheet, ValidityChip } from "./ui";

function Concept({ c }: { c: StudyConcept }) {
  const ss = c.sample_size;
  return (
    <div className="space-y-5">
      <section>
        <div className="text-xs text-ink-3">{c.design.type}</div>
        <h4 className="mt-0.5 text-base font-semibold leading-snug">{c.design.title}</h4>
        <p className="mt-1 text-sm text-ink-2">{c.design.rationale}</p>
        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-ink-3">Arms</dt><dd>{c.design.arms.join("; ")}</dd>
          <dt className="text-ink-3">Blinding</dt><dd>{c.design.blinding}</dd>
          <dt className="text-ink-3">Duration</dt><dd className="tnum">{c.duration_weeks} weeks</dd>
          <dt className="text-ink-3">Primary endpoint</dt><dd>{c.primary_endpoint}</dd>
          <dt className="text-ink-3">Analysis</dt><dd>{c.design.primary_analysis}</dd>
        </dl>
      </section>
      <section>
        <h4 className="text-sm font-semibold">Sample size</h4>
        <p className="mt-1 font-cond text-xl font-medium tnum">{ss.n_per_arm_with_dropout ?? "—"} <span className="text-sm font-normal text-ink-2">per arm, {ss.total_n ?? "—"} total</span></p>
        <p className="mt-1 text-xs text-ink-2">{ss.basis}; {ss.assumption}; 80% power, two-sided α 0.05, {(ss.dropout_assumed * 100).toFixed(0)}% dropout ({ss.n_per_arm} evaluable per arm).</p>
        {ss.note && <p className="mt-1 text-xs text-ink-3">{ss.note}</p>}
      </section>
      {c.secondary_endpoints.length > 0 && (
        <section>
          <h4 className="text-sm font-semibold">Secondary endpoints used by precedent trials</h4>
          <ul className="mt-1 text-sm text-ink-2">{c.secondary_endpoints.map((s) => <li key={s.scale}>{s.label} <span className="text-ink-3">({s.n_precedents})</span></li>)}</ul>
        </section>
      )}
      {(["inclusion", "exclusion"] as const).map((k) => c[k].length > 0 && (
        <section key={k}>
          <h4 className="text-sm font-semibold">{k === "inclusion" ? "Key inclusion criteria" : "Key exclusion criteria"}</h4>
          <ul className="mt-1 space-y-1.5">
            {c[k].slice(0, 8).map((i) => (
              <li key={i.criterion} className="text-sm">
                <span className="font-cond tnum text-ink-3">{i.pct_precedents}%</span> {i.criterion}
                <div className="text-xs text-ink-3">e.g. <SourceLink id={i.example_nct} />: “{i.example}”</div>
              </li>
            ))}
          </ul>
        </section>
      ))}
      <section>
        <h4 className="text-sm font-semibold">Precedent trials ({c.n_precedents})</h4>
        <table className="mt-1 w-full font-cond text-xs tnum">
          <thead className="text-left text-ink-3"><tr><th className="py-1 font-normal">Trial</th><th className="font-normal">Sponsor</th><th className="font-normal">Phase</th><th className="text-right font-normal">N</th><th className="pl-2 font-normal">Endpoint timing</th></tr></thead>
          <tbody>
            {c.precedents.slice(0, 12).map((p) => (
              <tr key={p.nct_id} className="border-t border-line align-top">
                <td className="py-1 pr-2"><SourceLink id={p.nct_id} />{p.competitor_trial && <span className="ml-1 text-ink-3">competitor</span>}</td>
                <td className="pr-2 font-sans">{p.sponsor}</td>
                <td className="pr-2">{p.phase.replaceAll("PHASE", "")}</td>
                <td className="text-right">{p.n ?? ""}</td>
                <td className="pl-2">{p.outcomes[0]?.time_frame}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

export function CellDrawer({ target, asset, onClose }: {
  target: { row: string; col: HeatColumn; cell: HeatCell } | null; asset: string; onClose: () => void;
}) {
  const tKey = target ? `${target.row}|${target.col.key}` : null;
  const q = useApi<CellEvidence>(target ? ["/api/v1/heatmap/cell", { asset, row: target.row, column: target.col.key }, 0] : null);
  const ev = q.loading ? null : q.data;
  const [conceptState, setConcept] = useState<{ key: string | null; c: StudyConcept | null }>({ key: null, c: null });
  const concept = conceptState.key === tKey ? conceptState.c : null;
  const [busy, setBusy] = useState(false);
  const [draftErr, setErr] = useState<{ key: string | null; msg: string } | null>(null);
  const err = q.error || (draftErr && draftErr.key === tKey ? draftErr.msg : null);

  async function draft() {
    if (!target) return;
    setBusy(true);
    setErr(null);
    try {
      setConcept({ key: tKey, c: await send<StudyConcept>("/api/v1/study-concept", "POST", { asset, row: target.row, column: target.col.key, cell_state: target.cell.state }) });
    } catch (e) {
      setErr({ key: tKey, msg: `Couldn't draft the study concept: ${(e as Error).message}` });
    } finally {
      setBusy(false);
    }
  }

  const gap = target ? isGap(target.cell) : false;
  return (
    <Sheet open={!!target} onOpenChange={(o) => !o && onClose()} title={ev?.row.label ?? target?.row ?? ""}
      subtitle={target ? <span className="flex items-center gap-2">{target.col.label}<ValidityChip level={target.cell.validity} /></span> : null}>
      {err && <p className="mb-3 text-sm text-critical">{err}</p>}
      {gap && !concept && (
        <div className="mb-5 rounded-md border border-accent/40 bg-accent-wash px-3.5 py-3">
          <p className="text-sm">
            {target?.cell.state === "evidence" ? "Evidence here is low-validity (L1–L2)." : "No study has reported this endpoint here."}
            {" "}Draft a Phase 4 / RWE / IIS concept from how competitors designed trials on this endpoint.
          </p>
          <Button variant="primary" size="sm" className="mt-2" onClick={draft} disabled={busy}>
            <FlaskConical className="h-3.5 w-3.5" aria-hidden />{busy ? "Querying ClinicalTrials.gov…" : "Draft study concept"}
          </Button>
        </div>
      )}
      {concept ? (
        <div>
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-sm font-semibold">Study concept synopsis</h3>
            <a href={url(`/api/v1/study-concept/${concept.id}.md`)} className="inline-flex items-center gap-1 text-sm text-accent hover:underline">
              <Download className="h-3.5 w-3.5" aria-hidden />Download .md
            </a>
          </div>
          <Concept c={concept} />
        </div>
      ) : (
        <>
          {!ev && !err && <p className="text-sm text-ink-3">Loading evidence…</p>}
          {ev && (
            <>
              <h3 className="text-sm font-semibold">Reported results ({ev.endpoints.length})</h3>
              <EndpointList items={ev.endpoints} empty="No reported results for this endpoint in this column." />
              {ev.registered_trials.length > 0 && (
                <>
                  <h3 className="mt-5 text-sm font-semibold">Registered trials measuring this endpoint</h3>
                  <ul className="mt-1">
                    {ev.registered_trials.map((t) => (
                      <li key={t.id} className="border-b border-line py-2 text-sm last:border-0">
                        <div className="flex items-center gap-2"><SourceLink id={t.nct_id || t.ref} /><span className="text-xs text-ink-3">{t.status?.toLowerCase().replaceAll("_", " ")}, {t.phase}</span></div>
                        <div className="mt-0.5 text-xs text-ink-2">{t.outcome_type.toLowerCase()} outcome: {t.registered_measure} ({t.time_frame})</div>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
        </>
      )}
    </Sheet>
  );
}
