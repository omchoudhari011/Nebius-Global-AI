"""Encrypted memory store: write/recall via the memories_fts virtual table. Step 2 — see docs/PLAN.md."""


def write(user_id: int, content: str, type: str = "episodic") -> int:
    raise NotImplementedError("Step 2: encrypt(content) before INSERT into memories + memories_fts")


def recall(user_id: int, query: str, k: int = 3) -> list[dict]:
    raise NotImplementedError("Step 2: memories_fts MATCH ?, then decrypt() each matching row")
