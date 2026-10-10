"""Polish legislation from the Sejm ELI API (Dziennik Ustaw / Monitor
Polski), the same corpus as ISAP. Searches act titles."""
from __future__ import annotations

import re

from .. import http
from ..model import Work
from . import Connector, SearchResult, year_of

BASE = "https://api.sejm.gov.pl/eli/"


def parse(it: dict) -> Work:
    w = Work()
    pub, year, pos = it.get("publisher", "DU"), it.get("year"), it.get("pos")
    w.eli = f"{pub}/{year}/{pos}"
    w.title = it.get("title", "")
    w.year = year_of(year)
    w.date = it.get("promulgation") or it.get("announcementDate") or ""
    w.venue = {"DU": "Dziennik Ustaw", "MP": "Monitor Polski"}.get(pub, pub)
    w.pages = f"poz. {pos}"
    w.authors = ["Sejm RP"] if it.get("type") == "Ustawa" else []
    w.kind = "legislation"
    w.extra.update({"act_type": it.get("type"), "in_force_status": it.get("status"),
                    "display_address": it.get("displayAddress"), "isap_id": it.get("address")})
    w.url = f"https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id={it.get('address')}"
    if it.get("textPDF"):
        w.pdf_url = f"{BASE}acts/{w.eli}/text.pdf"
    if it.get("textHTML"):
        w.extra["html_url"] = f"{BASE}acts/{w.eli}/text.html"
    w.abstract = f"{it.get('type', '')} - {it.get('displayAddress', '')}. Status: {it.get('status', '')}."
    w.language = "pl"
    w.sources = ["sejm_eli"]
    return w


class SejmELI(Connector):
    name = "sejm_eli"

    def search(self, query, limit=30, since=None, fresh=False):
        params = {"title": query, "limit": min(limit, 500)}
        if since:
            params["dateFrom"] = f"{since}-01-01"
        d = http.get_json(BASE + "acts/search", params, ttl=self.ttl(fresh))
        res = SearchResult(total=int(d.get("totalCount", 0)), works=[parse(i) for i in d.get("items", [])])
        if res.works or len(query.split()) < 3:
            return res
        # titles are matched as a phrase - fall back to the longest words (stemmed for Polish inflection)
        words = sorted({w for w in re.findall(r"[^\W\d_]{7,}", query.lower())}, key=len, reverse=True)[:3]
        seen = set()
        for wd in words:
            p2 = dict(params, title=wd[:-2], limit=max(5, limit // 3))
            d = http.get_json(BASE + "acts/search", p2, ttl=self.ttl(fresh))
            for it in d.get("items", []):
                w = parse(it)
                if w.eli not in seen:
                    seen.add(w.eli)
                    res.works.append(w)
                    res.total += 1
        res.note = f"no act title contains the full phrase; searched title words: {', '.join(x[:-2] for x in words)}"
        return res


CONNECTOR = SejmELI()
