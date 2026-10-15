"""Skills and scheduled tasks."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from .. import scheduler, skills, tasks
from ..auth import require_session

router = APIRouter(tags=["tasks"])

ParamValue = str | int | float | bool | None


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    skill: str = Field(min_length=1, max_length=64, description="Skill name; its newest active version is pinned")
    cron_schedule: str = Field(min_length=1, max_length=120, examples=["0 9 * * *"])
    timezone: str | None = Field(default=None, max_length=64, description="IANA name; defaults to CUSTODIAN_TIMEZONE")
    params: dict[str, ParamValue] | None = None
    status: Literal["active", "paused"] = "active"


class TaskUpdate(BaseModel):
    status: Literal["active", "paused"] | None = None
    cron_schedule: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)
    params: dict[str, ParamValue] | None = None
    skill: str | None = Field(default=None, max_length=64, description="Re-pin to this skill's newest active version")


@router.get("/skills")
def list_skills(user_id: int = Depends(require_session)) -> list[dict]:
    return skills.list_skills()


@router.get("/tasks")
def list_tasks(user_id: int = Depends(require_session)) -> list[dict]:
    return tasks.list_tasks(user_id)


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
def create_task(body: TaskCreate, user_id: int = Depends(require_session)) -> dict:
    task = tasks.create_task(
        user_id, body.name, body.skill, body.cron_schedule, body.timezone, body.params, body.status
    )
    scheduler.sync_task(task["id"])
    return tasks.get_task(user_id, task["id"])


@router.get("/tasks/{task_id}")
def get_task(task_id: int, user_id: int = Depends(require_session)) -> dict:
    return tasks.get_task(user_id, task_id)


@router.patch("/tasks/{task_id}")
def update_task(task_id: int, body: TaskUpdate, user_id: int = Depends(require_session)) -> dict:
    fields = body.model_dump(exclude_unset=True)
    extra = {"params": fields["params"]} if "params" in fields else {}
    tasks.update_task(
        user_id,
        task_id,
        status=fields.get("status"),
        cron_schedule=fields.get("cron_schedule"),
        timezone_name=fields.get("timezone"),
        skill_name=fields.get("skill"),
        **extra,
    )
    scheduler.sync_task(task_id)
    return tasks.get_task(user_id, task_id)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, user_id: int = Depends(require_session)) -> None:
    if not tasks.delete_task(user_id, task_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Task not found")
    scheduler.sync_task(task_id)


@router.get("/tasks/{task_id}/runs")
def list_runs(
    task_id: int, limit: int = Query(default=20, ge=1, le=100), user_id: int = Depends(require_session)
) -> list[dict]:
    return tasks.list_runs(user_id, task_id, limit)


@router.post("/tasks/{task_id}/run")
def run_now(task_id: int, user_id: int = Depends(require_session)) -> dict:
    """Run the task immediately and wait for it; returns the finished task_runs row."""
    tasks.get_task(user_id, task_id)
    if tasks.is_running(task_id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This task is already running")
    run_id = scheduler.run_task(task_id)
    if run_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This task is already running")
    return tasks.get_run(user_id, run_id)  # type: ignore[return-value]
