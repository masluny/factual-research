"""CORE: aggregator of open-access repository full texts - needs CORE_API_KEY."""
from __future__ import annotations

import os

from .. import http
from ..model import Work, norm_doi, set_display_authors
from . import Connector, SearchResult


class CORE(Connector):
    name = "core"
    env_key = "CORE_API_KEY"

    def search(self, query, limit=50, since=None, fresh=False):
        q = query + (f" AND yearPublished>={since}" if since else "")
        d = http.get_json("https://api.core.ac.uk/v3/search/works", {"q": q, "limit": min(limit, 100)},
                          headers={"Authorization": f"Bearer {os.environ['CORE_API_KEY']}"}, ttl=self.ttl(fresh))
        out = []
        for r in d.get("results", []):
            w = Work(title=r.get("title", ""), year=r.get("yearPublished"), abstract=r.get("abstract") or "",
                     doi=norm_doi(r.get("doi") or ""), publisher=r.get("publisher") or "")
            set_display_authors(w, [a.get("name") for a in r.get("authors", [])])
            w.venue = ((r.get("journals") or [{}])[0] or {}).get("title", "") or ""
            w.pdf_url = r.get("downloadUrl") or ""
            w.other_ids["core"] = str(r.get("id"))
            w.kind = (r.get("documentType") or "").lower()
            w.url = f"https://core.ac.uk/works/{r.get('id')}"
            w.sources = ["core"]
            out.append(w)
        return SearchResult(total=int(d.get("totalHits", 0)), works=out)


CONNECTOR = CORE()
