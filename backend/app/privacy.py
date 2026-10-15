"""Privacy controls: where data goes, what is stored, export, erase, how long chats are kept,
off-the-record chats, and withholding secrets from the model.

- summary(): where each kind of data is sent (the model and the embedding service: this
  device, the local network or the internet), what is stored on this server, and the
  current settings.
- Retention: with retention_days set, the hourly job deletes chats whose last message is
  older (purge_old_chats). Memories stay until you delete them - they are the lifetime
  memory - but the message a memory came from goes, so that link is cleared.
- Off-the-record chats (conversations.private) are never recalled in other chats, never
  embedded and never saved to memory, and are deleted PRIVATE_CHAT_HOURS after their last
  message.
- Withholding secrets: when the model runs on the internet (redact_secrets 'auto', the
  default) or always ('on'), everything sent to it passes through sensitive.redact(), so a
  password typed into a chat never leaves this server. The stored message keeps the
  original, encrypted.
- export(): everything Custodian holds about you, decrypted, as one JSON document.
- erase(): deletes every conversation, memory, task, tool call, note and calendar event,
  then compacts the database files so the deleted text doesn't linger in them.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import db, embeddings, history_search, memory, permissions, plans, sensitive, tasks, vectors
from .config import get_settings
from .crypto import decrypt, decrypt_json

log = logging.getLogger(__name__)

PRIVATE_CHAT_HOURS = 24
REDACT_MODES = ("auto", "on", "off")
EXPORT_FORMAT = "custodian-export/1"
TOOL_TABLES = {"calendar": ("calendar.db", "events"), "notes": ("notes.db", "notes")}
_UNSET: Any = object()


# Settings ----------------------------------------------------------------------------------

def settings_for(user_id: int) -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT retention_days, redact_secrets FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
    return {"retention_days": row["retention_days"], "redact_secrets": row["redact_secrets"]} if row else {
        "retention_days": None,
        "redact_secrets": "auto",
    }


def update_settings(user_id: int, *, retention_days: int | None = _UNSET, redact_secrets: str | None = None) -> dict:
    current = settings_for(user_id)
    if retention_days is not _UNSET:
        if retention_days is not None and not 1 <= retention_days <= 3650:
            raise ValueError("retention_days must be between 1 and 3650, or null to keep chats")
        current["retention_days"] = retention_days
    if redact_secrets is not None:
        if redact_secrets not in REDACT_MODES:
            raise ValueError("redact_secrets must be auto, on or off")
        current["redact_secrets"] = redact_secrets
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO user_settings (user_id, retention_days, redact_secrets) VALUES (?, ?, ?)
            ON CONFLICT (user_id) DO UPDATE SET retention_days = excluded.retention_days,
              redact_secrets = excluded.redact_secrets, updated_at = datetime('now')
            """,
            (user_id, current["retention_days"], current["redact_secrets"]),
        )
    if retention_days is not _UNSET and retention_days is not None:
        purge_old_chats()  # a shorter retention applies straight away
    return current


# Where data goes ---------------------------------------------------------------------------

def endpoint_location(url: str) -> str:
    """'this device', 'local network' or 'internet' for a service URL (no DNS lookup)."""
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return "internet"
    if host == "localhost" or host.endswith(".localhost"):
        return "this device"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        local = host.endswith((".local", ".lan", ".internal", ".home.arpa")) or "." not in host  # e.g. "ollama" in Docker
        return "local network" if local else "internet"
    if address.is_loopback:
        return "this device"
    return "local network" if address.is_private or address.is_link_local else "internet"


def model_location() -> str:
    settings = get_settings()
    return "offline" if settings.model_backend == "stub" else endpoint_location(settings.nebius_base_url)


def redaction_active(user_id: int) -> bool:
    mode = settings_for(user_id)["redact_secrets"]
    return mode == "on" or (mode == "auto" and model_location() == "internet")


def prepare_outbound(messages: list[dict], active: bool) -> tuple[list[dict], int]:
    """The messages as they should leave this server, and how many secrets were withheld."""
    if not active:
        return messages, 0
    out: list[dict] = []
    withheld = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) and content:
            text, count = sensitive.redact(content)
            if count:
                message, withheld = {**message, "content": text}, withheld + count
        out.append(message)
    return out, withheld


def _service(url: str, name: str) -> dict:
    return {"name": name, "host": urlsplit(url).hostname, "location": endpoint_location(url)}


def _tool_db(tool: str) -> Path:
    filename, _ = TOOL_TABLES[tool]
    return get_settings().tool_data_dir / tool / filename


def _tool_rows(user_id: int, tool: str, *, count_only: bool = False) -> Any:
    """A tool's rows for this user, read straight from its own database (read-only)."""
    path = _tool_db(tool)
    if not path.exists():
        return 0 if count_only else []
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        table = TOOL_TABLES[tool][1]
        if count_only:
            return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (user_id,)).fetchone()[0]
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE user_id = ? ORDER BY id", (user_id,))]
    except sqlite3.OperationalError:  # the tool hasn't created its table yet
        return 0 if count_only else []
    finally:
        conn.close()


def summary(user_id: int) -> dict:
    settings = get_settings()
    prefs = settings_for(user_id)
    embedder = embeddings.get_embedder()
    with db.connect() as conn:
        def count(sql: str) -> int:
            return conn.execute(sql, (user_id,)).fetchone()[0]

        stored = {
            "conversations": count("SELECT COUNT(*) FROM conversations WHERE user_id = ?"),
            "messages": count(
                "SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id "
                "WHERE c.user_id = ? AND m.role IN ('user', 'assistant') AND length(m.content) > 0"
            ),
            "memories": count("SELECT COUNT(*) FROM memories WHERE user_id = ?"),
            "tool_calls": count(
                "SELECT COUNT(*) FROM tool_calls tc JOIN messages m ON m.id = tc.message_id "
                "JOIN conversations c ON c.id = m.conversation_id WHERE c.user_id = ?"
            ),
            "tasks": count("SELECT COUNT(*) FROM tasks WHERE user_id = ?"),
        }
        oldest = conn.execute(
            "SELECT MIN(m.created_at) FROM messages m JOIN conversations c ON c.id = m.conversation_id WHERE c.user_id = ?",
            (user_id,),
        ).fetchone()[0]
    stored["calendar_events"] = _tool_rows(user_id, "calendar", count_only=True)
    stored["notes"] = _tool_rows(user_id, "notes", count_only=True)
    return {
        "model": (
            {"name": "Built-in offline model", "host": None, "location": "offline"}
            if settings.model_backend == "stub"
            else _service(settings.nebius_base_url, settings.nebius_model_id)
        ),
        "embeddings": _service(settings.embedding_base_url, embedder.model) if embedder else None,
        "redaction": {"mode": prefs["redact_secrets"], "active": redaction_active(user_id)},
        "retention_days": prefs["retention_days"],
        "private_chat_hours": PRIVATE_CHAT_HOURS,
        "stored": stored,
        "oldest_message_at": db.iso(oldest),
        # Messages, memories, tool arguments and results, and embeddings are Fernet-encrypted;
        # the keyword index keeps a plaintext copy of memory text (see app/memory.py).
        "encrypted": ["messages", "memories", "tool calls", "embeddings"],
        "plaintext": ["memory search index", "notes and calendar events (in each tool's own database)"],
    }


# Retention ---------------------------------------------------------------------------------

def purge_old_chats() -> int:
    """Delete off-the-record chats a day after their last message, and every chat older than
    its owner's retention setting. Returns how many conversations were deleted."""
    last_activity = "COALESCE((SELECT MAX(created_at) FROM messages WHERE conversation_id = c.id), c.started_at)"
    with db.connect() as conn:
        ids = [
            r["id"]
            for r in conn.execute(
                f"""
                SELECT c.id FROM conversations c LEFT JOIN user_settings s ON s.user_id = c.user_id
                WHERE (c.private = 1 AND {last_activity} <= datetime('now', ?))
                   OR (s.retention_days IS NOT NULL AND {last_activity} <= datetime('now', '-' || s.retention_days || ' days'))
                """,
                (f"-{PRIVATE_CHAT_HOURS} hours",),
            )
        ]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        message_ids = [r["id"] for r in conn.execute(f"SELECT id FROM messages WHERE conversation_id IN ({marks})", ids)]
        conn.execute(f"DELETE FROM conversations WHERE id IN ({marks})", ids)
    history_search.forget(message_ids)
    vectors.forget("message", message_ids)
    log.info("deleted %d conversation(s) past their retention", len(ids))
    return len(ids)


# Export and erase --------------------------------------------------------------------------

def _json_or_text(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def export(user_id: int) -> dict:
    """Everything Custodian keeps about the user, decrypted (GDPR-style data portability)."""
    with db.connect() as conn:
        account = conn.execute("SELECT email, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
        conversations = []
        for c in conn.execute("SELECT * FROM conversations WHERE user_id = ? ORDER BY id", (user_id,)).fetchall():
            messages = []
            for m in conn.execute(
                "SELECT id, role, content, model, created_at FROM messages WHERE conversation_id = ? ORDER BY id", (c["id"],)
            ):
                text = decrypt(m["content"])
                if m["role"] == "assistant" and not text.strip():
                    continue  # a turn that only called tools, or a draft the accuracy check replaced
                messages.append({"id": m["id"], "role": m["role"], "content": text, "model": m["model"], "created_at": db.iso(m["created_at"])})
            conversations.append(
                {
                    "id": c["id"],
                    "channel": c["channel"],
                    "private": bool(c["private"]),
                    "started_at": db.iso(c["started_at"]),
                    "messages": messages,
                }
            )
        tool_calls = [
            {
                "id": r["id"],
                "message_id": r["message_id"],
                "tool": r["tool"],
                "function": r["function"],
                "scope": r["scope"],
                "status": r["status"],
                "args": _json_or_text(decrypt(r["args"])),
                "result": decrypt_json(r["result"]),
                "error": r["error"],
                "created_at": db.iso(r["created_at"]),
                "finished_at": db.iso(r["finished_at"]),
            }
            for r in conn.execute(
                """
                SELECT tc.*, t.name AS tool FROM tool_calls tc JOIN tools t ON t.id = tc.tool_id
                JOIN messages m ON m.id = tc.message_id JOIN conversations c ON c.id = m.conversation_id
                WHERE c.user_id = ? ORDER BY tc.id
                """,
                (user_id,),
            )
        ]
        usage_rows = [dict(r) for r in conn.execute("SELECT * FROM usage_daily WHERE user_id = ? ORDER BY day", (user_id,))]
    task_list = tasks.list_tasks(user_id)
    for task in task_list:
        task["runs"] = tasks.list_runs(user_id, task["id"], limit=100_000)
    state = plans.current()
    return {
        "format": EXPORT_FORMAT,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "account": {"email": account["email"], "created_at": db.iso(account["created_at"])} if account else None,
        "settings": settings_for(user_id),
        "plan": {"id": state.plan.id, "name": state.plan.name, "source": state.source},
        "memories": memory.list_memories(user_id, limit=1_000_000),
        "conversations": conversations,
        "tool_calls": tool_calls,
        "tasks": task_list,
        "permissions": permissions.list_for_user(user_id),
        "permission_events": permissions.events(user_id, limit=1_000_000),
        "calendar_events": _tool_rows(user_id, "calendar"),
        "notes": _tool_rows(user_id, "notes"),
        "usage": usage_rows,
    }


def _compact(path: Path) -> None:
    """Rebuild a database file without its free pages and empty its write-ahead log."""
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as exc:  # another connection was busy; secure_delete already zeroed the rows
        log.warning("couldn't compact %s after erasing: %s", path.name, exc)
    finally:
        conn.close()


def erase(user_id: int) -> dict:
    """Delete everything the user has stored - conversations (with their tool calls),
    memories, tasks, switch history, notes and calendar events - and return the counts.
    The account, its switches, privacy settings, license and usage counters stay."""
    from . import scheduler  # here: scheduler -> orchestrator -> this module

    with db.connect() as conn:
        message_ids = [
            r["id"]
            for r in conn.execute(
                "SELECT m.id FROM messages m JOIN conversations c ON c.id = m.conversation_id WHERE c.user_id = ?", (user_id,)
            )
        ]
        task_ids = [r["id"] for r in conn.execute("SELECT id FROM tasks WHERE user_id = ?", (user_id,))]
        counts = {
            "messages": len(message_ids),
            "conversations": conn.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,)).rowcount,
            "memories": conn.execute("DELETE FROM memories WHERE user_id = ?", (user_id,)).rowcount,
            "tasks": conn.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,)).rowcount,
            "switch_history": conn.execute(
                "DELETE FROM permission_events WHERE permission_id IN (SELECT id FROM permissions WHERE user_id = ?)", (user_id,)
            ).rowcount,
        }
        memory.purge_search_index(conn)
    for tool, (_, table) in TOOL_TABLES.items():
        path = _tool_db(tool)
        counts["calendar_events" if tool == "calendar" else "notes"] = 0
        if not path.exists():
            continue
        conn = sqlite3.connect(path, timeout=10)
        try:
            conn.execute("PRAGMA secure_delete = ON")
            counts["calendar_events" if tool == "calendar" else "notes"] = conn.execute(
                f"DELETE FROM {table} WHERE user_id = ?", (user_id,)
            ).rowcount
            conn.commit()
        except sqlite3.OperationalError:  # no table yet: nothing to delete
            pass
        finally:
            conn.close()
        _compact(path)
    _compact(db.db_path())
    history_search.reset()
    vectors.reset()
    for task_id in task_ids:
        scheduler.sync_task(task_id)
    log.warning("erased all stored data for user %s", user_id)
    return counts
