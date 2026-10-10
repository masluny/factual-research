"""Evidence weighting, consensus per claim and GRADE-style certainty."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import DESIGNS, Work
from .packs import GRADE_LEVELS, Combined

INDUSTRY_HINTS = ("inc.", "inc ", "ltd", "gmbh", "s.a.", "plc", "llc", "corp", "pharma", "laborator", "nutrition co",
                  "nestl", "danone", "pfizer", "novartis", "roche", "bayer", "dsm", "abbott", "glaxo", "sanofi",
                  "merck", "astrazeneca", "johnson & johnson", "unilever", "pepsico", "coca-cola", "industry")


def industry_funded(w: Work) -> bool:
    text = f"{w.funding} {w.coi}".lower()
    if not text.strip():
        return False
    neg = ("no funding", "no conflict", "none declared", "no competing", "nothing to disclose", "brak konflikt",
           "no relevant", "declare no", "have no", "no financial")
    if any(n in text for n in neg) and not any(h in text for h in ("employee of", "received funding from", "grant from")):
        return False
    return any(h in text for h in INDUSTRY_HINTS)


def flags(w: Work) -> list[str]:
    out = []
    if w.is_retracted:
        out.append("RETRACTED")
    if any(u.get("type") == "expression_of_concern" or "concern" in (u.get("label") or "").lower() for u in w.updates):
        out.append("expression of concern")
    if any(u.get("type") in ("correction", "erratum") for u in w.updates):
        out.append("corrected")
    if w.is_preprint or w.design == "preprint":
        out.append("preprint" + (f" → published as {w.published_version}" if w.published_version else ""))
    if w.design in ("animal", "in_vitro"):
        out.append("not human" if w.design == "animal" else "in vitro")
    if industry_funded(w):
        out.append("industry funding/COI")
    if w.design == "trial_registration":
        st = w.extra.get("trial_status")
        out.append(f"registration only ({st or 'status ?'}{', results posted' if w.extra.get('has_results') else ''})")
    vs = w.venue_signals or {}
    medline = "pubmed" in w.sources and bool(w.mesh)       # MEDLINE-indexed journals are curated
    if vs and not vs.get("listed_in") and vs.get("in_doaj") is False and vs.get("core") is False \
            and vs.get("source_type") != "repository" and not medline and w.design not in (
            "preprint", "dataset", "legislation", "judgment", "trial_registration"):
        out.append("low-visibility venue")
    return out


# A review's own appraisal of its evidence ("GRADE: very low quality of
# evidence", "the available evidence is of poor quality and likely to be
# biased"). Phrases about other people's studies ("we excluded studies at high
# risk of bias") are deliberately not matched.
_EV = r"(?:certainty|quality|confidence)"
# "quality of the studies, however, is poor" / "certainty of evidence was rated as low" / "(certainty of evidence: low)"
_RATED = (rf"\b{_EV}\s+(?:of|in)\s+(?:the\s+)?(?:available\s+|overall\s+|included\s+)?(?:evidence|studies|trials|data)"
          r"(?:\s*,[^,.;]{1,25},)?[\s:(]+(?:(?:was|were|is|are|remains?)\s+)?"
          r"(?:(?:rated|judged|graded|considered|assessed)\s+(?:as\s+)?)?(?:generally\s+|overall\s+|mostly\s+)?")
_GRADE = r"(?-i:\bGRADE\b)[^.;]{0,30}?"          # "GRADE: very low quality of evidence"
# "the body of evidence ... was assessed as being of high quality"
_ASSESSED = r"\bevidence\b[^.;]{0,80}?\b(?:assessed|rated|judged|graded|considered)\s+(?:as\s+)?(?:being\s+)?(?:of\s+)?"
_APPRAISAL = [
    ("very low", rf"\bvery[\s-]+low[\s-]+{_EV}\b|{_RATED}very[\s-]+low\b|{_GRADE}\bvery[\s-]+low\b"
                 r"|\bbardzo\s+nisk\w*\s+(?:jakoś|pewnoś)"),
    ("low", rf"\blow[\s-]+{_EV}(?:\s+of)?\s+(?:the\s+)?evidence\b|{_RATED}(?:low|poor)\b|{_GRADE}\blow\b"
            r"|\bevidence\s+(?:is|was|are|were)\s+(?:of\s+)?(?:low|poor)\b|\blikely\s+to\s+be\s+biased\b"
            r"|(?<!\bno\s)(?<!\bnot\s)\b(?:serious|significant|substantial)\s+(?:risk\s+of\s+)?bias\b"
            r"|\bnisk\w*\s+(?:jakoś|pewnoś)\w*\s+(?:dowod|badań)"
            rf"|{_ASSESSED}(?:low|poor)\s+{_EV}"),
    ("moderate", rf"\bmoderate[\s-]+{_EV}(?:\s+of)?\s+(?:the\s+)?evidence\b|{_RATED}moderate\b|{_GRADE}\bmoderate\b"
                 rf"|{_ASSESSED}moderate\s+{_EV}"),
    ("high", rf"\bhigh[\s-]+{_EV}(?:\s+of)?\s+(?:the\s+)?evidence\b|{_RATED}high\b|{_GRADE}\bhigh\b"
             rf"|{_ASSESSED}high\s+{_EV}"),
]
SYNTHESES = ("sr_ma", "guideline")


def certainty_near(quote: str, abstract: str) -> str | None:
    """Certainty stated in the abstract sentence the quote comes from (Cochrane
    abstracts rate each outcome separately: 'low-certainty evidence')."""
    from .textutil import fold
    q, a = fold(quote or "")[:80], fold(abstract or "")
    at = a.find(q) if q else -1
    if at < 0:
        return None
    start = a.rfind(". ", 0, at) + 1                # the whole sentence: ratings often come first
    end = a.find(". ", at + len(q))                   # ("We found low-certainty evidence that ...")
    return reported_certainty(a[start:(end if end > 0 else at + 600)])


def reported_certainty(text: str) -> str | None:
    """Lowest certainty a source states for its own evidence, if any."""
    from .textutil import normalize
    t = normalize(text or "")         # Cochrane writes "low‐certainty" with U+2010
    for level, pat in _APPRAISAL:
        if re.search(pat, t, re.I):
            return level
    return None


def weight(w: Work, combined: Combined) -> float:
    if w.is_retracted:
        return 0.0
    base = combined.weight(w.design or "other")
    venue = (w.venue or "").lower()
    if any(v.lower() in venue for v in combined.top_venues() if len(v) > 3) or \
            any(venue == v.lower() for v in combined.top_venues()):
        base = min(1.0, base + 0.15)
    if (w.is_preprint and w.design != "preprint"):
        base *= 0.8
    if any(u.get("type") == "expression_of_concern" for u in w.updates):
        base *= 0.5
    return round(base, 3)


@dataclass
class ClaimSummary:
    claim_id: str
    text: str
    n: int = 0
    for_w: float = 0.0
    against_w: float = 0.0
    mixed_w: float = 0.0
    keys_for: list[str] = field(default_factory=list)
    keys_against: list[str] = field(default_factory=list)
    keys_mixed: list[str] = field(default_factory=list)
    designs: list[str] = field(default_factory=list)
    consensus: float | None = None
    label: str = "no evidence"
    certainty: str = "very low"
    reasons: list[str] = field(default_factory=list)
    unverified: int = 0
    effects: list[str] = field(default_factory=list)


def summarize_claim(claim, evidence_rows, works: dict[str, Work], combined: Combined) -> ClaimSummary:
    s = ClaimSummary(claim_id=claim["id"], text=claim["text"])
    seen = set()
    best_start = None
    preprint_only = True
    industry = 0
    quotes: dict[str, list[str]] = {}
    for e in evidence_rows:
        w = works.get(e["key"])
        if not w:
            continue
        if e["stance"] in ("for", "against", "mixed"):
            quotes.setdefault(e["key"], []).append(e["quote"] or "")
        if (e["verified"] or 0) < 0.85:
            s.unverified += 1
        if e["effect"]:
            s.effects.append(f"{e['key']}: {e['effect']}")
        wt = weight(w, combined)
        if e["key"] in seen:
            continue
        seen.add(e["key"])
        if w.is_retracted:
            s.reasons.append(f"{e['key']} is retracted - ignored")
            continue
        s.n += 1
        s.designs.append(w.design)
        st = e["stance"]
        if st == "for":
            s.for_w += wt
            s.keys_for.append(e["key"])
        elif st == "against":
            s.against_w += wt
            s.keys_against.append(e["key"])
        else:
            s.mixed_w += wt
            s.keys_mixed.append(e["key"])
        if st in ("for", "against", "mixed"):
            g = combined.grade_start(w.design)
            if best_start is None or GRADE_LEVELS.index(g) > GRADE_LEVELS.index(best_start):
                best_start = g
        if not (w.is_preprint or w.design == "preprint"):
            preprint_only = False
        if industry_funded(w):
            industry += 1
    total = s.for_w + s.against_w + 0.5 * s.mixed_w
    if s.n == 0 or total == 0:
        s.label, s.certainty = "no evidence", "very low"
        return s
    s.consensus = (s.for_w + 0.25 * s.mixed_w) / total
    c = s.consensus
    if c >= 0.8:
        s.label = "consistent support" if s.n >= 3 else "support (few studies)"
    elif c >= 0.6:
        s.label = "mostly supports"
    elif c > 0.4:
        s.label = "mixed / inconclusive"
    elif c > 0.2:
        s.label = "mostly contradicts"
    else:
        s.label = "contradicted"
    # GRADE-lite
    level = GRADE_LEVELS.index(best_start or "very low")
    # a review that rates its own evidence low caps the claim, whatever the design says
    appraised = {}
    for k in s.keys_for + s.keys_against + s.keys_mixed:
        if works[k].design in SYNTHESES:
            lvl = (reported_certainty(" ".join(quotes.get(k, [])))
                   or next((c for c in (certainty_near(q, works[k].abstract) for q in quotes.get(k, [])) if c), None)
                   or reported_certainty(works[k].abstract))
            if lvl:
                appraised[k] = lvl
    if appraised:
        # the best-rated review sets the ceiling (a review stating high certainty lifts it)
        cap = max(GRADE_LEVELS.index(v) for v in appraised.values())
        if cap < level:
            s.reasons.append("risk of bias: reviews rate their own evidence "
                             + ", ".join(f"{k}: {v}" for k, v in appraised.items()) + f" (−{level - cap})")
            level = cap
    if 0.3 < c < 0.75:
        level -= 1
        s.reasons.append("inconsistency: studies disagree (−1)")
    elif s.keys_against and any(works[k].design in SYNTHESES for k in s.keys_against if k in works):
        level -= 1
        s.reasons.append("inconsistency: a systematic review / guideline disagrees (−1)")
    elif s.keys_mixed and any(works[k].design in SYNTHESES for k in s.keys_mixed if k in works):
        level -= 1
        s.reasons.append("inconsistency: a systematic review reports mixed or non-significant results (−1)")
    if s.n == 1:
        level -= 1
        s.reasons.append("imprecision: single study (−1)")
    elif s.n == 2 and level > GRADE_LEVELS.index("moderate") and not any(
            reported_certainty(" ".join(quotes.get(k, []))) == "high" or reported_certainty(works[k].abstract) == "high"
            for k in s.keys_for + s.keys_against + s.keys_mixed if works[k].design in SYNTHESES):
        level = GRADE_LEVELS.index("moderate")
        s.reasons.append("imprecision: only two sources and none states high certainty (cap moderate)")
    if preprint_only:
        level -= 1
        s.reasons.append("only preprints (−1)")
    if industry and industry >= max(1, s.n // 2 + 1):
        level -= 1
        s.reasons.append("risk of bias: majority industry-funded (−1)")
    if set(s.designs) <= {"animal", "in_vitro"}:
        s.reasons.append("indirectness: no human data")
    s.certainty = GRADE_LEVELS[max(0, level)]
    if best_start:
        s.reasons.insert(0, f"start: {best_start} (best design: {DESIGNS.get(max(s.designs, key=lambda d: combined.weight(d)), '?')})")
    return s


# ---------------------------------------------------------------------------
# main (headline) estimate of a review, as stated in its abstract
# ---------------------------------------------------------------------------

_ABBR = r"(?-i:a?OR|a?RR|a?HR|IRR|SMD|MD|RD)"
_METRIC = (r"(?:(?:adjusted|pooled|summary|overall)\s+)?(?:odds\s+ratio|risk\s+ratio|relative\s+risk|hazard\s+ratio"
           r"|(?:incidence\s+)?rate\s+ratio|(?:standardi[sz]ed\s+)?mean\s+difference|risk\s+difference|" + _ABBR + r")")
_ESTIMATE = re.compile(
    _METRIC + r"\s*(?:[\[(]" + _ABBR + r"[\])])?\s*[=:]?\s*(-?\d+(?:\.\d+)?)[^0-9]{0,40}?"
    r"(?:95\s*%\s*(?:CI|confidence\s+intervals?)(?:\s*\(CI\))?)?[\s:=,\[(]*(-?\d+(?:\.\d+)?)\s*(?:to|-|,)\s*(-?\d+(?:\.\d+)?)",
    re.I)
_RESULTS_LABEL = re.compile(
    r"(?:^|(?<=[.!?)\]]\s)|(?<=\s\s))(?:findings|results|main\s+results|synthesis\s+of\s+results|wyniki)\s*:"
    r"|(?-i:(?:^|(?<=[.!?]\s))(?:FINDINGS|RESULTS|MAIN RESULTS|SYNTHESIS OF RESULTS|Findings|Results|Main results)\s)",
    re.I)


def main_estimate(abstract: str):
    """(label, estimate, lo, hi, context) of the first effect estimate in the
    results/findings part of an abstract, or None. Background sections often
    quote earlier reviews, so text before the results label is ignored."""
    from .textutil import normalize
    t = normalize(abstract or "")
    lab = _RESULTS_LABEL.search(t)
    if not lab:
        return None
    for m in _ESTIMATE.finditer(t, lab.end()):
        est, lo, hi = (float(m.group(i)) for i in (1, 2, 3))
        if lo <= est <= hi:
            label = re.sub(r"\s+", " ", t[m.start():m.start(1)]).strip(" =:([")
            return label, est, lo, hi, t[max(0, m.start() - 60):m.end() + 20]
    return None
