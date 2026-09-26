"""Fernet encrypt/decrypt for memory content, key from APP_SECRET_KEY. Step 2 — see docs/PLAN.md."""


def encrypt(text: str) -> bytes:
    raise NotImplementedError("Step 2: from cryptography.fernet import Fernet; Fernet(key).encrypt(text.encode())")


def decrypt(blob: bytes) -> str:
    raise NotImplementedError("Step 2: Fernet(key).decrypt(blob).decode()")
