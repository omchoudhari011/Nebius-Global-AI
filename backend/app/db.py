"""sqlite3 connection helper. Runs schema.sql on first launch. Step 1 — see docs/PLAN.md."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "custodian.db"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if existing is None:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.commit()
    conn.close()
