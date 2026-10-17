"""Keeping a local model loaded, so a reply doesn't start with a 5-second model load.

Ollama unloads a model after 5 idle minutes; loading it again takes ~5 s on an 8 GB GPU. While
Custodian has been used in the last MODEL_KEEP_WARM_MINUTES (default 60), a background thread
asks Ollama every few minutes to keep the chat and embedding models loaded. After that the
GPU is given back. `warm()` also loads them on demand - the desktop app calls it the moment it
hears "Hey Jarvis", so the load happens while you are still talking.

Only for a model server on this machine (Ollama's native API); a cloud model needs none of it.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from urllib.parse import urlsplit

import httpx

from .config import get_settings

log = logging.getLogger(__name__)
PING_SECONDS = 240  # under Ollama's 5-minute default, so the models never unload in between
_last_used = 0.0
_lock = threading.Lock()
_started = False


def keep_minutes() -> float:
    try:
        return float(os.getenv("MODEL_KEEP_WARM_MINUTES", "60"))
    except ValueError:
        return 60.0


def _ollama_root(base_url: str) -> str | None:
    """'http://127.0.0.1:11434/v1' -> 'http://127.0.0.1:11434' for a model server on this machine."""
    parts = urlsplit(base_url or "")
    if parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _targets() -> list[tuple[str, str, str]]:
    """(root, model, kind) for each local model in use: the chat model and the embedder."""
    settings = get_settings()
    out = []
    if settings.model_backend == "nebius":
        root = _ollama_root(settings.nebius_base_url)
        if root and settings.nebius_model_id:
            out.append((root, settings.nebius_model_id, "chat"))
    if settings.embedding_model:
        root = _ollama_root(settings.embedding_base_url or settings.nebius_base_url)
        if root:
            out.append((root, settings.embedding_model, "embed"))
    return out


def warm(keep: str | None = None) -> None:
    """Load the local models now (if they aren't) and keep them for `keep` (Ollama duration)."""
    keep = keep or f"{max(5, int(keep_minutes()))}m"
    for root, model, kind in _targets():
        try:
            if kind == "chat":  # an empty prompt only loads the model
                httpx.post(f"{root}/api/generate", json={"model": model, "keep_alive": keep}, timeout=60)
            else:
                httpx.post(f"{root}/api/embed", json={"model": model, "input": "", "keep_alive": keep}, timeout=60)
        except httpx.HTTPError as exc:
            log.debug("couldn't warm %s: %s", model, exc)


def used() -> None:
    """A message is being answered: keep the models warm for the next while."""
    global _last_used
    with _lock:
        _last_used = time.monotonic()
    start()


def _loop() -> None:
    while True:
        time.sleep(PING_SECONDS)
        with _lock:
            recent = _last_used and time.monotonic() - _last_used < keep_minutes() * 60
        if recent:
            warm("6m")  # just past the next ping; once you stop using it, the GPU frees up within minutes


def start() -> None:
    global _started
    with _lock:
        if _started or keep_minutes() <= 0 or not _targets():
            return
        _started = True
    threading.Thread(target=_loop, daemon=True, name="model-warmup").start()


def warm_soon() -> None:
    """warm(), without making the caller wait for the load."""
    used()
    threading.Thread(target=warm, args=("6m",), daemon=True).start()
