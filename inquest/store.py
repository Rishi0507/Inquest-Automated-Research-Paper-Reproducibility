"""SQLite persistence: runs, claims, adapters, deviations, analyses, jobs and provenance."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    paper_id TEXT PRIMARY KEY, meta TEXT NOT NULL, source TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS claims (
    paper_id TEXT NOT NULL, claim_id TEXT NOT NULL, origin TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (paper_id, claim_id, origin));
CREATE TABLE IF NOT EXISTS adapters (
    paper_id TEXT PRIMARY KEY, body TEXT NOT NULL, origin TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS deviations (
    paper_id TEXT NOT NULL, dev_id TEXT NOT NULL, origin TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (paper_id, dev_id, origin));
CREATE TABLE IF NOT EXISTS runs (
    cache_key TEXT PRIMARY KEY, run_id TEXT NOT NULL, paper_id TEXT NOT NULL, seed INTEGER NOT NULL,
    config TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS runs_paper ON runs (paper_id);
CREATE TABLE IF NOT EXISTS analyses (
    analysis_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS analyses_paper ON analyses (paper_id, created);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
    created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS provenance (
    id INTEGER PRIMARY KEY AUTOINCREMENT, paper_id TEXT NOT NULL, src TEXT NOT NULL, rel TEXT NOT NULL,
    dst TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS provenance_paper ON provenance (paper_id);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def _connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.ensure_dirs()
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=30)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(_SCHEMA)
    return _conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = _connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _rows(sql: str, args: tuple = ()) -> list[tuple]:
    with tx() as c:
        return c.execute(sql, args).fetchall()


# ------------------------------------------------------------------ papers

def put_paper(paper_id: str, meta: dict, source: str) -> None:
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO papers VALUES (?,?,?,?)",
                  (paper_id, json.dumps(meta), source, time.time()))


def registered_papers() -> list[dict]:
    return [json.loads(r[0]) | {"source": r[1]} for r in
            _rows("SELECT meta, source FROM papers ORDER BY created")]


def delete_paper(paper_id: str) -> None:
    with tx() as c:
        for table in ("papers", "claims", "adapters", "deviations", "analyses"):
            c.execute(f"DELETE FROM {table} WHERE paper_id=?", (paper_id,))


# ------------------------------------------------------------------ claims, adapters, deviations

def put_claims(paper_id: str, origin: str, claims: list[dict]) -> None:
    with tx() as c:
        c.execute("DELETE FROM claims WHERE paper_id=? AND origin=?", (paper_id, origin))
        c.executemany("INSERT INTO claims VALUES (?,?,?,?)",
                      [(paper_id, cl["claim_id"], origin, json.dumps(cl)) for cl in claims])


def get_claims(paper_id: str, origin: str) -> list[dict]:
    return [json.loads(r[0]) for r in
            _rows("SELECT body FROM claims WHERE paper_id=? AND origin=? ORDER BY claim_id", (paper_id, origin))]


def put_adapter(paper_id: str, adapter: dict, origin: str) -> None:
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO adapters VALUES (?,?,?,?)",
                  (paper_id, json.dumps(adapter), origin, time.time()))


def get_adapter(paper_id: str) -> tuple[dict, str] | None:
    r = _rows("SELECT body, origin FROM adapters WHERE paper_id=?", (paper_id,))
    return (json.loads(r[0][0]), r[0][1]) if r else None


def put_deviations(paper_id: str, origin: str, devs: list[dict]) -> None:
    with tx() as c:
        c.execute("DELETE FROM deviations WHERE paper_id=? AND origin=?", (paper_id, origin))
        c.executemany("INSERT INTO deviations VALUES (?,?,?,?)",
                      [(paper_id, d["dev_id"], origin, json.dumps(d)) for d in devs])


def get_deviations(paper_id: str, origin: str) -> list[dict]:
    return [json.loads(r[0]) for r in
            _rows("SELECT body FROM deviations WHERE paper_id=? AND origin=? ORDER BY dev_id", (paper_id, origin))]


# ------------------------------------------------------------------ runs

def get_run(cache_key: str) -> dict | None:
    r = _rows("SELECT result FROM runs WHERE cache_key=?", (cache_key,))
    return json.loads(r[0][0]) if r else None


def put_run(cache_key: str, run_id: str, paper_id: str, seed: int, cfg: dict, result: dict) -> None:
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?)",
                  (cache_key, run_id, paper_id, seed, json.dumps(cfg, sort_keys=True), json.dumps(result), time.time()))


def runs_for(paper_id: str) -> list[dict]:
    return [json.loads(r[0]) for r in _rows("SELECT result FROM runs WHERE paper_id=? ORDER BY created", (paper_id,))]


def run_by_id(run_id: str) -> dict | None:
    r = _rows("SELECT result FROM runs WHERE run_id=?", (run_id,))
    return json.loads(r[0][0]) if r else None


# ------------------------------------------------------------------ analyses and jobs

def put_analysis(analysis_id: str, paper_id: str, body: dict) -> None:
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO analyses VALUES (?,?,?,?)",
                  (analysis_id, paper_id, json.dumps(body), time.time()))


def latest_analysis(paper_id: str) -> dict | None:
    r = _rows("SELECT body FROM analyses WHERE paper_id=? ORDER BY created DESC LIMIT 1", (paper_id,))
    return json.loads(r[0][0]) if r else None


def put_job(job_id: str, paper_id: str, kind: str, state: dict) -> None:
    now = time.time()
    with tx() as c:
        c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET state=excluded.state, "
                  "updated=excluded.updated", (job_id, paper_id, kind, json.dumps(state), now, now))


def get_job(job_id: str) -> dict | None:
    r = _rows("SELECT state, paper_id, kind FROM jobs WHERE job_id=?", (job_id,))
    if not r:
        return None
    return json.loads(r[0][0]) | {"job_id": job_id, "paper_id": r[0][1], "kind": r[0][2]}


def jobs_for(paper_id: str) -> list[dict]:
    return [json.loads(s) | {"job_id": j, "kind": k} for j, s, k in
            _rows("SELECT job_id, state, kind FROM jobs WHERE paper_id=? ORDER BY created DESC LIMIT 20", (paper_id,))]


# ------------------------------------------------------------------ counters (E6)

def bump(name: str, by: int = 1) -> None:
    with tx() as c:
        c.execute("INSERT INTO counters VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=value+?", (name, by, by))


def counters() -> dict[str, int]:
    return dict(_rows("SELECT name, value FROM counters"))


# ------------------------------------------------------------------ provenance graph (append-only)

def add_edge(paper_id: str, src: str, rel: str, dst: str, data: dict | None = None) -> None:
    with tx() as c:
        c.execute("INSERT INTO provenance (paper_id, src, rel, dst, data, created) VALUES (?,?,?,?,?,?)",
                  (paper_id, src, rel, dst, json.dumps(data or {}), time.time()))


def edges(paper_id: str) -> list[dict]:
    return [{"src": s, "rel": r, "dst": d, "data": json.loads(x)} for s, r, d, x in
            _rows("SELECT src, rel, dst, data FROM provenance WHERE paper_id=? ORDER BY id", (paper_id,))]


# ------------------------------------------------------------------ key/value

def kv_set(key: str, value: Any) -> None:
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO kv VALUES (?,?,?)", (key, json.dumps(value), time.time()))


def kv_get(key: str, default: Any = None) -> Any:
    r = _rows("SELECT value FROM kv WHERE key=?", (key,))
    return json.loads(r[0][0]) if r else default
