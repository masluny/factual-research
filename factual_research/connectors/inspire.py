"""INSPIRE-HEP: high-energy physics literature with refereed flags."""
from __future__ import annotations

from .. import http
from ..model import Work, norm_doi
from . import Connector, SearchResult

FIELDS = "titles,authors.full_name,dois,arxiv_eprints,publication_info,citation_count,abstracts,document_type,refereed,preprint_date,control_number"


def parse(md: dict) -> Work:
    w = Work()
    w.title = ((md.get("titles") or [{}])[0].get("title") or "").rstrip(".")
    w.authors = [a.get("full_name") for a in (md.get("authors") or [])[:50] if a.get("full_name")]
    if md.get("dois"):
        w.doi = norm_doi(md["dois"][0].get("value", ""))
    if md.get("arxiv_eprints"):
        w.arxiv = md["arxiv_eprints"][0].get("value", "")
    pi = (md.get("publication_info") or [{}])[0]
    w.venue = pi.get("journal_title", "")
    w.volume = pi.get("journal_volume", "")
    w.year = pi.get("year") or (int(md["preprint_date"][:4]) if md.get("preprint_date") else None)
    w.abstract = (md.get("abstracts") or [{}])[0].get("value", "")
    w.cited_by = md.get("citation_count")
    dt = md.get("document_type") or []
    refereed = md.get("refereed", False)
    if "conference paper" in dt:
        w.kind = "conference_paper"
    elif "thesis" in dt:
        w.kind = "thesis"
    elif "review" in dt:
        w.kind = "review"
    elif refereed or w.venue:
        w.kind = "journal-article"
    else:
        w.kind, w.is_preprint = "preprint", True
    w.other_ids["inspire"] = str(md.get("control_number", ""))
    w.url = f"https://inspirehep.net/literature/{md.get('control_number')}"
    if w.arxiv:
        w.pdf_url = f"https://arxiv.org/pdf/{w.arxiv}"
    w.sources = ["inspire"]
    return w


class Inspire(Connector):
    name = "inspire"

    def search(self, query, limit=50, since=None, fresh=False):
        q = query + (f" and date > {since - 1}" if since else "")
        d = http.get_json("https://inspirehep.net/api/literature", {"q": q, "size": min(limit, 250), "fields": FIELDS,
                                                                   "sort": "mostcited"}, ttl=self.ttl(fresh))
        hits = d.get("hits", {})
        return SearchResult(total=int(hits.get("total", 0)), works=[parse(h["metadata"]) for h in hits.get("hits", [])])


CONNECTOR = Inspire()
