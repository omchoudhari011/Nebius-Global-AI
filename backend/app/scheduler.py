"""APScheduler job(s) that re-enter the same orchestrator.handle() chat uses,
with no user message — proving one code path serves both triggers.
Step 5 — see docs/PLAN.md.
"""


def start() -> None:
    raise NotImplementedError(
        "Step 5: AsyncIOScheduler().add_job(lambda: orchestrator.handle(user_id, None, trigger='daily-summary'), "
        "CronTrigger(hour=9))"
    )
