"""POST /auth/login, POST /auth/logout, GET /auth/me, and the signed-in devices:
GET /auth/sessions, DELETE /auth/sessions/{id}, DELETE /auth/sessions (all but this one)."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from .. import __version__, auth, scheduler
from ..config import get_settings
from ..model_backends import get_backend

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)


class LoginRequest(BaseModel):
    passcode: str = Field(min_length=1, max_length=256)
    # The native app keeps the token in the device keychain; the web build relies on the
    # httpOnly cookie alone, so it never sees the token.
    client: Literal["web", "native"] = "web"


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _server_info() -> dict:
    settings = get_settings()
    backend = get_backend()
    return {
        "version": __version__,
        "model_backend": backend.name,
        "model": backend.model_id,
        "timezone": settings.timezone,
        "scheduler": scheduler.is_running(),
    }


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    key = _client_key(request)
    wait = auth.login_limiter.attempt(key)  # counted now; a right passcode takes it back
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many attempts. Try again in {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )
    if not auth.passcode_matches(body.passcode):
        log.warning("failed sign-in from %s", key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="That passcode isn't right.")
    auth.login_limiter.succeeded(key)

    settings = get_settings()
    user_id = auth.owner_id()
    token = auth.issue_token(user_id, client=body.client, user_agent=request.headers.get("User-Agent", ""))
    response.set_cookie(
        auth.COOKIE_NAME,
        token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    result: dict = {"user": auth.get_user(user_id), "server": _server_info(), "expires_in": settings.session_max_age_seconds}
    if body.client == "native":
        result["token"] = token
    return result


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> Response:
    """End this device's session on the server, so its token stops working, and clear the cookie."""
    auth.end_session(auth.token_from(request))
    settings = get_settings()
    response.delete_cookie(
        auth.COOKIE_NAME, path="/", secure=settings.cookie_secure, httponly=True, samesite=settings.cookie_samesite
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me")
def me(user_id: int = Depends(auth.require_session)) -> dict:
    return {"user": auth.get_user(user_id), "server": _server_info()}


@router.get("/sessions")
def sessions(request: Request, user_id: int = Depends(auth.require_session)) -> list[dict]:
    """Every device signed in to this account, most recently used first."""
    return auth.list_sessions(user_id, getattr(request.state, "session_id", None))


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(session_id: int, user_id: int = Depends(auth.require_session)) -> Response:
    if not auth.revoke_session(user_id, session_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That device isn't signed in")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/sessions")
def revoke_other_sessions(request: Request, user_id: int = Depends(auth.require_session)) -> dict:
    """Sign out every other device; this one stays signed in."""
    return {"signed_out": auth.revoke_other_sessions(user_id, getattr(request.state, "session_id", None))}
