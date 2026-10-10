"""PubMed via NCBI E-utilities. Optional NCBI_API_KEY raises the limit
from 3 to 10 requests/s."""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET

from .. import http
from ..model import Work, person_name
from . import Connector, SearchResult, year_of

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


def _key() -> dict:
    k = os.environ.get("NCBI_API_KEY")
    return {"api_key": k} if k else {}


def _txt(e) -> str:
    return " ".join("".join(e.itertext()).split()) if e is not None else ""


def parse_article(a: ET.Element) -> Work:
    mc = a.find("MedlineCitation")
    art = mc.find("Article")
    w = Work()
    w.pmid = mc.findtext("PMID", "")
    w.title = _txt(art.find("ArticleTitle")).rstrip(".")
    parts = []
    for at in art.findall("Abstract/AbstractText"):
        lab = at.get("Label")
        t = _txt(at)
        parts.append(f"{lab.title()}: {t}" if lab else t)
    w.abstract = " ".join(parts)
    for au in art.findall("AuthorList/Author"):
        ln, fn, cn = au.findtext("LastName"), au.findtext("ForeName"), au.findtext("CollectiveName")
        if ln:
            w.authors.append(person_name(ln, fn or au.findtext("Initials") or ""))
        elif cn:
            w.authors.append(cn)
    j = art.find("Journal")
    w.venue = j.findtext("Title", "") if j is not None else ""
    w.volume = j.findtext("JournalIssue/Volume", "") if j is not None else ""
    w.issue = j.findtext("JournalIssue/Issue", "") if j is not None else ""
    w.pages = art.findtext("Pagination/MedlinePgn", "")
    pd = j.find("JournalIssue/PubDate") if j is not None else None
    y = (pd.findtext("Year") if pd is not None else None) or (pd.findtext("MedlineDate") if pd is not None else None)
    w.year = year_of(y) or year_of(art.findtext("ArticleDate/Year"))
    w.date = "-".join(x for x in [art.findtext("ArticleDate/Year"), art.findtext("ArticleDate/Month"),
                                  art.findtext("ArticleDate/Day")] if x)
    w.language = art.findtext("Language", "")
    w.pub_types = [_txt(p) for p in art.findall("PublicationTypeList/PublicationType")]
    w.mesh = [_txt(d) for d in mc.findall("MeshHeadingList/MeshHeading/DescriptorName")]
    w.keywords = [_txt(k) for k in mc.findall("KeywordList/Keyword")]
    w.coi = _txt(mc.find("CoiStatement"))
    grants = [g.findtext("Agency", "") for g in art.findall("GrantList/Grant")]
    if grants:
        w.funding = "; ".join(dict.fromkeys(g for g in grants if g))
    for e in art.findall("ELocationID"):
        if e.get("EIdType") == "doi":
            w.doi = (e.text or "").strip()
    pdata = a.find("PubmedData")
    if pdata is not None:
        for aid in pdata.findall("ArticleIdList/ArticleId"):
            t, v = aid.get("IdType"), (aid.text or "").strip()
            if t == "doi" and not w.doi:
                w.doi = v
            elif t == "pmc":
                w.pmcid = v
        for ref in pdata.findall("ReferenceList/Reference")[:400]:
            for aid in ref.findall("ArticleIdList/ArticleId"):
                if aid.get("IdType") in ("doi", "pubmed") and aid.text:
                    w.references.append(("doi:" if aid.get("IdType") == "doi" else "pmid:") + aid.text.strip())
                    break
    for cc in mc.findall("CommentsCorrectionsList/CommentsCorrections"):
        rt = cc.get("RefType", "")
        if rt in ("RetractionIn", "ExpressionOfConcernIn", "ErratumIn", "UpdateIn"):
            kind = {"RetractionIn": "retraction", "ExpressionOfConcernIn": "expression_of_concern",
                    "ErratumIn": "correction", "UpdateIn": "update"}[rt]
            w.updates.append({"type": kind, "source": "pubmed", "ref": cc.findtext("RefSource", ""),
                              "pmid": cc.findtext("PMID", "")})
            if kind == "retraction":
                w.is_retracted = True
    if any(p.lower() == "retracted publication" for p in w.pub_types):
        w.is_retracted = True
    w.kind = "journal-article"
    w.url = f"https://pubmed.ncbi.nlm.nih.gov/{w.pmid}/"
    if w.pmcid:
        w.oa_url = f"https://pmc.ncbi.nlm.nih.gov/articles/{w.pmcid}/"
    w.sources = ["pubmed"]
    return w


def fetch(pmids: list[str], ttl: float = 30 * 86400) -> list[Work]:
    out = []
    for i in range(0, len(pmids), 150):
        batch = pmids[i:i + 150]
        xml = http.get_text(BASE + "efetch.fcgi", {"db": "pubmed", "id": ",".join(batch), "retmode": "xml", **_key()},
                            ttl=ttl)
        root = ET.fromstring(xml)
        out += [parse_article(a) for a in root.findall("PubmedArticle")]
    return out


class PubMed(Connector):
    name = "pubmed"

    def search(self, query, limit=50, since=None, fresh=False):
        params = {"db": "pubmed", "term": query, "retmode": "json", "retmax": limit, "sort": "relevance", **_key()}
        if since:
            params.update({"datetype": "pdat", "mindate": f"{since}/01/01", "maxdate": "3000/12/31"})
        d = http.get_json(BASE + "esearch.fcgi", params, ttl=self.ttl(fresh))
        r = d.get("esearchresult", {})
        ids = r.get("idlist", [])
        res = SearchResult(total=int(r.get("count", 0)))
        if ids:
            res.works = fetch(ids)
        tr = r.get("querytranslation")
        if tr:
            res.note = f"PubMed translation: {tr[:300]}"
        return res


CONNECTOR = PubMed()
