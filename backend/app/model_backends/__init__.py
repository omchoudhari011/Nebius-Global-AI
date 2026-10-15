"""Picks the model backend named by MODEL_BACKEND (stub or nebius)."""

from __future__ import annotations

import threading

from ..config import get_settings
from .base import ModelBackend, ModelError, ModelResponse, ToolCallRequest

__all__ = ["ModelBackend", "ModelError", "ModelResponse", "ToolCallRequest", "close_backend", "get_backend", "set_backend"]

_lock = threading.Lock()
_backend: ModelBackend | None = None


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
