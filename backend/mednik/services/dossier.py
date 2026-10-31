"""Executive export — Markdown / PDF Medical Strategy Briefing Dossier with full citations."""

from __future__ import annotations

import base64
import html as html_lib
import io
import re
from datetime import datetime, timezone

import markdown as md_lib
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Asset, Indication, MarketValueDriver, StudyConcept
from .evidence import ref_url
from .guidelines import gap_to_guideline, guideline_status
from .heatmap import build_heatmap
from .matrix import QUADRANTS, asset_label, build_matrix, node_detail
from .mvd import effective_scores, serialize_driver
from .tiering import TIER_RULES, VALIDITY_RULES


def _cite(ref: str | None) -> str:
    if not ref:
        return ""
    url = ref_url(ref)
    return f"[{ref}]({url})" if url else ref


_SERIES = ["#1f5fbf", "#c2410c", "#0f766e"]
_QUAD_NAME = {"TR": "Core pillars", "TL": "Evidence-generation priorities", "BR": "Niche tailoring", "BL": "Deprioritized"}


def _short(name: str) -> str:
    n = re.sub(r"\s*\(.*?\)", "", name).strip()
    return n if len(n) <= 24 else n[:23] + "…"


def quadrant_svg(mx: dict, comparators: list[Asset]) -> str:
    """Differentiation (X) × decision impact (Y) map of the scored drivers, drawn as SVG so it prints."""
    W, H, L_, R_, T_, B_ = 640, 420, 56, 16, 14, 46
    pw, ph = W - L_ - R_, H - T_ - B_
    xt, yt = mx["thresholds"]["x"], mx["thresholds"]["y"]
    px = lambda v: L_ + (v + 100) / 200 * pw  # noqa: E731
    py = lambda v: T_ + (1 - v / 100) * ph  # noqa: E731
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Helvetica, Arial, sans-serif">',
         f'<rect x="0" y="0" width="{W}" height="{H}" fill="#fff"/>',
         f'<rect x="{L_}" y="{T_}" width="{pw}" height="{ph}" fill="#fafafa" stroke="#999" stroke-width="0.8"/>']
    for v in (-100, -50, 0, 50, 100):
        o.append(f'<line x1="{px(v)}" y1="{T_}" x2="{px(v)}" y2="{T_ + ph}" stroke="#e3e3e3" stroke-width="0.6"/>'
                 f'<text x="{px(v)}" y="{T_ + ph + 13}" font-size="9" fill="#555" text-anchor="middle">{v:+d}</text>')
    for v in (0, 25, 50, 75, 100):
        o.append(f'<line x1="{L_}" y1="{py(v)}" x2="{L_ + pw}" y2="{py(v)}" stroke="#e3e3e3" stroke-width="0.6"/>'
                 f'<text x="{L_ - 5}" y="{py(v) + 3}" font-size="9" fill="#555" text-anchor="end">{v}</text>')
    o.append(f'<line x1="{px(xt)}" y1="{T_}" x2="{px(xt)}" y2="{T_ + ph}" stroke="#555" stroke-width="0.9" stroke-dasharray="4 3"/>'
             f'<line x1="{L_}" y1="{py(yt)}" x2="{L_ + pw}" y2="{py(yt)}" stroke="#555" stroke-width="0.9" stroke-dasharray="4 3"/>')
    for q, (qx, qy, anchor) in {"TR": (L_ + pw - 6, T_ + 13, "end"), "TL": (L_ + 6, T_ + 13, "start"),
                                "BR": (L_ + pw - 6, T_ + ph - 6, "end"), "BL": (L_ + 6, T_ + ph - 6, "start")}.items():
        o.append(f'<text x="{qx}" y="{qy}" font-size="10" fill="#888" text-anchor="{anchor}">{_QUAD_NAME[q]}</text>')
    o.append(f'<text x="{L_ + pw / 2}" y="{H - 8}" font-size="10" fill="#333" text-anchor="middle">Magnitude of differentiation (+ favours {html_lib.escape(asset_label_short(mx))})</text>')
    o.append(f'<text transform="translate(13 {T_ + ph / 2}) rotate(-90)" font-size="10" fill="#333" text-anchor="middle">HCP decision impact</text>')
    colour = {c.generic_name: _SERIES[i % 3] for i, c in enumerate(comparators)}
    placed: list[tuple[float, float, float, float]] = []

    def free(b):
        return all(b[2] < q[0] or b[0] > q[2] or b[3] < q[1] or b[1] > q[3] for q in placed) and b[0] >= L_ and b[2] <= L_ + pw

    pts = sorted((n for n in mx["nodes"] if n["x"] is not None), key=lambda n: -n["y"])
    placed += [(px(max(-100, min(100, n["x"]))) - 6, py(n["y"]) - 6, px(max(-100, min(100, n["x"]))) + 6, py(n["y"]) + 6) for n in pts]  # markers block labels too
    for n in pts:
        cx, cy = px(max(-100, min(100, n["x"]))), py(n["y"])
        col = colour.get(n["comparator"], "#333")
        solid = n["tier"] in ("1A", "1B")
        o.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="{col if solid else "#fff"}" stroke="{col}" stroke-width="1.6"/>')
        text = html_lib.escape(_short(n["mvd_name"]))
        w = 5.6 * len(text) + 4
        for dx, dy, anchor in ((8, 3, "start"), (-8, 3, "end"), (0, -9, "middle"), (0, 16, "middle"), (8, -9, "start"), (8, 16, "start")):
            tx, ty = cx + dx, cy + dy
            x0 = tx if anchor == "start" else tx - w if anchor == "end" else tx - w / 2
            box = (x0, ty - 9, x0 + w, ty + 2)
            if free(box):
                placed.append(box)
                o.append(f'<text x="{tx:.1f}" y="{ty:.1f}" font-size="8.5" fill="#222" text-anchor="{anchor}">{text}</text>')
                break
    ly = T_ + ph + 28
    lx = L_
    for c in comparators[:3]:
        o.append(f'<circle cx="{lx + 4}" cy="{ly - 3}" r="4" fill="{colour[c.generic_name]}"/><text x="{lx + 12}" y="{ly}" font-size="9" fill="#333">vs {html_lib.escape(asset_label(c))}</text>')
        lx += 22 + 5.2 * len(asset_label(c)) + 14
    o.append(f'<circle cx="{lx + 4}" cy="{ly - 3}" r="4" fill="#fff" stroke="#555" stroke-width="1.4"/><text x="{lx + 12}" y="{ly}" font-size="9" fill="#333">open = Tier 2–3 evidence</text>')
    o.append("</svg>")
    return "".join(o)


def asset_label_short(mx: dict) -> str:
    return (mx.get("asset_a") or {}).get("label", "Product A").split(" (")[0]


def build_dossier(session: Session, indication: Indication, asset_a: Asset, comparators: list[Asset], *, persona: str = "evidence",
                  overrides: dict[str, float] | None = None, tier_filter: str = "all") -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    drivers = session.execute(select(MarketValueDriver).where(MarketValueDriver.indication_id == indication.id)).scalars().all()
    eff = effective_scores(drivers, persona, overrides)
    mx = build_matrix(session, indication, asset_a, comparators, persona=persona, overrides=overrides, tier_filter=tier_filter)
    hm = build_heatmap(session, asset_a, indication_keys=[indication.key], persona=persona)
    refs: set[str] = set()
    L: list[str] = []
    L += [f"# Medical Strategy Briefing — {asset_label(asset_a)} in {indication.name}",
          f"_Comparators: {', '.join(asset_label(c) for c in comparators)} · Persona: {persona} · Evidence filter: {tier_filter} · Generated {now} by mednik_", "",
          "> Internal medical-affairs working document. Cross-trial comparisons are indirect unless stated; not for promotional use.", ""]

    # --- Module 1 ---------------------------------------------------------------------
    L += ["## 1. Indication Market Value Drivers", "", "| Rank | Driver | Domain | Impact | Basis | Evidence |", "|---|---|---|---|---|---|"]
    ranked = sorted(drivers, key=lambda d: -eff[d.attribute_key]["score"])
    for i, d in enumerate(ranked, 1):
        sd = serialize_driver(d, eff[d.attribute_key])
        ids = sd["supporting_ids"][:4]
        refs |= set(ids)
        L.append(f"| {i} | {d.attribute_name} | {d.category} | {sd['effective_score']:.2f} | {sd['method']} ({sd['score_source']}) | {', '.join(_cite(x) for x in ids) or '—'} |")
    L.append("")
    if drivers and all(d.method == "evidence_floor" for d in drivers) and not overrides:
        L += ["> **No preference or guideline evidence was found for this indication, so every driver sits at the 0.10 floor and the "
              "ranking and the impact axis below carry no information.** Re-run the sync (it now searches the condition by its standard names) "
              "or set driver weights manually in the dashboard.", ""]
    elif drivers and sum(1 for d in drivers if d.method == "evidence_floor") >= len(drivers) / 2:
        L += [f"> {sum(1 for d in drivers if d.method == 'evidence_floor')} of {len(drivers)} drivers have no preference or guideline evidence and sit at the 0.10 floor.", ""]

    # --- Module 2 ---------------------------------------------------------------------
    L += ["## 2. Competitive Differentiation & Impact Matrix", ""]
    scored = [n for n in mx["nodes"] if n["x"] is not None]
    if scored:
        svg = quadrant_svg(mx, comparators)
        L += [f"![Differentiation versus impact map](data:image/svg+xml;base64,{base64.b64encode(svg.encode()).decode()})", ""]
    for q in ("TR", "TL", "BR", "BL"):
        nodes = [n for n in scored if n["quadrant"] == q]
        if not nodes:
            continue
        L += [f"### {QUADRANTS[q]}", "", "| Driver | vs | X | Y | Tier | Basis |", "|---|---|---|---|---|---|"]
        for n in sorted(nodes, key=lambda n: -(n["y"])):
            L.append(f"| {n['mvd_name']} | {n['comparator_label']} | {n['x']:+.0f} | {n['y']:.0f} | {n['tier'] or '—'} | {n['explanation']} |")
        L.append("")
    unscored = [n for n in mx["nodes"] if n["x"] is None]
    if unscored:
        L += ["### Insufficient evidence — not placed on the map", "", "| Driver | vs | Impact (Y) | Why |", "|---|---|---|---|"]
        for n in sorted(unscored, key=lambda n: (-(n["y"]), n["mvd_name"])):
            L.append(f"| {n['mvd_name']} | {n['comparator_label']} | {n['y']:.0f} | {n['explanation']} |")
        L.append("")

    # --- pillars with confounders + objection cards ---------------------------------
    pillars = [n for n in mx["nodes"] if n["quadrant"] == "TR"]
    if pillars:
        L += ["### Core pillar audit (Adjacency A + B)", ""]
        for n in pillars[:8]:
            comp = next(c for c in comparators if c.generic_name == n["comparator"])
            det = node_detail(session, indication, asset_a, comp, n["mvd_key"], persona=persona, overrides=overrides, tier_filter=tier_filter)
            for e in det["evidence_a"] + det["evidence_b"]:
                refs.add(e["study"]["nct_id"] or e["source_id"])
            L += [f"#### {n['mvd_name']} vs {n['comparator_label']}"]
            ca = det["confounder_alert"]
            if ca.get("applicable"):
                L.append(f"**Cross-trial confounder alert:** {'YES' if ca['alert'] else 'no'} — {ca['caveat']}")
            card = det["objection_card"]
            if card:
                L += [f"**Pillar statement ({card['status']}):** {card['pillar_statement']}", ""]
                for c in card["challenges"]:
                    L += [f"- _{c['type']} ({c['severity']})_ — **Challenge:** {c['challenge']}", f"  **Response:** {c['response']}"]
                if card["fair_balance"]:
                    L.append("- **Fair-balance notes:** " + "; ".join(card["fair_balance"]))
            L.append("")

    # --- Module 3 --------------------------------------------------------------------
    L += ["## 3. Evidence Heatmap (selected indication)", ""]
    cols = hm["columns"]
    if cols:
        L.append("| Endpoint | " + " | ".join(c["label"] for c in cols) + " |")
        L.append("|---|" + "---|" * len(cols))
        for r in hm["rows"]:
            cells = []
            for c in cols:
                cell = hm["cells"][r["key"]][c["key"]]
                cells.append("░ white space" if cell["state"] == "white_space" else ("◌ registered" if cell["state"] == "registered" else f"L{cell['validity']} · {cell['summary']}"))
                refs |= set(cell.get("refs") or [])
            L.append(f"| {r['label']} | " + " | ".join(cells) + " |")
    L.append("")

    # --- Adjacency D ----------------------------------------------------------------------
    L += ["## 4. Guideline Position & Gap-to-Guideline", ""]
    for a in [asset_a] + comparators:
        st = guideline_status(session, indication, a)
        L.append(f"- **{asset_label(a)}:** {st['status']}" + (f" — “{st['best']['quote'][:220]}” ({_cite(st['best']['source_id'])})" if st.get("best") else ""))
    gg = gap_to_guideline(session, indication, asset_a, hm)
    L += ["", f"**{asset_label(asset_a)} — {gg['summary']}**", ""]
    for c in gg["checks"]:
        L.append(f"- [{'x' if c['passed'] else ' '}] {c['label']}" + ("" if c["passed"] else " — blocking cells: " + ", ".join(
            (f"{b.get('row')} @ {b.get('column_label') or b.get('column')} ({b.get('state')})" if b.get("row") and (b.get("column_label") or b.get("column"))
             else "no qualifying study found") for b in c["blocking_cells"][:4])))
    L.append("")

    # --- Adjacency C ----------------------------------------------------------------------
    concepts = session.execute(select(StudyConcept).where(StudyConcept.asset_id == asset_a.id).order_by(StudyConcept.created_at.desc())).scalars().all()[:3]
    if concepts:
        L += ["## 5. Study Concepts Generated", ""]
        for c in concepts:
            L += [c.markdown.replace("# Study Concept Synopsis", "### Study Concept"), ""]

    # --- method + references -----------------------------------------------------------------
    L += ["## Methods", "", "**Evidence tiers:** " + "; ".join(f"Tier {k} — {v}" for k, v in TIER_RULES.items()),
          "", "**Validity levels:** " + "; ".join(f"L{k} — {v}" for k, v in sorted(VALIDITY_RULES.items(), reverse=True)), "",
          "Every quantitative claim is drawn from a registry field, USPI section or citation-validated verbatim quote stored in mednik.", "",
          "## References", ""]
    for a in [asset_a] + comparators:
        if a.spl_set_id:
            L.append(f"- {asset_label(a)} USPI (effective {a.label_effective_date}): {_cite(a.spl_set_id)}")
    for r in sorted(x for x in refs if x and x not in {a.spl_set_id for a in [asset_a] + comparators}):
        L.append(f"- {_cite(r)}")
    return "\n".join(L)


CSS = """
body { font-family: sans-serif; font-size: 9pt; color: #111; }
h1 { font-size: 16pt; } h2 { font-size: 13pt; margin-top: 14pt; } h3 { font-size: 11pt; } h4 { font-size: 10pt; }
table { border-collapse: collapse; width: 100%; } td, th { border: 0.5pt solid #999; padding: 2pt; font-size: 7.5pt; vertical-align: top; }
blockquote { color: #444; border-left: 2pt solid #999; padding-left: 6pt; }
a { color: #1d4ed8; }
"""


def markdown_to_pdf(md_text: str) -> bytes:
    html = md_lib.markdown(md_text, extensions=["tables"])
    import pymupdf  # imported on use (large resident footprint)

    archive = pymupdf.Archive()
    images: list[tuple[bytes, str]] = []

    def _img(m: re.Match[str]) -> str:
        name = f"img{len(images)}.svg"
        images.append((base64.b64decode(m.group(1)), name))
        return f'src="{name}" width="520"'

    html = re.sub(r'src="data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)"', _img, html)
    for data, name in images:
        archive.add(data, name)
    story = pymupdf.Story(html=f"<html><body>{html}</body></html>", user_css=CSS, archive=archive)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    mediabox = pymupdf.paper_rect("letter")
    where = mediabox + (36, 36, -36, -36)
    more = True
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return buf.getvalue()
