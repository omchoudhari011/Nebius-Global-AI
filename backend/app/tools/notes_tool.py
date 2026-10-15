"""Tool: local notes kept in DATA_DIR/tools/notes/notes.db. Same gated pattern as
calendar_tool.py, which shows the tool interface generalizes rather than being a one-off.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MAX_BODY_RETURNED = 2000


def _connect(ctx: dict) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(ctx["data_dir"]) / "notes.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          title TEXT NOT NULL,
          body TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS notes_by_user ON notes (user_id, created_at)")
    return conn


def _note(row: sqlite3.Row) -> dict:
    body = row["body"]
    return {
        "id": row["id"],
        "title": row["title"],
        "body": body if len(body) <= MAX_BODY_RETURNED else body[:MAX_BODY_RETURNED] + "…",
        "created_at": row["created_at"].replace(" ", "T") + "Z",
    }


def create_note(ctx: dict, title: str, body: str) -> dict:
    conn = _connect(ctx)
    try:
        with conn:
            note_id = conn.execute(
                "INSERT INTO notes (user_id, title, body) VALUES (?, ?, ?)", (ctx["user_id"], title, body)
            ).lastrowid
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    finally:
        conn.close()
    return {"created": {"id": row["id"], "title": row["title"], "created_at": _note(row)["created_at"]}}


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def read_notes(ctx: dict, query: str = "", limit: int = 10) -> dict:
    terms = [t for t in query.split() if t][:8]
    sql = "SELECT * FROM notes WHERE user_id = ?"
    params: list = [ctx["user_id"]]
    for term in terms:  # every term must appear in the title or body
        sql += " AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')"
        params += [_like(term), _like(term)]
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    conn = _connect(ctx)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return {"query": query, "notes": [_note(r) for r in rows]}
