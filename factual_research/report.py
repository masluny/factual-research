"""Single-file HTML research report: PRISMA flow, summary of findings,
evidence matrix, evidence table + forest plot, the verified draft with
quote-on-hover citations, the source list with flags, and the search log."""
from __future__ import annotations

import datetime as dt
import html as H
import json
import re
from pathlib import Path

from . import __version__
from . import prisma
from . import tables as T
from .citations import CITE_BLOCK, TABLE_TAG, cite_items
from .evidence import flags, summarize_claim, weight
from .export import intext_author, reference
from .model import DESIGNS
from .project import Plan
from .store import INCLUDED, Store

CSS = """
:root{--bg:#f7f7f5;--panel:#ffffff;--ink:#1d1f23;--muted:#666b73;--line:#e3e3df;--acc:#2f6fdf;--ok:#1f8a4c;
--warn:#b86e00;--bad:#c62f2f;--chip:#eef2fb;--mark:#fff3c4;--prisma:#f2f5fb;--x:#fbf0ee;--inc:#eaf6ee}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#16181c;--panel:#1e2126;--ink:#e7e8ea;--muted:#9aa0a8;
--line:#30343b;--acc:#7aa7ff;--ok:#4cc27e;--warn:#f0a83a;--bad:#ff6b6b;--chip:#26304a;--mark:#4a4320;--prisma:#222a38;--x:#3a2624;--inc:#1f3427}}
:root[data-theme=dark]{--bg:#16181c;--panel:#1e2126;--ink:#e7e8ea;--muted:#9aa0a8;--line:#30343b;--acc:#7aa7ff;--ok:#4cc27e;
--warn:#f0a83a;--bad:#ff6b6b;--chip:#26304a;--mark:#4a4320;--prisma:#222a38;--x:#3a2624;--inc:#1f3427}
*{box-sizing:border-box}a{color:var(--acc)}td.study a{text-decoration:none;font-weight:600}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif}
header{padding:28px 24px 8px;max-width:1180px;margin:auto}h1{font-size:24px;margin:0 0 6px;line-height:1.3}
.sub{color:var(--muted);font-size:13px}main{max-width:1180px;margin:auto;padding:0 24px 60px}
nav{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);z-index:5}
nav div{max-width:1180px;margin:auto;padding:8px 24px;display:flex;gap:14px;flex-wrap:wrap;font-size:13px}
nav a{color:var(--muted);text-decoration:none}nav a:hover{color:var(--acc)}
section{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:18px 0}
h2{font-size:17px;margin:0 0 12px}h3{font-size:15px;margin:16px 0 8px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:14px 0}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.tile b{display:block;font-size:22px}.tile span{color:var(--muted);font-size:12px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{font-weight:600;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.02em}
td.bad{background:color-mix(in srgb,var(--bad) 12%,transparent)}td .q{color:var(--acc);margin-left:4px;font-size:11px}
td[title]:not([title=""]){cursor:help}.warn{color:var(--warn)}.note{color:var(--muted);font-size:12px;margin:6px 0 0}
figure{margin:0}figcaption{font-weight:600;margin-bottom:8px}
.badge{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11px;font-weight:600;border:1px solid var(--line)}
.c-high{background:color-mix(in srgb,var(--ok) 20%,transparent)}.c-moderate{background:color-mix(in srgb,var(--acc) 18%,transparent)}
.c-low{background:color-mix(in srgb,var(--warn) 20%,transparent)}.c-verylow{background:color-mix(in srgb,var(--bad) 18%,transparent)}
.bar{height:8px;border-radius:5px;background:color-mix(in srgb,var(--bad) 45%,transparent);overflow:hidden;min-width:120px}
.bar i{display:block;height:100%;background:var(--ok)}
.claim{border-top:1px solid var(--line);padding:12px 0}.claim:first-of-type{border-top:0}
.claim .t{font-weight:600}.claim .meta{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:6px 0;font-size:13px;color:var(--muted)}
.ev{font-size:13px;margin:4px 0 4px 10px;padding-left:10px;border-left:3px solid var(--line)}
.ev.for{border-color:var(--ok)}.ev.against{border-color:var(--bad)}.ev.mixed{border-color:var(--warn)}
.ev q{font-style:italic}.cite{background:var(--chip);border-radius:5px;padding:0 5px;font-size:12.5px;white-space:nowrap;
text-decoration:none;color:var(--ink);cursor:help}
.draft{max-width:760px}.draft p,.draft li{margin:8px 0}.issue-line{background:var(--mark);border-radius:4px}
.iss{font-size:13px;margin:6px 0;padding:8px 10px;border-radius:8px;border:1px solid var(--line)}
.iss.error{border-left:4px solid var(--bad)}.iss.warn{border-left:4px solid var(--warn)}.iss.note{border-left:4px solid var(--muted)}
.iss .fx{color:var(--muted);margin-top:3px}.score{font-size:30px;font-weight:700}
.flag{display:inline-block;font-size:11px;padding:0 6px;border-radius:5px;margin:1px 2px;background:color-mix(in srgb,var(--warn) 18%,transparent)}
.flag.r{background:color-mix(in srgb,var(--bad) 25%,transparent)}
svg.prisma{width:100%;max-width:860px}svg .b{fill:var(--prisma);stroke:var(--line)}svg .b2{fill:var(--panel);stroke:var(--line)}
svg .x{fill:var(--x);stroke:var(--line)}svg .inc{fill:var(--inc);stroke:var(--line)}svg .bt{font-weight:600;font-size:14px;fill:var(--ink)}
svg .bl{font-size:12.5px;fill:var(--muted)}svg .ar{stroke:var(--muted);stroke-width:1.5}svg .ah{fill:var(--muted)}svg .ph{font-size:12px;fill:var(--muted)}
svg.forest{width:100%;max-width:760px}.fp-title{font-weight:600;font-size:13px;fill:var(--ink)}.fp-lab,.fp-val{font-size:12px;fill:var(--ink)}
.fp-tick{font-size:11px;fill:var(--muted)}.fp-null{stroke:var(--muted);stroke-dasharray:4 3}.fp-ci{stroke:var(--acc);stroke-width:2}.fp-pt{fill:var(--acc)}
details summary{cursor:pointer;color:var(--muted);font-size:13px}code{font-size:12px}
.themebtn{float:right;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:8px;padding:4px 10px;cursor:pointer}
@media (max-width:640px){header,main{padding-left:16px;padding-right:16px}nav div{padding:8px 16px}section{padding:14px}}
"""

JS = """
document.querySelector('.themebtn').onclick=()=>{const r=document.documentElement;
const cur=r.dataset.theme||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
r.dataset.theme=cur==='dark'?'light':'dark';};
"""


def e(s) -> str:
    return H.escape("" if s is None else str(s))


PORTABLE = False   # True: no file:// links (for sharing the report)


def _file_link(st: Store, key: str, loc: str = "") -> str:
    if PORTABLE:
        return ""
    ft = st.fulltext(key)
    if not ft or not ft.get("path"):
        return ""
    p = Path(ft["path"])
    if not p.is_absolute():
        p = st.root / p
    m = re.match(r"p\.\s*(\d+)", loc or "")
    return p.resolve().as_uri() + (f"#page={m.group(1)}" if m and p.suffix == ".pdf" else "")


def md_inline(text: str, st: Store, quote_map: dict) -> str:
    """Markdown inline → HTML with citation chips (hover shows anchor quote)."""
    out, last = [], 0
    for m in CITE_BLOCK.finditer(text):
        out.append(_fmt(text[last:m.start()]))
        chips = []
        for key, loc, quote in cite_items(m.group(1)):
            k = st.resolve(key)
            w = st.get(k) if k else None
            label = f"{intext_author(w)}, {w.year}" if w else f"@{key}?"
            tip = (f'"{quote}"' if quote else (w.title if w else "UNKNOWN SOURCE")) + (f" - {loc}" if loc else "")
            href = _file_link(st, k, loc) if k else ""
            chips.append(f'<a class="cite" href="{e(href or "#src-" + (k or ""))}" title="{e(tip)}">{e(label)}'
                         f'{", " + e(loc) if loc else ""}</a>')
        out.append(" ".join(chips))
        last = m.end()
    out.append(_fmt(text[last:]))
    return "".join(out)


def _fmt(s: str) -> str:
    s = e(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def draft_html(st: Store, text: str, issue_lines: dict[int, list], plan: Plan | None = None, cite_fmt=None) -> str:
    blocks: dict[str, str] = {}

    def ph(m):
        tok = f"@@FRTABLE{len(blocks)}@@"
        try:
            t, pr, svg = T.placeholder_table(st, plan, m.group(1).split()) if plan else (None, [], "")
            blocks[tok] = svg or (T.table_html(t, cite_fmt) if t else "") + "".join(
                f'<p class="warn">{e(x)}</p>' for x in pr)
        except Exception as ex:  # noqa: BLE001
            blocks[tok] = f'<p class="warn">{e(m.group(0))}: {e(ex)}</p>'
        return tok + "\n" * m.group(0).count("\n")

    text = re.sub(TABLE_TAG, ph, text)
    text = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    lines = text.split("\n")
    out, i = [], 0
    in_list = False
    while i < len(lines):
        s = lines[i].rstrip()
        ln = i + 1
        cls = ' class="issue-line"' if ln in issue_lines else ""
        tip = f' title="{e("; ".join(x["message"] for x in issue_lines.get(ln, [])))}"' if ln in issue_lines else ""
        if not s.strip():
            if in_list:
                out.append("</ul>")
                in_list = False
            i += 1
            continue
        if s.strip() in blocks:
            out.append(blocks[s.strip()])
            i += 1
            continue
        if s.startswith("#"):
            lvl = min(len(s) - len(s.lstrip("#")) + 1, 5)
            out.append(f"<h{lvl}>{md_inline(s.lstrip('#').strip(), st, {})}</h{lvl}>")
        elif s.strip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    rows.append((i + 1, cells))
                i += 1
            if rows:
                out.append('<div class="scroll"><table>')
                for j, (rl, cells) in enumerate(rows):
                    tag = "th" if j == 0 else "td"
                    rc = ' class="issue-line"' if rl in issue_lines else ""
                    out.append(f"<tr{rc}>" + "".join(f"<{tag}>{md_inline(c, st, {})}</{tag}>" for c in cells) + "</tr>")
                out.append("</table></div>")
            continue
        elif re.match(r"^\s*([-*+]|\d+[.)])\s+", s):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li{cls}{tip}>{md_inline(re.sub(r'^\s*([-*+]|\d+[.)])\s+', '', s), st, {})}</li>")
        else:
            buf, first = [s.strip()], ln
            mark = ln in issue_lines
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(r"^\s*(#|\||[-*+]\s|\d+[.)]\s)", lines[i + 1]):
                i += 1
                buf.append(lines[i].strip())
                mark = mark or (i + 1) in issue_lines
            ttl = "; ".join(x["message"] for l in range(first, i + 2) for x in issue_lines.get(l, []))
            out.append(f'<p{" class=issue-line" if mark else ""}{" title=" + chr(34) + e(ttl) + chr(34) if ttl else ""}>'
                       f"{md_inline(' '.join(buf), st, {})}</p>")
        i += 1
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def build(st: Store, plan: Plan, draft: Path | None = None, verify_report: dict | None = None) -> str:
    combined = plan.combined()
    works = {k: w for k, _, w in st.works()}
    included = st.works(status=INCLUDED)
    pc = prisma.counts(st)
    claims = st.claims()
    style = combined.citation_style()
    parts = [f"<!doctype html><html lang=\"{e(plan.lang)}\"><head><meta charset=\"utf-8\">"
             f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Research report</title>"
             f"<style>{CSS}</style></head><body>",
             f"<header><button class=\"themebtn\">◐ theme</button><h1>{e(plan.question)}</h1>"
             f"<div class=\"sub\">factual-research {__version__} · packs: {e(', '.join(combined.names))} · since {e(plan.since)} · "
             f"generated {dt.datetime.now():%Y-%m-%d %H:%M}</div></header>"]
    nav = [("overview", "Overview"), ("prisma", "PRISMA"), ("findings", "Findings"), ("tables", "Tables")]
    if draft:
        nav.append(("draft", "Draft & verification"))
    nav += [("sources", "Sources"), ("log", "Search log")]
    parts.append("<nav><div>" + "".join(f'<a href="#{a}">{b}</a>' for a, b in nav) + "</div></nav><main>")
    # ---- overview tiles ------------------------------------------------------
    n_ft = sum(1 for k, _, _ in included if st.fulltext(k))
    tiles = [(pc["identified"] + pc["other_sources"], "records retrieved"), (pc["screened"], "unique records"),
             (len(included), "included"), (n_ft, "verified on full text"), (len(claims), "claims in matrix"),
             (sum(1 for _, _, w in included if w.is_retracted), "retracted (included!)")]
    if verify_report:
        tiles.insert(0, (f"{verify_report['score']:.0f}", f"verify score ({verify_report['band']})"))
    parts.append('<section id="overview"><h2>Overview</h2><div class="tiles">' +
                 "".join(f'<div class="tile"><b>{e(v)}</b><span>{e(l)}</span></div>' for v, l in tiles) + "</div>")
    sq = "".join(f"<li><b>{e(s.get('id'))}</b> {e(s.get('text'))}</li>" for s in plan.subqs)
    parts.append(f"<h3>Sub-questions</h3><ul>{sq}</ul>")
    crit = plan.criteria
    if crit.get("include") or crit.get("exclude"):
        parts.append("<h3>Eligibility</h3><p class=note>Include: " + e("; ".join(crit.get("include", []))) +
                     "<br>Exclude: " + e("; ".join(crit.get("exclude", []))) + "</p>")
    parts.append("</section>")
    # ---- PRISMA -----------------------------------------------------------
    parts.append(f'<section id="prisma"><h2>Study selection (PRISMA 2020 flow)</h2>{prisma.svg(pc)}</section>')
    # ---- findings -----------------------------------------------------------
    parts.append('<section id="findings"><h2>Summary of findings</h2>')
    if not claims:
        parts.append('<p class="note">No claims yet - add them with <code>factual-research claim add</code> and attach '
                     'evidence with <code>factual-research evidence add</code>.</p>')
    for c in claims:
        s = summarize_claim(c, st.evidence(c["id"]), works, combined)
        pct = 0 if s.consensus is None else round(s.consensus * 100)
        cc = "c-" + s.certainty.replace(" ", "")
        parts.append(f'<div class="claim"><div class="t">{e(c["id"])} · {e(c["text"])}</div>'
                     f'<div class="meta"><span class="badge {cc}">certainty: {e(s.certainty)}</span>'
                     f'<span>{e(s.label)}</span><div class="bar" title="design-weighted support {pct}%"><i style="width:{pct}%"></i></div>'
                     f'<span>{s.n} studies · ✔ {len(s.keys_for)} ✘ {len(s.keys_against)} ≈ {len(s.keys_mixed)}</span></div>'
                     f'<div class="note">{e("; ".join(s.reasons))}</div>')
        for ev in st.evidence(c["id"]):
            w = works.get(ev["key"])
            ok = (ev["verified"] or 0) >= 0.85
            href = _file_link(st, ev["key"], ev["loc"]) or f"#src-{ev['key']}"
            parts.append(f'<div class="ev {e(ev["stance"])}"><a class="cite" href="{e(href)}">'
                         f'{e(intext_author(w) + ", " + str(w.year)) if w else e(ev["key"])}</a> '
                         f'<span class="note">[{e(DESIGNS.get(w.design, w.design) if w else "")}, {e(ev["loc"] or "?")}]</span> '
                         f'<q>{e(ev["quote"])}</q>' + ("" if ok else ' <span class="warn">⚠ quote not verified</span>')
                         + (f' <b>{e(ev["effect"])}</b>' if ev["effect"] else "") + "</div>")
        parts.append("</div>")
    parts.append("</section>")
    # ---- tables ---------------------------------------------------------------
    cite_fmt = lambda k: f"{intext_author(works[k])}, {works[k].year}" if k in works else k  # noqa: E731
    parts.append('<section id="tables"><h2>Tables</h2>')
    if claims:
        parts.append(T.table_html(T.sof_table(st, plan), cite_fmt))
        parts.append("<br>" + T.table_html(T.matrix_table(st), cite_fmt))
    if st.cells():
        parts.append("<br>" + T.table_html(T.extraction_table(st, plan), cite_fmt))
        svg, n = T.forest_svg(st)
        if n:
            parts.append(f"<h3>Forest plot</h3>{svg}<p class=note>Effect estimates as extracted (not pooled).</p>")
    for f in sorted((st.root / "tables").glob("*.json")) if (st.root / "tables").is_dir() else []:
        try:
            t, _ = T.custom_table(st, json.loads(f.read_text(encoding="utf-8")))
            parts.append("<br>" + T.table_html(t, cite_fmt))
        except Exception as ex:  # noqa: BLE001
            parts.append(f'<p class="warn">table {e(f.name)}: {e(ex)}</p>')
    if not claims and not st.cells():
        parts.append('<p class="note">No evidence tables yet - <code>factual-research extract template</code>.</p>')
    parts.append("</section>")
    # ---- draft --------------------------------------------------------------------
    if draft:
        parts.append('<section id="draft"><h2>Draft & verification</h2>')
        if verify_report:
            r = verify_report
            comp = " · ".join(f"{k} {v:.0f}" for k, v in r["components"].items())
            parts.append(f'<div class="score">{r["score"]:.0f}/100 <span class="badge">{e(r["band"])}</span> '
                         f'<span class="badge">{"PASS" if r["pass"] else "FAIL"}</span></div>'
                         f'<p class="note">{e(comp)} · errors {r["counts"]["error"]}, warnings {r["counts"]["warn"]}, notes {r["counts"]["note"]}</p>')
            for it in r["issues"][:60]:
                parts.append(f'<div class="iss {e(it["severity"])}"><b>{e(it["severity"].upper())}</b> · {e(it["check"])}'
                             f'{" · line " + str(it["line"]) if it["line"] else ""} - {e(it["message"])}'
                             + (f'<div class="fx">“{e(it["text"][:200])}”</div>' if it["text"] else "")
                             + (f'<div class="fx">→ {e(it["fix"][:300])}</div>' if it["fix"] else "") + "</div>")
        il: dict[int, list] = {}
        for it in (verify_report or {}).get("issues", []):
            if it["line"] and it["severity"] in ("error", "warn"):
                il.setdefault(it["line"], []).append(it)
        parts.append('<h3>Text</h3><div class="draft">' + draft_html(st, draft.read_text(encoding="utf-8"), il, plan, cite_fmt) + "</div></section>")
    # ---- sources --------------------------------------------------------------------
    parts.append(f'<section id="sources"><h2>Included sources ({len(included)})</h2><div class="scroll"><table>'
                 "<thead><tr><th>Reference</th><th>Design</th><th>Weight</th><th>Text</th><th>Flags</th></tr></thead><tbody>")
    for k, s, w in sorted(included, key=lambda x: -weight(x[2], combined)):
        fl = "".join(f'<span class="flag{" r" if f == "RETRACTED" else ""}">{e(f)}</span>' for f in flags(w))
        link = _file_link(st, k)
        depth = st.source_text(k)[0]
        parts.append(f'<tr id="src-{e(k)}"><td><b>@{e(k)}</b><br>{e(reference(w, style))}'
                     f'{" · <a href=" + chr(34) + e(w.url) + chr(34) + ">link</a>" if w.url else ""}'
                     f'{" · <a href=" + chr(34) + e(link) + chr(34) + ">local file</a>" if link else ""}</td>'
                     f'<td>{e(DESIGNS.get(w.design, w.design))}<br><span class="note">{e(w.design_basis)}</span></td>'
                     f'<td>{weight(w, combined):.2f}</td><td>{e(depth)}</td><td>{fl}</td></tr>')
    parts.append("</tbody></table></div></section>")
    # ---- search log ------------------------------------------------------------------------
    parts.append('<section id="log"><h2>Search log</h2><details><summary>'
                 f'{len(st.queries())} queries - full reproducibility record in research.lock.json</summary>'
                 '<div class="scroll"><table><thead><tr><th>Date</th><th>SQ</th><th>Source</th><th>Query</th><th>Total</th>'
                 '<th>Got</th></tr></thead><tbody>')
    for q in st.queries():
        parts.append(f"<tr><td>{dt.datetime.fromtimestamp(q['ts']):%Y-%m-%d %H:%M}</td><td>{e(q['subq'])}</td>"
                     f"<td>{e(q['connector'])}</td><td><code>{e(q['query'][:220])}</code>"
                     f"{'<br><span class=warn>' + e(q['error'][:160]) + '</span>' if q['error'] else ''}</td>"
                     f"<td>{q['total']}</td><td>{q['returned']}</td></tr>")
    parts.append("</tbody></table></div></details></section>")
    parts.append(f"</main><script>{JS}</script></body></html>")
    return "\n".join(parts)
