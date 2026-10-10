"""Full text acquisition and parsing. Only legal open sources: the user's
own PDFs in sources/, PMC open-access JATS, arXiv, NASA NTRS, publisher OA
links reported by OpenAlex/Europe PMC, and official legal texts. Paywalled
papers stay abstract-only until the user drops the PDF into sources/."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from . import http
from .model import Work, norm_doi
from .store import Store
from .textutil import normalize

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.I)


# ---------------------------------------------------------------------------
# parsers
# ---------------------------------------------------------------------------

def parse_pdf(path: Path) -> tuple[list[dict], dict]:
    import fitz  # PyMuPDF
    pages, meta = [], {}
    with fitz.open(path) as doc:
        meta = {"pdf_pages": doc.page_count, "pdf_title": (doc.metadata or {}).get("title", "")}
        for i, page in enumerate(doc, 1):
            text = page.get_text("text")
            label = ""
            try:
                label = page.get_label() or ""
            except Exception:  # noqa: BLE001
                pass
            if text.strip():
                pages.append({"loc": f"p. {i}", "label": label, "text": text})
    pages = _strip_running_lines(pages)
    pages, refs = _cut_references(pages)
    for p in pages:
        p["text"] = normalize(p["text"])
    if refs:
        meta["references_chars"] = len(refs)
    full = " ".join(p["text"] for p in pages)
    meta.update(find_statements(full))
    return pages, meta


def _strip_running_lines(pages: list[dict]) -> list[dict]:
    """Remove running headers/footers/watermarks: short lines that repeat
    (digits ignored) on at least half of the pages."""
    from collections import Counter
    n = len(pages)
    if n < 3:
        return pages
    sig = lambda l: re.sub(r"\d+", "#", " ".join(l.split()).lower())  # noqa: E731
    cnt = Counter()
    for p in pages:
        cnt.update({sig(l) for l in p["text"].splitlines() if 3 < len(l.strip()) < 220})
    common = {k for k, c in cnt.items() if c >= max(3, n // 2)}
    if not common:
        return pages
    out = []
    for p in pages:
        lines = [l for l in p["text"].splitlines() if sig(l) not in common]
        out.append({**p, "text": "\n".join(lines)})
    return out


_REFS_HEAD = re.compile(r"^\s*(references|reference list|bibliography|literature cited|works cited|bibliografia|"
                        r"piśmiennictwo|literatura)\s*:?\s*$", re.I | re.M)


def _cut_references(pages: list[dict]) -> tuple[list[dict], str]:
    """Drop the reference list (it would 'confirm' any number or phrase).
    Only a heading in the second half of the document counts."""
    n = len(pages)
    for i in range(n // 2, n):
        ms = list(_REFS_HEAD.finditer(pages[i]["text"]))
        if ms:
            m = ms[0]
            cut = pages[i]["text"][:m.start()]
            refs = pages[i]["text"][m.start():] + "\n".join(p["text"] for p in pages[i + 1:])
            # keep appendix/supplement pages that follow the references
            tail = [p for p in pages[i + 1:] if re.search(
                r"^\s*(appendix\b|supplementary (material|appendix|tables?|figures?)\b|table s\d+\b|figure s\d+\b)",
                p["text"][:300], re.I | re.M)]
            kept = pages[:i] + ([{**pages[i], "text": cut}] if cut.strip() else []) + tail
            return kept, refs
    return pages, ""


def _t(e) -> str:
    return " ".join("".join(e.itertext()).split()) if e is not None else ""


def parse_jats(xml: str) -> tuple[list[dict], dict]:
    root = ET.fromstring(xml.encode("utf-8"))
    pages: list[dict] = []
    meta: dict = {}
    abstract = root.find(".//front//abstract")
    if abstract is not None:
        pages.append({"loc": "abstract", "text": _t(abstract)})
    body = root.find("body")

    def walk(sec, prefix=""):
        title = _t(sec.find("title"))
        name = f"{prefix} › {title}" if prefix and title else (title or prefix or "body")
        paras = [_t(p) for p in sec if p.tag in ("p", "list", "disp-quote", "boxed-text")]
        if paras:
            pages.append({"loc": f"sec. {name}", "text": " ".join(paras)})
        for sub in sec.findall("sec"):
            walk(sub, title if not prefix else name)

    if body is not None:
        loose = [_t(p) for p in body if p.tag == "p"]
        if loose:
            pages.append({"loc": "sec. body", "text": " ".join(loose)})
        for sec in body.findall("sec"):
            walk(sec)
    for tw in root.iter("table-wrap"):
        label = _t(tw.find("label")) or "Table"
        cap = _t(tw.find("caption"))
        rows = []
        for tr in tw.iter("tr"):
            rows.append(" | ".join(_t(c) for c in tr if c.tag in ("td", "th")))
        foot = _t(tw.find("table-wrap-foot"))
        pages.append({"loc": f"{label.lower().rstrip('.:')}", "text": f"{label}. {cap}\n" + "\n".join(rows) + (f"\n{foot}" if foot else "")})
    for fig in root.iter("fig"):
        label = _t(fig.find("label")) or "Figure"
        cap = _t(fig.find("caption"))
        if cap:
            pages.append({"loc": f"{label.lower().rstrip('.:')}", "text": f"{label}. {cap}"})
    fund = [_t(f) for f in root.iter("funding-group")] + [_t(f) for f in root.iter("funding-statement")]
    coi = [_t(fn) for fn in root.iter("fn") if (fn.get("fn-type") or "").lower() in ("coi-statement", "conflict", "competing-interests")]
    for sec in root.iter("sec"):
        st = (sec.get("sec-type") or "").lower()
        title = _t(sec.find("title")).lower()
        if "coi" in st or "conflict" in title or "competing" in title:
            coi.append(_t(sec))
        if "funding" in st or title.startswith("funding"):
            fund.append(_t(sec))
    ack = _t(root.find(".//back/ack"))
    if fund:
        meta["funding"] = " ".join(dict.fromkeys(fund))[:2000]
    if coi:
        meta["coi"] = " ".join(dict.fromkeys(coi))[:2000]
    if ack:
        meta["acknowledgements"] = ack[:1500]
        pages.append({"loc": "sec. Acknowledgements", "text": ack})
    for p in pages:
        p["text"] = normalize(p["text"])
    return [p for p in pages if p["text"]], meta


_STATEMENT = {
    "funding": r"(?:^|\s)(?:funding|financial support|source of funding|finansowanie)\s*[:.]?\s+(.{20,700}?)(?=\s(?:conflicts? of interest|competing interests|declaration|author contributions|acknowledg|data availability|references)\b|$)",
    "coi": r"(?:^|\s)(?:conflicts? of interests?|competing interests?|declaration of interests?|disclosures?|konflikt interesów)\s*[:.]?\s+(.{10,700}?)(?=\s(?:funding|author contributions|acknowledg|data availability|references|ethics)\b|$)",
}


def find_statements(text: str) -> dict:
    out = {}
    low = text[-60000:]  # statements live near the end
    for k, pat in _STATEMENT.items():
        m = re.search(pat, low, flags=re.I | re.S)
        if m:
            out[k] = " ".join(m.group(1).split())[:700]
    return out


# ---------------------------------------------------------------------------
# acquisition
# ---------------------------------------------------------------------------

def _user_file(st: Store, key: str, w: Work) -> Path | None:
    src = st.root / "sources"
    if not src.is_dir():
        return None
    for ext in (".pdf", ".txt", ".md", ".xml"):
        p = src / f"{key}{ext}"
        if p.exists():
            return p
    doi = norm_doi(w.doi)
    if doi:
        safe = re.sub(r"[^a-z0-9]+", "_", doi)
        for p in src.glob("*"):
            if safe in re.sub(r"[^a-z0-9]+", "_", p.name.lower()):
                return p
    return None


def attach_file(st: Store, key: str, path: Path) -> tuple[str, int]:
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        pages, meta = parse_pdf(path)
        kind = "pdf"
    elif path.suffix.lower() == ".xml":
        pages, meta = parse_jats(path.read_text(encoding="utf-8", errors="replace"))
        kind = "jats"
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        pages = [{"loc": f"part {i + 1}", "text": normalize(t)} for i, t in enumerate(re.split(r"\n\s*\n\s*\n", text)) if t.strip()]
        meta, kind = find_statements(text), "text"
    _apply_meta(st, key, meta)
    st.save_fulltext(key, kind, str(path), pages, meta)
    return kind, len(pages)


def _apply_meta(st: Store, key: str, meta: dict):
    w = st.get(key)
    changed = False
    if meta.get("funding") and len(meta["funding"]) > len(w.funding or ""):
        w.funding, changed = meta["funding"], True
    if meta.get("coi") and len(meta["coi"]) > len(w.coi or ""):
        w.coi, changed = meta["coi"], True
    if changed:
        st.save(key, w)


def fetch_one(st: Store, key: str, prefer_pdf_pages: bool = True) -> tuple[str, str]:
    """Returns (status, detail). status: ok | skipped | failed."""
    w = st.get(key)
    if not w:
        return "failed", "unknown key"
    files = st.dir / "files"
    up = _user_file(st, key, w)
    if up:
        kind, n = attach_file(st, key, up)
        return "ok", f"user file {up.name} ({kind}, {n} parts)"
    tried = []
    # 1) PMC open access JATS (structure, tables, COI/funding), then the PDF for page numbers
    if w.pmcid:
        from .connectors.europepmc import fulltext_xml
        xml = fulltext_xml(w.pmcid)
        if xml:
            p = files / f"{key}.xml"
            p.write_text(xml, encoding="utf-8")
            pages, meta = parse_jats(xml)
            if len(" ".join(x["text"] for x in pages)) > 2000:
                if prefer_pdf_pages:
                    for url in _pdf_candidates(w):
                        got = http.download(url, files / f"{key}.pdf")
                        if not got:
                            continue
                        try:
                            ppages, pmeta = parse_pdf(got[1])
                            if len(ppages) >= 2 and _same_paper(w, ppages):
                                # JATS tables/figures are cleaner than PDF extraction - keep both
                                extra = [x for x in pages if x["loc"].startswith(("table", "figure", "fig"))]
                                meta.update({k: v for k, v in pmeta.items() if k not in meta})
                                _apply_meta(st, key, meta)
                                st.save_fulltext(key, "pdf+jats", str(got[1]), ppages + extra, meta)
                                return "ok", f"OA PDF ({len(ppages)} pages, page-anchored) + PMC JATS tables"
                        except Exception as e:  # noqa: BLE001
                            tried.append(f"pdf: {e}")
                _apply_meta(st, key, meta)
                st.save_fulltext(key, "jats", str(p), pages, meta)
                return "ok", f"PMC JATS ({len(pages)} sections)"
        tried.append("pmc: no OA xml")
    # 2) arXiv
    if w.arxiv:
        got = http.download(f"https://arxiv.org/pdf/{w.arxiv}", files / f"{key}.pdf")
        if got:
            pages, meta = parse_pdf(got[1])
            _apply_meta(st, key, meta)
            st.save_fulltext(key, "pdf", str(got[1]), pages, meta)
            return "ok", f"arXiv PDF ({len(pages)} pages)"
        tried.append("arxiv: download failed")
    # 2b) Polish court judgment (SAOS) - full reasons as text
    if w.other_ids.get("saos"):
        try:
            d = http.get_json(f"https://www.saos.org.pl/api/judgments/{w.other_ids['saos']}", ttl=90 * 86400, timeout=30)
            from .connectors import strip_tags
            txt = strip_tags((d.get("data") or {}).get("textContent", ""))
            if len(txt) > 500:
                p = files / f"{key}.txt"
                p.write_text(txt, encoding="utf-8")
                parts = [{"loc": f"part {i + 1}", "text": normalize(t)} for i, t in
                         enumerate(re.split(r"(?<=\.)\s+(?=(?:UZASADNIENIE|Uzasadnienie|Sąd (?:Najwyższy|Apelacyjny|Okręgowy|Rejonowy) zważył))", txt)) if t.strip()]
                st.save_fulltext(key, "text", str(p), parts, {})
                return "ok", f"SAOS judgment text ({len(txt)} chars)"
        except Exception as e:  # noqa: BLE001
            tried.append(f"saos: {e}")
    # 3) NASA NTRS plain text
    if w.extra.get("fulltext_txt"):
        try:
            txt = http.get_text(w.extra["fulltext_txt"], ttl=90 * 86400)
            if len(txt) > 2000:
                p = files / f"{key}.txt"
                p.write_text(txt, encoding="utf-8")
                return ("ok", "NTRS text " + str(attach_file(st, key, p)))
        except Exception as e:  # noqa: BLE001
            tried.append(f"ntrs: {e}")
    # 4) any OA PDF link we know about
    for url in dict.fromkeys(u for u in (w.pdf_url, w.extra.get("crossref_pdf"), w.oa_url) if u):
        if not url.startswith("http"):
            continue
        got = http.download(url, files / f"{key}.pdf")
        if got:
            try:
                pages, meta = parse_pdf(got[1])
            except Exception as e:  # noqa: BLE001
                tried.append(f"{url[:60]}: {e}")
                continue
            if sum(len(p["text"]) for p in pages) > 1500 and _same_paper(w, pages):
                _apply_meta(st, key, meta)
                st.save_fulltext(key, "pdf", str(got[1]), pages, meta)
                return "ok", f"OA PDF ({len(pages)} pages) from {url[:60]}"
        tried.append(f"{url[:60]}: not a usable PDF")
    return "skipped", "; ".join(tried) or "no open-access full text known - put the PDF in sources/<key>.pdf"


def _pdf_candidates(w: Work) -> list[str]:
    urls = [w.pdf_url, *[u for u in w.extra.get("alt_urls", []) if "pdf" in u.lower() or "printable" in u.lower()],
            w.extra.get("crossref_pdf")]
    if w.pmcid:
        urls.append(f"https://europepmc.org/articles/{w.pmcid}?pdf=render")
    return [u for u in dict.fromkeys(urls) if u and u.startswith("http")]


def _same_paper(w: Work, pages: list[dict]) -> bool:
    """Guard against landing on a different document (cover sheets, wrong DOI)."""
    from .textutil import overlap
    head = " ".join(p["text"] for p in pages[:2])[:6000]
    return overlap(w.title, head) >= 0.6 or (w.doi and w.doi.lower() in head.lower())


def doi_in_pdf(path: Path) -> str | None:
    try:
        pages, _ = parse_pdf(path)
    except Exception:  # noqa: BLE001
        return None
    for p in pages[:2]:
        m = DOI_RE.search(p["text"])
        if m:
            return norm_doi(m.group(1).rstrip(".,;)"))
    return None
