"""Tool: your routines - one phrase that runs several PC actions ("work mode", "good night"),
kept in DATA_DIR/tools/routines/routines.db.

The tool only stores them. Saying a routine's name runs it on the server (app/routines.py),
each step through the same gate as any tool call, after one confirmation on the PC for the whole
list when a step would otherwise ask (closing an app, sorting a folder). The steps were checked
before they got here (schemas.SaveRoutineArgs): each is a short command already turned into the
exact tool call it stands for.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

MAX_ROUTINES = 50


def connect(data_dir: str | Path) -> sqlite3.Connection:
    """The routines database; also read and written by app/routines.py (outside the sandbox)."""
    conn = sqlite3.connect(Path(data_dir) / "routines.db", timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA secure_delete = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS routines (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          phrases TEXT NOT NULL DEFAULT '[]',   -- JSON: other ways to say it
          steps TEXT NOT NULL,                  -- JSON: [{function, args, said}]
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          last_run_at TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS routines_by_name ON routines(user_id, name COLLATE NOCASE);
        """
    )
    return conn


def describe(row: sqlite3.Row) -> dict:
    steps = json.loads(row["steps"])
    return {
        "id": row["id"],
        "name": row["name"],
        "phrases": json.loads(row["phrases"]),
        "steps": [s.get("said") or s["function"] for s in steps],
        "created_at": row["created_at"].replace(" ", "T") + "Z",
        "last_run_at": row["last_run_at"].replace(" ", "T") + "Z" if row["last_run_at"] else None,
    }


def store(conn: sqlite3.Connection, user_id: int, name: str, parsed: list[dict], phrases: list[str]) -> tuple[dict, bool]:
    """Insert or replace (same name, any case) a routine. Returns it and whether one was replaced."""
    name = " ".join(name.split())
    with conn:
        old = conn.execute("SELECT id FROM routines WHERE user_id = ? AND name = ? COLLATE NOCASE", (user_id, name)).fetchone()
        if old is None:
            count = conn.execute("SELECT COUNT(*) FROM routines WHERE user_id = ?", (user_id,)).fetchone()[0]
            if count >= MAX_ROUTINES:
                raise ValueError(f"there are already {MAX_ROUTINES} routines; delete one first")
            routine_id = conn.execute(
                "INSERT INTO routines (user_id, name, phrases, steps) VALUES (?, ?, ?, ?)",
                (user_id, name, json.dumps(phrases), json.dumps(parsed)),
            ).lastrowid
        else:
            routine_id = old["id"]
            conn.execute(
                "UPDATE routines SET name = ?, phrases = ?, steps = ?, updated_at = datetime('now') WHERE id = ?",
                (name, json.dumps(phrases), json.dumps(parsed), routine_id),
            )
    row = conn.execute("SELECT * FROM routines WHERE id = ?", (routine_id,)).fetchone()
    return describe(row), old is not None


def save_routine(ctx: dict, name: str, steps: list[str], phrases: list[str] | None = None, parsed: list[dict] | None = None) -> dict:
    if not parsed or len(parsed) != len(steps):
        raise ValueError("the steps weren't checked; nothing was saved")
    conn = connect(ctx["data_dir"])
    try:
        saved, replaced = store(conn, ctx["user_id"], name, parsed, [" ".join(p.split()) for p in phrases or []])
    finally:
        conn.close()
    return {"saved": saved, "replaced": replaced, "how_to_run": f"say “{saved['name']}”"}


def list_routines(ctx: dict) -> dict:
    conn = connect(ctx["data_dir"])
    try:
        rows = conn.execute("SELECT * FROM routines WHERE user_id = ? ORDER BY name COLLATE NOCASE", (ctx["user_id"],)).fetchall()
    finally:
        conn.close()
    return {"routines": [describe(r) for r in rows]}


def delete_routine(ctx: dict, name: str) -> dict:
    conn = connect(ctx["data_dir"])
    try:
        with conn:
            removed = conn.execute("DELETE FROM routines WHERE user_id = ? AND name = ? COLLATE NOCASE",
                                   (ctx["user_id"], " ".join(name.split()))).rowcount
    finally:
        conn.close()
    if not removed:
        raise ValueError(f"there's no routine called {name!r}")
    return {"deleted": name}
