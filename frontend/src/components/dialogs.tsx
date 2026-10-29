"use client";

import { FileUp, Plus, Trash2 } from "lucide-react";
import { useRef, useState } from "react";

import { send, type AssetRow } from "@/lib/api";
import { cn } from "@/lib/utils";

import { Button, Dialog, Select } from "./ui";

const field = "h-8 w-full rounded-[5px] border border-line bg-panel px-2.5 text-sm text-ink placeholder:text-ink-3";

const PRESETS = [
  { label: "Schizophrenia: Caplyta, Vraylar, Cobenfy", indication: "Schizophrenia",
    assets: [{ generic: "lumateperone", brand: "Caplyta" }, { generic: "cariprazine", brand: "Vraylar" }, { generic: "xanomeline and trospium", brand: "Cobenfy" }] },
  { label: "Bipolar I depression: Caplyta, Vraylar", indication: "Bipolar I Depression",
    assets: [{ generic: "lumateperone", brand: "Caplyta" }, { generic: "cariprazine", brand: "Vraylar" }] },
];

export function SyncDialog({ open, onOpenChange, onStarted, indications }: {
  open: boolean; onOpenChange: (o: boolean) => void; onStarted: (jobId: string, indication: string, assets: { generic: string; brand: string }[]) => void;
  indications: { key: string; label: string }[];
}) {
  const [indication, setIndication] = useState(PRESETS[0].indication);
  const [assets, setAssets] = useState(PRESETS[0].assets);
  const [lit, setLit] = useState(true);
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function start() {
    const clean = assets.filter((a) => a.generic.trim());
    if (!indication.trim() || !clean.length) {
      setErr("Enter an indication and at least one asset (generic name).");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const r = await send<{ job_id: string }>("/api/v1/ingest", "POST", {
        indication, assets: clean.map((a) => ({ generic: a.generic.trim(), brand: a.brand.trim() || null })), include_literature: lit, force_refresh: force,
      });
      onStarted(r.job_id, indication, clean);
      onOpenChange(false);
    } catch (e) {
      setErr(`Couldn't start the sync: ${(e as Error).message}. Is the backend running at the API address?`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Sync live data" wide
      description="Pulls the current USPI (DailyMed / openFDA), ClinicalTrials.gov records, PubMed / Europe PMC literature, preference studies and guidelines, then rebuilds every view. First syncs take a few minutes; later ones reuse the local cache.">
      <div className="space-y-4">
        <div className="flex flex-wrap gap-2">
          {PRESETS.map((p) => (
            <button key={p.label} type="button" onClick={() => { setIndication(p.indication); setAssets(p.assets); }}
              className="rounded-full border border-line px-3 py-1 text-xs text-ink-2 hover:bg-raised hover:text-ink">{p.label}</button>
          ))}
        </div>
        <label className="block">
          <span className="text-xs text-ink-2">Indication</span>
          <input className={cn(field, "mt-1")} list="mednik-indications" value={indication} onChange={(e) => setIndication(e.target.value)} />
          <datalist id="mednik-indications">{indications.map((i) => <option key={i.key} value={i.label} />)}</datalist>
        </label>
        <div>
          <div className="grid grid-cols-[1fr_1fr_32px] gap-2 text-xs text-ink-2"><span>Generic name</span><span>Brand name</span><span /></div>
          {assets.map((a, i) => (
            <div key={i} className="mt-1.5 grid grid-cols-[1fr_1fr_32px] gap-2">
              <input aria-label="Generic name" className={field} value={a.generic} placeholder="e.g. cariprazine"
                onChange={(e) => setAssets(assets.map((x, j) => (j === i ? { ...x, generic: e.target.value } : x)))} />
              <input aria-label="Brand name" className={field} value={a.brand} placeholder="e.g. Vraylar"
                onChange={(e) => setAssets(assets.map((x, j) => (j === i ? { ...x, brand: e.target.value } : x)))} />
              <Button size="icon" variant="quiet" aria-label="Remove asset" onClick={() => setAssets(assets.filter((_, j) => j !== i))}><Trash2 className="h-4 w-4" /></Button>
            </div>
          ))}
          <Button size="sm" variant="quiet" className="mt-2" onClick={() => setAssets([...assets, { generic: "", brand: "" }])}><Plus className="h-3.5 w-3.5" />Add asset</Button>
        </div>
        <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
          <label className="inline-flex items-center gap-2"><input type="checkbox" checked={lit} onChange={(e) => setLit(e.target.checked)} />Include literature extraction</label>
          <label className="inline-flex items-center gap-2"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />Ignore cache (re-fetch everything)</label>
        </div>
        {err && <p className="text-sm text-critical">{err}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="quiet" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" onClick={start} disabled={busy}>{busy ? "Starting…" : "Start sync"}</Button>
        </div>
      </div>
    </Dialog>
  );
}

export function PdfDialog({ open, onOpenChange, indication, assets, onDone }: {
  open: boolean; onOpenChange: (o: boolean) => void; indication: string; assets: AssetRow[]; onDone: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [role, setRole] = useState("asset_evidence");
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function upload() {
    if (!file) return;
    setBusy(true);
    setErr(null);
    setResult(null);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("indication", indication);
    fd.append("assets", picked.join(","));
    fd.append("role", role);
    try {
      setResult(await send<Record<string, unknown>>("/api/v1/ingest/pdf", "POST", fd));
      onDone();
    } catch (e) {
      setErr(`Upload failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) { setFile(null); setResult(null); setErr(null); } }} title="Add a PDF"
      description="Full-text papers, congress posters or data-on-file tables. Text and tables are extracted locally; endpoints are kept only when their verbatim quote is found in the document. The file stays in your local mednik database and is never committed to the repository.">
      <div className="space-y-4">
        <div onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files?.[0]; if (f) setFile(f); }}
          className={cn("flex flex-col items-center justify-center rounded-md border border-dashed px-4 py-8 text-center", over ? "border-accent bg-accent-wash" : "border-line-strong")}>
          <FileUp className="h-5 w-5 text-ink-3" aria-hidden />
          <p className="mt-2 text-sm">{file ? file.name : "Drop a PDF here"}</p>
          <Button size="sm" variant="quiet" className="mt-1" onClick={() => input.current?.click()}>Choose file</Button>
          <input ref={input} type="file" accept="application/pdf" className="hidden" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <label className="block"><span className="text-xs text-ink-2">Use as</span>
            <Select className="mt-1 w-full" label="Document role" value={role} onValueChange={setRole} options={[
              { value: "asset_evidence", label: "Asset evidence (trial results)" },
              { value: "mvd_preference", label: "HCP preference study (DCE / survey)" },
              { value: "guideline", label: "Clinical practice guideline" },
            ]} />
          </label>
          <div><span className="text-xs text-ink-2">Indication</span><p className="mt-1.5 text-sm">{indication || "—"}</p></div>
        </div>
        {role === "asset_evidence" && (
          <fieldset>
            <legend className="text-xs text-ink-2">Assets this document reports on (leave empty to detect by name)</legend>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
              {assets.map((a) => (
                <label key={a.generic} className="inline-flex items-center gap-1.5 text-sm">
                  <input type="checkbox" checked={picked.includes(a.generic)}
                    onChange={(e) => setPicked(e.target.checked ? [...picked, a.generic] : picked.filter((x) => x !== a.generic))} />{a.label}
                </label>
              ))}
            </div>
          </fieldset>
        )}
        {err && <p className="text-sm text-critical">{err}</p>}
        {result && (
          <div className="rounded-md bg-raised px-3 py-2 text-xs text-ink-2">
            <p className="font-medium text-ink">Added “{String(result.title)}”</p>
            <p>Sections: {(result.chunks as string[] | undefined)?.join(", ") || "—"}; tables: {String(result.tables)}</p>
            {"endpoints_extracted" in result && <p>{String(result.endpoints_extracted)} citation-validated endpoints added to the evidence map.</p>}
            {"drivers_recomputed" in result && <p>Value drivers recomputed ({String(result.drivers_recomputed)}).</p>}
            {"guideline" in result && <p>Guideline statements extracted: {String((result.guideline as { recommendations: number }).recommendations)}.</p>}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="quiet" onClick={() => onOpenChange(false)}>Close</Button>
          <Button variant="primary" onClick={upload} disabled={!file || busy || !indication}>{busy ? "Extracting…" : "Add PDF"}</Button>
        </div>
      </div>
    </Dialog>
  );
}
