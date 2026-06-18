"""SQLite-backed state: today's instances and their history.

The template says what the ideal day looks like; this module records what
actually happened. One row per (block, date). Adherence is just a query over
``instances``. All functions take an open connection so they stay easy to test.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from .template import Block

PENDING = "pending"
DONE = "done"
SKIPPED = "skipped"
MISSED = "missed"
TERMINAL = {DONE, SKIPPED, MISSED}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS instances (
    id              INTEGER PRIMARY KEY,
    block_name      TEXT NOT NULL,
    date            TEXT NOT NULL,
    scheduled_start TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    responded_at    TEXT,
    nag_count       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (block_name, date)
);
CREATE TABLE IF NOT EXISTS step_state (
    instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    step_text   TEXT NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instance_id, position)
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


def upsert_instance(
    conn: sqlite3.Connection, block: Block, date: dt.date, start: dt.datetime
) -> tuple[int, bool]:
    """Ensure an instance row (and its steps) exist. Returns (id, created)."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO instances (block_name, date, scheduled_start) "
        "VALUES (?, ?, ?)",
        (block.name, date.isoformat(), start.isoformat()),
    )
    created = cur.rowcount == 1
    iid = conn.execute(
        "SELECT id FROM instances WHERE block_name = ? AND date = ?",
        (block.name, date.isoformat()),
    ).fetchone()["id"]
    if created:
        conn.executemany(
            "INSERT OR IGNORE INTO step_state (instance_id, position, step_text) "
            "VALUES (?, ?, ?)",
            [(iid, pos, text) for pos, text in enumerate(block.steps)],
        )
    return iid, created


def get_instance(conn: sqlite3.Connection, iid: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM instances WHERE id = ?", (iid,)).fetchone()


def find_instance(
    conn: sqlite3.Connection, block_name: str, date: dt.date
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM instances WHERE block_name = ? AND date = ?",
        (block_name, date.isoformat()),
    ).fetchone()


def instances_on(conn: sqlite3.Connection, date: dt.date) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM instances WHERE date = ? ORDER BY scheduled_start",
        (date.isoformat(),),
    ).fetchall()


def set_status(
    conn: sqlite3.Connection, iid: int, status: str, *, now: dt.datetime | None = None
) -> None:
    responded = now.isoformat() if (now is not None and status in TERMINAL) else None
    conn.execute(
        "UPDATE instances SET status = ?, responded_at = ? WHERE id = ?",
        (status, responded, iid),
    )


def reschedule(conn: sqlite3.Connection, iid: int, start: dt.datetime) -> None:
    conn.execute(
        "UPDATE instances SET scheduled_start = ?, status = 'pending', nag_count = 0 "
        "WHERE id = ?",
        (start.isoformat(), iid),
    )


def bump_nag(conn: sqlite3.Connection, iid: int) -> int:
    conn.execute("UPDATE instances SET nag_count = nag_count + 1 WHERE id = ?", (iid,))
    return conn.execute(
        "SELECT nag_count FROM instances WHERE id = ?", (iid,)
    ).fetchone()["nag_count"]


def mark_missed_before(
    conn: sqlite3.Connection, date: dt.date, *, now: dt.datetime | None = None
) -> int:
    """Any still-pending instance from a past day counts as missed."""
    cur = conn.execute(
        "UPDATE instances SET status = 'missed', responded_at = ? "
        "WHERE status = 'pending' AND date < ?",
        (now.isoformat() if now is not None else None, date.isoformat()),
    )
    return cur.rowcount


def steps(conn: sqlite3.Connection, iid: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM step_state WHERE instance_id = ? ORDER BY position", (iid,)
    ).fetchall()


def toggle_step(conn: sqlite3.Connection, iid: int, position: int) -> None:
    conn.execute(
        "UPDATE step_state SET done = 1 - done WHERE instance_id = ? AND position = ?",
        (iid, position),
    )


def first_undone_step(conn: sqlite3.Connection, iid: int) -> str | None:
    row = conn.execute(
        "SELECT step_text FROM step_state WHERE instance_id = ? AND done = 0 "
        "ORDER BY position LIMIT 1",
        (iid,),
    ).fetchone()
    return row["step_text"] if row else None


def adherence(conn: sqlite3.Connection, since: dt.date) -> list[sqlite3.Row]:
    """Per-block counts of done vs total since ``since`` (inclusive)."""
    return conn.execute(
        "SELECT block_name, "
        "  SUM(status = 'done') AS done, "
        "  COUNT(*) AS total "
        "FROM instances WHERE date >= ? "
        "GROUP BY block_name ORDER BY block_name",
        (since.isoformat(),),
    ).fetchall()
