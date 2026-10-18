"""Custodian speaking first: reminders and alerts read out on the PC, a morning briefing when
you sit down, and a heads-up before a meeting with the link ready to open.

- announce(): a notification of a kind worth hearing (a reminder coming due, a meeting about to
  start, something a watcher spotted) is also handed to the desktop app, which shows it and
  says it - unless announcements are off, or it's within your quiet hours (settings_store). It
  isn't queued for later: one that can't be delivered within a few minutes is dropped.
- briefing(): the desktop app asks for it the first time you use the PC in the morning (after
  hours away). It runs the morning-briefing skill like a scheduled task - today's calendar,
  reminders and notes, and the weather where you live if web search is on - once a day.
- meeting_heads_up(): every minute, events on your calendar (and Google Calendar, when
  connected and switched on) starting within `meeting_heads_up` minutes get one notification
  each - "“Design review” starts in 10 minutes" - with the meeting link when there is one.
  Your calendar switches apply: nothing is read from a calendar whose switch is off.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db, desktop, google_account, notifications, permissions, settings_store
from .config import get_settings
from .crypto import decrypt
from .tools import calendar_tool

log = logging.getLogger(__name__)

SPOKEN_KINDS = {"reminder", "meeting", "watch"}
MEETING_LINK = re.compile(r"https?://(?:[\w-]+\.)*(?:meet\.google\.com|zoom\.us|teams\.microsoft\.com|teams\.live\.com|webex\.com"
                          r"|whereby\.com|meet\.jit\.si)/[^\s<>\"')\]]+", re.I)
GOOGLE_CACHE_SECONDS = 600

_google_cache: dict[int, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def _now_local() -> datetime:
    return datetime.now(ZoneInfo(get_settings().timezone))


def spoken(kind: str, title: str, body: str | None, link: str | None) -> str:
    if kind == "reminder":
        return f"Reminder: {title.rstrip('.')}."
    if kind == "meeting":
        return f"{title.rstrip('.')}." + (" Shall I open the meeting link?" if link else "")
    return f"{title.rstrip('.')}." + (f" {body}" if body else "")


def announce(user_id: int, kind: str, title: str, body: str | None = None, *, link: str | None = None) -> bool:
    """Say it on the PC now, if the user wants that now. Returns whether it was handed over."""
    prefs = settings_store.get(user_id)
    if not prefs["announce"] or settings_store.in_quiet_hours(prefs, _now_local().strftime("%H:%M")):
        return False
    args = {"kind": kind, "title": title, "body": body, "say": spoken(kind, title, body, link)}
    if link:
        args["link"] = link
    return desktop.post(user_id, "announce", args, ttl=300 if kind == "meeting" else 600)


# The morning briefing ---------------------------------------------------------------------------

def _first_name(user_id: int) -> str | None:
    with db.connect() as conn:
        for row in conn.execute("SELECT content FROM memories WHERE user_id = ? ORDER BY id DESC", (user_id,)):
            text = decrypt(row["content"])
            match = re.match(r"(?:their|my) (?:full )?name is ([^\W\d_][\w'-]*)", text, re.I)
            if match:
                return match.group(1).capitalize()
    return None


def greeting(user_id: int) -> str:
    hour = _now_local().hour
    part = "Good morning" if 4 <= hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    name = _first_name(user_id)
    return f"{part}{', ' + name if name else ''}."


def briefing(user_id: int, *, force: bool = False) -> dict:
    """Today's briefing as one short spoken text, at most once a day unless `force`."""
    from . import orchestrator, skills  # here: the orchestrator imports this module's neighbours

    prefs = settings_store.get(user_id)
    today = _now_local().date().isoformat()
    if not force and not prefs["briefing"]:
        return {"skipped": "the morning briefing is switched off"}
    if not force and settings_store.internal(user_id, "last_briefing") == today:
        return {"skipped": "already briefed today"}
    skill = skills.load_skill("morning-briefing") or skills.load_skill("daily-summary")
    result = orchestrator.handle(user_id, None, trigger="morning-briefing",
                                 task=orchestrator.TaskContext(task_id=0, name="morning-briefing", skill_id=skill.id))
    settings_store.set_internal(user_id, "last_briefing", today)
    text = f"{greeting(user_id)} {result['reply'].strip()}"
    notifications.add(user_id, "task", "Your morning briefing", text[:280] + ("…" if len(text) > 280 else ""),
                      f"conversation:{result['conversation_id']}", push=False)
    return {"text": text, "conversation_id": result["conversation_id"]}


# Meeting heads-ups ------------------------------------------------------------------------------

def _granted(user_id: int, tool: str) -> bool:
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM tools WHERE name = ?", (tool,)).fetchone()
        return row is not None and permissions.check(user_id, row["id"], "read", conn=conn)


def _local_events(user_id: int, start: datetime, end: datetime) -> list[dict]:
    folder = get_settings().tool_data_dir / "calendar"
    if not (folder / "calendar.db").exists() or not _granted(user_id, "calendar"):
        return []
    conn = calendar_tool._connect({"data_dir": str(folder)})  # noqa: SLF001 - the server reads the tool's own file
    try:
        rows = conn.execute(
            "SELECT * FROM events WHERE user_id = ? AND starts_at > ? AND starts_at <= ? ORDER BY starts_at",
            (user_id, calendar_tool._utc(start), calendar_tool._utc(end)),  # noqa: SLF001
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        begins = datetime.strptime(row["starts_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        link = MEETING_LINK.search(row["location"] or "")
        out.append({"source": "calendar", "id": row["id"], "title": row["title"], "start": begins,
                    "location": row["location"], "link": link.group(0) if link else None})
    return out


def _google_events(user_id: int, start: datetime, end: datetime) -> list[dict]:
    if not google_account.status(user_id)["connected"] or not _granted(user_id, "gcal"):
        return []
    with _lock:
        cached = _google_cache.get(user_id)
    if cached is None or time.monotonic() - cached[0] > GOOGLE_CACHE_SECONDS:
        from .tools import sandbox
        from .tools.base import ToolRequest

        settings = get_settings()
        try:
            context = {"user_id": user_id, "data_dir": str(settings.tool_data_dir / "gcal"), "timezone": settings.timezone,
                       "access_token": google_account.access_token(user_id), "allow_hosts": ["www.googleapis.com"]}
            result = sandbox.SubprocessSandbox().run(
                ToolRequest(module="app.tools.google_tools", function="list_events", args={"days": 2}, context=context),
                sandbox.NETWORK_TIMEOUT_SECONDS,
            )
            events = result.get("events", [])
        except Exception as exc:  # noqa: BLE001 - Google unreachable: try again next time
            log.info("couldn't read Google Calendar for heads-ups: %s", exc)
            events = cached[1] if cached else []
        with _lock:
            _google_cache[user_id] = (time.monotonic(), events)
    else:
        events = cached[1]
    out = []
    for event in events:
        try:
            begins = datetime.fromisoformat(str(event.get("start")))
        except ValueError:
            continue
        if begins.tzinfo is None or not start < begins <= end:  # all-day events have no time: no heads-up
            continue
        link = event.get("link") or (MEETING_LINK.search(event.get("location") or "") or [None])[0]
        out.append({"source": "gcal", "id": event.get("id"), "title": event.get("title") or "A meeting", "start": begins,
                    "location": event.get("location"), "link": link})
    return out


def _told(user_id: int, ref: str) -> bool:
    with db.connect() as conn:
        return conn.execute("SELECT 1 FROM notifications WHERE user_id = ? AND ref = ?", (user_id, ref)).fetchone() is not None


def meeting_heads_up(now: datetime | None = None) -> int:
    """One notification per event starting soon (run every minute). Returns how many were sent."""
    now = now or datetime.now(timezone.utc)
    sent = 0
    with db.connect() as conn:
        users = [r["id"] for r in conn.execute("SELECT id FROM users")]
    for user_id in users:
        lead = settings_store.get(user_id)["meeting_heads_up"]
        if not lead:
            continue
        end = now + timedelta(minutes=lead)
        for event in _local_events(user_id, now, end) + _google_events(user_id, now, end):
            ref = f"meeting:{event['source']}:{event['id']}:{event['start']:%Y%m%d%H%M}"
            if _told(user_id, ref):
                continue
            minutes = max(1, round((event["start"] - now).total_seconds() / 60))
            title = f"“{event['title']}” starts in {minutes} minute{'s' if minutes != 1 else ''}"
            body = event["link"] or event["location"]
            notifications.add(user_id, "meeting", title, body, ref, link=event["link"])
            sent += 1
    return sent
