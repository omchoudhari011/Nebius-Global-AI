"""Picks the model backend named by MODEL_BACKEND (stub or nebius), and the optional larger
model (LARGE_MODEL_ID, any OpenAI-compatible endpoint) used for "think harder" and when the
everyday model's reply fails the accuracy checks twice."""

from __future__ import annotations

import os
import threading

from ..config import get_settings
from .base import ModelBackend, ModelError, ModelResponse, ToolCallRequest

__all__ = [
    "ModelBackend", "ModelError", "ModelResponse", "ToolCallRequest", "close_backend", "get_backend",
    "get_large_backend", "large_base_url", "set_backend", "set_large_backend",
]

DEFAULT_LARGE_BASE_URL = "https://api.tokenfactory.nebius.com/v1"

_lock = threading.Lock()
_backend: ModelBackend | None = None
_large: ModelBackend | None = None
_large_override = False


def get_backend() -> ModelBackend:
    global _backend
    with _lock:
        if _backend is None:
            settings = get_settings()
            if settings.model_backend == "nebius":
                from .nebius_nemotron_backend import NebiusNemotronBackend

                _backend = NebiusNemotronBackend(
                    api_key=settings.nebius_api_key,
                    model_id=settings.nebius_model_id,
                    base_url=settings.nebius_base_url,
                    timeout=settings.model_timeout_seconds,
                    max_tokens=settings.model_max_tokens,
                    temperature=settings.model_temperature,
                    extra_body=settings.model_extra_body,
                )
            else:
                from .stub_backend import StubBackend

                _backend = StubBackend()
        return _backend


def set_backend(backend: ModelBackend | None) -> None:
    """Swap the backend (tests, evals)."""
    global _backend
    with _lock:
        if _backend is not None and _backend is not backend:
            _backend.close()
        _backend = backend


def close_backend() -> None:
    set_backend(None)
    set_large_backend(None)


def large_base_url() -> str:
    return os.getenv("LARGE_MODEL_BASE_URL", "").strip() or DEFAULT_LARGE_BASE_URL


def get_large_backend() -> ModelBackend | None:
    """The larger model, or None when LARGE_MODEL_ID isn't set."""
    global _large
    with _lock:
        if _large is None and not _large_override:
            model_id = os.getenv("LARGE_MODEL_ID", "").strip()
            if model_id:
                from .nebius_nemotron_backend import NebiusNemotronBackend

                settings = get_settings()
                _large = NebiusNemotronBackend(
                    api_key=os.getenv("LARGE_MODEL_API_KEY", "").strip() or settings.nebius_api_key,
                    model_id=model_id,
                    base_url=large_base_url(),
                    timeout=max(settings.model_timeout_seconds, 120.0),
                    max_tokens=max(settings.model_max_tokens, 2048),
                    temperature=settings.model_temperature,
                )
        return _large


def set_large_backend(backend: ModelBackend | None) -> None:
    """Swap the larger model (tests); None goes back to the settings."""
    global _large, _large_override
    with _lock:
        if _large is not None and _large is not backend:
            _large.close()
        _large, _large_override = backend, backend is not None
