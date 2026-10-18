"""What Custodian did, searchable - and undo where an action can be reversed.

Every tool call is already in the audit log (tool_calls, encrypted). This module reads it back
for you: search() answers "what did I send Tanishka yesterday?" and "which files did you delete
this week?"; undo() reverses the newest action that can be reversed, or one you pick in the
app's activity list.

What can be undone is decided by the desktop app when it acts: with its result it reports how to
put things back - the volume before, the brightness before, where a moved file came from, the
Recycle Bin item a deleted file became, the files a folder sort moved. The server keeps that
record (tool_calls.undo, encrypted) out of what the model sees, and only the functions in
UNDO_FUNCTIONS can be sent back; the model can't send arbitrary "undo" calls, only ask for the
stored one to run. A sent WhatsApp message, a closed window's unsaved work or a locked screen
can't be undone, and Custodian says so.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from . import db, desktop, permissions
from .crypto import decrypt, decrypt_json, encrypt, encrypt_json

log = logging.getLogger(__name__)

UNDO_HOURS = 24  # an action older than this isn't undone by "undo that"
# What the desktop app may hand back as the way to reverse something.
UNDO_FUNCTIONS = frozenset({
    "set_volume", "media", "set_brightness", "set_theme", "do_not_disturb", "restore_deleted", "move_file", "undo_moves",
    "remove_empty_folder", "recycle_created", "restore_backup", "cancel_timer", "open",
})
SEARCH_LIMIT = 10
_WORD = re.compile(r"[\w']+")


class NothingToUndo(LookupError):
    """There's no action that can still be undone; the message is safe to show."""


def clean_undo(raw: object) -> dict | None:
    """A desktop app's undo record, if it's one the server will ever send back."""
    if not isinstance(raw, dict) or raw.get("function") not in UNDO_FUNCTIONS or not isinstance(raw.get("args", {}), dict):
        return None
    label = str(raw.get("label") or "undo that")[:200]
    return {"function": raw["function"], "args": raw.get("args", {}), "label": label}


def record(call_id: int, undo: dict) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE tool_calls SET undo = ? WHERE id = ?", (encrypt_json(undo), call_id))


def _rows(conn, user_id: int, where: str = "", params: tuple = (), limit: int = 200):  # noqa: ANN001, ANN202
    return conn.execute(
        f"""
        SELECT tc.*, t.name AS tool_name, c.id AS conversation_id FROM tool_calls tc
        JOIN tools t ON t.id = tc.tool_id JOIN messages m ON m.id = tc.message_id
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.user_id = ? {where} ORDER BY tc.id DESC LIMIT ?
        """,
        (user_id, *params, limit),
    ).fetchall()


def _describe(row) -> dict:  # noqa: ANN001 - sqlite3.Row
    args_text = decrypt(row["args"])
    try:
        args = json.loads(args_text)
    except json.JSONDecodeError:
        args = args_text
    undo = decrypt_json(row["undo"]) if row["undo"] else None
    return {
        "id": row["id"],
        "tool": row["tool_name"],
        "function": row["function"],
        "status": row["status"],
        "args": args,
        "result": decrypt_json(row["result"]) if row["result"] else None,
        "error": row["error"],
        "when": db.iso(row["created_at"]),
        "conversation_id": row["conversation_id"],
        "can_undo": bool(undo) and row["undone_at"] is None and row["status"] == "succeeded",
        "undo": undo["label"] if undo else None,
        "undone_at": db.iso(row["undone_at"]),
    }


def recent(user_id: int, limit: int = 50) -> list[dict]:
    """The newest actions (not blocked attempts), for the app's activity list."""
    with db.connect() as conn:
        rows = _rows(conn, user_id, "AND tc.status IN ('succeeded', 'failed', 'timed_out')", limit=limit)
    return [_describe(r) for r in rows]


def search(user_id: int, query: str = "", days: int = 7, limit: int = SEARCH_LIMIT) -> list[dict]:
    """Actions from the last `days` days whose function, arguments or result mention every word
    of `query` (any word for a long query), newest first."""
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))).strftime(db.DB_TIME_FORMAT)
    words = [w for w in _WORD.findall(query.lower()) if len(w) > 2 and w not in _SKIP]
    with db.connect() as conn:
        rows = _rows(conn, user_id, "AND tc.created_at >= ? AND tc.status != 'rejected'", (since,), limit=2000)
    found = []
    for row in rows:
        item = _describe(row)
        haystack = " ".join([item["tool"], item["function"].replace("_", " "), json.dumps(item["args"], ensure_ascii=False),
                             json.dumps(item["result"], ensure_ascii=False) if item["result"] else ""]).lower()
        hits = sum(1 for w in words if w in haystack)
        if not words or hits == len(words) or (len(words) > 2 and hits >= len(words) - 1):
            found.append(item)
        if len(found) >= limit:
            break
    return found


_SKIP = frozenset("what did you the and for with from that this when have has was were yesterday today week ago last "
                  "which who whom send sent open opened delete deleted play played show tell me my".split())


def for_model(items: list[dict]) -> list[dict]:
    """search() results as the model reads them: short, no ids it could misuse beyond undo."""
    out = []
    for item in items:
        result = item["result"]
        if isinstance(result, dict):
            result = {k: v for k, v in result.items() if k not in ("image_b64",)}
        out.append({"id": item["id"], "when": item["when"], "action": f"{item['tool']}_{item['function']}",
                    "status": item["status"], "args": item["args"], "result": result, "error": item["error"],
                    "can_undo": item["can_undo"], "undo": item["undo"] if item["can_undo"] else None})
    return out


def latest_undoable(user_id: int, *, within_minutes: int | None = None) -> dict | None:
    since = datetime.now(timezone.utc) - (timedelta(minutes=within_minutes) if within_minutes else timedelta(hours=UNDO_HOURS))
    with db.connect() as conn:
        rows = _rows(conn, user_id, "AND tc.undo IS NOT NULL AND tc.undone_at IS NULL AND tc.status = 'succeeded' "
                     "AND tc.created_at >= ?", (since.strftime(db.DB_TIME_FORMAT),), limit=1)
    return _describe(rows[0]) if rows else None


def undo(user_id: int, call_id: int | None = None, *, message_id: int | None = None) -> dict:
    """Reverse one action: `call_id`, or the newest that can still be undone. Runs the stored
    undo record on the PC (no dialog: it puts back what Custodian itself changed), logs it as a
    tool call, and marks the original undone. Raises NothingToUndo, PermissionError or
    RuntimeError (the PC's own error)."""
    with db.connect() as conn:
        if call_id is not None:
            rows = _rows(conn, user_id, "AND tc.id = ?", (call_id,), limit=1)
        else:
            since = (datetime.now(timezone.utc) - timedelta(hours=UNDO_HOURS)).strftime(db.DB_TIME_FORMAT)
            rows = _rows(conn, user_id, "AND tc.undo IS NOT NULL AND tc.undone_at IS NULL AND tc.status = 'succeeded' "
                         "AND tc.created_at >= ?", (since,), limit=1)
        row = rows[0] if rows else None
        pc = conn.execute("SELECT id FROM tools WHERE name = 'pc'").fetchone()
    if row is None or row["undo"] is None:
        raise NothingToUndo("There's nothing I did recently that I can undo." if call_id is None
                            else "That action can't be undone.")
    if row["undone_at"] is not None:
        raise NothingToUndo("That was already undone.")
    if not permissions.check(user_id, pc["id"], "write"):
        raise PermissionError("Undoing needs the “PC · write” switch in Tools & Data.")
    step = decrypt_json(row["undo"])
    log_message = message_id or row["message_id"]
    with db.connect() as conn:
        log_id = conn.execute(
            "INSERT INTO tool_calls (message_id, tool_id, scope, function, args, status) VALUES (?, ?, 'write', ?, ?, 'running')",
            (log_message, pc["id"], f"undo_{step['function']}", encrypt(json.dumps(step["args"], ensure_ascii=False))),
        ).lastrowid
    try:
        result = desktop.run(user_id, step["function"], step["args"], confirm=False, description=f"Undo: {step['label']}")
    except Exception as exc:  # noqa: BLE001 - recorded, then reported
        with db.connect() as conn:
            conn.execute("UPDATE tool_calls SET status = 'failed', error = ?, finished_at = datetime('now') WHERE id = ?",
                         (str(exc)[:500], log_id))
        raise RuntimeError(f"I couldn't {step['label']}: {exc}") from None
    with db.connect() as conn:
        conn.execute("UPDATE tool_calls SET status = 'succeeded', result = ?, finished_at = datetime('now') WHERE id = ?",
                     (encrypt_json(result), log_id))
        conn.execute("UPDATE tool_calls SET undone_at = datetime('now') WHERE id = ?", (row["id"],))
    log.info("undid tool call %s (%s)", row["id"], step["function"])
    return {"undone": step["label"], "of": f"{row['tool_name']}_{row['function']}", "result": result}


# The internal tools the model can call ---------------------------------------------------------

UNDO_TOOL = {
    "type": "function",
    "function": {
        "name": "undo_last_action",
        "description": ("Undo something Custodian did on the PC: puts the volume, brightness or theme back, restores a "
                        "deleted file from the Recycle Bin, moves a moved file back, un-sorts a sorted folder. Without "
                        "an id it undoes the newest action that can be undone; ids come from search_activity."),
        "parameters": {"type": "object", "properties": {"id": {"type": "integer", "description": "Optional: which action."}},
                       "additionalProperties": False},
    },
}
SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_activity",
        "description": ("Search what Custodian did for the user - messages it sent, files it opened, moved or deleted, "
                        "music it played, settings it changed - by words in it, over the last few days."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to look for, e.g. 'Tanishka' or 'delete'. Empty = everything."},
                "days": {"type": "integer", "minimum": 1, "maximum": 90, "description": "How many days back (default 7)."},
            },
            "additionalProperties": False,
        },
    },
}
INTERNAL_NAMES = {"undo_last_action", "search_activity"}
ASKS_ABOUT_ACTIVITY = re.compile(
    r"\b(?:undo|revert|put\s+(?:it|that|them|everything)\s+back|restore|take\s+that\s+back|what\s+did\s+(?:i|you)\s+"
    r"(?:send|open|delete|move|play|do|close|change)|did\s+you\s+(?:send|delete|open|move)|what\s+have\s+you\s+done|history\s+of"
    r"|activity|which\s+files\s+did)\b",
    re.IGNORECASE,
)


def run_internal(user_id: int, name: str, args: dict, *, message_id: int) -> str:
    """The tool message for one of the internal tools above."""
    try:
        if name == "search_activity":
            items = search(user_id, str(args.get("query") or ""), int(args.get("days") or 7))
            return json.dumps({"function": name, "status": "succeeded", "result": {"actions": for_model(items)}},
                              ensure_ascii=False, default=str)
        result = undo(user_id, int(args["id"]) if args.get("id") else None, message_id=message_id)
        return json.dumps({"function": name, "status": "succeeded", "result": result}, ensure_ascii=False, default=str)
    except (NothingToUndo, PermissionError, RuntimeError, desktop.DesktopUnavailable) as exc:
        return json.dumps({"function": name, "status": "failed", "error": str(exc)}, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        return json.dumps({"function": name, "status": "rejected", "error": f"invalid arguments - {exc}"})
