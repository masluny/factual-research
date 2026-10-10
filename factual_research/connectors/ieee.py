"""IEEE Xplore metadata API - needs IEEE_API_KEY (free developer key)."""
from __future__ import annotations

import os

from .. import http
from ..model import Work, norm_doi, set_display_authors
from . import Connector, SearchResult, year_of


class IEEE(Connector):
    name = "ieee"
    env_key = "IEEE_API_KEY"

    def search(self, query, limit=50, since=None, fresh=False):
        params = {"querytext": query, "max_records": min(limit, 200), "apikey": os.environ["IEEE_API_KEY"],
                  "format": "json"}
        if since:
            params["start_year"] = since
        d = http.get_json("https://ieeexploreapi.ieee.org/api/v1/search/articles", params, ttl=self.ttl(fresh))
        out = []
        for a in d.get("articles", []):
            w = Work(title=a.get("title", ""), year=year_of(a.get("publication_year")),
                     venue=a.get("publication_title", ""), abstract=a.get("abstract", ""),
                     doi=norm_doi(a.get("doi", "")), cited_by=a.get("citing_paper_count"),
                     publisher=a.get("publisher", "IEEE"))
            set_display_authors(w, [x.get("full_name") for x in (a.get("authors") or {}).get("authors", [])])
            ct = (a.get("content_type") or "").lower()
            w.kind = {"conferences": "conference_paper", "journals": "journal-article", "standards": "standard",
                      "early access articles": "journal-article", "books": "book"}.get(ct, ct)
            w.url = a.get("html_url") or a.get("abstract_url", "")
            if a.get("access_type") == "OPEN_ACCESS":
                w.pdf_url = a.get("pdf_url", "")
            w.sources = ["ieee"]
            out.append(w)
        return SearchResult(total=int(d.get("total_records", 0)), works=out)


CONNECTOR = IEEE()
