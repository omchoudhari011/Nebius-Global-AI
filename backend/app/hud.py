"""What the Jarvis HUD on your PC shows from Custodian (GET /hud): today's events and the next
reminders, the last few things Custodian did, unread notifications, how to address you, and the
city for the weather.

Calendars are read only with their switches on (Calendar · read, Google Calendar · read), as for
the meeting heads-ups. The activity list names each action and its target ("open · chrome"),
never a message's text. The city is the "home_city" setting, or else the one your memories say
you live in ("Lives in Pune.").
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import activity, db, desktop, memory_graph, notifications, proactive, settings_store
from .config import get_settings
from .crypto import decrypt

# The argument that says what an action was about, by function (never a message's text).
TARGETS = ("target", "query", "name", "title", "contact", "site", "page", "mode", "level", "keys", "path", "seconds", "time", "on")


def _city(user_id: int, prefs: dict) -> str | None:
    if prefs.get("home_city"):
        return prefs["home_city"]
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT content FROM memories WHERE user_id = ? AND superseded_by IS NULL ORDER BY id DESC LIMIT 400", (user_id,)
        ).fetchall()
    for row in rows:
        text = decrypt(row["content"])
        said = memory_graph.attribute(text)
        if said and said[0] == "home" and said[1] == "self":
            at = text.lower().find(said[2])
            return text[at: at + len(said[2])] if at >= 0 else said[2].title()
    return None


def _action(item: dict) -> dict:
    args = item["args"] if isinstance(item["args"], dict) else {}
    target = next((args[k] for k in TARGETS if k in args and args[k] not in (None, "")), None)
    if item["function"] == "send_message":
        target = args.get("contact")  # who, never what
    label = item["function"].replace("_", " ")
    if target is not None:
        label += f" · {str(target)[:60]}"
    return {"when": item["when"], "label": label, "status": item["status"], "can_undo": item["can_undo"]}


def snapshot(user_id: int) -> dict:
    settings = get_settings()
    prefs = settings_store.get(user_id)
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    events = []
    for event in proactive._local_events(user_id, start.astimezone(timezone.utc), end.astimezone(timezone.utc)) + \
            proactive._google_events(user_id, start.astimezone(timezone.utc), end.astimezone(timezone.utc)):  # noqa: SLF001
        begins = event["start"].astimezone(tz)
        events.append({"title": event["title"], "time": begins.strftime("%H:%M"), "past": begins < now,
                       "location": event.get("location"), "link": event.get("link"), "_at": begins})
    events.sort(key=lambda e: e.pop("_at"))
    reminders = [{"text": r.get("text"), "due": r.get("due")} for r in notifications.upcoming_reminders(user_id, limit=5)]
    return {
        "name": proactive._first_name(user_id),  # noqa: SLF001
        "persona": prefs["persona"],
        "address": prefs["address"] or "sir",
        "city": _city(user_id, prefs),
        "quiet": settings_store.in_quiet_hours(prefs, now.strftime("%H:%M")),
        "announce": prefs["announce"],
        "events": events,
        "reminders": reminders,
        "activity": [_action(a) for a in activity.recent(user_id, 6)],
        "unread": notifications.unread_count(user_id),
        "model": settings.nebius_model_id if settings.model_backend != "stub" else "offline",
        "now": now.isoformat(timespec="seconds"),
        "pc": desktop.vitals(user_id),  # the PC's vitals and weather, when it reported in the last 2 minutes
    }
