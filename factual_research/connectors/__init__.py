"""Source connectors. Each module exposes a `Connector` subclass instance
named CONNECTOR. Import is lazy so a broken/slow source never blocks others."""
from __future__ import annotations

import html
import importlib
import os
import re
from dataclasses import dataclass, field

from ..model import Work

REGISTRY = {
    # name: (module, title, kind)
    "pubmed": ("pubmed", "PubMed / MEDLINE (NCBI E-utilities)", "literature"),
    "europepmc": ("europepmc", "Europe PMC (incl. preprints, PMC full text)", "literature"),
    "openalex": ("openalex", "OpenAlex (all disciplines)", "literature"),
    "crossref": ("crossref", "Crossref (DOI registry, retractions)", "literature"),
    "semanticscholar": ("semanticscholar", "Semantic Scholar", "literature"),
    "arxiv": ("arxiv", "arXiv preprints", "literature"),
    "clinicaltrials": ("clinicaltrials", "ClinicalTrials.gov registry", "registry"),
    "dblp": ("dblp", "dblp computer science bibliography", "literature"),
    "inspire": ("inspire", "INSPIRE-HEP (high-energy physics)", "literature"),
    "nasa_ads": ("nasa_ads", "NASA ADS (astronomy; ADS_API_TOKEN)", "literature"),
    "ntrs": ("ntrs", "NASA Technical Reports Server", "literature"),
    "ieee": ("ieee", "IEEE Xplore (IEEE_API_KEY)", "literature"),
    "core": ("core", "CORE open-access aggregator (CORE_API_KEY)", "literature"),
    "sejm_eli": ("sejm_eli", "ISAP / Sejm ELI API (Polish legislation)", "law"),
    "saos": ("saos", "SAOS (Polish court judgments)", "law"),
    "pubchem": ("pubchem", "PubChem compound facts", "facts"),
    "worldbank": ("worldbank", "World Bank indicators", "data"),
    "gus_bdl": ("gus_bdl", "GUS Bank Danych Lokalnych (Polish statistics)", "data"),
}


@dataclass
class SearchResult:
    total: int = 0
    works: list[Work] = field(default_factory=list)
    note: str = ""


class Connector:
    name = ""
    env_key: str | None = None      # env var that must be set, if any

    def available(self) -> tuple[bool, str]:
        if self.env_key and not os.environ.get(self.env_key):
            return False, f"set {self.env_key} to enable {self.name}"
        return True, ""

    def search(self, query: str, limit: int = 50, since: int | None = None, fresh: bool = False) -> SearchResult:
        raise NotImplementedError

    def ttl(self, fresh: bool) -> float:
        return 0 if fresh else 3 * 86400


_loaded: dict[str, Connector] = {}


def get(name: str) -> Connector:
    if name not in REGISTRY:
        raise KeyError(f"unknown connector {name!r}; known: {', '.join(REGISTRY)}")
    if name not in _loaded:
        mod = importlib.import_module(f".{REGISTRY[name][0]}", __name__)
        _loaded[name] = mod.CONNECTOR
    return _loaded[name]


# ---- shared helpers --------------------------------------------------------

def strip_tags(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<(jats:)?(title|sec)[^>]*>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def year_of(s) -> int | None:
    if s is None:
        return None
    m = re.search(r"(19|20)\d{2}", str(s))
    return int(m.group(0)) if m else None
