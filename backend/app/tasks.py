"""Scheduled jobs. The tasks table is the source of truth; app/scheduler.py registers every
active row with APScheduler and records each execution in task_runs.

A task pins one exact skill version (tasks.skill_id), so a new prompt version never
silently changes what a scheduled job does; update the task to move it to the newest one.

Every run calls the model, which costs money with a cloud backend, so a schedule may not
run more often than every MIN_INTERVAL (scheduler.MAX_RUNS_PER_DAY caps the total as well).
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger

from . import db, plans, skills
from .config import get_settings

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_PARAMS_BYTES = 2000
MIN_INTERVAL = timedelta(minutes=15)


class TaskError(ValueError):
    """Invalid task input; the message is safe to show to the user."""


class TaskNotFound(LookupError):
    pass


def trigger_for(cron: str, tz_name: str) -> CronTrigger:
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise TaskError(f"unknown time zone {tz_name!r}; use an IANA name such as Asia/Kolkata") from None
    try:
        return CronTrigger.from_crontab(cron.strip(), timezone=tz)
    except (ValueError, TypeError) as exc:
        raise TaskError(f"invalid cron expression {cron!r}: {exc}") from None


def next_fire(cron: str, tz_name: str, after: datetime | None = None) -> datetime | None:
    now = after or datetime.now(timezone.utc)
    return trigger_for(cron, tz_name).get_next_fire_time(None, now)


def check_frequency(cron: str, tz_name: str) -> None:
    """Refuse a schedule with two runs closer than MIN_INTERVAL anywhere in its next 96 runs,
    so "every minute" is caught and so is "9:00 and 9:05 every day"."""
    trigger = trigger_for(cron, tz_name)
    previous = trigger.get_next_fire_time(None, datetime.now(timezone.utc))
    for _ in range(96):
        if previous is None:
            return
        upcoming = trigger.get_next_fire_time(previous, previous + timedelta(microseconds=1))
        if upcoming is not None and upcoming - previous < MIN_INTERVAL:
            minutes = int(MIN_INTERVAL.total_seconds() // 60)
            raise TaskError(
                f"that schedule runs too often: leave at least {minutes} minutes between runs, "
                "because every run uses the model"
            )
        previous = upcoming


def _check_params(params: dict | None) -> str | None:
    if params is None or params == {}:
        return None
    if not isinstance(params, dict) or not all(isinstance(k, str) for k in params):
        raise TaskError("params must be a JSON object")
    if not all(isinstance(v, (str, int, float, bool)) or v is None for v in params.values()):
        raise TaskError("params values must be plain text, numbers or true/false")
    text = json.dumps(params, ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_PARAMS_BYTES:
        raise TaskError("params are too large")
    return text


_SELECT = """
    SELECT t.*, s.name AS skill_name, s.version AS skill_version, s.active AS skill_active,
           (SELECT MAX(version) FROM skills x WHERE x.name = s.name AND x.active = 1) AS latest_version,
           r.id AS run_id, r.status AS run_status, r.error AS run_error, r.conversation_id AS run_conversation_id,
           r.started_at AS run_started_at, r.finished_at AS run_finished_at
    FROM tasks t
    JOIN skills s ON s.id = t.skill_id
    LEFT JOIN task_runs r ON r.id = (SELECT id FROM task_runs WHERE task_id = t.id ORDER BY id DESC LIMIT 1)
"""


def _shape(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "skill": {
            "id": row["skill_id"],
            "name": row["skill_name"],
            "version": row["skill_version"],
            "active": bool(row["skill_active"]),
            "latest_version": row["latest_version"],
        },
        "params": json.loads(row["params"]) if row["params"] else {},
        "cron_schedule": row["cron_schedule"],
        "timezone": row["timezone"],
        "status": row["status"],
        "next_run_at": db.iso(row["next_run_at"]),
        "created_at": db.iso(row["created_at"]),
        "last_run": (
            {
                "id": row["run_id"],
                "status": row["run_status"],
                "error": row["run_error"],
                "conversation_id": row["run_conversation_id"],
                "started_at": db.iso(row["run_started_at"]),
                "finished_at": db.iso(row["run_finished_at"]),
            }
            if row["run_id"]
            else None
        ),
    }


def list_tasks(user_id: int) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(_SELECT + " WHERE t.user_id = ? ORDER BY t.name", (user_id,)).fetchall()
    return [_shape(r) for r in rows]


def get_task(user_id: int, task_id: int) -> dict:
    with db.connect() as conn:
        row = conn.execute(_SELECT + " WHERE t.user_id = ? AND t.id = ?", (user_id, task_id)).fetchone()
    if row is None:
        raise TaskNotFound(task_id)
    return _shape(row)


def _latest_skill_id(skill_name: str) -> int:
    skill = skills.load_skill(skill_name)
    if skill is None:
        raise TaskError(f"there is no active skill called {skill_name!r}")
    return skill.id


def create_task(
    user_id: int,
    name: str,
    skill_name: str,
    cron_schedule: str,
    timezone_name: str | None = None,
    params: dict | None = None,
    status: str = "active",
) -> dict:
    name = name.strip().lower()
    if not NAME_RE.match(name):
        raise TaskError("name must be lowercase letters, digits and dashes (up to 64), e.g. daily-summary")
    if status not in ("active", "paused"):
        raise TaskError("status must be active or paused")
    tz_name = (timezone_name or get_settings().timezone).strip()
    cron = " ".join(cron_schedule.split())
    upcoming = next_fire(cron, tz_name)
    check_frequency(cron, tz_name)
    params_text = _check_params(params)
    skill_id = _latest_skill_id(skill_name)
    try:
        with db.connect() as conn:
            if status == "active":
                plans.check_task_room(conn, user_id)  # PlanLimit past the plan's active-task limit
            task_id = conn.execute(
                """
                INSERT INTO tasks (user_id, name, skill_id, params, cron_schedule, timezone, status, next_run_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    name,
                    skill_id,
                    params_text,
                    cron,
                    tz_name,
                    status,
                    db.to_db_time(upcoming) if upcoming and status == "active" else None,
                ),
            ).lastrowid
    except sqlite3.IntegrityError:
        raise TaskError(f"you already have a task called {name!r}") from None
    return get_task(user_id, task_id)


_UNSET = object()


def update_task(
    user_id: int,
    task_id: int,
    *,
    status: str | None = None,
    cron_schedule: str | None = None,
    timezone_name: str | None = None,
    params: dict | None | object = _UNSET,
    skill_name: str | None = None,
) -> dict:
    current = get_task(user_id, task_id)
    changes: dict[str, object] = {}
    if status is not None:
        if status not in ("active", "paused"):
            raise TaskError("status must be active or paused")
        changes["status"] = status
    cron = " ".join(cron_schedule.split()) if cron_schedule is not None else current["cron_schedule"]
    tz_name = timezone_name.strip() if timezone_name is not None else current["timezone"]
    if cron_schedule is not None or timezone_name is not None:
        check_frequency(cron, tz_name)
        changes["cron_schedule"] = cron
        changes["timezone"] = tz_name
    if params is not _UNSET:
        changes["params"] = _check_params(params)  # type: ignore[arg-type]
    if skill_name is not None:  # re-pin to the newest active version of this skill
        changes["skill_id"] = _latest_skill_id(skill_name)
    if changes:
        assignments = ", ".join(f"{column} = ?" for column in changes)
        with db.connect() as conn:
            if changes.get("status") == "active" and current["status"] != "active":
                plans.check_task_room(conn, user_id, exclude_task_id=task_id)
            conn.execute(f"UPDATE tasks SET {assignments} WHERE id = ? AND user_id = ?", [*changes.values(), task_id, user_id])
    return get_task(user_id, task_id)


def delete_task(user_id: int, task_id: int) -> bool:
    with db.connect() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
    return cur.rowcount > 0


def set_next_run(task_id: int, when: datetime | None) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE tasks SET next_run_at = ? WHERE id = ?", (db.to_db_time(when) if when else None, task_id)
        )


def is_running(task_id: int) -> bool:
    with db.connect() as conn:
        row = conn.execute("SELECT 1 FROM task_runs WHERE task_id = ? AND status = 'running'", (task_id,)).fetchone()
    return row is not None


def get_run(user_id: int, run_id: int) -> dict | None:
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT r.* FROM task_runs r JOIN tasks t ON t.id = r.task_id
            WHERE r.id = ? AND t.user_id = ?
            """,
            (run_id, user_id),
        ).fetchone()
    return _run(row) if row else None


def list_runs(user_id: int, task_id: int, limit: int = 20) -> list[dict]:
    get_task(user_id, task_id)  # 404 for someone else's task
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT ?", (task_id, limit)
        ).fetchall()
    return [_run(r) for r in rows]


def _run(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "conversation_id": row["conversation_id"],
        "status": row["status"],
        "error": row["error"],
        "started_at": db.iso(row["started_at"]),
        "finished_at": db.iso(row["finished_at"]),
    }
