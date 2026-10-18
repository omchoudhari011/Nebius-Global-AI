"""Search inside your PC's files by meaning: "the PDF where I wrote about the Pune flat",
"which notes mention the client meeting?".

Off until you switch it on (Tools & Data, settings_store "pc_index"); switching it off deletes
the index. While it's on, the desktop app reads the files in the folders you chose (Documents,
Downloads and Desktop to start with), takes the text out of the ones it can read - text and code,
PDF, Word, PowerPoint, Excel - and sends only that text here, in passages:

    GET  /desktop/index/config      on or off, the folders, and what's indexed already (path -> modified)
    POST /desktop/index             new or changed files' passages, and paths that are gone

Each passage is embedded with the same model as your memories and kept, with its file's path,
in the PC tool's own database (DATA_DIR/tools/pc/pc_files.db), where the sandboxed search tool
(app/tools/pc_files_tool.py) reads it: by keyword (FTS5) and by meaning. Like your notes and
uploaded files, that text is stored unencrypted in the tool's own file - the tool has no key -
and a passage that looks like it holds a secret (a password, a card number) is never stored.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import numpy as np

from . import embeddings, sensitive, settings_store
from .config import get_settings
from .tools import pc_files_tool

log = logging.getLogger(__name__)

MAX_FILES = 20_000
MAX_CHUNKS = 60_000
MAX_CHUNKS_PER_FILE = 120
MAX_CHUNK_CHARS = 2_000
BATCH_FILES = 40  # what one POST may carry


class IndexOff(RuntimeError):
    """Indexing is switched off for this user."""


def _folder() -> Path:
    folder = get_settings().tool_data_dir / "pc"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _connect() -> sqlite3.Connection:
    return pc_files_tool.connect(_folder())


def config(user_id: int) -> dict:
    """What the desktop app needs: whether to index, where, and what's here already."""
    prefs = settings_store.get(user_id)
    if not prefs["pc_index"]:
        return {"enabled": False, "folders": [], "indexed": {}}
    conn = _connect()
    try:
        indexed = {r["path"]: [r["modified"], r["size"]] for r in conn.execute(
            "SELECT path, modified, size FROM files WHERE user_id = ?", (user_id,))}
    finally:
        conn.close()
    return {"enabled": True, "folders": prefs["pc_index_folders"], "indexed": indexed,
            "max_files": MAX_FILES, "max_chunks_per_file": MAX_CHUNKS_PER_FILE, "batch": BATCH_FILES}


def ingest(user_id: int, files: list[dict], removed: list[str]) -> dict:
    """Store new or changed files' passages (replacing what a file had) and forget removed
    paths. Returns counts. Raises IndexOff when indexing is switched off."""
    if not settings_store.get(user_id)["pc_index"]:
        raise IndexOff("Searching inside PC files is switched off")
    embedder = embeddings.get_embedder()
    stored = skipped_secret = 0
    conn = _connect()
    try:
        with conn:
            for path in removed[:5000]:
                conn.execute("DELETE FROM files WHERE user_id = ? AND path = ?", (user_id, path))
            total = conn.execute("SELECT COUNT(*) FROM chunks c JOIN files f ON f.id = c.file_id WHERE f.user_id = ?",
                                 (user_id,)).fetchone()[0]
        for item in files[:BATCH_FILES]:
            path, chunks = str(item.get("path") or "")[:1000], [str(c)[:MAX_CHUNK_CHARS] for c in item.get("chunks") or []]
            if not path:
                continue
            kept = [c for c in chunks[:MAX_CHUNKS_PER_FILE] if c.strip() and not sensitive.looks_secret(c)]
            skipped_secret += len(chunks[:MAX_CHUNKS_PER_FILE]) - len(kept)
            if total + len(kept) > MAX_CHUNKS:
                kept = kept[: max(0, MAX_CHUNKS - total)]
            vectors: list[np.ndarray | None] = [None] * len(kept)
            if embedder is not None and kept:
                try:
                    vectors = list(embedder.documents([f"{item.get('name', '')}\n{c}" for c in kept]))
                except embeddings.EmbeddingError as exc:
                    log.warning("couldn't embed PC file passages (keyword search still works): %s", exc)
            with conn:
                conn.execute("DELETE FROM files WHERE user_id = ? AND path = ?", (user_id, path))
                file_id = conn.execute(
                    "INSERT INTO files (user_id, path, name, modified, size, kind) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, path, str(item.get("name") or Path(path).name)[:300], float(item.get("modified") or 0),
                     int(item.get("size") or 0), str(item.get("kind") or "")[:20]),
                ).lastrowid
                for seq, (text, vector) in enumerate(zip(kept, vectors)):
                    chunk_id = conn.execute(
                        "INSERT INTO chunks (file_id, seq, text, vector, model) VALUES (?, ?, ?, ?, ?)",
                        (file_id, seq, text, None if vector is None else np.asarray(vector, dtype=np.float32).tobytes(),
                         embedder.model if embedder is not None and vector is not None else None),
                    ).lastrowid
                    conn.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (chunk_id, text))
            total += len(kept)
            stored += 1
        if removed:
            conn.execute("INSERT INTO chunks_fts (chunks_fts) VALUES ('optimize')")
            conn.commit()
    finally:
        conn.close()
    return {"stored": stored, "removed": len(removed), "secret_passages_skipped": skipped_secret}


def status(user_id: int) -> dict:
    prefs = settings_store.get(user_id)
    path = get_settings().tool_data_dir / "pc" / "pc_files.db"
    if not path.exists():
        return {"enabled": prefs["pc_index"], "folders": prefs["pc_index_folders"], "files": 0, "passages": 0, "updated_at": None}
    conn = _connect()
    try:
        files, updated = conn.execute("SELECT COUNT(*), MAX(indexed_at) FROM files WHERE user_id = ?", (user_id,)).fetchone()
        passages = conn.execute("SELECT COUNT(*) FROM chunks c JOIN files f ON f.id = c.file_id WHERE f.user_id = ?",
                                (user_id,)).fetchone()[0]
    finally:
        conn.close()
    return {"enabled": prefs["pc_index"], "folders": prefs["pc_index_folders"], "files": files, "passages": passages,
            "updated_at": updated.replace(" ", "T") + "Z" if updated else None}


def clear(user_id: int) -> int:
    """Delete this user's whole index (when they switch it off). Returns how many files it held."""
    path = get_settings().tool_data_dir / "pc" / "pc_files.db"
    if not path.exists():
        return 0
    conn = _connect()
    try:
        with conn:
            removed = conn.execute("DELETE FROM files WHERE user_id = ?", (user_id,)).rowcount
        conn.execute("INSERT INTO chunks_fts (chunks_fts) VALUES ('optimize')")
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()
    return removed


def query_vector(text: str) -> list[float] | None:
    """The search's own vector, made here (the sandboxed tool can't reach the embedding model)."""
    embedder = embeddings.get_embedder()
    if embedder is None:
        return None
    try:
        return embedder.query(text).astype(np.float32).tolist()
    except embeddings.EmbeddingError as exc:
        log.warning("PC file search by meaning unavailable, keyword only: %s", exc)
        return None
