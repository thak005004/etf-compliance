"""SQLite audit log: records every compliance run and exception acknowledgments."""

from __future__ import annotations
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("audit.db")


def _get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at        TEXT NOT NULL,
            filename      TEXT NOT NULL,
            file_sha256   TEXT,
            rules_version TEXT,
            rules_sha256  TEXT,
            error_count   INTEGER NOT NULL DEFAULT 0,
            fail_count    INTEGER NOT NULL DEFAULT 0,
            pass          INTEGER NOT NULL DEFAULT 0
        )
    """)
    # Forward-compatible migration for databases created before rules_sha256 was added
    try:
        conn.execute("ALTER TABLE runs ADD COLUMN rules_sha256 TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS acknowledgments (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id           INTEGER NOT NULL REFERENCES runs(id),
            exception_index  INTEGER NOT NULL,
            rule             TEXT NOT NULL,
            ticker           TEXT,
            acknowledged_by  TEXT NOT NULL,
            reason           TEXT NOT NULL,
            acknowledged_at  TEXT NOT NULL,
            UNIQUE(run_id, exception_index)
        )
    """)
    conn.commit()
    return conn


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def log_run(
    filename: str,
    file_bytes: bytes | None,
    rules_version: str,
    error_count: int,
    fail_count: int,
    rules_sha256: str | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """Insert a run record and return its row id."""
    file_sha256 = sha256_of_bytes(file_bytes) if file_bytes is not None else None
    passed = 1 if (error_count == 0 and fail_count == 0) else 0
    conn = _get_conn(db_path)
    cur = conn.execute(
        """
        INSERT INTO runs
            (run_at, filename, file_sha256, rules_version, rules_sha256,
             error_count, fail_count, pass)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            filename,
            file_sha256,
            rules_version,
            rules_sha256,
            error_count,
            fail_count,
            passed,
        ),
    )
    conn.commit()
    run_id = cur.lastrowid
    conn.close()
    return run_id


def log_acknowledgment(
    run_id: int,
    exception_index: int,
    rule: str,
    ticker: str | None,
    acknowledged_by: str,
    reason: str,
    db_path: Path = DB_PATH,
) -> None:
    """Write (or overwrite) an exception acknowledgment for a given run."""
    conn = _get_conn(db_path)
    conn.execute(
        """
        INSERT OR REPLACE INTO acknowledgments
            (run_id, exception_index, rule, ticker,
             acknowledged_by, reason, acknowledged_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            exception_index,
            rule,
            ticker,
            acknowledged_by,
            reason,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def fetch_acknowledgments(run_id: int, db_path: Path = DB_PATH) -> list[dict]:
    """Return all acknowledgments for a run, ordered by exception_index."""
    conn = _get_conn(db_path)
    cur = conn.execute(
        """
        SELECT exception_index, rule, ticker,
               acknowledged_by, reason, acknowledged_at
        FROM acknowledgments
        WHERE run_id = ?
        ORDER BY exception_index
        """,
        (run_id,),
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    conn.close()
    return rows


def fetch_runs(limit: int = 50, db_path: Path = DB_PATH) -> list[dict]:
    """Return recent runs as a list of dicts, newest first."""
    conn = _get_conn(db_path)
    cur = conn.execute(
        """
        SELECT id, run_at, filename, file_sha256, rules_sha256,
               rules_version, error_count, fail_count, pass
        FROM runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    conn.close()
    return rows
