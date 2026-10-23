"""POST /chat and the conversation history routes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from .. import conversations, orchestrator, usage
from ..auth import require_session

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: int | None = Field(default=None, ge=1)
    # Start an off-the-record chat (ignored when continuing one, which keeps its own setting).
    private: bool = False
    # Ask the larger model (LARGE_MODEL_ID) to answer this one, when the server has it.
    deep: bool = False
    # Uploaded files attached to this message (POST /documents ids): their text is read with it.
    document_ids: list[int] = Field(default_factory=list, max_length=3)
    # Said out loud to the desktop app: answered in Jarvis's manner (app/persona.py).
    voice: bool = False

    @field_validator("message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message is empty")
        return value.strip()


def origin_of(request: Request) -> str:
    """"desktop" for the Custodian desktop app on the PC, "app" for the phone and web apps. A
    PC action that needs an OK asks where the request came from (app/approvals.py); claiming to
    be the desktop app only moves that question to the PC's own dialog."""
    return "desktop" if request.headers.get("X-Custodian-Device", "").startswith("Custodian desktop") else "app"


@router.post("/chat")
def chat(body: ChatRequest, request: Request, user_id: int = Depends(require_session)) -> dict:
    """One chat turn. Returns {reply, used, conversation_id, message_id, private}; `used`
    lists the memories recalled, tool calls made (with their gate status), the skill version,
    memories saved during the turn, and the accuracy and privacy checks on the reply."""
    usage.check_allowance(user_id)  # 402 when the plan's monthly model allowance is used up
    return orchestrator.handle(
        user_id, body.message, conversation_id=body.conversation_id, private=body.private, deep=body.deep,
        attachments=body.document_ids, origin=origin_of(request), voice=body.voice,
    )


@router.get("/conversations")
def list_conversations(
    channel: Literal["chat", "task"] | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    user_id: int = Depends(require_session),
) -> list[dict]:
    return conversations.list_conversations(user_id, channel, limit)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, user_id: int = Depends(require_session)) -> dict:
    found = conversations.get_conversation(user_id, conversation_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return found


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: int, user_id: int = Depends(require_session)) -> None:
    if not conversations.delete_conversation(user_id, conversation_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Conversation not found")
