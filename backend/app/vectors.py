"""Semantic search over memories and the user's own messages, the second half of hybrid
retrieval (keyword search is the first; fuse() merges the two rankings).

Every memory, and every user message worth recalling (app/statements.py: not a question or
small talk), is embedded once (app/embeddings.py) and the vector is stored Fernet-encrypted
in memory_vectors / message_vectors: a vector can be partly turned back into its text, so it
gets the text's protection. Anything else - including whatever looks like a secret, which is
never sent to the embedding endpoint at all - gets a NULL row. Leaving questions out also
keeps a pile of near-identical "what's my name?" from drowning the one statement that
answers it. Each process decrypts the vectors into RAM once, picks up rows added since (by
any process) by their ever-growing id, and searches with one matrix product.

A semantic match has to stand out, not just score well: with nomic-embed-text an unrelated
"hello" still reaches 0.5 against almost anything. A match needs a cosine similarity of at
least EMBEDDING_MIN_SIMILARITY and, once there are enough items to judge by, must sit
MIN_STANDOUT standard deviations above the query's average similarity; with fewer items it
needs SMALL_SET_BONUS more similarity instead.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import db, embeddings, sensitive, statements
from .config import get_settings
from .crypto import decrypt, decrypt_bytes, encrypt_bytes

log = logging.getLogger(__name__)

# kind -> (vector table, its item column, the table the items live in)
KINDS = {"memory": ("memory_vectors", "memory_id", "memories"), "message": ("message_vectors", "message_id", "messages")}
MIN_STANDOUT = 2.0
SMALL_SET = 12  # below this many vectors the standout test isn't meaningful
SMALL_SET_BONUS = 0.1
RRF_K = 60  # reciprocal rank fusion constant (the usual choice)


@dataclass
class _Space:
    ids: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    matrix: np.ndarray | None = None
    last_row: int = 0


@dataclass
class _Store:
    path: Path
    model: str
    spaces: dict[str, _Space] = field(default_factory=lambda: {kind: _Space() for kind in KINDS})


_lock = threading.Lock()  # guards _store
_index_lock = threading.Lock()  # one embedding pass at a time
_store: _Store | None = None


def _load_new(store: _Store) -> None:
    """Add vector rows written since the last call (by any process). Call with _lock held."""
    for kind, (table, column, _) in KINDS.items():
        space = store.spaces[kind]
        with db.connect() as conn:
            rows = conn.execute(
                f"SELECT id, {column} AS item, vector FROM {table} WHERE id > ? AND model = ? ORDER BY id",
                (space.last_row, store.model),
            ).fetchall()
        if not rows:
            continue
        space.last_row = rows[-1]["id"]
        fresh = [(r["item"], np.frombuffer(decrypt_bytes(r["vector"]), dtype=np.float32)) for r in rows if r["vector"]]
        if not fresh:
            continue
        items = np.array([item for item, _ in fresh], dtype=np.int64)
        vectors = np.vstack([vector for _, vector in fresh])
        if space.matrix is not None and len(space.ids):
            keep = ~np.isin(space.ids, items)  # a re-embedded item replaces its old vector
            items = np.concatenate([space.ids[keep], items])
            vectors = np.vstack([space.matrix[keep], vectors])
        space.ids, space.matrix = items, vectors


def _replaced(store: _Store) -> bool:
    """True when a vector table's id counter is behind what was loaded: another file."""
    with db.connect() as conn:
        seq = dict(conn.execute("SELECT name, seq FROM sqlite_sequence").fetchall())
    return any(seq.get(KINDS[kind][0], 0) < space.last_row for kind, space in store.spaces.items())


def _current() -> _Store | None:
    """The in-RAM vectors for this database and embedding model, up to date. None when
    semantic search is off. Call with _lock held."""
    global _store
    embedder = embeddings.get_embedder()
    if embedder is None:
        return None
    path = db.db_path()
    stale = _store is None or _store.path != path or _store.model != embedder.model or _replaced(_store)
    if stale:
        _store = _Store(path, embedder.model)
    _load_new(_store)
    return _store


def search(kind: str, query: np.ndarray | None, limit: int) -> list[tuple[int, float]]:
    """Items of `kind` ('memory' or 'message') whose meaning matches `query`, best first, as
    (item id, cosine similarity). Empty when semantic search is off or nothing stands out.
    Ids aren't checked against the database: callers fetch the rows and drop missing ones."""
    if query is None or limit <= 0:
        return []
    with _lock:
        store = _current()
        if store is None:
            return []
        space = store.spaces[kind]
        ids, matrix = space.ids, space.matrix
    if matrix is None or not len(ids) or matrix.shape[1] != query.shape[0]:
        return []
    scores = matrix @ query
    floor = get_settings().embedding_min_similarity
    if len(scores) >= SMALL_SET:
        spread = float(scores.std()) or 1.0
        keep = (scores >= floor) & ((scores - float(scores.mean())) / spread >= MIN_STANDOUT)
    else:
        keep = scores >= floor + SMALL_SET_BONUS
    passing = np.flatnonzero(keep)
    order = passing[np.argsort(-scores[passing])][:limit]
    return [(int(ids[i]), float(scores[i])) for i in order]


def fuse(*rankings: list[int]) -> list[int]:
    """Reciprocal rank fusion: one ranking from several, each item scoring 1/(RRF_K + rank)
    in every list it appears in. An item found by both keyword and meaning rises to the top."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores, key=lambda item: -scores[item])


def _store_rows(kind: str, rows: list[tuple[int, np.ndarray | None]], model: str) -> None:
    table, column, source = KINDS[kind]
    with db.connect() as conn:
        conn.executemany(
            f"""
            INSERT OR IGNORE INTO {table} ({column}, model, vector)
            SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM {source} WHERE id = ?)
            """,
            [(item, model, None if vector is None else encrypt_bytes(vector.astype(np.float32).tobytes()), item)
             for item, vector in rows],
        )


def index_missing(max_items: int = 256, *, wait: bool = True) -> int:
    """Embed memories and user messages that have no vector for the current model yet, newest
    first, up to `max_items`. Returns how many were handled (0 = all done, or off). With
    wait=False it returns 0 at once if another pass is already running."""
    embedder = embeddings.get_embedder()
    if embedder is None or not _index_lock.acquire(blocking=wait):
        return 0
    try:
        handled = 0
        for kind, (table, column, source) in KINDS.items():
            if handled >= max_items:
                break
            only_user = (  # the user's own messages, never from an off-the-record chat
                "AND s.role = 'user' AND NOT EXISTS (SELECT 1 FROM conversations c WHERE c.id = s.conversation_id AND c.private = 1)"
                if kind == "message"
                else ""
            )
            with db.connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT s.id, s.content FROM {source} s
                    LEFT JOIN {table} v ON v.{column} = s.id AND v.model = ?
                    WHERE v.id IS NULL {only_user} ORDER BY s.id DESC LIMIT ?
                    """,
                    (embedder.model, max_items - handled),
                ).fetchall()
            if not rows:
                continue
            texts = [(r["id"], decrypt(r["content"])) for r in rows]
            keep = statements.worth_recalling if kind == "message" else lambda text: not sensitive.looks_secret(text)
            skipped = [(item, None) for item, text in texts if not keep(text)]
            todo = [(item, text) for item, text in texts if keep(text)]
            vectors = embedder.documents([text for _, text in todo]) if todo else []
            _store_rows(kind, skipped + [(item, vector) for (item, _), vector in zip(todo, vectors)], embedder.model)
            handled += len(rows)
        return handled
    finally:
        _index_lock.release()


def embed_turn(text: str, message_id: int | None = None) -> np.ndarray | None:
    """The query vector for one turn. A stored user message worth recalling is embedded as a
    document in the same request; anything else still missing is caught up after. None
    (keyword search only) when semantic search is off, the endpoint fails, or the text looks
    like a secret or is only small talk."""
    embedder = embeddings.get_embedder()
    if embedder is None:
        return None
    if sensitive.looks_secret(text) or statements.looks_like_small_talk(text):  # secret, or nothing to look up
        if message_id is not None:
            _store_rows("message", [(message_id, None)], embedder.model)
        return None
    try:
        if message_id is not None and statements.worth_recalling(text):
            query, document = embedder.query_and_document(text)
            _store_rows("message", [(message_id, document)], embedder.model)
        else:
            query = embedder.query(text)
            if message_id is not None:  # a question or small talk: searched with, never recalled
                _store_rows("message", [(message_id, None)], embedder.model)
    except embeddings.EmbeddingError as exc:
        _report(exc, "semantic search unavailable, using keyword search only")
        return None
    try:
        index_missing(32, wait=False)  # memories saved last turn, messages from other processes
    except embeddings.EmbeddingError as exc:
        _report(exc, "couldn't embed new memories and messages yet")
    return query


def _report(exc: Exception, what: str) -> None:
    level = logging.DEBUG if isinstance(exc, embeddings.EmbeddingPaused) else logging.WARNING
    log.log(level, "%s: %s", what, exc)


def forget(kind: str, item_ids: list[int]) -> None:
    """Drop items from RAM (their rows are deleted with them, or by the caller)."""
    if not item_ids:
        return
    with _lock:
        if _store is None or _store.path != db.db_path():
            return
        space = _store.spaces[kind]
        if space.matrix is not None and len(space.ids):
            keep = ~np.isin(space.ids, np.array(item_ids, dtype=np.int64))
            space.ids, space.matrix = space.ids[keep], space.matrix[keep]


def start_backfill() -> None:
    """Embed everything not embedded yet, in a background thread (server start)."""
    if embeddings.get_embedder() is None:
        return

    def run() -> None:
        total = 0
        try:
            while handled := index_missing(256):
                total += handled
        except embeddings.EmbeddingError as exc:
            log.warning("couldn't embed existing memories and messages yet: %s", exc)
        except Exception:  # noqa: BLE001 - a background helper must not take the server down
            log.exception("embedding backfill failed")
        if total:
            log.info("embedded %d memories and messages for semantic search", total)

    threading.Thread(target=run, name="vector-backfill", daemon=True).start()


def reset() -> None:
    """Throw the in-RAM vectors away; the next search reloads them."""
    global _store
    with _lock:
        _store = None
