"""Encrypted long-term memory with hybrid recall.

Every prompt gets the user's whole lifetime memory that fits its budget (for_context()):
the memories that match the message first - by keyword (FTS5, below) and, when an
embedding model is set, by meaning (app/vectors.py), the two rankings fused - so the
relevant ones are always among those included, then everything else, newest first.

memories.content is Fernet ciphertext. memories_fts keeps a plaintext copy for FTS5
keyword search (rowid = memories.id) - a deliberate tradeoff: the search copy lives in
the same database file, so encryption protects message text and memories from anyone who
reads the ciphertext columns, but not from someone holding the whole file. write() and
update() keep both tables in step in one transaction; the memories_fts_delete trigger
removes the search copy on every delete, including cascades from a user delete, and
purge_search_index() then makes sure the removed text leaves the file too.

Every query is parameterized and recall() turns free text into a quoted FTS5 query, so
memory text and questions can contain SQL or FTS syntax without breaking anything.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime

import numpy as np

from . import db, plans, sensitive, vectors
from .crypto import decrypt, encrypt

MEMORY_TYPES = ("episodic", "semantic", "preference")
MAX_MEMORY_CHARS = 2000

# Words that match almost every memory and would drown out the useful ones.
STOPWORDS = frozenset(
    """
    a about above after again all am an and any are as at be been before being below between both but by can
    could did do does doing don down during each few for from further had has have having he her here hers herself
    him himself his how i if in into is it its itself just let me more most my myself no nor not now of off on once
    only or other our ours ourselves out over own please same she should so some such than that the their theirs
    them themselves then there these they this those through to too under until up very was we were what when where
    which while who whom why will with would you your yours yourself yourselves tell know remember remind think
    get got want wants like need needs ok okay hey hi hello thanks thank also whats whos wheres whens hows
    """.split()
)


class InvalidMemory(ValueError):
    """Invalid memory input (bad type, empty or too long)."""


def _row(row: sqlite3.Row) -> dict:
    keys = row.keys()
    return {
        "id": row["id"],
        "type": row["type"],
        "content": decrypt(row["content"]),
        "source_message_id": row["source_message_id"],
        "created_at": db.iso(row["created_at"]),
        "updated_at": db.iso(row["updated_at"]),
        "expires_at": db.iso(row["expires_at"]),
        # A newer memory replaced this one (app/memory_graph.py): kept as history, no longer true.
        "superseded_by": row["superseded_by"] if "superseded_by" in keys else None,
        "valid_until": db.iso(row["valid_until"]) if "valid_until" in keys else None,
    }


def _clean(content: str) -> str:
    text = " ".join(content.split())
    if not text:
        raise InvalidMemory("memory content is empty")
    if len(text) > MAX_MEMORY_CHARS:
        raise InvalidMemory(f"memory content is longer than {MAX_MEMORY_CHARS} characters")
    if kind := sensitive.find(text):
        raise InvalidMemory(f"that looks like {kind}, and Custodian never keeps secrets in memory")
    return text


def _check_type(type: str) -> str:
    if type not in MEMORY_TYPES:
        raise InvalidMemory(f"memory type must be one of {', '.join(MEMORY_TYPES)}")
    return type


def write(
    user_id: int,
    content: str,
    type: str = "episodic",
    *,
    source_message_id: int | None = None,
    expires_at: datetime | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Save a memory (encrypted) and its search copy. Returns the id; an identical
    unexpired memory of the same user is reused instead of stored twice."""
    text = _clean(content)
    _check_type(type)
    expires = db.to_db_time(expires_at) if expires_at else None
    with db.use(conn) as c:
        existing = c.execute(
            """
            SELECT m.id FROM memories_fts f JOIN memories m ON m.id = f.rowid
            WHERE f.content = ? AND m.user_id = ?
              AND (m.expires_at IS NULL OR m.expires_at > datetime('now'))
            """,
            (text, user_id),
        ).fetchone()
        if existing:
            return existing["id"]
        plans.check_memory_room(c, user_id)  # PlanLimit when the plan's memory limit is reached
        memory_id = c.execute(
            "INSERT INTO memories (user_id, type, content, source_message_id, expires_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, type, encrypt(text), source_message_id, expires),
        ).lastrowid
        c.execute("INSERT INTO memories_fts (rowid, content) VALUES (?, ?)", (memory_id, text))
        return memory_id


def update(
    user_id: int,
    memory_id: int,
    *,
    content: str | None = None,
    type: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict | None:
    with db.use(conn) as c:
        row = c.execute("SELECT id FROM memories WHERE id = ? AND user_id = ?", (memory_id, user_id)).fetchone()
        if row is None:
            return None
        if type is not None:
            c.execute("UPDATE memories SET type = ? WHERE id = ?", (_check_type(type), memory_id))
        if content is not None:
            text = _clean(content)
            c.execute("UPDATE memories SET content = ?, linked = 0 WHERE id = ?", (encrypt(text), memory_id))  # mapped again
            c.execute("DELETE FROM memories_fts WHERE rowid = ?", (memory_id,))
            c.execute("INSERT INTO memories_fts (rowid, content) VALUES (?, ?)", (memory_id, text))
            purge_search_index(c)
            c.execute("DELETE FROM memory_vectors WHERE memory_id = ?", (memory_id,))  # re-embedded next turn
            c.execute("DELETE FROM memory_entities WHERE memory_id = ?", (memory_id,))
            c.execute("DELETE FROM entity_links WHERE memory_id = ?", (memory_id,))
            vectors.forget("memory", [memory_id])
        return get(user_id, memory_id, conn=c)


def purge_search_index(conn: sqlite3.Connection) -> None:
    """Rewrite the FTS5 index after a delete or edit. FTS5 only marks a removed row as
    deleted; its words stay, in plaintext, in older index segments until they are merged.
    'optimize' merges every segment, and with PRAGMA secure_delete (db.get_connection)
    the freed pages are zeroed, so the old text really leaves the file."""
    conn.execute("INSERT INTO memories_fts (memories_fts) VALUES ('optimize')")


def fts_query(text: str, max_terms: int = 12) -> str | None:
    """Free text -> an FTS5 query of quoted terms joined by OR, or None if nothing is searchable."""
    terms: list[str] = []
    for raw in re.findall(r"\w+", text.lower()):
        if raw in STOPWORDS or (len(raw) < 2 and not raw.isdigit()) or raw in terms:
            continue
        terms.append(raw)
        if len(terms) >= max_terms:
            break
    if not terms:
        return None
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)


def recall(user_id: int, query: str, k: int = 5, *, conn: sqlite3.Connection | None = None) -> list[dict]:
    """The user's unexpired memories that best match `query`, best first."""
    match = fts_query(query)
    if match is None or k <= 0:
        return []
    with db.use(conn) as c:
        rows = c.execute(
            """
            SELECT m.*, bm25(memories_fts) AS score
            FROM memories_fts JOIN memories m ON m.id = memories_fts.rowid
            WHERE memories_fts MATCH ? AND m.user_id = ?
              AND (m.expires_at IS NULL OR m.expires_at > datetime('now'))
            ORDER BY score
            LIMIT ?
            """,
            (match, user_id, k),
        ).fetchall()
    return [_row(r) for r in rows]


def preferences(user_id: int, limit: int = 3, *, conn: sqlite3.Connection | None = None) -> list[dict]:
    """The most recent standing preferences; they apply to every reply, not just keyword matches."""
    with db.use(conn) as c:
        rows = c.execute(
            """
            SELECT * FROM memories
            WHERE user_id = ? AND type = 'preference' AND superseded_by IS NULL
              AND (expires_at IS NULL OR expires_at > datetime('now'))
            ORDER BY updated_at DESC, id DESC LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [_row(r) for r in rows]


def search(
    user_id: int,
    query: str,
    k: int = 10,
    *,
    query_vector: np.ndarray | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict]:
    """The k memories that best match `query`, best first: keyword and, given `query_vector`,
    semantic rankings fused. Empty means nothing saved bears on it."""
    with db.use(conn) as c:
        keyword = recall(user_id, query, k, conn=c)
        semantic = [item for item, _ in vectors.search("memory", query_vector, k)]
        found = {m["id"]: m for m in keyword}
        missing = [item for item in semantic if item not in found]
        if missing:
            marks = ",".join("?" * len(missing))
            for row in c.execute(
                f"""
                SELECT * FROM memories WHERE id IN ({marks}) AND user_id = ?
                  AND (expires_at IS NULL OR expires_at > datetime('now'))
                """,
                [*missing, user_id],
            ):
                found[row["id"]] = _row(row)
    return [found[item] for item in vectors.fuse([m["id"] for m in keyword], semantic) if item in found][:k]


def for_context(
    user_id: int,
    query: str,
    *,
    budget_chars: int,
    k: int = 10,
    preferences_k: int = 3,
    query_vector: np.ndarray | None = None,
    best: list[dict] | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[tuple[dict, bool]]:
    """The lifetime memory for one prompt, as (memory, matched) pairs: the k best matches for
    `query` (search(), or `best` when the caller already ran it) and the newest standing
    preferences first (matched=True), then every other unexpired memory, newest first, while
    the text fits in `budget_chars`. A memory that looks like a secret (saved before write()
    refused them) is left out."""
    picked: list[tuple[dict, bool]] = []
    seen: set[int] = set()
    used = 0

    def take(item: dict, matched: bool) -> None:
        nonlocal used
        cost = len(item["content"]) + 30  # the "- [memory #id · type] " prefix
        if item["id"] in seen or used + cost > budget_chars or sensitive.looks_secret(item["content"]):
            return
        seen.add(item["id"])
        picked.append((item, matched))
        used += cost

    with db.use(conn) as c:
        if best is None:
            best = search(user_id, query, k, query_vector=query_vector, conn=c)
        for item in best + preferences(user_id, preferences_k, conn=c):
            take(item, True)
        rows = c.execute(  # a replaced fact comes in only when the question matches it (history)
            """
            SELECT * FROM memories
            WHERE user_id = ? AND superseded_by IS NULL AND (expires_at IS NULL OR expires_at > datetime('now'))
            ORDER BY updated_at DESC, id DESC
            """,
            (user_id,),
        )
        for row in rows:
            if budget_chars - used < 40:
                break
            if row["id"] not in seen:
                take(_row(row), False)
    return picked


def get(user_id: int, memory_id: int, *, conn: sqlite3.Connection | None = None) -> dict | None:
    with db.use(conn) as c:
        row = c.execute("SELECT * FROM memories WHERE id = ? AND user_id = ?", (memory_id, user_id)).fetchone()
    return _row(row) if row else None


def list_memories(
    user_id: int, type: str | None = None, limit: int = 200, *, conn: sqlite3.Connection | None = None
) -> list[dict]:
    sql = "SELECT * FROM memories WHERE user_id = ?"
    params: list = [user_id]
    if type is not None:
        sql += " AND type = ?"
        params.append(_check_type(type))
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with db.use(conn) as c:
        rows = c.execute(sql, params).fetchall()
    return [_row(r) for r in rows]


def delete(user_id: int, memory_id: int, *, conn: sqlite3.Connection | None = None) -> bool:
    with db.use(conn) as c:
        cur = c.execute("DELETE FROM memories WHERE id = ? AND user_id = ?", (memory_id, user_id))
        if cur.rowcount:
            purge_search_index(c)
            # People and places no memory names any more go too (their links went with the memory).
            c.execute(
                """
                DELETE FROM entities WHERE user_id = ? AND id NOT IN (SELECT entity_id FROM memory_entities)
                  AND id NOT IN (SELECT source_id FROM entity_links) AND id NOT IN (SELECT target_id FROM entity_links)
                """,
                (user_id,),
            )
    if cur.rowcount:
        vectors.forget("memory", [memory_id])  # its stored vector went with it (ON DELETE CASCADE)
        from . import memory_graph  # here: it reads memories

        memory_graph._cache.pop(user_id, None)  # noqa: SLF001
    return cur.rowcount > 0


def purge_expired(*, conn: sqlite3.Connection | None = None) -> int:
    """Delete expired memories (and, by trigger, their search copies). Run hourly by the scheduler."""
    with db.use(conn) as c:
        cur = c.execute("DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at <= datetime('now')")
        if cur.rowcount:
            purge_search_index(c)
    return cur.rowcount
