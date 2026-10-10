"""arXiv Atom API. Results are preprints unless a journal reference / DOI
shows a published version."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .. import http
from ..model import Work, norm_doi, set_display_authors
from . import Connector, SearchResult, year_of

NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom",
      "os": "http://a9.com/-/spec/opensearch/1.1/"}


def build_query(q: str) -> str:
    if re.search(r"\b(ti|abs|au|all|cat):", q):
        return q
    phrases = re.findall(r'"([^"]+)"', q)
    rest = re.sub(r'"[^"]+"', " ", q)
    terms = [f'all:"{p}"' for p in phrases] + [f"all:{t}" for t in re.findall(r"[\w-]{3,}", rest)
                                                if t.lower() not in {"and", "the", "for", "with"}]
    return " AND ".join(terms) or f"all:{q}"


def parse(e: ET.Element) -> Work:
    w = Work()
    aid = e.findtext("a:id", "", NS)
    w.arxiv = re.sub(r"v\d+$", "", aid.rsplit("/abs/", 1)[-1])
    w.title = " ".join(e.findtext("a:title", "", NS).split()).rstrip(".")
    w.abstract = " ".join(e.findtext("a:summary", "", NS).split())
    set_display_authors(w, [a.findtext("a:name", "", NS) for a in e.findall("a:author", NS)])
    w.date = e.findtext("a:published", "", NS)[:10]
    w.year = year_of(w.date)
    doi = e.findtext("arxiv:doi", "", NS)
    jref = e.findtext("arxiv:journal_ref", "", NS)
    if doi:
        w.published_version = norm_doi(doi)
    w.venue = jref or "arXiv"
    w.is_preprint = True
    w.kind = "preprint"
    cat = e.find("arxiv:primary_category", NS)
    if cat is not None:
        w.keywords = [cat.get("term", "")]
    w.url = f"https://arxiv.org/abs/{w.arxiv}"
    w.pdf_url = f"https://arxiv.org/pdf/{w.arxiv}"
    w.oa_url = w.url
    w.sources = ["arxiv"]
    return w


class ArXiv(Connector):
    name = "arxiv"

    def search(self, query, limit=50, since=None, fresh=False):
        xml = http.get_text("https://export.arxiv.org/api/query",
                            {"search_query": build_query(query), "max_results": min(limit, 200),
                             "sortBy": "relevance"}, ttl=self.ttl(fresh))
        root = ET.fromstring(xml)
        works = [parse(e) for e in root.findall("a:entry", NS)]
        if since:
            works = [w for w in works if not w.year or w.year >= since]
        total = int(root.findtext("os:totalResults", "0", NS) or 0)
        return SearchResult(total=total, works=works)


CONNECTOR = ArXiv()
