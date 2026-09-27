"""sqlite3 connection helper. Runs schema.sql on first launch. Step 1 — see docs/PLAN.md."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "custodian.db"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"
SCHEMA_VERSION = 2  # must match the PRAGMA user_version at the end of schema.sql


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # SQLite has foreign keys off by default, per connection; without this every
    # REFERENCES / ON DELETE in schema.sql is ignored.
    conn.execute("PRAGMA foreign_keys = ON")
    # The API and scheduler write concurrently; wait for the lock instead of raising "database is locked".
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db() -> None:
    conn = get_connection()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if existing is not None:
            conn.close()
            raise RuntimeError(
                f"{DB_PATH.name} was created by the v1 schema; delete it so schema v{SCHEMA_VERSION} can be applied"
            )
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    elif version != SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(f"{DB_PATH.name} is schema v{version}; this code expects v{SCHEMA_VERSION}")
    conn.close()
