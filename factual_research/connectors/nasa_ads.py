"""NASA Astrophysics Data System - needs a free token in ADS_API_TOKEN."""
from __future__ import annotations

import os

from .. import http
from ..model import Work, norm_doi
from . import Connector, SearchResult, year_of


class ADS(Connector):
    name = "nasa_ads"
    env_key = "ADS_API_TOKEN"

    def search(self, query, limit=50, since=None, fresh=False):
        q = query + (f" year:{since}-3000" if since else "")
        d = http.get_json("https://api.adsabs.harvard.edu/v1/search/query",
                          {"q": q, "rows": min(limit, 200),
                           "fl": "title,author,year,pub,doi,identifier,abstract,citation_count,doctype,property,bibcode"},
                          headers={"Authorization": f"Bearer {os.environ['ADS_API_TOKEN']}"}, ttl=self.ttl(fresh))
        out = []
        for r in d.get("response", {}).get("docs", []):
            w = Work(title=(r.get("title") or [""])[0], authors=r.get("author", [])[:50], year=year_of(r.get("year")),
                     venue=r.get("pub", ""), abstract=r.get("abstract", ""), cited_by=r.get("citation_count"))
            if r.get("doi"):
                w.doi = norm_doi(r["doi"][0])
            for ident in r.get("identifier", []):
                if ident.lower().startswith("arxiv:"):
                    w.arxiv = ident[6:]
            props = r.get("property", [])
            w.kind = "journal-article" if "REFEREED" in props else ("preprint" if r.get("doctype") == "eprint" else r.get("doctype", ""))
            w.is_preprint = w.kind == "preprint"
            w.url = f"https://ui.adsabs.harvard.edu/abs/{r.get('bibcode')}"
            w.other_ids["bibcode"] = r.get("bibcode", "")
            w.sources = ["nasa_ads"]
            out.append(w)
        return SearchResult(total=int(d.get("response", {}).get("numFound", 0)), works=out)


CONNECTOR = ADS()
