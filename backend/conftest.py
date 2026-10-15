"""Shared test fixtures: every test gets its own data directory, database and settings."""

from __future__ import annotations

import os
import tempfile

import pytest
from cryptography.fernet import Fernet

PASSCODE = "test-passcode-123"
TEST_KEY = Fernet.generate_key().decode()

# Set before anything imports app.main (which builds an app at import time), so no test
# run ever touches backend/.env or backend/data.
_BASE_ENV = {
    "CUSTODIAN_SKIP_DOTENV": "1",
    "CUSTODIAN_ENV": "development",
    "DATA_DIR": tempfile.mkdtemp(prefix="custodian-test-"),
    "CUSTODIAN_PASSCODE": PASSCODE,
    "APP_SECRET_KEY": TEST_KEY,
    "SESSION_SECRET": "s" * 48,
    "CUSTODIAN_TIMEZONE": "Asia/Kolkata",
    "MODEL_BACKEND": "stub",
    "SCHEDULER_ENABLED": "false",
    "LOG_LEVEL": "WARNING",
}
_CLEARED = (
    "DATABASE_PATH", "NEBIUS_API_KEY", "NEBIUS_MODEL_ID", "NEBIUS_BASE_URL", "MODEL_EXTRA_BODY", "CORS_ORIGINS",
    "COOKIE_SECURE", "COOKIE_SAMESITE", "WEB_DIST_DIR", "TOOL_TIMEOUT_SECONDS", "CUSTODIAN_USER_EMAIL",
    "SESSION_MAX_AGE_DAYS", "EMBEDDING_MODEL", "EMBEDDING_BASE_URL", "EMBEDDING_API_KEY", "EMBEDDING_MIN_SIMILARITY",
    "CUSTODIAN_PLAN", "CUSTODIAN_LICENSE_KEY", "MODEL_PRICE_INPUT_PER_M", "MODEL_PRICE_OUTPUT_PER_M",
)
os.environ.update(_BASE_ENV)
for _name in _CLEARED:
    os.environ.pop(_name, None)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    from app import embeddings, plans, vectors
    from app.auth import login_limiter
    from app.config import reset_settings
    from app.model_backends import set_backend

    for name, value in _BASE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    for name in _CLEARED:
        monkeypatch.delenv(name, raising=False)
    reset_settings()
    plans.reset()
    set_backend(None)
    embeddings.set_embedder(None)
    vectors.reset()
    login_limiter.reset()
    yield
    from app import scheduler

    scheduler.shutdown()
    set_backend(None)
    embeddings.set_embedder(None)
    vectors.reset()
    reset_settings()
    plans.reset()


@pytest.fixture
def user_id() -> int:
    """A fresh database with the owner account and synced skills."""
    from app import auth, db, skills

    db.init_db()
    uid = auth.ensure_owner()
    skills.sync_from_files()
    return uid


@pytest.fixture
def client():
    """A signed-in API client (native-style bearer token)."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        resp = test_client.post("/auth/login", json={"passcode": PASSCODE, "client": "native"})
        assert resp.status_code == 200, resp.text
        test_client.headers["Authorization"] = f"Bearer {resp.json()['token']}"
        yield test_client


@pytest.fixture
def anon_client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def tool_ids(user_id) -> dict[str, int]:
    """{'calendar': 1, 'notes': 2} for the seeded tools."""
    from app import db

    with db.connect() as conn:
        return {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM tools").fetchall()}


@pytest.fixture
def grant(user_id, tool_ids):
    """grant('calendar', 'read') switches a permission on for the owner."""
    from app import permissions

    def _grant(tool: str, scope: str, on: bool = True) -> None:
        permissions.set_granted(user_id, tool_ids[tool], scope, on)

    return _grant
