"""NASA Technical Reports Server: public technical memoranda, reports and
conference papers, many with full text."""
from __future__ import annotations

from .. import http
from ..model import Work, norm_doi
from . import Connector, SearchResult, year_of

KIND = {"TECHNICAL_MEMORANDUM": "technical_report", "TECHNICAL_REPORT": "technical_report",
        "CONTRACTOR_REPORT": "technical_report", "CONFERENCE_PAPER": "conference_paper",
        "JOURNAL_ARTICLE": "journal-article", "PRESENTATION": "other", "THESIS_DISSERTATION": "thesis",
        "OTHER": "technical_report"}


def parse(r: dict) -> Work:
    w = Work()
    w.title = r.get("title", "")
    w.abstract = r.get("abstract", "") or ""
    w.authors = [((a.get("meta") or {}).get("author") or {}).get("name") for a in r.get("authorAffiliations", [])]
    w.authors = [a for a in w.authors if a]
    pubs = r.get("publications") or [{}]
    w.date = (pubs[0].get("publicationDate") or r.get("submittedDate") or "")[:10]
    w.year = year_of(w.date)
    if pubs[0].get("doi"):
        w.doi = norm_doi(pubs[0]["doi"])
    w.venue = pubs[0].get("publicationName") or "NASA Technical Reports Server"
    w.kind = KIND.get(r.get("stiType", ""), "technical_report")
    w.other_ids["ntrs"] = str(r.get("id"))
    w.url = f"https://ntrs.nasa.gov/citations/{r.get('id')}"
    for d in r.get("downloads") or []:
        links = d.get("links") or {}
        if links.get("pdf") and not w.pdf_url:
            w.pdf_url = "https://ntrs.nasa.gov" + links["pdf"]
        if links.get("fulltext"):
            w.extra["fulltext_txt"] = "https://ntrs.nasa.gov" + links["fulltext"]
    w.funding = "NASA"
    w.sources = ["ntrs"]
    return w


class NTRS(Connector):
    name = "ntrs"

    def search(self, query, limit=50, since=None, fresh=False):
        params = {"q": query, "page.size": min(limit, 100)}
        if since:
            params["published.gte"] = f"{since}-01-01"
        d = http.get_json("https://ntrs.nasa.gov/api/citations/search", params, ttl=self.ttl(fresh))
        return SearchResult(total=int((d.get("stats") or {}).get("total", 0)), works=[parse(r) for r in d.get("results", [])])


CONNECTOR = NTRS()
