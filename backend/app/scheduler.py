"""Runs scheduled tasks with APScheduler, re-entering the same orchestrator.handle() that
chat uses, with no user message - one code path serves both triggers.

On startup: leftover 'running' task_runs (from a process that crashed) are marked
'failed', every active task is registered with its CronTrigger in its own time zone, and
an hourly job deletes expired memories; a
per-minute job delivers reminders that have come due (app/notifications.py). Each fire inserts a task_runs row; the partial
unique index task_runs_one_running guarantees at most one run in progress per task, even
if a run is triggered by hand while the cron job fires.

Every run calls the model, so a user gets at most MAX_RUNS_PER_DAY runs (scheduled and
"Run now" together) in any 24 hours. Past that - or past what the plan covers: its number of
scheduled tasks, or its monthly model allowance (app/plans.py) - a run is recorded as failed
with a "skipped:" reason instead of calling the model, so the limit is visible in the
Scheduled tab. The hourly job also deletes chats past their retention (app/privacy.py).

Run the API with a single worker process (the default here); several workers would each
start a scheduler.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from . import auth, db, memory, memory_graph, notifications, orchestrator, plans, privacy, proactive, tasks, usage, watchers

log = logging.getLogger(__name__)

PURGE_JOB_ID = "purge-expired-memories"
REMINDERS_JOB_ID = "deliver-reminders"
MEETINGS_JOB_ID = "meeting-heads-up"
WATCHERS_JOB_ID = "check-watches"
GRAPH_JOB_ID = "memory-graph"
LEARNING_JOB_ID = "overnight-learning"
MAX_RUNS_PER_DAY = 48
SKIPPED = "skipped:"

_lock = threading.Lock()
_scheduler: BackgroundScheduler | None = None


def _job_id(task_id: int) -> str:
    return f"task-{task_id}"


def is_running() -> bool:
    return _scheduler is not None and _scheduler.running


def recover_stale_runs() -> int:
    with db.connect() as conn:
        cur = conn.execute(
            """
            UPDATE task_runs SET status = 'failed', error = 'interrupted: the server restarted',
                                 finished_at = datetime('now')
            WHERE status = 'running'
            """
        )
    if cur.rowcount:
        log.warning("marked %d interrupted task run(s) as failed", cur.rowcount)
    return cur.rowcount


def _purge() -> None:
    """Hourly: expired memories, off-the-record chats a day old, chats past their retention, and
    sign-ins whose token has expired."""
    removed = memory.purge_expired()
    if removed:
        log.info("deleted %d expired memories", removed)
    privacy.purge_old_chats()
    auth.purge_expired_sessions()


def _safe(job):  # noqa: ANN001, ANN202
    """A background job that logs its failures instead of raising them into APScheduler."""
    def run() -> None:
        try:
            job()
        except Exception:  # noqa: BLE001
            log.exception("%s failed", getattr(job, "__name__", "a background job"))
    run.__name__ = getattr(job, "__name__", "job")
    return run


def start() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            return
        recover_stale_runs()
        sched = BackgroundScheduler(
            timezone=timezone.utc,
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600},
        )
        sched.start()
        _scheduler = sched
        sched.add_job(
            _purge,
            IntervalTrigger(hours=1, timezone=timezone.utc),
            id=PURGE_JOB_ID,
            next_run_time=datetime.now(timezone.utc),
            replace_existing=True,
        )
        sched.add_job(
            notifications.deliver_due,
            IntervalTrigger(minutes=1, timezone=timezone.utc),
            id=REMINDERS_JOB_ID,
            next_run_time=datetime.now(timezone.utc),
            replace_existing=True,
        )
        # Proactive (app/proactive.py, app/watchers.py): meeting heads-ups every minute, watches
        # every five (each at its own pace).
        for job, minutes, job_id in ((_safe(proactive.meeting_heads_up), 1, MEETINGS_JOB_ID),
                                     (_safe(watchers.check_due), 5, WATCHERS_JOB_ID),
                                     (_safe(memory_graph.process_pending), 1, GRAPH_JOB_ID)):  # memories saved elsewhere
            sched.add_job(job, IntervalTrigger(minutes=minutes, timezone=timezone.utc), id=job_id,
                          next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30), replace_existing=True)
    with db.connect() as conn:
        task_ids = [r["id"] for r in conn.execute("SELECT id FROM tasks WHERE status = 'active'").fetchall()]
    for task_id in task_ids:
        sync_task(task_id)
    log.info("scheduler started with %d active task(s)", len(task_ids))


def shutdown() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
            _scheduler = None


def sync_task(task_id: int) -> None:
    """Make the scheduler match the tasks row: (re)register an active task, drop a paused or
    deleted one, and write next_run_at."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, cron_schedule, timezone, status FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
    if _scheduler is not None:
        try:
            _scheduler.remove_job(_job_id(task_id))
        except JobLookupError:
            pass
    if row is None:
        return
    if row["status"] != "active":
        tasks.set_next_run(task_id, None)
        return
    trigger = tasks.trigger_for(row["cron_schedule"], row["timezone"])
    if _scheduler is not None:
        job = _scheduler.add_job(run_task, trigger, args=[task_id], id=_job_id(task_id), replace_existing=True)
        upcoming = job.next_run_time
    else:
        upcoming = trigger.get_next_fire_time(None, datetime.now(timezone.utc))
    tasks.set_next_run(task_id, upcoming)


def run_task(task_id: int) -> int | None:
    """Execute one run now. Returns the task_runs id, or None if the task is gone or
    already has a run in progress."""
    with db.connect() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        return None
    skipped = _skip_if_over_plan(task) or _skip_if_over_limit(task)
    if skipped is not None:
        _refresh_next_run(task)
        return skipped
    try:
        with db.connect() as conn:
            run_id = conn.execute("INSERT INTO task_runs (task_id) VALUES (?)", (task_id,)).lastrowid
            conversation_id = conn.execute(
                "INSERT INTO conversations (user_id, channel) VALUES (?, 'task')", (task["user_id"],)
            ).lastrowid
            conn.execute("UPDATE task_runs SET conversation_id = ? WHERE id = ?", (conversation_id, run_id))
    except sqlite3.IntegrityError:
        log.info("task %s (%s) already has a run in progress; skipping", task_id, task["name"])
        return None

    log.info("running task %s (%s), run %s", task_id, task["name"], run_id)
    status, error = "succeeded", None
    try:
        result = orchestrator.handle(
            task["user_id"],
            None,
            trigger=task["name"],
            conversation_id=conversation_id,
            task=orchestrator.TaskContext(
                task_id=task_id,
                name=task["name"],
                skill_id=task["skill_id"],
                params=json.loads(task["params"]) if task["params"] else {},
                run_id=run_id,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - recorded on the run
        log.exception("task %s (%s) failed", task_id, task["name"])
        status, error = "failed", f"{type(exc).__name__}: {exc}"[:1000]

    with db.connect() as conn:
        conn.execute(
            "UPDATE task_runs SET status = ?, error = ?, finished_at = datetime('now') WHERE id = ?",
            (status, error, run_id),
        )
    if status == "succeeded":
        reply = " ".join(result["reply"].split())
        notifications.add(task["user_id"], "task", f"{task['name'].replace('-', ' ').capitalize()} is ready",
                          reply[:280] + ("…" if len(reply) > 280 else ""), f"conversation:{conversation_id}")
    _refresh_next_run(task)
    return run_id


def _record_skip(task: sqlite3.Row, reason: str) -> int:
    with db.connect() as conn:
        run_id = conn.execute(
            "INSERT INTO task_runs (task_id, status, error, finished_at) VALUES (?, 'failed', ?, datetime('now'))",
            (task["id"], f"{SKIPPED} {reason}"),
        ).lastrowid
    log.warning("task %s (%s) skipped: %s", task["id"], task["name"], reason)
    return run_id


def _skip_if_over_plan(task: sqlite3.Row) -> int | None:
    """Record a skipped run when the plan doesn't cover this one: past the plan's active-task
    limit (after a downgrade the oldest tasks keep running), or with the monthly model
    allowance used up. Returns its id."""
    with db.connect() as conn:
        runnable = plans.runnable_task_ids(conn, task["user_id"])
    if task["status"] == "active" and runnable is not None and task["id"] not in runnable:  # "Run now" on a paused task is fine
        plan = plans.current().plan
        noun = "task" if plan.max_active_tasks == 1 else "tasks"
        return _record_skip(
            task,
            f"the {plan.name} plan runs {plan.max_active_tasks} scheduled {noun} at a time and this one is past that. "
            + plans.upgrade_hint(plan),
        )
    try:
        usage.check_allowance(task["user_id"])
    except plans.PlanLimit as exc:
        return _record_skip(task, str(exc))
    return None


def _skip_if_over_limit(task: sqlite3.Row) -> int | None:
    """Record a skipped run instead of calling the model when the user already had
    MAX_RUNS_PER_DAY runs in the last 24 hours (skipped ones don't count). Returns its id."""
    with db.connect() as conn:
        recent = conn.execute(
            """
            SELECT COUNT(*) FROM task_runs r JOIN tasks t ON t.id = r.task_id
            WHERE t.user_id = ? AND r.started_at > datetime('now', '-1 day')
              AND (r.error IS NULL OR r.error NOT LIKE ?)
            """,
            (task["user_id"], SKIPPED + "%"),
        ).fetchone()[0]
    if recent < MAX_RUNS_PER_DAY:
        return None
    return _record_skip(
        task, f"{recent} runs in the last 24 hours already (limit {MAX_RUNS_PER_DAY}), so this one didn't call the model"
    )


def _refresh_next_run(task: sqlite3.Row) -> None:
    try:
        job = _scheduler.get_job(_job_id(task["id"])) if _scheduler is not None else None
        if job is not None:
            tasks.set_next_run(task["id"], job.next_run_time)
        elif task["status"] == "active":
            tasks.set_next_run(task["id"], tasks.next_fire(task["cron_schedule"], task["timezone"]))
    except Exception:  # noqa: BLE001 - next_run_at is informational
        log.debug("couldn't refresh next_run_at for task %s", task["id"], exc_info=True)
