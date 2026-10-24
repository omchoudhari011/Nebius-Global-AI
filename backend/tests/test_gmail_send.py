"""Writing email from Gmail: drafts, replies in their thread, and sending - only after the user
allows it, where they asked (the app, Telegram or the PC), never from a scheduled task."""

import base64
import email
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import approvals, db, desktop, google_account, grounding
from app.tools import sandbox
from app.tools.base import ToolRequest
from app.tools.schemas import EmailArgs

SEND = {"to": ["priya@example.com"], "subject": "Q3 numbers", "body": "Hi Priya,\n\nThe final numbers are in.\n\nOm"}


class _FakeGmail(BaseHTTPRequestHandler):
    posts: list = []

    def _json(self, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        assert "/users/me/messages/m1?format=metadata" in self.path
        self._json({"id": "m1", "threadId": "t-9", "payload": {"headers": [
            {"name": "Subject", "value": "Dinner on Friday?"}, {"name": "Message-ID", "value": "<abc@mail.example>"}]}})

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeGmail.posts.append((self.path, body))
        self._json({"id": "d-1"} if self.path.endswith("/drafts") else {"id": "sent-1", "threadId": "t-9"})

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_gmail():
    _FakeGmail.posts = []
    server = HTTPServer(("127.0.0.1", 0), _FakeGmail)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _sandboxed(function: str, args: dict, base: str, tmp_path):  # noqa: ANN202
    ctx = {"user_id": 1, "data_dir": str(tmp_path), "timezone": "UTC", "access_token": "at", "allow_hosts": ["127.0.0.1"],
           "api_base": base}
    return sandbox.SubprocessSandbox().run(ToolRequest("app.tools.google_tools", function, args, ctx), 20)


def _raw(post) -> email.message.Message:  # noqa: ANN001
    body = post[1].get("message", post[1])
    return email.message_from_bytes(base64.urlsafe_b64decode(body["raw"]))


def test_a_reply_stays_in_its_thread_and_a_draft_is_only_saved(fake_gmail, tmp_path):
    sent = _sandboxed("send_email", {**SEND, "subject": "", "reply_to_id": "m1"}, fake_gmail, tmp_path)
    assert sent == {"id": "sent-1", "to": ["priya@example.com"], "cc": [], "sent": True, "in_reply_to": "m1"}
    path, body = _FakeGmail.posts[0]
    assert path.endswith("/users/me/messages/send") and body["threadId"] == "t-9"
    mail = _raw(_FakeGmail.posts[0])
    assert mail["Subject"] == "Re: Dinner on Friday?" and mail["In-Reply-To"] == "<abc@mail.example>"
    assert mail["To"] == "priya@example.com" and "The final numbers are in." in mail.get_payload()

    draft = _sandboxed("draft_email", {**SEND, "cc": ["sam@example.com"]}, fake_gmail, tmp_path)
    assert draft["sent"] is False and draft["saved_in"] == "Gmail Drafts"
    assert _FakeGmail.posts[1][0].endswith("/drafts") and _raw(_FakeGmail.posts[1])["Cc"] == "sam@example.com"


@pytest.mark.parametrize("bad", ["Priya", "priya at example.com", "a@b", "x@y.com>, evil@z.com"])
def test_only_real_addresses_are_accepted_never_a_guess_from_a_name(bad):
    with pytest.raises(ValueError, match="email address"):
        EmailArgs(to=[bad], body="Hi")


# The gate: your OK first ------------------------------------------------------------------------

class _Recorder:
    def __init__(self):
        self.ran = []

    def run(self, request, timeout):
        self.ran.append(request)
        return {"id": "sent-1", "to": request.args["to"], "sent": True}


@pytest.fixture
def gmail_ready(monkeypatch, grant):
    grant("gmail", "write")
    monkeypatch.setattr(google_account, "access_token", lambda user_id: "at")
    recorder = _Recorder()
    monkeypatch.setitem(sandbox.EXECUTORS, "sandbox", recorder)
    desktop.reset()
    approvals.reset()
    yield recorder
    desktop.reset()
    approvals.reset()


def _message(user_id: int) -> int:
    with db.connect() as conn:
        conv = conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (user_id,)).lastrowid
        return conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', x'00')", (conv,)).lastrowid


def _send_from(origin: str, user_id: int, outcome: list) -> threading.Thread:
    message_id = _message(user_id)

    def turn():
        token = approvals.origin.set(origin)
        try:
            outcome.append(sandbox.execute(user_id, "gmail_send_email", SEND, message_id=message_id, allowed=None))
        finally:
            approvals.origin.reset(token)

    thread = threading.Thread(target=turn, daemon=True)
    thread.start()
    return thread


def _waiting(client) -> dict:
    for _ in range(100):
        if client.get("/approvals").json():
            return client.get("/approvals").json()[0]
        time.sleep(0.05)
    raise AssertionError("nothing was asked")


def test_an_email_asked_for_in_the_app_is_sent_only_once_allowed_there(user_id, client, gmail_ready):
    outcome: list = []
    thread = _send_from("app", user_id, outcome)
    waiting = _waiting(client)
    assert waiting["title"] == "Send an email" and "To: priya@example.com" in waiting["details"]
    assert "Subject: Q3 numbers" in waiting["details"] and "The final numbers are in." in waiting["details"]
    assert gmail_ready.ran == []  # nothing goes before you answer
    client.post(f"/approvals/{waiting['id']}", json={"allow": True})
    thread.join(timeout=10)
    assert outcome[0].status == "succeeded" and len(gmail_ready.ran) == 1


def test_a_declined_email_is_never_sent(user_id, client, gmail_ready):
    outcome: list = []
    thread = _send_from("telegram", user_id, outcome)
    client.post(f"/approvals/{_waiting(client)['id']}", json={"allow": False})
    thread.join(timeout=10)
    assert outcome[0].status == "denied" and "declined" in outcome[0].error and gmail_ready.ran == []


def test_spoken_on_the_pc_the_pc_shows_the_email_and_asks(user_id, gmail_ready, monkeypatch):
    asked = []

    def pc(user_id, function, args, *, confirm, description, approved=None):
        asked.append((function, args, confirm))
        if len(asked) > 1:
            raise RuntimeError("The user declined this on the PC.")

    desktop.touch(user_id, "Test PC")
    monkeypatch.setattr(desktop, "run", pc)
    outcome: list = []
    _send_from("desktop", user_id, outcome).join(timeout=10)
    function, args, confirm = asked[0]
    assert function == "confirm_plan" and confirm and args["title"] == "Send an email"
    assert args["details"].startswith("Send an email from your Gmail\nTo: priya@example.com")
    assert outcome[0].status == "succeeded"
    _send_from("desktop", user_id, outcome).join(timeout=10)  # declined the second time
    assert outcome[1].status == "denied" and "declined" in outcome[1].error and len(gmail_ready.ran) == 1


def test_a_scheduled_task_never_sends_and_with_nowhere_to_ask_nothing_goes(user_id, gmail_ready):
    outcome: list = []
    _send_from("task", user_id, outcome).join(timeout=10)
    assert outcome[0].status == "denied" and "scheduled task" in outcome[0].error
    _send_from("desktop", user_id, outcome).join(timeout=10)  # the desktop app isn't connected
    assert outcome[1].status == "denied" and "nowhere to ask" in outcome[1].error
    assert gmail_ready.ran == []


def test_a_draft_needs_no_ok_but_its_switch(user_id, gmail_ready):
    outcome: list = []
    token = approvals.origin.set("task")
    try:
        out = sandbox.execute(user_id, "gmail_draft_email", SEND, message_id=_message(user_id), allowed=None)
    finally:
        approvals.origin.reset(token)
    assert out.status == "succeeded" and len(gmail_ready.ran) == 1
    assert outcome == []


# The accuracy check ------------------------------------------------------------------------------

class _Outcome:
    def __init__(self, tool, function, status):
        self.tool, self.function, self.status, self.error = tool, function, status, None


def _findings(reply: str, *outcomes) -> list[str]:
    evidence = grounding.Evidence(message="email Priya the numbers", outcomes=list(outcomes))
    return [f.kind for f in grounding.review(reply, evidence)]


def test_sent_is_said_only_when_gmail_sent_it():
    assert "unverified_send" in _findings("I've sent the email to Priya.")
    assert "unverified_send" in _findings("Done, sent the reply.", _Outcome("gmail", "send_email", "denied"))
    assert "unverified_send" not in _findings("I've sent the email to Priya.", _Outcome("gmail", "send_email", "succeeded"))
    assert "unverified_draft" in _findings("I saved it as a draft.")
    assert "unverified_draft" not in _findings("It's in your Drafts.", _Outcome("gmail", "draft_email", "succeeded"))
    assert _findings("Shall I send the email now?") == []  # a question claims nothing
