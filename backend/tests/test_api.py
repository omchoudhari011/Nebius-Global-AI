import pytest

from app.model_backends import ModelError, set_backend
from app.model_backends.base import ModelBackend
from conftest import PASSCODE

PROTECTED = [
    ("get", "/auth/me"),
    ("post", "/chat"),
    ("get", "/conversations"),
    ("get", "/conversations/1"),
    ("delete", "/conversations/1"),
    ("get", "/permissions"),
    ("get", "/permissions/events"),
    ("post", "/permissions/1"),
    ("get", "/memories"),
    ("post", "/memories"),
    ("patch", "/memories/1"),
    ("delete", "/memories/1"),
    ("get", "/tool-calls"),
    ("get", "/skills"),
    ("get", "/tasks"),
    ("post", "/tasks"),
    ("patch", "/tasks/1"),
    ("delete", "/tasks/1"),
    ("post", "/tasks/1/run"),
    ("get", "/tasks/1/runs"),
    ("get", "/privacy"),
    ("patch", "/privacy/settings"),
    ("get", "/privacy/export"),
    ("post", "/privacy/erase"),
    ("get", "/billing"),
    ("post", "/billing/license"),
    ("delete", "/billing/license"),
]


def test_health_is_public(anon_client):
    assert anon_client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_routes_require_a_session(anon_client, method, path):
    assert getattr(anon_client, method)(path).status_code == 401


def test_forged_token_is_rejected(anon_client):
    resp = anon_client.get("/auth/me", headers={"Authorization": "Bearer 1.forged.signature"})
    assert resp.status_code == 401


def test_web_login_uses_an_httponly_cookie(anon_client):
    resp = anon_client.post("/auth/login", json={"passcode": PASSCODE})
    assert resp.status_code == 200
    assert "token" not in resp.json()
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    me = anon_client.get("/auth/me")
    assert me.status_code == 200 and me.json()["server"]["model_backend"] == "stub"
    assert anon_client.post("/auth/logout").status_code == 204
    assert anon_client.get("/auth/me").status_code == 401


def test_login_is_rate_limited(anon_client):
    for _ in range(5):
        assert anon_client.post("/auth/login", json={"passcode": "wrong"}).status_code == 401
    blocked = anon_client.post("/auth/login", json={"passcode": PASSCODE})
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0


def test_security_headers(client):
    resp = client.get("/permissions")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Request-ID"]


def test_chat_round_trip_and_history(client):
    resp = client.post("/chat", json={"message": "Remember that I take the 8:10 train"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"reply", "used", "conversation_id", "message_id", "private"}
    assert set(body["used"]) == {"memories", "earlier", "tools", "skill", "remembered", "checks"}
    assert body["used"]["checks"]["status"] == "passed"

    conv = client.get(f"/conversations/{body['conversation_id']}").json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["messages"][1]["used"]["remembered"][0]["content"] == "I take the 8:10 train"

    listed = client.get("/conversations", params={"channel": "chat"}).json()
    assert listed[0]["id"] == body["conversation_id"] and listed[0]["preview"]

    assert client.delete(f"/conversations/{body['conversation_id']}").status_code == 204
    assert client.get(f"/conversations/{body['conversation_id']}").status_code == 404


@pytest.mark.parametrize("payload", [{"message": ""}, {"message": "   "}, {"message": "x" * 8001}, {}])
def test_chat_validates_input(client, payload):
    assert client.post("/chat", json=payload).status_code == 422


def test_chat_into_missing_conversation(client):
    assert client.post("/chat", json={"message": "hi", "conversation_id": 999}).status_code == 404


def test_permission_switches(client):
    rows = client.get("/permissions").json()
    cal = next(r for r in rows if r["tool_name"] == "calendar" and r["scope"] == "read")
    on = client.post(f"/permissions/{cal['tool_id']}", json={"scope": "read", "granted": True})
    assert on.status_code == 200 and on.json()["granted"] is True
    assert client.post(f"/permissions/{cal['tool_id']}", json={"scope": "nope", "granted": True}).status_code == 404
    assert client.post("/permissions/999", json={"scope": "read", "granted": True}).status_code == 404
    events = client.get("/permissions/events").json()
    assert events[0]["tool_name"] == "calendar" and events[0]["granted"] is True


def test_memories_crud(client):
    created = client.post("/memories", json={"content": "Cat is called Miso", "type": "semantic"})
    assert created.status_code == 201
    memory_id = created.json()["id"]
    assert client.patch(f"/memories/{memory_id}", json={"type": "episodic"}).json()["type"] == "episodic"
    assert [m["id"] for m in client.get("/memories").json()] == [memory_id]
    assert client.delete(f"/memories/{memory_id}").status_code == 204
    assert client.delete(f"/memories/{memory_id}").status_code == 404
    assert client.post("/memories", json={"content": "x", "type": "gossip"}).status_code == 422


def test_tasks_lifecycle(client):
    skills = {s["name"] for s in client.get("/skills").json()}
    assert "daily-summary" in skills

    created = client.post(
        "/tasks", json={"name": "morning-brief", "skill": "daily-summary", "cron_schedule": "30 7 * * 1-5"}
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["timezone"] == "Asia/Kolkata" and task["next_run_at"] and task["status"] == "active"

    assert client.post("/tasks", json={"name": "morning-brief", "skill": "general", "cron_schedule": "0 9 * * *"}).status_code == 400
    assert client.post("/tasks", json={"name": "x", "skill": "general", "cron_schedule": "61 * * * *"}).status_code == 400
    assert client.post("/tasks", json={"name": "x", "skill": "nope", "cron_schedule": "0 9 * * *"}).status_code == 400
    assert client.post("/tasks", json={"name": "Bad Name", "skill": "general", "cron_schedule": "0 9 * * *"}).status_code == 400

    paused = client.patch(f"/tasks/{task['id']}", json={"status": "paused"}).json()
    assert paused["status"] == "paused" and paused["next_run_at"] is None

    run = client.post(f"/tasks/{task['id']}/run")
    assert run.status_code == 200 and run.json()["status"] == "succeeded"
    output = client.get(f"/conversations/{run.json()['conversation_id']}").json()
    assert output["channel"] == "task" and output["task"]["name"] == "morning-brief"
    assert output["messages"][-1]["role"] == "assistant"
    assert client.get(f"/tasks/{task['id']}/runs").json()[0]["id"] == run.json()["id"]
    assert client.get("/tasks").json()[0]["last_run"]["status"] == "succeeded"

    assert client.delete(f"/tasks/{task['id']}").status_code == 204
    assert client.get(f"/tasks/{task['id']}").status_code == 404


def test_tool_call_audit_log(client):
    client.post("/chat", json={"message": "What's on my calendar today?"})
    log = client.get("/tool-calls").json()
    assert log[0]["function"] == "list_events" and log[0]["status"] == "denied"
    assert client.get("/tool-calls", params={"status": "succeeded"}).json() == []


def test_model_errors_become_502(client):
    class Down(ModelBackend):
        name, model_id = "down", "down"

        def generate(self, messages, tools=None):
            raise ModelError("Nebius returned 503: overloaded")

    set_backend(Down())
    resp = client.post("/chat", json={"message": "hello"})
    assert resp.status_code == 502 and "overloaded" in resp.json()["detail"]


def test_web_app_is_served_with_spa_fallback(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import reset_settings
    from app.main import create_app

    web = tmp_path / "web"
    (web / "_expo").mkdir(parents=True)
    (web / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (web / "_expo" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("WEB_DIST_DIR", str(web))
    reset_settings()
    with TestClient(create_app()) as c:
        index = c.get("/tools-data")
        assert index.status_code == 200 and "app" in index.text
        assert "default-src 'self'" in index.headers["Content-Security-Policy"]
        assert "immutable" in c.get("/_expo/app.js").headers["Cache-Control"]
        assert c.get("/missing.js").status_code == 404
        assert c.get("/../conftest.py").status_code == 404
        assert c.get("/permissions").status_code == 401  # API routes still win
