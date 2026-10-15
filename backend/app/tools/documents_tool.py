"""Tool: files you uploaded (PDFs, text, photos), kept as text in
DATA_DIR/tools/documents/documents.db with an FTS5 index. The server extracts the text when
you upload (app/documents.py) and writes it here; the tool only searches and reads.

Like the notes, this text sits unencrypted in the tool's own file, because the sandboxed
tool has no encryption key; deleting a file removes it and compacts the database.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

PART_CHARS = 4000


def connect(data_dir: str | Path) -> sqlite3.Connection:
    """The documents database; also used by app/documents.py (outside the sandbox)."""
    conn = sqlite3.connect(Path(data_dir) / "documents.db", timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA secure_delete = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          kind TEXT NOT NULL,                -- pdf | text | image
          bytes INTEGER NOT NULL,
          text TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(name, text, tokenize = 'porter unicode61');
        CREATE TRIGGER IF NOT EXISTS documents_fts_delete AFTER DELETE ON documents BEGIN
          DELETE FROM documents_fts WHERE rowid = old.id;
        END;
        """
    )
    return conn


def _fts_query(text: str) -> str | None:
    words = re.findall(r"\w+", text.lower())[:12]
    return " OR ".join(f'"{w}"' for w in words) if words else None


def _parts(text: str) -> int:
    return max(1, -(-len(text) // PART_CHARS))


def _summary(row: sqlite3.Row, snippet: str | None = None) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "uploaded": row["created_at"].replace(" ", "T") + "Z",
        "parts": _parts(row["text"]),
        "snippet": snippet if snippet is not None else row["text"][:300],
    }


def search_documents(ctx: dict, query: str = "", limit: int = 5) -> dict:
    conn = connect(ctx["data_dir"])
    try:
        match = _fts_query(query)
        if match:
            rows = conn.execute(
                """
                SELECT d.*, snippet(documents_fts, 1, '', '', ' … ', 40) AS snip
                FROM documents_fts JOIN documents d ON d.id = documents_fts.rowid
                WHERE documents_fts MATCH ? AND d.user_id = ? ORDER BY bm25(documents_fts) LIMIT ?
                """,
                (match, ctx["user_id"], limit),
            ).fetchall()
            found = [_summary(r, r["snip"]) for r in rows]
        else:
            rows = conn.execute(
                "SELECT * FROM documents WHERE user_id = ? ORDER BY id DESC LIMIT ?", (ctx["user_id"], limit)
            ).fetchall()
            found = [_summary(r) for r in rows]
    finally:
        conn.close()
    return {"query": query, "documents": found}


def read_document(ctx: dict, id: int, part: int = 1) -> dict:  # noqa: A002 - the model's argument name
    conn = connect(ctx["data_dir"])
    try:
        row = conn.execute("SELECT * FROM documents WHERE id = ? AND user_id = ?", (id, ctx["user_id"])).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"there is no document #{id}")
    parts = _parts(row["text"])
    if part > parts:
        raise ValueError(f"document #{id} has only {parts} part(s)")
    start = (part - 1) * PART_CHARS
    return {"id": id, "name": row["name"], "part": part, "parts": parts, "text": row["text"][start:start + PART_CHARS]}
