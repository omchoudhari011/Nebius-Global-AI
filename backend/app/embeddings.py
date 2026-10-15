"""Text embeddings for semantic search, from any OpenAI-compatible /embeddings endpoint:
Ollama on this machine (nomic-embed-text) or a cloud provider such as Nebius Token Factory.
Semantic search is off unless EMBEDDING_MODEL is set.

Vectors come back unit length, so cosine similarity is a plain dot product. Some models are
trained with task prefixes ("search_query: " / "search_document: "); PREFIXES adds them.
When the endpoint fails, callers fall back to keyword search, and further calls pause for a
minute so a stopped Ollama doesn't slow down every chat turn.
"""

from __future__ import annotations

import logging
import threading
import time

import httpx
import numpy as np

from . import plans
from .config import get_settings

log = logging.getLogger(__name__)

PREFIXES = {"nomic-embed-text": ("search_query: ", "search_document: ")}  # (query, document)
MAX_CHARS = 2000  # longer texts are cut; their opening carries the gist
BATCH = 64
PAUSE_SECONDS = 60.0


class EmbeddingError(RuntimeError):
    """The embedding endpoint couldn't produce vectors (down, unknown model, bad reply)."""


class EmbeddingPaused(EmbeddingError):
    """Not tried: the endpoint failed less than PAUSE_SECONDS ago."""


class Embedder:
    def __init__(
        self, model: str, base_url: str, api_key: str = "", *, timeout: float = 30.0, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.model = model
        self.query_prefix, self.document_prefix = PREFIXES.get(model.split(":")[0], ("", ""))
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(timeout, connect=5.0),
            headers=headers,
            limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=300.0),  # see the chat backend
            transport=transport,
        )
        self._paused_until = 0.0

    def close(self) -> None:
        self._client.close()

    def _raw(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.post("embeddings", json={"model": self.model, "input": texts})
        if resp.status_code >= 400:
            raise EmbeddingError(f"the embedding endpoint returned {resp.status_code}: {resp.text[:200]}")
        data = resp.json()["data"]
        return [item["embedding"] for item in sorted(data, key=lambda item: item.get("index", 0))]

    def _embed(self, texts: list[str]) -> np.ndarray:
        if time.monotonic() < self._paused_until:
            raise EmbeddingPaused("paused after a recent failure")
        try:
            rows: list[list[float]] = []
            for start in range(0, len(texts), BATCH):
                rows.extend(self._raw([t[:MAX_CHARS] for t in texts[start : start + BATCH]]))
            matrix = np.asarray(rows, dtype=np.float32)
            if matrix.ndim != 2 or len(matrix) != len(texts):
                raise EmbeddingError(f"expected {len(texts)} vectors, got shape {matrix.shape}")
        except (httpx.HTTPError, KeyError, ValueError, TypeError, EmbeddingError) as exc:
            self._paused_until = time.monotonic() + PAUSE_SECONDS
            raise EmbeddingError(f"{self.model}: {exc}") from None
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms

    def documents(self, texts: list[str]) -> np.ndarray:
        return self._embed([self.document_prefix + t for t in texts])

    def query(self, text: str) -> np.ndarray:
        return self._embed([self.query_prefix + text])[0]

    def query_and_document(self, text: str) -> tuple[np.ndarray, np.ndarray]:
        """The same text as a search query and as a stored document, in one request."""
        matrix = self._embed([self.query_prefix + text, self.document_prefix + text])
        return matrix[0], matrix[1]


_lock = threading.Lock()
_embedder: Embedder | None = None
_override = False


def get_embedder() -> Embedder | None:
    """The configured embedder, or None when semantic search is off - not configured, or not
    in the current plan (app/plans.py; keyword search still works on every plan)."""
    global _embedder
    with _lock:
        if _override:
            return _embedder
    if not plans.semantic_search_allowed():
        return None
    with _lock:
        if _embedder is None and not _override:
            settings = get_settings()
            if settings.embedding_model:
                _embedder = Embedder(settings.embedding_model, settings.embedding_base_url, settings.embedding_api_key)
        return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    """Swap the embedder (tests, evals); None goes back to the settings."""
    global _embedder, _override
    with _lock:
        if _embedder is not None and _embedder is not embedder:
            _embedder.close()
        _embedder, _override = embedder, embedder is not None


def disable() -> None:
    """Keyword search only, whatever the settings say (evals' baseline)."""
    global _embedder, _override
    with _lock:
        if _embedder is not None:
            _embedder.close()
        _embedder, _override = None, True
