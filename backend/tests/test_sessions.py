"""Sign-ins are server-side sessions: signing out (here or from another device) makes a token
stop working at once, which a plain signed token can't do."""

import pytest
from fastapi.testclient import TestClient

from app import auth, db
from app.main import create_app
from conftest import PASSCODE

PHONE_UA = "okhttp/4.12.0"
CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"


@pytest.fixture
def app_client(user_id):
    with TestClient(create_app()) as client:
        yield client


def sign_in(client, user_agent=PHONE_UA) -> dict:
    resp = client.post("/auth/login", json={"passcode": PASSCODE, "client": "native"}, headers={"User-Agent": user_agent})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def test_signing_out_kills_the_token_everywhere(app_client):
    phone = sign_in(app_client)
    stolen = dict(phone)  # a copy taken off the phone before it signed out
    assert app_client.get("/auth/me", headers=phone).status_code == 200
    assert app_client.post("/auth/logout", headers=phone).status_code == 204
    assert app_client.get("/auth/me", headers=stolen).status_code == 401


def test_devices_are_listed_and_can_be_signed_out(app_client):
    laptop = sign_in(app_client, CHROME_UA)
    phone = sign_in(app_client, PHONE_UA)

    listed = app_client.get("/auth/sessions", headers=laptop).json()
    assert {s["device"] for s in listed} == {"Custodian app on Windows", "Custodian app on Android"}
    assert [s["current"] for s in listed if s["device"] == "Custodian app on Windows"] == [True]
    phone_id = next(s["id"] for s in listed if s["device"] == "Custodian app on Android")

    assert app_client.delete(f"/auth/sessions/{phone_id}", headers=laptop).status_code == 204
    assert app_client.get("/auth/me", headers=phone).status_code == 401
    assert app_client.get("/auth/me", headers=laptop).status_code == 200
    assert app_client.delete(f"/auth/sessions/{phone_id}", headers=laptop).status_code == 404


def test_sign_out_everywhere_else_keeps_this_device(app_client):
    here = sign_in(app_client, CHROME_UA)
    others = [sign_in(app_client) for _ in range(3)]
    assert app_client.delete("/auth/sessions", headers=here).json() == {"signed_out": 3}
    assert all(app_client.get("/auth/me", headers=h).status_code == 401 for h in others)
    assert [s["current"] for s in app_client.get("/auth/sessions", headers=here).json()] == [True]


def test_a_web_sign_out_ends_the_cookie_session(app_client):
    resp = app_client.post("/auth/login", json={"passcode": PASSCODE}, headers={"User-Agent": CHROME_UA})
    cookie = resp.cookies.get(auth.COOKIE_NAME)
    assert app_client.post("/auth/logout").status_code == 204
    app_client.cookies.set(auth.COOKIE_NAME, cookie)  # replay the old cookie
    assert app_client.get("/auth/me").status_code == 401


def test_another_users_session_cannot_be_revoked(app_client, user_id):
    mine = sign_in(app_client)
    with db.connect() as conn:
        other = conn.execute("INSERT INTO users (email) VALUES ('other@example.com')").lastrowid
    theirs = auth.issue_token(other)
    their_id = auth.session_for(theirs)[1]
    assert app_client.delete(f"/auth/sessions/{their_id}", headers=mine).status_code == 404
    assert auth.verify_token(theirs) == other


def test_tokens_without_a_session_are_refused(user_id):
    legacy = auth._signer().sign(str(user_id)).decode()  # the old format: just the user id
    assert auth.verify_token(legacy) is None
    unknown = auth._signer().sign(f"{user_id}:never-issued").decode()
    assert auth.verify_token(unknown) is None


def test_expired_sessions_are_purged(user_id):
    token = auth.issue_token(user_id)
    with db.connect() as conn:
        conn.execute("UPDATE sessions SET created_at = datetime('now', '-400 days')")
    assert auth.purge_expired_sessions() == 1
    assert auth.verify_token(token) is None


@pytest.mark.parametrize(
    "user_agent, client, label",
    [
        (CHROME_UA, "web", "Chrome on Windows"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15", "web", "Safari on macOS"),
        ("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/141.0 Safari/537.36 Edg/141.0", "web", "Edge on Windows"),
        ("Custodian/1 CFNetwork/1568 Darwin/24.0.0", "native", "Custodian app on iOS"),
        ("", "native", "Custodian app"),
        ("", "web", "A browser"),
    ],
)
def test_devices_get_readable_names(user_agent, client, label):
    assert auth.describe_device(user_agent, client) == label
