"""The scorer. Grades a markdown draft against the project's sources and
returns a 0–100 score plus ranked, actionable notes (same loop idea as
write-as-me: write → verify → fix → repeat).

Checks
  integrity    every [@key] exists, is screened in, not retracted/excluded
  quotes       anchor quotes appear verbatim (fuzzy ≥ 0.9) at the cited place
  support      each cited sentence is backed by a passage of the cited source
  numbers      every number in a sentence occurs in its cited source(s)
  coverage     factual sentences / numbers without a citation
  calibration  causal or absolute wording backed only by weak designs,
               animal / in-vitro results stated as if about humans
  balance      contrary evidence in the matrix is mentioned
  scope        every sub-question of the plan is addressed
  tables       numbers in markdown tables trace to the row's sources
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .citations import TABLE_TAG, parse_cites, quote_location, strip_cites
from .evidence import main_estimate, summarize_claim
from .model import Work
from .project import Plan
from .store import EXCLUDED, Store
from .textutil import (detect_lang, extract_numbers, find_quote, is_significant, normalize, number_in,
                       number_index, overlap, snippet, split_sentences)

CAUSAL = re.compile(
    r"\b(causes?|caused|causing|leads? to|results? in|prevents?|prevented|cures?|cured|protects? against|"
    r"eliminates?|reverses?|boosts?|improves?|reduces?|increases?|decreases?|lowers?|raises?|"
    r"powoduj\w*|spowodowa\w*|prowadzi do|zapobieg\w*|lecz[yąy]\w*|chroni\w* przed|zmniejsz\w*|zwiększ\w*|"
    r"obniż\w*|podnos\w*|popraw\w*|redukuj\w*|wywołuj\w*)\b", re.I)
ABSOLUTE = re.compile(
    r"\b(proves?|proven|definitely|undeniabl\w*|undoubtedly|always|never|guarantee\w*|conclusively|"
    r"there is no doubt|beyond doubt|udowodni\w*|niezaprzeczaln\w*|bez wątpienia|zawsze|nigdy|gwarantuj\w*|"
    r"niewątpliwie|ponad wszelką wątpliwość)\b", re.I)
HEDGED = re.compile(r"\b(associated|association|linked|correlat\w*|may|might|could|suggest\w*|appears?|"
                    r"wiąż\w*|związ\w*|skorel\w*|może|mogą|sugeruj\w*|wydaje się|prawdopodobn\w*)\b", re.I)
ANIMAL_WORDS = re.compile(r"\b(mice|mouse|rats?|animals?|murine|rodents?|in vitro|cells?|cell lines?|zebrafish|"
                          r"myszy|myszach|szczur\w*|zwierz\w*|komór\w*|in vivo)\b", re.I)
FACTUAL = re.compile(
    r"\b(stud(y|ies)|trials?|evidence|meta-analys\w*|research|found|showed|shown|demonstrat\w*|reported|"
    r"associated|risk|rates?|effect|significant\w*|increase\w*|decrease\w*|reduc\w*|higher|lower|more likely|"
    r"less likely|prevalence|incidence|percent|badani\w*|metaanaliz\w*|wykaza\w*|stwierdz\w*|wyniki|ryzyk\w*|"
    r"skuteczn\w*|częstoś\w*|odsetek|istotn\w*|zwiększ\w*|zmniejsz\w*|wyższ\w*|niższ\w*)\b", re.I)
CONCLUSION_HEAD = re.compile(r"(conclusion|summary|bottom line|take[- ]home|implications|discussion of limits|"
                             r"wnioski|podsumowanie|konkluzj|zakończenie)", re.I)
REF_HEAD = re.compile(r"^#+\s*(references|bibliography|works cited|sources|bibliografia|literatura|źródła|przypisy)\b", re.I)

SEV_ORDER = {"error": 0, "warn": 1, "note": 2}


@dataclass
class Issue:
    severity: str
    check: str
    message: str
    line: int = 0
    text: str = ""
    fix: str = ""
    keys: list[str] = field(default_factory=list)


@dataclass
class Sentence:
    text: str
    line: int
    section: str
    paragraph: int
    in_table: bool = False
    row_cells: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# markdown → sentences
# ---------------------------------------------------------------------------

def _clean_md(s: str) -> str:
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"(?<!\[)\[([^\[\]@]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"[*_`]{1,3}", "", s)
    return s.strip()


def parse_markdown(text: str) -> tuple[list[Sentence], list[str]]:
    text = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                lines = [""] * (i + 1) + lines[i + 1:]
                break
    out: list[Sentence] = []
    headings: list[str] = []
    section, in_code, in_refs, para_no = "", False, False, 0
    buf: list[tuple[int, str]] = []

    def flush():
        nonlocal buf, para_no
        if not buf:
            return
        para_no += 1
        start = buf[0][0]
        joined = " ".join(t for _, t in buf)
        for s in split_sentences(joined):
            # map back to a line number
            probe = s[:25]
            ln = start
            for (n, t) in buf:
                if probe[:12] and probe[:12] in t:
                    ln = n
                    break
            out.append(Sentence(_clean_md(s), ln, section, para_no))
        buf = []

    i = 0
    while i < len(lines):
        raw = lines[i]
        ln = i + 1
        s = raw.strip()
        if s.startswith("```") or s.startswith("~~~"):
            flush()
            in_code = not in_code
            i += 1
            continue
        if in_code:
            i += 1
            continue
        if s.startswith("#"):
            flush()
            section = s.lstrip("#").strip()
            headings.append(section)
            in_refs = bool(REF_HEAD.match(s))
            i += 1
            continue
        if in_refs:
            i += 1
            continue
        if s.startswith("|"):
            flush()
            para_no += 1
            header = None
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = lines[i].strip()
                cells = [c.strip() for c in row.strip("|").split("|")]
                if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    i += 1
                    continue
                if header is None:
                    header = cells
                else:
                    out.append(Sentence(_clean_md(" | ".join(cells)), i + 1, section, para_no, True, cells))
                i += 1
            continue
        if not s:
            flush()
            i += 1
            continue
        if re.match(r"^([-*+]|\d+[.)])\s+", s):
            flush()
            s = re.sub(r"^([-*+]|\d+[.)])\s+", "", s)
        s = s.lstrip("> ").strip()
        buf.append((ln, s))
        i += 1
    flush()
    return out, headings


# ---------------------------------------------------------------------------
# source access with caching
# ---------------------------------------------------------------------------

class Sources:
    def __init__(self, st: Store):
        self.st = st
        self._text: dict[str, tuple[str, str, list[dict]]] = {}
        self._nums: dict[str, tuple[set, list]] = {}
        self._lang: dict[str, str] = {}

    def text(self, key):
        if key not in self._text:
            self._text[key] = self.st.source_text(key)
        return self._text[key]

    def depth(self, key) -> str:
        return self.text(key)[0]

    def nums(self, key):
        if key not in self._nums:
            idx = number_index(self.text(key)[1])
            self._nums[key] = ({v for v, _ in idx}, idx)
        return self._nums[key]

    def lang(self, key) -> str:
        if key not in self._lang:
            w = self.st.get(key)
            l = (w.language or "")[:2].lower() if w else ""
            l = {"en": "en", "pl": "pl", "po": "pl"}.get(l, "")
            self._lang[key] = l or detect_lang(self.text(key)[1][:5000])
        return self._lang[key]

    def best_passage(self, key: str, sentence: str, locator: str = "") -> tuple[float, str, str]:
        depth, full, pages = self.text(key)
        cands = []
        for r in self.st.search_chunks(sentence, keys=[key], limit=12):
            cands.append((r["loc"], r["text"]))
        if not cands:
            cands = [(p["loc"], p["text"][:2000]) for p in pages[:3]]
        best = (0.0, "", "")
        for loc, t in cands:
            sc = overlap(sentence, t)
            if sc > best[0]:
                best = (sc, loc, t)
        return best


_sem_model = None


def semantic_score(a: str, b: str) -> float | None:
    global _sem_model
    try:
        if _sem_model is None:
            from sentence_transformers import SentenceTransformer
            _sem_model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        ea, eb = _sem_model.encode([a, b], normalize_embeddings=True)
        return float((ea * eb).sum())
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def verify(st: Store, plan: Plan, draft: Path, target: float = 80, semantic: bool = False,
           style_scorer: str | None = None) -> dict:
    raw = Path(draft).read_text(encoding="utf-8")
    sentences, headings = parse_markdown(raw)
    combined = plan.combined()
    causal_ok = combined.causal_allowed()
    src = Sources(st)
    draft_lang = detect_lang(strip_cites(raw)) or plan.lang
    issues: list[Issue] = []
    hard = 0
    m = {"sentences": 0, "cited_sentences": 0, "factual_uncited": 0, "factual_sentences": 0,
         "numbers_checked": 0, "numbers_ok": 0.0, "quotes": 0, "quotes_ok": 0, "support_sum": 0.0,
         "overclaims": 0, "cited_keys": set(), "depth": {}}
    key_cache: dict[str, str | None] = {}
    works: dict[str, Work] = {}

    def resolve(k: str) -> str | None:
        if k not in key_cache:
            key_cache[k] = st.resolve(k)
        return key_cache[k]

    flagged_keys: set[str] = set()
    per_sentence: list[dict] = []
    key_text: dict[str, list[tuple[int, str]]] = {}     # sentences citing each key
    for s in sentences:
        if not s.text or len(s.text) < 3:
            continue
        m["sentences"] += 1
        cites = parse_cites(s.text)
        plain = normalize(strip_cites(s.text))
        short = plain[:160]
        keys: list[str] = []
        anchored_q: list[tuple[str, str, float]] = []
        # ---- integrity -----------------------------------------------------
        if re.search(r"\[-?@", plain):
            hard += 1
            issues.append(Issue("error", "integrity", "Malformed citation: `render` would leave it as raw text.",
                                s.line, short, 'Write [@key, loc :: "quote"]; close the quote and keep any '
                                "[brackets] inside it balanced.", []))
        for c in cites:
            k = resolve(c.key)
            if not k:
                hard += 1
                issues.append(Issue("error", "integrity", f"Unknown source @{c.key} - not in the project library "
                                    "(possible fabricated citation).", s.line, short,
                                    "Cite only keys from `factual-research screen list --status included`, or add the "
                                    "source with `factual-research add --doi ...` first.", [c.key]))
                continue
            keys.append(k)
            w = works.setdefault(k, st.get(k))
            status = st.status(k)
            if k not in flagged_keys:
                if w.is_retracted:
                    hard += 1
                    flagged_keys.add(k)
                    issues.append(Issue("error", "integrity", f"@{k} is RETRACTED.", s.line, short,
                                        "Remove it; cite only if discussing the retraction itself.", [k]))
                elif status in EXCLUDED:
                    hard += 1
                    flagged_keys.add(k)
                    reason = (st.decisions(k)[-1]["reason"] if st.decisions(k) else "")
                    issues.append(Issue("error", "integrity", f"@{k} was excluded during screening ({reason}).",
                                        s.line, short, "Use an included source or re-screen it with a reason.", [k]))
                elif status in ("new", "maybe"):
                    flagged_keys.add(k)
                    issues.append(Issue("warn", "integrity", f"@{k} is cited but was never screened in.", s.line, short,
                                        f"Run `factual-research screen include {k} --reason ...` if it meets the criteria.", [k]))
                if (w.is_preprint or w.design == "preprint") and w.published_version:
                    pk = st.key_for_id("doi", w.published_version)
                    flagged_keys.add(k)
                    issues.append(Issue("warn", "integrity", f"@{k} is a preprint; a peer-reviewed version exists"
                                        + (f" (@{pk})." if pk else f" (doi:{w.published_version})."), s.line, short,
                                        f"Cite @{pk} instead." if pk else "Add the published version and cite it.", [k]))
            m["cited_keys"].add(k)
            key_text.setdefault(k, []).append((s.line, plain))
            # ---- anchor quote ---------------------------------------------
            if c.quote:
                m["quotes"] += 1
                depth, full, pages = src.text(k)
                sim, at = find_quote(c.quote, full)
                if sim >= 0.9 and len(c.quote.split()) < 5:
                    m["quotes_ok"] += 0.5
                    issues.append(Issue("warn", "quotes", f"Anchor quote for @{k} is too short to prove the claim.",
                                        s.line, f'"{c.quote}"', "Quote the full supporting sentence.", [k]))
                elif sim >= 0.9:
                    m["quotes_ok"] += 1
                    anchored_q.append((k, c.quote, sim))
                    if c.locator:
                        ok, found = quote_location(c.quote, c.locator, pages, w.abstract)
                        if ok is False:
                            issues.append(Issue("warn", "quotes", f"Quote for @{k} found, but at {found or '?'}, not "
                                                f"{c.locator}.", s.line, short,
                                                f"Change the locator to `{found}`." if found else "Fix the locator.", [k]))
                else:
                    sev = "error" if depth == "fulltext" else "warn"
                    near = snippet(full, at) if at >= 0 else ""
                    issues.append(Issue(sev, "quotes", f"Anchor quote not found in @{k} ({depth}; best match "
                                        f"{sim:.0%}).", s.line, f'"{c.quote[:120]}"',
                                        "Copy the words verbatim from `factual-research find` output"
                                        + (f" - closest: {near[:150]}" if near else "")
                                        + ("" if depth == "fulltext" else " (only the abstract is available; add the PDF to sources/)"),
                                        [k]))
        # ---- table rows ------------------------------------------------------
        if s.in_table:
            nums = [n for cell in s.row_cells for n in extract_numbers(strip_cites(cell)) if is_significant(n)]
            if nums and not keys:
                issues.append(Issue("warn", "tables", "Table row with numbers but no citation.", s.line, short,
                                    "Add [@key] in the row (e.g. in the Study column).", []))
            for n in nums:
                m["numbers_checked"] += 1
                res = _check_number(n, keys, src)
                if res in ("exact", "rounded"):
                    m["numbers_ok"] += 1
                elif keys:
                    issues.append(_num_issue(n, keys, src, s, short))
            continue
        # ---- support ---------------------------------------------------------
        has_factual = bool(FACTUAL.search(plain)) or any(is_significant(n) for n in extract_numbers(plain))
        if keys:
            m["cited_sentences"] += 1
            sup, how = 0.0, ""
            cross = any(src.lang(k) and src.lang(k) != draft_lang for k in keys)
            if anchored_q:
                if cross:
                    sup, how = 1.0, "anchored (cross-language)"
                else:
                    sup = max(max(overlap(plain, q) for _, q, _ in anchored_q) * 1.6, 0.75)
                    sup = min(1.0, sup)
                    how = "anchored"
            else:
                best = (0.0, "", "", "")
                for k in keys:
                    sc, loc, txt = src.best_passage(k, plain)
                    if sc > best[0]:
                        best = (sc, loc, txt, k)
                sc = best[0]
                if semantic and best[2]:
                    sem = semantic_score(plain, best[2])
                    if sem is not None:
                        sc = max(sc, (sem - 0.35) / 0.4)
                if cross and sc < 0.5:
                    sup, how = 0.5, "cross-language"
                    issues.append(Issue("warn", "support", "Unverifiable: sentence and source are in different "
                                        "languages and there is no anchor quote.", s.line, short,
                                        "Add an anchor quote in the source language: [@key, p. N :: \"exact source words\"] "
                                        "(or run with --semantic).", keys))
                elif sc >= 0.5:
                    sup, how = 1.0, "supported"
                elif sc >= 0.3:
                    sup, how = 0.5, "weak"
                    issues.append(Issue("warn", "support", f"Weak support in cited source(s) (overlap {sc:.0%}); "
                                        f"closest passage in @{best[3]} {best[1]}.", s.line, short,
                                        f"Check the claim; closest text: \"{best[2][:200]}\" - rephrase closer to it "
                                        "or add an anchor quote.", keys))
                else:
                    deep = any(src.depth(k) == "fulltext" for k in keys)
                    sup, how = (0.0 if deep else 0.4), "unsupported"
                    issues.append(Issue("error" if deep else "warn", "support",
                                        "Claim not found in the cited source(s)" + ("" if deep else
                                        " (only abstracts available)") + ".", s.line, short,
                                        "Find the supporting passage with `factual-research find \"...\" --keys "
                                        + ",".join(keys) + "`; cite the right source or soften the claim."
                                        + ("" if deep else " Add the PDF to sources/ and run `factual-research fetch`."),
                                        keys))
            m["support_sum"] += sup
            per_sentence.append({"line": s.line, "support": how, "score": round(sup, 2), "keys": keys})
        # ---- numbers ---------------------------------------------------------
        nums = [n for n in extract_numbers(plain) if is_significant(n)]
        for n in nums:
            m["numbers_checked"] += 1
            if not keys:
                issues.append(Issue("error", "numbers", f"Number {n.raw} has no citation.", s.line, short,
                                    "Cite the source the number comes from.", []))
                continue
            res = _check_number(n, keys, src)
            if res == "exact":
                m["numbers_ok"] += 1
            elif res == "rounded":
                m["numbers_ok"] += 1
                issues.append(Issue("note", "numbers", f"{n.raw} is a rounded value of the source number.", s.line,
                                    short, "Fine if intended; keep the precision used in the source for effect sizes.", keys))
            else:
                deep = any(src.depth(k) == "fulltext" for k in keys)
                m["numbers_ok"] += 0 if deep else 0.4
                issues.append(_num_issue(n, keys, src, s, short))
        # ---- coverage --------------------------------------------------------
        if has_factual and not s.text.rstrip().endswith("?"):
            m["factual_sentences"] += 1
            if not keys and CONCLUSION_HEAD.search(s.section or "") and not nums:
                m["factual_sentences"] -= 1   # author's own synthesis in a conclusion section
            elif not keys:
                m["factual_uncited"] += 1
                if not nums:
                    issues.append(Issue("warn", "coverage", "Factual-sounding sentence without a citation.", s.line,
                                        short, "Cite a source, or mark it clearly as your own conclusion.", []))
        # ---- calibration -----------------------------------------------------
        if keys:
            designs = {works.setdefault(k, st.get(k)).design for k in keys}
            causal = CAUSAL.search(plain)
            hedged = HEDGED.search(plain)
            if causal and not hedged and not (designs & causal_ok):
                m["overclaims"] += 1
                issues.append(Issue("warn", "calibration", f"Causal wording (\"{causal.group(0)}\") backed only by "
                                    f"{', '.join(sorted(designs))}.", s.line, short,
                                    "Use associational wording (\"is associated with\" / \"wiąże się z\") or cite "
                                    "an RCT / systematic review.", keys))
            ab = ABSOLUTE.search(plain)
            if ab and not (designs & {"guideline", "legislation", "standard"}):
                m["overclaims"] += 1
                issues.append(Issue("warn", "calibration", f"Absolute wording (\"{ab.group(0)}\") - science rarely "
                                    "supports certainty.", s.line, short, "Soften: \"strong evidence suggests…\".", keys))
            if designs and designs <= {"animal", "in_vitro"} and not ANIMAL_WORDS.search(plain):
                m["overclaims"] += 1
                issues.append(Issue("warn", "calibration", "Animal / in-vitro evidence stated as a general (human) "
                                    "fact.", s.line, short, "Say so explicitly (\"in mice…\") or cite human studies.", keys))
    # ---- balance & scope (matrix-level) -------------------------------------
    cited = m["cited_keys"]
    claims = st.claims()
    all_works = {k: w for k, _, w in st.works()}
    unbalanced = 0
    for c in claims:
        summ = summarize_claim(c, st.evidence(c["id"]), all_works, combined)
        if set(summ.keys_for) & cited and summ.keys_against and not (set(summ.keys_against) & cited):
            unbalanced += 1
            issues.append(Issue("warn", "balance", f"Claim {c['id']} (\"{c['text'][:80]}\") has contrary evidence that "
                                "the draft never mentions.", 0, "", "Discuss: " + ", ".join("@" + k for k in summ.keys_against[:5]),
                                summ.keys_against))
        newer = [k for k in summ.keys_for + summ.keys_against + summ.keys_mixed
                 if all_works[k].design in ("sr_ma", "guideline") and k not in cited]
        old_cited = [k for k in (set(summ.keys_for) & cited) if all_works[k].year and
                     any(all_works[n].year and all_works[n].year > all_works[k].year + 2 for n in newer)]
        if old_cited and newer:
            issues.append(Issue("note", "freshness", f"Claim {c['id']}: newer synthesis available but not cited.", 0, "",
                                "Consider citing " + ", ".join("@" + k for k in newer[:3]), newer))
    uncovered = 0
    for sq in plan.subqs:
        sid = sq.get("id")
        rel = {e["key"] for c in claims if c["subq"] == sid for e in st.evidence(c["id"])}
        if not rel:
            rel = {r[0] for r in st.db.execute(
                "SELECT DISTINCT h.key FROM hits h JOIN queries q ON q.id=h.query_id JOIN works w ON w.key=h.key "
                "WHERE q.subq=? AND w.status IN ('ta_included','ft_included')", (sid,))}
        if rel and not (rel & cited):
            uncovered += 1
            issues.append(Issue("warn", "scope", f"{sid} (\"{sq.get('text', '')[:80]}\") is not addressed - none of its "
                                "sources are cited.", 0, "", "Add a paragraph answering it, citing e.g. "
                                + ", ".join("@" + k for k in sorted(rel)[:4]), sorted(rel)[:6]))
    # ---- main result of cited reviews ------------------------------------------------
    # A review cited with numbers must be represented by its main pooled estimate,
    # not only by a subgroup, sensitivity or fixed-effects result.
    missing_main = checked_main = 0
    for k, sents in key_text.items():
        w = works.get(k) or st.get(k)
        if not w or w.design != "sr_ma":
            continue
        reported = [n for _, t in sents for n in extract_numbers(t) if n.decimals and not n.pct]
        head = main_estimate(w.abstract) if reported else None
        if not head:
            continue
        checked_main += 1
        label, est, lo, hi, ctx = head
        if any(abs(v - est) < 0.0051 for n in reported for v in n.values):
            continue
        missing_main += 1
        issues.append(Issue("warn", "primary", f"@{k}: the draft reports other estimates from this review but not its "
                            f"main result ({label} {est:g}, 95% CI {lo:g} to {hi:g}).", sents[0][0], ctx[:170],
                            "State the review's overall/primary estimate (from its abstract) before any subgroup, "
                            "sensitivity or fixed-effects result.", [k]))
    # ---- generated tables referenced by placeholders ---------------------------
    for pm in re.finditer(TABLE_TAG, raw):
        spec = pm.group(1).split()
        line = raw[:pm.start()].count("\n") + 1
        try:
            if spec[0] == "custom" and len(spec) > 1:
                from .tables import custom_table
                tpath = st.root / spec[1]
                _, probs = custom_table(st, json.loads(tpath.read_text(encoding="utf-8")))
                for pr in probs:
                    issues.append(Issue("error" if "not found" in pr else "warn", "tables", f"{spec[1]}: {pr}", line, "",
                                        "Fix the cell value/quote in the table spec.", []))
                m["cited_keys"].update(k for k in (st.resolve(r.get("key", "")) for r in json.loads(
                    tpath.read_text(encoding="utf-8")).get("rows", [])) if k)
            elif spec[0] == "extraction":
                bad = [c for c in st.cells() if (c["verified"] or 0) < 0.85 and
                       (len(spec) < 2 or c["field"] in spec[1].split(","))]
                for c in bad:
                    issues.append(Issue("warn", "tables", f"evidence table cell @{c['key']}.{c['field']} failed "
                                        "verification.", line, c["value"][:120],
                                        "Fix it with `factual-research extract set` (verbatim quote) or remove the field.", [c["key"]]))
                fields = spec[1].split(",") if len(spec) > 1 else None
                m["cited_keys"].update({c["key"] for c in st.cells() if not fields or c["field"] in fields})
            elif spec[0] not in ("sources", "sof", "matrix", "forest"):
                issues.append(Issue("warn", "tables", f"unknown table placeholder `{pm.group(0)}`", line, "",
                                    "Use: extraction [fields] | sources | sof | matrix | forest | custom <file.json>", []))
        except FileNotFoundError:
            issues.append(Issue("error", "tables", f"table spec {spec[1]} not found", line, "", "", []))
        except Exception as ex:  # noqa: BLE001
            issues.append(Issue("warn", "tables", f"table {spec}: {ex}", line, "", "", []))
    # ---- verified evidence table ------------------------------------------------------
    table_tags = [pm.group(1).split()[0] for pm in re.finditer(TABLE_TAG, raw)]
    research = [k for k in cited if (works.get(k) or st.get(k)) and (works.get(k) or st.get(k)).design not in
                ("dataset", "legislation", "judgment", "standard", "trial_registration")]
    table_score = None
    if len(research) >= 3 and combined.fields():
        verified_rows = {c["key"] for c in st.cells() if (c["verified"] or 0) >= 0.85}
        if "extraction" not in table_tags or not verified_rows:
            table_score = 0.0
            issues.append(Issue("warn", "tables", "No verified evidence table in the draft (a hand-made table is not "
                                "checked cell by cell).", 0, "",
                                "Run `factual-research extract template --out extraction.json`, fill value + verbatim "
                                "quote per cell, `factual-research extract import extraction.json`, then put "
                                "`<!-- fr:table extraction -->` in the draft.", []))
        else:
            cov = len(verified_rows & set(research)) / len(research)
            table_score = min(1.0, cov / 0.5)
            if cov < 0.5:
                issues.append(Issue("note", "tables", f"The evidence table covers {len(verified_rows & set(research))}"
                                    f"/{len(research)} cited studies.", 0, "",
                                    "Extract the key studies cited in the text too.", sorted(set(research) - verified_rows)[:8]))
    # ---- hand-written bibliography --------------------------------------------
    if any(REF_HEAD.match("# " + h) for h in headings):
        issues.append(Issue("note", "format", "Draft contains a hand-written references section.", 0, "",
                        "Leave references out - `factual-research render` generates them from the [@key] citations.", []))
    # ---- verification depth -----------------------------------------------
    depth = {k: src.depth(k) for k in cited}
    m["depth"] = {d: sum(1 for v in depth.values() if v == d) for d in ("fulltext", "abstract", "metadata")}
    if cited and m["depth"]["fulltext"] / len(cited) < 0.5:
        issues.append(Issue("note", "depth", f"Only {m['depth']['fulltext']}/{len(cited)} cited sources were verified "
                            "against full text.", 0, "", "Run `factual-research fetch`; put paywalled PDFs in sources/<key>.pdf.",
                            [k for k, v in depth.items() if v != "fulltext"][:10]))
    # ---- score -----------------------------------------------------------------
    comp = {}
    if m["cited_sentences"]:
        comp["support"] = (30, m["support_sum"] / m["cited_sentences"])
    if m["numbers_checked"]:
        comp["numbers"] = (20, m["numbers_ok"] / m["numbers_checked"])
    if m["quotes"]:
        comp["quotes"] = (10, m["quotes_ok"] / m["quotes"])
    if m["factual_sentences"]:
        comp["coverage"] = (20, 1 - m["factual_uncited"] / m["factual_sentences"])
    if m["cited_sentences"]:
        comp["calibration"] = (10, max(0.0, 1 - 2 * m["overclaims"] / m["cited_sentences"]))
    denom = len([c for c in claims]) + len(plan.subqs) + checked_main
    if denom:
        comp["balance_scope"] = (10, max(0.0, 1 - (unbalanced + uncovered + missing_main) / denom))
    if table_score is not None:
        comp["evidence_table"] = (5, table_score)
    tw = sum(w for w, _ in comp.values()) or 1
    score = 100 * sum(w * r for w, r in comp.values()) / tw if comp else 0.0
    if m["sentences"] and not m["cited_sentences"]:
        score = min(score, 20.0)
    score = max(0.0, score - 15 * hard)
    issues.sort(key=lambda i: (SEV_ORDER[i.severity], {"integrity": 0, "quotes": 1, "numbers": 2, "support": 3,
                                                        "coverage": 4, "calibration": 5, "primary": 6, "tables": 7,
                                                        "balance": 8, "scope": 9}.get(i.check, 9), i.line))
    report = {
        "score": round(score, 1), "target": target,
        "pass": score >= target and hard == 0 and not any(i.severity == "error" for i in issues),
        "band": "verified" if score >= 90 else "solid" if score >= 80 else "needs work" if score >= 60 else "unreliable",
        "hard_errors": hard, "components": {k: round(r * 100, 1) for k, (w, r) in comp.items()},
        "metrics": {k: (sorted(v) if isinstance(v, set) else v) for k, v in m.items() if k != "cited_keys"},
        "cited_keys": sorted(cited), "verification_depth": depth, "sentences": per_sentence,
        "counts": {s: sum(1 for i in issues if i.severity == s) for s in ("error", "warn", "note")},
        "issues": [asdict(i) for i in issues],
    }
    if style_scorer:
        report["style"] = run_style_scorer(style_scorer, draft)
    return report


def _check_number(n, keys, src: Sources) -> str:
    best = "missing"
    for k in keys:
        vals, pairs = src.nums(k)
        r = number_in(n, vals, pairs)
        if r == "exact":
            return "exact"
        if r == "rounded":
            best = "rounded"
    return best


def _num_issue(n, keys, src: Sources, s: Sentence, short: str) -> Issue:
    deep = any(src.depth(k) == "fulltext" for k in keys)
    return Issue("error" if deep else "warn", "numbers",
                 f"{n.raw} does not occur in " + ", ".join("@" + k for k in keys)
                 + ("" if deep else " (abstract only - may be in the full text)") + ".",
                 s.line, short, "Copy the exact value from the source (check `factual-research find`)"
                 + ("" if deep else " or add the PDF to sources/") + ".", keys)


def run_style_scorer(path: str, draft: Path) -> dict:
    p = Path(path).expanduser()
    if p.is_dir():
        p = p / "score.py"
    if not p.exists():
        return {"error": f"style scorer not found at {p}"}
    # score the prose without citation markup
    from .citations import strip_cites as _sc
    tmp = draft.with_suffix(".style.md")
    tmp.write_text(_sc(draft.read_text(encoding="utf-8")), encoding="utf-8")
    try:
        r = subprocess.run(["python3", str(p), str(tmp), "--json"], capture_output=True, text=True, timeout=120)
        return json.loads(r.stdout) if r.stdout.strip().startswith("{") else {"error": (r.stderr or r.stdout)[:500]}
    finally:
        tmp.unlink(missing_ok=True)


def format_report(r: dict, max_items: int = 40) -> str:
    out = [f"factual-research verify: score {r['score']}/100 ({r['band']}), target {r['target']}: "
           f"{'PASS' if r['pass'] else 'FAIL'}"]
    comp = "  ".join(f"{k} {v:.0f}" for k, v in r["components"].items())
    out.append(f"components: {comp}")
    d = r["metrics"]["depth"]
    out.append(f"sources cited: {len(r['cited_keys'])} (full text {d.get('fulltext', 0)}, abstract {d.get('abstract', 0)}, "
               f"metadata {d.get('metadata', 0)}) | errors {r['counts']['error']}, warnings {r['counts']['warn']}, "
               f"notes {r['counts']['note']}")
    if r.get("style"):
        st = r["style"]
        out.append(f"style: {st.get('overall', st.get('score', st.get('error', '?')))}")
    out.append("")
    for i, it in enumerate(r["issues"][:max_items], 1):
        loc = f"L{it['line']}" if it["line"] else "--"
        out.append(f"{i:2d}. [{it['severity'].upper():5s}] {it['check']:11s} {loc:>5s}  {it['message']}")
        if it["text"]:
            out.append(f"      > {it['text'][:150]}")
        if it["fix"]:
            out.append(f"      fix: {it['fix'][:300]}")
    if len(r["issues"]) > max_items:
        out.append(f"... {len(r['issues']) - max_items} more (use --json)")
    return "\n".join(out)
