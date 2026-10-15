"""Single-user sign-in.

The owner signs in with CUSTODIAN_PASSCODE and gets a session token signed with
itsdangerous.TimestampSigner. The web build receives it as an httpOnly cookie; the
native app stores the same token in the device keychain and sends it as
`Authorization: Bearer <token>`. require_session() guards every route except /health
and /auth/login, raising 401 when the token is missing, forged, expired, signed out or
belongs to a deleted user.

Every sign-in is a row in `sessions` and the token carries a random id naming that row
(only its SHA-256 is stored). Signing out deletes the row, so a token stops working at once
- also one copied off a lost phone - and the Account tab can list and sign out devices.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from . import db
from .config import get_settings

COOKIE_NAME = "custodian_session"

log = logging.getLogger(__name__)


# Account setup ------------------------------------------------------------------------

def ensure_owner() -> int:
    """Create the single account row on first start, or keep it in step with CUSTODIAN_USER_EMAIL."""
    email = get_settings().user_email
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            return row["id"]
        users = conn.execute("SELECT id, email FROM users ORDER BY id").fetchall()
        if len(users) == 1:
            log.info("Updating the account email from %s to %s", users[0]["email"], email)
            conn.execute("UPDATE users SET email = ? WHERE id = ?", (email, users[0]["id"]))
            return users[0]["id"]
        if len(users) > 1:
            raise RuntimeError(
                f"The database has {len(users)} users and none matches CUSTODIAN_USER_EMAIL={email}"
            )
        return conn.execute("INSERT INTO users (email) VALUES (?)", (email,)).lastrowid


def owner_id() -> int:
    with db.connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (get_settings().user_email,)).fetchone()
    if row is None:
        return ensure_owner()
    return row["id"]


def get_user(user_id: int) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT id, email, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    return {"id": row["id"], "email": row["email"], "created_at": db.iso(row["created_at"])} if row else None


# Tokens -------------------------------------------------------------------------------

def _signer() -> TimestampSigner:
    # The salt carries a fingerprint of the passcode, so changing CUSTODIAN_PASSCODE (or
    # SESSION_SECRET) signs out every device, including one that learned the old passcode.
    settings = get_settings()
    fingerprint = hashlib.sha256(settings.passcode.encode("utf-8")).hexdigest()[:16]
    return TimestampSigner(settings.session_secret, salt=f"custodian.session.v3.{fingerprint}")


SESSION_TOUCH_SECONDS = 300  # last_seen_at is refreshed at most this often per session


def _hash(session_key: str) -> str:
    return hashlib.sha256(session_key.encode("ascii")).hexdigest()


def describe_device(user_agent: str, client: str) -> str:
    """'Chrome on Windows', 'Custodian app on Android'... from a User-Agent, for the device list."""
    ua = user_agent or ""
    system = next(
        (name for pattern, name in (
            (r"iPhone|iPad|iOS|CFNetwork|Darwin", "iOS"), (r"Android|okhttp", "Android"), (r"Windows", "Windows"),
            (r"Mac OS X|Macintosh", "macOS"), (r"CrOS", "ChromeOS"), (r"Linux", "Linux"),
        ) if re.search(pattern, ua)),
        None,
    )
    if client == "native":
        return f"Custodian app on {system}" if system else "Custodian app"
    browser = next(
        (name for pattern, name in (
            (r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"Firefox/", "Firefox"), (r"Chrome/|CriOS/", "Chrome"),
            (r"Safari/", "Safari"),
        ) if re.search(pattern, ua)),
        "A browser",
    )
    return f"{browser} on {system}" if system else browser


def issue_token(user_id: int, *, client: str = "web", user_agent: str = "") -> str:
    """Start a session: store its row and return the signed token that names it."""
    session_key = secrets.token_urlsafe(24)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (user_id, token_hash, client, device) VALUES (?, ?, ?, ?)",
            (user_id, _hash(session_key), client, describe_device(user_agent, client)[:80]),
        )
    return _signer().sign(f"{user_id}:{session_key}").decode()


def _unsign(token: str) -> tuple[int, str] | None:
    """(user id, session key) from a token with a good signature that hasn't expired."""
    try:
        value = _signer().unsign(token, max_age=get_settings().session_max_age_seconds).decode()
        user, session_key = value.split(":", 1)
        return int(user), session_key
    except (BadSignature, SignatureExpired, ValueError, UnicodeDecodeError):
        return None


def session_for(token: str) -> tuple[int, int] | None:
    """(user id, session row id) for a live session, or None. Refreshes last_seen_at."""
    parsed = _unsign(token)
    if parsed is None:
        return None
    user_id, session_key = parsed
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT s.id, s.last_seen_at <= datetime('now', ?) AS stale FROM sessions s
            JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.user_id = ?
            """,
            (f"-{SESSION_TOUCH_SECONDS} seconds", _hash(session_key), user_id),
        ).fetchone()
        if row is None:
            return None
        if row["stale"]:
            conn.execute("UPDATE sessions SET last_seen_at = datetime('now') WHERE id = ?", (row["id"],))
    return user_id, row["id"]


def verify_token(token: str) -> int | None:
    found = session_for(token)
    return found[0] if found else None


def end_session(token: str | None) -> bool:
    """Sign out the session this token names (a no-op for a bad or already-ended token)."""
    parsed = _unsign(token) if token else None
    if parsed is None:
        return False
    with db.connect() as conn:
        return conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(parsed[1]),)).rowcount > 0


def list_sessions(user_id: int, current_id: int | None = None) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, client, device, created_at, last_seen_at FROM sessions WHERE user_id = ? ORDER BY last_seen_at DESC, id DESC",
            (user_id,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "client": r["client"],
            "device": r["device"],
            "created_at": db.iso(r["created_at"]),
            "last_seen_at": db.iso(r["last_seen_at"]),
            "current": r["id"] == current_id,
        }
        for r in rows
    ]


def revoke_session(user_id: int, session_id: int) -> bool:
    with db.connect() as conn:
        return conn.execute("DELETE FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)).rowcount > 0


def revoke_other_sessions(user_id: int, keep_id: int | None) -> int:
    with db.connect() as conn:
        return conn.execute("DELETE FROM sessions WHERE user_id = ? AND id IS NOT ?", (user_id, keep_id)).rowcount


def purge_expired_sessions() -> int:
    """Delete sessions whose token has expired anyway (run hourly by the scheduler)."""
    seconds = get_settings().session_max_age_seconds
    with db.connect() as conn:
        return conn.execute("DELETE FROM sessions WHERE created_at <= datetime('now', ?)", (f"-{seconds} seconds",)).rowcount


def passcode_matches(candidate: str) -> bool:
    # Compare fixed-length digests: compare_digest on the raw strings reveals the length.
    expected = hashlib.sha256(get_settings().passcode.encode("utf-8")).digest()
    return hmac.compare_digest(hashlib.sha256(candidate.encode("utf-8")).digest(), expected)


def token_from(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header[:7].lower() == "bearer ":
        return header[7:].strip() or None
    return request.cookies.get(COOKIE_NAME)


def require_session(request: Request) -> int:
    """FastAPI dependency: the signed-in user's id, or 401."""
    token = token_from(request)
    found = session_for(token) if token else None
    if found is not None:
        request.state.session_id = found[1]
        return found[0]
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not signed in",
        headers={"WWW-Authenticate": "Bearer"},
    )


# Brute-force protection for /auth/login -------------------------------------------------

class LoginLimiter:
    """At most `limit` failed attempts per client, and `global_limit` from all clients
    together, in `window` seconds (in-process).

    attempt() counts an attempt *before* the passcode is checked, under the lock, so
    parallel requests can't all pass the check before any failure is recorded; a correct
    passcode takes it back with succeeded(). The global cap stops an attacker who rotates
    addresses (an IPv6 block, a botnet) from getting a fresh allowance per address; while
    it is reached, new sign-ins wait, but devices already signed in are unaffected.
    Memory stays bounded: every stored timestamp is also in the global queue.
    """

    def __init__(self, limit: int = 5, window: float = 900.0, global_limit: int = 50) -> None:
        self.limit = limit
        self.window = window
        self.global_limit = global_limit
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._all: deque[tuple[float, str]] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        while self._all and now - self._all[0][0] > self.window:
            _, key = self._all.popleft()
            q = self._failures.get(key)
            if q:
                q.popleft()
                if not q:
                    del self._failures[key]

    def _wait(self, oldest: float, now: float) -> int:
        return max(1, int(self.window - (now - oldest)) + 1)

    def attempt(self, key: str) -> int:
        """Count one sign-in attempt for this client: 0 if allowed, else seconds to wait."""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            q = self._failures.get(key)
            if q and len(q) >= self.limit:
                return self._wait(q[0], now)
            if len(self._all) >= self.global_limit:
                return self._wait(self._all[0][0], now)
            self._failures[key].append(now)
            self._all.append((now, key))
            return 0

    def succeeded(self, key: str) -> None:
        """The passcode was right: forget this client's attempts."""
        with self._lock:
            if self._failures.pop(key, None) is not None:
                self._all = deque(item for item in self._all if item[1] != key)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._all.clear()


login_limiter = LoginLimiter()
