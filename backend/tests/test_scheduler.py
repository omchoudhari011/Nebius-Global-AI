from app import db, scheduler, tasks


def _task(user_id, **kwargs):
    return tasks.create_task(user_id, kwargs.pop("name", "daily-summary"), "daily-summary", "0 9 * * *", **kwargs)


def test_run_task_records_a_run_and_a_task_conversation(user_id, grant):
    grant("calendar", "read")
    task = _task(user_id)
    run_id = scheduler.run_task(task["id"])
    run = tasks.get_run(user_id, run_id)
    assert run["status"] == "succeeded" and run["finished_at"]
    with db.connect() as conn:
        conv = conn.execute("SELECT channel FROM conversations WHERE id = ?", (run["conversation_id"],)).fetchone()
        statuses = [r["status"] for r in conn.execute("SELECT status FROM tool_calls ORDER BY id").fetchall()]
    assert conv["channel"] == "task"
    assert statuses == ["succeeded", "denied"]  # calendar allowed, notes still off


def test_only_one_run_at_a_time(user_id):
    task = _task(user_id)
    with db.connect() as conn:
        conn.execute("INSERT INTO task_runs (task_id) VALUES (?)", (task["id"],))
    assert tasks.is_running(task["id"])
    assert scheduler.run_task(task["id"]) is None


def test_interrupted_runs_are_marked_failed(user_id):
    task = _task(user_id)
    with db.connect() as conn:
        conn.execute("INSERT INTO task_runs (task_id) VALUES (?)", (task["id"],))
    assert scheduler.recover_stale_runs() == 1
    assert tasks.list_runs(user_id, task["id"])[0]["status"] == "failed"


def test_failed_run_keeps_the_error(user_id):
    task = _task(user_id)
    with db.connect() as conn:
        conn.execute("UPDATE skills SET active = 0 WHERE id = ?", (task["skill"]["id"],))
    run = tasks.get_run(user_id, scheduler.run_task(task["id"]))
    assert run["status"] == "failed" and "rolled back" in run["error"]


def test_start_registers_active_tasks(user_id):
    active = _task(user_id)
    paused = _task(user_id, name="paused-one", status="paused")
    scheduler.start()
    try:
        assert scheduler.is_running()
        jobs = {job.id for job in scheduler._scheduler.get_jobs()}
        assert f"task-{active['id']}" in jobs and f"task-{paused['id']}" not in jobs
        assert scheduler.PURGE_JOB_ID in jobs
        assert tasks.get_task(user_id, active["id"])["next_run_at"]

        tasks.update_task(user_id, active["id"], status="paused")
        scheduler.sync_task(active["id"])
        assert f"task-{active['id']}" not in {job.id for job in scheduler._scheduler.get_jobs()}
    finally:
        scheduler.shutdown()


def test_next_fire_respects_the_time_zone():
    from datetime import datetime, timezone

    after = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    upcoming = tasks.next_fire("0 9 * * *", "Asia/Kolkata", after)
    assert upcoming.astimezone(timezone.utc) == datetime(2026, 10, 3, 3, 30, tzinfo=timezone.utc)
