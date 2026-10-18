"""Tool: search inside the files on your PC - by keyword and by meaning - in the index the
desktop app builds when you switch it on (app/pc_index.py writes DATA_DIR/tools/pc/pc_files.db;
this tool only reads it). The search's own vector is made by the gate, outside the sandbox, and
passed in as ctx["query_vector"]: the tool can't reach the embedding model.

Results are files, best first, each with its best-matching passage, so the model can say what
it found and open it with pc_open.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np  # imported before the sandbox's guard goes up (runner.py)

MIN_SIMILARITY = 0.55  # nomic-embed-text: unrelated text still scores ~0.45
MIN_STANDOUT = 1.5  # with many passages, a match must also stand out from the rest
RRF_K = 60


def connect(data_dir: str | Path) -> sqlite3.Connection:
    """The index; also written by app/pc_index.py (outside the sandbox)."""
    conn = sqlite3.connect(Path(data_dir) / "pc_files.db", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA secure_delete = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          path TEXT NOT NULL,
          name TEXT NOT NULL,
          kind TEXT,
          modified REAL NOT NULL,          -- the file's own modified time (seconds), to spot changes
          size INTEGER NOT NULL,
          indexed_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE (user_id, path)
        );
        CREATE TABLE IF NOT EXISTS chunks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          file_id INTEGER NOT NULL,
          seq INTEGER NOT NULL,
          text TEXT NOT NULL,
          vector BLOB,                     -- float32, unit length; NULL when no embedding model was set
          model TEXT
        );
        CREATE INDEX IF NOT EXISTS chunks_by_file ON chunks(file_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize = 'porter unicode61');
        CREATE TRIGGER IF NOT EXISTS files_delete AFTER DELETE ON files BEGIN
          DELETE FROM chunks WHERE file_id = old.id;
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_delete AFTER DELETE ON chunks BEGIN
          DELETE FROM chunks_fts WHERE rowid = old.id;
        END;
        """
    )
    return conn


_STOP = frozenset("a an and are about any did do does file files find for from have i in is it me my of on or pdf "
                  "that the this to was what where which with wrote doc document documents note notes say says".split())


def _fts_query(text: str) -> str | None:
    words = [w for w in re.findall(r"\w+", text.lower()) if w not in _STOP and len(w) > 1][:12]
    return " OR ".join(f'"{w}"' for w in words) if words else None


def _fuse(*rankings: list[int]) -> list[int]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores, key=lambda item: -scores[item])


def _semantic(conn: sqlite3.Connection, user_id: int, query: list[float], limit: int = 50) -> list[int]:
    vector = np.asarray(query, dtype=np.float32)
    rows = conn.execute(
        "SELECT c.id, c.vector FROM chunks c JOIN files f ON f.id = c.file_id WHERE f.user_id = ? AND c.vector IS NOT NULL",
        (user_id,),
    ).fetchall()
    rows = [r for r in rows if len(r["vector"]) == vector.nbytes]  # another embedding model's vectors don't compare
    if not rows:
        return []
    ids = np.array([r["id"] for r in rows], dtype=np.int64)
    matrix = np.frombuffer(b"".join(r["vector"] for r in rows), dtype=np.float32).reshape(len(rows), -1)
    scores = matrix @ vector
    keep = scores >= MIN_SIMILARITY
    if len(scores) >= 12:
        keep &= (scores - scores.mean()) / (scores.std() or 1.0) >= MIN_STANDOUT
    passing = np.flatnonzero(keep)
    return [int(ids[i]) for i in passing[np.argsort(-scores[passing])][:limit]]


def search_file_contents(ctx: dict, query: str, limit: int = 5) -> dict:
    data = Path(ctx["data_dir"])
    if not (data / "pc_files.db").exists():
        return {"query": query, "files": [], "note": "nothing is indexed: switch on 'Search inside my PC files' in Tools & Data"}
    conn = connect(data)
    try:
        indexed = conn.execute("SELECT COUNT(*) FROM files WHERE user_id = ?", (ctx["user_id"],)).fetchone()[0]
        keyword: list[int] = []
        match = _fts_query(query)
        if match:
            keyword = [r["id"] for r in conn.execute(
                """
                SELECT c.id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid JOIN files f ON f.id = c.file_id
                WHERE chunks_fts MATCH ? AND f.user_id = ? ORDER BY bm25(chunks_fts) LIMIT 50
                """,
                (match, ctx["user_id"]),
            )]
        semantic = _semantic(conn, ctx["user_id"], ctx["query_vector"]) if ctx.get("query_vector") else []
        found, seen = [], set()
        for chunk_id in _fuse(keyword, semantic):
            row = conn.execute(
                "SELECT c.text, f.* FROM chunks c JOIN files f ON f.id = c.file_id WHERE c.id = ?", (chunk_id,)
            ).fetchone()
            if row is None or row["path"] in seen:
                continue
            seen.add(row["path"])
            found.append({
                "path": row["path"], "name": row["name"], "kind": row["kind"],
                "modified": datetime.fromtimestamp(row["modified"]).strftime("%Y-%m-%d %H:%M") if row["modified"] else None,
                "passage": " ".join(row["text"].split())[:400],
            })
            if len(found) >= limit:
                break
    finally:
        conn.close()
    out: dict = {"query": query, "files": found, "files_indexed": indexed}
    if not indexed:
        out["note"] = "the index is empty: the desktop app fills it while it runs, after the switch is turned on"
    return out
