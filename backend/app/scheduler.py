"""APScheduler job(s) that re-enter the same orchestrator.handle() chat uses,
with no user message — proving one code path serves both triggers.
Step 5 — see docs/PLAN.md.
"""


def start() -> None:
    raise NotImplementedError(
        "Step 5: mark leftover task_runs 'running' rows as 'failed', then for each active row in tasks: "
        "AsyncIOScheduler().add_job(lambda: orchestrator.handle(task.user_id, None, trigger=task.name), "
        "CronTrigger.from_crontab(task.cron_schedule, timezone=task.timezone)), wrapping each fire in a task_runs row"
    )
