"""Fernet encryption for the private columns: messages.content, tool_calls.args,
tool_calls.result, memories.content, and the embeddings in memory_vectors.vector and
message_vectors.vector (a vector can be partly turned back into its text).

APP_SECRET_KEY may hold several comma-separated keys. The first encrypts; every key can
decrypt, so a key can be rotated without downtime (`python -m app.cli rotate-keys`
re-encrypts old rows with the first key).
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .config import get_settings

__all__ = [
    "InvalidToken", "decrypt", "decrypt_bytes", "decrypt_json", "encrypt", "encrypt_bytes", "encrypt_json",
    "generate_key", "rotate",
]


@lru_cache(maxsize=4)
def _fernet_for(keys: tuple[str, ...]) -> MultiFernet:
    return MultiFernet([Fernet(k.encode()) for k in keys])


def _fernet() -> MultiFernet:
    return _fernet_for(get_settings().app_secret_keys)


def encrypt(text: str) -> bytes:
    return _fernet().encrypt(text.encode("utf-8"))


def decrypt(blob: bytes | memoryview) -> str:
    return _fernet().decrypt(bytes(blob)).decode("utf-8")


def encrypt_bytes(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt_bytes(blob: bytes | memoryview) -> bytes:
    return _fernet().decrypt(bytes(blob))


def encrypt_json(value: Any) -> bytes:
    return encrypt(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def decrypt_json(blob: bytes | memoryview | None) -> Any:
    if blob is None:
        return None
    return json.loads(decrypt(blob))


def rotate(blob: bytes | memoryview) -> bytes:
    """Re-encrypt a token with the primary key."""
    return _fernet().rotate(bytes(blob))


def generate_key() -> str:
    return Fernet.generate_key().decode()
