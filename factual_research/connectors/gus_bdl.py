"""GUS Bank Danych Lokalnych (Statistics Poland) as citable data records.

Query syntax:  var:<variable id>[:FROM-TO]     e.g.  var:60270:2015-2024
Free text (e.g. "stopa bezrobocia") lists matching variables with ids in the
note, so the agent can pick one. Values are for Poland (unit level 0)."""
from __future__ import annotations

import re

from .. import http
from ..model import Work
from . import Connector, SearchResult

BASE = "https://bdl.stat.gov.pl/api/v1/"


def _label(v: dict) -> str:
    parts = [v.get(f"n{i}") for i in range(1, 6) if v.get(f"n{i}")]
    return " / ".join(parts)


class GUSBDL(Connector):
    name = "gus_bdl"

    def search(self, query, limit=10, since=None, fresh=False):
        m = re.fullmatch(r"\s*var:(\d+)(?::(\d{4})-(\d{4}))?\s*", query)
        if not m:
            d = http.get_json(BASE + "variables/search", {"name": query, "format": "json", "page-size": 40, "lang": "pl"},
                              ttl=30 * 86400)
            res = d.get("results", [])
            if not res:
                return SearchResult(0, [], note=f"GUS BDL: no variable matches {query!r}")
            note = "GUS BDL variables (use var:<id>[:YYYY-YYYY]): " + "; ".join(
                f"{v['id']} = {_label(v)} [{v.get('measureUnitName', '')}]" for v in res[:25])
            return SearchResult(0, [], note=note)
        vid, y0, y1 = m.group(1), int(m.group(2) or since or 2010), int(m.group(3) or 2100)
        meta = http.get_json(f"{BASE}variables/{vid}", {"format": "json", "lang": "pl"}, ttl=30 * 86400)
        years = [y for y in range(y0, min(y1, 2100) + 1)][:40]
        d = http.get_json(f"{BASE}data/by-variable/{vid}", {"format": "json", "unit-level": 0, "lang": "pl",
                                                            "year": years}, ttl=self.ttl(fresh))
        rows = (d.get("results") or [{}])[0].get("values", [])
        if not rows:
            return SearchResult(0, [], note=f"GUS BDL: no data for variable {vid} in {y0}-{y1}")
        unit = meta.get("measureUnitName", "")
        label = _label(meta)
        lines = [f"Polska, {r['year']}: {r['val']} {unit}." for r in rows]
        w = Work(title=f"Bank Danych Lokalnych: {label} [{unit}], Polska", authors=["Główny Urząd Statystyczny"],
                 venue="GUS, Bank Danych Lokalnych", year=int(rows[-1]["year"]), kind="dataset", language="pl",
                 abstract=f"{label}. " + " ".join(lines),
                 url=f"https://bdl.stat.gov.pl/bdl/dane/podgrup/zmienna/{vid}")
        w.other_ids["bdl_variable"] = vid
        w.sources = ["gus_bdl"]
        return SearchResult(1, [w])


CONNECTOR = GUSBDL()
