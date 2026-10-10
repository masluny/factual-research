"""The normalised record every connector produces, plus id handling,
deduplication keys, citation keys and study-design inference."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .textutil import normalize, strip_accents

# Study designs / document kinds, shared vocabulary across all packs.
DESIGNS = {
    "guideline": "Practice guideline / consensus statement",
    "sr_ma": "Systematic review / meta-analysis",
    "rct": "Randomised controlled trial",
    "clinical_trial": "Non-randomised clinical trial",
    "cohort": "Cohort study",
    "case_control": "Case-control study",
    "cross_sectional": "Cross-sectional study",
    "observational": "Observational study",
    "case_report": "Case report / series",
    "narrative_review": "Narrative review",
    "animal": "Animal study",
    "in_vitro": "In vitro / cell study",
    "trial_registration": "Trial registration (no results)",
    "standard": "Technical standard",
    "technical_report": "Technical report",
    "journal_article": "Peer-reviewed journal article",
    "conference_paper": "Conference paper",
    "preprint": "Preprint (not peer-reviewed)",
    "book": "Book / chapter",
    "thesis": "Thesis",
    "dataset": "Dataset / statistics",
    "legislation": "Legislation",
    "judgment": "Court judgment",
    "editorial": "Editorial / comment / letter",
    "other": "Other",
}

CAUSAL_OK = {"rct", "sr_ma", "guideline", "legislation", "standard"}


@dataclass
class Work:
    title: str = ""
    authors: list[str] = field(default_factory=list)      # "Family, Given" or "Family G"
    year: int | None = None
    date: str = ""
    venue: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    publisher: str = ""
    doi: str = ""
    pmid: str = ""
    pmcid: str = ""
    arxiv: str = ""
    openalex: str = ""
    s2: str = ""
    nct: str = ""
    eli: str = ""
    other_ids: dict = field(default_factory=dict)
    url: str = ""
    oa_url: str = ""
    pdf_url: str = ""
    abstract: str = ""
    language: str = ""
    kind: str = ""                                        # raw type from the source
    pub_types: list[str] = field(default_factory=list)
    mesh: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    design: str = ""
    design_basis: str = ""
    is_preprint: bool = False
    is_retracted: bool = False
    updates: list[dict] = field(default_factory=list)      # retraction/correction notices
    published_version: str = ""                            # DOI of peer-reviewed version (for preprints)
    cited_by: int | None = None
    venue_signals: dict = field(default_factory=dict)      # medline, doaj, core, h-index...
    funding: str = ""
    coi: str = ""
    references: list[str] = field(default_factory=list)    # ids (doi:/pmid:/openalex:)
    topics: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)       # connectors that returned it
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Work":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    # ---- identifiers -----------------------------------------------------
    def ids(self) -> list[tuple[str, str]]:
        out = []
        for t in ("doi", "pmid", "pmcid", "arxiv", "openalex", "s2", "nct", "eli"):
            v = getattr(self, t)
            if v:
                out.append((t, norm_id(t, v)))
        t = title_key(self.title, self.year)
        if t:
            out.append(("title", t))
        return out


def norm_doi(doi: str) -> str:
    d = (doi or "").strip().lower()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d)
    d = re.sub(r"^doi:\s*", "", d)
    return d.rstrip(".;,")


def norm_id(kind: str, v: str) -> str:
    v = str(v).strip()
    if kind == "doi":
        return norm_doi(v)
    if kind == "pmcid":
        v = v.upper()
        return v if v.startswith("PMC") else "PMC" + v
    if kind == "arxiv":
        v = re.sub(r"^(https?://arxiv\.org/(abs|pdf)/|arxiv:)", "", v, flags=re.I)
        return re.sub(r"v\d+$", "", v.replace(".pdf", ""))
    if kind == "openalex":
        return v.rsplit("/", 1)[-1].upper()
    if kind == "pmid":
        return re.sub(r"\D", "", v)
    return v.lower()


def title_key(title: str, year) -> str:
    t = strip_accents(normalize(title)).lower()
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = " ".join(w for w in t.split() if len(w) > 2)[:120]
    if len(t) < 20:
        return ""
    return t


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

_LIST_FIELDS = ("authors", "pub_types", "mesh", "keywords", "references", "topics", "sources", "updates")


def merge(a: Work, b: Work) -> Work:
    """Merge b into a: keep a's non-empty scalars unless b is richer."""
    display = False
    for f in a.__dataclass_fields__:
        va, vb = getattr(a, f), getattr(b, f)
        if f in _LIST_FIELDS:
            if f == "authors":
                a.authors, display = _merge_authors(a, b)
            elif f == "updates":
                seen = {(u.get("doi"), u.get("type")) for u in va}
                setattr(a, f, va + [u for u in vb if (u.get("doi"), u.get("type")) not in seen])
            else:
                setattr(a, f, list(dict.fromkeys(list(va) + list(vb))))
        elif f in ("other_ids", "venue_signals", "extra"):
            merged = dict(vb)
            merged.update({k: v for k, v in va.items() if v not in (None, "", [], {})})
            setattr(a, f, merged)
        elif f == "abstract":
            if len(vb or "") > len(va or ""):
                a.abstract = vb
        elif f in ("is_retracted", "is_preprint"):
            setattr(a, f, bool(va or vb))
        elif f == "cited_by":
            if vb is not None and (va is None or vb > va):
                a.cited_by = vb
        elif f in ("design", "design_basis"):
            continue
        elif f in ("pdf_url", "oa_url") and va and vb and va != vb:
            alts = a.extra.setdefault("alt_urls", [])
            if vb not in alts:
                alts.append(vb)
        elif not va and vb:
            setattr(a, f, vb)
    if display:
        a.extra["authors_from"] = "display"
    else:
        a.extra.pop("authors_from", None)
    if a.is_preprint and b.kind and b.kind not in ("preprint", "posted-content") and not b.is_preprint:
        a.is_preprint = False
    return a


# ---------------------------------------------------------------------------
# author names: always "Family, Given" (or PubMed "Family GI")
# ---------------------------------------------------------------------------

_PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "di", "da", "dos", "das", "du", "le", "la", "ter",
              "ten", "zu", "bin", "ibn", "al", "el"}
_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
_INITIALS = re.compile(r"[A-Z]{1,3}")


def person_name(family: str, given: str = "") -> str:
    """'Family, Given' from structured fields (Crossref, PubMed, Europe PMC)."""
    family, given = " ".join((family or "").split()), " ".join((given or "").split())
    return f"{family}, {given}" if family and given else family or given


def display_name(name: str) -> str:
    """Best-effort 'Family, Given' from a "Given Family" display name
    (OpenAlex, Semantic Scholar, arXiv...). Names already in 'Family, Given'
    or PubMed 'Family GI' form and organisations are kept. The last word
    (plus particles like 'van der') is taken as the family name, which is
    wrong for some names, so merge() prefers structured sources."""
    n = " ".join((name or "").split())
    if not n or "," in n or is_org(n):
        return n
    parts = n.split()
    if len(parts) > 2 and parts[-1].lower() in _SUFFIXES:
        parts.pop()
    if len(parts) < 2 or _INITIALS.fullmatch(parts[-1]):
        return n
    i = len(parts) - 1
    while i > 1 and parts[i - 1].lower() in _PARTICLES:
        i -= 1
    return f"{' '.join(parts[i:])}, {' '.join(parts[:i])}"


def set_display_authors(w: Work, names) -> None:
    """Fill w.authors from display names and mark them as heuristically split."""
    w.authors = [display_name(n) for n in names if n]
    if w.authors:
        w.extra["authors_from"] = "display"


def _display_style(a: str) -> bool:
    parts = a.split()
    return "," not in a and len(parts) > 1 and not _INITIALS.fullmatch(parts[-1]) and not is_org(a)


def author_rank(w: Work) -> int:
    """2 = names from structured family/given fields, 1 = display names
    (split heuristically, or raw "Given Family" in records stored before
    names were normalised), 0 = no authors."""
    if not w.authors:
        return 0
    if w.extra.get("authors_from") == "display" or any(_display_style(a) for a in w.authors):
        return 1
    return 2


def _tokens(a: str) -> set[str]:
    return set(re.findall(r"[^\W\d_]+", strip_accents(a).lower()))


def _same_person(structured: str, other: str) -> bool:
    """Order-insensitive: 'Xu, Chen' matches the display name 'Xu Chen' even
    after it was split the wrong way round; 'Secades, J. J.' matches
    'Julio J Secades' (same family name, same initials)."""
    ts, to = _tokens(structured), _tokens(other)
    if ts == to:
        return True
    fam = _tokens(family_name(structured))
    if not fam or not fam <= to:
        return False
    ia, ib = {t[0] for t in ts - fam}, {t[0] for t in to - fam}
    return not ia or not ib or ia == ib


def _merge_authors(a: Work, b: Work) -> tuple[list[str], bool]:
    """The longer list wins (no author is lost); on a tie the structured one,
    then the one with fuller names ("Xu, Chen" over "Xu, C"). Display-derived
    names in the result are replaced by the matching structured names from
    the other list. Returns (authors, still_display)."""
    ra, rb = author_rank(a), author_rank(b)
    if (len(b.authors), rb, len("".join(b.authors))) > (len(a.authors), ra, len("".join(a.authors))):
        base, rbase, other, rother = list(b.authors), rb, a.authors, ra
    else:
        base, rbase, other, rother = list(a.authors), ra, b.authors, rb
    if rbase != 1:
        return base, False
    pool = list(other) if rother == 2 else []
    display = False
    for i, n in enumerate(base):
        j = next((j for j, s in enumerate(pool) if _same_person(s, n)), None)
        if j is not None:
            base[i] = pool.pop(j)
        else:
            base[i] = display_name(n)
            display = display or not is_org(n)
    return base, display


# ---------------------------------------------------------------------------
# citation keys
# ---------------------------------------------------------------------------

ORG_WORDS = ("group", "collaboration", "consortium", "committee", "organization", "organisation", "center", "centre",
             "institute", "hospital", "university", "foundation", "agency", "ministry", "council", "society",
             "association", "bank", "sejm", "national", "department", "office", "inc", "ltd", "gmbh")


def is_org(author: str) -> bool:
    a = author.lower()
    return any(re.search(rf"\b{w}", a) for w in ORG_WORDS) or len(a.split()) > 4


def family_name(author: str) -> str:
    a = author.strip()
    if is_org(a) and "," not in a:
        words = [x for x in re.findall(r"[A-Za-zÀ-ž]+", a) if x.lower() not in {"the", "of", "for", "and", "de", "la"}]
        return words[0] if words else a
    if "," in a:
        fam = a.split(",", 1)[0]
    else:
        parts = a.split()
        # "Smith J" / "Smith JR" (PubMed style) vs "John Smith"
        fam = parts[0] if len(parts) > 1 and re.fullmatch(r"[A-Z]{1,3}", parts[-1]) else (parts[-1] if parts else "")
    return fam


def make_key(w: Work, taken: set[str]) -> str:
    if w.kind == "judgment" and w.extra.get("case_numbers"):
        base = "sygn" + re.sub(r"[^a-z0-9]", "", strip_accents(w.extra["case_numbers"].split(",")[0].lower()))
        return _unique(base, taken)
    if w.kind == "legislation" and w.eli:
        return _unique(re.sub(r"[^a-z0-9]", "", w.eli.lower()), taken)
    fam = family_name(w.authors[0]) if w.authors else (w.venue or "anon")
    fam = re.sub(r"[^a-z]", "", strip_accents(fam).lower()) or "anon"
    words_ = [x for x in re.findall(r"[a-z]+", strip_accents(w.title.lower()))
              if len(x) > 3 and x not in {"with", "from", "that", "this", "their", "between", "among", "versus",
                                          "effect", "effects", "study", "analysis", "using", "based", "into"}]
    base = f"{fam[:20]}{w.year or 'nd'}{words_[0] if words_ else ''}"
    return _unique(base, taken)


def _unique(base: str, taken: set[str]) -> str:
    key, n = base, 1
    while key in taken:
        n += 1
        key = f"{base}{chr(ord('a') + n - 2)}" if n < 28 else f"{base}{n}"
    return key


# ---------------------------------------------------------------------------
# design inference
# ---------------------------------------------------------------------------

_PUBTYPE_MAP = [
    ("practice guideline", "guideline"), ("guideline", "guideline"), ("consensus development", "guideline"),
    ("meta-analysis", "sr_ma"), ("systematic review", "sr_ma"), ("network meta-analysis", "sr_ma"),
    ("clinical trial protocol", "trial_registration"), ("study protocol", "trial_registration"),
    ("retraction of publication", "editorial"), ("retracted publication", "other"),
    ("randomized controlled trial", "rct"), ("randomised controlled trial", "rct"),
    ("pragmatic clinical trial", "rct"), ("equivalence trial", "rct"),
    ("controlled clinical trial", "clinical_trial"), ("clinical trial, phase", "clinical_trial"),
    ("clinical trial", "clinical_trial"),
    ("observational study", "observational"), ("cohort", "cohort"), ("case-control", "case_control"),
    ("cross-sectional", "cross_sectional"),
    ("case reports", "case_report"), ("case report", "case_report"),
    ("editorial", "editorial"), ("comment", "editorial"), ("letter", "editorial"), ("news", "editorial"),
    ("review", "narrative_review"),
    ("preprint", "preprint"),
]

_TITLE_RULES = [
    (r"\bumbrella review\b|\bmeta[- ]analys[ie]s\b|\bsystematic (literature )?review\b|\bpooled analysis\b"
     r"|\bmetaanaliz|\bprzegląd systematyczny", "sr_ma"),
    (r"\bguideline|\bconsensus statement\b|\bposition statement\b|\brecommendations? (of|from)\b|\bwytyczn", "guideline"),
    (r"\brandomi[sz]ed\b.*\btrial\b|\brandomi[sz]ed controlled\b|\b(double|single)[- ]blind\b|\bplacebo[- ]controlled\b"
     r"|\brandomizowan", "rct"),
    (r"\bcohort\b|\bprospective (study|analysis)\b|\blongitudinal (study|analysis)\b|\bbadanie kohortowe", "cohort"),
    (r"\bcase[- ]control\b", "case_control"),
    (r"\bcross[- ]sectional\b|\bprzekrojow", "cross_sectional"),
    (r"\bcase report\b|\bcase series\b|\ba case of\b", "case_report"),
    (r"\bin vitro\b|\bcell line|\bcultured cells\b|\bhek293\b|\bhela\b", "in_vitro"),
    (r"\bmice\b|\bmouse\b|\bmurine\b|\brats?\b|\bzebrafish\b|\bin vivo\b(?!.*\bhuman)|\bmyszy|\bszczur", "animal"),
    (r"\bmendelian randomi[sz]ation\b", "observational"),
    (r"\b(narrative|scoping|literature) review\b|\ba review\b|\breview of\b|\boverview\b", "narrative_review"),
]


def infer_design(w: Work) -> tuple[str, str]:
    """Best guess of study design, with the evidence used. Order: curated
    publication types > OpenAlex study designs > MeSH > title heuristics >
    document kind."""
    definitive = {"trial_registration": "trial_registration", "legislation": "legislation", "judgment": "judgment",
                  "dataset": "dataset", "standard": "standard", "dissertation": "thesis", "thesis": "thesis"}
    if (w.kind or "").lower() in definitive:
        return definitive[w.kind.lower()], f"kind:{w.kind}"
    pts = [p.lower() for p in w.pub_types]
    for needle, d in _PUBTYPE_MAP:
        if any(needle in p for p in pts):
            if d == "narrative_review" and any("systematic" in p or "meta" in p for p in pts):
                continue
            # curated types can still hide animal work
            if d in ("clinical_trial", "observational", "narrative_review") and _is_animal(w):
                return "animal", "mesh:Animals (no Humans)"
            return d, f"pubtype:{needle}"
    for sd in w.extra.get("study_designs", []) or []:
        sdl = sd.lower()
        for needle, d in _PUBTYPE_MAP:
            if needle in sdl:
                return d, f"openalex:{sd}"
    if _is_animal(w):
        return "animal", "mesh:Animals (no Humans)"
    if any(m.lower() == "in vitro techniques" for m in w.mesh):
        return "in_vitro", "mesh:In Vitro Techniques"
    title = normalize(w.title).lower()
    for pat, d in _TITLE_RULES:
        if re.search(pat, title):
            return d, "title"
    k = (w.kind or "").lower()
    if w.is_preprint or k in ("preprint", "posted-content"):
        return "preprint", f"kind:{k or 'preprint'}"
    kind_map = {
        "standard": "standard", "report": "technical_report", "technical_report": "technical_report",
        "proceedings-article": "conference_paper", "inproceedings": "conference_paper",
        "conference_paper": "conference_paper", "proceedings": "conference_paper",
        "journal-article": "journal_article", "article": "journal_article", "journal_article": "journal_article",
        "review": "narrative_review", "book": "book", "book-chapter": "book", "monograph": "book",
        "dissertation": "thesis", "thesis": "thesis", "dataset": "dataset", "legislation": "legislation",
        "judgment": "judgment", "trial_registration": "trial_registration", "editorial": "editorial",
        "letter": "editorial", "peer-review": "editorial", "erratum": "editorial", "paratext": "other",
    }
    if k in kind_map:
        return kind_map[k], f"kind:{k}"
    if w.abstract:
        ab = normalize(w.abstract).lower()
        for pat, d in _TITLE_RULES[:6]:
            if re.search(pat, ab[:600]):
                return d, "abstract"
    return ("journal_article" if w.venue else "other"), "default"


def _is_animal(w: Work) -> bool:
    mesh = {m.lower() for m in w.mesh}
    return "animals" in mesh and "humans" not in mesh
