"""Text helpers shared by the deterministic parsers and the citation validator."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from lxml import etree, html

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−﹣－"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"', ord("´"): "'"}
_SPACES = dict.fromkeys(map(ord, "       "), " ")


def normalize_text(s: str | None) -> str:
    """Canonical form used for verbatim-quote validation (case, dash, quote, whitespace insensitive)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_DASHES).translate(_QUOTES).translate(_SPACES)
    s = s.replace("­", "").replace("​", "")
    s = s.replace("≥", ">=").replace("≤", "<=")
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


def clean_ws(s: str | None) -> str:
    if not s:
        return ""
    s = s.translate(_SPACES)
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def sha256_text(s: str | bytes) -> str:
    if isinstance(s, str):
        s = s.encode("utf-8", "ignore")
    return hashlib.sha256(s).hexdigest()


_ABBREV = r"(?:e\.g|i\.e|vs|approx|Fig|Figs|No|Nos|et al|Dr|Inc|Ltd|Co|St|ca|cf|incl|resp|min|max|wk|wks|mo|hr|hrs|Ref|Refs|U\.S|i\.v|p\.o|b\.i\.d|t\.i\.d|q\.d)"


def split_sentences(text: str | None) -> list[str]:
    """Split label / abstract prose into sentences or clause-like bullets."""
    if not text:
        return []
    t = text.replace("•", "\n").replace("●", "\n").replace("•", "\n")
    # protect abbreviations and decimals
    t = re.sub(rf"\b({_ABBREV})\.", lambda m: m.group(1).replace(".", "<DOT>") + "<DOT>", t)
    t = re.sub(r"(\d)\.(\d)", r"\1<DOT>\2", t)
    parts: list[str] = []
    for line in re.split(r"\n+", t):
        for p in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(\[\"'])", line):
            p = p.replace("<DOT>", ".").strip()
            if len(p) > 2:
                parts.append(p)
    return parts


def split_clauses(sentence: str) -> list[str]:
    """Further split on semicolons (labels pack several rules into one sentence)."""
    return [c.strip() for c in re.split(r";\s+", sentence) if c.strip()]


_NUM = r"[-−–]?\s?\d+(?:[.,]\d+)?"


def parse_number(s: Any) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().translate(_DASHES).replace(",", "")
    m = re.search(r"-?\s?\d+(?:\.\d+)?", t)
    if not m:
        return None
    try:
        return float(m.group(0).replace(" ", ""))
    except ValueError:
        return None


def parse_p_value(s: Any) -> tuple[float | None, str | None]:
    """'<0.001' → (0.001, '<0.001'); '0.0123' → (0.0123, '0.0123'); 'NS' → (None, 'NS')."""
    if s is None:
        return None, None
    if isinstance(s, (int, float)):
        return float(s), str(s)
    t = str(s).strip()
    if not t:
        return None, None
    m = re.search(r"([<>≤≥=]?)\s*(0?\.\d+|\d(?:\.\d+)?(?:[eE]-?\d+)?)", t.replace(" ", ""))
    if not m:
        return None, t
    try:
        v = float(m.group(2))
    except ValueError:
        return None, t
    if v > 1:
        return None, t
    return v, t


def parse_weeks(s: str | None) -> float | None:
    """'Baseline to Week 6' → 6; 'Day 43' → 6.1; '52 weeks' → 52; '6 months' → 26."""
    if not s:
        return None
    t = s.lower()
    vals: list[float] = []
    for m in re.finditer(r"(?:week|wk)s?\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*[-\s]?(?:weeks?|wks?)\b", t):
        vals.append(float(m.group(1) or m.group(2)))
    for m in re.finditer(r"day\s*(\d+)|(\d+)\s*[-\s]?days?\b", t):
        vals.append(float(m.group(1) or m.group(2)) / 7.0)
    for m in re.finditer(r"month\s*(\d+)|(\d+)\s*[-\s]?months?\b", t):
        vals.append(float(m.group(1) or m.group(2)) * 4.345)
    for m in re.finditer(r"(\d+)\s*[-\s]?years?\b", t):
        vals.append(float(m.group(1)) * 52.0)
    return round(max(vals), 1) if vals else None


def parse_age_years(s: str | None) -> float | None:
    if not s:
        return None
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(year|month|week|day)", s.lower())
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2)
    return {"year": v, "month": v / 12, "week": v / 52, "day": v / 365}[unit]


# ---------------------------------------------------------------------------
# HTML / XML table → grid
# ---------------------------------------------------------------------------


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _cell_text(el) -> str:
    """Cell text without footnote markers (<sup>, <footnoteRef>) and with XML layout whitespace collapsed."""
    out: list[str] = []

    def walk(node):
        name = _local(node.tag)
        if name in ("sup", "footnoteRef", "footnote"):
            if node.tail:
                out.append(node.tail)
            return
        if node.text and name:
            out.append(node.text)
        for ch in node:
            walk(ch)
        if name in ("paragraph", "br", "p", "item"):
            out.append(" ")
        if node.tail and node is not el:
            out.append(node.tail)

    walk(el)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def grid_from_table_element(table, ns: str | None = None) -> dict[str, Any]:
    """Expand a <table> (HTML or HL7 SPL) into a rectangular grid honoring colspan/rowspan.

    Returns {caption, header_rows: [[...]], rows: [[...]]}.
    """

    def tag(name: str) -> str:
        return f"{{{ns}}}{name}" if ns else name

    caption_el = table.find(tag("caption"))
    caption = _cell_text(caption_el) if caption_el is not None else ""
    head_trs, body_trs = [], []
    thead = table.find(tag("thead"))
    if thead is not None:
        head_trs = thead.findall(tag("tr"))
    for tb in table.findall(tag("tbody")) or [table]:
        body_trs += tb.findall(tag("tr"))
    if not head_trs and not body_trs:
        body_trs = table.findall(f".//{tag('tr')}")

    def expand(trs) -> list[list[str]]:
        grid: list[list[str]] = []
        spans: dict[tuple[int, int], str] = {}
        for r_i, tr in enumerate(trs):
            row: list[str] = []
            c_i = 0
            cells = [c for c in tr if isinstance(c.tag, str) and c.tag in (tag("td"), tag("th"))]
            for cell in cells:
                while (r_i, c_i) in spans:
                    row.append(spans.pop((r_i, c_i)))
                    c_i += 1
                txt = _cell_text(cell)
                try:
                    cs = max(1, int(cell.get("colspan", "1") or 1))
                except ValueError:
                    cs = 1
                try:
                    rs = max(1, int(cell.get("rowspan", "1") or 1))
                except ValueError:
                    rs = 1
                for k in range(cs):
                    row.append(txt)
                    for dr in range(1, rs):
                        spans[(r_i + dr, c_i + k)] = txt
                c_i += cs
            while (r_i, c_i) in spans:
                row.append(spans.pop((r_i, c_i)))
                c_i += 1
            grid.append(row)
        width = max((len(r) for r in grid), default=0)
        return [r + [""] * (width - len(r)) for r in grid]

    tfoot = table.find(tag("tfoot"))
    footnotes = _cell_text(tfoot) if tfoot is not None else ""
    return {"caption": caption, "header_rows": expand(head_trs), "rows": expand(body_trs), "footnotes": footnotes}


def tables_from_html(fragment: str) -> list[dict[str, Any]]:
    try:
        doc = html.fromstring(f"<div>{fragment}</div>")
    except (etree.ParserError, ValueError):
        return []
    return [grid_from_table_element(t) for t in doc.iter("table")]


def strip_html(fragment: str | None) -> str:
    if not fragment:
        return ""
    try:
        return clean_ws(html.fromstring(f"<div>{fragment}</div>").text_content())
    except (etree.ParserError, ValueError):
        return clean_ws(re.sub(r"<[^>]+>", " ", fragment))


def table_to_text_rows(tbl: dict[str, Any]) -> list[str]:
    """Flatten a grid into 'header: value | header: value' strings (one per body row)."""
    headers = merge_header_rows(tbl.get("header_rows") or [])
    out = []
    for row in tbl.get("rows") or []:
        cells = []
        for i, v in enumerate(row):
            if not v:
                continue
            h = headers[i] if i < len(headers) else ""
            cells.append(f"{h}: {v}" if h and h != v else v)
        if cells:
            out.append(" | ".join(dict.fromkeys(cells)))
    return out


def merge_header_rows(header_rows: list[list[str]]) -> list[str]:
    if not header_rows:
        return []
    width = max(len(r) for r in header_rows)
    merged = []
    for c in range(width):
        parts = []
        for r in header_rows:
            v = r[c] if c < len(r) else ""
            if v and v not in parts:
                parts.append(v)
        merged.append(" ".join(parts))
    return merged
