"""Hybrid search over everything the user has said, in every conversation they've had:
keyword search here (FTS5) and, when an embedding model is set, search by meaning
(app/vectors.py), the two rankings fused - so "who has my brolly?" can find "I lent my
umbrella to Kavya" although they share no word.

This is what lets a new chat pick up something mentioned weeks ago that never became a
memory. Off-the-record chats are never indexed (app/privacy.py). Only the user's own
statements are recalled (app/statements.py): Custodian's past
replies can be wrong (a misspelled name, say), and recalling them would repeat the mistake;
earlier questions and small talk hold no facts; and a message that looks like it holds a
secret is never recalled, so a password typed once doesn't follow the user into later chats.

Message text is encrypted at rest, so unlike memories_fts there is no search copy on disk.
Each process keeps an in-memory FTS5 index instead. The first search decrypts the user
messages once; every later search first adds any messages saved since, by this process or
any other (messages.id only grows - AUTOINCREMENT never reuses an id). Every hit is
decrypted from the messages table and compared with the indexed text before it's returned,
so a message deleted elsewhere drops out even while the index still holds it, and an index
left over from a replaced database file is thrown away. Deleting a conversation through
this process also removes its text from the index straight away.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import numpy as np

from . import db, memory, vectors
from .crypto import decrypt
from .statements import looks_like_question, looks_like_small_talk, worth_recalling  # noqa: F401 - re-exported

_lock = threading.Lock()
_index: _Index | None = None


class _Index:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.execute("CREATE VIRTUAL TABLE said USING fts5(content, tokenize = 'porter unicode61')")
        self.last_id = 0

    def catch_up(self) -> None:
        """Index user messages saved since the last call, leaving out off-the-record chats."""
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT m.id, m.content FROM messages m JOIN conversations c ON c.id = m.conversation_id
                WHERE m.id > ? AND m.role = 'user' AND c.private = 0 ORDER BY m.id
                """,
                (self.last_id,),
            ).fetchall()
        if rows:
            self.conn.executemany(
                "INSERT INTO said (rowid, content) VALUES (?, ?)", [(r["id"], decrypt(r["content"])) for r in rows]
            )
            self.last_id = rows[-1]["id"]

    def highest_id_ever(self) -> int:
        with db.connect() as conn:
            row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'messages'").fetchone()
        return row["seq"] if row else 0


def _current() -> _Index:
    """The index for the database in use, rebuilt if the settings point at another file or the
    file was replaced (its id counter went backwards). Call with _lock held."""
    global _index
    path = db.db_path()
    if _index is None or _index.path != path or _index.highest_id_ever() < _index.last_id:
        if _index is not None:
            _index.conn.close()
        _index = _Index(path)
    _index.catch_up()
    return _index


def search(
    user_id: int,
    query: str,
    *,
    query_vector: np.ndarray | None = None,
    exclude: set[int] | frozenset[int] = frozenset(),
    limit: int = 6,
) -> list[dict]:
    """The user's earlier messages that best match `query`, best first, as
    {id, conversation_id, created_at, content}: keyword hits and, given `query_vector`,
    semantic hits (app/vectors.py), the two rankings fused. Messages in `exclude` (the
    current message and the history already in the prompt) are skipped, and repeats of the
    same words are returned once, as the newest copy."""
    if limit <= 0 or looks_like_small_talk(query):  # "thanks, that's all" has nothing to look up
        return []
    match = memory.fts_query(query)
    pool = limit * 4 + len(exclude)
    semantic = [item for item, _ in vectors.search("message", query_vector, pool)]
    if match is None and not semantic:
        return []
    with _lock:
        index = _current()
        keyword = (
            index.conn.execute(
                "SELECT rowid, content FROM said WHERE said MATCH ? ORDER BY rank LIMIT ?", (match, pool)
            ).fetchall()
            if match
            else []
        )
        texts = dict(keyword)
        extra = [item for item in semantic if item not in texts]
        if extra:
            marks = ",".join("?" * len(extra))
            texts.update(index.conn.execute(f"SELECT rowid, content FROM said WHERE rowid IN ({marks})", extra).fetchall())
    ranked = [item for item in vectors.fuse([row[0] for row in keyword], semantic) if item in texts]
    hits = [
        (position, item, texts[item])
        for position, item in enumerate(ranked)
        if item not in exclude and worth_recalling(texts[item])
    ]
    if not hits:
        return []

    marks = ",".join("?" * len(hits))
    with db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT m.id, m.conversation_id, m.content, m.created_at FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE c.user_id = ? AND m.role = 'user' AND c.private = 0 AND m.id IN ({marks})
            """,
            [user_id, *(h[1] for h in hits)],
        ).fetchall()
    found = {r["id"]: r for r in rows}

    best: dict[str, tuple[int, int, str]] = {}  # same words -> (best position, newest id, text)
    for position, message_id, text in hits:
        if message_id not in found:
            continue  # deleted, or another user's
        if decrypt(found[message_id]["content"]) != text:  # the index belongs to a replaced file
            reset()
            continue
        key = " ".join(text.lower().split())
        if key not in best or message_id > best[key][1]:
            best[key] = (min(position, best[key][0]) if key in best else position, message_id, text)
    chosen = sorted(best.values())[:limit]
    return [
        {
            "id": message_id,
            "conversation_id": found[message_id]["conversation_id"],
            "created_at": found[message_id]["created_at"],
            "content": text,
        }
        for _, message_id, text in chosen
    ]


def forget(message_ids: list[int]) -> None:
    """Drop deleted messages from the index (if it's built) so their text leaves memory too."""
    if not message_ids:
        return
    with _lock:
        if _index is None or _index.path != db.db_path():
            return
        _index.conn.executemany("DELETE FROM said WHERE rowid = ?", [(i,) for i in message_ids])


def reset() -> None:
    """Throw the index away; the next search rebuilds it (tests, key rotation)."""
    global _index
    with _lock:
        if _index is not None:
            _index.conn.close()
        _index = None
