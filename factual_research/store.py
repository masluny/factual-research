"""Project storage: one SQLite file per research project (.fr/db.sqlite)
holding records, search log, screening, full text index, claims, evidence
and extraction tables."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from . import env
from .model import Work, family_name, infer_design, make_key, merge, norm_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
    key TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new',
    origin TEXT NOT NULL DEFAULT 'search', created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS ids (kind TEXT, value TEXT, key TEXT, PRIMARY KEY (kind, value));
CREATE TABLE IF NOT EXISTS queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, subq TEXT, connector TEXT, query TEXT, params TEXT,
    ts REAL, total INTEGER, returned INTEGER, error TEXT, run TEXT);
CREATE TABLE IF NOT EXISTS hits (query_id INTEGER, key TEXT, rank INTEGER, PRIMARY KEY (query_id, key));
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, stage TEXT, decision TEXT, reason TEXT, by TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS fulltext (
    key TEXT PRIMARY KEY, kind TEXT, path TEXT, pages TEXT, meta TEXT, ts REAL);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(key UNINDEXED, loc UNINDEXED, src UNINDEXED, text,
    tokenize = 'unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS claims (id TEXT PRIMARY KEY, subq TEXT, text TEXT, outcome TEXT, created REAL);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT, key TEXT, stance TEXT, quote TEXT, loc TEXT,
    note TEXT, effect TEXT, verified REAL, created REAL);
CREATE TABLE IF NOT EXISTS extraction (
    key TEXT, field TEXT, value TEXT, quote TEXT, loc TEXT, verified REAL, ts REAL, PRIMARY KEY (key, field));
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

# project data folder; older projects keep theirs in ".cg"
DATA_DIRS = (".fr", ".cg")

INCLUDED = ("ta_included", "ft_included")
EXCLUDED = ("ta_excluded", "ft_excluded", "auto_excluded")


def data_dir(root: Path) -> Path:
    return next((root / d for d in DATA_DIRS if (root / d).is_dir()), root / DATA_DIRS[0])


def find_root(start: Path | None = None) -> Path | None:
    forced = env("PROJECT")
    if forced:
        return Path(forced).resolve()
    p = (start or Path.cwd()).resolve()
    for d in [p, *p.parents]:
        if any((d / x).is_dir() for x in DATA_DIRS):
            return d
    return None


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = data_dir(self.root)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "files").mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.dir / "db.sqlite", timeout=60, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.renamed: dict[str, str] = {}

    # ---- meta ------------------------------------------------------------
    def get_meta(self, k: str, default=None):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    def set_meta(self, k: str, v):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, json.dumps(v)))
        self.db.commit()

    # ---- works -----------------------------------------------------------
    def find_key(self, w: Work) -> str | None:
        for kind, val in w.ids():
            r = self.db.execute("SELECT key FROM ids WHERE kind=? AND value=?", (kind, val)).fetchone()
            if r:
                return r[0]
        return None

    def key_for_id(self, kind: str, value: str) -> str | None:
        r = self.db.execute("SELECT key FROM ids WHERE kind=? AND value=?", (kind, norm_id(kind, value))).fetchone()
        return r[0] if r else None

    def _index_ids(self, key: str, w: Work):
        for kind, val in w.ids():
            self.db.execute("INSERT OR IGNORE INTO ids VALUES (?,?,?)", (kind, val, key))

    def upsert(self, w: Work, origin: str = "search", commit: bool = True) -> tuple[str, bool]:
        """Insert or merge a record. Returns (key, is_new). A record that
        nothing refers to yet is re-keyed when a merge changes its first
        author (e.g. OpenAlex "Xu Chen" corrected by Crossref "Xu, Chen")."""
        key = self.find_key(w)
        now = time.time()
        if key:
            cur = self.get(key)
            lead = family_name(cur.authors[0]) if cur.authors else ""
            merged = merge(cur, w)
            merged.design, merged.design_basis = infer_design(merged)
            self.db.execute("UPDATE works SET data=?, updated=? WHERE key=?",
                            (json.dumps(merged.to_dict(), ensure_ascii=False), now, key))
            self._index_ids(key, merged)
            if merged.authors and family_name(merged.authors[0]) != lead and self._unreferenced(key):
                taken = {r[0] for r in self.db.execute("SELECT key FROM works")} - {key}
                new = make_key(merged, taken)
                if new != key:
                    self.rekey(key, new)
                    key = new
            is_new = False
        else:
            taken = {r[0] for r in self.db.execute("SELECT key FROM works")}
            key = make_key(w, taken)
            w.design, w.design_basis = infer_design(w)
            self.db.execute("INSERT INTO works (key, data, status, origin, created, updated) VALUES (?,?,?,?,?,?)",
                            (key, json.dumps(w.to_dict(), ensure_ascii=False), "new", origin, now, now))
            self._index_ids(key, w)
            is_new = True
        if commit:
            self.db.commit()
        return key, is_new

    def _unreferenced(self, key: str) -> bool:
        """Never screened, decided on, cited as evidence or given full text,
        and not added by hand (the user has seen that key)."""
        r = self.row(key)
        if not r or r["status"] != "new" or r["origin"] == "manual":
            return False
        return not any(self.db.execute(f"SELECT 1 FROM {t} WHERE key=? LIMIT 1", (key,)).fetchone()
                       for t in ("decisions", "fulltext", "evidence", "extraction"))

    def rekey(self, old: str, new: str):
        for t in ("works", "ids", "hits", "decisions", "fulltext", "chunks", "evidence", "extraction"):
            self.db.execute(f"UPDATE {t} SET key=? WHERE key=?", (new, old))
        self.renamed[old] = new

    def current_key(self, key: str) -> str:
        """Follow renames made by upsert() in this session."""
        while key in self.renamed:
            key = self.renamed[key]
        return key

    def save(self, key: str, w: Work):
        w.design, w.design_basis = (w.design, w.design_basis) if w.extra.get("design_locked") else infer_design(w)
        self.db.execute("UPDATE works SET data=?, updated=? WHERE key=?",
                        (json.dumps(w.to_dict(), ensure_ascii=False), time.time(), key))
        self._index_ids(key, w)
        self.db.commit()

    def get(self, key: str) -> Work | None:
        r = self.db.execute("SELECT data FROM works WHERE key=?", (key,)).fetchone()
        return Work.from_dict(json.loads(r[0])) if r else None

    def row(self, key: str):
        return self.db.execute("SELECT * FROM works WHERE key=?", (key,)).fetchone()

    def works(self, status: tuple[str, ...] | str | None = None, origin: str | None = None) -> list[tuple[str, str, Work]]:
        sql, args = "SELECT key, status, data FROM works", []
        conds = []
        if status:
            st = (status,) if isinstance(status, str) else status
            conds.append(f"status IN ({','.join('?' * len(st))})")
            args += list(st)
        if origin:
            conds.append("origin=?")
            args.append(origin)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created"
        return [(r[0], r[1], Work.from_dict(json.loads(r[2]))) for r in self.db.execute(sql, args)]

    def status(self, key: str) -> str | None:
        r = self.db.execute("SELECT status FROM works WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def set_status(self, key: str, status: str):
        self.db.execute("UPDATE works SET status=?, updated=? WHERE key=?", (status, time.time(), key))
        self.db.commit()

    def resolve(self, ref: str) -> str | None:
        """Accept a key, DOI, PMID, PMCID, arXiv id or OpenAlex id."""
        ref = ref.strip().lstrip("@")
        if self.row(ref):
            return ref
        low = ref.lower()
        for kind, pref in (("doi", "doi:"), ("pmid", "pmid:"), ("pmcid", "pmcid:"), ("arxiv", "arxiv:"),
                           ("openalex", "openalex:"), ("nct", "nct:")):
            if low.startswith(pref):
                return self.key_for_id(kind, ref[len(pref):])
        if low.startswith("10.") or "doi.org/" in low:
            return self.key_for_id("doi", ref)
        if low.startswith("pmc"):
            return self.key_for_id("pmcid", ref)
        if ref.isdigit():
            return self.key_for_id("pmid", ref)
        if low.startswith("nct"):
            return self.key_for_id("nct", ref)
        return None

    # ---- search log ------------------------------------------------------
    def log_query(self, subq, connector, query, params, total, returned, error="", run="") -> int:
        cur = self.db.execute(
            "INSERT INTO queries (subq, connector, query, params, ts, total, returned, error, run) VALUES (?,?,?,?,?,?,?,?,?)",
            (subq, connector, query, json.dumps(params), time.time(), total, returned, error, run))
        self.db.commit()
        return cur.lastrowid

    def add_hit(self, qid: int, key: str, rank: int):
        self.db.execute("INSERT OR IGNORE INTO hits VALUES (?,?,?)", (qid, key, rank))

    def queries(self) -> list[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM queries ORDER BY id"))

    # ---- screening -------------------------------------------------------
    def decide(self, key: str, stage: str, decision: str, reason: str = "", by: str = "agent"):
        self.db.execute("INSERT INTO decisions (key, stage, decision, reason, by, ts) VALUES (?,?,?,?,?,?)",
                        (key, stage, decision, reason, by, time.time()))
        status = {("ta", "include"): "ta_included", ("ta", "exclude"): "ta_excluded",
                  ("ft", "include"): "ft_included", ("ft", "exclude"): "ft_excluded",
                  ("auto", "exclude"): "auto_excluded", ("ta", "maybe"): "maybe",
                  ("ft", "not_retrieved"): "not_retrieved"}.get((stage, decision))
        if status:
            self.set_status(key, status)
        self.db.commit()

    def decisions(self, key: str | None = None):
        if key:
            return list(self.db.execute("SELECT * FROM decisions WHERE key=? ORDER BY id", (key,)))
        return list(self.db.execute("SELECT * FROM decisions ORDER BY id"))

    # ---- full text -------------------------------------------------------
    def save_fulltext(self, key: str, kind: str, path: str, pages: list[dict], meta: dict):
        try:
            path = str(Path(path).resolve().relative_to(self.root.resolve()))
        except ValueError:
            pass
        self.db.execute("INSERT OR REPLACE INTO fulltext VALUES (?,?,?,?,?,?)",
                        (key, kind, path, json.dumps(pages, ensure_ascii=False), json.dumps(meta, ensure_ascii=False),
                         time.time()))
        self.db.execute("DELETE FROM chunks WHERE key=? AND src='fulltext'", (key,))
        from .textutil import chunk_text
        for pg in pages:
            for ch in chunk_text(pg["text"]):
                self.db.execute("INSERT INTO chunks (key, loc, src, text) VALUES (?,?,?,?)",
                                (key, pg["loc"], "fulltext", ch))
        self.db.commit()

    def fulltext(self, key: str) -> dict | None:
        r = self.db.execute("SELECT * FROM fulltext WHERE key=?", (key,)).fetchone()
        if not r:
            return None
        return {"kind": r["kind"], "path": r["path"], "pages": json.loads(r["pages"]), "meta": json.loads(r["meta"])}

    def index_abstract(self, key: str, w: Work):
        self.db.execute("DELETE FROM chunks WHERE key=? AND src='abstract'", (key,))
        text = (w.title + ". " + (w.abstract or "")).strip()
        from .textutil import chunk_text
        for ch in chunk_text(text):
            self.db.execute("INSERT INTO chunks (key, loc, src, text) VALUES (?,?,?,?)", (key, "abstract", "abstract", ch))

    def source_text(self, key: str) -> tuple[str, str, list[dict]]:
        """(depth, full concatenated text, pages). depth: fulltext|abstract|metadata."""
        w = self.get(key)
        ft = self.fulltext(key)
        abstract = (w.title + ". " + (w.abstract or "")) if w else ""
        if ft and ft["pages"]:
            pages = ft["pages"]
            return "fulltext", abstract + "\n" + "\n".join(p["text"] for p in pages), pages
        if w and w.abstract:
            return "abstract", abstract, [{"loc": "abstract", "text": abstract}]
        return "metadata", abstract, [{"loc": "title", "text": abstract}]

    def search_chunks(self, query: str, keys: list[str] | None = None, limit: int = 8) -> list[sqlite3.Row]:
        from .textutil import words
        toks = [w for w in words(query) if len(w) > 2]
        if not toks:
            return []
        fts = " OR ".join('"' + t.replace('"', "") + '"' for t in dict.fromkeys(toks))
        sql = "SELECT key, loc, src, text, bm25(chunks) AS score FROM chunks WHERE chunks MATCH ?"
        args: list = [fts]
        if keys:
            sql += f" AND key IN ({','.join('?' * len(keys))})"
            args += keys
        sql += " ORDER BY score LIMIT ?"
        args.append(limit)
        try:
            return list(self.db.execute(sql, args))
        except sqlite3.OperationalError:
            return []

    # ---- claims & evidence ----------------------------------------------
    def add_claim(self, cid: str, text: str, subq: str = "", outcome: str = ""):
        self.db.execute("INSERT OR REPLACE INTO claims VALUES (?,?,?,?,?)", (cid, subq, text, outcome, time.time()))
        self.db.commit()

    def claims(self):
        return list(self.db.execute("SELECT * FROM claims ORDER BY created"))

    def next_claim_id(self) -> str:
        n = self.db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        while self.db.execute("SELECT 1 FROM claims WHERE id=?", (f"C{n + 1}",)).fetchone():
            n += 1
        return f"C{n + 1}"

    def add_evidence(self, claim_id, key, stance, quote, loc, note, effect, verified) -> int:
        cur = self.db.execute(
            "INSERT INTO evidence (claim_id, key, stance, quote, loc, note, effect, verified, created) VALUES (?,?,?,?,?,?,?,?,?)",
            (claim_id, key, stance, quote, loc, note, effect, verified, time.time()))
        self.db.commit()
        return cur.lastrowid

    def evidence(self, claim_id: str | None = None):
        if claim_id:
            return list(self.db.execute("SELECT * FROM evidence WHERE claim_id=? ORDER BY id", (claim_id,)))
        return list(self.db.execute("SELECT * FROM evidence ORDER BY id"))

    # ---- extraction ------------------------------------------------------
    def set_cell(self, key, field, value, quote, loc, verified):
        self.db.execute("INSERT OR REPLACE INTO extraction VALUES (?,?,?,?,?,?,?)",
                        (key, field, value, quote, loc, verified, time.time()))
        self.db.commit()

    def cells(self, key: str | None = None):
        if key:
            return list(self.db.execute("SELECT * FROM extraction WHERE key=? ORDER BY ts", (key,)))
        return list(self.db.execute("SELECT * FROM extraction ORDER BY key, ts"))
