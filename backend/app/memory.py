"""Encrypted memory store: write/recall via the memories_fts virtual table. Step 2 — see docs/PLAN.md."""


def write(user_id: int, content: str, type: str = "episodic") -> int:
    raise NotImplementedError(
        "Step 2: in one transaction, INSERT encrypt(content) into memories, then INSERT the plaintext "
        "into memories_fts with rowid = the new memories.id"
    )


def recall(user_id: int, query: str, k: int = 3) -> list[dict]:
    raise NotImplementedError(
        "Step 2: memories_fts MATCH ? JOIN memories ON memories.id = memories_fts.rowid, filtered to "
        "user_id = ? and unexpired rows, ORDER BY rank LIMIT k, then decrypt() each row"
    )
