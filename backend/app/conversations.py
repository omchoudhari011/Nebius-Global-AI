"""Reading conversations back: thread lists, message history and the `used` field.

`used` is rebuilt from foreign keys (messages.skill_id, message_memories, message_recalls,
tool_calls and memories.source_message_id) rather than stored as JSON, so deleting a memory
or a conversation can never leave a stale reference behind. The orchestrator returns the
same shape from /chat.
"""

from __future__ import annotations

import sqlite3

from . import db, feedback, history_search, vectors
from .crypto import decrypt, decrypt_json


def _memory(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "type": row["type"], "content": decrypt(row["content"])}


def build_used(
    conn: sqlite3.Connection,
    final_message_id: int,
    assistant_message_ids: list[int],
    source_message_id: int | None,
) -> dict:
    """What shaped one reply: the memories in its context (`matched` marks the ones picked for
    the message), earlier messages recalled from any conversation, tool calls, skill version,
    and memories saved."""
    memories = conn.execute(
        """
        SELECT m.*, mm.matched FROM message_memories mm JOIN memories m ON m.id = mm.memory_id
        WHERE mm.message_id = ? ORDER BY mm.matched DESC, m.id
        """,
        (final_message_id,),
    ).fetchall()
    earlier = conn.execute(
        """
        SELECT m.id, m.conversation_id, m.content, m.created_at FROM message_recalls r
        JOIN messages m ON m.id = r.recalled_id
        WHERE r.message_id = ? ORDER BY m.id
        """,
        (final_message_id,),
    ).fetchall()
    tools: list[sqlite3.Row] = []
    if assistant_message_ids:
        marks = ",".join("?" * len(assistant_message_ids))
        tools = conn.execute(
            f"""
            SELECT tc.id, t.name AS tool, tc.function, tc.scope, tc.status, tc.error, tc.latency_ms
            FROM tool_calls tc JOIN tools t ON t.id = tc.tool_id
            WHERE tc.message_id IN ({marks}) ORDER BY tc.id
            """,
            assistant_message_ids,
        ).fetchall()
    skill = conn.execute(
        "SELECT s.id, s.name, s.version FROM messages m JOIN skills s ON s.id = m.skill_id WHERE m.id = ?",
        (final_message_id,),
    ).fetchone()
    remembered: list[sqlite3.Row] = []
    if source_message_id is not None:
        remembered = conn.execute(
            "SELECT * FROM memories WHERE source_message_id = ? ORDER BY id", (source_message_id,)
        ).fetchall()
    checks = conn.execute("SELECT checks FROM messages WHERE id = ?", (final_message_id,)).fetchone()
    return {
        "memories": [{**_memory(m), "matched": bool(m["matched"])} for m in memories],
        "earlier": [
            {
                "id": e["id"],
                "conversation_id": e["conversation_id"],
                "created_at": db.iso(e["created_at"]),
                "content": decrypt(e["content"]),
            }
            for e in earlier
        ],
        "tools": [
            {
                "id": t["id"],
                "tool": t["tool"],
                "function": t["function"],
                "scope": t["scope"],
                "status": t["status"],
                "error": t["error"],
                "latency_ms": t["latency_ms"],
            }
            for t in tools
        ],
        "skill": {"id": skill["id"], "name": skill["name"], "version": skill["version"]} if skill else None,
        "remembered": [_memory(m) for m in remembered],
        # Accuracy and privacy checks on the reply (app/grounding.py); None for replies saved
        # before the checks existed.
        "checks": decrypt_json(checks["checks"]) if checks and checks["checks"] else None,
    }


def list_conversations(user_id: int, channel: str | None = None, limit: int = 30) -> list[dict]:
    sql = """
        SELECT c.id, c.channel, c.private, c.started_at,
               (SELECT MAX(created_at) FROM messages WHERE conversation_id = c.id) AS last_message_at,
               tr.id AS run_id, tr.status AS run_status, t.id AS task_id, t.name AS task_name
        FROM conversations c
        LEFT JOIN task_runs tr ON tr.conversation_id = c.id
        LEFT JOIN tasks t ON t.id = tr.task_id
        WHERE c.user_id = ?
    """
    params: list = [user_id]
    if channel:
        sql += " AND c.channel = ?"
        params.append(channel)
    sql += " ORDER BY c.id DESC LIMIT ?"
    params.append(limit)
    out = []
    with db.connect() as conn:
        for r in conn.execute(sql, params).fetchall():
            last = conn.execute(
                """
                SELECT content FROM messages
                WHERE conversation_id = ? AND role IN ('user', 'assistant') AND length(content) > 0
                ORDER BY id DESC LIMIT 1
                """,
                (r["id"],),
            ).fetchone()
            preview = decrypt(last["content"]).strip() if last else ""
            out.append(
                {
                    "id": r["id"],
                    "channel": r["channel"],
                    "private": bool(r["private"]),
                    "started_at": db.iso(r["started_at"]),
                    "last_message_at": db.iso(r["last_message_at"]),
                    "preview": preview[:140] + ("…" if len(preview) > 140 else ""),
                    "task": (
                        {"id": r["task_id"], "name": r["task_name"], "run_id": r["run_id"], "run_status": r["run_status"]}
                        if r["run_id"]
                        else None
                    ),
                }
            )
    return out


def get_conversation(user_id: int, conversation_id: int) -> dict | None:
    """The thread as the app shows it: each user (or task start) message, then the turn's
    final assistant reply with its `used`. Tool rows and intermediate tool-calling turns
    are folded into `used.tools`."""
    with db.connect() as conn:
        conv = conn.execute(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)
        ).fetchone()
        if conv is None:
            return None
        rows = conn.execute(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()

        items: list[dict] = []
        start_id: int | None = None
        assistants: list[sqlite3.Row] = []

        def flush() -> None:
            if not assistants:
                return
            texts = [(a, decrypt(a["content"])) for a in assistants]
            final, text = next(((a, t) for a, t in reversed(texts) if t.strip()), texts[-1])
            items.append(
                {
                    "id": final["id"],
                    "role": "assistant",
                    "content": text,
                    "created_at": db.iso(final["created_at"]),
                    "used": build_used(conn, final["id"], [a["id"] for a in assistants], start_id),
                    "feedback": feedback.for_messages(conn, [final["id"]]).get(final["id"]),
                }
            )

        for row in rows:
            if row["role"] in ("user", "system"):
                flush()
                assistants = []
                start_id = row["id"]
                items.append(
                    {"id": row["id"], "role": row["role"], "content": decrypt(row["content"]), "created_at": db.iso(row["created_at"])}
                )
            elif row["role"] == "assistant":
                assistants.append(row)
        flush()

        run = conn.execute(
            """
            SELECT tr.id, tr.status, tr.error, tr.started_at, tr.finished_at, t.id AS task_id, t.name
            FROM task_runs tr JOIN tasks t ON t.id = tr.task_id WHERE tr.conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()

    return {
        "id": conv["id"],
        "channel": conv["channel"],
        "private": bool(conv["private"]),
        "started_at": db.iso(conv["started_at"]),
        "task": (
            {
                "id": run["task_id"],
                "name": run["name"],
                "run_id": run["id"],
                "run_status": run["status"],
                "error": run["error"],
                "started_at": db.iso(run["started_at"]),
                "finished_at": db.iso(run["finished_at"]),
            }
            if run
            else None
        ),
        "messages": items,
    }


def delete_conversation(user_id: int, conversation_id: int) -> bool:
    """Delete a thread and its messages; its words and their vectors also leave the
    earlier-chat search (the stored vectors go with the messages, ON DELETE CASCADE)."""
    with db.connect() as conn:
        message_ids = [
            r["id"] for r in conn.execute("SELECT id FROM messages WHERE conversation_id = ?", (conversation_id,))
        ]
        cur = conn.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id))
    if cur.rowcount:
        history_search.forget(message_ids)
        vectors.forget("message", message_ids)
    return cur.rowcount > 0
