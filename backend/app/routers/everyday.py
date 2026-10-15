"""Day-to-day features: notifications and reminders, files, voice, feedback on replies, the
Google connection, and which of these this server has set up.

    GET  /features
    GET  /notifications · POST /notifications/{id}/read · POST /notifications/read-all
    GET  /reminders · DELETE /reminders/{id}
    GET  /documents · POST /documents · DELETE /documents/{id}
    POST /voice/transcribe                 raw audio body (Content-Type: audio/...)
    POST /messages/{id}/feedback · DELETE /messages/{id}/feedback
    GET  /integrations · GET /integrations/google/start · DELETE /integrations/google
    GET  /integrations/google/callback     Google's redirect after consent (no session: the
                                           signed `state` names the account)
"""

from __future__ import annotations

import html
import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .. import documents, feedback, google_account, notifications, voice
from ..auth import require_session
from ..model_backends import get_large_backend

router = APIRouter(tags=["everyday"])


@router.get("/features")
def features(user_id: int = Depends(require_session)) -> dict:
    """What this server has set up, so the app shows only what works."""
    large = get_large_backend()
    return {
        "voice": voice.configured(),
        "photos": bool(os.getenv("VISION_MODEL", "").strip()),
        "larger_model": large.model_id if large else None,
        "google": google_account.configured(),
        "phone_push": bool(os.getenv("NTFY_URL", "").strip()),
        "unread": notifications.unread_count(user_id),
        "feedback": feedback.counts(user_id),
    }


# Notifications and reminders -------------------------------------------------------------

@router.get("/notifications")
def list_notifications(unread: bool = False, user_id: int = Depends(require_session)) -> list[dict]:
    return notifications.list_for(user_id, unread_only=unread)


@router.post("/notifications/read-all")
def read_all(user_id: int = Depends(require_session)) -> dict:
    return {"marked": notifications.mark_read(user_id)}


@router.post("/notifications/{note_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def read_one(note_id: int, user_id: int = Depends(require_session)) -> Response:
    notifications.mark_read(user_id, note_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/reminders")
def upcoming(user_id: int = Depends(require_session)) -> list[dict]:
    return notifications.upcoming_reminders(user_id)


@router.delete("/reminders/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel(reminder_id: int, user_id: int = Depends(require_session)) -> Response:
    if not notifications.cancel_reminder(user_id, reminder_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such pending reminder")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Files -------------------------------------------------------------------------------------

class Upload(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="", max_length=100)
    data: str = Field(min_length=1, description="The file, base64-encoded")


@router.get("/documents")
def list_documents(user_id: int = Depends(require_session)) -> list[dict]:
    return documents.list_for(user_id)


@router.post("/documents", status_code=status.HTTP_201_CREATED)
def upload(body: Upload, user_id: int = Depends(require_session)) -> dict:
    try:
        return documents.add(user_id, body.name, body.content_type, body.data)
    except documents.DocumentError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.delete("/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(doc_id: int, user_id: int = Depends(require_session)) -> Response:
    if not documents.delete(user_id, doc_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="File not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Voice -------------------------------------------------------------------------------------

@router.post("/voice/transcribe")
async def transcribe(request: Request, user_id: int = Depends(require_session)) -> dict:
    audio = await request.body()
    try:
        return {"text": voice.transcribe(audio, request.headers.get("Content-Type", ""))}
    except voice.VoiceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


# Feedback ----------------------------------------------------------------------------------

class Feedback(BaseModel):
    rating: Literal[1, -1]
    correction: str | None = Field(default=None, max_length=feedback.MAX_CORRECTION)
    share: bool = False


@router.post("/messages/{message_id}/feedback")
def rate(message_id: int, body: Feedback, user_id: int = Depends(require_session)) -> dict:
    try:
        return feedback.save(user_id, message_id, body.rating, body.correction, body.share)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Message not found") from None
    except feedback.FeedbackError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.delete("/messages/{message_id}/feedback", status_code=status.HTTP_204_NO_CONTENT)
def unrate(message_id: int, user_id: int = Depends(require_session)) -> Response:
    try:
        feedback.clear(user_id, message_id)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Message not found") from None
    except feedback.FeedbackError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Google ------------------------------------------------------------------------------------

@router.get("/integrations")
def integrations(user_id: int = Depends(require_session)) -> list[dict]:
    return [google_account.status(user_id)]


@router.get("/integrations/google/start")
def google_start(user_id: int = Depends(require_session)) -> dict:
    try:
        return {"url": google_account.start_url(user_id)}
    except google_account.GoogleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None


def _page(title: str, text: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><body style='font:16px/1.5 system-ui,sans-serif;max-width:520px;margin:15vh auto;"
        f"padding:0 16px'><h1 style='font-size:22px'>{html.escape(title)}</h1><p>{html.escape(text)}</p>"
        "<p><a href='/'>Back to Custodian</a></p></body>",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/integrations/google/callback", include_in_schema=False)
def google_callback(code: str = "", state: str = "", error: str = "") -> HTMLResponse:
    if error or not code:
        return _page("Google wasn't connected", "The request was cancelled or refused. You can try again from Tools & Data.")
    try:
        result = google_account.finish(code, state)
    except google_account.GoogleError as exc:
        return _page("Google wasn't connected", str(exc))
    who = f" as {result['account']}" if result.get("account") else ""
    return _page("Google is connected", f"Custodian is connected{who}. Turn on the switches you want in Tools & Data; "
                 "every Google switch starts off. You can close this tab.")


@router.delete("/integrations/google", status_code=status.HTTP_204_NO_CONTENT)
def google_disconnect(user_id: int = Depends(require_session)) -> Response:
    google_account.disconnect(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
