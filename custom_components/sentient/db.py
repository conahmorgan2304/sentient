"""Sentient's own database: one SQLite file for everything it remembers over the long term.

SQLite is part of Python, so there is nothing to install. The file sits with Home Assistant's other storage
(``.storage/sentient.<entry>.db``), so Home Assistant's backups include it. Every call runs in the executor,
never in Home Assistant's event loop.

What lives here:

* ``ledger`` — every change in the house and what (or who) caused it, kept for 90 days, then summarised by day;
* ``memories`` — facts, conversations and the timeline, copied here as they happen and kept for a year, with
  full-text search (FTS5, ranked by BM25) across all of them;
* ``beliefs`` — claims it has formed about the house and how well they hold up;
* ``kv`` — small learned models (expectations, outcomes, cases, scores) saved as JSON.

If the Python build has no FTS5, searching falls back to plain word matching.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from typing import Any, Callable

from homeassistant.core import HomeAssistant

LEDGER_DAYS = 90
LEDGER_MAX_ROWS = 400_000  # about 60 MB: the oldest go first beyond this
MEMORY_DAYS = 365
_WORD = re.compile(r"[\w']{2,}", re.UNICODE)

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS ledger (
        id INTEGER PRIMARY KEY, ts REAL NOT NULL, entity TEXT NOT NULL, name TEXT, domain TEXT, area TEXT,
        old TEXT, new TEXT, cause TEXT, who TEXT, surprise REAL)""",
    "CREATE INDEX IF NOT EXISTS ledger_entity ON ledger(entity, ts)",
    "CREATE INDEX IF NOT EXISTS ledger_ts ON ledger(ts)",
    """CREATE TABLE IF NOT EXISTS ledger_days (day TEXT NOT NULL, entity TEXT NOT NULL, changes INTEGER,
        unexplained INTEGER, PRIMARY KEY (day, entity))""",
    """CREATE TABLE IF NOT EXISTS memories (kind TEXT NOT NULL, ref TEXT NOT NULL, ts TEXT, body TEXT, data TEXT,
        PRIMARY KEY (kind, ref))""",
    "CREATE INDEX IF NOT EXISTS memories_ts ON memories(kind, ts)",
    """CREATE TABLE IF NOT EXISTS beliefs (id TEXT PRIMARY KEY, kind TEXT, subject TEXT, claim TEXT, params TEXT,
        support INTEGER DEFAULT 0, against INTEGER DEFAULT 0, created TEXT, tested TEXT, status TEXT DEFAULT 'open',
        asked INTEGER DEFAULT 0)""",
    "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)",
]
FTS = "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(kind UNINDEXED, ref UNINDEXED, body)"


STOP = {"the", "and", "what", "when", "where", "which", "did", "does", "have", "with", "about", "from", "this",
        "that", "there", "was", "were", "you", "any", "anything", "tell"}


def stem(token: str) -> str:
    """A rough stem so boilers/boiler and banging/bang find each other (searched as a prefix)."""
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def fts_query(words: list[str]) -> str:
    """An FTS5 query matching any of the words (quoted, so nothing in them is read as syntax)."""
    out = []
    for w in words:
        for token in _WORD.findall(w.casefold().replace('"', " ")):
            if token in STOP:
                continue
            token = stem(token)
            out.append(f'"{token}"*' if len(token) > 3 else f'"{token}"')
    return " OR ".join(dict.fromkeys(out))


class Database:
    def __init__(self, hass: HomeAssistant, entry_id: str, path: str | None = None) -> None:
        self.hass = hass
        self.path = path or hass.config.path(".storage", f"sentient.{entry_id}.db")
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self.fts = False

    # ----- plumbing ---------------------------------------------------------------------------
    def _open(self) -> sqlite3.Connection:
        if self._conn is None:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            for stmt in SCHEMA:
                conn.execute(stmt)
            try:
                conn.execute(FTS)
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False
            conn.commit()
            self._conn = conn
        return self._conn

    def run(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        """Run fn(connection) in this thread (callers in the event loop use ``call``)."""
        with self._lock:
            conn = self._open()
            try:
                out = fn(conn)
                conn.commit()
                return out
            except Exception:
                conn.rollback()
                raise

    async def call(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        return await self.hass.async_add_executor_job(self.run, fn)

    @staticmethod
    def checkpoint(conn: sqlite3.Connection) -> list[int]:
        """Fold the write-ahead log into the main file (run on its own, never inside a write)."""
        return list(conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())

    def size_mb(self) -> float:
        total = sum(os.path.getsize(p) for p in (self.path, self.path + "-wal") if os.path.exists(p))
        return round(total / 1_048_576, 1)

    async def async_open(self) -> None:
        await self.call(lambda conn: None)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ----- the ledger -------------------------------------------------------------------------
    @staticmethod
    def add_changes(conn: sqlite3.Connection, rows: list[tuple]) -> None:
        conn.executemany("INSERT INTO ledger (ts, entity, name, domain, area, old, new, cause, who, surprise) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)

    @staticmethod
    def changes(conn: sqlite3.Connection, *, entities: list[str] | None = None, since: float | None = None,
                until: float | None = None, causes: list[str] | None = None, limit: int = 50,
                newest_first: bool = True) -> list[dict[str, Any]]:
        where, args = [], []
        if entities:
            where.append(f"entity IN ({','.join('?' * len(entities))})")
            args += entities
        if since is not None:
            where.append("ts >= ?")
            args.append(since)
        if until is not None:
            where.append("ts <= ?")
            args.append(until)
        if causes:
            where.append(f"cause IN ({','.join('?' * len(causes))})")
            args += causes
        sql = "SELECT * FROM ledger" + (" WHERE " + " AND ".join(where) if where else "")
        sql += f" ORDER BY ts {'DESC' if newest_first else 'ASC'} LIMIT ?"
        return [dict(r) for r in conn.execute(sql, [*args, limit])]

    @staticmethod
    def prune_ledger(conn: sqlite3.Connection, cutoff: float) -> int:
        """Summarise old days by entity, then let the detail go."""
        conn.execute(
            "INSERT OR REPLACE INTO ledger_days (day, entity, changes, unexplained) "
            "SELECT date(ts, 'unixepoch'), entity, COUNT(*), SUM(cause = 'device') FROM ledger WHERE ts < ? "
            "GROUP BY date(ts, 'unixepoch'), entity", (cutoff,))
        gone = conn.execute("DELETE FROM ledger WHERE ts < ?", (cutoff,)).rowcount
        over = conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] - LEDGER_MAX_ROWS
        if over > 0:
            gone += conn.execute("DELETE FROM ledger WHERE id IN (SELECT id FROM ledger ORDER BY ts LIMIT ?)", (over,)).rowcount
        return gone

    # ----- memories and search ----------------------------------------------------------------
    def put_memory(self, conn: sqlite3.Connection, kind: str, ref: str, ts: str | None, body: str,
                   data: dict[str, Any] | None = None) -> None:
        conn.execute("INSERT OR REPLACE INTO memories (kind, ref, ts, body, data) VALUES (?,?,?,?,?)",
                     (kind, ref, ts, body, json.dumps(data or {}, default=str)))
        if self.fts:
            conn.execute("DELETE FROM memories_fts WHERE kind = ? AND ref = ?", (kind, ref))
            conn.execute("INSERT INTO memories_fts (kind, ref, body) VALUES (?,?,?)", (kind, ref, body))

    def drop_memory(self, conn: sqlite3.Connection, kind: str, ref: str) -> None:
        conn.execute("DELETE FROM memories WHERE kind = ? AND ref = ?", (kind, ref))
        if self.fts:
            conn.execute("DELETE FROM memories_fts WHERE kind = ? AND ref = ?", (kind, ref))

    def search(self, conn: sqlite3.Connection, words: list[str], kinds: list[str] | None = None,
               limit: int = 10) -> list[dict[str, Any]]:
        """Best matches first (BM25), across facts, conversations and the timeline."""
        query = fts_query(words)
        if not query:
            return []
        kind_sql = f" AND m.kind IN ({','.join('?' * len(kinds))})" if kinds else ""
        if self.fts:
            sql = ("SELECT m.kind, m.ref, m.ts, m.body, bm25(memories_fts) AS rank FROM memories_fts "
                   "JOIN memories m ON m.kind = memories_fts.kind AND m.ref = memories_fts.ref "
                   f"WHERE memories_fts MATCH ?{kind_sql} ORDER BY rank LIMIT ?")
            try:
                return [dict(r) for r in conn.execute(sql, [query, *(kinds or []), limit])]
            except sqlite3.OperationalError:
                pass
        tokens = [t for w in words for t in _WORD.findall(w.casefold())]
        if not tokens:
            return []
        like = " OR ".join("lower(m.body) LIKE ?" for _ in tokens)
        sql = f"SELECT m.kind, m.ref, m.ts, m.body, 0 AS rank FROM memories m WHERE ({like}){kind_sql} ORDER BY m.ts DESC LIMIT ?"
        return [dict(r) for r in conn.execute(sql, [*(f"%{t}%" for t in tokens), *(kinds or []), limit])]

    @staticmethod
    def prune_memories(conn: sqlite3.Connection, kind: str, cutoff_iso: str) -> int:
        refs = [r[0] for r in conn.execute("SELECT ref FROM memories WHERE kind = ? AND ts < ?", (kind, cutoff_iso))]
        conn.executemany("DELETE FROM memories WHERE kind = ? AND ref = ?", [(kind, r) for r in refs])
        try:
            conn.executemany("DELETE FROM memories_fts WHERE kind = ? AND ref = ?", [(kind, r) for r in refs])
        except sqlite3.OperationalError:
            pass
        return len(refs)

    # ----- small saved models -----------------------------------------------------------------
    @staticmethod
    def get_kv(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def set_kv(conn: sqlite3.Connection, key: str, value: Any) -> None:
        conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value, default=str)))

    @staticmethod
    def set_kv_text(conn: sqlite3.Connection, key: str, text: str) -> None:
        """Already serialised (in the event loop, so nothing changes while it's written)."""
        conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, text))

    # ----- clearing ---------------------------------------------------------------------------
    def clear(self, conn: sqlite3.Connection, what: str) -> None:
        if what in ("ledger", "all"):
            conn.execute("DELETE FROM ledger")
            conn.execute("DELETE FROM ledger_days")
        if what in ("learning", "all"):
            conn.execute("DELETE FROM beliefs")
            conn.execute("DELETE FROM kv")
        if what == "all":
            conn.execute("DELETE FROM memories")
            if self.fts:
                conn.execute("DELETE FROM memories_fts")
