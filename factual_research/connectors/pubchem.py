"""PubChem compound facts. Query = a compound name (or CID). Returns one
'dataset' record whose abstract lists the properties, so numbers quoted in
a draft can be verified against it."""
from __future__ import annotations

import urllib.parse

from .. import http
from ..model import Work
from . import Connector, SearchResult

PROPS = ("MolecularFormula,MolecularWeight,IUPACName,XLogP,ExactMass,TPSA,HBondDonorCount,HBondAcceptorCount,"
         "RotatableBondCount,Complexity,Charge")
LABEL = {"MolecularFormula": "Molecular formula", "MolecularWeight": "Molecular weight (g/mol)",
         "IUPACName": "IUPAC name", "XLogP": "XLogP3", "ExactMass": "Exact mass (Da)",
         "TPSA": "Topological polar surface area (Å²)", "HBondDonorCount": "Hydrogen bond donors",
         "HBondAcceptorCount": "Hydrogen bond acceptors", "RotatableBondCount": "Rotatable bonds",
         "Complexity": "Complexity", "Charge": "Formal charge"}


class PubChem(Connector):
    name = "pubchem"

    def search(self, query, limit=5, since=None, fresh=False):
        q = query.strip()
        path = f"cid/{q}" if q.isdigit() else f"name/{urllib.parse.quote(q)}"
        try:
            d = http.get_json(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/{path}/property/{PROPS}/JSON",
                              ttl=30 * 86400, retries=1)
        except http.HTTPError as e:
            if e.status == 404:
                return SearchResult(0, [], note=f"PubChem: no compound named {q!r}")
            raise
        out = []
        for p in d.get("PropertyTable", {}).get("Properties", [])[:limit]:
            cid = p.get("CID")
            lines = [f"{LABEL.get(k, k)}: {v}." for k, v in p.items() if k != "CID"]
            w = Work(title=f"PubChem Compound Summary for CID {cid}, {q}", authors=["National Center for Biotechnology Information"],
                     venue="PubChem", kind="dataset", abstract=" ".join(lines),
                     url=f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}")
            w.other_ids["pubchem_cid"] = str(cid)
            w.extra["properties"] = p
            w.sources = ["pubchem"]
            out.append(w)
        return SearchResult(total=len(out), works=out)


CONNECTOR = PubChem()
