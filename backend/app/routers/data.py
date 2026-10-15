"""The Tools & Data screen: permission switches, memories and the tool-call audit log."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from .. import memory, permissions
from ..auth import require_session
from ..tools import sandbox

router = APIRouter(tags=["tools & data"])

MemoryType = Literal["episodic", "semantic", "preference"]
ToolStatus = Literal["rejected", "denied", "running", "succeeded", "failed", "timed_out"]


class PermissionUpdate(BaseModel):
    scope: str = Field(min_length=1, max_length=32)
    granted: bool


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=memory.MAX_MEMORY_CHARS)
    type: MemoryType = "semantic"


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=memory.MAX_MEMORY_CHARS)
    type: MemoryType | None = None


@router.get("/permissions")
def list_permissions(user_id: int = Depends(require_session)) -> list[dict]:
    """Every tool scope with your switch. A scope you never touched is off."""
    return permissions.list_for_user(user_id)


@router.get("/permissions/events")
def permission_events(
    limit: int = Query(default=50, ge=1, le=200), user_id: int = Depends(require_session)
) -> list[dict]:
    return permissions.events(user_id, limit)


@router.post("/permissions/{tool_id}")
def set_permission(tool_id: int, body: PermissionUpdate, user_id: int = Depends(require_session)) -> dict:
    """Flip one switch: permissions.grant() or revoke() for (tool_id, scope)."""
    try:
        return permissions.set_granted(user_id, tool_id, body.scope, body.granted)
    except permissions.UnknownScope:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That tool doesn't have this scope") from None


@router.get("/memories")
def list_memories(
    type: MemoryType | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    user_id: int = Depends(require_session),
) -> list[dict]:
    return memory.list_memories(user_id, type, limit)


@router.post("/memories", status_code=status.HTTP_201_CREATED)
def add_memory(body: MemoryCreate, user_id: int = Depends(require_session)) -> dict:
    memory_id = memory.write(user_id, body.content, body.type)
    return memory.get(user_id, memory_id)  # type: ignore[return-value]


@router.patch("/memories/{memory_id}")
def edit_memory(memory_id: int, body: MemoryUpdate, user_id: int = Depends(require_session)) -> dict:
    updated = memory.update(user_id, memory_id, content=body.content, type=body.type)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Memory not found")
    return updated


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(memory_id: int, user_id: int = Depends(require_session)) -> None:
    if not memory.delete(user_id, memory_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Memory not found")


@router.get("/tool-calls")
def tool_calls(
    status_filter: ToolStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    user_id: int = Depends(require_session),
) -> list[dict]:
    """Audit log of every tool call the model proposed, including blocked ones."""
    return sandbox.list_tool_calls(user_id, limit, status=status_filter)
