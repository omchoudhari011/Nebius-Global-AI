"""Tool: a local calendar kept in DATA_DIR/tools/calendar/calendar.db (no OAuth, no network).

Runs inside the sandbox process. `ctx` comes from the gate (user_id, data_dir, timezone);
the other arguments were validated against app/tools/schemas.py before the process started.
Times are stored in UTC and returned in the user's time zone.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_DB_TIME = "%Y-%m-%d %H:%M:%S"


def _connect(ctx: dict) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(ctx["data_dir"]) / "calendar.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          title TEXT NOT NULL,
          starts_at TEXT NOT NULL,
          ends_at TEXT NOT NULL,
          location TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS events_by_user_start ON events (user_id, starts_at)")
    return conn


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(_DB_TIME)


def _local(value: str, tz: ZoneInfo) -> str:
    moment = datetime.strptime(value, _DB_TIME).replace(tzinfo=timezone.utc).astimezone(tz)
    return moment.isoformat(timespec="minutes")


def _event(row: sqlite3.Row, tz: ZoneInfo) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "start": _local(row["starts_at"], tz),
        "end": _local(row["ends_at"], tz),
        "location": row["location"],
    }


def list_events(ctx: dict, start_date: str | None = None, days: int = 1) -> dict:
    tz = ZoneInfo(ctx["timezone"])
    first = date.fromisoformat(start_date) if start_date else datetime.now(tz).date()
    last = first + timedelta(days=days - 1)
    window_start = datetime.combine(first, time.min, tz)
    window_end = datetime.combine(last + timedelta(days=1), time.min, tz)
    conn = _connect(ctx)
    try:
        rows = conn.execute(
            """
            SELECT * FROM events
            WHERE user_id = ? AND starts_at < ? AND ends_at > ?
            ORDER BY starts_at, id
            """,
            (ctx["user_id"], _utc(window_end), _utc(window_start)),
        ).fetchall()
    finally:
        conn.close()
    return {
        "timezone": ctx["timezone"],
        "from": first.isoformat(),
        "to": last.isoformat(),
        "events": [_event(r, tz) for r in rows],
    }


def create_event(
    ctx: dict, title: str, start: str, duration_minutes: int = 60, location: str | None = None
) -> dict:
    tz = ZoneInfo(ctx["timezone"])
    begins = datetime.fromisoformat(start)
    if begins.tzinfo is None:
        begins = begins.replace(tzinfo=tz)
    ends = begins + timedelta(minutes=duration_minutes)
    conn = _connect(ctx)
    try:
        with conn:
            event_id = conn.execute(
                "INSERT INTO events (user_id, title, starts_at, ends_at, location) VALUES (?, ?, ?, ?, ?)",
                (ctx["user_id"], title, _utc(begins), _utc(ends), location),
            ).lastrowid
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    finally:
        conn.close()
    return {"created": _event(row, tz)}
