"""Domain packs: which sources to trust for a field, how to rank evidence,
which columns the evidence table has. Packs are TOML files; users can add
their own in ~/.config/factual-research/packs/ or <project>/packs/."""
from __future__ import annotations

import os
import re
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .textutil import normalize, strip_accents

BUILTIN = Path(__file__).parent / "packs"
USER = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "factual-research" / "packs"

GRADE_LEVELS = ["very low", "low", "moderate", "high"]


@dataclass
class Pack:
    name: str
    title: str
    description: str
    keywords: list[str]
    openalex_fields: list[str]
    connectors: list[str]
    optional_connectors: list[str]
    per_query: int
    since: int
    citation_style: str
    question_frame: str
    authorities: list[str]
    order: list[str]
    weight: dict[str, float]
    grade_start: dict[str, str]
    screening: dict
    fields: list[dict]
    causal_allowed: list[str]
    top_venues: list[str] = field(default_factory=list)
    path: str = ""

    @classmethod
    def load(cls, path: Path) -> "Pack":
        d = tomllib.loads(path.read_text(encoding="utf-8"))
        ev = d.get("evidence", {})
        return cls(
            name=d["name"], title=d.get("title", d["name"]), description=d.get("description", ""),
            keywords=d.get("keywords", []), openalex_fields=d.get("openalex_fields", []),
            connectors=d.get("connectors", []), optional_connectors=d.get("optional_connectors", []),
            per_query=int(d.get("per_query", 50)), since=int(d.get("since", 1990)),
            citation_style=d.get("citation_style", "apa"), question_frame=d.get("question_frame", ""),
            authorities=d.get("authorities", {}).get("names", []), order=ev.get("order", []),
            weight=ev.get("weight", {}), grade_start=ev.get("grade_start", {}),
            screening=d.get("screening", {}), fields=d.get("extraction", {}).get("fields", []),
            causal_allowed=d.get("causal", {}).get("allowed", []), top_venues=d.get("top_venues", []),
            path=str(path))


def pack_dirs(project: Path | None = None) -> list[Path]:
    dirs = [BUILTIN, USER]
    if project:
        dirs.append(project / "packs")
    return [d for d in dirs if d.is_dir()]


def all_packs(project: Path | None = None) -> dict[str, Pack]:
    out: dict[str, Pack] = {}
    for d in pack_dirs(project):
        for f in sorted(d.glob("*.toml")):
            try:
                p = Pack.load(f)
                out[p.name] = p   # later dirs override builtin
            except Exception as e:  # noqa: BLE001
                print(f"warning: cannot load pack {f}: {e}")
    return out


def get_packs(names: list[str], project: Path | None = None) -> list[Pack]:
    allp = all_packs(project)
    missing = [n for n in names if n not in allp]
    if missing:
        raise SystemExit(f"unknown pack(s): {', '.join(missing)} - available: {', '.join(sorted(allp))}")
    return [allp[n] for n in names]


class Combined:
    """Several packs used together (e.g. medicine + psychology)."""

    def __init__(self, packs: list[Pack]):
        self.packs = packs or []
        self.names = [p.name for p in self.packs]

    @property
    def primary(self) -> Pack:
        return self.packs[0]

    def connectors(self, optional: bool = False) -> list[str]:
        out = []
        for p in self.packs:
            out += p.connectors + (p.optional_connectors if optional else [])
        return list(dict.fromkeys(out))

    def weight(self, design: str) -> float:
        ws = [p.weight.get(design) for p in self.packs if design in p.weight]
        if ws:
            return max(ws)
        return max((p.weight.get("other", 0.15) for p in self.packs), default=0.15)

    def grade_start(self, design: str) -> str:
        best = None
        for p in self.packs:
            g = p.grade_start.get(design)
            if g and (best is None or GRADE_LEVELS.index(g) > GRADE_LEVELS.index(best)):
                best = g
        if best:
            return best
        w = self.weight(design)
        return "high" if w >= 0.85 else "moderate" if w >= 0.6 else "low" if w >= 0.3 else "very low"

    def causal_allowed(self) -> set[str]:
        s = set()
        for p in self.packs:
            s |= set(p.causal_allowed)
        return s

    def fields(self) -> list[dict]:
        seen, out = set(), []
        for p in self.packs:
            for f in p.fields:
                if f["id"] not in seen:
                    seen.add(f["id"])
                    out.append(f)
        return out

    def top_venues(self) -> list[str]:
        return [v for p in self.packs for v in p.top_venues]

    def screening(self, key: str, default=None):
        for p in self.packs:
            if key in p.screening:
                return p.screening[key]
        return default

    def since(self) -> int:
        return min(p.since for p in self.packs) if self.packs else 1990

    def per_query(self) -> int:
        return max(p.per_query for p in self.packs) if self.packs else 50

    def citation_style(self) -> str:
        return self.primary.citation_style if self.packs else "apa"


def _kw_hit(kw: str, text: str) -> bool:
    kw = strip_accents(kw.lower())
    return re.search(r"(?<![a-z])" + re.escape(kw), text) is not None


def detect(question: str, project: Path | None = None, online: bool = False) -> list[tuple[str, float, list[str]]]:
    """Rank packs for a question. Keyword prefixes (EN+PL) plus, optionally,
    OpenAlex topic fields of the top results for the question."""
    text = strip_accents(normalize(question).lower())
    packs = all_packs(project)
    scores: dict[str, float] = {}
    why: dict[str, list[str]] = {}
    for name, p in packs.items():
        hits = [k for k in p.keywords if _kw_hit(k, text)]
        if hits:
            scores[name] = float(len(hits))
            why[name] = [f"kw:{h}" for h in hits[:6]]
    if online:
        try:
            from .connectors.openalex import topic_fields
            fields = topic_fields(question)
            total = sum(fields.values()) or 1
            for name, p in packs.items():
                share = sum(c for f, c in fields.items() if f in p.openalex_fields) / total
                if share > 0:
                    scores[name] = scores.get(name, 0) + 3 * share
                    why.setdefault(name, []).append(f"openalex:{share:.0%}")
        except Exception as e:  # noqa: BLE001
            why.setdefault("general", []).append(f"openalex lookup failed: {e}")
    ranked = sorted(((n, s, why.get(n, [])) for n, s in scores.items() if n != "general"), key=lambda x: -x[1])
    if not ranked:
        return [("general", 1.0, ["no domain keywords matched"])]
    top = ranked[0][1]
    return [r for r in ranked if r[1] >= 0.5 * top][:3]


def field_counter(fields: list[str]) -> Counter:
    return Counter(fields)
