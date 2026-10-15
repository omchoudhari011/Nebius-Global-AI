"""Notifications: reminders coming due and scheduled-task results, in the app's inbox and,
optionally, on your phone.

deliver_due() runs every minute (app/scheduler.py): each pending reminder in the reminders
tool's database whose time has come becomes a notification here (Fernet-encrypted), and a
repeating one moves on to its next time. The app shows the inbox, raises a system
notification while it's open, and on phones also schedules the upcoming reminders itself
(GET /reminders), so they fire even when the server can't reach the phone.

Phone push without an app store account: set NTFY_URL to an ntfy topic
(https://ntfy.sh/<a-long-random-topic>, or your own ntfy server) and install the ntfy app.
By default the push says only "Reminder from Custodian"; NTFY_SHOW_TEXT=true includes the
reminder's text, which then passes through that ntfy server.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from . import db
from .config import get_settings
from .crypto import decrypt, encrypt
from .tools import reminders_tool

log = logging.getLogger(__name__)

_transport: httpx.BaseTransport | None = None  # tests swap in a mock


def _reminders_dir():  # noqa: ANN202 - a Path
    return get_settings().tool_data_dir / "reminders"


def add(user_id: int, kind: str, title: str, body: str | None = None, ref: str | None = None, *, push: bool = True) -> int:
    with db.connect() as conn:
        note_id = conn.execute(
            "INSERT INTO notifications (user_id, kind, title, body, ref) VALUES (?, ?, ?, ?, ?)",
            (user_id, kind, encrypt(title), encrypt(body) if body else None, ref),
        ).lastrowid
    if push:
        _push(kind, title, body)
    return note_id


def _push(kind: str, title: str, body: str | None) -> None:
    url = os.getenv("NTFY_URL", "").strip()
    if not url:
        return
    show = os.getenv("NTFY_SHOW_TEXT", "").strip().lower() in ("1", "true", "yes")
    generic = {"reminder": "Reminder from Custodian", "task": "Custodian finished a scheduled task"}.get(kind, "Custodian")
    try:
        with httpx.Client(timeout=10, transport=_transport) as http:
            http.post(url, content=(f"{title}\n{body}" if body else title) if show else generic,
                      headers={"Title": "Custodian", "Tags": "bell" if kind == "reminder" else "robot"})
    except httpx.HTTPError as exc:
        log.warning("couldn't push a notification to ntfy: %s", exc)


def deliver_due(now: datetime | None = None) -> int:
    """Turn every reminder that's due into a notification. Returns how many were delivered."""
    folder = _reminders_dir()
    if not (folder / "reminders.db").exists():
        return 0
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo(get_settings().timezone)
    conn = reminders_tool.connect(folder)
    try:
        due = conn.execute(
            "SELECT * FROM reminders WHERE status = 'pending' AND due_at <= ? ORDER BY due_at", (reminders_tool.to_db(now),)
        ).fetchall()
        for row in due:
            when = reminders_tool.from_db(row["due_at"]).astimezone(tz)
            add(row["user_id"], "reminder", row["text"], f"Reminder set for {when:%a %d %b, %H:%M}", f"reminder:{row['id']}")
            with conn:
                if row["repeat"]:
                    following = reminders_tool.next_due(when, row["repeat"], tz)
                    while following <= now:  # the server was off for a while: skip the missed ones
                        following = reminders_tool.next_due(following, row["repeat"], tz)
                    conn.execute("UPDATE reminders SET due_at = ?, delivered_at = datetime('now') WHERE id = ?",
                                 (reminders_tool.to_db(following), row["id"]))
                else:
                    conn.execute("UPDATE reminders SET status = 'done', delivered_at = datetime('now') WHERE id = ?",
                                 (row["id"],))
    finally:
        conn.close()
    if due:
        log.info("delivered %d reminder(s)", len(due))
    return len(due)


def upcoming_reminders(user_id: int, limit: int = 50) -> list[dict]:
    """Pending reminders, soonest first - the phone app schedules these locally."""
    folder = _reminders_dir()
    if not (folder / "reminders.db").exists():
        return []
    tz = ZoneInfo(get_settings().timezone)
    conn = reminders_tool.connect(folder)
    try:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE user_id = ? AND status = 'pending' ORDER BY due_at LIMIT ?", (user_id, limit)
        ).fetchall()
    finally:
        conn.close()
    return [reminders_tool.describe(r, tz) for r in rows]


def cancel_reminder(user_id: int, reminder_id: int) -> bool:
    folder = _reminders_dir()
    if not (folder / "reminders.db").exists():
        return False
    conn = reminders_tool.connect(folder)
    try:
        with conn:
            return conn.execute(
                "UPDATE reminders SET status = 'cancelled' WHERE id = ? AND user_id = ? AND status = 'pending'",
                (reminder_id, user_id),
            ).rowcount > 0
    finally:
        conn.close()


def list_for(user_id: int, *, unread_only: bool = False, limit: int = 50) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM notifications WHERE user_id = ? {'AND read_at IS NULL' if unread_only else ''} "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "title": decrypt(r["title"]),
            "body": decrypt(r["body"]) if r["body"] else None,
            "ref": r["ref"],
            "created_at": db.iso(r["created_at"]),
            "read_at": db.iso(r["read_at"]),
        }
        for r in rows
    ]


def total_count(user_id: int) -> int:
    with db.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ?", (user_id,)).fetchone()[0]


def unread_count(user_id: int) -> int:
    with db.connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL", (user_id,)).fetchone()[0]


def mark_read(user_id: int, note_id: int | None = None) -> int:
    """Mark one notification read, or all of them with note_id=None."""
    with db.connect() as conn:
        if note_id is None:
            return conn.execute(
                "UPDATE notifications SET read_at = datetime('now') WHERE user_id = ? AND read_at IS NULL", (user_id,)
            ).rowcount
        return conn.execute(
            "UPDATE notifications SET read_at = datetime('now') WHERE id = ? AND user_id = ? AND read_at IS NULL",
            (note_id, user_id),
        ).rowcount
