"""Tool: reminders kept in DATA_DIR/tools/reminders/reminders.db. Same gated pattern as
calendar_tool.py; delivering a reminder when it's due is the server's job
(app/notifications.py reads this file every minute), so the tool only stores and lists them.

Times are stored in UTC and returned in the user's time zone.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DB_TIME = "%Y-%m-%d %H:%M:%S"
REPEATS = ("daily", "weekdays", "weekly")


def connect(data_dir: str | Path) -> sqlite3.Connection:
    """The reminders database; also used by app/notifications.py (outside the sandbox)."""
    conn = sqlite3.connect(Path(data_dir) / "reminders.db", timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reminders (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          text TEXT NOT NULL,
          due_at TEXT NOT NULL,
          repeat TEXT CHECK (repeat IS NULL OR repeat IN ('daily', 'weekdays', 'weekly')),
          status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'cancelled')),
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          delivered_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS reminders_due ON reminders (status, due_at)")
    return conn


def to_db(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(DB_TIME)


def from_db(value: str) -> datetime:
    return datetime.strptime(value, DB_TIME).replace(tzinfo=timezone.utc)


def next_due(due: datetime, repeat: str, tz: ZoneInfo) -> datetime:
    """The next time a repeating reminder fires after `due`, keeping its local wall-clock time."""
    local = due.astimezone(tz) + timedelta(days=7 if repeat == "weekly" else 1)  # wall-clock arithmetic
    while repeat == "weekdays" and local.weekday() >= 5:
        local += timedelta(days=1)
    return local


def describe(row: sqlite3.Row, tz: ZoneInfo) -> dict:
    due = from_db(row["due_at"]).astimezone(tz)
    return {
        "id": row["id"],
        "text": row["text"],
        "due": due.isoformat(timespec="minutes"),
        "when": due.strftime("%a %d %b, %H:%M").replace(" 0", " "),  # "Sun 25 Oct, 07:00": say this, don't work it out
        "repeat": row["repeat"],
        "status": row["status"],
    }


# What a small model copies along with the task: "remind me to ...", "... की याद दिलाना".
_ASKING = re.compile(r"^\s*(?:please\s+)?(?:remind\s+me\s+(?:to\s+)?|remember\s+to\s+|reminder\s*(?:to|:)\s*)", re.I)
_ASKING_HI = re.compile(r"\s*(?:की|का|के)?\s*याद\s*(?:दिलाना|दिला|दिलाओ|दिलाइए|दिलाना है|दिला देना)\s*[।.!]*\s*$")


def task_text(text: str) -> str:
    """'remind me to call mum' -> 'call mum'; 'दवाई लेने की याद दिलाना' -> 'दवाई लेना'."""
    cleaned = _ASKING.sub("", text).strip()
    if _ASKING_HI.search(cleaned):
        cleaned = _ASKING_HI.sub("", cleaned).strip()
        cleaned = re.sub(r"ने$", "ना", cleaned)  # "लेने की याद" -> "लेना": the task, as a task
    return cleaned or text.strip()


def create_reminder(ctx: dict, text: str, at: str, repeat: str | None = None) -> dict:
    tz = ZoneInfo(ctx["timezone"])
    text = task_text(text)
    due = datetime.fromisoformat(at)
    if due.tzinfo is None:
        due = due.replace(tzinfo=tz)
    if due <= datetime.now(timezone.utc) - timedelta(minutes=1):
        raise ValueError(f"{due.astimezone(tz).isoformat(timespec='minutes')} is in the past; pick a later time")
    conn = connect(ctx["data_dir"])
    try:
        with conn:
            reminder_id = conn.execute(
                "INSERT INTO reminders (user_id, text, due_at, repeat) VALUES (?, ?, ?, ?)",
                (ctx["user_id"], text, to_db(due), repeat),
            ).lastrowid
        row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    finally:
        conn.close()
    return {"created": describe(row, tz)}


def list_reminders(ctx: dict, include_done: bool = False, limit: int = 20) -> dict:
    tz = ZoneInfo(ctx["timezone"])
    status = "" if include_done else "AND status = 'pending'"
    conn = connect(ctx["data_dir"])
    try:
        rows = conn.execute(
            f"SELECT * FROM reminders WHERE user_id = ? AND status != 'cancelled' {status} ORDER BY due_at LIMIT ?",
            (ctx["user_id"], limit),
        ).fetchall()
    finally:
        conn.close()
    return {"reminders": [describe(r, tz) for r in rows]}


def cancel_reminder(ctx: dict, id: int) -> dict:  # noqa: A002 - the model's argument name
    conn = connect(ctx["data_dir"])
    try:
        with conn:
            changed = conn.execute(
                "UPDATE reminders SET status = 'cancelled' WHERE id = ? AND user_id = ? AND status = 'pending'",
                (id, ctx["user_id"]),
            ).rowcount
    finally:
        conn.close()
    if not changed:
        raise ValueError(f"there is no pending reminder #{id}")
    return {"cancelled": id}
