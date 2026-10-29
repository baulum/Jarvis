"""Persistence of the connector in its own SQLite database (``<data_dir>/uptik/uptik.db``, WAL).

Own file instead of new Postgres tables: the connector stays self-contained (no migration, nothing shared with other
modules) and runs the same on every OS. Approvals, audit log and notifications stay in Jarvis' database.

Tables: ``accounts`` (platform account, runner, connection state, autopost flag, circuit breaker), ``posts`` (one row
per account and post = the queue and, once finished, the history), ``post_events`` (what happened to a post) and
``inbox`` (videos handed over by the video production or uploaded in the app).

All times are UTC ISO strings of one format (``timing.iso``), so they compare as text. Every call runs in a worker
thread on one connection guarded by a lock.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from jarvis.uptik.circuit import BlockSignal
from jarvis.uptik.model import FINISHED_STATUSES, SUBMITTING
from jarvis.uptik.timing import iso

T = TypeVar("T")
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    label TEXT NOT NULL,
    handle TEXT NOT NULL DEFAULT '',
    runner TEXT NOT NULL DEFAULT 'local',
    device TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'not_connected',
    autopost INTEGER NOT NULL DEFAULT 0,
    circuit_state TEXT NOT NULL DEFAULT 'ok',
    circuit_code TEXT,
    circuit_reason TEXT,
    circuit_at TEXT,
    last_error TEXT,
    connected_at TEXT,
    last_used_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    video_path TEXT NOT NULL,
    video_name TEXT NOT NULL,
    video_size INTEGER NOT NULL DEFAULT 0,
    video_sha256 TEXT NOT NULL DEFAULT '',
    video_duration REAL,
    caption TEXT NOT NULL DEFAULT '',
    hashtags TEXT NOT NULL DEFAULT '[]',
    title TEXT NOT NULL DEFAULT '',
    options TEXT NOT NULL DEFAULT '{}',
    run_at TEXT NOT NULL,
    scheduled INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TEXT,
    error_class TEXT,
    last_error TEXT,
    result_url TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT,
    approval_id TEXT,
    requested_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE (group_id, account_id)
);
CREATE INDEX IF NOT EXISTS ix_posts_status_run_at ON posts (status, run_at);
CREATE INDEX IF NOT EXISTS ix_posts_account ON posts (account_id, status);
CREATE TABLE IF NOT EXISTS post_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id TEXT,
    account_id TEXT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_post_events_post ON post_events (post_id);
CREATE TABLE IF NOT EXISTS inbox (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    duration REAL,
    title TEXT NOT NULL DEFAULT '',
    captions TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (source, source_ref)
);
"""

_ACCOUNT_FIELDS = {
    "label",
    "handle",
    "runner",
    "device",
    "status",
    "autopost",
    "circuit_state",
    "circuit_code",
    "circuit_reason",
    "circuit_at",
    "last_error",
    "connected_at",
    "last_used_at",
}
_POST_FIELDS = {
    "status",
    "stage",
    "attempts",
    "next_attempt_at",
    "error_class",
    "last_error",
    "result_url",
    "started_at",
    "finished_at",
    "run_at",
}


def _now() -> str:
    return iso(datetime.now(UTC))


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("hashtags", "options", "captions"):
        if k in d and isinstance(d[k], str):
            d[k] = json.loads(d[k])
    for k in ("autopost", "scheduled"):
        if k in d:
            d[k] = bool(d[k])
    return d


class UptikStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ plumbing
    def _open(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._conn = conn
        return self._conn

    async def _run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        def call() -> T:
            with self._lock:
                conn = self._open()
                conn.execute("BEGIN IMMEDIATE")
                try:
                    out = fn(conn)
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
                return out

        return await asyncio.to_thread(call)

    async def open(self) -> None:
        await self._run(lambda c: None)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------ accounts
    async def create_account(
        self, platform: str, label: str, runner: str = "local", device: str = ""
    ) -> dict[str, Any]:
        aid = str(uuid.uuid4())
        now = _now()

        def fn(c: sqlite3.Connection) -> dict[str, Any]:
            c.execute(
                "INSERT INTO accounts (id, platform, label, runner, device, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (aid, platform, label, runner, device, now, now),
            )
            return _row(c.execute("SELECT * FROM accounts WHERE id = ?", (aid,)).fetchone()) or {}

        return await self._run(fn)

    async def account(self, aid: str) -> dict[str, Any] | None:
        return await self._run(lambda c: _row(c.execute("SELECT * FROM accounts WHERE id = ?", (aid,)).fetchone()))

    async def accounts(self, platform: str | None = None) -> list[dict[str, Any]]:
        def fn(c: sqlite3.Connection) -> list[dict[str, Any]]:
            q, args = "SELECT * FROM accounts", tuple[str, ...]()
            if platform:
                q, args = q + " WHERE platform = ?", (platform,)
            return [r for r in (_row(x) for x in c.execute(q + " ORDER BY platform, created_at", args)) if r]

        return await self._run(fn)

    async def update_account(self, aid: str, **fields: Any) -> dict[str, Any] | None:
        bad = set(fields) - _ACCOUNT_FIELDS
        if bad:
            raise ValueError(f"unknown account fields {bad}")

        def fn(c: sqlite3.Connection) -> dict[str, Any] | None:
            if fields:
                cols = ", ".join(f"{k} = ?" for k in fields)
                vals = [int(v) if isinstance(v, bool) else v for v in fields.values()]
                c.execute(f"UPDATE accounts SET {cols}, updated_at = ? WHERE id = ?", (*vals, _now(), aid))  # noqa: S608
            return _row(c.execute("SELECT * FROM accounts WHERE id = ?", (aid,)).fetchone())

        return await self._run(fn)

    async def delete_account(self, aid: str) -> None:
        def fn(c: sqlite3.Connection) -> None:
            c.execute("DELETE FROM accounts WHERE id = ?", (aid,))
            c.execute(
                "UPDATE posts SET status = 'cancelled', last_error = 'Konto entfernt', finished_at = ?, updated_at = ? "
                "WHERE account_id = ? AND status IN ('queued', 'retry_wait', 'blocked')",
                (_now(), _now(), aid),
            )

        await self._run(fn)

    # ------------------------------------------------------------------ circuit breaker
    async def trip(self, aid: str, signal: BlockSignal, post_id: str | None = None) -> int:
        """Open the breaker of an account and block all its waiting posts. Returns how many were blocked."""
        now = _now()

        def fn(c: sqlite3.Connection) -> int:
            c.execute(
                "UPDATE accounts SET circuit_state = 'tripped', circuit_code = ?, circuit_reason = ?, circuit_at = ?, "
                "updated_at = ? WHERE id = ?",
                (signal.code, signal.label, now, now, aid),
            )
            cur = c.execute(
                "UPDATE posts SET status = 'blocked', last_error = ?, error_class = 'manual_review', updated_at = ? "
                "WHERE account_id = ? AND status IN ('queued', 'retry_wait')",
                (signal.label, now, aid),
            )
            c.execute(
                "INSERT INTO post_events (post_id, account_id, ts, kind, detail) "
                "VALUES (?, ?, ?, 'circuit_tripped', ?)",
                (post_id, aid, now, f"{signal.code}: {signal.label}"),
            )
            return int(cur.rowcount)

        return await self._run(fn)

    async def reset_circuit(self, aid: str, *, requeue: bool, actor: str) -> int:
        """Sir checked the account: close the breaker; with ``requeue`` blocked posts wait again (else stay blocked)."""
        now = _now()

        def fn(c: sqlite3.Connection) -> int:
            c.execute(
                "UPDATE accounts SET circuit_state = 'ok', circuit_code = NULL, circuit_reason = NULL, "
                "circuit_at = NULL, updated_at = ? WHERE id = ?",
                (now, aid),
            )
            n = 0
            if requeue:
                n = int(
                    c.execute(
                        "UPDATE posts SET status = 'queued', next_attempt_at = NULL, error_class = NULL, "
                        "last_error = NULL, updated_at = ? WHERE account_id = ? AND status = 'blocked'",
                        (now, aid),
                    ).rowcount
                )
            c.execute(
                "INSERT INTO post_events (post_id, account_id, ts, kind, detail) "
                "VALUES (NULL, ?, ?, 'circuit_reset', ?)",
                (aid, now, f"{actor}; {n} wieder eingereiht"),
            )
            return n

        return await self._run(fn)

    # ------------------------------------------------------------------ queue
    async def enqueue(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert posts (one per account) in one transaction. A (group, account) pair already queued is returned
        as it is – a repeated hand-over or approval never queues the same post twice."""
        now = _now()
        items = list(rows)

        def fn(c: sqlite3.Connection) -> list[dict[str, Any]]:
            out = []
            for r in items:
                existing = c.execute(
                    "SELECT * FROM posts WHERE group_id = ? AND account_id = ?", (r["group_id"], r["account_id"])
                ).fetchone()
                if existing is None:
                    pid = str(uuid.uuid4())
                    c.execute(
                        "INSERT INTO posts (id, group_id, account_id, platform, video_path, video_name, video_size, "
                        "video_sha256, video_duration, caption, hashtags, title, options, run_at, scheduled, status, "
                        "max_attempts, source, source_ref, approval_id, requested_by, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?)",
                        (
                            pid,
                            r["group_id"],
                            r["account_id"],
                            r["platform"],
                            r["video_path"],
                            r["video_name"],
                            int(r.get("video_size") or 0),
                            r.get("video_sha256") or "",
                            r.get("video_duration"),
                            r.get("caption") or "",
                            json.dumps(r.get("hashtags") or []),
                            r.get("title") or "",
                            json.dumps(r.get("options") or {}),
                            r["run_at"],
                            int(bool(r.get("scheduled"))),
                            int(r.get("max_attempts") or 3),
                            r.get("source") or "manual",
                            r.get("source_ref"),
                            r.get("approval_id"),
                            r.get("requested_by"),
                            now,
                            now,
                        ),
                    )
                    c.execute(
                        "INSERT INTO post_events (post_id, account_id, ts, kind, detail) VALUES (?, ?, ?, 'queued', ?)",
                        (pid, r["account_id"], now, f"geplant für {r['run_at']}" if r.get("scheduled") else "sofort"),
                    )
                    existing = c.execute("SELECT * FROM posts WHERE id = ?", (pid,)).fetchone()
                out.append(_row(existing) or {})
            return out

        return await self._run(fn)

    async def post(self, pid: str) -> dict[str, Any] | None:
        return await self._run(lambda c: _row(c.execute("SELECT * FROM posts WHERE id = ?", (pid,)).fetchone()))

    async def posts(
        self,
        statuses: Iterable[str] | None = None,
        *,
        limit: int = 100,
        account_id: str | None = None,
        newest_first: bool = False,
    ) -> list[dict[str, Any]]:
        st = list(statuses or [])

        def fn(c: sqlite3.Connection) -> list[dict[str, Any]]:
            where, args = [], []
            if st:
                where.append(f"status IN ({', '.join('?' * len(st))})")
                args += st
            if account_id:
                where.append("account_id = ?")
                args.append(account_id)
            order = "updated_at DESC" if newest_first else "run_at, rowid"
            # only fixed column names and placeholders are joined here; values go in as parameters
            cond = " WHERE " + " AND ".join(where) if where else ""
            q = f"SELECT * FROM posts{cond} ORDER BY {order} LIMIT ?"  # noqa: S608
            return [r for r in (_row(x) for x in c.execute(q, (*args, limit))) if r]

        return await self._run(fn)

    async def due(self, now: datetime, limit: int = 50) -> list[dict[str, Any]]:
        """Posts whose time has come, oldest planned first (then in insertion order)."""
        t = iso(now)
        return await self._run(
            lambda c: [
                r
                for r in (
                    _row(x)
                    for x in c.execute(
                        "SELECT * FROM posts WHERE (status = 'queued' AND run_at <= ?) "
                        "OR (status = 'retry_wait' AND next_attempt_at <= ?) "
                        "ORDER BY COALESCE(next_attempt_at, run_at), run_at, rowid LIMIT ?",
                        (t, t, limit),
                    )
                )
                if r
            ]
        )

    async def lease(self, pid: str, now: datetime) -> dict[str, Any] | None:
        """Atomically take a due post (queued/retry_wait → running, attempts + 1); None if someone else has it."""
        t = iso(now)

        def fn(c: sqlite3.Connection) -> dict[str, Any] | None:
            cur = c.execute(
                "UPDATE posts SET status = 'running', stage = 'start', attempts = attempts + 1, started_at = ?, "
                "updated_at = ? WHERE id = ? AND status IN ('queued', 'retry_wait')",
                (t, t, pid),
            )
            if cur.rowcount != 1:
                return None
            return _row(c.execute("SELECT * FROM posts WHERE id = ?", (pid,)).fetchone())

        return await self._run(fn)

    async def update_post(self, pid: str, **fields: Any) -> dict[str, Any] | None:
        bad = set(fields) - _POST_FIELDS
        if bad:
            raise ValueError(f"unknown post fields {bad}")
        now = _now()
        if fields.get("status") in FINISHED_STATUSES and "finished_at" not in fields:
            fields["finished_at"] = now

        def fn(c: sqlite3.Connection) -> dict[str, Any] | None:
            cols = ", ".join(f"{k} = ?" for k in fields)
            c.execute(f"UPDATE posts SET {cols}, updated_at = ? WHERE id = ?", (*fields.values(), now, pid))  # noqa: S608
            return _row(c.execute("SELECT * FROM posts WHERE id = ?", (pid,)).fetchone())

        return await self._run(fn)

    async def cancel(self, pid: str) -> bool:
        now = _now()

        def fn(c: sqlite3.Connection) -> bool:
            cur = c.execute(
                "UPDATE posts SET status = 'cancelled', finished_at = ?, updated_at = ? "
                "WHERE id = ? AND status IN ('queued', 'retry_wait', 'blocked')",
                (now, now, pid),
            )
            if cur.rowcount:
                c.execute(
                    "INSERT INTO post_events (post_id, account_id, ts, kind, detail) "
                    "SELECT id, account_id, ?, 'cancelled', '' FROM posts WHERE id = ?",
                    (now, pid),
                )
            return bool(cur.rowcount)

        return await self._run(fn)

    async def recover(self) -> dict[str, list[str]]:
        """After a restart: posts left ``running`` wait again – except those that were already submitting (whether
        they went out is unknown; repeating could post twice), which go to ``needs_review``."""
        now = _now()

        def fn(c: sqlite3.Connection) -> dict[str, list[str]]:
            rows = c.execute("SELECT id, stage FROM posts WHERE status = 'running'").fetchall()
            requeued, review = [], []
            for r in rows:
                if r["stage"] == SUBMITTING:
                    c.execute(
                        "UPDATE posts SET status = 'needs_review', last_error = ?, finished_at = ?, updated_at = ? "
                        "WHERE id = ?",
                        ("Neustart während des Veröffentlichens – bitte auf der Plattform prüfen", now, now, r["id"]),
                    )
                    review.append(r["id"])
                else:
                    c.execute(
                        "UPDATE posts SET status = 'queued', stage = '', next_attempt_at = NULL, updated_at = ? "
                        "WHERE id = ?",
                        (now, r["id"]),
                    )
                    requeued.append(r["id"])
                c.execute(
                    "INSERT INTO post_events (post_id, account_id, ts, kind, detail) "
                    "SELECT id, account_id, ?, 'recovered', ? FROM posts WHERE id = ?",
                    (now, "needs_review" if r["stage"] == SUBMITTING else "queued", r["id"]),
                )
            return {"requeued": requeued, "needs_review": review}

        return await self._run(fn)

    async def event(self, pid: str | None, aid: str | None, kind: str, detail: str = "") -> None:
        await self._run(
            lambda c: c.execute(
                "INSERT INTO post_events (post_id, account_id, ts, kind, detail) VALUES (?, ?, ?, ?, ?)",
                (pid, aid, _now(), kind, detail[:500]),
            )
        )

    async def events(self, pid: str) -> list[dict[str, Any]]:
        return await self._run(
            lambda c: [dict(r) for r in c.execute("SELECT * FROM post_events WHERE post_id = ? ORDER BY id", (pid,))]
        )

    async def counts(self) -> dict[str, int]:
        return await self._run(
            lambda c: {r[0]: int(r[1]) for r in c.execute("SELECT status, COUNT(*) FROM posts GROUP BY status")}
        )

    # ------------------------------------------------------------------ inbox
    async def add_inbox(self, **f: Any) -> dict[str, Any]:
        def fn(c: sqlite3.Connection) -> dict[str, Any]:
            row = c.execute(
                "SELECT * FROM inbox WHERE source = ? AND source_ref = ?", (f["source"], f["source_ref"])
            ).fetchone()
            if row is None:
                iid = str(uuid.uuid4())
                c.execute(
                    "INSERT INTO inbox (id, source, source_ref, path, name, size, duration, title, captions, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        iid,
                        f["source"],
                        f["source_ref"],
                        f["path"],
                        f["name"],
                        int(f.get("size") or 0),
                        f.get("duration"),
                        f.get("title") or "",
                        json.dumps(f.get("captions") or {}),
                        _now(),
                    ),
                )
                row = c.execute("SELECT * FROM inbox WHERE id = ?", (iid,)).fetchone()
            return _row(row) or {}

        return await self._run(fn)

    async def inbox_item(self, iid: str) -> dict[str, Any] | None:
        return await self._run(lambda c: _row(c.execute("SELECT * FROM inbox WHERE id = ?", (iid,)).fetchone()))

    async def inbox(self, limit: int = 50) -> list[dict[str, Any]]:
        return await self._run(
            lambda c: [
                r
                for r in (_row(x) for x in c.execute("SELECT * FROM inbox ORDER BY created_at DESC LIMIT ?", (limit,)))
                if r
            ]
        )

    async def remove_inbox(self, iid: str) -> dict[str, Any] | None:
        def fn(c: sqlite3.Connection) -> dict[str, Any] | None:
            row = _row(c.execute("SELECT * FROM inbox WHERE id = ?", (iid,)).fetchone())
            c.execute("DELETE FROM inbox WHERE id = ?", (iid,))
            return row

        return await self._run(fn)
