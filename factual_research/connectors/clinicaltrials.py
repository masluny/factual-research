"""ClinicalTrials.gov API v2 - registrations: ongoing / unpublished trials,
useful for spotting publication bias and what is still being tested."""
from __future__ import annotations

from .. import http
from ..model import Work
from . import Connector, SearchResult, year_of


def parse(s: dict) -> Work:
    p = s.get("protocolSection", {})
    idm, st = p.get("identificationModule", {}), p.get("statusModule", {})
    dm, desc = p.get("designModule", {}), p.get("descriptionModule", {})
    sp = p.get("sponsorCollaboratorsModule", {})
    w = Work()
    w.nct = idm.get("nctId", "")
    w.title = idm.get("officialTitle") or idm.get("briefTitle", "")
    w.abstract = desc.get("briefSummary", "")
    lead = (sp.get("leadSponsor") or {}).get("name")
    w.authors = [lead] if lead else []
    w.funding = lead or ""
    start = (st.get("startDateStruct") or {}).get("date", "")
    w.year = year_of(start)
    w.date = start
    w.venue = "ClinicalTrials.gov"
    w.kind = "trial_registration"
    alloc = (dm.get("designInfo") or {}).get("allocation", "")
    enr = (dm.get("enrollmentInfo") or {})
    w.extra.update({
        "trial_status": st.get("overallStatus"), "study_type": dm.get("studyType"), "phases": dm.get("phases"),
        "allocation": alloc, "enrollment": enr.get("count"), "has_results": s.get("hasResults", False),
        "completion": (st.get("completionDateStruct") or {}).get("date"),
        "conditions": (p.get("conditionsModule") or {}).get("conditions", []),
        "interventions": [i.get("name") for i in (p.get("armsInterventionsModule") or {}).get("interventions", [])],
    })
    w.url = f"https://clinicaltrials.gov/study/{w.nct}"
    w.sources = ["clinicaltrials"]
    return w


class ClinicalTrials(Connector):
    name = "clinicaltrials"

    def search(self, query, limit=50, since=None, fresh=False):
        params = {"query.term": query, "pageSize": min(limit, 100), "countTotal": "true"}
        if since:
            params["filter.advanced"] = f"AREA[StartDate]RANGE[{since}-01-01,MAX]"
        d = http.get_json("https://clinicaltrials.gov/api/v2/studies", params, ttl=self.ttl(fresh))
        return SearchResult(total=int(d.get("totalCount", 0) or 0), works=[parse(s) for s in d.get("studies", [])])


CONNECTOR = ClinicalTrials()
