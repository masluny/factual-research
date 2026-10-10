"""Europe PMC: PubMed + PMC + preprints (bioRxiv/medRxiv/Research Square)
and open-access full text as JATS XML."""
from __future__ import annotations

from .. import http
from ..model import Work, display_name, person_name
from . import Connector, SearchResult, strip_tags, year_of

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/"


def parse(r: dict) -> Work:
    w = Work()
    w.title = strip_tags(r.get("title", "")).rstrip(".")
    w.pmid = r.get("pmid", "") or ""
    w.pmcid = r.get("pmcid", "") or ""
    w.doi = r.get("doi", "") or ""
    w.abstract = strip_tags(r.get("abstractText", ""))
    for a in (r.get("authorList") or {}).get("author", []):
        if a.get("lastName"):
            w.authors.append(person_name(a["lastName"], a.get("firstName") or a.get("initials") or ""))
        elif a.get("collectiveName"):
            w.authors.append(a["collectiveName"])
        elif a.get("fullName"):
            w.authors.append(display_name(a["fullName"]))
    ji = r.get("journalInfo") or {}
    w.venue = (ji.get("journal") or {}).get("title", "") or (r.get("bookOrReportDetails") or {}).get("publisher", "")
    w.volume, w.issue = str(ji.get("volume", "") or ""), str(ji.get("issue", "") or "")
    w.pages = r.get("pageInfo", "") or ""
    w.year = year_of(r.get("pubYear"))
    w.date = r.get("firstPublicationDate", "") or ""
    w.language = r.get("language", "") or ""
    w.pub_types = list((r.get("pubTypeList") or {}).get("pubType", []))
    w.mesh = [m.get("descriptorName", "") for m in (r.get("meshHeadingList") or {}).get("meshHeading", [])]
    w.keywords = list((r.get("keywordList") or {}).get("keyword", []))
    w.cited_by = r.get("citedByCount")
    src = r.get("source", "")
    if src == "PPR" or any("preprint" in p.lower() for p in w.pub_types):
        w.is_preprint = True
        w.kind = "preprint"
        w.venue = w.venue or (r.get("bookOrReportDetails") or {}).get("publisher", "") or "preprint server"
    else:
        w.kind = "journal-article"
    for u in (r.get("fullTextUrlList") or {}).get("fullTextUrl", []):
        if u.get("availabilityCode") in ("OA", "F") and u.get("documentStyle") == "pdf" and not w.pdf_url:
            w.pdf_url = u.get("url", "")
        if u.get("availabilityCode") in ("OA", "F") and not w.oa_url:
            w.oa_url = u.get("url", "")
    if r.get("isOpenAccess") == "Y":
        w.venue_signals["open_access"] = True
    w.url = f"https://europepmc.org/article/{src}/{r.get('id')}" if src else ""
    w.extra["epmc_id"] = f"{src}:{r.get('id')}"
    w.sources = ["europepmc"]
    return w


class EuropePMC(Connector):
    name = "europepmc"

    def search(self, query, limit=50, since=None, fresh=False):
        q = f"({query})" + (f" AND FIRST_PDATE:[{since}-01-01 TO 3000-12-31]" if since else "")
        d = http.get_json(BASE + "search", {"query": q, "format": "json", "resultType": "core",
                                            "pageSize": min(limit, 1000)}, ttl=self.ttl(fresh))
        return SearchResult(total=int(d.get("hitCount", 0)),
                            works=[parse(r) for r in d.get("resultList", {}).get("result", [])])


def fulltext_xml(pmcid: str) -> str | None:
    try:
        st, ct, body = http.request(f"{BASE}{pmcid}/fullTextXML", ttl=90 * 86400)
    except http.HTTPError:
        return None
    text = body.decode("utf-8", "replace")
    return text if "<article" in text else None


CONNECTOR = EuropePMC()
