"use client";

import * as PopoverPrimitive from "@radix-ui/react-popover";
import { ChevronDown, Download, FileUp, Moon, RefreshCw, Sun } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  get, send, url,
  type AssetRow, type GuidelinesResponse, type HeatCell, type HeatColumn, type HeatmapResponse, type Health, type IndicationRow, type Job,
  type MatrixNode, type MatrixResponse, type MvdResponse,
} from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { cn } from "@/lib/utils";

import { CellDrawer } from "./cell-drawer";
import { PdfDialog, SyncDialog } from "./dialogs";
import { HeatmapTab } from "./heatmap-tab";
import { MatrixTab } from "./matrix-tab";
import { MvdTab } from "./mvd-tab";
import { NodeDrawer } from "./node-drawer";
import { Button, Empty, Menu, Segmented, Select, Tabs, TabsContent, TabsList, TabsTrigger } from "./ui";

const PERSONAS: Record<string, string> = {
  evidence: "Evidence-derived weights", inpatient_acute: "Inpatient acute psychiatrist", outpatient_community: "Outpatient community psychiatrist",
  primary_care: "Primary / collaborative care",
};

function useStored<T>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(initial);
  useEffect(() => {
    try {
      const raw = localStorage.getItem(key);
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time hydration from browser storage
      if (raw) setV(JSON.parse(raw));
    } catch {}
  }, [key]);
  const set = useCallback((x: T) => {
    setV(x);
    try {
      localStorage.setItem(key, JSON.stringify(x));
    } catch {}
  }, [key]);
  return [v, set];
}

function ComparatorPicker({ assets, value, onChange, exclude }: { assets: AssetRow[]; value: string[]; onChange: (v: string[]) => void; exclude?: string }) {
  const opts = assets.filter((a) => a.generic !== exclude);
  const label = value.length ? opts.filter((a) => value.includes(a.generic)).map((a) => a.brand || a.generic).join(", ") : "Choose comparators";
  return (
    <PopoverPrimitive.Root>
      <PopoverPrimitive.Trigger className="inline-flex h-8 max-w-64 items-center gap-2 rounded-[5px] border border-line bg-panel px-2.5 text-sm hover:bg-raised" aria-label="Comparators">
        <span className="truncate">{label}</span><ChevronDown className="h-3.5 w-3.5 shrink-0 text-ink-3" />
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content sideOffset={4} align="start" className="z-50 w-64 rounded-md border border-line bg-panel p-2 shadow-lg">
          <p className="px-1 pb-1 text-xs text-ink-3">Up to three comparators</p>
          {opts.map((a) => {
            const on = value.includes(a.generic);
            return (
              <label key={a.generic} className={cn("flex items-center gap-2 rounded px-1.5 py-1 text-sm hover:bg-raised", !on && value.length >= 3 && "opacity-50")}>
                <input type="checkbox" checked={on} disabled={!on && value.length >= 3}
                  onChange={(e) => onChange(e.target.checked ? [...value, a.generic] : value.filter((x) => x !== a.generic))} />
                {a.label}
              </label>
            );
          })}
          {!opts.length && <p className="px-1 py-1 text-sm text-ink-3">Sync at least two assets to compare.</p>}
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}

export function Workspace() {
  const [health, setHealth] = useState<Health | null>(null);
  const [apiDown, setApiDown] = useState(false);
  const [assets, setAssets] = useState<AssetRow[]>([]);
  const [indications, setIndications] = useState<IndicationRow[]>([]);
  const [metaInd, setMetaInd] = useState<{ key: string; label: string }[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);

  const [indication, setIndication] = useStored<string>("mednik-ind", "");
  const [assetA, setAssetA] = useStored<string>("mednik-a", "");
  const [comparators, setComparators] = useStored<string[]>("mednik-b", []);
  const [persona, setPersona] = useStored<string>("mednik-persona", "evidence");
  const [tier, setTier] = useStored<string>("mednik-tier", "all");
  const [tab, setTab] = useStored<string>("mednik-tab", "mvd");
  const [hmScope, setHmScope] = useStored<string>("mednik-hm-scope", "all");
  const [overrides, setOverrides] = useState<Record<string, number>>({});
  const [theme, setTheme] = useState<"light" | "dark" | null>(null);

  const [error, setError] = useState<string | null>(null);
  const [savingW, setSavingW] = useState(false);

  const [node, setNode] = useState<MatrixNode | null>(null);
  const [cell, setCell] = useState<{ row: string; col: HeatColumn; cell: HeatCell } | null>(null);
  const [syncOpen, setSyncOpen] = useState(false);
  const [pdfOpen, setPdfOpen] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [logOpen, setLogOpen] = useState(false);

  /* ---------------- bootstrap ---------------- */
  useEffect(() => {
    get<Health>("/api/v1/health").then((h) => { setHealth(h); setApiDown(false); }).catch(() => setApiDown(true));
    get<AssetRow[]>("/api/v1/assets").then(setAssets).catch(() => {});
    get<IndicationRow[]>("/api/v1/indications").then(setIndications).catch(() => {});
    get<{ indications: { key: string; label: string }[] }>("/api/v1/meta").then((m) => setMetaInd(m.indications)).catch(() => {});
  }, [refreshKey]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- read the theme the inline script applied before paint
    setTheme((document.documentElement.dataset.theme as "light" | "dark") || null);
  }, []);

  // sensible defaults once data exists
  useEffect(() => {
    if (!indication && indications.length) setIndication(indications.find((i) => i.n_drivers)?.key || indications[0].key);
    if ((!assetA || !assets.some((a) => a.generic === assetA)) && assets.length) setAssetA(assets[0].generic);
  }, [indications, assets, indication, assetA, setIndication, setAssetA]);
  useEffect(() => {
    const valid = comparators.filter((c) => c !== assetA && assets.some((a) => a.generic === c));
    if (valid.length !== comparators.length) setComparators(valid);
    else if (!valid.length && assets.length > 1) setComparators(assets.filter((a) => a.generic !== assetA).slice(0, 2).map((a) => a.generic));
  }, [assets, assetA, comparators, setComparators]);

  const overridesParam = useMemo(() => (Object.keys(overrides).length ? JSON.stringify(overrides) : undefined), [overrides]);
  const nodeParams = useMemo(() => ({ indication, asset_a: assetA, persona, tier_filter: tier, overrides: overridesParam }),
    [indication, assetA, persona, tier, overridesParam]);

  const mvdQ = useApi<MvdResponse>(indication ? ["/api/v1/mvd", { indication, persona, overrides: overridesParam }, refreshKey] : null);
  const glQ = useApi<GuidelinesResponse>(indication ? ["/api/v1/guidelines", { indication }, refreshKey] : null);
  const mxQ = useApi<MatrixResponse>(indication && assetA && comparators.length
    ? ["/api/v1/matrix", { indication, asset_a: assetA, comparators: comparators.join(","), persona, tier_filter: tier, overrides: overridesParam }, refreshKey] : null);
  const hmQ = useApi<HeatmapResponse>(assetA
    ? ["/api/v1/heatmap", { asset: assetA, persona, indication: hmScope === "indication" ? indication : undefined }, refreshKey] : null);
  const mvd = mvdQ.data, guidelines = glQ.data, matrix = mxQ.data, heatmap = hmQ.data;
  const loading = { mvd: mvdQ.loading, matrix: mxQ.loading, heatmap: hmQ.loading };
  const queryError = [mvdQ.error, glQ.error, mxQ.error, hmQ.error].find((e) => e && !/not ingested yet/.test(e)) || null;

  /* ---------------- job polling ---------------- */
  useEffect(() => {
    if (!job || job.status === "done" || job.status === "error") return;
    const t = setInterval(async () => {
      try {
        const j = await get<Job>(`/api/v1/jobs/${job.id}`);
        setJob(j);
        if (j.status === "done" || j.status === "error") setRefreshKey((k) => k + 1);
      } catch {}
    }, 2000);
    return () => clearInterval(t);
  }, [job]);

  function toggleTheme() {
    const isDark = theme ? theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = isDark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("mednik-theme", next); } catch {}
    setTheme(next);
  }

  async function saveWeights() {
    if (!mvd) return;
    setSavingW(true);
    try {
      for (const [k, v] of Object.entries(overrides)) {
        const d = mvd.drivers.find((x) => x.key === k);
        if (d) await send(`/api/v1/mvd/${d.id}`, "PUT", { score: v });
      }
      setOverrides({});
      setRefreshKey((x) => x + 1);
    } catch (e) {
      setError(`Couldn't save weights: ${(e as Error).message}`);
    } finally {
      setSavingW(false);
    }
  }

  const indName = indications.find((i) => i.key === indication)?.name || indication;
  const exportUrl = (format: string) => url("/api/v1/export/dossier", { indication, asset_a: assetA, comparators: comparators.join(","), format, persona, tier_filter: tier, overrides: overridesParam });
  const lastLog = job?.log[job.log.length - 1]?.msg;
  const noData = !apiDown && health && health.counts.assets === 0;

  return (
    <div className="flex h-full flex-col">
      {/* ---------------- command bar ---------------- */}
      <header className="flex flex-wrap items-center gap-2 border-b border-line bg-panel px-4 py-2">
        <span className="mr-2 text-[17px] font-semibold tracking-tight text-ink">mednik</span>
        <Select label="Indication" className="w-52" value={indication || undefined} onValueChange={(v) => { setIndication(v); setOverrides({}); }} placeholder="Indication"
          options={indications.map((i) => ({ value: i.key, label: i.name, hint: i.n_drivers ? undefined : "no drivers" }))} />
        <Select label="Primary asset (Product A)" className="w-56" value={assetA || undefined} onValueChange={setAssetA} placeholder="Product A"
          options={assets.map((a) => ({ value: a.generic, label: a.label }))} />
        <span className="text-sm text-ink-3">vs</span>
        <ComparatorPicker assets={assets} value={comparators} onChange={setComparators} exclude={assetA} />
        <div className="ml-auto flex items-center gap-2">
          <Button variant="primary" onClick={() => setSyncOpen(true)}><RefreshCw className={cn("h-3.5 w-3.5", job?.status === "running" && "animate-spin")} aria-hidden />Sync live data</Button>
          <Button onClick={() => setPdfOpen(true)}><FileUp className="h-3.5 w-3.5" aria-hidden />Add PDF</Button>
          <Menu trigger={<Button disabled={!matrix}><Download className="h-3.5 w-3.5" aria-hidden />Export</Button>} items={[
            { label: "Briefing dossier (PDF)", hint: "Current selection, fully cited", onSelect: () => window.open(exportUrl("pdf"), "_blank") },
            { label: "Briefing dossier (Markdown)", onSelect: () => window.open(exportUrl("md"), "_blank") },
          ]} />
          <Button size="icon" variant="quiet" aria-label="Toggle light and dark theme" onClick={toggleTheme}>
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
        </div>
      </header>

      {/* ---------------- tabs ---------------- */}
      <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
        <div className="flex flex-wrap items-end gap-x-6 gap-y-2 border-b border-line bg-panel px-4">
          <TabsList>
            <TabsTrigger value="mvd">Value drivers</TabsTrigger>
            <TabsTrigger value="matrix">Differentiation matrix</TabsTrigger>
            <TabsTrigger value="heatmap">Evidence map</TabsTrigger>
          </TabsList>
          <div className="ml-auto flex items-center gap-2 pb-1.5">
            {tab !== "heatmap" && (
              <Select label="HCP persona" className="w-64" value={persona} onValueChange={setPersona}
                options={Object.entries(PERSONAS).map(([value, label]) => ({ value, label }))} />
            )}
            {tab === "matrix" && (
              <Segmented label="Evidence filter" value={tier} onValueChange={setTier}
                options={[{ value: "all", label: "All tiers" }, { value: "tier1", label: "Tier 1A/1B only" }]} />
            )}
            {tab === "heatmap" && (
              <Segmented label="Columns" value={hmScope} onValueChange={setHmScope}
                options={[{ value: "all", label: "All indications" }, { value: "indication", label: indName ? `${indName} only` : "This indication" }]} />
            )}
          </div>
        </div>
        {(error || queryError) && <div className="border-b border-critical/40 bg-critical/10 px-4 py-1.5 text-sm text-ink">{error || queryError}</div>}
        <div className="min-h-0 flex-1">
          {apiDown ? (
            <Empty title="mednik can't reach its backend">
              Start it with <code className="rounded bg-raised px-1">python mednik.py dev</code> from the repository folder (in a Codespace it starts automatically), then reload.
            </Empty>
          ) : noData ? (
            <Empty title="No evidence ingested yet">
              <p>Sync live data to pull labels, registry records, literature, preference studies and guidelines.</p>
              <Button variant="primary" className="mt-3" onClick={() => setSyncOpen(true)}>Sync live data</Button>
            </Empty>
          ) : (
            <>
              <TabsContent value="mvd" className="h-full">
                <MvdTab data={mvd} guidelines={guidelines} overrides={overrides} loading={!!loading.mvd} saving={savingW}
                  setOverride={(k, v) => setOverrides((o) => ({ ...o, [k]: Math.round(v * 100) / 100 }))} resetOverrides={() => setOverrides({})} saveOverrides={saveWeights} />
              </TabsContent>
              <TabsContent value="matrix" className="h-full">
                <MatrixTab data={matrix} loading={!!loading.matrix} onOpen={setNode} />
              </TabsContent>
              <TabsContent value="heatmap" className="h-full">
                <HeatmapTab data={heatmap} loading={!!loading.heatmap} highlightIndication={indName} onOpen={(row, col, c) => setCell({ row, col, cell: c })} />
              </TabsContent>
            </>
          )}
        </div>
      </Tabs>

      {/* ---------------- status bar ---------------- */}
      <footer className="relative flex h-7 shrink-0 items-center gap-4 border-t border-line bg-panel px-4 font-cond text-xs text-ink-2 tnum">
        <span className="inline-flex items-center gap-1.5">
          <span className={cn("h-2 w-2 rounded-full", apiDown ? "bg-critical" : health ? "bg-good" : "bg-ink-3")} aria-hidden />
          {apiDown ? "Backend offline" : health ? `API ${health.version}` : "Connecting"}
        </span>
        {health && (
          <span>
            Extraction: {health.llm_enabled ? `${health.llm_provider} ${health.llm_model ?? ""}` : "deterministic only (no LLM key)"}
          </span>
        )}
        {health && <span className="hidden md:inline">{health.counts.assets} assets, {health.counts.studies} studies, {health.counts.endpoints} endpoints, {health.counts.documents} documents</span>}
        {health?.auth_required && <a href="/logout" className="underline-offset-2 hover:text-ink hover:underline">Sign out</a>}
        {job && (
          <button type="button" onClick={() => setLogOpen(!logOpen)} className="ml-auto flex min-w-0 items-center gap-2 text-left hover:text-ink">
            <span className={cn("shrink-0 font-medium", job.status === "error" ? "text-critical" : job.status === "done" ? "text-good" : "text-accent")}>
              {job.status === "running" || job.status === "queued" ? "Syncing" : job.status === "done" ? "Sync complete" : "Sync failed"}
            </span>
            <span className="truncate">{lastLog}</span>
          </button>
        )}
        {logOpen && job && (
          <div className="scroll-thin absolute bottom-7 right-0 z-40 max-h-80 w-[min(720px,100vw)] overflow-y-auto border border-line bg-panel p-3 font-cond text-xs shadow-xl">
            {job.log.map((l, i) => <div key={i} className="py-0.5"><span className="text-ink-3">{l.t.slice(11, 19)}</span> {l.msg}</div>)}
            {job.error && <pre className="mt-2 whitespace-pre-wrap text-critical">{job.error.slice(-1200)}</pre>}
          </div>
        )}
      </footer>

      <NodeDrawer node={node} params={nodeParams} onClose={() => setNode(null)} />
      <CellDrawer target={cell} asset={assetA} onClose={() => setCell(null)} />
      <SyncDialog open={syncOpen} onOpenChange={setSyncOpen} indications={metaInd}
        onStarted={(id) => { setJob({ id, status: "queued", log: [], summary: null, error: null }); setLogOpen(true); }} />
      <PdfDialog open={pdfOpen} onOpenChange={setPdfOpen} indication={indName} assets={assets} onDone={() => setRefreshKey((k) => k + 1)} />
    </div>
  );
}
