"""Connected accounts besides Google: Spotify and Microsoft 365 (OAuth 2.0 with PKCE), and Home
Assistant (its address and a long-lived access token you create in its profile page).

    start_url(provider, user_id)   -> the provider's consent page; `state` is signed and names the user
    finish(provider, code, state)  exchanges the code for tokens and stores them (Fernet-encrypted)
    connect_home(user_id, url, token) checks the token against Home Assistant and stores it
    access_token(provider, user_id) -> a valid access token for the gate to hand a tool
    api_base(provider, user_id)    -> where that tool's requests go (Home Assistant's own address)
    status(user_id) · disconnect(provider, user_id)

Each provider needs your own app registration (SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET,
MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET), with `<server>/integrations/<provider>/callback`
as its redirect address; README explains it. Connecting an account doesn't by itself let the
model use it: every tool has its own switch in Tools & Data, all off until you turn them on.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import httpx
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import db, google_account, telegram
from .config import get_settings
from .crypto import decrypt, encrypt

log = logging.getLogger(__name__)

STATE_MAX_AGE = 600
_transport: httpx.BaseTransport | None = None  # tests swap in a mock


class AccountError(RuntimeError):
    """Not configured, not connected, or the service refused; the message is safe to show."""


@dataclass(frozen=True)
class Provider:
    name: str  # integrations.provider
    label: str
    env: str  # SPOTIFY -> SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI
    auth_url: str
    token_url: str
    scopes: tuple[str, ...]
    me_url: str
    account_keys: tuple[str, ...]  # the first of these in the "me" answer names the account
    host: str  # what "Where your data goes" lists
    extra: tuple[tuple[str, str], ...] = ()


PROVIDERS: dict[str, Provider] = {
    "spotify": Provider(
        name="spotify", label="Spotify", env="SPOTIFY",
        auth_url="https://accounts.spotify.com/authorize", token_url="https://accounts.spotify.com/api/token",
        scopes=("user-read-playback-state", "user-modify-playback-state", "user-read-currently-playing",
                "playlist-read-private"),
        me_url="https://api.spotify.com/v1/me", account_keys=("display_name", "id"), host="api.spotify.com",
    ),
    "microsoft": Provider(
        name="microsoft", label="Microsoft 365", env="MICROSOFT",
        auth_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        scopes=("offline_access", "User.Read", "Mail.Read", "Calendars.ReadWrite"),
        me_url="https://graph.microsoft.com/v1.0/me", account_keys=("mail", "userPrincipalName"),
        host="graph.microsoft.com", extra=(("prompt", "select_account"),),
    ),
}
HOME = "homeassistant"
# Which connected account each tool needs (registry ToolFunction.account).
TOOL_ACCOUNTS = {"spotify": "spotify", "outlook": "microsoft", "home": HOME}


def client(provider: Provider) -> tuple[str, str, str]:
    cid = os.getenv(f"{provider.env}_CLIENT_ID", "").strip()
    secret = os.getenv(f"{provider.env}_CLIENT_SECRET", "").strip()
    redirect = (os.getenv(f"{provider.env}_REDIRECT_URI", "").strip()
                or f"http://localhost:8000/integrations/{provider.name}/callback")
    if not cid or not secret:
        raise AccountError(f"{provider.label} isn't set up on this server: set {provider.env}_CLIENT_ID and "
                           f"{provider.env}_CLIENT_SECRET (see README).")
    return cid, secret, redirect


def _provider(name: str) -> Provider:
    try:
        return PROVIDERS[name]
    except KeyError:
        raise AccountError(f"Custodian can't connect {name!r}.") from None


def configured(name: str) -> bool:
    if name == HOME:
        return True  # nothing to register: you paste its address and a token
    try:
        client(_provider(name))
    except AccountError:
        return False
    return True


def _serializer(name: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt=f"custodian.{name}.oauth")


def _http() -> httpx.Client:
    return httpx.Client(timeout=15, transport=_transport)


def _store(user_id: int, provider: str, account: str | None, scopes: str, refresh: str, access: str | None,
           expires: datetime | None) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO integrations (user_id, provider, account, scopes, refresh_token, access_token, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id, provider) DO UPDATE SET account = excluded.account, scopes = excluded.scopes,
              refresh_token = excluded.refresh_token, access_token = excluded.access_token,
              expires_at = excluded.expires_at, created_at = datetime('now')
            """,
            (user_id, provider, account, scopes, encrypt(refresh), encrypt(access) if access else None,
             db.to_db_time(expires) if expires else None),
        )


def _row(user_id: int, provider: str):  # noqa: ANN202 - sqlite3.Row | None
    with db.connect() as conn:
        return conn.execute("SELECT * FROM integrations WHERE user_id = ? AND provider = ?", (user_id, provider)).fetchone()


# OAuth: Spotify and Microsoft ------------------------------------------------------------------

def start_url(name: str, user_id: int) -> str:
    provider = _provider(name)
    cid, _, redirect = client(provider)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = _serializer(name).dumps({"u": user_id, "v": verifier, "n": secrets.token_hex(8)})
    return provider.auth_url + "?" + urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": " ".join(provider.scopes),
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256", **dict(provider.extra),
    })


def _expiry(tokens: dict) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=int(tokens.get("expires_in", 3600)) - 60)


def finish(name: str, code: str, state: str) -> dict:
    """Store the tokens for the user named in `state`; returns {account}."""
    provider = _provider(name)
    try:
        data = _serializer(name).loads(state, max_age=STATE_MAX_AGE)
    except SignatureExpired:
        raise AccountError("That sign-in took too long; start again from Tools & Data.") from None
    except BadSignature:
        raise AccountError("That sign-in link isn't valid; start again from Tools & Data.") from None
    cid, secret, redirect = client(provider)
    with _http() as http:
        resp = http.post(provider.token_url, data={"code": code, "client_id": cid, "client_secret": secret,
                                                   "redirect_uri": redirect, "grant_type": "authorization_code",
                                                   "code_verifier": data["v"]})
        if resp.status_code >= 400:
            raise AccountError(f"{provider.label} didn't accept the sign-in ({resp.status_code}).")
        tokens = resp.json()
        if not tokens.get("refresh_token"):
            raise AccountError(f"{provider.label} didn't grant lasting access; connect again.")
        me = http.get(provider.me_url, headers={"Authorization": f"Bearer {tokens['access_token']}"})
    info = me.json() if me.status_code < 400 else {}
    account = next((str(info[k]) for k in provider.account_keys if info.get(k)), None)
    _store(data["u"], name, account, tokens.get("scope", " ".join(provider.scopes)), tokens["refresh_token"],
           tokens["access_token"], _expiry(tokens))
    log.info("connected a %s account", provider.label)
    return {"account": account}


def access_token(name: str, user_id: int) -> str:
    """A valid access token, refreshed when it's about to expire. Raises AccountError."""
    if name == HOME:
        row = _row(user_id, HOME)
        if row is None:
            raise AccountError("Home Assistant isn't connected; connect it in Tools & Data.")
        return decrypt(row["refresh_token"])
    provider = _provider(name)
    row = _row(user_id, name)
    if row is None:
        raise AccountError(f"{provider.label} isn't connected; connect it in Tools & Data.")
    if row["access_token"] and row["expires_at"] and db.from_db_time(row["expires_at"]) > datetime.now(timezone.utc):
        return decrypt(row["access_token"])
    cid, secret, _ = client(provider)
    with _http() as http:
        resp = http.post(provider.token_url, data={"client_id": cid, "client_secret": secret,
                                                   "grant_type": "refresh_token",
                                                   "refresh_token": decrypt(row["refresh_token"])})
    if resp.status_code >= 400:
        raise AccountError(f"{provider.label} access was withdrawn or expired; reconnect it in Tools & Data.")
    tokens = resp.json()
    refresh = tokens.get("refresh_token") or decrypt(row["refresh_token"])  # Microsoft sends a new one each time
    with db.connect() as conn:
        conn.execute(
            "UPDATE integrations SET access_token = ?, refresh_token = ?, expires_at = ? WHERE user_id = ? AND provider = ?",
            (encrypt(tokens["access_token"]), encrypt(refresh), db.to_db_time(_expiry(tokens)), user_id, name),
        )
    return tokens["access_token"]


# Home Assistant --------------------------------------------------------------------------------

def _home_url(url: str) -> str:
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.path not in ("", "/"):
        raise AccountError("Give Home Assistant's address, like http://homeassistant.local:8123")
    return f"{parts.scheme}://{parts.netloc}"


def connect_home(user_id: int, url: str, token: str) -> dict:
    """Check the token with Home Assistant (GET /api/), then store it. It's on your own network,
    so this is the one tool the sandbox lets reach a local address - that one address only."""
    base = _home_url(url)
    token = token.strip()
    if not 20 <= len(token) <= 1000:
        raise AccountError("That doesn't look like a Home Assistant long-lived access token.")
    try:
        with _http() as http:
            resp = http.get(f"{base}/api/", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise AccountError(f"Couldn't reach Home Assistant at {base}: {exc}") from None
    if resp.status_code in (401, 403):
        raise AccountError("Home Assistant didn't accept that token.")
    if resp.status_code >= 400:
        raise AccountError(f"Home Assistant answered {resp.status_code}.")
    _store(user_id, HOME, base, "api", token, None, None)
    return {"account": base}


def api_base(name: str, user_id: int) -> str | None:
    if name != HOME:
        return None
    row = _row(user_id, HOME)
    return row["account"] if row else None


# Every connected account -----------------------------------------------------------------------

def status(user_id: int) -> list[dict]:
    with db.connect() as conn:
        rows = {r["provider"]: r for r in conn.execute(
            "SELECT provider, account, created_at FROM integrations WHERE user_id = ?", (user_id,))}
    out = []
    for name, label in (("spotify", "Spotify"), ("microsoft", "Microsoft 365"), (HOME, "Home Assistant")):
        row = rows.get(name)
        out.append({"provider": name, "label": label, "configured": configured(name), "connected": row is not None,
                    "account": row["account"] if row else None,
                    "connected_at": db.iso(row["created_at"]) if row else None})
    return out


def connected(user_id: int) -> set[str]:
    """The accounts a tool can use right now: Google and the ones above."""
    with db.connect() as conn:
        return {r["provider"] for r in conn.execute("SELECT provider FROM integrations WHERE user_id = ?", (user_id,))}


def disconnect(name: str, user_id: int) -> bool:
    if name == "google":
        return google_account.disconnect(user_id)
    with db.connect() as conn:
        return conn.execute("DELETE FROM integrations WHERE user_id = ? AND provider = ?", (user_id, name)).rowcount > 0


def where_data_goes(user_id: int) -> list[dict]:
    """For Account -> Where your data goes."""
    out = []
    if telegram.status(user_id)["connected"]:
        out.append({"name": "Telegram", "host": "api.telegram.org", "location": "internet",
                    "purpose": "Chatting with Custodian, approvals and alerts in your Telegram chat"})
    for item in status(user_id):
        if not item["connected"]:
            continue
        if item["provider"] == HOME:
            out.append({"name": "Home Assistant", "host": urlsplit(item["account"] or "").hostname or "",
                        "location": "local network", "purpose": "Lights, AC and other devices at home"})
        else:
            provider = PROVIDERS[item["provider"]]
            out.append({"name": provider.label, "host": provider.host, "location": "internet",
                        "purpose": f"{provider.label} account"})
    return out
