import json

import pytest

from app import crypto, db
from app.tools import calendar_tool, notes_tool, sandbox
from app.tools.base import ToolTimeout

ALL = frozenset({("calendar", "read"), ("calendar", "write"), ("notes", "read"), ("notes", "write")})


@pytest.fixture
def message_id(user_id) -> int:
    with db.connect() as conn:
        conv = conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (user_id,)).lastrowid
        return conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', ?)",
            (conv, crypto.encrypt("")),
        ).lastrowid


def _row(call_id):
    with db.connect() as conn:
        return conn.execute("SELECT * FROM tool_calls WHERE id = ?", (call_id,)).fetchone()


def test_unknown_tool_is_not_logged(user_id, message_id):
    out = sandbox.execute(user_id, "shell_exec", {"cmd": "rm -rf /"}, message_id=message_id, allowed=ALL)
    assert out.status == "unknown" and out.tool_call_id is None
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 0


@pytest.mark.parametrize(
    "args",
    [
        {"title": "x", "start": "not a date"},
        {"title": "x", "start": "2026-10-05T15:00", "attendees": ["eve"]},
        {"title": "../../etc/passwd", "start": "2026-10-05T15:00"},
        {"title": "line\nbreak", "start": "2026-10-05T15:00"},
        "{not json",
        "[1, 2]",
    ],
)
def test_bad_arguments_are_rejected_before_the_gate(user_id, message_id, grant, args):
    grant("calendar", "write")
    out = sandbox.execute(user_id, "calendar_create_event", args, message_id=message_id, allowed=ALL)
    assert out.status == "rejected"
    row = _row(out.tool_call_id)
    assert row["status"] == "rejected" and row["finished_at"] is not None and row["result"] is None


def test_denied_without_permission(user_id, message_id):
    out = sandbox.execute(user_id, "calendar_list_events", {}, message_id=message_id, allowed=ALL)
    assert out.status == "denied"
    assert "Calendar · read" in out.error
    assert _row(out.tool_call_id)["status"] == "denied"


def test_denied_outside_the_skill_allowlist(user_id, message_id, grant):
    grant("calendar", "write")
    out = sandbox.execute(
        user_id,
        "calendar_create_event",
        {"title": "Sneaky", "start": "2026-10-05T15:00"},
        message_id=message_id,
        allowed=frozenset({("calendar", "read")}),
        skill_name="daily-summary",
    )
    assert out.status == "denied" and "daily-summary" in out.error


def test_denied_when_the_tool_is_disabled(user_id, message_id, grant):
    grant("notes", "read")
    with db.connect() as conn:
        conn.execute("UPDATE tools SET enabled = 0 WHERE name = 'notes'")
    out = sandbox.execute(user_id, "notes_read_notes", {}, message_id=message_id, allowed=ALL)
    assert out.status == "denied" and "switched off" in out.error


def test_succeeds_and_stores_encrypted_result(user_id, message_id, grant):
    grant("calendar", "write")
    out = sandbox.execute(
        user_id,
        "calendar_create_event",
        json.dumps({"title": "Standup", "start": "2026-10-05T09:30", "duration_minutes": 15}),
        message_id=message_id,
        allowed=ALL,
    )
    assert out.status == "succeeded", out.error
    assert out.result["created"]["start"] == "2026-10-05T09:30+05:30"
    row = _row(out.tool_call_id)
    assert row["latency_ms"] is not None and row["finished_at"] is not None
    assert b"Standup" not in bytes(row["args"]) and b"Standup" not in bytes(row["result"])
    assert crypto.decrypt_json(row["result"])["created"]["title"] == "Standup"


def test_timeout_is_recorded(user_id, message_id, grant, monkeypatch):
    class Slow:
        def run(self, request, timeout):
            raise ToolTimeout("the tool took longer than 5 s and was stopped")

    monkeypatch.setitem(sandbox.EXECUTORS, "sandbox", Slow())
    grant("notes", "read")
    out = sandbox.execute(user_id, "notes_read_notes", {}, message_id=message_id, allowed=ALL)
    assert out.status == "timed_out"
    assert _row(out.tool_call_id)["status"] == "timed_out"


def test_unavailable_executor_fails_cleanly(user_id, message_id, grant):
    grant("notes", "read")
    with db.connect() as conn:
        conn.execute("UPDATE tools SET executor = 'nemoclaw' WHERE name = 'notes'")
    out = sandbox.execute(user_id, "notes_read_notes", {}, message_id=message_id, allowed=ALL)
    assert out.status == "failed" and "nemoclaw" in out.error


def test_recover_stale(user_id, message_id, tool_ids):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO tool_calls (message_id, tool_id, scope, function, args, status) VALUES (?, ?, 'read', 'list_events', ?, 'running')",
            (message_id, tool_ids["calendar"], crypto.encrypt("{}")),
        )
    assert sandbox.recover_stale() == 1
    assert sandbox.list_tool_calls(user_id)[0]["status"] == "failed"


def test_calendar_tool_handles_time_zones(tmp_path):
    ctx = {"user_id": 1, "data_dir": str(tmp_path), "timezone": "Asia/Kolkata"}
    calendar_tool.create_event(ctx, "Late call", "2026-10-05T23:30", 60)
    calendar_tool.create_event(ctx, "UTC call", "2026-10-05T10:00:00+00:00", 30)
    day = calendar_tool.list_events(ctx, "2026-10-05")
    assert [e["title"] for e in day["events"]] == ["UTC call", "Late call"]
    assert day["events"][0]["start"] == "2026-10-05T15:30+05:30"
    assert [e["title"] for e in calendar_tool.list_events(ctx, "2026-10-06")["events"]] == ["Late call"]  # spans midnight


def test_notes_search_escapes_wildcards(tmp_path):
    ctx = {"user_id": 1, "data_dir": str(tmp_path), "timezone": "UTC"}
    notes_tool.create_note(ctx, "Budget", "Rent is 40% of income")
    notes_tool.create_note(ctx, "Groceries", "Milk and eggs")
    assert [n["title"] for n in notes_tool.read_notes(ctx, "%")["notes"]] == ["Budget"]
    assert [n["title"] for n in notes_tool.read_notes(ctx, "milk eggs")["notes"]] == ["Groceries"]
    assert len(notes_tool.read_notes(ctx, "")["notes"]) == 2
    other = {**ctx, "user_id": 2}
    assert notes_tool.read_notes(other, "")["notes"] == []
