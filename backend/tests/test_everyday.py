"""Day-to-day features: reminders and notifications, files, voice, feedback that can become
training data, the Google connection and its sandboxed tools, and the larger model."""

import base64
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app import db, documents, feedback, google_account, notifications, orchestrator, privacy, voice
from app.config import get_settings
from app.model_backends import ModelResponse, ToolCallRequest, set_backend, set_large_backend
from app.model_backends.base import ModelBackend
from app.tools import reminders_tool, sandbox
from app.tools.base import ToolFailure, ToolRequest


class Script(ModelBackend):
    name = "script"

    def __init__(self, *responses: ModelResponse, model_id: str = "script-1"):
        self.model_id = model_id
        self.responses = list(responses)
        self.requests: list[list[dict]] = []

    def generate(self, messages, tools=None):
        self.requests.append(list(messages))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def say(text: str) -> ModelResponse:
    return ModelResponse(text, [], "script-1")


def call(name: str, args: dict) -> ModelResponse:
    return ModelResponse("", [ToolCallRequest(f"c-{name}", name, args)], "script-1")


def later(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat(timespec="minutes")


# Reminders and notifications -----------------------------------------------------------------

def test_a_reminder_is_set_delivered_and_marked_done(user_id, grant, client):
    grant("reminders", "write")
    set_backend(Script(call("reminders_create_reminder", {"text": "Call mum", "at": later(minutes=30)}),
                       say("Done - I'll remind you to call mum in 30 minutes.")))
    out = orchestrator.handle(user_id, "remind me to call mum in 30 minutes")
    assert out["used"]["checks"]["status"] == "passed"
    upcoming = client.get("/reminders").json()
    assert [r["text"] for r in upcoming] == ["Call mum"]

    assert notifications.deliver_due() == 0  # not yet
    assert notifications.deliver_due(datetime.now(timezone.utc) + timedelta(minutes=31)) == 1
    inbox = client.get("/notifications").json()
    assert inbox[0]["kind"] == "reminder" and inbox[0]["title"] == "Call mum" and inbox[0]["read_at"] is None
    assert client.get("/reminders").json() == []  # one-off: done
    assert client.get("/features").json()["unread"] == 1
    assert client.post("/notifications/read-all").json() == {"marked": 1}
    assert client.get("/notifications?unread=true").json() == []


def test_notification_text_is_encrypted_at_rest(user_id):
    notifications.add(user_id, "reminder", "Pick up the biopsy results", push=False)
    with db.connect() as conn:
        assert b"biopsy" not in bytes(conn.execute("SELECT title FROM notifications").fetchone()["title"])


def test_a_repeating_reminder_moves_to_its_next_time(user_id):
    ctx = {"user_id": user_id, "data_dir": str(get_settings().tool_data_dir / "reminders"), "timezone": "Asia/Kolkata"}
    (get_settings().tool_data_dir / "reminders").mkdir(parents=True, exist_ok=True)
    first = datetime(2030, 1, 4, 9, 0)  # a Friday, 09:00 local
    reminders_tool.create_reminder(ctx, "Stand-up", first.isoformat(), "weekdays")
    notifications.deliver_due(datetime(2030, 1, 4, 4, 0, tzinfo=timezone.utc))  # 09:30 in Kolkata
    (left,) = notifications.upcoming_reminders(user_id)
    assert left["due"] == "2030-01-07T09:00+05:30"  # skips the weekend, keeps 09:00


def test_a_reminder_in_the_past_is_refused(user_id):
    ctx = {"user_id": user_id, "data_dir": str(get_settings().tool_data_dir / "reminders"), "timezone": "UTC"}
    (get_settings().tool_data_dir / "reminders").mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="in the past"):
        reminders_tool.create_reminder(ctx, "Too late", "2001-01-01T09:00")


def test_phone_push_hides_the_text_unless_asked(user_id, monkeypatch):
    sent = []
    monkeypatch.setattr(notifications, "_transport", httpx.MockTransport(lambda r: sent.append(r.content) or httpx.Response(200)))
    monkeypatch.setenv("NTFY_URL", "https://ntfy.example/secret-topic")
    notifications.add(user_id, "reminder", "Take the blood pressure pill")
    monkeypatch.setenv("NTFY_SHOW_TEXT", "true")
    notifications.add(user_id, "reminder", "Take the blood pressure pill")
    assert sent == [b"Reminder from Custodian", b"Take the blood pressure pill"]


def test_phone_pushes_are_set_up_from_the_app_on_a_topic_of_your_own(user_id, client, monkeypatch):
    sent = []
    monkeypatch.setattr(notifications, "_transport", httpx.MockTransport(
        lambda r: sent.append((str(r.url), r.content)) or httpx.Response(200)))
    monkeypatch.delenv("NTFY_URL", raising=False)
    assert client.get("/push").json() == {"on": False, "server": "https://ntfy.sh"}
    assert client.post("/push/test").status_code == 400
    info = client.post("/push").json()
    topic = info["topic"]
    assert info["on"] and topic.startswith("custodian-") and len(topic) >= 30  # long enough that nobody guesses it
    assert info["subscribe_url"] == f"https://ntfy.sh/{topic}" and info["app_link"] == f"ntfy://ntfy.sh/{topic}"
    assert client.post("/push").json()["topic"] == topic  # set up again: the same one
    assert client.get("/features").json()["phone_push"] is True

    notifications.add(user_id, "reminder", "Take the blood pressure pill")
    client.patch("/settings/prefs", json={"push_text": True})
    assert client.post("/push/test").status_code == 204
    assert sent == [(f"https://ntfy.sh/{topic}", b"Reminder from Custodian"),
                    (f"https://ntfy.sh/{topic}", b"Custodian can reach your phone\nThis is a test notification.")]
    assert client.delete("/push").status_code == 204
    assert client.get("/push").json()["on"] is False
    notifications.add(user_id, "reminder", "Not sent anywhere")
    assert len(sent) == 2


def test_a_finished_task_lands_in_the_inbox(user_id):
    from app import scheduler, tasks

    set_backend(Script(say("Nothing on your calendar today.")))
    task = tasks.create_task(user_id, "daily-summary", "daily-summary", "0 9 * * *")
    scheduler.run_task(task["id"])
    (note,) = notifications.list_for(user_id)
    assert note["kind"] == "task" and note["title"] == "Daily summary is ready"
    assert note["ref"].startswith("conversation:")


def test_promising_a_reminder_without_setting_one_is_flagged(user_id):
    set_backend(Script(say("Sure, I'll remind you at 6.")))
    out = orchestrator.handle(user_id, "remind me at 6 to stretch")
    assert "No reminder was set" in out["reply"]


# Files ---------------------------------------------------------------------------------------

def _pdf(text: str) -> bytes:
    """A minimal one-page PDF with `text` in its text layer."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_an_uploaded_pdf_can_be_searched_and_read_through_the_gate(user_id, grant, client):
    resp = client.post("/documents", json={"name": "lease.pdf", "content_type": "application/pdf",
                                           "data": b64(_pdf("Pets are allowed with a deposit of 500 euros."))})
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert doc["kind"] == "pdf" and "deposit of 500" in doc["preview"]

    blocked = sandbox.execute(user_id, "documents_search_documents", {"query": "pets"}, message_id=_message(user_id), allowed=None)
    assert blocked.status == "denied" and "Files · read" in blocked.error
    grant("documents", "read")
    found = sandbox.execute(user_id, "documents_search_documents", {"query": "pets deposit"}, message_id=_message(user_id), allowed=None)
    assert found.status == "succeeded" and found.result["documents"][0]["id"] == doc["id"]
    read = sandbox.execute(user_id, "documents_read_document", {"id": doc["id"]}, message_id=_message(user_id), allowed=None)
    assert "500 euros" in read.result["text"]

    assert client.delete(f"/documents/{doc['id']}").status_code == 204
    assert client.get("/documents").json() == []


def _message(user_id: int) -> int:
    from app.crypto import encrypt

    with db.connect() as conn:
        conv = conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (user_id,)).lastrowid
        return conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', ?)",
                            (conv, encrypt(""))).lastrowid


def test_text_files_and_refusals(user_id, client):
    ok = client.post("/documents", json={"name": "list.txt", "data": b64(b"eggs, milk, bread")})
    assert ok.status_code == 201 and ok.json()["kind"] == "text"
    zip_ = client.post("/documents", json={"name": "a.zip", "content_type": "application/zip", "data": b64(b"PK\x03\x04")})
    assert zip_.status_code == 400 and "PDFs, text and web pages, and photos" in zip_.json()["detail"]
    photo = client.post("/documents", json={"name": "a.png", "content_type": "image/png", "data": b64(b"\x89PNG\r\n\x1a\n" + b"x" * 20)})
    assert photo.status_code == 400 and "VISION_MODEL" in photo.json()["detail"]
    fake = client.post("/documents", json={"name": "b.png", "content_type": "image/png", "data": b64(b"not really a picture")})
    assert fake.status_code == 400 and "isn't a PNG" in fake.json()["detail"]
    named_pdf = client.post("/documents", json={"name": "notes.pdf", "content_type": "application/pdf", "data": b64(b"just plain text")})
    assert named_pdf.status_code == 201 and named_pdf.json()["kind"] == "text"  # judged by content, not by name
    secret = client.post("/documents", json={"name": "wifi.txt", "data": b64(b"router password: sunshine42")})
    assert "password" in secret.json()["warning"]


def test_a_photo_is_described_by_the_vision_model(user_id, monkeypatch):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "A receipt. Text in the image: Total 42.50"}}]})

    monkeypatch.setenv("VISION_MODEL", "llava")
    monkeypatch.setenv("VISION_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(documents, "_transport", httpx.MockTransport(handler))
    doc = documents.add(user_id, "receipt.jpg", "image/jpeg", b64(b"\xff\xd8\xff fake jpeg"))
    assert doc["kind"] == "image" and "Total 42.50" in doc["preview"]
    assert seen["model"] == "llava" and seen["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_uploads_may_be_larger_than_other_requests(client):
    big = b64(b"x" * 600_000)  # over the 256 KB limit for ordinary requests
    assert client.post("/memories", json={"content": big, "type": "semantic"}).status_code == 413
    assert client.post("/documents", json={"name": "big.txt", "data": big}).status_code == 201


def test_erase_removes_files_reminders_and_notifications(user_id, grant, client):
    client.post("/documents", json={"name": "notes.txt", "data": b64(b"my locker is number 12")})
    notifications.add(user_id, "system", "hello", push=False)
    counts = privacy.erase(user_id)
    assert counts["documents"] == 1 and counts["notifications"] == 1
    assert documents.list_for(user_id) == [] and notifications.list_for(user_id) == []
    data = (get_settings().tool_data_dir / "documents" / "documents.db").read_bytes()
    assert b"locker is number 12" not in data


# Voice ---------------------------------------------------------------------------------------

def test_voice_is_transcribed_by_the_local_server(client, monkeypatch):
    assert client.post("/voice/transcribe", content=b"RIFF", headers={"Content-Type": "audio/wav"}).status_code == 400
    seen = {}

    def handler(request):
        seen["url"], seen["type"] = str(request.url), request.headers["content-type"]
        return httpx.Response(200, json={"text": " What's on my calendar today? "})

    monkeypatch.setenv("WHISPER_URL", "http://127.0.0.1:9000/v1")
    monkeypatch.setattr(voice, "_transport", httpx.MockTransport(handler))
    resp = client.post("/voice/transcribe", content=b"OggS fake audio", headers={"Content-Type": "audio/webm;codecs=opus"})
    assert resp.json() == {"text": "What's on my calendar today?"}
    assert seen["url"] == "http://127.0.0.1:9000/v1/audio/transcriptions" and seen["type"].startswith("multipart/form-data")


class FakeWhisper:
    """faster_whisper.WhisperModel's surface: transcribe() gives (segments, info)."""
    made: list = []

    def __init__(self, name, device, compute_type, download_root):
        self.args = (name, device, compute_type)
        self.calls = []
        FakeWhisper.made.append(self)

    def transcribe(self, audio, language=None, beam_size=5, vad_filter=False):
        self.calls.append((audio.read(), language, vad_filter))
        segment = type("Segment", (), {"text": " Open Spotify,"})
        return iter([segment, type("Segment", (), {"text": " please. "})]), None


@pytest.fixture
def local_whisper(monkeypatch):
    import sys
    import types

    FakeWhisper.made = []
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeWhisper))
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 1))
    monkeypatch.setenv("WHISPER_URL", "local")
    monkeypatch.setenv("WHISPER_DEVICE", "auto")
    monkeypatch.delenv("WHISPER_LOCAL_MODEL", raising=False)
    engine = voice.LocalWhisper()
    monkeypatch.setattr(voice, "LOCAL", engine)
    yield engine
    if engine._timer is not None:
        engine._timer.cancel()


def test_voice_can_be_heard_in_the_server_itself_on_the_graphics_card(client, local_whisper):
    resp = client.post("/voice/transcribe?language=hi", content=b"OggS fake audio", headers={"Content-Type": "audio/ogg"})
    assert resp.json() == {"text": "Open Spotify, please."}
    (model,) = FakeWhisper.made
    assert model.args == ("large-v3-turbo", "cuda", "int8_float16")  # the large model hears Hindi best
    assert model.calls == [(b"OggS fake audio", "hi", True)]  # silence skipped: no imagined "Thank you."
    client.post("/voice/transcribe", content=b"more", headers={"Content-Type": "audio/ogg"})
    assert len(FakeWhisper.made) == 1  # loaded once
    assert client.get("/features").json()["voice"] is True


def test_the_local_speech_model_is_let_go_when_idle_and_loaded_again(client, local_whisper, monkeypatch):
    monkeypatch.setenv("WHISPER_IDLE_MINUTES", "10")
    client.post("/voice/transcribe", content=b"one", headers={"Content-Type": "audio/ogg"})
    assert local_whisper.loaded() and local_whisper._timer is not None and local_whisper._timer.interval == 600
    local_whisper.unload()  # what the timer does after 10 quiet minutes
    assert not local_whisper.loaded()
    client.post("/voice/transcribe", content=b"two", headers={"Content-Type": "audio/ogg"})
    assert local_whisper.loaded() and len(FakeWhisper.made) == 2


def test_without_a_graphics_card_the_small_model_runs_on_the_processor(local_whisper, monkeypatch):
    import sys
    import types

    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(get_cuda_device_count=lambda: 0))
    assert voice.transcribe(b"audio", "audio/wav") == "Open Spotify, please."
    assert FakeWhisper.made[-1].args == ("small", "cpu", "int8")


def test_hey_jarvis_loads_the_speech_model_while_you_talk(client, local_whisper, monkeypatch):
    from app import warmup

    started = threading.Event()
    monkeypatch.setattr(warmup, "warm", lambda keep=None: None)
    monkeypatch.setattr(local_whisper, "preload", started.set)
    assert client.post("/warm").status_code == 204
    assert started.wait(2)


def test_local_speech_says_what_to_install_when_faster_whisper_is_missing(client, monkeypatch):
    import sys

    monkeypatch.setenv("WHISPER_URL", "local")
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # import fails
    monkeypatch.setattr(voice, "LOCAL", voice.LocalWhisper())
    resp = client.post("/voice/transcribe", content=b"audio", headers={"Content-Type": "audio/ogg"})
    assert resp.status_code == 400 and "requirements-voice.txt" in resp.json()["detail"]


# Feedback ------------------------------------------------------------------------------------

def test_a_rated_reply_becomes_a_training_example_only_when_shared(user_id, client):
    set_backend(Script(say("Paris.")))
    good = orchestrator.handle(user_id, "What's the capital of France?")
    set_backend(Script(say("Lima.")))
    bad = orchestrator.handle(user_id, "What's the capital of Peru?")

    assert client.post(f"/messages/{good['message_id']}/feedback", json={"rating": 1}).status_code == 200
    assert feedback.training_examples(user_id) == []  # not shared yet
    client.post(f"/messages/{good['message_id']}/feedback", json={"rating": 1, "share": True})
    client.post(f"/messages/{bad['message_id']}/feedback", json={"rating": -1, "correction": "Lima is right, but say it in a sentence: The capital of Peru is Lima.", "share": True})

    good_ex, bad_ex = feedback.training_examples(user_id)
    assert good_ex["messages"][0]["role"] == "system" and good_ex["messages"][-2]["content"] == "What's the capital of France?"
    assert good_ex["messages"][-1] == {"role": "assistant", "content": "Paris."}
    assert bad_ex["kind"] == "feedback_corrected" and bad_ex["messages"][-1]["content"].startswith("Lima is right")
    assert good_ex["tools"]  # the tools the model was offered

    thread = client.get(f"/conversations/{good['conversation_id']}").json()
    assert thread["messages"][-1]["feedback"] == {"rating": 1, "correction": None, "share": True}


def test_feedback_training_skips_repairs_secrets_and_off_the_record(user_id):
    set_backend(Script(call("calendar_create_event", {"title": "Lunch", "start": "2030-01-01T13:00"}),
                       say("I've added lunch to your calendar."),  # false: rewritten
                       say("I couldn't add it: Calendar · write is switched off.")))
    fixed = orchestrator.handle(user_id, "add lunch on 1 Jan 2030 at 1pm")
    feedback.save(user_id, fixed["message_id"], 1, share=True)
    (example,) = feedback.training_examples(user_id)
    assert not any("accuracy check" in str(m.get("content")) for m in example["messages"])
    assert example["messages"][-1]["content"].startswith("I couldn't add it")

    set_backend(Script(say("Noted.")))
    secret = orchestrator.handle(user_id, "my bank password is hunter2")
    feedback.save(user_id, secret["message_id"], 1, share=True)
    private = orchestrator.handle(user_id, "hello", private=True)
    feedback.save(user_id, private["message_id"], 1, share=True)  # rated, but an off-the-record reply keeps no prompt
    assert len(feedback.training_examples(user_id)) == 1  # neither the secret nor the off-the-record one


def test_only_final_replies_can_be_rated(user_id, client):
    set_backend(Script(say("Hi!")))
    out = orchestrator.handle(user_id, "hello")
    assert client.post(f"/messages/{out['message_id'] - 1}/feedback", json={"rating": 1}).status_code == 400  # the user's message
    assert client.post("/messages/999999/feedback", json={"rating": 1}).status_code == 404


# Google --------------------------------------------------------------------------------------

@pytest.fixture
def google_app(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "shh")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/integrations/google/callback")
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/token":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                return httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600, "scope": "s"})
            return httpx.Response(200, json={"access_token": "at-2", "expires_in": 3600})
        if request.url.path == "/v1/userinfo":
            return httpx.Response(200, json={"email": "om@example.com"})
        return httpx.Response(200)

    monkeypatch.setattr(google_account, "_transport", httpx.MockTransport(handler))
    return calls


def test_connecting_google_stores_encrypted_tokens(user_id, client, google_app):
    start = client.get("/integrations/google/start").json()["url"]
    query = parse_qs(urlsplit(start).query)
    assert query["code_challenge_method"] == ["S256"] and query["access_type"] == ["offline"]
    page = client.get("/integrations/google/callback", params={"code": "c-1", "state": query["state"][0]})
    assert "connected as om@example.com" in page.text
    assert client.get("/integrations").json()[0] == {"provider": "google", "configured": True, "connected": True,
                                                     "account": "om@example.com",
                                                     "connected_at": client.get("/integrations").json()[0]["connected_at"]}
    with db.connect() as conn:
        row = conn.execute("SELECT refresh_token FROM integrations").fetchone()
    assert b"rt-1" not in bytes(row["refresh_token"])
    assert google_account.access_token(user_id) == "at-1"
    with db.connect() as conn:  # expire it: the next call refreshes
        conn.execute("UPDATE integrations SET expires_at = '2000-01-01 00:00:00'")
    assert google_account.access_token(user_id) == "at-2"
    assert client.delete("/integrations/google").status_code == 204
    assert client.get("/integrations").json()[0]["connected"] is False


def test_a_forged_google_callback_is_refused(client, google_app):
    page = client.get("/integrations/google/callback", params={"code": "c", "state": "forged"})
    assert "sign-in link isn&#x27;t valid" in page.text


def test_google_tools_are_hidden_until_connected_and_denied_without_a_token(user_id, grant):
    backend = Script(say("OK."))
    set_backend(backend)
    orchestrator.handle(user_id, "any email from the bank?")
    grant("gmail", "read")
    out = sandbox.execute(user_id, "gmail_search_messages", {"query": "bank"}, message_id=_message(user_id), allowed=None)
    assert out.status == "denied" and "Google isn't" in out.error


class _FakeGoogle(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server's name
        if self.headers.get("Authorization") != "Bearer at-test":
            self.send_response(401)
            self.end_headers()
            return
        if "/users/me/messages?" in self.path:
            body = {"messages": [{"id": "m1"}]}
        elif "/users/me/messages/m1?format=metadata" in self.path:
            body = {"id": "m1", "snippet": "Your statement is ready", "payload": {"headers": [
                {"name": "From", "value": "Bank <no-reply@bank.example>"}, {"name": "Subject", "value": "Statement"}]}}
        else:
            body = {}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # quiet
        pass


@pytest.fixture
def fake_google():
    server = HTTPServer(("127.0.0.1", 0), _FakeGoogle)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _run(function: str, args: dict, ctx: dict):  # noqa: ANN202
    request = ToolRequest(module="app.tools.google_tools", function=function, args=args, context=ctx)
    return sandbox.SubprocessSandbox().run(request, 20)


def test_a_google_tool_reaches_only_its_allowed_hosts(tmp_path, fake_google):
    ctx = {"user_id": 1, "data_dir": str(tmp_path), "timezone": "UTC", "access_token": "at-test",
           "allow_hosts": ["127.0.0.1"], "api_base": fake_google}
    result = _run("search_messages", {"query": "bank", "limit": 5}, ctx)
    assert result["messages"][0]["subject"] == "Statement"

    with pytest.raises(ToolFailure, match="not allowed"):  # any other host stays blocked
        _run("search_messages", {"query": "x", "limit": 1}, {**ctx, "api_base": "http://example.com"})
    with pytest.raises(ToolFailure, match="not allowed"):  # and with no allow_hosts, no network at all
        _run("search_messages", {"query": "x", "limit": 1}, {**ctx, "allow_hosts": []})


# The larger model ----------------------------------------------------------------------------

def test_think_harder_uses_the_larger_model(user_id, client):
    small, large = Script(say("small")), Script(say("large"), model_id="big-1")
    set_backend(small)
    set_large_backend(large)
    resp = client.post("/chat", json={"message": "plan my week", "deep": True}).json()
    assert resp["reply"] == "large" and small.requests == []
    assert resp["used"]["checks"]["larger_model"] is True
    assert client.get("/features").json()["larger_model"] == "big-1"


def test_a_reply_that_fails_twice_goes_to_the_larger_model(user_id):
    small = Script(say("I've added lunch to your calendar for 1pm."))  # false every time
    large = Script(say("I can't add events here; Calendar · write is switched off in Tools & Data."), model_id="big-1")
    set_backend(small)
    set_large_backend(large)
    out = orchestrator.handle(user_id, "add lunch tomorrow at 1pm")
    assert out["reply"].startswith("I can't add events here")
    assert out["used"]["checks"]["status"] == "corrected" and out["used"]["checks"]["larger_model"] is True
    assert len(small.requests) == 2 and len(large.requests) == 1


def test_a_local_model_is_kept_loaded_and_warmed_on_request(client, monkeypatch):
    from app import warmup

    assert client.post("/warm").status_code == 204  # stub model: nothing to load, still fine
    posts = []
    monkeypatch.setattr(warmup.httpx, "post", lambda url, json, timeout: posts.append((url, json)))
    monkeypatch.setenv("MODEL_BACKEND", "nebius")
    monkeypatch.setenv("NEBIUS_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("NEBIUS_MODEL_ID", "custodian-nemotron")
    monkeypatch.setenv("NEBIUS_API_KEY", "ollama")
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    from app.config import reset_settings

    reset_settings()
    warmup.warm("6m")
    assert posts == [("http://127.0.0.1:11434/api/generate", {"model": "custodian-nemotron", "keep_alive": "6m"}),
                     ("http://127.0.0.1:11434/api/embed", {"model": "nomic-embed-text", "input": "", "keep_alive": "6m"})]
    monkeypatch.setenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1")  # a cloud model: nothing to keep loaded
    monkeypatch.setenv("EMBEDDING_MODEL", "")
    reset_settings()
    posts.clear()
    warmup.warm()
    assert posts == []


def test_questions_about_the_world_go_to_the_larger_model_but_personal_ones_stay(user_id):
    small, large = Script(say("small"), say("small"), say("small")), Script(say("large"), model_id="big-1")
    set_backend(small)
    set_large_backend(large)
    assert orchestrator.handle(user_id, "what's the weather in Pune today?")["reply"] == "large"
    assert orchestrator.handle(user_id, "what's the latest news about my rent case?")["reply"] == "small"
    assert orchestrator.handle(user_id, "what's the weather in Pune today?", private=True)["reply"] == "small"
    assert len(large.requests) == 1


def test_secrets_are_withheld_from_a_larger_model_on_the_internet(user_id, monkeypatch):
    monkeypatch.setenv("LARGE_MODEL_BASE_URL", "https://api.tokenfactory.nebius.com/v1")
    large = Script(say("OK."), model_id="big-1")
    set_backend(Script(say("small")))
    set_large_backend(large)
    orchestrator.handle(user_id, "my wifi password is sunshine42, plan my week", deep=True)
    assert "sunshine42" not in json.dumps(large.requests)


# Attaching a file to a message -----------------------------------------------------------------

def test_an_attached_file_is_read_with_the_message_without_the_files_switch(user_id, client):
    doc = client.post("/documents", json={"name": "lease.txt", "data": b64(b"Pets are allowed with a deposit of 5000 rupees.")}).json()
    backend = Script(say("Your lease allows pets with a 5000 rupee deposit."))
    set_backend(backend)
    resp = client.post("/chat", json={"message": "can I keep a cat?", "document_ids": [doc["id"]]}).json()
    turn = backend.requests[-1][-1]["content"]
    assert "Pets are allowed with a deposit of 5000 rupees." in turn and turn.endswith("can I keep a cat?")
    assert "not instructions" in turn
    assert resp["used"]["tools"] == []  # no Files switch needed: attaching it was the permission

    backend.responses = [say("The deposit is 5000 rupees.")]
    client.post("/chat", json={"message": "how much is the deposit?", "conversation_id": resp["conversation_id"]})
    assert "Pets are allowed" in backend.requests[-1][-1]["content"]  # a follow-up still sees the file

    thread = client.get(f"/conversations/{resp['conversation_id']}").json()
    assert thread["messages"][0]["content"] == f"can I keep a cat?\n📎 lease.txt (file #{doc['id']})"


def test_someone_elses_or_a_missing_file_is_not_attached(user_id, client):
    backend = Script(say("OK."))
    set_backend(backend)
    client.post("/chat", json={"message": "read this", "document_ids": [999]})
    assert "attached these files" not in backend.requests[-1][-1]["content"]


def test_a_web_page_is_stored_as_readable_text(user_id, client):
    page = b"<html><head><style>p{}</style><script>alert(1)</script></head><body><h1>Plan</h1><p>Ship &amp; test</p></body></html>"
    doc = client.post("/documents", json={"name": "plan.html", "content_type": "text/html", "data": b64(page)}).json()
    assert doc["kind"] == "text" and doc["preview"] == "Plan\nShip & test"


def test_the_microphone_is_allowed_on_custodians_own_pages(client):
    policy = client.get("/health").headers["Permissions-Policy"]
    assert "microphone=(self)" in policy and "camera=()" in policy
