"""Text helpers shared by every stage: normalisation, tokens, sentences,
numbers and quote matching. Pure stdlib, works for English and Polish."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------

_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe63\uff0d"), "-")
_QUOTES = {ord(c): '"' for c in "“”„‟«»″"}
_QUOTES.update({ord(c): "'" for c in "‘’‚‛′"})
_SPACES = dict.fromkeys(map(ord, "       "), " ")
_ZW = dict.fromkeys(map(ord, "​‌‍﻿­"), None)


def normalize(text: str) -> str:
    """Canonical form for matching: NFKC, plain dashes/quotes/spaces,
    de-hyphenated line breaks, collapsed whitespace."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = t.translate(_ZW).translate(_DASHES).translate(_QUOTES).translate(_SPACES)
    t = t.replace("·", ".").replace("•", " ")  # Lancet-style decimal point
    t = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", t)          # infec-\ntion -> infection
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def fold(text: str) -> str:
    """normalize() + lowercase + strip punctuation noise (for fuzzy matching)."""
    t = normalize(text).lower()
    t = re.sub(r"[\"'`*_\[\]()]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def slug(s: str, maxlen: int = 40) -> str:
    s = strip_accents(s).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:maxlen].strip("-") or "x"


# --------------------------------------------------------------------------
# tokens
# --------------------------------------------------------------------------

STOP_EN = set("""a an the and or but if then else of to in on at by for with without from into onto over under
as is are was were be been being am do does did done has have had having this that these those it its it's
they them their there here which who whom whose what when where why how not no nor so than too very can could
may might must shall should will would also such each other some any all both either neither more most less
least many much few own same only just about above after again against before below between during through
until up down out off further once i we you he she our your his her my me us one two per via vs versus
et al eg ie""".split())

STOP_PL = set("""a aby albo ale ani aż bardzo bez bo być był była było były będzie będą by byli co coś czy czyli
dla do gdy gdzie go i ich ile im inne inny iż ja jak jako je jego jej jest jeszcze jeśli już ją każdy kiedy
kto która które którego której który których którym którzy lub ma mają mi mnie mu my na nad nam nas nie nich
nim niż no o od oraz po pod podczas przez przy również się są ta tak także tam te tego tej ten też to tu tylko
tym u w we więc wszystko z za ze że żeby oraz natomiast jednak według wśród około""".split())

STOP = STOP_EN | STOP_PL

_WORD = re.compile(r"[^\W\d_](?:[^\W_]|['-][^\W\d_])*", re.UNICODE)

_EN_SUFFIXES = ("ational", "ization", "fulness", "iveness", "ations", "ation", "ments", "ment", "ities",
                "ity", "ness", "ings", "ing", "ies", "ied", "ers", "er", "ed", "ly", "es", "s")
_PL_SUFFIXES = ("owaniami", "owaniach", "owanie", "owania", "ościami", "ościach", "ości", "ość", "ami", "ach",
                "ych", "ymi", "ego", "emu", "owi", "iem", "ów", "om", "ej", "ie", "ia", "a", "y", "e", "ą", "ę",
                "u", "i", "o")


def stem(word: str) -> str:
    """Tiny language-agnostic stemmer: suffix strip then truncate. Good enough
    for overlap scoring, deliberately not linguistically perfect."""
    w = word.lower()
    if len(w) <= 3:
        return w
    for suf in _EN_SUFFIXES + _PL_SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            break
    return w[:7]


def words(text: str) -> list[str]:
    return _WORD.findall(normalize(text).lower())


def content_tokens(text: str) -> list[str]:
    return [stem(w) for w in words(text) if w not in STOP and len(w) > 1]


def detect_lang(text: str) -> str:
    ws = words(text[:20000])
    if not ws:
        return "en"
    en = sum(w in STOP_EN for w in ws)
    pl = sum(w in STOP_PL for w in ws) + sum(any(c in w for c in "ąćęłńóśźż") for w in ws)
    return "pl" if pl > en else "en"


def overlap(query: str, passage: str) -> float:
    """Share of the query's content stems that appear in the passage, with a
    small bonus for shared adjacent pairs (word order evidence)."""
    q = content_tokens(query)
    if not q:
        return 0.0
    p = content_tokens(passage)
    ps = set(p)
    uni = sum(t in ps for t in q) / len(q)
    if len(q) < 2:
        return uni
    qb = set(zip(q, q[1:]))
    pb = set(zip(p, p[1:]))
    bi = len(qb & pb) / len(qb)
    return min(1.0, 0.8 * uni + 0.2 * bi + 0.1 * bi * uni)


# --------------------------------------------------------------------------
# sentences
# --------------------------------------------------------------------------

_ABBR = ("e.g", "i.e", "et al", "fig", "figs", "tab", "vs", "approx", "ca", "cf", "no", "vol", "pp", "p",
         "dr", "prof", "mr", "mrs", "ms", "st", "jr", "sr", "inc", "ltd", "co", "eq", "ref", "refs", "sec",
         "np", "tzw", "tj", "m.in", "ok", "itd", "itp", "ds", "wg", "por", "zob", "r", "mln", "mld", "tys",
         "ang", "łac", "jw", "dot", "nr", "s")
_ABBR_RE = re.compile(r"\b(?:" + "|".join(re.escape(a) for a in _ABBR) + r")\.$", re.I)


def split_sentences(text: str) -> list[str]:
    """Split a paragraph into sentences. Keeps trailing citation brackets with
    their sentence ("...risk [@a]." stays one sentence)."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    out, start, depth = [], 0, 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "[(":
            depth += 1
        elif c in "])":
            depth = max(0, depth - 1)
        elif c in ".!?" and depth == 0:
            j = i + 1
            while j < n and text[j] in ".!?\"')”’":
                j += 1
            if j >= n or (text[j] == " " and j + 1 < n and (text[j + 1].isupper() or text[j + 1] in "\"'“„([0123456789")):
                piece = text[start:j]
                before = text[start:i + 1]
                is_abbr = bool(_ABBR_RE.search(before)) or bool(re.search(r"\b[A-Z]\.$", before))
                is_decimal = i + 1 < n and text[i + 1].isdigit()
                if not is_abbr and not is_decimal:
                    out.append(piece.strip())
                    start = j
            i = j
            continue
        i += 1
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


# --------------------------------------------------------------------------
# numbers
# --------------------------------------------------------------------------

_NUM = re.compile(
    r"(?<![\w.,])([-+]?)(\d{1,3}(?:[ ,.]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(\s?%)?(?![\w])")


def _num_candidates(raw: str) -> set[float]:
    """All plausible numeric readings of a token: 1,234 may be 1234 (EN) or
    1.234 (PL decimal comma); 31 521 is 31521."""
    s = raw.replace(" ", "")
    cands: set[float] = set()
    if re.fullmatch(r"\d+", s):
        cands.add(float(s))
    elif re.fullmatch(r"\d+[.,]\d+", s):
        cands.add(float(s.replace(",", ".")))
        if re.fullmatch(r"\d{1,3}[.,]\d{3}", s):  # thousands separator reading
            cands.add(float(s.replace(",", "").replace(".", "")))
    else:
        m = re.fullmatch(r"(\d{1,3}(?:[,.]\d{3})+)(?:([.,])(\d+))?", s)
        if m:
            intpart = re.sub(r"[,.]", "", m.group(1))
            cands.add(float(intpart + ("." + m.group(3) if m.group(3) else "")))
        try:
            cands.add(float(s.replace(",", "")))
        except ValueError:
            pass
    return cands


class Num:
    __slots__ = ("raw", "values", "decimals", "pct", "start", "end")

    def __init__(self, raw, values, decimals, pct, start, end):
        self.raw, self.values, self.decimals, self.pct, self.start, self.end = raw, values, decimals, pct, start, end

    def __repr__(self):
        return f"Num({self.raw!r})"


def extract_numbers(text: str) -> list[Num]:
    t = normalize(text)
    out = []
    for m in _NUM.finditer(t):
        sign, body, pct = m.group(1), m.group(2), bool(m.group(3))
        vals = _num_candidates(body)
        if sign == "-":
            vals = {-v for v in vals}
        dec = 0
        dm = re.search(r"[.,](\d+)$", body.replace(" ", ""))
        if dm and not re.fullmatch(r"\d{1,3}(?:[ ,.]\d{3})+", body):
            dec = len(dm.group(1))
        out.append(Num(m.group(0).strip(), vals, dec, pct, m.start(), m.end()))
    return out


def is_significant(n: Num, context: str = "") -> bool:
    """Numbers worth checking: decimals, percentages, or integers > 10 that
    are not plausibly a year or a list/section index."""
    if n.pct or n.decimals:
        return True
    v = max(abs(x) for x in n.values) if n.values else 0
    if v <= 10:
        return False
    if 1900 <= v <= 2100 and re.fullmatch(r"\d{4}", n.raw):
        return False
    return True


def number_index(text: str) -> list[tuple[float, int]]:
    """(value, decimals) pairs for every number in a source text."""
    idx = []
    for n in extract_numbers(text):
        for v in n.values:
            idx.append((v, n.decimals))
    return idx


def number_in(n: Num, index_values: set[float], index_pairs: list[tuple[float, int]]) -> str:
    """'exact' | 'rounded' | 'missing'."""
    for v in n.values:
        if v in index_values or -v in index_values:
            return "exact"
    for v in n.values:
        for sv, sdec in index_pairs:
            if sdec > n.decimals and round(abs(sv), n.decimals) == abs(v):
                return "rounded"
    return "missing"


# --------------------------------------------------------------------------
# quote matching
# --------------------------------------------------------------------------

def find_quote(quote: str, text: str, threshold: float = 0.88) -> tuple[float, int]:
    """Locate a (possibly ellipsised) quote in text. Returns (similarity,
    char offset in folded text) - similarity 1.0 for an exact hit."""
    q = fold(quote).strip(" .\"'")
    t = fold(text)
    if not q or not t:
        return 0.0, -1
    parts = [p.strip(" .") for p in re.split(r"\.\.\.|…|\[\.\.\.\]", q) if len(p.strip(" .")) > 3]
    if len(parts) > 1:
        pos, sims = 0, []
        for p in parts:
            s, at = find_quote(p, t[pos:] if pos > 0 else t, threshold)
            if at < 0:
                return min(sims + [s]) if sims else s, -1
            sims.append(s)
            pos += at + len(p)
        return min(sims), pos
    at = t.find(q)
    if at >= 0:
        return 1.0, at
    # fuzzy: anchor on the rarest long words, compare windows
    ws = sorted(set(re.findall(r"\w{5,}", q)), key=len, reverse=True)[:4] or q.split()[:2]
    best, best_at, L = 0.0, -1, len(q)
    seen = set()
    for w in ws:
        for m in re.finditer(re.escape(w), t):
            qoff = q.find(w)
            lo = max(0, m.start() - qoff - 20)
            if lo // 10 in seen:
                continue
            seen.add(lo // 10)
            win = t[lo: lo + L + 40]
            sm = SequenceMatcher(None, q, win, autojunk=False)
            blocks = sm.get_matching_blocks()
            matched = sum(b.size for b in blocks)
            r = matched / L
            if r > best:
                best, best_at = r, lo
            if best >= 0.995:
                return best, best_at
    return (best, best_at) if best >= threshold else (best, -1)


def snippet(text: str, at: int, length: int = 220) -> str:
    t = fold(text)
    if at < 0:
        return ""
    lo = max(0, at - 30)
    s = t[lo: lo + length]
    return ("…" if lo else "") + s + ("…" if lo + length < len(t) else "")


# --------------------------------------------------------------------------
# chunking
# --------------------------------------------------------------------------

def chunk_text(text: str, size: int = 700, overlap_sents: int = 1) -> list[str]:
    sents = split_sentences(normalize(text))
    chunks, cur, cur_len = [], [], 0
    for s in sents:
        if cur and cur_len + len(s) > size:
            chunks.append(" ".join(cur))
            cur = cur[-overlap_sents:] if overlap_sents else []
            cur_len = sum(len(x) for x in cur)
        cur.append(s)
        cur_len += len(s) + 1
    if cur:
        chunks.append(" ".join(cur))
    return chunks
