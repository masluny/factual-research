"""dblp: curated computer-science bibliography (venues, CoRR preprints)."""
from __future__ import annotations

import html
import json
import re

from .. import http
from ..model import Work, norm_doi, set_display_authors
from . import Connector, SearchResult, year_of


def parse(info: dict) -> Work:
    w = Work()
    w.title = html.unescape(info.get("title", "")).rstrip(".")
    au = (info.get("authors") or {}).get("author", [])
    if isinstance(au, dict):
        au = [au]
    # dblp disambiguates homonyms with a number: "Wei Wang 0001"
    set_display_authors(w, [re.sub(r"\s+\d{4}$", "", html.unescape(a.get("text", ""))) for a in au])
    w.venue = info.get("venue", "") if isinstance(info.get("venue"), str) else " ".join(info.get("venue", []))
    w.year = year_of(info.get("year"))
    w.doi = norm_doi(info.get("doi", ""))
    w.volume, w.pages = info.get("volume", ""), info.get("pages", "")
    t = info.get("type", "")
    if "Conference" in t:
        w.kind = "conference_paper"
    elif "Journal" in t:
        w.kind = "journal-article"
    elif "Informal" in t:
        w.kind, w.is_preprint = "preprint", True
        ee = info.get("ee", "")
        if "arxiv.org/abs/" in ee:
            w.arxiv = ee.rsplit("/abs/", 1)[-1]
    else:
        w.kind = t.lower()
    w.url = info.get("ee", "") or info.get("url", "")
    w.other_ids["dblp"] = info.get("key", "")
    w.sources = ["dblp"]
    return w


class DBLP(Connector):
    name = "dblp"

    def search(self, query, limit=50, since=None, fresh=False):
        st, ctype, body = http.request("https://dblp.org/search/publ/api",
                                       {"q": query, "format": "json", "h": min(limit, 1000)}, ttl=self.ttl(fresh))
        if "json" not in ctype:
            # dblp sometimes sits behind a browser bot-check; we never try to get around it
            return SearchResult(0, [], note="dblp returned a browser check page instead of JSON - skipped "
                                            "(OpenAlex/arXiv cover the same venues)")
        d = json.loads(body.decode("utf-8"))
        hits = d.get("result", {}).get("hits", {})
        works = [parse(h["info"]) for h in hits.get("hit", [])]
        if since:
            works = [w for w in works if not w.year or w.year >= since]
        return SearchResult(total=int(hits.get("@total", 0)), works=works)


CONNECTOR = DBLP()
