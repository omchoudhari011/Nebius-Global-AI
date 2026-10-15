"""Every tool function the model can call, through the real gate and sandbox: blocked while its
switch is off, working once it's on. A new function in app/tools/registry.py without a case
here fails test_every_registered_function_is_checked."""

import base64
from datetime import datetime, timedelta

import pytest

from app import db, documents, permissions
from app.crypto import encrypt
from app.tools import registry, sandbox

SOON = (datetime.now() + timedelta(hours=2)).replace(second=0, microsecond=0).isoformat()

# (function, arguments, check on the result). Run in order: later cases use earlier results.
LOCAL = [
    ("calendar_create_event", {"title": "Lunch", "start": "2030-01-02T13:00"}, lambda r: r["created"]["title"] == "Lunch"),
    ("calendar_list_events", {"start_date": "2030-01-02"}, lambda r: r["events"][0]["title"] == "Lunch"),
    ("notes_create_note", {"title": "Plumber", "body": "Call about the tap"}, lambda r: r["created"]["title"] == "Plumber"),
    ("notes_read_notes", {"query": "plumber"}, lambda r: r["notes"][0]["body"] == "Call about the tap"),
    ("reminders_create_reminder", {"text": "Stretch", "at": SOON}, lambda r: r["created"]["text"] == "Stretch"),
    ("reminders_list_reminders", {}, lambda r: r["reminders"][0]["text"] == "Stretch"),
    ("reminders_cancel_reminder", {"id": 1}, lambda r: r["cancelled"] == 1),
    ("documents_search_documents", {"query": "pets"}, lambda r: r["documents"][0]["name"] == "lease.txt"),
    ("documents_read_document", {"id": 1}, lambda r: "5000" in r["text"]),
]
# The Google functions need a connected account and Google's servers; tests/test_everyday.py
# runs them in the sandbox against a local fake API, and the gate's handling is checked here.
GOOGLE = {
    "gcal_list_events": {},
    "gcal_create_event": {"title": "Call", "start": "2030-01-02T15:00"},
    "gmail_search_messages": {"query": "bank"},
    "gmail_read_message": {"id": "m1"},
    "gdrive_search_files": {"query": "budget"},
    "gdrive_read_file": {"id": "d1"},
}


def test_every_registered_function_is_checked():
    assert {f.exposed_name for f in registry.FUNCTIONS} == {name for name, _, _ in LOCAL} | set(GOOGLE)


@pytest.fixture
def message_id(user_id):
    with db.connect() as conn:
        conv = conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (user_id,)).lastrowid
        return conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', ?)",
                            (conv, encrypt(""))).lastrowid


def test_each_local_tool_is_blocked_until_allowed_then_works(user_id, tool_ids, message_id):
    documents.add(user_id, "lease.txt", "text/plain", base64.b64encode(b"Pets allowed with a deposit of 5000 rupees.").decode())
    for name, args, check in LOCAL:
        fn = registry.find(name)
        permissions.set_granted(user_id, tool_ids[fn.tool], fn.scope, False)
        blocked = sandbox.execute(user_id, name, args, message_id=message_id, allowed=None)
        assert blocked.status == "denied", name
        permissions.set_granted(user_id, tool_ids[fn.tool], fn.scope, True)
        out = sandbox.execute(user_id, name, args, message_id=message_id, allowed=None)
        assert out.status == "succeeded", (name, out.error)
        assert check(out.result), (name, out.result)


@pytest.mark.parametrize("name", sorted(GOOGLE))
def test_google_tools_are_blocked_without_a_connected_account(user_id, tool_ids, message_id, name):
    fn = registry.find(name)
    permissions.set_granted(user_id, tool_ids[fn.tool], fn.scope, True)  # switch on, but no account
    out = sandbox.execute(user_id, name, GOOGLE[name], message_id=message_id, allowed=None)
    assert out.status == "denied" and "Google isn't connected" in out.error
