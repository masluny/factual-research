"""Search orchestration: run the plan's queries across the packs'
connectors in parallel, deduplicate into the project store, auto-screen,
enrich metadata, snowball through the citation graph, and watch for new
papers. Everything is logged for PRISMA and research.lock.json."""
from __future__ import annotations

import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import __version__
from . import connectors as C
from .model import Work, merge, norm_doi
from .project import Plan
from .store import EXCLUDED, INCLUDED, Store
from .textutil import overlap

# extra high-tier query appended per connector, so syntheses are not drowned
TIER_FILTERS = {
    "pubmed": ' AND (systematic[sb] OR meta-analysis[pt] OR randomized controlled trial[pt] OR practice guideline[pt] OR guideline[pt])',
}


def _jobs(plan: Plan, connectors: list[str], only_sq: str | None, adhoc: tuple[str, str] | None, tiered: bool):
    jobs = []
    if adhoc:
        q, conn = adhoc
        for c in ([conn] if conn else connectors):
            jobs.append(("adhoc", c, q))
        return jobs
    for sq in plan.subqs:
        if only_sq and sq.get("id") != only_sq:
            continue
        cq = sq.get("connector_queries", {}) or {}
        for c in connectors:
            qs = [cq[c]] if c in cq else list(sq.get("queries") or [sq.get("text", "")])
            for q in qs:
                if not q:
                    continue
                jobs.append((sq.get("id", "SQ?"), c, q))
                if tiered and c in TIER_FILTERS and "[pt]" not in q and "[sb]" not in q:
                    jobs.append((sq.get("id", "SQ?"), c, f"({q}){TIER_FILTERS[c]}"))
    return jobs


def run_search(st: Store, plan: Plan, connectors: list[str] | None = None, limit: int | None = None,
               only_sq: str | None = None, adhoc: tuple[str, str] | None = None, fresh: bool = False,
               optional: bool = False, tiered: bool = True, origin: str = "search", log=print) -> dict:
    combined = plan.combined()
    conns = connectors or combined.connectors(optional=optional)
    usable = []
    for c in conns:
        ok, why = C.get(c).available()
        if ok:
            usable.append(c)
        else:
            log(f"  skip {c}: {why}")
    limit = limit or combined.per_query()
    since = plan.since or combined.since()
    jobs = _jobs(plan, usable, only_sq, adhoc, tiered)
    run_id = dt.datetime.now().isoformat(timespec="seconds")
    stats = {"queries": 0, "hits": 0, "new": 0, "errors": 0, "new_keys": []}

    def work(job):
        sq, c, q = job
        try:
            return job, C.get(c).search(q, limit=limit, since=since, fresh=fresh), None
        except Exception as e:  # noqa: BLE001
            return job, None, f"{type(e).__name__}: {e}"

    # one worker per connector host keeps each API's rate limit while sources run in parallel
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(set(j[1] for j in jobs))))) as ex:
        futs = [ex.submit(work, j) for j in jobs]
        for f in as_completed(futs):
            (sq, c, q), res, err = f.result()
            stats["queries"] += 1
            if err:
                stats["errors"] += 1
                st.log_query(sq, c, q, {"limit": limit, "since": since}, 0, 0, error=err[:500], run=run_id)
                log(f"  ! {c:15s} {sq}: {err[:140]}")
                continue
            qid = st.log_query(sq, c, q, {"limit": limit, "since": since}, res.total, len(res.works), run=run_id)
            new_here = 0
            for rank, w in enumerate(res.works, 1):
                if not w.title:
                    continue
                key, is_new = st.upsert(w, origin=origin, commit=False)
                st.add_hit(qid, key, rank)
                if is_new:
                    new_here += 1
                    stats["new_keys"].append(key)
            st.db.commit()
            stats["hits"] += len(res.works)
            stats["new"] += new_here
            note = f"  ({res.note[:120]})" if res.note and not res.works else ""
            log(f"  {c:15s} {sq:5s} total={res.total:<8} got={len(res.works):<4} new={new_here:<4} {q[:70]}{note}")
    stats["new_keys"] = list(dict.fromkeys(st.current_key(k) for k in stats["new_keys"]))
    write_lock(st, plan)
    return stats


# ---------------------------------------------------------------------------
# relevance & auto screening
# ---------------------------------------------------------------------------

def relevance(w: Work, plan: Plan) -> float:
    text = f"{w.title}. {w.title}. {w.abstract[:1500]}"
    targets = [plan.question] + [sq.get("text", "") for sq in plan.subqs] + \
              [q for sq in plan.subqs for q in (sq.get("queries") or [])]
    return max((overlap(t, text) for t in targets if t), default=0.0)


def auto_screen(st: Store, plan: Plan, log=print) -> dict:
    combined = plan.combined()
    since = plan.since or combined.since()
    langs = [l.lower() for l in plan.criteria.get("languages", [])]
    lang_alias = {"eng": "en", "pol": "pl", "ger": "de", "fre": "fr", "spa": "es"}
    excl_designs = set(combined.screening("auto_exclude_designs", []) or [])
    counts: dict[str, int] = {}
    present_dois = {norm_doi(w.doi): k for k, s, w in st.works() if w.doi}
    for key, status, w in st.works(status="new"):
        reason = ""
        if w.is_retracted and combined.screening("auto_exclude_retracted", True):
            reason = "retracted"
        elif w.year and since and w.year < since:
            reason = f"published before {since}"
        elif w.design in excl_designs:
            reason = f"design excluded by pack: {w.design}"
        elif w.kind in ("paratext", "erratum", "peer-review"):
            reason = f"not a research item ({w.kind})"
        elif langs and w.language:
            lg = lang_alias.get(w.language.lower(), w.language.lower())[:2]
            if lg not in langs:
                reason = f"language {w.language} not in criteria"
        if not reason and w.is_preprint and w.published_version and norm_doi(w.published_version) in present_dois:
            reason = f"superseded by published version @{present_dois[norm_doi(w.published_version)]}"
        if reason:
            st.decide(key, "auto", "exclude", reason, by="rule")
            counts[reason.split(":")[0]] = counts.get(reason.split(":")[0], 0) + 1
    write_lock(st, plan)
    return counts


# ---------------------------------------------------------------------------
# enrichment: retractions, published versions, venue signals, design types
# ---------------------------------------------------------------------------

def enrich(st: Store, plan: Plan, keys: list[str] | None = None, deep: bool = True, log=print) -> dict:
    from .connectors import crossref, openalex, pubmed
    rows = [(k, s, w) for k, s, w in st.works() if (keys is None and s not in EXCLUDED) or (keys and k in keys)]
    stats = {"openalex": 0, "pubmed": 0, "crossref": 0, "retracted": 0, "linked_versions": 0}
    # 1) OpenAlex batch by DOI: venue signals, is_retracted, OA pdf, study designs
    need = [(k, w) for k, s, w in rows if w.doi and not w.openalex]
    by_doi = {norm_doi(w.doi): k for k, w in need}
    for i in range(0, len(need), 50):
        try:
            for ow in openalex.by_ids([norm_doi(w.doi) for _, w in need[i:i + 50]], kind="doi"):
                k = by_doi.get(norm_doi(ow.doi))
                if k:
                    st.save(k, merge(st.get(k), ow))
                    stats["openalex"] += 1
        except Exception as e:  # noqa: BLE001
            log(f"  openalex enrich failed: {e}")
    # 2) PubMed batch: curated publication types + MeSH for anything with a PMID
    need = [(k, w) for k, s, w in st.works() if (k in {r[0] for r in rows}) and w.pmid and "pubmed" not in w.sources]
    if need:
        try:
            got = {pw.pmid: pw for pw in pubmed.fetch([w.pmid for _, w in need])}
            for k, w in need:
                if w.pmid in got:
                    st.save(k, merge(st.get(k), got[w.pmid]))
                    stats["pubmed"] += 1
        except Exception as e:  # noqa: BLE001
            log(f"  pubmed enrich failed: {e}")
    # 3) Crossref per DOI (only shortlisted): retraction notices, preprint→published
    if deep:
        targets = [(k, w) for k, s, w in st.works() if (k in {r[0] for r in rows}) and w.doi and
                   (keys or s in INCLUDED or s == "maybe")]
        for k, w in targets:
            try:
                cw = crossref.by_doi(w.doi)
            except Exception as e:  # noqa: BLE001
                log(f"  crossref {w.doi}: {e}")
                continue
            if not cw:
                continue
            cur = merge(st.get(k), cw)
            st.save(k, cur)
            stats["crossref"] += 1
            if cur.is_retracted:
                stats["retracted"] += 1
                log(f"  !! @{k} is RETRACTED ({', '.join(u.get('doi', '') for u in cur.updates if u.get('type') == 'retraction')})")
            if cur.is_preprint and cur.published_version and not st.key_for_id("doi", cur.published_version):
                pv = crossref.by_doi(cur.published_version)
                if pv:
                    nk, _ = st.upsert(pv, origin="linked")
                    stats["linked_versions"] += 1
                    log(f"  preprint @{k} has a peer-reviewed version → added @{nk}")
    for k, s, w in st.works():
        st.index_abstract(k, w)
    st.db.commit()
    write_lock(st, plan)
    return stats


# ---------------------------------------------------------------------------
# snowballing (citation chasing) via OpenAlex
# ---------------------------------------------------------------------------

def snowball(st: Store, plan: Plan, keys: list[str] | None, direction: str = "both", per_seed: int = 25,
             min_relevance: float = 0.25, log=print) -> dict:
    from .connectors import openalex
    seeds = keys or [k for k, s, w in st.works(status=INCLUDED)]
    stats = {"seeds": len(seeds), "candidates": 0, "added": 0}
    since = plan.since or plan.combined().since()
    for k in seeds:
        w = st.get(k)
        if not w:
            continue
        if not w.openalex and w.doi:
            try:
                got = openalex.by_ids([norm_doi(w.doi)], kind="doi")
                if got:
                    w = merge(w, got[0])
                    st.save(k, w)
            except Exception as e:  # noqa: BLE001
                log(f"  {k}: openalex lookup failed: {e}")
        cands: list[Work] = []
        if direction in ("back", "both"):
            ref_ids = [r.split(":", 1)[1] for r in w.references if r.startswith("openalex:")]
            dois = [r.split(":", 1)[1] for r in w.references if r.startswith("doi:")]
            try:
                if ref_ids:
                    cands += openalex.by_ids(ref_ids[:200])
                elif dois:
                    cands += openalex.by_ids(dois[:200], kind="doi")
            except Exception as e:  # noqa: BLE001
                log(f"  {k}: references failed: {e}")
        if direction in ("forward", "both") and w.openalex:
            try:
                cands += openalex.citing(w.openalex, limit=100, since=since)
            except Exception as e:  # noqa: BLE001
                log(f"  {k}: citations failed: {e}")
        scored = sorted(((relevance(c, plan), c) for c in cands if c.title), key=lambda x: -x[0])
        stats["candidates"] += len(scored)
        added = 0
        for rel, c in scored:
            if rel < min_relevance or added >= per_seed:
                break
            nk, is_new = st.upsert(c, origin="snowball", commit=False)
            if is_new:
                added += 1
        st.db.commit()
        stats["added"] += added
        log(f"  @{k}: {len(scored)} linked works, {added} relevant new")
    write_lock(st, plan)
    return stats


# ---------------------------------------------------------------------------
# watch: re-run all queries without cache and report what is new
# ---------------------------------------------------------------------------

def watch(st: Store, plan: Plan, notify: bool = False, min_relevance: float = 0.3, log=print) -> dict:
    """Replay every distinct logged query (same connector, query, limit,
    since) without the cache and report records that were not seen before."""
    before = {k for k, s, w in st.works()}
    seen_q, jobs = set(), []
    for q in st.queries():
        if q["error"] or q["run"] == "watch" or q["subq"] == "adhoc":
            continue
        sig = (q["connector"], q["query"])
        if sig in seen_q:
            continue
        seen_q.add(sig)
        jobs.append((q["subq"], q["connector"], q["query"], json.loads(q["params"] or "{}")))
    new_keys = []
    for sq, conn, query, params in jobs:
        try:
            res = C.get(conn).search(query, limit=params.get("limit", 50), since=params.get("since"), fresh=True)
        except Exception as e:  # noqa: BLE001
            log(f"  ! {conn}: {e}")
            continue
        qid = st.log_query(sq, conn, query, params, res.total, len(res.works), run="watch")
        for rank, w in enumerate(res.works, 1):
            if not w.title:
                continue
            k, is_new = st.upsert(w, origin="watch", commit=False)
            st.add_hit(qid, k, rank)
            if is_new and k not in before:
                new_keys.append(k)
        st.db.commit()
    auto_screen(st, plan, log=lambda *a: None)
    fresh = [(k, st.get(k)) for k in dict.fromkeys(st.current_key(k) for k in new_keys) if st.status(k) == "new"]
    scored = sorted(((relevance(w, plan), k, w) for k, w in fresh), key=lambda x: -x[0])
    relevant = [(r, k, w) for r, k, w in scored if r >= min_relevance]
    st.set_meta("last_watch", {"ts": time.time(), "new": [k for _, k, _ in relevant]})
    for r, k, w in relevant[:30]:
        log(f"  NEW @{k} [{w.design}] {w.year} rel {r:.2f}  {w.title[:90]}")
    if notify and relevant:
        import subprocess
        msg = f"{len(relevant)} new papers for: {plan.question[:80]}"
        subprocess.run(["osascript", "-e", f'display notification {json.dumps(msg)} with title "factual-research watch"'],
                       check=False)
    write_lock(st, plan)
    return {"new": len(relevant), "new_total": len(fresh), "keys": [k for _, k, _ in relevant]}


# ---------------------------------------------------------------------------
# research.lock.json - everything needed to reproduce the search
# ---------------------------------------------------------------------------

def write_lock(st: Store, plan: Plan):
    qs = []
    for q in st.queries():
        qs.append({"run": q["run"], "subq": q["subq"], "connector": q["connector"], "query": q["query"],
                   "params": json.loads(q["params"] or "{}"),
                   "date": dt.datetime.fromtimestamp(q["ts"]).isoformat(timespec="seconds"),
                   "total": q["total"], "returned": q["returned"], **({"error": q["error"]} if q["error"] else {})})
    inc = []
    for k, s, w in st.works(status=INCLUDED):
        inc.append({"key": k, "status": s, "doi": w.doi or None, "pmid": w.pmid or None, "arxiv": w.arxiv or None,
                    "title": w.title, "year": w.year, "design": w.design})
    lock = {"tool": f"factual-research {__version__}", "written": dt.datetime.now().isoformat(timespec="seconds"),
            "question": plan.question, "packs": plan.pack_names, "since": plan.since,
            "criteria": plan.criteria, "subquestions": [{"id": s.get("id"), "text": s.get("text")} for s in plan.subqs],
            "queries": qs, "included": inc,
            "decisions": [{"key": d["key"], "stage": d["stage"], "decision": d["decision"], "reason": d["reason"],
                           "by": d["by"]} for d in st.decisions()]}
    (st.root / "research.lock.json").write_text(json.dumps(lock, indent=1, ensure_ascii=False), encoding="utf-8")
