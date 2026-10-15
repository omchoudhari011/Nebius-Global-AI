"""sqlite3 connection helpers. init_db() runs schema.sql on an empty database and upgrades
an older one with MIGRATIONS.

Every connection turns on foreign keys (SQLite's default is off, which would make every
REFERENCES / ON DELETE in schema.sql decorative) and waits up to 5 s for a lock, because
API requests and scheduler jobs write concurrently. Transactions are kept short: callers
open one with connect(), do one step, and commit, so a slow model call never holds the
write lock.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import BACKEND_DIR, get_settings

SCHEMA_PATH = BACKEND_DIR / "schema.sql"
SCHEMA_VERSION = 5  # must match the PRAGMA user_version at the end of schema.sql

# Upgrades for databases made by an older schema.sql: {from version: script}. Each script
# runs in one transaction and ends by setting user_version to the next version, so a failed
# upgrade leaves the file exactly as it was.
MIGRATIONS = {
    2: """
        BEGIN;
        ALTER TABLE message_memories ADD COLUMN matched INTEGER NOT NULL DEFAULT 1 CHECK (matched IN (0, 1));
        CREATE TABLE message_recalls (
          message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
          recalled_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
          PRIMARY KEY (message_id, recalled_id)
        );
        CREATE INDEX message_recalls_by_recalled ON message_recalls(recalled_id);
        PRAGMA user_version = 3;
        COMMIT;
    """,
    3: """
        BEGIN;
        CREATE TABLE memory_vectors (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
          model TEXT NOT NULL,
          vector BLOB,
          UNIQUE (memory_id, model)
        );
        CREATE TABLE message_vectors (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
          model TEXT NOT NULL,
          vector BLOB,
          UNIQUE (message_id, model)
        );
        PRAGMA user_version = 4;
        COMMIT;
    """,
    4: """
        BEGIN;
        ALTER TABLE conversations ADD COLUMN private INTEGER NOT NULL DEFAULT 0 CHECK (private IN (0, 1));
        ALTER TABLE messages ADD COLUMN checks BLOB;
        CREATE TABLE user_settings (
          user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          retention_days INTEGER CHECK (retention_days IS NULL OR retention_days BETWEEN 1 AND 3650),
          redact_secrets TEXT NOT NULL DEFAULT 'auto' CHECK (redact_secrets IN ('auto', 'on', 'off')),
          license_key TEXT,
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE usage_daily (
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          day TEXT NOT NULL,
          chat_turns INTEGER NOT NULL DEFAULT 0,
          task_runs INTEGER NOT NULL DEFAULT 0,
          model_calls INTEGER NOT NULL DEFAULT 0,
          estimated_calls INTEGER NOT NULL DEFAULT 0,
          prompt_tokens INTEGER NOT NULL DEFAULT 0,
          completion_tokens INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (user_id, day)
        );
        PRAGMA user_version = 5;
        COMMIT;
    """,
}


def db_path() -> Path:
    return get_settings().database_path


def get_connection() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    # Zero deleted content instead of leaving it in free pages, so a deleted memory's
    # plaintext search copy doesn't stay readable in the file (see memory.purge_search_index).
    conn.execute("PRAGMA secure_delete = ON")
    return conn


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """One transaction: commits on success, rolls back on any error, always closes."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def use(conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
    """Join the caller's transaction when given a connection, otherwise open one."""
    if conn is not None:
        yield conn
    else:
        with connect() as own:
            yield own


def init_db() -> None:
    conn = get_connection()
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            existing = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
            ).fetchone()
            if existing is not None:
                raise RuntimeError(
                    f"{db_path().name} was created by the v1 schema; delete it so schema v{SCHEMA_VERSION} can be applied"
                )
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            return
        while version in MIGRATIONS and version < SCHEMA_VERSION:
            try:
                conn.executescript(MIGRATIONS[version])
            except sqlite3.Error:
                if conn.in_transaction:
                    conn.rollback()
                raise
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise RuntimeError(f"{db_path().name} is schema v{version}; this code expects v{SCHEMA_VERSION}")
    finally:
        conn.close()


# Timestamps -------------------------------------------------------------------------
# The database stores UTC as 'YYYY-MM-DD HH:MM:SS' (what datetime('now') produces), so
# values written from Python use the same shape and compare correctly as strings.

DB_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def to_db_time(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("naive datetime; pass an aware one")
    return moment.astimezone(timezone.utc).strftime(DB_TIME_FORMAT)


def from_db_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, DB_TIME_FORMAT).replace(tzinfo=timezone.utc)


def iso(value: str | None) -> str | None:
    """'2026-09-27 03:30:00' -> '2026-09-27T03:30:00Z' for API responses."""
    return value.replace(" ", "T") + "Z" if value else None
