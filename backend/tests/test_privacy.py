"""Privacy controls (app/privacy.py): secrets withheld from a model on the internet,
off-the-record chats, retention, export and erase."""

import json

import pytest

from app import conversations, db, embeddings, memory, orchestrator, privacy, sensitive, vectors
from app.config import reset_settings
from app.model_backends import ModelResponse, set_backend
from app.model_backends.base import ModelBackend
from test_semantic_search import FakeEmbedder


class Recorder(ModelBackend):
    """Replies "OK." and keeps everything it was sent, as the model service would see it."""

    name = "recorder"
    model_id = "recorder-1"

    def __init__(self):
        self.sent: list[str] = []

    def generate(self, messages, tools=None):
        self.sent.append(json.dumps(messages, ensure_ascii=False))
        return ModelResponse("OK.", [], self.model_id)


@pytest.fixture
def recorder():
    backend = Recorder()
    set_backend(backend)
    return backend


def use_model_at(monkeypatch, url: str) -> None:
    monkeypatch.setenv("MODEL_BACKEND", "nebius")
    monkeypatch.setenv("NEBIUS_API_KEY", "test-key")
    monkeypatch.setenv("NEBIUS_BASE_URL", url)
    reset_settings()


# Secrets stay on this server ----------------------------------------------------------------

def test_secrets_are_withheld_from_a_model_on_the_internet(user_id, recorder, monkeypatch):
    use_model_at(monkeypatch, "https://api.tokenfactory.nebius.com/v1")
    out = orchestrator.handle(user_id, "the wifi password is sunshine42 and my card is 4111 1111 1111 1111")

    assert "sunshine42" not in recorder.sent[-1] and "4111" not in recorder.sent[-1]
    assert "[secret withheld]" in recorder.sent[-1] and "[card number withheld]" in recorder.sent[-1]
    assert out["used"]["checks"]["secrets_withheld"] == 2
    stored = conversations.get_conversation(user_id, out["conversation_id"])["messages"][0]["content"]
    assert "sunshine42" in stored  # your own copy is kept as typed (encrypted at rest)


def test_a_model_on_this_machine_gets_the_message_as_typed(user_id, recorder, monkeypatch):
    use_model_at(monkeypatch, "http://127.0.0.1:11434/v1")
    out = orchestrator.handle(user_id, "the wifi password is sunshine42")
    assert "sunshine42" in recorder.sent[-1]
    assert out["used"]["checks"]["secrets_withheld"] == 0


def test_withholding_can_be_forced_on_or_off(user_id, recorder, monkeypatch):
    use_model_at(monkeypatch, "http://127.0.0.1:11434/v1")
    privacy.update_settings(user_id, redact_secrets="on")
    orchestrator.handle(user_id, "the wifi password is sunshine42")
    assert "sunshine42" not in recorder.sent[-1]

    use_model_at(monkeypatch, "https://api.tokenfactory.nebius.com/v1")
    privacy.update_settings(user_id, redact_secrets="off")
    orchestrator.handle(user_id, "the wifi password is sunshine42")
    assert "sunshine42" in recorder.sent[-1]


@pytest.mark.parametrize(
    "url, where",
    [
        ("https://api.tokenfactory.nebius.com/v1", "internet"),
        ("http://127.0.0.1:11434/v1", "this device"),
        ("http://localhost:11434/v1", "this device"),
        ("http://[::1]:8080/v1", "this device"),
        ("http://192.168.1.20:11434/v1", "local network"),
        ("http://ollama:11434/v1", "local network"),
        ("http://gpu-box.local/v1", "local network"),
        ("http://8.8.8.8/v1", "internet"),
    ],
)
def test_where_a_service_is(url, where):
    assert privacy.endpoint_location(url) == where


@pytest.mark.parametrize(
    "text, hidden",
    [
        ('my password is "correct horse battery"', "correct horse battery"),
        ("the password for gmail is Tr0ub4dor&3 ok", "Tr0ub4dor&3"),
        ("seed phrase: abandon ability able about", "abandon ability"),
        ("my Aadhaar number is 2345 6789 0123", "2345 6789 0123"),
        ("PAN ABCPE1234K please", "ABCPE1234K"),
        ("api key sk-abcdefghijklmnopqrstuv123", "sk-abcdefghijklmnopqrstuv123"),
    ],
)
def test_redaction(text, hidden):
    redacted, count = sensitive.redact(text)
    assert hidden not in redacted and count >= 1


def test_redacting_a_tool_result_keeps_it_valid_json():
    result = json.dumps({"notes": [{"title": "Wifi", "body": 'router password is "sunshine 42", pin 1234'}]})
    redacted, count = sensitive.redact(result)
    assert count == 2 and "sunshine" not in redacted and "1234" not in redacted
    assert json.loads(redacted)["notes"][0]["title"] == "Wifi"


# Off the record -------------------------------------------------------------------------------

def test_an_off_the_record_chat_is_never_remembered_or_recalled(user_id, client):
    first = client.post("/chat", json={"message": "Remember that I parked on level 3 near the lifts", "private": True}).json()
    assert first["private"] is True
    assert first["used"]["remembered"] == [] and memory.list_memories(user_id) == []
    again = client.post("/chat", json={"message": "my locker is number 52", "conversation_id": first["conversation_id"]}).json()
    assert again["private"] is True  # a chat keeps the setting it started with

    later = client.post("/chat", json={"message": "where did I park? which level and which locker?"}).json()
    assert later["used"]["earlier"] == []
    assert client.get(f"/conversations/{first['conversation_id']}").json()["private"] is True
    assert client.get("/conversations").json()[-1]["private"] is True


def test_off_the_record_messages_are_never_embedded(user_id):
    embeddings.set_embedder(FakeEmbedder())
    out = orchestrator.handle(user_id, "I parked the car on level 3", private=True)
    vectors.index_missing()
    with db.connect() as conn:
        stored = conn.execute(
            "SELECT COUNT(*) FROM message_vectors v JOIN messages m ON m.id = v.message_id WHERE m.conversation_id = ?",
            (out["conversation_id"],),
        ).fetchone()[0]
    assert stored == 0


def _age(conversation_id: int, hours: int) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE messages SET created_at = datetime('now', ?) WHERE conversation_id = ?", (f"-{hours} hours", conversation_id)
        )
        conn.execute("UPDATE conversations SET started_at = datetime('now', ?) WHERE id = ?", (f"-{hours} hours", conversation_id))


def test_off_the_record_chats_are_deleted_a_day_later(user_id):
    private = orchestrator.handle(user_id, "just thinking out loud", private=True)
    kept = orchestrator.handle(user_id, "an ordinary chat")
    _age(private["conversation_id"], 25)
    _age(kept["conversation_id"], 25)
    assert privacy.purge_old_chats() == 1
    assert conversations.get_conversation(user_id, private["conversation_id"]) is None
    assert conversations.get_conversation(user_id, kept["conversation_id"]) is not None


# Retention ------------------------------------------------------------------------------------

def test_retention_deletes_old_chats_but_keeps_memories(user_id, client):
    out = orchestrator.handle(user_id, "Remember that I prefer aisle seats")
    saved = out["used"]["remembered"][0]["id"]
    recent = orchestrator.handle(user_id, "a newer chat")
    _age(out["conversation_id"], 40 * 24)

    summary = client.patch("/privacy/settings", json={"retention_days": 30}).json()
    assert summary["retention_days"] == 30
    assert conversations.get_conversation(user_id, out["conversation_id"]) is None  # applied at once
    assert conversations.get_conversation(user_id, recent["conversation_id"]) is not None
    assert memory.get(user_id, saved)["source_message_id"] is None  # the memory stays; its link is cleared

    assert client.patch("/privacy/settings", json={"retention_days": None}).json()["retention_days"] is None
    assert client.patch("/privacy/settings", json={"redact_secrets": "sometimes"}).status_code == 422


# Export and erase -----------------------------------------------------------------------------

def test_export_has_everything_decrypted(user_id, client, grant):
    grant("notes", "write")
    grant("calendar", "write")
    memory.write(user_id, "Prefers green tea.", "preference")
    client.post("/chat", json={"message": "Take a note: call the plumber about the kitchen tap"})
    client.post("/chat", json={"message": "Add a dentist appointment tomorrow at 10am"})

    resp = client.get("/privacy/export")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith('attachment; filename="custodian-export-')
    data = resp.json()
    assert data["format"] == privacy.EXPORT_FORMAT
    assert [m["content"] for m in data["memories"]] == ["Prefers green tea."]
    said = [m["content"] for c in data["conversations"] for m in c["messages"] if m["role"] == "user"]
    assert "Take a note: call the plumber about the kitchen tap" in said
    assert [n["body"] for n in data["notes"]] == ["call the plumber about the kitchen tap"]
    assert len(data["calendar_events"]) == 1
    assert {c["function"] for c in data["tool_calls"]} == {"create_note", "create_event"}
    assert all(isinstance(c["args"], dict) for c in data["tool_calls"])


def test_erase_removes_everything_and_leaves_no_trace(user_id, client, grant):
    grant("notes", "write")
    memory.write(user_id, "The zxqvulpine plan is secret-ish.", "semantic")
    client.post("/chat", json={"message": "Take a note: zxqvulpine shopping list"})
    client.post("/chat", json={"message": "my zxqvulpine collection is in the attic"})

    assert client.post("/privacy/erase", json={"confirm": "yes"}).status_code == 400
    erased = client.post("/privacy/erase", json={"confirm": "ERASE"}).json()["erased"]
    assert erased["memories"] == 1 and erased["conversations"] == 2 and erased["notes"] == 1

    assert client.get("/memories").json() == [] and client.get("/conversations").json() == []
    assert client.get("/privacy").json()["stored"]["notes"] == 0
    later = client.post("/chat", json={"message": "where is my zxqvulpine collection?"}).json()
    assert later["used"]["earlier"] == []
    # The plaintext search copy and the note are gone from the files, not just hidden.
    with db.connect() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert b"zxqvulpin" not in db.db_path().read_bytes()
    notes_db = privacy._tool_db("notes")
    assert b"zxqvulpin" not in notes_db.read_bytes()


def test_privacy_summary(client):
    summary = client.get("/privacy").json()
    assert summary["model"]["location"] == "offline"
    assert summary["embeddings"] is None
    assert summary["redaction"] == {"mode": "auto", "active": False}
    assert summary["private_chat_hours"] == privacy.PRIVATE_CHAT_HOURS
    assert set(summary["stored"]) >= {"conversations", "messages", "memories", "notes", "calendar_events"}


def test_every_outside_service_is_listed(user_id, client, monkeypatch):
    assert client.get("/privacy").json()["other_services"] == []
    monkeypatch.setenv("WHISPER_URL", "http://127.0.0.1:9000/v1")
    monkeypatch.setenv("VISION_MODEL", "llava")
    monkeypatch.setenv("VISION_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("NTFY_URL", "https://ntfy.sh/secret-topic")
    monkeypatch.setenv("LARGE_MODEL_ID", "nvidia/nemotron-3-super-120b-a12b")
    services = {s["purpose"]: s for s in client.get("/privacy").json()["other_services"]}
    assert services["Voice messages"]["location"] == "this device"
    assert services["Photos"]["location"] == "this device"
    assert services["Phone notifications"]["location"] == "internet"
    assert services["Phone notifications"]["sends"] == 'only "Reminder from Custodian"'
    assert services["Think harder"]["location"] == "internet"
    assert "Web search" not in services and "PC control" not in services  # both off until switched on

    from app import db, desktop, permissions

    with db.connect() as conn:
        web = conn.execute("SELECT id FROM tools WHERE name = 'web'").fetchone()["id"]
    permissions.grant(user_id, web, "read")
    desktop.touch(user_id, "Custodian desktop on HOME-PC")
    try:
        services = {s["purpose"]: s for s in client.get("/privacy").json()["other_services"]}
    finally:
        desktop.reset()
    assert services["Web search"]["location"] == "this device" and services["Reading a web page"]["location"] == "internet"
    assert services["PC control"]["name"] == "Custodian desktop on HOME-PC"
