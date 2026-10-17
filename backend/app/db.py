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
SCHEMA_VERSION = 8  # must match the PRAGMA user_version at the end of schema.sql

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
    5: """
        BEGIN;
        CREATE TABLE sessions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          token_hash TEXT NOT NULL UNIQUE,
          client TEXT NOT NULL CHECK (client IN ('web', 'native')),
          device TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          last_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX sessions_by_user ON sessions(user_id);
        PRAGMA user_version = 6;
        COMMIT;
    """,
    6: """
        BEGIN;
        ALTER TABLE messages ADD COLUMN trace BLOB;
        -- Things Custodian tells you without being asked: reminders coming due, a scheduled task's
        -- result (app/notifications.py). Shown in the app's inbox; optionally pushed to a phone.
        CREATE TABLE notifications (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          kind TEXT NOT NULL CHECK (kind IN ('reminder', 'task', 'system')),
          title BLOB NOT NULL,                   -- Fernet
          body BLOB,                             -- Fernet
          ref TEXT,                              -- what it points at, e.g. 'reminder:12', 'conversation:40'
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          read_at TEXT
        );
        CREATE INDEX notifications_by_user ON notifications(user_id, read_at, id);

        -- Your verdict on a reply (app/feedback.py). With share = 1 it may become fine-tuning data
        -- (backend/finetune/from_feedback.py), built from the stored prompt in messages.trace.
        CREATE TABLE message_feedback (
          message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
          rating INTEGER NOT NULL CHECK (rating IN (-1, 1)),
          correction BLOB,                       -- Fernet: what the reply should have said
          share INTEGER NOT NULL DEFAULT 0 CHECK (share IN (0, 1)),
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Connected accounts (app/integrations/google.py). Tokens are Fernet-encrypted and never
        -- reach a tool: the gate hands a tool only a short-lived access token.
        CREATE TABLE integrations (
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          provider TEXT NOT NULL CHECK (provider IN ('google')),
          account TEXT,                          -- the connected address, for display
          scopes TEXT NOT NULL,
          refresh_token BLOB NOT NULL,           -- Fernet
          access_token BLOB,                     -- Fernet
          expires_at TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          PRIMARY KEY (user_id, provider)
        );
        INSERT INTO tools (name) VALUES
          ('reminders'),                         -- app/tools/reminders_tool.py
          ('documents'),                         -- app/tools/documents_tool.py (files you upload)
          ('gcal'),                              -- app/tools/google_tools.py: Google Calendar
          ('gmail'),                             -- app/tools/google_tools.py: Gmail (read only)
          ('gdrive');                            -- app/tools/google_tools.py: Google Drive (read only)

        INSERT INTO tool_scopes (tool_id, scope, description) VALUES
          ((SELECT id FROM tools WHERE name = 'reminders'), 'read',  'list_reminders'),
          ((SELECT id FROM tools WHERE name = 'reminders'), 'write', 'create_reminder'),
          ((SELECT id FROM tools WHERE name = 'documents'), 'read',  'search_documents, read_document'),
          ((SELECT id FROM tools WHERE name = 'gcal'),      'read',  'list_events'),
          ((SELECT id FROM tools WHERE name = 'gcal'),      'write', 'create_event'),
          ((SELECT id FROM tools WHERE name = 'gmail'),     'read',  'search_messages, read_message'),
          ((SELECT id FROM tools WHERE name = 'gdrive'),    'read',  'search_files, read_file');
        PRAGMA user_version = 7;
        COMMIT;
    """,
    7: """
        BEGIN;
        INSERT INTO tools (name) VALUES ('web'), ('pc');
        INSERT INTO tool_scopes (tool_id, scope, description) VALUES
          ((SELECT id FROM tools WHERE name = 'web'), 'read',  'search, read_page'),
          ((SELECT id FROM tools WHERE name = 'pc'),  'read',  'system info, files, windows, clipboard, screenshot'),
          ((SELECT id FROM tools WHERE name = 'pc'),  'write', 'open apps, media, volume, lock, type, click, files');
        PRAGMA user_version = 8;
        COMMIT;
    """,
}


def db_path() -> Path:
    return get_settings().database_path


_made_dirs: set[Path] = set()


def get_connection() -> sqlite3.Connection:
    path = db_path()
    if path.parent not in _made_dirs:
        path.parent.mkdir(parents=True, exist_ok=True)
        _made_dirs.add(path.parent)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    # With WAL (schema.sql), NORMAL syncs at checkpoints rather than on every commit: a turn
    # makes ~20 small commits, and a power cut can lose at most the last ones, never corrupt.
    conn.execute("PRAGMA synchronous = NORMAL")
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
