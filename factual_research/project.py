"""Project scaffolding and the research plan (plan.toml)."""
from __future__ import annotations

import datetime as dt
import json
import tomllib
from pathlib import Path

from . import __version__
from .packs import Combined, detect, get_packs
from .store import DATA_DIRS, Store, find_root
from .textutil import detect_lang, slug

PLAN_TEMPLATE = '''# factual-research research plan: edit freely, then run `factual-research search`.
# Strings in single quotes are literal (handy for PubMed syntax with "double quotes").

[factual_research]
version = 1
created = "{created}"

[question]
text = {question}
lang = "{lang}"                 # language of the final text (en / pl / ...)
packs = {packs}         # domain packs: `factual-research packs` lists them
since = {since}                 # earliest publication year searched
frame = "{frame}"
{frame_fields}
[criteria]
# Screening criteria the agent applies at title/abstract and full-text stage.
include = []
exclude = []
languages = ["en", "pl"]       # records in other languages are auto-excluded (unknown = kept)

# One block per sub-question. `queries` run on every connector of the packs;
# `connector_queries` override that for one source (use its native syntax).
[[subq]]
id = "SQ1"
text = {question}
queries = [{query}]
[subq.connector_queries]
# pubmed = '("Vitamin D"[MeSH] OR cholecalciferol) AND "Respiratory Tract Infections"[MeSH]'
'''

PICO_FIELDS = '''population = ""
intervention = ""
comparator = ""
outcome = ""
'''


def init_project(question: str, directory: Path, packs: list[str] | None = None, online: bool = False,
                 lang: str | None = None) -> tuple[Path, list]:
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    detected = detect(question, directory, online=online)
    names = packs or [d[0] for d in detected]
    combined = Combined(get_packs(names, directory))
    frame = combined.primary.question_frame if combined.packs else ""
    plan = PLAN_TEMPLATE.format(
        created=dt.date.today().isoformat(), question=json.dumps(question, ensure_ascii=False),
        lang=lang or detect_lang(question), packs=json.dumps(names), since=combined.since(), frame=frame,
        frame_fields=PICO_FIELDS if frame.startswith("PICO") or "PICO" in frame else "",
        query=json.dumps(question, ensure_ascii=False))
    pf = directory / "plan.toml"
    if not pf.exists():
        pf.write_text(plan, encoding="utf-8")
    for d in ("sources", "out"):
        (directory / d).mkdir(exist_ok=True)
    st = Store(directory)
    st.set_meta("created_with", __version__)
    return directory, detected


class Plan:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "plan.toml"
        self.d = tomllib.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    @property
    def question(self) -> str:
        return self.d.get("question", {}).get("text", "")

    @property
    def lang(self) -> str:
        return self.d.get("question", {}).get("lang", "en")

    @property
    def pack_names(self) -> list[str]:
        return self.d.get("question", {}).get("packs", ["general"])

    @property
    def since(self) -> int | None:
        return self.d.get("question", {}).get("since")

    @property
    def criteria(self) -> dict:
        return self.d.get("criteria", {})

    @property
    def subqs(self) -> list[dict]:
        return self.d.get("subq", [])

    def combined(self) -> Combined:
        return Combined(get_packs(self.pack_names, self.root))


def open_project(path: str | None = None) -> tuple[Store, Plan]:
    root = Path(path).resolve() if path else find_root()
    if not root or not any((root / d).exists() for d in DATA_DIRS):
        raise SystemExit("No factual-research project here. Run `factual-research init \"<question>\" --dir <folder>` first "
                         "(or cd into a project / pass --project).")
    return Store(root), Plan(root)


def project_slug(question: str) -> str:
    return slug(question, 48)
