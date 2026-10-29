"""Executive export — Markdown / PDF Medical Strategy Briefing Dossier with full citations."""

from __future__ import annotations

import io
from datetime import datetime, timezone

import markdown as md_lib
import pymupdf
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Asset, Indication, MarketValueDriver, StudyConcept
from .evidence import source_url
from .guidelines import gap_to_guideline, guideline_status
from .heatmap import build_heatmap
from .matrix import QUADRANTS, asset_label, build_matrix, node_detail
from .mvd import effective_scores, serialize_driver
from .tiering import TIER_RULES, VALIDITY_RULES


def _cite(ref: str | None) -> str:
    if not ref:
        return ""
    url = source_url("ctgov" if ref.startswith("NCT") else "pubmed" if ref.isdigit() else "uspi", ref)
    return f"[{ref}]({url})" if url else ref


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

    # --- Module 2 ---------------------------------------------------------------------
    L += ["## 2. Competitive Differentiation & Impact Matrix", ""]
    for q in ("TR", "TL", "BR", "BL"):
        nodes = [n for n in mx["nodes"] if n["quadrant"] == q]
        if not nodes:
            continue
        L += [f"### {QUADRANTS[q]}", "", "| Driver | vs | X | Y | Tier | Basis |", "|---|---|---|---|---|---|"]
        for n in sorted(nodes, key=lambda n: -(n["y"])):
            x = "n/a" if n["x"] is None else f"{n['x']:+.0f}"
            L.append(f"| {n['mvd_name']} | {n['comparator_label']} | {x} | {n['y']:.0f} | {n['tier'] or '—'} | {n['explanation']} |")
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
            f"{b.get('row') or ''} @ {b.get('column_label') or b.get('column') or ''} ({b.get('state')})" for b in c["blocking_cells"][:4])))
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
    story = pymupdf.Story(html=f"<html><body>{html}</body></html>", user_css=CSS)
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
