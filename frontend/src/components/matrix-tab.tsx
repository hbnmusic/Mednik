"use client";

import { useMemo } from "react";

import type { MatrixNode, MatrixResponse } from "@/lib/api";
import { brandOf, cn, fmt } from "@/lib/utils";

import { cssVar, EChart, useThemeVersion } from "./echart";
import { Empty, TierChip } from "./ui";

const SERIES_VARS = ["--series-1", "--series-2", "--series-3"];
const QUAD_ORDER: MatrixNode["quadrant"][] = ["TR", "TL", "BR", "BL"];
const QUAD_HELP: Record<string, string> = {
  TR: "High differentiation on drivers HCPs weigh heavily — candidate scientific platform pillars.",
  TL: "Drivers HCPs weigh heavily where differentiation is low or unproven — evidence-generation priorities.",
  BR: "Differentiated, but on drivers with narrower prescribing impact — phenotype or niche messaging.",
  BL: "Low differentiation on low-impact drivers.",
};

const SHORT: Record<string, string> = {
  positive_symptoms: "Positive sx", negative_symptoms: "Negative sx", depressive_symptoms: "Depressive sx", manic_symptoms: "Manic sx",
  anxiety_symptoms: "Anxiety", rapid_onset: "Onset", relapse_prevention: "Relapse", psychosocial_functioning: "Function", cognition: "Cognition",
  quality_of_life: "QoL", akathisia_eps: "Akathisia/EPS", weight_metabolic: "Weight/metabolic", sedation: "Sedation", prolactin: "Prolactin",
  anticholinergic_gi: "Anticholinergic/GI", manic_switch: "Manic switch", label_safety: "Label safety", no_titration: "No titration",
  ddi_flexibility: "CYP flexibility", dosing_convenience: "Dosing convenience",
};

export function MatrixTab({ data, onOpen, loading }: { data: MatrixResponse | null; onOpen: (n: MatrixNode) => void; loading: boolean }) {
  const themeV = useThemeVersion();

  const option = useMemo(() => {
    if (!data) return {};
    void themeV;
    const ink = cssVar("--ink"), ink2 = cssVar("--ink-2"), ink3 = cssVar("--ink-3"), line = cssVar("--line"), lineStrong = cssVar("--line-strong");
    const surface = cssVar("--panel");
    const { x: xt, y: yt } = data.thresholds;
    const series = data.comparators.slice(0, 3).map((c, i) => {
      const color = cssVar(SERIES_VARS[i]);
      const nodes = data.nodes.filter((n) => n.comparator === c.generic && n.x !== null);
      return {
        name: brandOf(c.label),
        type: "scatter",
        symbolSize: 12,
        itemStyle: { color, borderColor: surface, borderWidth: 2 },
        emphasis: { scale: 1.4 },
        data: nodes.map((n) => ({
          value: [n.x, n.y], node: n,
          symbol: n.tier === "1A" || n.tier === "1B" ? "circle" : "emptyCircle",
          itemStyle: { opacity: n.uncertain ? 0.5 : 1, ...(n.tier === "1A" || n.tier === "1B" ? {} : { borderColor: color, borderWidth: 2, color: surface }) },
          label: { show: n.y >= yt || Math.abs(n.x ?? 0) >= 40, formatter: SHORT[n.mvd_key] || n.mvd_name, position: "right", color: ink2, fontSize: 11, fontFamily: "IBM Plex Sans Condensed" },
        })),
        labelLayout: { hideOverlap: true },
      };
    });
    const insufficient = data.nodes.filter((n) => n.x === null);
    // insufficient-evidence drivers sit on the left gutter (x = −100 lane) so they read as "unproven", never as a score
    series.push({
      name: "Insufficient evidence",
      type: "scatter",
      symbolSize: 10,
      itemStyle: { color: "transparent", borderColor: ink3, borderWidth: 1.5, borderType: "dashed" } as never,
      emphasis: { scale: 1.3 },
      data: insufficient.map((n, i) => ({
        value: [-96 + (i % 3) * 3, n.y], node: n, symbol: "diamond",
        label: { show: n.y >= yt, formatter: SHORT[n.mvd_key] || n.mvd_name, position: "right", color: ink3, fontSize: 11, fontFamily: "IBM Plex Sans Condensed" },
      })) as never,
      labelLayout: { hideOverlap: true },
    } as never);

    const quadLabel = (text: string, x: string | number, top: string | number, align: "left" | "right") => ({
      type: "text", ...(align === "right" ? { right: typeof x === "string" ? `${100 - parseFloat(x)}%` : x } : { left: x }), top, z: 0, silent: true,
      style: { text, fill: ink3, opacity: 0.55, font: "500 15px 'IBM Plex Sans'", textAlign: align },
    });

    return {
      animation: false,
      grid: { left: 52, right: 28, top: 24, bottom: 48 },
      xAxis: {
        type: "value", min: -100, max: 100, interval: 25, name: "Magnitude of differentiation (+ favours " + brandOf(data.asset_a.label) + ")",
        nameLocation: "middle", nameGap: 30, nameTextStyle: { color: ink2, fontSize: 12 },
        axisLine: { lineStyle: { color: lineStrong } }, axisLabel: { color: ink3, fontSize: 11 }, splitLine: { lineStyle: { color: line, width: 1 } },
      },
      yAxis: {
        type: "value", min: 0, max: 100, interval: 25, name: "HCP decision impact", nameLocation: "middle", nameGap: 36,
        nameTextStyle: { color: ink2, fontSize: 12 }, axisLine: { lineStyle: { color: lineStrong } }, axisLabel: { color: ink3, fontSize: 11 },
        splitLine: { lineStyle: { color: line, width: 1 } },
      },
      graphic: [
        // quadrant names sit just above the impact threshold and along the floor, away from high-impact points
        quadLabel("Core scientific platform pillars", "97%", "44%", "right"),
        quadLabel("Priority evidence generation gaps", 64, "44%", "left"),
        quadLabel("Phenotype / niche tailoring", "97%", "85%", "right"),
        quadLabel("Deprioritized noise", 64, "85%", "left"),
      ],
      tooltip: {
        trigger: "item", backgroundColor: surface, borderColor: line, textStyle: { color: ink, fontSize: 12 }, extraCssText: "max-width:320px;white-space:normal;",
        formatter: (p: { data: { node: MatrixNode } }) => {
          const n = p.data.node;
          return `<b>${n.mvd_name}</b><br/>vs ${n.comparator_label}<br/>X ${n.x === null ? "n/a" : fmt(n.x, 0, true)} &nbsp; Y ${fmt(n.y, 0)} &nbsp; Tier ${n.tier ?? "—"}${n.uncertain && n.x !== null ? " (indirect CI crosses 0)" : ""}<br/><span style="color:${ink2}">${n.explanation}</span>`;
        },
      },
      series: [
        ...series,
        {
          type: "scatter", data: [], silent: true,
          markLine: { silent: true, symbol: "none", label: { show: false }, lineStyle: { color: ink3, type: "dashed", width: 1 },
            data: [{ xAxis: xt }, { yAxis: yt }] },
        },
      ],
    };
  }, [data, themeV]);

  if (!data) {
    return loading ? <Empty title="Computing the matrix…" /> : <Empty title="Choose an indication, a primary asset and at least one comparator">The matrix compares Product A with each comparator on every value driver for the indication.</Empty>;
  }
  const comps = data.comparators.slice(0, 3);
  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col p-4">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-ink-2">
          <span className="font-medium text-ink">{brandOf(data.asset_a.label)} vs</span>
          {comps.map((c, i) => (
            <span key={c.generic} className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: `var(${SERIES_VARS[i]})` }} />
              {c.label}
            </span>
          ))}
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full border-2 border-ink-3" />Tier 2/3 evidence</span>
          <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rotate-45 border border-dashed border-ink-3" />Insufficient evidence</span>
          <span className="text-ink-3">Faded = indirect 95% CI crosses zero</span>
        </div>
        <EChart option={option} className="mt-2 min-h-[420px] flex-1" ariaLabel="Differentiation versus HCP impact scatter plot; table of nodes on the right"
          onClick={(p) => { const d = p.data as { node?: MatrixNode } | undefined; if (d?.node) onOpen(d.node); }} />
      </div>
      <aside className="scroll-thin w-[400px] shrink-0 overflow-y-auto border-l border-line bg-panel">
        {QUAD_ORDER.map((q) => {
          const nodes = data.nodes.filter((n) => n.quadrant === q).sort((a, b) => b.y - a.y || (b.x ?? -999) - (a.x ?? -999));
          if (!nodes.length) return null;
          return (
            <section key={q} className="border-b border-line px-4 py-3">
              <h3 className="text-sm font-semibold">{data.quadrants[q]}</h3>
              <p className="mt-0.5 text-xs text-ink-3">{QUAD_HELP[q]}</p>
              <table className="mt-2 w-full font-cond text-xs tnum">
                <thead className="text-left text-ink-3">
                  <tr><th className="py-1 font-normal">Driver</th><th className="w-14 font-normal">vs</th><th className="w-10 text-right font-normal">X</th><th className="w-10 text-right font-normal">Y</th><th className="w-12 pl-3 font-normal">Tier</th></tr>
                </thead>
                <tbody>
                  {nodes.map((n) => (
                    <tr key={n.id} onClick={() => onOpen(n)} className="cursor-pointer border-t border-line hover:bg-raised">
                      <td className="py-1.5 pr-2 font-sans text-xs text-ink">
                        {n.mvd_name}
                        {n.competitor_advantage && <span className="ml-1 text-critical" title="Comparator advantage">◀</span>}
                      </td>
                      <td className="pr-2 text-ink-2">{brandOf(n.comparator_label)}</td>
                      <td className={cn("text-right", n.x === null && "text-ink-3")}>{n.x === null ? "n/a" : fmt(n.x, 0, true)}</td>
                      <td className="text-right">{fmt(n.y, 0)}</td>
                      <td className="pl-3"><TierChip tier={n.tier} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          );
        })}
      </aside>
    </div>
  );
}
