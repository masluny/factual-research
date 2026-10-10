"""Crossref: DOI metadata, retraction / correction notices (incl. the
Retraction Watch database, now served by Crossref) and preprint→published
relations."""
from __future__ import annotations

import urllib.parse

from .. import http
from ..model import Work, norm_doi, person_name
from . import Connector, SearchResult, strip_tags

BASE = "https://api.crossref.org/"


def _date(m: dict) -> tuple[int | None, str]:
    for k in ("published", "published-print", "published-online", "issued", "created"):
        dp = (m.get(k) or {}).get("date-parts")
        if dp and dp[0] and dp[0][0]:
            parts = dp[0]
            return parts[0], "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts))
    return None, ""


def parse(m: dict) -> Work:
    w = Work()
    w.doi = norm_doi(m.get("DOI", ""))
    w.title = strip_tags(" ".join(m.get("title") or [])).rstrip(".")
    sub = " ".join(m.get("subtitle") or [])
    if sub and sub.lower() not in w.title.lower():
        w.title += f": {strip_tags(sub)}"
    for a in m.get("author") or []:
        if a.get("family"):
            w.authors.append(person_name(a["family"], a.get("given", "")))
        elif a.get("name"):
            w.authors.append(a["name"])
    w.year, w.date = _date(m)
    w.venue = " ".join(m.get("container-title") or []) or (m.get("institution") or [{}])[0].get("name", "")
    w.publisher = m.get("publisher", "")
    w.volume, w.issue, w.pages = m.get("volume", ""), m.get("issue", ""), m.get("page", "")
    w.kind = m.get("type", "")
    if w.kind == "posted-content" and m.get("subtype", "preprint") == "preprint":
        w.is_preprint = True
        w.kind = "preprint"
    w.abstract = strip_tags(m.get("abstract", ""))
    w.cited_by = m.get("is-referenced-by-count")
    w.language = m.get("language", "")
    w.keywords = list(m.get("subject") or [])
    funders = [f.get("name") for f in m.get("funder") or [] if f.get("name")]
    if funders:
        w.funding = "; ".join(dict.fromkeys(funders))
    for l in m.get("link") or []:
        if "pdf" in (l.get("content-type") or "") and l.get("intended-application") in ("text-mining", "similarity-checking", None):
            w.extra.setdefault("crossref_pdf", l.get("URL"))
    for u in m.get("updated-by") or []:
        w.updates.append({"type": u.get("type"), "doi": norm_doi(u.get("DOI", "")), "source": u.get("source", "crossref"),
                          "date": (u.get("updated") or {}).get("date-time", "")[:10], "label": u.get("label", "")})
        if u.get("type") == "retraction":
            w.is_retracted = True
    rel = m.get("relation") or {}
    for r in rel.get("is-preprint-of", []):
        if r.get("id-type") == "doi":
            w.published_version = norm_doi(r["id"])
    w.url = m.get("URL") or (f"https://doi.org/{w.doi}" if w.doi else "")
    w.references = ["doi:" + norm_doi(r["DOI"]) for r in m.get("reference") or [] if r.get("DOI")]
    w.sources = ["crossref"]
    return w


class Crossref(Connector):
    name = "crossref"

    def search(self, query, limit=50, since=None, fresh=False):
        params = {"query.bibliographic": query, "rows": min(limit, 1000),
                  "select": "DOI,title,subtitle,author,published,published-print,published-online,issued,created,"
                            "container-title,publisher,volume,issue,page,type,abstract,is-referenced-by-count,"
                            "subject,funder,link,updated-by,relation,URL,posted"}
        if since:
            params["filter"] = f"from-pub-date:{since}-01-01"
        d = http.get_json(BASE + "works", params, ttl=self.ttl(fresh))
        msg = d.get("message", {})
        return SearchResult(total=int(msg.get("total-results", 0)), works=[parse(m) for m in msg.get("items", [])])


def by_doi(doi: str, ttl: float = 14 * 86400) -> Work | None:
    try:
        d = http.get_json(BASE + "works/" + urllib.parse.quote(norm_doi(doi), safe="/()"), ttl=ttl)
    except http.HTTPError as e:
        if e.status == 404:
            return None
        raise
    return parse(d["message"])


def notices_for(doi: str) -> list[dict]:
    """Retraction/correction notices that point at this DOI."""
    d = http.get_json(BASE + "works", {"filter": f"updates:{norm_doi(doi)}", "rows": 20}, ttl=7 * 86400)
    out = []
    for it in d.get("message", {}).get("items", []):
        for u in it.get("update-to") or []:
            if norm_doi(u.get("DOI", "")) == norm_doi(doi):
                out.append({"type": u.get("type"), "doi": norm_doi(it.get("DOI", "")), "source": u.get("source", "crossref"),
                            "date": (u.get("updated") or {}).get("date-time", "")[:10], "label": u.get("label", "")})
    return out


CONNECTOR = Crossref()
