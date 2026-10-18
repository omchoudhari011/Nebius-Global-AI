"""The Jarvis features in the app: routines, what Custodian did (with undo), and the settings for
the proactive and learning features.

    GET  /settings/prefs · PATCH /settings/prefs
    GET  /routines · POST /routines · DELETE /routines/{id} · POST /routines/{id}/run
    GET  /activity?q=&days= · POST /activity/{id}/undo
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from .. import activity, desktop, orchestrator, routines, settings_store
from ..auth import require_session

router = APIRouter(tags=["jarvis"])


# Settings ------------------------------------------------------------------------------------

@router.get("/settings/prefs")
def get_prefs(user_id: int = Depends(require_session)) -> dict:
    return settings_store.get(user_id)


@router.patch("/settings/prefs")
def set_prefs(body: dict[str, Any], user_id: int = Depends(require_session)) -> dict:
    try:
        return settings_store.update(user_id, **body)
    except settings_store.SettingError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


# Routines ------------------------------------------------------------------------------------

class RoutineBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    steps: list[str] = Field(min_length=1, max_length=12)
    phrases: list[str] = Field(default_factory=list, max_length=5)


@router.get("/routines")
def list_routines(user_id: int = Depends(require_session)) -> list[dict]:
    return routines.list_for(user_id)


@router.post("/routines", status_code=status.HTTP_201_CREATED)
def save_routine(body: RoutineBody, user_id: int = Depends(require_session)) -> dict:
    try:
        return routines.save(user_id, body.name, body.steps, body.phrases)
    except routines.RoutineError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.delete("/routines/{routine_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_routine(routine_id: int, user_id: int = Depends(require_session)) -> Response:
    if not routines.delete(user_id, routine_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Routine not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/routines/{routine_id}/run")
def run_routine(routine_id: int, user_id: int = Depends(require_session)) -> dict:
    """Run it as if you had said its name: a chat turn, so it shows up in your history."""
    routine = routines.get(user_id, routine_id)
    if routine is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Routine not found")
    return orchestrator.handle(user_id, routine["name"])


# Activity and undo ---------------------------------------------------------------------------

@router.get("/activity")
def list_activity(q: str = Query(default="", max_length=200), days: int = Query(default=7, ge=1, le=365),
                  limit: int = Query(default=50, ge=1, le=200), user_id: int = Depends(require_session)) -> list[dict]:
    if q.strip():
        return activity.search(user_id, q, days, limit)
    return activity.recent(user_id, limit)


@router.post("/activity/{call_id}/undo")
def undo(call_id: int, user_id: int = Depends(require_session)) -> dict:
    try:
        return activity.undo(user_id, call_id)
    except activity.NothingToUndo as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from None
    except (RuntimeError, desktop.DesktopUnavailable) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from None
