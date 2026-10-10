"""Citation syntax used in drafts (pandoc-compatible, plus anchor quotes):

    [@key]                      plain citation
    [@key, p. 4]                with locator (PDF page / sec. Results / table 2)
    [@key, p. 4 :: "quote"]     evidence-anchored: verbatim words from the source
    [@a; @b, p. 3 :: "q"]       several sources

Anchor quotes are what make verification language-independent: the quote
is checked against the source, then removed by `factual-research render`."""
from __future__ import annotations

import re
from dataclasses import dataclass

_Q_OPEN, _Q_CLOSE, _Q_ANY = '"“„', '"”“', '"“”„'
# An anchor quote may contain balanced brackets ("odds ratio [OR] 1.56") but
# no blank line. If the quote is not well formed the block falls back to the
# plain form, which stops at the first bracket. The atomic group keeps
# matching linear. verify, render and the HTML report all use this pattern.
_QUOTED = rf"[{_Q_OPEN}](?:[^{_Q_ANY}\[\]\n]|\[[^{_Q_ANY}\[\]\n]*\]|\n(?![ \t]*\n))*[{_Q_CLOSE}]"
CITE_BLOCK = re.compile(rf"\[(-?@(?>{_QUOTED}|[^\[\]])+?)\]")
# `<!-- fr:table extraction -->`; older projects may use `cg:table`
TABLE_TAG = r"<!--\s*(?:fr|cg):table\s+(.+?)\s*-->"
ITEM = re.compile(r"-?@(\w(?:[\w:./-]*\w)?)\s*(?:,\s*([^;]*?))?\s*$", re.S)


@dataclass
class Cite:
    key: str
    locator: str = ""
    quote: str = ""
    start: int = 0
    end: int = 0
    raw: str = ""


def _split_items(inner: str) -> list[str]:
    out, buf, inq = [], [], False
    for ch in inner:
        if not inq and ch in _Q_OPEN:
            inq = True
        elif inq and ch in _Q_CLOSE:
            inq = False
        if ch == ";" and not inq:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return [x.strip() for x in out if x.strip()]


def cite_items(inner: str) -> list[tuple[str, str, str]]:
    """(key, locator, quote) for each item of a citation block (CITE_BLOCK group 1)."""
    out = []
    for item in _split_items(inner):
        quote = ""
        if "::" in item:
            item, q = item.split("::", 1)
            quote = q.strip().strip(_Q_ANY).strip()
        im = ITEM.match(item.strip())
        if im:
            out.append((im.group(1), (im.group(2) or "").strip(), quote))
    return out


def parse_cites(text: str) -> list[Cite]:
    return [Cite(key=k, locator=loc, quote=q, start=m.start(), end=m.end(), raw=m.group(0))
            for m in CITE_BLOCK.finditer(text) for k, loc, q in cite_items(m.group(1))]


def strip_cites(text: str) -> str:
    return re.sub(r"\s*" + CITE_BLOCK.pattern, "", text)


def norm_loc(loc: str) -> tuple[str, str]:
    """('page', '4') / ('sec', 'results') / ('table', '2') / ('', '')."""
    l = loc.strip().lower()
    m = re.match(r"(?:pp?\.?|page|s\.|str\.)\s*(\d+)", l)
    if m:
        return "page", m.group(1)
    m = re.match(r"(?:sec\.?|section|§)\s*(.+)", l)
    if m:
        return "sec", m.group(1).strip()
    m = re.match(r"(table|tab\.?|tabela|fig\.?|figure|rys\.?)\s*(\w+)", l)
    if m:
        kind = "table" if m.group(1).startswith(("tab",)) else "figure"
        return kind, m.group(2)
    return ("", l) if l else ("", "")


def loc_matches(want: str, have: str, label: str = "") -> bool:
    wk, wv = norm_loc(want)
    hk, hv = norm_loc(have)
    if not wk and not wv:
        return True
    if wk == "page":
        return (hk == "page" and hv == wv) or (label and label.strip() == wv)
    if wk == "sec":
        return hk == "sec" and wv in hv
    if wk in ("table", "figure"):
        return hk in ("table", "figure") and hv == wv
    return wv in have.lower()


def _kind_of_page(loc: str) -> str:
    k, _ = norm_loc(loc)
    if loc.startswith("abstract"):
        return "abstract"
    return k or "other"


def quote_location(quote: str, loc: str, pages: list[dict], abstract: str = "") -> tuple[bool | None, str]:
    """Is the quote at the stated location? Returns (ok, where_found).
    ok=None when the location type cannot be checked for this text (e.g. a
    section given but the text is split into PDF pages)."""
    from .textutil import find_quote
    where = next((p["loc"] for p in pages if find_quote(quote, p["text"])[0] >= 0.9), "")
    if not loc:
        return True, where
    if loc.strip().lower().startswith("abstract"):
        if abstract and find_quote(quote, abstract)[0] >= 0.9:
            return True, "abstract"
        return (False if abstract else None), where
    want = _kind_of_page(loc)
    have = {_kind_of_page(p["loc"]) for p in pages}
    if want not in have:
        return None, where
    for p in pages:
        if loc_matches(loc, p["loc"], p.get("label", "")) and find_quote(quote, p["text"])[0] >= 0.9:
            return True, p["loc"]
    return False, where
