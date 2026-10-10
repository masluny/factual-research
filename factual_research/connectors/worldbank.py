"""World Bank indicators as citable data records.

Query syntax:  INDICATOR@COUNTRY[;COUNTRY...][:FROM-TO]
  e.g.  FP.CPI.TOTL.ZG@PL;DE:2015-2024   (inflation, Poland & Germany)
Free text instead searches indicator names (first 30 matches listed in the
note) so the agent can pick a code."""
from __future__ import annotations

import re

from .. import http
from ..model import Work
from . import Connector, SearchResult

BASE = "https://api.worldbank.org/v2/"


class WorldBank(Connector):
    name = "worldbank"

    def search(self, query, limit=10, since=None, fresh=False):
        m = re.fullmatch(r"\s*([A-Z0-9_.]+)@([A-Za-z;]+)(?::(\d{4})-(\d{4}))?\s*", query)
        if not m:
            return self._find_indicators(query)
        code, countries, y0, y1 = m.groups()
        y0 = y0 or str(since or 2000)
        y1 = y1 or "2100"
        d = http.get_json(f"{BASE}country/{countries}/indicator/{code}",
                          {"format": "json", "per_page": 2000, "date": f"{y0}:{y1}"}, ttl=self.ttl(fresh))
        if not isinstance(d, list) or len(d) < 2 or not d[1]:
            return SearchResult(0, [], note=f"World Bank: no data for {query}")
        rows = [r for r in d[1] if r.get("value") is not None]
        name = d[1][0]["indicator"]["value"]
        by_c: dict[str, list] = {}
        for r in rows:
            by_c.setdefault(r["country"]["value"], []).append(r)
        lines = []
        for c, rs in by_c.items():
            for r in sorted(rs, key=lambda x: x["date"]):
                lines.append(f"{c}, {r['date']}: {r['value']:.6g}.")
        years = sorted({r["date"] for r in rows})
        w = Work(title=f"{name} ({code}), {', '.join(by_c)}", authors=["World Bank"], venue="World Development Indicators",
                 year=int(years[-1]) if years else None, kind="dataset", abstract=f"{name}. " + " ".join(lines),
                 url=f"https://data.worldbank.org/indicator/{code}?locations={countries.replace(';', '-')}")
        w.other_ids["wb_indicator"] = f"{code}@{countries}"
        w.sources = ["worldbank"]
        return SearchResult(1, [w])

    def _find_indicators(self, q):
        d = http.get_json(f"{BASE}indicator", {"format": "json", "per_page": 20000, "source": 2}, ttl=30 * 86400)
        words = [w for w in re.findall(r"\w+", q.lower()) if len(w) > 2]
        hits = [i for i in (d[1] if isinstance(d, list) and len(d) > 1 else [])
                if all(w in i["name"].lower() for w in words)]
        note = "World Bank indicators matching: " + "; ".join(f"{i['id']} = {i['name']}" for i in hits[:30])
        return SearchResult(0, [], note=note if hits else f"World Bank: no indicator matches {q!r}")


CONNECTOR = WorldBank()
