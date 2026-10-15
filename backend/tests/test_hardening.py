"""Security hardening: CSRF origin check, body size limit, no-store caching, the sign-in
limiter, session revocation by passcode change, the embedding key, and deleted memories
leaving the database file."""

import pytest

from app import auth, db, memory
from app.config import load_settings, reset_settings
from conftest import PASSCODE


@pytest.fixture
def web_client(anon_client):
    """Signed in with the httpOnly cookie, like the web build."""
    assert anon_client.post("/auth/login", json={"passcode": PASSCODE}).status_code == 200
    return anon_client


# CSRF ------------------------------------------------------------------------------------

def test_cross_site_cookie_write_is_blocked(web_client):
    resp = web_client.post("/memories", json={"content": "Planted by evil.example"}, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert web_client.get("/memories").json() == []


def test_cross_site_write_without_origin_is_blocked_by_fetch_metadata(web_client):
    resp = web_client.post("/memories", json={"content": "Planted"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert resp.status_code == 403


def test_null_origin_is_blocked(web_client):
    assert web_client.delete("/conversations/1", headers={"Origin": "null"}).status_code == 403


@pytest.mark.parametrize("origin", ["http://testserver", "http://localhost:8081"])
def test_same_origin_and_allowed_origins_can_write(web_client, origin):
    resp = web_client.post("/memories", json={"content": "Likes green tea"}, headers={"Origin": origin})
    assert resp.status_code == 201


def test_reads_and_bearer_clients_are_not_origin_checked(client):
    assert client.get("/memories", headers={"Origin": "https://evil.example"}).status_code == 200
    resp = client.post("/memories", json={"content": "Likes rain"}, headers={"Origin": "https://elsewhere.example"})
    assert resp.status_code == 201  # a bearer token can't be sent cross-site without CORS allowing it


# Request size and caching ------------------------------------------------------------------

def test_oversized_body_is_refused_before_parsing(anon_client):
    resp = anon_client.post("/auth/login", content=b"x" * (300 * 1024), headers={"Content-Type": "application/json"})
    assert resp.status_code == 413


def test_oversized_chunked_body_is_refused(anon_client):
    def chunks():
        for _ in range(40):
            yield b"x" * 8192

    resp = anon_client.post("/auth/login", content=chunks(), headers={"Content-Type": "application/json"})
    assert resp.status_code == 413


def test_api_responses_are_not_cached(client):
    assert client.get("/memories").headers["Cache-Control"] == "no-store"


# Sign-in limiter -------------------------------------------------------------------------

def test_attempts_are_counted_before_the_passcode_is_checked():
    limiter = auth.LoginLimiter(limit=3, window=60)
    # Five requests arriving together: only three get through, whatever their passcodes turn out to be.
    assert [limiter.attempt("1.2.3.4") for _ in range(5)][:3] == [0, 0, 0]
    assert limiter.attempt("1.2.3.4") > 0


def test_a_right_passcode_gives_the_attempts_back():
    limiter = auth.LoginLimiter(limit=2, window=60, global_limit=3)
    limiter.attempt("a")
    limiter.attempt("a")
    limiter.succeeded("a")
    assert limiter.attempt("a") == 0
    assert len(limiter._all) == 1


def test_rotating_addresses_hits_the_global_cap():
    limiter = auth.LoginLimiter(limit=5, window=60, global_limit=10)
    results = [limiter.attempt(f"2001:db8::{i}") for i in range(25)]
    assert results[:10] == [0] * 10 and all(r > 0 for r in results[10:])
    assert len(limiter._failures) == 10  # memory stays bounded by the global cap


# Sessions --------------------------------------------------------------------------------

def test_changing_the_passcode_signs_every_device_out(client, monkeypatch):
    assert client.get("/auth/me").status_code == 200
    monkeypatch.setenv("CUSTODIAN_PASSCODE", "a-brand-new-passcode")
    reset_settings()
    assert client.get("/auth/me").status_code == 401


def test_passcode_check():
    assert auth.passcode_matches(PASSCODE)
    assert not auth.passcode_matches(PASSCODE + "x")
    assert not auth.passcode_matches("")


# Embedding key ---------------------------------------------------------------------------

def test_nebius_key_is_not_sent_to_another_embedding_host(monkeypatch):
    monkeypatch.setenv("MODEL_BACKEND", "nebius")
    monkeypatch.setenv("NEBIUS_API_KEY", "nebius-secret")
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embeddings.other-provider.example/v1")
    assert load_settings().embedding_api_key == ""
    monkeypatch.setenv("EMBEDDING_API_KEY", "other-key")
    assert load_settings().embedding_api_key == "other-key"


def test_nebius_key_is_reused_for_nebius_embeddings(monkeypatch):
    monkeypatch.setenv("MODEL_BACKEND", "nebius")
    monkeypatch.setenv("NEBIUS_API_KEY", "nebius-secret")
    monkeypatch.setenv("EMBEDDING_MODEL", "BAAI/bge-en-icl")
    assert load_settings().embedding_api_key == "nebius-secret"
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")
    assert load_settings().embedding_api_key == "nebius-secret"


# Deleting memories -------------------------------------------------------------------------

def _database_bytes() -> bytes:
    with db.connect() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return db.db_path().read_bytes()


@pytest.mark.parametrize("how", ["delete", "update", "expire"])
def test_deleted_memory_text_leaves_the_database_file(user_id, how):
    for i in range(30):
        memory.write(user_id, f"Ordinary filler fact number {i}", "semantic")
    memory_id = memory.write(user_id, "Private diagnosis is zxqvulpine syndrome", "semantic")
    assert b"zxqvulpine" in _database_bytes()  # the plaintext search copy, as documented

    if how == "delete":
        memory.delete(user_id, memory_id)
    elif how == "update":
        memory.update(user_id, memory_id, content="Nothing to see here")
    else:
        with db.connect() as conn:
            conn.execute("UPDATE memories SET expires_at = datetime('now', '-1 hour') WHERE id = ?", (memory_id,))
        assert memory.purge_expired() == 1

    data = _database_bytes()
    assert b"zxqvulpin" not in data  # neither the text nor its stemmed index token
