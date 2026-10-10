"""Semantic Scholar Graph API. Works without a key but the shared pool is
often rate-limited; set S2_API_KEY for reliable use."""
from __future__ import annotations

import os

from .. import http
from ..model import Work, norm_doi, set_display_authors
from . import Connector, SearchResult

BASE = "https://api.semanticscholar.org/graph/v1/"
FIELDS = "title,abstract,year,venue,externalIds,authors,citationCount,publicationTypes,openAccessPdf,journal,publicationDate"


def _hdr():
    k = os.environ.get("S2_API_KEY")
    return {"x-api-key": k} if k else {}


def parse(r: dict) -> Work:
    w = Work()
    w.s2 = r.get("paperId", "")
    w.title = (r.get("title") or "").rstrip(".")
    w.abstract = r.get("abstract") or ""
    w.year = r.get("year")
    w.date = r.get("publicationDate") or ""
    w.venue = r.get("venue") or (r.get("journal") or {}).get("name", "")
    j = r.get("journal") or {}
    w.volume, w.pages = j.get("volume", "") or "", (j.get("pages") or "").strip()
    ext = r.get("externalIds") or {}
    w.doi = norm_doi(ext.get("DOI", "") or "")
    w.pmid = str(ext.get("PubMed", "") or "")
    w.arxiv = ext.get("ArXiv", "") or ""
    set_display_authors(w, [a.get("name") for a in r.get("authors") or []])
    w.cited_by = r.get("citationCount")
    w.pub_types = list(r.get("publicationTypes") or [])
    pdf = (r.get("openAccessPdf") or {}).get("url")
    if pdf:
        w.pdf_url = pdf
    if "Conference" in w.pub_types:
        w.kind = "conference_paper"
    elif "JournalArticle" in w.pub_types:
        w.kind = "journal-article"
    elif w.arxiv and not w.venue:
        w.kind, w.is_preprint = "preprint", True
    w.url = f"https://www.semanticscholar.org/paper/{w.s2}"
    w.sources = ["semanticscholar"]
    # S2 uses "Review"/"MetaAnalysis"/"ClinicalTrial" - map onto PubMed-style names
    w.pub_types = [{"MetaAnalysis": "Meta-Analysis", "ClinicalTrial": "Clinical Trial", "CaseReport": "Case Reports",
                    "Review": "Review", "Editorial": "Editorial", "LettersAndComments": "Letter"}.get(p, p)
                   for p in w.pub_types if p not in ("JournalArticle", "Conference", "Study", "Dataset", "Book")]
    return w


class SemanticScholar(Connector):
    name = "semanticscholar"

    def search(self, query, limit=50, since=None, fresh=False):
        params = {"query": query, "limit": min(limit, 100), "fields": FIELDS}
        if since:
            params["year"] = f"{since}-"
        d = http.get_json(BASE + "paper/search", params, headers=_hdr(), ttl=self.ttl(fresh), retries=3)
        return SearchResult(total=int(d.get("total", 0)), works=[parse(r) for r in d.get("data") or []])


CONNECTOR = SemanticScholar()
