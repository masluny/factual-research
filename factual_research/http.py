"""Polite HTTP: per-host rate limits, retries with backoff, and a shared
SQLite response cache (~/.cache/factual-research/http.sqlite). Stdlib only."""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__, env

CACHE_DIR = Path(env("CACHE") or Path.home() / ".cache" / "factual-research")

# minimum seconds between requests to one host (public, key-less limits)
HOST_INTERVAL = {
    "eutils.ncbi.nlm.nih.gov": 0.36,
    "www.ebi.ac.uk": 0.12,
    "api.openalex.org": 0.12,
    "api.crossref.org": 0.1,
    "export.arxiv.org": 3.1,
    "api.semanticscholar.org": 1.1,
    "dblp.org": 1.0,
    "inspirehep.net": 0.4,
    "ntrs.nasa.gov": 0.5,
    "clinicaltrials.gov": 0.2,
    "api.sejm.gov.pl": 0.2,
    "www.saos.org.pl": 0.5,
    "pubchem.ncbi.nlm.nih.gov": 0.25,
    "api.worldbank.org": 0.2,
    "api.adsabs.harvard.edu": 0.3,
    "ieeexploreapi.ieee.org": 0.5,
    "api.core.ac.uk": 1.0,
    "doaj.org": 0.3,
    "bdl.stat.gov.pl": 0.6,
}
DEFAULT_INTERVAL = 0.25


class HTTPError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url}")
        self.status, self.url, self.body = status, url, body


def user_agent() -> str:
    mail = env("MAILTO").strip()
    ua = f"factual-research/{__version__} (research verification CLI; +https://github.com/masluny/factual-research)"
    return ua.replace(")", f"; mailto:{mail})") if mail else ua


class _Limiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.next_ok: dict[str, float] = {}
        self.host_locks: dict[str, threading.Lock] = {}

    def wait(self, host: str):
        with self.lock:
            hl = self.host_locks.setdefault(host, threading.Lock())
        with hl:
            now = time.monotonic()
            t = self.next_ok.get(host, 0.0)
            if t > now:
                time.sleep(t - now)
            self.next_ok[host] = time.monotonic() + HOST_INTERVAL.get(host, DEFAULT_INTERVAL)


_limiter = _Limiter()


class Cache:
    def __init__(self, path: Path | None = None):
        path = path or CACHE_DIR / "http.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.local = threading.local()

    @property
    def db(self) -> sqlite3.Connection:
        c = getattr(self.local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE IF NOT EXISTS resp (k TEXT PRIMARY KEY, ts REAL, status INT, ctype TEXT, body BLOB)")
            self.local.c = c
        return c

    def get(self, k: str, ttl: float):
        row = self.db.execute("SELECT ts, status, ctype, body FROM resp WHERE k=?", (k,)).fetchone()
        if row and (ttl < 0 or time.time() - row[0] < ttl):
            return row[1], row[2], gzip.decompress(row[3])
        return None

    def put(self, k: str, status: int, ctype: str, body: bytes):
        self.db.execute("INSERT OR REPLACE INTO resp VALUES (?,?,?,?,?)",
                        (k, time.time(), status, ctype, gzip.compress(body)))
        self.db.commit()


_cache: Cache | None = None


def cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache


DAY = 86400.0


def request(url: str, params: dict | None = None, *, headers: dict | None = None, data: bytes | None = None,
            ttl: float = 7 * DAY, retries: int = 4, timeout: float = 40, use_cache: bool = True) -> tuple[int, str, bytes]:
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(clean, doseq=True)
    key = hashlib.sha256((url + "\n" + (data or b"").decode("utf-8", "ignore")).encode()).hexdigest()
    if use_cache and ttl != 0:
        hit = cache().get(key, ttl)
        if hit:
            return hit
    host = urllib.parse.urlparse(url).netloc
    hdrs = {"User-Agent": user_agent(), "Accept-Encoding": "gzip"}
    hdrs.update(headers or {})
    delay = 2.0
    last: Exception | None = None
    for attempt in range(retries + 1):
        _limiter.wait(host)
        req = urllib.request.Request(url, data=data, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                status, ctype = r.status, r.headers.get("Content-Type", "")
                if use_cache and ttl != 0:
                    cache().put(key, status, ctype, body)
                return status, ctype, body
        except urllib.error.HTTPError as e:
            body = e.read() or b""
            last = HTTPError(e.code, url, body[:500].decode("utf-8", "ignore"))
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                ra = e.headers.get("Retry-After")
                wait = float(ra) if ra and ra.isdigit() else delay
                time.sleep(min(wait, 30))
                delay *= 2
                continue
            raise last
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
            if attempt < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise last  # pragma: no cover


def get_json(url: str, params: dict | None = None, **kw):
    status, ctype, body = request(url, params, headers={"Accept": "application/json", **kw.pop("headers", {})}, **kw)
    return json.loads(body.decode("utf-8"))


def get_text(url: str, params: dict | None = None, **kw) -> str:
    return request(url, params, **kw)[2].decode("utf-8", "replace")


def download(url: str, dest: Path, *, timeout: float = 90, max_bytes: int = 80_000_000) -> tuple[str, Path] | None:
    """Download a file (no cache). Returns (content-type, path) or None if the
    response is not usable (HTML paywall page, too large, error)."""
    host = urllib.parse.urlparse(url).netloc
    _limiter.wait(host)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent(), "Accept": "application/pdf,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            body = r.read(max_bytes + 1)
    except Exception:
        return None
    if len(body) > max_bytes or len(body) < 1000:
        return None
    if body[:5] != b"%PDF-" and "pdf" in ctype.lower():
        return None
    if body[:5] != b"%PDF-" and "html" in ctype.lower():
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return ctype, dest
