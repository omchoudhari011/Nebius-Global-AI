"""Your verdict on a reply - good, or wrong and what it should have said - and, only when you
tick "use this to improve the model", a training example made from it.

    POST   /messages/{id}/feedback   {rating: 1 | -1, correction?, share}
    DELETE /messages/{id}/feedback

A training example is the exact prompt and tools behind the reply (messages.trace, kept
encrypted with the chat) followed by the answer you approved: the reply itself for a thumbs
up, your correction for a thumbs down. backend/finetune/from_feedback.py collects them into
the fine-tuning data on this machine; nothing is sent anywhere. An example whose text looks
like it holds a secret is never used, and deleting the chat deletes its feedback too.
"""

from __future__ import annotations

from . import db, grounding, sensitive
from .crypto import decrypt, decrypt_json, encrypt

MAX_CORRECTION = 4000


class FeedbackError(ValueError):
    """The message can't take feedback; the message is safe to show."""


def _final_reply(conn, user_id: int, message_id: int):  # noqa: ANN001, ANN202 - sqlite3 objects
    row = conn.execute(
        """
        SELECT m.id, m.role, m.checks FROM messages m JOIN conversations c ON c.id = m.conversation_id
        WHERE m.id = ? AND c.user_id = ?
        """,
        (message_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError(message_id)
    if row["role"] != "assistant" or row["checks"] is None:
        raise FeedbackError("Only Custodian's final replies can be rated.")
    return row


def save(user_id: int, message_id: int, rating: int, correction: str | None = None, share: bool = False) -> dict:
    if rating not in (1, -1):
        raise FeedbackError("rating must be 1 or -1")
    correction = (correction or "").strip() or None
    if correction and len(correction) > MAX_CORRECTION:
        raise FeedbackError(f"keep the correction under {MAX_CORRECTION} characters")
    if rating == 1:
        correction = None
    with db.connect() as conn:
        _final_reply(conn, user_id, message_id)
        conn.execute(
            """
            INSERT INTO message_feedback (message_id, rating, correction, share) VALUES (?, ?, ?, ?)
            ON CONFLICT (message_id) DO UPDATE SET rating = excluded.rating, correction = excluded.correction,
              share = excluded.share, updated_at = datetime('now')
            """,
            (message_id, rating, encrypt(correction) if correction else None, int(share)),
        )
    return {"rating": rating, "correction": correction, "share": bool(share)}


def clear(user_id: int, message_id: int) -> bool:
    with db.connect() as conn:
        _final_reply(conn, user_id, message_id)
        return conn.execute("DELETE FROM message_feedback WHERE message_id = ?", (message_id,)).rowcount > 0


def for_messages(conn, message_ids: list[int]) -> dict[int, dict]:  # noqa: ANN001 - sqlite3.Connection
    if not message_ids:
        return {}
    marks = ",".join("?" * len(message_ids))
    rows = conn.execute(f"SELECT * FROM message_feedback WHERE message_id IN ({marks})", message_ids).fetchall()
    return {
        r["message_id"]: {"rating": r["rating"], "correction": decrypt(r["correction"]) if r["correction"] else None,
                          "share": bool(r["share"])}
        for r in rows
    }


def counts(user_id: int) -> dict:
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT SUM(f.rating = 1) AS good, SUM(f.rating = -1) AS bad, SUM(f.share) AS shared
            FROM message_feedback f JOIN messages m ON m.id = f.message_id
            JOIN conversations c ON c.id = m.conversation_id WHERE c.user_id = ?
            """,
            (user_id,),
        ).fetchone()
    return {"good": row["good"] or 0, "bad": row["bad"] or 0, "shared": row["shared"] or 0}


def _without_repairs(messages: list[dict]) -> list[dict]:
    """Drop a rejected draft and Custodian's repair request: the example should show the
    model answering right the first time."""
    out: list[dict] = []
    for message in messages:
        if message["role"] == "user" and str(message.get("content", "")).startswith(grounding.REPAIR_HEADER):
            if out and out[-1]["role"] == "assistant" and not out[-1].get("tool_calls"):
                out.pop()
            continue
        out.append(message)
    return out


def training_examples(user_id: int | None = None) -> list[dict]:
    """Every shared rating as {messages, tools, kind}: a thumbs up keeps the reply, a thumbs
    down with a correction uses the correction; a thumbs down without one teaches nothing."""
    sql = """
        SELECT f.rating, f.correction, m.content, m.trace FROM message_feedback f
        JOIN messages m ON m.id = f.message_id JOIN conversations c ON c.id = m.conversation_id
        WHERE f.share = 1 AND m.trace IS NOT NULL AND c.private = 0
    """
    params: list = []
    if user_id is not None:
        sql += " AND c.user_id = ?"
        params.append(user_id)
    examples = []
    with db.connect() as conn:
        for row in conn.execute(sql + " ORDER BY f.message_id", params):
            answer = decrypt(row["content"]) if row["rating"] == 1 else (decrypt(row["correction"]) if row["correction"] else None)
            if not answer:
                continue
            answer = answer.split("\n\n" + grounding.NOTE_PREFIX)[0].strip()  # never teach the check's own note
            trace = decrypt_json(row["trace"])
            messages = _without_repairs(trace.get("messages") or []) + [{"role": "assistant", "content": answer}]
            if any(sensitive.looks_secret(str(m.get("content") or "")) for m in messages if m["role"] in ("user", "assistant")):
                continue
            examples.append({"kind": "feedback_good" if row["rating"] == 1 else "feedback_corrected",
                             "messages": messages, "tools": trace.get("tools") or []})
    return examples


def preference_pairs(user_id: int | None = None) -> list[dict]:
    """Every shared thumbs-down with a correction as {messages, chosen, rejected, tools}: the
    prompt the model saw, what you said it should have said, and what it said. Training prefers
    the first over the second (train_lora.py --pairs), so a mistake you corrected doesn't come back.
    The same skips as training_examples(): off the record, secrets, the check's own note."""
    sql = """
        SELECT f.correction, m.content, m.trace, f.updated_at FROM message_feedback f
        JOIN messages m ON m.id = f.message_id JOIN conversations c ON c.id = m.conversation_id
        WHERE f.share = 1 AND f.rating = -1 AND f.correction IS NOT NULL AND m.trace IS NOT NULL AND c.private = 0
    """
    params: list = []
    if user_id is not None:
        sql += " AND c.user_id = ?"
        params.append(user_id)
    pairs = []
    with db.connect() as conn:
        for row in conn.execute(sql + " ORDER BY f.message_id", params):
            chosen = decrypt(row["correction"]).strip()
            rejected = decrypt(row["content"]).split("\n\n" + grounding.NOTE_PREFIX)[0].strip()
            if not chosen or not rejected or chosen == rejected:
                continue
            trace = decrypt_json(row["trace"])
            messages = _without_repairs(trace.get("messages") or [])
            texts = [str(m.get("content") or "") for m in messages if m["role"] in ("user", "assistant")] + [chosen, rejected]
            if not messages or any(sensitive.looks_secret(t) for t in texts):
                continue
            pairs.append({"kind": "feedback_pair", "messages": messages, "chosen": chosen, "rejected": rejected,
                          "tools": trace.get("tools") or [], "rated_at": row["updated_at"]})
    return pairs


def shared_since(user_id: int, since: str | None) -> int:
    """How many shared ratings were made or changed after `since` (a database time; None = ever)."""
    with db.connect() as conn:
        return conn.execute(
            """
            SELECT COUNT(*) FROM message_feedback f JOIN messages m ON m.id = f.message_id
            JOIN conversations c ON c.id = m.conversation_id
            WHERE c.user_id = ? AND f.share = 1 AND c.private = 0 AND (? IS NULL OR f.updated_at > ?)
              AND (f.rating = 1 OR f.correction IS NOT NULL)
            """,
            (user_id, since, since),
        ).fetchone()[0]
