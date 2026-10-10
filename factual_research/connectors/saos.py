"""SAOS: System Analizy Orzeczeń Sądowych (Polish court judgments)."""
from __future__ import annotations

from .. import http
from ..model import Work
from . import Connector, SearchResult, strip_tags, year_of


JTYPE = {"SENTENCE": "Wyrok", "DECISION": "Postanowienie", "RESOLUTION": "Uchwała", "REASONS": "Uzasadnienie",
         "REGULATION": "Zarządzenie"}


class SAOS(Connector):
    name = "saos"

    def search(self, query, limit=30, since=None, fresh=False):
        params = {"all": query, "pageSize": min(limit, 100), "sortingField": "JUDGMENT_DATE", "sortingDirection": "DESC"}
        if since:
            params["judgmentDateFrom"] = f"{since}-01-01"
        d = http.get_json("https://www.saos.org.pl/api/search/judgments", params, ttl=self.ttl(fresh), timeout=25,
                          retries=1)
        out = []
        for it in d.get("items", []):
            cases = ", ".join(c.get("caseNumber", "") for c in it.get("courtCases", []))
            court = (it.get("division") or {}).get("court", {}).get("name") or it.get("courtType", "")
            jt = JTYPE.get((it.get("judgmentType") or "").upper(), "Orzeczenie")
            w = Work(title=f"{jt} {court} z dnia {it.get('judgmentDate', '')}, sygn. {cases}",
                     year=year_of(it.get("judgmentDate")), date=it.get("judgmentDate", ""), venue=court,
                     abstract=strip_tags(it.get("textContent", ""))[:3000], kind="judgment", language="pl")
            w.authors = [j.get("name") for j in it.get("judges", []) if j.get("name")][:5]
            w.other_ids["saos"] = str(it.get("id"))
            w.extra["case_numbers"] = cases
            w.url = f"https://www.saos.org.pl/judgments/{it.get('id')}"
            w.sources = ["saos"]
            out.append(w)
        return SearchResult(total=int((d.get("info") or {}).get("totalResults", len(out))), works=out)


CONNECTOR = SAOS()
