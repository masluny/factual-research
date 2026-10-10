"""factual-research command line."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import webbrowser
from pathlib import Path

from . import __version__, env

GUIDE = """workflow:  init → (edit plan.toml) → search → screen → enrich → fetch → claim/evidence → extract → draft
           → verify (loop) → render → report.   `factual-research status` always tells you the next step."""


def _out(obj, as_json: bool):
    if as_json:
        print(json.dumps(obj, indent=1, ensure_ascii=False, default=str))
    else:
        print(obj)


def _proj(a):
    from .project import open_project
    return open_project(a.project)


def _keys(st, raw: list[str] | str | None) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        raw = raw.split(",")
    out = []
    for r in raw:
        for x in re.split(r"[,\s]+", r):
            x = x.strip()
            if not x:
                continue
            k = st.resolve(x)
            if not k:
                sys.exit(f"unknown key/id: {x}")
            out.append(k)
    return out


# ---------------------------------------------------------------------------
# project & plan
# ---------------------------------------------------------------------------

def cmd_init(a):
    from .project import init_project, project_slug
    d = Path(a.dir) if a.dir else Path.cwd() / project_slug(a.question)
    root, detected = init_project(a.question, d, a.packs.split(",") if a.packs else None, a.online, a.lang)
    print(f"project: {root}")
    print("domain detection:")
    for name, score, why in detected:
        print(f"  {name:12s} {score:5.1f}  {', '.join(why)}")
    print(f"\nnext: edit {root / 'plan.toml'} (sub-questions, connector queries, criteria), then\n"
          f"      cd {root} && factual-research search")


def cmd_detect(a):
    from .packs import detect
    for name, score, why in detect(a.question, online=a.online):
        print(f"{name:12s} {score:5.1f}  {', '.join(why)}")


def cmd_packs(a):
    from .packs import all_packs
    packs = all_packs()
    if a.show:
        p = packs.get(a.show) or sys.exit(f"no pack {a.show}")
        print(Path(p.path).read_text(encoding="utf-8"))
        return
    for p in packs.values():
        print(f"{p.name:12s} {p.title}\n{'':12s} sources: {', '.join(p.connectors)}"
              + (f" (+ optional {', '.join(p.optional_connectors)})" if p.optional_connectors else "")
              + f"\n{'':12s} authorities: {', '.join(p.authorities[:6])}\n")


def cmd_connectors(a):
    from . import connectors as C
    for name, (_, title, kind) in C.REGISTRY.items():
        ok, why = C.get(name).available()
        print(f"{name:16s} {kind:10s} {'ready' if ok else why:32s} {title}")


def cmd_status(a):
    st, plan = _proj(a)
    from .prisma import counts
    c = counts(st)
    nclaims = len(st.claims())
    ncells = len(st.cells())
    print(f"question : {plan.question}\npacks    : {', '.join(plan.pack_names)} | since {plan.since}")
    print(f"queries  : {len(st.queries())} | records {c['screened']} (dups removed {c['duplicates']}) | "
          f"awaiting screening {c['awaiting']} | excluded {c['auto_excluded'] + c['ta_excluded'] + c['ft_excluded']} | "
          f"included {c['included']} (full text {c['included_fulltext']})")
    print(f"matrix   : {nclaims} claims, {len(st.evidence())} evidence links | extraction cells: {ncells}")
    step = ("search" if not st.queries() else
            "screen list  → include/exclude" if c["awaiting"] and not c["included"] else
            "enrich  (retractions, versions, designs)" if not st.get_meta("enriched") else
            "fetch   (full texts)" if c["included"] and not c["included_fulltext"] else
            "claim add / evidence add" if not nclaims else
            "extract template → extract import" if not ncells else
            "verify draft.md   (after writing draft.md)")
    print(f"next     : factual-research {step}")


# ---------------------------------------------------------------------------
# search & screening
# ---------------------------------------------------------------------------

def cmd_search(a):
    st, plan = _proj(a)
    from .search import auto_screen, run_search
    conns = a.connectors.split(",") if a.connectors else None
    adhoc = (a.query, a.connector) if a.query else None
    print(f"searching ({', '.join(conns or plan.combined().connectors(optional=a.all))}) ...")
    s = run_search(st, plan, connectors=conns, limit=a.limit, only_sq=a.sq, adhoc=adhoc, fresh=a.fresh,
                   optional=a.all, tiered=not a.no_tiers)
    ex = auto_screen(st, plan)
    print(f"\n{s['queries']} queries, {s['hits']} hits, {s['new']} new unique records, {s['errors']} errors")
    if ex:
        print("auto-excluded: " + ", ".join(f"{k} {v}" for k, v in ex.items()))
    print("next: factual-research screen list")


def cmd_screen(a):
    st, plan = _proj(a)
    from .evidence import flags, weight
    from .search import auto_screen, relevance, write_lock
    if a.action == "auto":
        print(auto_screen(st, plan))
        return
    if a.action == "list":
        status = tuple(a.status.split(",")) if a.status else ("new", "maybe")
        rows = st.works(status=status)
        comb = plan.combined()
        scored = [(relevance(w, plan), weight(w, comb), k, s, w) for k, s, w in rows]
        if a.sort == "relevance":
            scored.sort(key=lambda x: -(x[0] * 0.75 + x[1] * 0.25))
        elif a.sort == "weight":
            scored.sort(key=lambda x: (-x[1], -x[0]))
        elif a.sort == "year":
            scored.sort(key=lambda x: -(x[4].year or 0))
        page = scored[a.offset:a.offset + a.limit]
        if a.json:
            _out([{"key": k, "status": s, "relevance": round(r, 2), "weight": wt, "design": w.design, "year": w.year,
                   "venue": w.venue, "title": w.title, "abstract": w.abstract[:a.chars], "flags": flags(w)}
                  for r, wt, k, s, w in page], True)
            return
        print(f"{len(rows)} records with status {','.join(status)} - showing {a.offset + 1}-{a.offset + len(page)} "
              f"(sorted by {a.sort})\n")
        for i, (r, wt, k, s, w) in enumerate(page, a.offset + 1):
            fl = f" ⚑ {', '.join(flags(w))}" if flags(w) else ""
            print(f"[{i:3d}] {k}  rel {r:.2f}  w {wt:.2f}  {w.design}  {w.year}  {w.venue[:40]}{fl}\n      {w.title}")
            if not a.brief and w.abstract:
                print(f"      {w.abstract[:a.chars]}{'…' if len(w.abstract) > a.chars else ''}")
            print()
        return
    if a.action == "import":
        p = Path(a.keys[0])
        rows = json.loads(p.read_text()) if p.suffix == ".json" else list(csv.DictReader(p.open()))
        n = 0
        for r in rows:
            k = st.resolve(r["key"])
            if not k:
                print(f"  unknown key {r['key']}")
                continue
            st.decide(k, r.get("stage", "ta"), r["decision"], r.get("reason", ""), by=r.get("by", "agent"))
            n += 1
        write_lock(st, plan)
        print(f"{n} decisions recorded")
        return
    decision = {"include": "include", "exclude": "exclude", "maybe": "maybe"}[a.action]
    if decision == "exclude" and not a.reason:
        sys.exit("exclusions need --reason (it goes into the PRISMA diagram)")
    for k in _keys(st, a.keys):
        st.decide(k, a.stage, decision, a.reason or "", by=a.by)
        print(f"  {decision:7s} @{k} ({a.stage})" + (f" - {a.reason}" if a.reason else ""))
    write_lock(st, plan)


def cmd_enrich(a):
    st, plan = _proj(a)
    from .search import enrich
    keys = _keys(st, a.keys) or None
    s = enrich(st, plan, keys=keys, deep=not a.fast)
    st.set_meta("enriched", True)
    print(s)


def cmd_snowball(a):
    st, plan = _proj(a)
    from .search import snowball
    s = snowball(st, plan, _keys(st, a.keys) or None, a.direction, a.per_seed, a.min_relevance)
    print(s)
    print("next: factual-research screen list   (snowballed records have origin 'snowball')")


def cmd_add(a):
    st, plan = _proj(a)
    from .connectors import crossref, openalex, pubmed
    from .fulltext import attach_file, doi_in_pdf
    from .model import Work
    w, path = None, None
    if a.file:
        path = Path(a.file).expanduser().resolve()
        if not path.exists():
            sys.exit(f"no such file {path}")
        if a.key and st.row(a.key):
            kind, n = attach_file(st, a.key, path)
            print(f"attached {path.name} to @{a.key} ({kind}, {n} parts)")
            return
        doi = a.doi or (doi_in_pdf(path) if path.suffix.lower() == ".pdf" else None)
        if doi:
            a.doi = doi
    if a.doi:
        w = crossref.by_doi(a.doi)
        try:
            got = openalex.by_ids([a.doi], kind="doi")
            if got:
                from .model import merge
                w = merge(w, got[0]) if w else got[0]
        except Exception:  # noqa: BLE001
            pass
    elif a.pmid:
        got = pubmed.fetch([a.pmid])
        w = got[0] if got else None
    elif a.arxiv:
        import xml.etree.ElementTree as ET
        from . import http
        from .connectors.arxiv import NS, parse
        xml = http.get_text("https://export.arxiv.org/api/query", {"id_list": a.arxiv})
        ents = ET.fromstring(xml).findall("a:entry", NS)
        w = parse(ents[0]) if ents else None
    elif path:
        from .fulltext import parse_pdf
        pages, meta = parse_pdf(path) if path.suffix.lower() == ".pdf" else ([], {})
        w = Work(title=a.title or meta.get("pdf_title") or path.stem, kind="other", sources=["manual"])
    if not w:
        sys.exit("could not resolve the source (give --doi / --pmid / --arxiv, or a PDF with a DOI on page 1)")
    if a.title:
        w.title = a.title
    k, new = st.upsert(w, origin="manual")
    st.index_abstract(k, st.get(k))
    st.db.commit()
    print(f"{'added' if new else 'already present as'} @{k}: {w.title[:90]}")
    if path:
        kind, n = attach_file(st, k, path)
        print(f"  full text: {path.name} ({kind}, {n} parts)")
    if a.include:
        st.decide(k, "ta", "include", a.reason or "added manually", by="user")
        print("  included")


def cmd_fetch(a):
    st, plan = _proj(a)
    from .fulltext import fetch_one
    from .store import INCLUDED
    keys = _keys(st, a.keys) or [k for k, s, w in st.works(status=INCLUDED) if a.retry or not st.fulltext(k)]
    ok = 0
    for k in keys:
        status, detail = fetch_one(st, k)
        ok += status == "ok"
        print(f"  {'✓' if status == 'ok' else '·'} @{k}: {detail[:150]}")
    print(f"\nfull text for {ok}/{len(keys)}. Paywalled papers: save the PDF as sources/<key>.pdf and re-run "
          f"`factual-research fetch --retry`.")


def cmd_show(a):
    st, plan = _proj(a)
    from .evidence import flags, weight
    k = _keys(st, [a.key])[0]
    w = st.get(k)
    if a.json:
        _out({"key": k, "status": st.status(k), **w.to_dict()}, True)
        return
    depth, full, pages = st.source_text(k)
    if a.text:
        for p in pages:
            if a.loc and a.loc.lower() not in p["loc"].lower():
                continue
            if a.grep and a.grep.lower() not in p["text"].lower():
                continue
            print(f"\n=== @{k} {p['loc']}{' (printed ' + p['label'] + ')' if p.get('label') else ''} ===\n{p['text']}")
        return
    print(f"@{k}  [{st.status(k)}]\n{w.title}\n{', '.join(w.authors[:8])}{' et al.' if len(w.authors) > 8 else ''}")
    print(f"{w.venue} {w.year} {w.volume}{'(' + w.issue + ')' if w.issue else ''} {w.pages}")
    print(f"design: {w.design} ({w.design_basis}) | weight {weight(w, plan.combined()):.2f} | text: {depth} "
          f"({len(pages)} parts) | cited by {w.cited_by}")
    ids = {x: getattr(w, x) for x in ("doi", "pmid", "pmcid", "arxiv", "openalex", "nct", "eli") if getattr(w, x)}
    print("ids: " + ", ".join(f"{x}={v}" for x, v in ids.items()))
    if flags(w):
        print("flags: " + ", ".join(flags(w)))
    if w.funding:
        print(f"funding: {w.funding[:300]}")
    if w.coi:
        print(f"COI: {w.coi[:300]}")
    if w.updates:
        print("notices: " + "; ".join(f"{u.get('type')} {u.get('doi', '')} {u.get('date', '')}" for u in w.updates))
    print(f"\n{w.abstract}")
    for d in st.decisions(k):
        print(f"decision: {d['stage']} {d['decision']} - {d['reason']} ({d['by']})")


def cmd_find(a):
    st, plan = _proj(a)
    keys = _keys(st, a.keys) or None
    if not keys and not a.everything:
        from .store import INCLUDED
        keys = [k for k, s, w in st.works(status=INCLUDED)] or None
    rows, seen = [], set()
    for r in st.search_chunks(a.query, keys=keys, limit=a.limit * 2):
        sig = (r["key"], r["text"][:120])
        if sig not in seen:
            seen.add(sig)
            rows.append(r)
    rows = rows[:a.limit]
    from .textutil import overlap
    res = sorted(({"key": r["key"], "loc": r["loc"], "src": r["src"], "score": round(overlap(a.query, r["text"]), 2),
                   "text": r["text"]} for r in rows), key=lambda x: -x["score"])
    if a.json:
        _out(res, True)
        return
    for r in res:
        print(f"@{r['key']}  {r['loc']}  ({r['src']}, overlap {r['score']:.2f})\n  {r['text']}\n")
    if not res:
        print("no passages - did you run `factual-research fetch` / `enrich` (indexes abstracts)?")


# ---------------------------------------------------------------------------
# claims, evidence, extraction, tables
# ---------------------------------------------------------------------------

def cmd_claim(a):
    st, plan = _proj(a)
    if a.action == "add":
        cid = a.id or st.next_claim_id()
        st.add_claim(cid, a.text, a.sq or "", a.outcome or "")
        print(f"claim {cid}: {a.text}")
    elif a.action == "rm":
        st.db.execute("DELETE FROM claims WHERE id=?", (a.text,))
        st.db.execute("DELETE FROM evidence WHERE claim_id=?", (a.text,))
        st.db.commit()
        print(f"removed {a.text}")
    else:
        for c in st.claims():
            print(f"{c['id']:5s} [{c['subq'] or '-'}] {c['text']}  ({len(st.evidence(c['id']))} evidence)")


def _add_ev(st, claim, key, stance, quote, loc, note, effect):
    from .tables import check_cell
    if not any(c["id"] == claim for c in st.claims()):
        return None, [f"unknown claim {claim}"]
    k = st.resolve(key)
    if not k:
        return None, [f"unknown key {key}"]
    if stance not in ("for", "against", "mixed", "neutral"):
        return None, [f"stance must be for/against/mixed/neutral, got {stance}"]
    sc, pr = check_cell(st, k, effect or "", quote, loc)
    if quote and not loc:
        from .citations import quote_location
        loc = quote_location(quote, "", st.source_text(k)[2])[1]
    eid = st.add_evidence(claim, k, stance, quote, loc, note, effect, sc)
    return (eid, sc, k), pr


def cmd_evidence(a):
    st, plan = _proj(a)
    if a.action == "add":
        res, pr = _add_ev(st, a.claim, a.key, a.stance, a.quote or "", a.loc or "", a.note or "", a.effect or "")
        if not res:
            sys.exit("; ".join(pr))
        eid, sc, k = res
        print(f"evidence #{eid}: {a.claim} ← @{k} [{a.stance}] verified {sc:.0%}")
        for p in pr:
            print(f"  ! {p}")
    elif a.action == "import":
        data = json.loads(Path(a.claim).read_text(encoding="utf-8"))
        for e in data:
            if e.get("claim") and not any(c["id"] == e["claim"] for c in st.claims()) and e.get("claim_text"):
                st.add_claim(e["claim"], e["claim_text"], e.get("sq", ""))
            res, pr = _add_ev(st, e["claim"], e["key"], e.get("stance", "for"), e.get("quote", ""), e.get("loc", ""),
                              e.get("note", ""), e.get("effect", ""))
            print(f"  {e['claim']} ← @{e['key']}: " + (f"verified {res[1]:.0%}" if res else "FAILED")
                  + (f"  ! {'; '.join(pr)}" if pr else ""))
    elif a.action == "rm":
        st.db.execute("DELETE FROM evidence WHERE id=?", (int(a.claim),))
        st.db.commit()
        print(f"removed evidence #{a.claim}")
    else:
        for e in st.evidence(a.claim if a.claim else None):
            ok = "✓" if (e["verified"] or 0) >= 0.85 else "⚠"
            print(f"#{e['id']:<4d} {e['claim_id']:5s} {e['stance']:8s} {ok} @{e['key']} {e['loc'] or ''}: \"{(e['quote'] or '')[:110]}\"")


def cmd_matrix(a):
    st, plan = _proj(a)
    from .evidence import summarize_claim
    works = {k: w for k, _, w in st.works()}
    comb = plan.combined()
    out = []
    for c in st.claims():
        s = summarize_claim(c, st.evidence(c["id"]), works, comb)
        out.append(s.__dict__)
        if not a.json:
            cons = "–" if s.consensus is None else f"{s.consensus:.0%}"
            print(f"{c['id']}: {c['text']}\n   {s.label} | consensus {cons} | certainty {s.certainty.upper()} | "
                  f"n={s.n} ✔{len(s.keys_for)} ✘{len(s.keys_against)} ≈{len(s.keys_mixed)}"
                  + (f" | {s.unverified} unverified quotes" if s.unverified else ""))
            for r in s.reasons:
                print(f"   - {r}")
    if a.json:
        _out(out, True)


def cmd_extract(a):
    st, plan = _proj(a)
    from . import tables as T
    if a.action == "template":
        t = T.extraction_template(st, plan, _keys(st, a.keys) or None)
        if a.out:
            Path(a.out).write_text(json.dumps(t, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"wrote {a.out} ({len(t['studies'])} studies × {len(t['_fields'])} fields)")
        else:
            _out(t, True)
    elif a.action == "set":
        k = _keys(st, [a.args[0]])[0]
        sc, pr = T.extract_set(st, k, a.args[1], a.args[2], a.quote or "", a.loc or "")
        print(f"@{k}.{a.args[1]} = {a.args[2]!r}  verified {sc:.0%}" + "".join(f"\n  ! {p}" for p in pr))
    elif a.action == "import":
        res = T.extract_import(st, json.loads(Path(a.args[0]).read_text(encoding="utf-8")))
        bad = [r for r in res if r[2] < 0.85]
        print(f"{len(res)} cells imported, {len(res) - len(bad)} verified, {len(bad)} need attention")
        for k, f, sc, pr in bad:
            print(f"  ⚠ @{k}.{f} ({sc:.0%}): {'; '.join(pr)}")
    elif a.action == "check":
        bad = 0
        for c in st.cells():
            sc, pr = T.check_cell(st, c["key"], c["value"], c["quote"], c["loc"])
            if sc < 0.85:
                bad += 1
                print(f"  ⚠ @{c['key']}.{c['field']} ({sc:.0%}): {'; '.join(pr)}")
        print(f"{len(st.cells())} cells, {bad} problems")


def cmd_table(a):
    st, plan = _proj(a)
    from . import tables as T
    if a.kind == "forest":
        svg, n = T.forest_svg(st, a.field or "effect", lang=plan.lang)
        if not n:
            sys.exit("no parsable effect estimates (extraction field 'effect', e.g. 'OR 0.88 (95% CI 0.81–0.96)')")
        out = Path(a.out or st.root / "out" / "forest.svg")
        out.parent.mkdir(exist_ok=True)
        out.write_text(svg, encoding="utf-8")
        print(f"wrote {out} ({n} studies)")
        return
    if a.kind == "extraction":
        t = T.extraction_table(st, plan, fields=a.fields.split(",") if a.fields else None, lang=plan.lang)
    elif a.kind == "sources":
        t = T.sources_table(st, plan, lang=plan.lang)
    elif a.kind == "sof":
        t = T.sof_table(st, plan, lang=plan.lang)
    elif a.kind == "matrix":
        t = T.matrix_table(st, plan.lang)
    elif a.kind == "custom":
        if not a.spec:
            sys.exit("custom tables need --spec tables/<name>.json")
        t, pr = T.custom_table(st, json.loads(Path(a.spec).read_text(encoding="utf-8")))
        for p in pr:
            print(f"  ⚠ {p}", file=sys.stderr)
        print(f"  {len(pr)} problems" if pr else "  all sourced cells verified", file=sys.stderr)
    else:
        sys.exit(f"unknown table {a.kind}")
    print(T.write(t, a.format, Path(a.out) if a.out else None))


# ---------------------------------------------------------------------------
# verify, render, export, report
# ---------------------------------------------------------------------------

def cmd_verify(a):
    st, plan = _proj(a)
    from .verify import format_report, verify
    r = verify(st, plan, Path(a.draft), a.target, a.semantic, a.style_scorer)
    if a.json:
        _out(r, True)
    else:
        print(format_report(r, a.max))
    st.set_meta("last_verify", {"draft": str(Path(a.draft).resolve()), "score": r["score"], "pass": r["pass"]})
    if a.strict and not r["pass"]:
        sys.exit(1)


def cmd_render(a):
    st, plan = _proj(a)
    from .export import md_to_docx, render_draft
    style = a.style or plan.combined().citation_style()
    text = Path(a.draft).read_text(encoding="utf-8")
    out_md, keys, problems = render_draft(st, plan, text, style, a.lang)
    out = Path(a.out) if a.out else Path(a.draft).with_name(Path(a.draft).stem + ".final.md")
    out.write_text(out_md, encoding="utf-8")
    print(f"wrote {out} ({len(keys)} references, style {style})")
    if a.docx:
        dx = out.with_suffix(".docx")
        md_to_docx(out_md, dx, out.parent)
        print(f"wrote {dx}")
    if not a.no_html:
        from .article import build_article
        from .verify import verify
        try:
            vr = verify(st, plan, Path(a.draft), a.target)
        except Exception as ex:  # noqa: BLE001
            vr = None
            problems.append(f"verify failed, no score in the HTML: {ex}")
        html, _, hp = build_article(st, plan, text, style, a.lang, vr, evidence=not a.no_evidence)
        hx = out.with_suffix(".html")
        hx.write_text(html, encoding="utf-8")
        print(f"wrote {hx}" + (f" (verify {vr['score']:.0f}/100)" if vr else ""))
        problems += [x for x in hp if x not in problems]
        if a.open:
            webbrowser.open(hx.resolve().as_uri())
    for p in problems:
        print(f"  ! {p}")


def cmd_export(a):
    st, plan = _proj(a)
    from . import export as X
    from .citations import parse_cites
    from .store import INCLUDED
    if a.cited:
        ks = list(dict.fromkeys(st.resolve(c.key) for c in parse_cites(Path(a.cited).read_text(encoding="utf-8"))))
        entries = [(k, st.get(k)) for k in ks if k]
    else:
        entries = [(k, w) for k, s, w in st.works(status=None if a.all else INCLUDED)]
    if a.fmt == "obsidian":
        out = Path(a.out or st.root / "out" / "vault")
        n = X.obsidian(st, plan, out, [k for k, _ in entries])
        print(f"wrote {n} source notes + claim notes to {out}")
        return
    data = {"bibtex": X.bibtex, "ris": X.ris, "csl": X.csl_json}[a.fmt](entries)
    if a.out:
        Path(a.out).write_text(data, encoding="utf-8")
        print(f"wrote {a.out} ({len(entries)} entries)")
    else:
        print(data)


def cmd_report(a):
    st, plan = _proj(a)
    from . import report as R
    from .report import build
    from .verify import verify
    R.PORTABLE = a.portable
    draft = Path(a.draft) if a.draft else None
    if not draft:
        last = st.get_meta("last_verify")
        if last and Path(last["draft"]).exists():
            draft = Path(last["draft"])
    vr = verify(st, plan, draft, a.target) if draft else None
    html = build(st, plan, draft, vr)
    out = Path(a.out) if a.out else st.root / "out" / "report.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out}" + (f" (draft {draft.name}: {vr['score']}/100)" if vr else ""))
    if a.open:
        webbrowser.open(out.resolve().as_uri())


def cmd_prisma(a):
    st, plan = _proj(a)
    from .prisma import counts, svg
    c = counts(st)
    if a.out:
        Path(a.out).write_text(svg(c), encoding="utf-8")
        print(f"wrote {a.out}")
    _out(c, True)


def cmd_lock(a):
    st, plan = _proj(a)
    from .search import write_lock
    write_lock(st, plan)
    print(f"wrote {st.root / 'research.lock.json'}")


def cmd_watch(a):
    st, plan = _proj(a)
    if a.print_cron:
        exe = shutil.which("factual-research") or f"{sys.executable} -m factual_research"
        print(f"# add with `crontab -e` - every Monday 08:00:\n0 8 * * 1 cd {st.root} && {exe} watch --notify >> "
              f"{st.root / 'out' / 'watch.log'} 2>&1")
        return
    from .search import watch
    r = watch(st, plan, notify=a.notify)
    print(f"{r['new']} new relevant records ({r['new_total']} new in total) since the last run"
          + (" - screen them with `factual-research screen list`" if r["new"] else ""))


# ---------------------------------------------------------------------------
# skill, mcp, doctor
# ---------------------------------------------------------------------------

def cmd_install_skill(a):
    src = Path(__file__).parent / "skill"
    if a.codex:
        dest = Path(a.dest or Path.cwd()) / "AGENTS.md"
        text = (src / "AGENTS.md").read_text(encoding="utf-8")
        if dest.exists() and "factual-research" in dest.read_text(encoding="utf-8"):
            print(f"{dest} already mentions factual-research - not changed")
            return
        with dest.open("a", encoding="utf-8") as f:
            f.write("\n\n" + text)
        print(f"appended factual-research instructions to {dest}")
        return
    dest = Path(a.dest).expanduser() if a.dest else Path.home() / ".claude" / "skills" / "factual-research"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(src / "SKILL.md", dest / "SKILL.md")
    shutil.copy(src / "WORKFLOW.md", dest / "WORKFLOW.md")
    print(f"installed skill → {dest}\nIn Claude Code just ask: \"research <topic> with factual-research\"")


def cmd_mcp(a):
    from .mcp_server import serve
    serve(a.project)


def cmd_doctor(a):
    import importlib.util
    print(f"factual-research {__version__} on Python {sys.version.split()[0]}")
    for mod, why in (("fitz", "PDF full text (pip install pymupdf)"), ("docx", "render --docx (pip install python-docx)"),
                     ("openpyxl", "table --format xlsx (pip install openpyxl)"),
                     ("sentence_transformers", "verify --semantic (pip install sentence-transformers)")):
        print(f"  {'✓' if importlib.util.find_spec(mod) else '·'} {mod:22s} {why}")
    print(f"  {'✓' if env('MAILTO') else '·'} FACTUAL_RESEARCH_MAILTO")
    for var in ("NCBI_API_KEY", "OPENALEX_API_KEY", "S2_API_KEY", "ADS_API_TOKEN", "IEEE_API_KEY", "CORE_API_KEY"):
        print(f"  {'✓' if os.environ.get(var) else '·'} {var}")
    if a.online:
        from . import connectors as C
        for name in C.REGISTRY:
            con = C.get(name)
            ok, why = con.available()
            if not ok:
                print(f"  - {name:16s} {why}")
                continue
            try:
                q = {"pubchem": "caffeine", "worldbank": "FP.CPI.TOTL.ZG@PL:2022-2023", "sejm_eli": "konsument",
                     "gus_bdl": "var:60270:2022-2023", "saos": "konsument"}.get(name, "vitamin D")
                r = con.search(q, limit=1, fresh=True)
                print(f"  ✓ {name:16s} total {r.total}" + (f" - {r.note[:80]}" if r.note and not r.works else ""))
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ {name:16s} {type(e).__name__}: {str(e)[:90]}")


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factual-research", description="Domain-aware research with verified citations.",
                                epilog=GUIDE, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"factual-research {__version__}")
    p.add_argument("--project", help="project folder (default: nearest folder with .fr/)")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("init", help="create a research project for a question")
    s.add_argument("question")
    s.add_argument("--dir")
    s.add_argument("--packs", help="comma list; default = auto-detected")
    s.add_argument("--online", action="store_true", help="also use OpenAlex topics for domain detection")
    s.add_argument("--lang", help="language of the final text (default: language of the question)")
    s.set_defaults(fn=cmd_init)

    s = sp.add_parser("detect", help="which domain packs fit a question")
    s.add_argument("question")
    s.add_argument("--online", action="store_true")
    s.set_defaults(fn=cmd_detect)

    s = sp.add_parser("packs", help="list domain packs")
    s.add_argument("--show")
    s.set_defaults(fn=cmd_packs)
    sp.add_parser("connectors", help="list sources and whether they are ready").set_defaults(fn=cmd_connectors)
    sp.add_parser("status", help="project overview + next step").set_defaults(fn=cmd_status)

    s = sp.add_parser("search", help="run the plan's queries")
    s.add_argument("--sq", help="only this sub-question")
    s.add_argument("--connectors", help="comma list overriding the packs")
    s.add_argument("--all", action="store_true", help="include optional connectors")
    s.add_argument("--limit", type=int)
    s.add_argument("--query", help="ad-hoc query (logged)")
    s.add_argument("--connector", help="with --query: only this connector")
    s.add_argument("--fresh", action="store_true", help="bypass the HTTP cache")
    s.add_argument("--no-tiers", action="store_true", help="skip the extra high-evidence-tier queries")
    s.set_defaults(fn=cmd_search)

    s = sp.add_parser("screen", help="list / include / exclude records")
    s.add_argument("action", choices=["list", "include", "exclude", "maybe", "auto", "import"])
    s.add_argument("keys", nargs="*")
    s.add_argument("--reason")
    s.add_argument("--stage", default="ta", choices=["ta", "ft"])
    s.add_argument("--by", default="agent")
    s.add_argument("--status", help="for list: comma statuses (default new,maybe)")
    s.add_argument("--sort", default="relevance", choices=["relevance", "weight", "year"])
    s.add_argument("--limit", type=int, default=25)
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--chars", type=int, default=420)
    s.add_argument("--brief", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_screen)

    s = sp.add_parser("enrich", help="retractions, preprint→published, designs, venue signals")
    s.add_argument("--keys")
    s.add_argument("--fast", action="store_true", help="skip per-DOI Crossref lookups")
    s.set_defaults(fn=cmd_enrich)

    s = sp.add_parser("snowball", help="citation chasing (references + citing works) via OpenAlex")
    s.add_argument("--keys")
    s.add_argument("--direction", default="both", choices=["back", "forward", "both"])
    s.add_argument("--per-seed", type=int, default=15)
    s.add_argument("--min-relevance", type=float, default=0.3)
    s.set_defaults(fn=cmd_snowball)

    s = sp.add_parser("add", help="add a source by DOI/PMID/arXiv or a PDF file")
    s.add_argument("file", nargs="?")
    s.add_argument("--doi")
    s.add_argument("--pmid")
    s.add_argument("--arxiv")
    s.add_argument("--key", help="attach the file to this existing key")
    s.add_argument("--title")
    s.add_argument("--include", action="store_true")
    s.add_argument("--reason")
    s.set_defaults(fn=cmd_add)

    s = sp.add_parser("fetch", help="get open-access full texts for included records")
    s.add_argument("--keys")
    s.add_argument("--retry", action="store_true")
    s.set_defaults(fn=cmd_fetch)

    s = sp.add_parser("show", help="show a record (or its full text with --text)")
    s.add_argument("key")
    s.add_argument("--text", action="store_true")
    s.add_argument("--loc", help="with --text: only parts whose location contains this (e.g. 'p. 4', 'Results')")
    s.add_argument("--grep", help="with --text: only parts containing this")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_show)

    s = sp.add_parser("find", aliases=["ask"], help="find passages (with locations) to quote")
    s.add_argument("query")
    s.add_argument("--keys")
    s.add_argument("--everything", action="store_true", help="search all records, not only included")
    s.add_argument("--limit", type=int, default=8)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_find)

    s = sp.add_parser("claim", help="claims of the evidence matrix")
    s.add_argument("action", choices=["add", "list", "rm"])
    s.add_argument("text", nargs="?")
    s.add_argument("--id")
    s.add_argument("--sq")
    s.add_argument("--outcome")
    s.set_defaults(fn=cmd_claim)

    s = sp.add_parser("evidence", help="link sources to claims with a verified quote")
    s.add_argument("action", choices=["add", "list", "import", "rm"])
    s.add_argument("claim", nargs="?")
    s.add_argument("key", nargs="?")
    s.add_argument("--stance", default="for")
    s.add_argument("--quote")
    s.add_argument("--loc")
    s.add_argument("--note")
    s.add_argument("--effect")
    s.set_defaults(fn=cmd_evidence)

    s = sp.add_parser("matrix", help="consensus + GRADE-style certainty per claim")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_matrix)

    s = sp.add_parser("extract", help="evidence-table data extraction (verified cells)")
    s.add_argument("action", choices=["template", "set", "import", "check"])
    s.add_argument("args", nargs="*")
    s.add_argument("--keys")
    s.add_argument("--out")
    s.add_argument("--quote")
    s.add_argument("--loc")
    s.set_defaults(fn=cmd_extract)

    s = sp.add_parser("table", help="build a table: sources | extraction | sof | matrix | forest | custom")
    s.add_argument("kind", choices=["sources", "extraction", "sof", "matrix", "forest", "custom"])
    s.add_argument("--format", default="md", choices=["md", "csv", "html", "latex", "xlsx", "json"])
    s.add_argument("--out")
    s.add_argument("--fields")
    s.add_argument("--field")
    s.add_argument("--spec")
    s.set_defaults(fn=cmd_table)

    s = sp.add_parser("verify", help="score a draft (0-100) with ranked fixes")
    s.add_argument("draft")
    s.add_argument("--target", type=float, default=80)
    s.add_argument("--json", action="store_true")
    s.add_argument("--semantic", action="store_true", help="multilingual embedding check (sentence-transformers)")
    s.add_argument("--style-scorer", help="path to write-as-me score.py (or its skill folder)")
    s.add_argument("--max", type=int, default=40)
    s.add_argument("--strict", action="store_true", help="exit 1 when not passing")
    s.set_defaults(fn=cmd_verify)

    s = sp.add_parser("render", help="final text: in-text citations, tables, reference list (+ HTML article)")
    s.add_argument("draft")
    s.add_argument("--style", choices=["apa", "harvard", "chicago", "vancouver", "ieee", "acs"])
    s.add_argument("--out")
    s.add_argument("--lang")
    s.add_argument("--docx", action="store_true")
    s.add_argument("--no-html", action="store_true", help="skip the standalone HTML article (written by default)")
    s.add_argument("--no-evidence", action="store_true", help="HTML without the strength-of-evidence block")
    s.add_argument("--target", type=float, default=80, help="verify target shown in the HTML badge")
    s.add_argument("--open", action="store_true", help="open the HTML article in the browser")
    s.set_defaults(fn=cmd_render)

    s = sp.add_parser("export", help="bibliography export")
    s.add_argument("fmt", choices=["bibtex", "ris", "csl", "obsidian"])
    s.add_argument("--out")
    s.add_argument("--cited", help="only sources cited in this draft")
    s.add_argument("--all", action="store_true", help="every record, not only included")
    s.set_defaults(fn=cmd_export)

    s = sp.add_parser("report", help="HTML research report")
    s.add_argument("--draft")
    s.add_argument("--out")
    s.add_argument("--target", type=float, default=80)
    s.add_argument("--open", action="store_true")
    s.add_argument("--portable", action="store_true", help="no links to local files (for sharing)")
    s.set_defaults(fn=cmd_report)

    s = sp.add_parser("prisma", help="PRISMA counts (+ --out flow.svg)")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_prisma)
    sp.add_parser("lock", help="write research.lock.json").set_defaults(fn=cmd_lock)

    s = sp.add_parser("watch", help="re-run searches, report new papers")
    s.add_argument("--notify", action="store_true", help="macOS notification when something new appears")
    s.add_argument("--print-cron", action="store_true")
    s.set_defaults(fn=cmd_watch)

    s = sp.add_parser("install-skill", help="install the Claude Code skill (or Codex AGENTS.md)")
    s.add_argument("--dest")
    s.add_argument("--codex", action="store_true")
    s.set_defaults(fn=cmd_install_skill)

    sp.add_parser("mcp", help="run as an MCP server (stdio)").set_defaults(fn=cmd_mcp)
    s = sp.add_parser("doctor", help="check optional deps, API keys, connectivity")
    s.add_argument("--online", action="store_true")
    s.set_defaults(fn=cmd_doctor)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        pass


if __name__ == "__main__":
    main()
