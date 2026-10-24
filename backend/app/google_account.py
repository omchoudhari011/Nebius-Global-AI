"""Connecting a Google account (OAuth 2.0, authorization-code flow with PKCE).

    start_url()      -> the Google consent page; `state` is signed and names the user
    finish(code, state) exchanges the code for tokens and stores them (Fernet-encrypted)
    access_token()   -> a valid access token for the gate to hand a tool, refreshed when needed
    disconnect()     revokes the grant at Google and deletes the tokens

Your own Google Cloud OAuth client is needed (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET, type
"Web application", with GOOGLE_REDIRECT_URI as an authorised redirect URI); README explains
it. The scopes are read-and-add for Calendar, read-only for Gmail and Drive; each tool also
has its own switch in Tools & Data, all off until you turn them on.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import db
from .config import get_settings
from .crypto import decrypt, encrypt

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",  # drafts, and sending - each send asked first
    "https://www.googleapis.com/auth/drive.readonly",
)
STATE_MAX_AGE = 600  # seconds to finish the consent screen

_transport: httpx.BaseTransport | None = None  # tests swap in a mock


class GoogleError(RuntimeError):
    """Not configured, not connected, or Google refused; the message is safe to show."""


def client() -> tuple[str, str, str]:
    cid, secret = os.getenv("GOOGLE_CLIENT_ID", "").strip(), os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    redirect = os.getenv("GOOGLE_REDIRECT_URI", "").strip() or "http://localhost:8000/integrations/google/callback"
    if not cid or not secret:
        raise GoogleError("Google isn't set up on this server: set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (see README).")
    return cid, secret, redirect


def configured() -> bool:
    try:
        client()
    except GoogleError:
        return False
    return True


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="custodian.google.oauth")


def _http() -> httpx.Client:
    return httpx.Client(timeout=15, transport=_transport)


def start_url(user_id: int) -> str:
    cid, _, redirect = client()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = _serializer().dumps({"u": user_id, "v": verifier, "n": secrets.token_hex(8)})
    return AUTH_URL + "?" + urlencode(
        {
            "client_id": cid,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",  # a refresh token, so the connection lasts
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )


def finish(code: str, state: str) -> dict:
    """Store the tokens for the user named in `state`; returns {account}."""
    try:
        data = _serializer().loads(state, max_age=STATE_MAX_AGE)
    except SignatureExpired:
        raise GoogleError("That sign-in took too long; start again from Tools & Data.") from None
    except BadSignature:
        raise GoogleError("That sign-in link isn't valid; start again from Tools & Data.") from None
    cid, secret, redirect = client()
    with _http() as http:
        resp = http.post(TOKEN_URL, data={"code": code, "client_id": cid, "client_secret": secret,
                                          "redirect_uri": redirect, "grant_type": "authorization_code",
                                          "code_verifier": data["v"]})
        if resp.status_code >= 400:
            raise GoogleError(f"Google didn't accept the sign-in ({resp.status_code}).")
        tokens = resp.json()
        if not tokens.get("refresh_token"):
            raise GoogleError("Google didn't grant lasting access; remove Custodian at myaccount.google.com and connect again.")
        info = http.get(USERINFO_URL, headers={"Authorization": f"Bearer {tokens['access_token']}"})
    account = info.json().get("email") if info.status_code < 400 else None
    expires = datetime.now(timezone.utc) + timedelta(seconds=int(tokens.get("expires_in", 3600)) - 60)
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO integrations (user_id, provider, account, scopes, refresh_token, access_token, expires_at)
            VALUES (?, 'google', ?, ?, ?, ?, ?)
            ON CONFLICT (user_id, provider) DO UPDATE SET account = excluded.account, scopes = excluded.scopes,
              refresh_token = excluded.refresh_token, access_token = excluded.access_token,
              expires_at = excluded.expires_at, created_at = datetime('now')
            """,
            (data["u"], account, tokens.get("scope", " ".join(SCOPES)), encrypt(tokens["refresh_token"]),
             encrypt(tokens["access_token"]), db.to_db_time(expires)),
        )
    log.info("connected a Google account")
    return {"account": account}


def status(user_id: int) -> dict:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT account, scopes, created_at FROM integrations WHERE user_id = ? AND provider = 'google'", (user_id,)
        ).fetchone()
    return {
        "provider": "google",
        "configured": configured(),
        "connected": row is not None,
        "account": row["account"] if row else None,
        "connected_at": db.iso(row["created_at"]) if row else None,
    }


def access_token(user_id: int) -> str:
    """A valid access token, refreshed when it's about to expire. Raises GoogleError."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT refresh_token, access_token, expires_at FROM integrations WHERE user_id = ? AND provider = 'google'",
            (user_id,),
        ).fetchone()
    if row is None:
        raise GoogleError("Google isn't connected; connect it in Tools & Data.")
    if row["access_token"] and row["expires_at"] and db.from_db_time(row["expires_at"]) > datetime.now(timezone.utc):
        return decrypt(row["access_token"])
    cid, secret, _ = client()
    with _http() as http:
        resp = http.post(TOKEN_URL, data={"client_id": cid, "client_secret": secret, "grant_type": "refresh_token",
                                          "refresh_token": decrypt(row["refresh_token"])})
    if resp.status_code >= 400:
        raise GoogleError("Google access was withdrawn or expired; reconnect Google in Tools & Data.")
    tokens = resp.json()
    expires = datetime.now(timezone.utc) + timedelta(seconds=int(tokens.get("expires_in", 3600)) - 60)
    with db.connect() as conn:
        conn.execute(
            "UPDATE integrations SET access_token = ?, expires_at = ? WHERE user_id = ? AND provider = 'google'",
            (encrypt(tokens["access_token"]), db.to_db_time(expires), user_id),
        )
    return tokens["access_token"]


def disconnect(user_id: int) -> bool:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT refresh_token FROM integrations WHERE user_id = ? AND provider = 'google'", (user_id,)
        ).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM integrations WHERE user_id = ? AND provider = 'google'", (user_id,))
    try:  # best effort: the tokens are gone from here either way
        with _http() as http:
            http.post(REVOKE_URL, data={"token": decrypt(row["refresh_token"])})
    except httpx.HTTPError as exc:
        log.warning("couldn't revoke the Google token: %s", exc)
    return True

