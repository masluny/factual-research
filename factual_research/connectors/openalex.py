"""OpenAlex: every discipline, citation graph, OA locations, retraction
flag, venue signals. Optional OPENALEX_API_KEY; FACTUAL_RESEARCH_MAILTO joins the
polite pool."""
from __future__ import annotations

import os
import re
from collections import Counter

from .. import env, http
from ..model import Work, norm_doi, set_display_authors
from . import Connector, SearchResult

BASE = "https://api.openalex.org/"
SELECT = ("id,doi,display_name,publication_year,publication_date,authorships,primary_location,best_oa_location,"
          "open_access,type,ids,cited_by_count,is_retracted,abstract_inverted_index,primary_topic,topics,"
          "referenced_works,biblio,language,study_designs,funders,mesh,locations")


def _auth() -> dict:
    p = {}
    if os.environ.get("OPENALEX_API_KEY"):
        p["api_key"] = os.environ["OPENALEX_API_KEY"]
    if env("MAILTO"):
        p["mailto"] = env("MAILTO")
    return p


def _abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def parse(r: dict) -> Work:
    w = Work()
    w.openalex = r.get("id", "").rsplit("/", 1)[-1]
    w.title = (r.get("display_name") or "").rstrip(".")
    w.year = r.get("publication_year")
    w.date = r.get("publication_date") or ""
    ids = r.get("ids") or {}
    w.doi = norm_doi(r.get("doi") or ids.get("doi") or "")
    w.pmid = (ids.get("pmid") or "").rsplit("/", 1)[-1]
    pmcid = (ids.get("pmcid") or "").rsplit("/", 1)[-1]
    w.pmcid = pmcid if pmcid.upper().startswith("PMC") else (f"PMC{pmcid}" if pmcid else "")
    set_display_authors(w, [(a.get("author") or {}).get("display_name") or a.get("raw_author_name")
                            for a in r.get("authorships") or []])
    pl = r.get("primary_location") or {}
    src = pl.get("source") or {}
    if src.get("type") == "repository":
        # e.g. a Cochrane review whose primary location is its Europe PMC copy: use the journal's own record
        journal = next((l for l in r.get("locations") or [] if (l.get("source") or {}).get("type") == "journal"), None)
        if journal:
            pl, src = journal, journal["source"]
    w.venue = src.get("display_name") or pl.get("raw_source_name") or ""
    w.publisher = src.get("host_organization_name") or ""
    b = r.get("biblio") or {}
    w.volume, w.issue = b.get("volume") or "", b.get("issue") or ""
    if b.get("first_page"):
        w.pages = b["first_page"] + (f"-{b['last_page']}" if b.get("last_page") else "")
    w.kind = r.get("type") or ""
    if w.kind == "preprint" or src.get("type") == "repository" and pl.get("version") == "submittedVersion":
        w.is_preprint = True
    w.abstract = _abstract(r.get("abstract_inverted_index"))
    w.language = r.get("language") or ""
    w.cited_by = r.get("cited_by_count")
    w.is_retracted = bool(r.get("is_retracted"))
    oa = r.get("best_oa_location") or {}
    w.pdf_url = oa.get("pdf_url") or ""
    w.oa_url = (r.get("open_access") or {}).get("oa_url") or oa.get("landing_page_url") or ""
    w.url = pl.get("landing_page_url") or (f"https://doi.org/{w.doi}" if w.doi else r.get("id", ""))
    w.references = ["openalex:" + x.rsplit("/", 1)[-1] for x in (r.get("referenced_works") or [])]
    pt = r.get("primary_topic") or {}
    w.topics = [t.get("display_name") for t in (r.get("topics") or [])[:3] if t.get("display_name")]
    if pt:
        w.extra["field"] = (pt.get("field") or {}).get("display_name")
    w.extra["study_designs"] = [s.get("display_name") for s in (r.get("study_designs") or [])]
    w.mesh = [m.get("descriptor_name") for m in (r.get("mesh") or []) if m.get("descriptor_name")]
    funders = [f.get("display_name") for f in (r.get("funders") or []) if f.get("display_name")]
    if funders:
        w.funding = "; ".join(funders)
    w.venue_signals = {k: v for k, v in {
        "listed_in": src.get("listed_in") or [], "in_doaj": src.get("is_in_doaj"), "core": src.get("is_core"),
        "source_type": src.get("type"), "open_access": (r.get("open_access") or {}).get("is_oa")}.items()
        if v not in (None, [], "")}
    w.sources = ["openalex"]
    return w


class OpenAlex(Connector):
    name = "openalex"

    def search(self, query, limit=50, since=None, fresh=False):
        query = re.sub(r"[?*]", " ", query).strip()   # '?'/'*' are wildcards in OpenAlex search
        params = {"search": query, "per-page": min(limit, 200), "select": SELECT, **_auth()}
        if since:
            params["filter"] = f"from_publication_date:{since}-01-01"
        d = http.get_json(BASE + "works", params, ttl=self.ttl(fresh))
        return SearchResult(total=int((d.get("meta") or {}).get("count", 0)),
                            works=[parse(r) for r in d.get("results", [])])


def by_ids(ids: list[str], kind: str = "openalex") -> list[Work]:
    """Batch lookup (≤50 per call) by openalex W-ids or DOIs."""
    out = []
    field = {"openalex": "openalex", "doi": "doi", "pmid": "pmid"}[kind]
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        d = http.get_json(BASE + "works", {"filter": f"{field}:" + "|".join(batch), "per-page": 50,
                                           "select": SELECT, **_auth()}, ttl=30 * 86400)
        out += [parse(r) for r in d.get("results", [])]
    return out


def citing(openalex_id: str, limit: int = 50, since: int | None = None) -> list[Work]:
    flt = f"cites:{openalex_id}" + (f",from_publication_date:{since}-01-01" if since else "")
    d = http.get_json(BASE + "works", {"filter": flt, "per-page": min(limit, 200), "sort": "cited_by_count:desc",
                                       "select": SELECT, **_auth()}, ttl=7 * 86400)
    return [parse(r) for r in d.get("results", [])]


def topic_fields(question: str, n: int = 25) -> Counter:
    d = http.get_json(BASE + "works", {"search": question, "per-page": n, "select": "primary_topic", **_auth()},
                      ttl=7 * 86400)
    c = Counter()
    for r in d.get("results", []):
        f = ((r.get("primary_topic") or {}).get("field") or {}).get("display_name")
        if f:
            c[f] += 1
    return c


def related_reviews(query: str, since: int | None = None, limit: int = 10) -> list[Work]:
    flt = "type:review" + (f",from_publication_date:{since}-01-01" if since else "")
    d = http.get_json(BASE + "works", {"search": query, "filter": flt, "per-page": limit, "select": SELECT,
                                       "sort": "publication_date:desc", **_auth()}, ttl=7 * 86400)
    return [parse(r) for r in d.get("results", [])]


CONNECTOR = OpenAlex()
