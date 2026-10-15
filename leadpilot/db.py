"""SQLite persistence: leads, per-step checkpoints, audit events, job queue, local CRM, outbox.

SQLite keeps the project zero-infrastructure (it runs on a Hugging Face Space or a $5 VPS),
while WAL mode + short transactions are enough for the single-worker queue used here.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id               TEXT PRIMARY KEY,
    idempotency_key  TEXT NOT NULL UNIQUE,
    received_at      REAL NOT NULL,
    updated_at       REAL NOT NULL,
    source           TEXT NOT NULL,
    email            TEXT NOT NULL,
    name             TEXT,
    company          TEXT,
    payload          TEXT NOT NULL,
    status           TEXT NOT NULL,          -- queued | processing | done | needs_review | dead
    tier             TEXT,
    score            INTEGER,
    needs_review     INTEGER NOT NULL DEFAULT 0,
    review_reasons   TEXT,
    enrichment       TEXT,
    qualification    TEXT,
    qualifier        TEXT,
    decision         TEXT,
    reply            TEXT,
    reply_status     TEXT NOT NULL DEFAULT 'none',  -- none | draft | sent | skipped
    crm_contact_id   TEXT,
    crm_deal_id      TEXT,
    cost_usd         REAL NOT NULL DEFAULT 0,
    human_touches    INTEGER NOT NULL DEFAULT 0,
    completed_at     REAL,
    first_response_at REAL,
    error            TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);

CREATE TABLE IF NOT EXISTS steps (
    lead_id     TEXT NOT NULL,
    step        TEXT NOT NULL,
    output      TEXT,
    duration_ms REAL NOT NULL,
    attempt     INTEGER NOT NULL,
    finished_at REAL NOT NULL,
    PRIMARY KEY (lead_id, step)
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id TEXT,
    ts      REAL NOT NULL,
    level   TEXT NOT NULL,      -- info | warn | error
    kind    TEXT NOT NULL,
    message TEXT NOT NULL,
    data    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_lead ON events(lead_id);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL,   -- queued | running | done | dead
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_run_at REAL NOT NULL,
    locked_at   REAL,
    last_error  TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_ready ON jobs(status, next_run_at);

CREATE TABLE IF NOT EXISTS crm_contacts (
    id         TEXT PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE,
    name       TEXT,
    company    TEXT,
    properties TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS crm_deals (
    id          TEXT PRIMARY KEY,
    contact_id  TEXT NOT NULL,
    lead_id     TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    amount      INTEGER,
    stage       TEXT NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id   TEXT NOT NULL UNIQUE,
    recipient TEXT NOT NULL,
    subject   TEXT NOT NULL,
    body      TEXT NOT NULL,
    transport TEXT NOT NULL,
    sent_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id   TEXT,
    channel   TEXT NOT NULL,
    text      TEXT NOT NULL,
    delivered INTEGER NOT NULL,
    ts        REAL NOT NULL,
    UNIQUE (lead_id, channel)
);
"""

JSON_COLUMNS = {"payload", "review_reasons", "enrichment", "qualification", "decision", "reply"}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # One shared connection guarded by a lock: simple, and correct for a single
        # web process + one worker thread. (":memory:" databases also need this.)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._decode(dict(r)) for r in rows]

    def query_one(self, sql: str, params: tuple = ()) -> Optional[dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        for key in JSON_COLUMNS & row.keys():
            if row[key] is not None:
                row[key] = json.loads(row[key])
        return row

    # ---------------------------------------------------------------- leads
    def insert_lead_with_job(self, lead_id: str, idem_key: str, payload: dict[str, Any]) -> bool:
        """Insert a lead and enqueue its job atomically. Returns False on duplicate key."""
        now = time.time()
        try:
            with self.tx() as c:
                c.execute(
                    """INSERT INTO leads (id, idempotency_key, received_at, updated_at, source, email,
                                          name, company, payload, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued')""",
                    (lead_id, idem_key, now, now, payload["source"], payload["email"],
                     payload.get("name"), payload.get("company"), dumps(payload)),
                )
                c.execute(
                    "INSERT INTO jobs (lead_id, status, next_run_at) VALUES (?, 'queued', ?)",
                    (lead_id, now),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def get_lead(self, lead_id: str) -> Optional[dict[str, Any]]:
        return self.query_one("SELECT * FROM leads WHERE id = ?", (lead_id,))

    def get_lead_by_key(self, idem_key: str) -> Optional[dict[str, Any]]:
        return self.query_one("SELECT * FROM leads WHERE idempotency_key = ?", (idem_key,))

    def update_lead(self, lead_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = tuple(dumps(v) if k in JSON_COLUMNS and v is not None else v for k, v in fields.items())
        self.execute(f"UPDATE leads SET {cols} WHERE id = ?", values + (lead_id,))

    def count_prior_leads(self, email: str, before: float, lead_id: str) -> int:
        row = self.query_one(
            "SELECT COUNT(*) AS n FROM leads WHERE email = ? AND received_at <= ? AND id != ?",
            (email, before, lead_id),
        )
        return int(row["n"]) if row else 0

    # ---------------------------------------------------------------- steps (checkpoints)
    def get_steps(self, lead_id: str) -> dict[str, Any]:
        rows = self.query("SELECT step, output FROM steps WHERE lead_id = ?", (lead_id,))
        return {r["step"]: json.loads(r["output"]) if r["output"] else None for r in rows}

    def save_step(self, lead_id: str, step: str, output: Any, duration_ms: float, attempt: int) -> None:
        self.execute(
            """INSERT OR REPLACE INTO steps (lead_id, step, output, duration_ms, attempt, finished_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (lead_id, step, dumps(output), duration_ms, attempt, time.time()),
        )

    def step_timings(self, lead_id: str) -> list[dict[str, Any]]:
        return self.query(
            "SELECT step, duration_ms, attempt, finished_at FROM steps WHERE lead_id = ? ORDER BY finished_at",
            (lead_id,),
        )

    # ---------------------------------------------------------------- events (audit log)
    def log(self, lead_id: Optional[str], kind: str, message: str,
            level: str = "info", data: Any = None) -> None:
        self.execute(
            "INSERT INTO events (lead_id, ts, level, kind, message, data) VALUES (?, ?, ?, ?, ?, ?)",
            (lead_id, time.time(), level, kind, message, dumps(data) if data is not None else None),
        )

    def events(self, lead_id: str) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM events WHERE lead_id = ? ORDER BY id", (lead_id,))
        for r in rows:
            r["data"] = json.loads(r["data"]) if r["data"] else None
        return rows

    # ---------------------------------------------------------------- jobs
    def claim_job(self, lease_seconds: float = 120.0) -> Optional[dict[str, Any]]:
        """Atomically claim the next ready job. Jobs whose lease expired (worker crashed
        mid-run) become claimable again, so nothing is stuck in 'running' forever."""
        now = time.time()
        with self.tx() as c:
            row = c.execute(
                """SELECT * FROM jobs
                   WHERE (status = 'queued' AND next_run_at <= ?)
                      OR (status = 'running' AND locked_at < ?)
                   ORDER BY next_run_at LIMIT 1""",
                (now, now - lease_seconds),
            ).fetchone()
            if row is None:
                return None
            c.execute(
                "UPDATE jobs SET status = 'running', locked_at = ?, attempts = attempts + 1 WHERE id = ?",
                (now, row["id"]),
            )
        job = dict(row)
        job["attempts"] += 1
        return job

    def finish_job(self, job_id: int) -> None:
        self.execute("UPDATE jobs SET status = 'done', locked_at = NULL, last_error = NULL WHERE id = ?", (job_id,))

    def retry_job(self, job_id: int, delay: float, error: str) -> None:
        self.execute(
            "UPDATE jobs SET status = 'queued', locked_at = NULL, next_run_at = ?, last_error = ? WHERE id = ?",
            (time.time() + delay, error, job_id),
        )

    def dead_letter_job(self, job_id: int, error: str) -> None:
        self.execute("UPDATE jobs SET status = 'dead', locked_at = NULL, last_error = ? WHERE id = ?", (error, job_id))

    def requeue_lead(self, lead_id: str) -> bool:
        cur = self.execute(
            """UPDATE jobs SET status = 'queued', attempts = 0, next_run_at = ?, last_error = NULL
               WHERE lead_id = ? AND status = 'dead'""",
            (time.time(), lead_id),
        )
        return cur.rowcount > 0

    def get_job(self, lead_id: str) -> Optional[dict[str, Any]]:
        return self.query_one("SELECT * FROM jobs WHERE lead_id = ?", (lead_id,))
