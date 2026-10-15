"""Privacy: where data goes, retention and secret withholding, export and erase."""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import privacy
from ..auth import require_session

router = APIRouter(prefix="/privacy", tags=["privacy"])

ERASE_CONFIRMATION = "ERASE"


class PrivacySettings(BaseModel):
    retention_days: int | None = Field(default=None, ge=1, le=3650, description="Delete chats older than this; null keeps them")
    redact_secrets: Literal["auto", "on", "off"] | None = None


class EraseRequest(BaseModel):
    confirm: str = Field(description=f'Must be "{ERASE_CONFIRMATION}"')


@router.get("")
def privacy_summary(user_id: int = Depends(require_session)) -> dict:
    """Where your data goes (model and embedding service), what is stored, and your settings."""
    return privacy.summary(user_id)


@router.patch("/settings")
def update_settings(body: PrivacySettings, user_id: int = Depends(require_session)) -> dict:
    fields = body.model_dump(exclude_unset=True)
    changes = {"retention_days": fields["retention_days"]} if "retention_days" in fields else {}
    privacy.update_settings(user_id, redact_secrets=fields.get("redact_secrets"), **changes)
    return privacy.summary(user_id)


@router.get("/export")
def export_data(user_id: int = Depends(require_session)) -> JSONResponse:
    """Everything Custodian keeps about you, decrypted, as a JSON file to download."""
    filename = f"custodian-export-{date.today().isoformat()}.json"
    return JSONResponse(privacy.export(user_id), headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/erase")
def erase_data(body: EraseRequest, user_id: int = Depends(require_session)) -> dict:
    """Delete every conversation, memory, task, tool call, note and calendar event. Can't be undone."""
    if body.confirm != ERASE_CONFIRMATION:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f'Send {{"confirm": "{ERASE_CONFIRMATION}"}} to erase everything.')
    return {"erased": privacy.erase(user_id)}
