"""The Jarvis features in the app: routines, what Custodian did (with undo), and the settings for
the proactive and learning features.

    GET  /settings/prefs · PATCH /settings/prefs
    GET  /routines · POST /routines · DELETE /routines/{id} · POST /routines/{id}/run
    GET  /activity?q=&days= · POST /activity/{id}/undo
    GET  /pc-index · GET /desktop/index/config · POST /desktop/index   searching inside the PC's files
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from .. import activity, desktop, memory, memory_graph, orchestrator, pc_index, proactive, routines, settings_store, watchers
from ..auth import require_session

router = APIRouter(tags=["jarvis"])


# Settings ------------------------------------------------------------------------------------

@router.get("/settings/prefs")
def get_prefs(user_id: int = Depends(require_session)) -> dict:
    return settings_store.get(user_id)


@router.patch("/settings/prefs")
def set_prefs(body: dict[str, Any], user_id: int = Depends(require_session)) -> dict:
    try:
        prefs = settings_store.update(user_id, **body)
    except settings_store.SettingError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    if body.get("pc_index") is False:
        pc_index.clear(user_id)  # switching it off deletes what was indexed
    elif body.get("pc_index") is True or "pc_index_folders" in body:
        desktop.post(user_id, "index_now", {})  # start now rather than at the next half hour
    return prefs


# Searching inside the PC's files ----------------------------------------------------------------

class IndexedFile(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    name: str = Field(default="", max_length=300)
    kind: str = Field(default="", max_length=20)
    modified: float = 0
    size: int = Field(default=0, ge=0)
    chunks: list[str] = Field(default_factory=list, max_length=pc_index.MAX_CHUNKS_PER_FILE)


class IndexBatch(BaseModel):
    files: list[IndexedFile] = Field(default_factory=list, max_length=pc_index.BATCH_FILES)
    removed: list[str] = Field(default_factory=list, max_length=5000)


@router.get("/pc-index")
def index_status(user_id: int = Depends(require_session)) -> dict:
    return pc_index.status(user_id)


@router.get("/desktop/index/config")
def index_config(user_id: int = Depends(require_session)) -> dict:
    return pc_index.config(user_id)


@router.post("/desktop/index")
def index_files(body: IndexBatch, user_id: int = Depends(require_session)) -> dict:
    try:
        return pc_index.ingest(user_id, [f.model_dump() for f in body.files], body.removed)
    except pc_index.IndexOff as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None


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


# The memory graph --------------------------------------------------------------------------------

@router.get("/memory/graph")
def memory_map(user_id: int = Depends(require_session)) -> list[dict]:
    """The people, places, projects and organisations your memories name, with how they relate."""
    memory_graph.process_pending()  # anything saved a moment ago
    texts = {m["id"]: m for m in memory.list_memories(user_id, limit=1000)}
    return [{**e, "memories": [{"id": i, "content": texts[i]["content"], "current": not texts[i]["superseded_by"]}
                               for i in e["memories"] if i in texts]} for e in memory_graph.graph(user_id)]


# Proactive -------------------------------------------------------------------------------------

@router.post("/briefing")
def morning_briefing(force: bool = False, user_id: int = Depends(require_session)) -> dict:
    """Today's briefing to read out (the desktop app asks the first time you use the PC in the
    morning; the app's "Brief me" button sends force=true). At most once a day otherwise."""
    return proactive.briefing(user_id, force=force)


@router.get("/watches")
def list_watches(user_id: int = Depends(require_session)) -> list[dict]:
    return watchers.list_for(user_id)


@router.delete("/watches/{watch_id}", status_code=status.HTTP_204_NO_CONTENT)
def stop_watch(watch_id: int, user_id: int = Depends(require_session)) -> Response:
    if not watchers.stop(user_id, watch_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such watch")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
