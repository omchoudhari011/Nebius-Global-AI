"""Demo data: a few memories, calendar events, notes and the daily-summary task.

Safe to run more than once: memories are de-duplicated, tool data is only added to an
empty calendar / notebook, and an existing task is left alone. Permissions stay off
unless `grant_reads` is set, so the default-deny behaviour is still visible.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import auth, db, memory, permissions, scheduler, skills, tasks
from .config import get_settings
from .tools import calendar_tool, notes_tool

MEMORIES = [
    ("preference", "Wants the daily summary kept short: three bullet points at most."),
    ("preference", "Prefers meetings before noon."),
    ("semantic", "Works as a product designer at a small studio in Bengaluru."),
    ("semantic", "Partner's name is Priya."),
    ("semantic", "Is allergic to peanuts."),
    ("episodic", "Ran a first 10 km race in September 2026."),
]

NOTES = [
    ("Groceries", "Milk, eggs, spinach, coffee beans, oat crackers."),
    ("Q4 planning", "Draft the roadmap by Friday. Ask Sam for the retention numbers before the review."),
]


def seed(*, grant_reads: bool = False) -> dict:
    settings = get_settings()
    db.init_db()
    user_id = auth.ensure_owner()
    skills.sync_from_files()

    for kind, text in MEMORIES:
        memory.write(user_id, text, kind)

    tz = ZoneInfo(settings.timezone)
    today = datetime.now(tz).replace(second=0, microsecond=0)

    def at(day_offset: int, hour: int, minute: int = 0) -> str:
        moment = (today + timedelta(days=day_offset)).replace(hour=hour, minute=minute)
        return moment.isoformat()

    calendar_ctx = {"user_id": user_id, "data_dir": str(settings.tool_data_dir / "calendar"), "timezone": settings.timezone}
    notes_ctx = {"user_id": user_id, "data_dir": str(settings.tool_data_dir / "notes"), "timezone": settings.timezone}
    for ctx in (calendar_ctx, notes_ctx):
        Path(ctx["data_dir"]).mkdir(parents=True, exist_ok=True)

    events_added = 0
    if not calendar_tool.list_events(calendar_ctx, days=31)["events"]:
        for title, start, minutes, location in (
            ("Team standup", at(0, 9, 30), 15, "Studio"),
            ("Lunch with Priya", at(0, 12, 30), 60, "Toit, Indiranagar"),
            ("Design review", at(0, 16), 45, None),
            ("Dentist", at(1, 10), 30, "Smile Clinic"),
        ):
            calendar_tool.create_event(calendar_ctx, title, start, minutes, location)
            events_added += 1

    notes_added = 0
    if not notes_tool.read_notes(notes_ctx, "", 1)["notes"]:
        for title, body in NOTES:
            notes_tool.create_note(notes_ctx, title, body)
            notes_added += 1

    task_created = False
    if not any(t["name"] == "daily-summary" for t in tasks.list_tasks(user_id)):
        task = tasks.create_task(user_id, "daily-summary", "daily-summary", "0 9 * * *", settings.timezone)
        scheduler.sync_task(task["id"])
        task_created = True

    if grant_reads:
        for row in permissions.list_for_user(user_id):
            if row["scope"] == "read":
                permissions.grant(user_id, row["tool_id"], "read")

    return {
        "user_id": user_id,
        "memories": len(memory.list_memories(user_id)),
        "events_added": events_added,
        "notes_added": notes_added,
        "task_created": task_created,
        "read_permissions_granted": grant_reads,
    }
