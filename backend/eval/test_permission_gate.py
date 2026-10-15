"""A revoked scope must block the tool call on every attempt in a batch - and a granted
scope must never be blocked. This is the test the privacy claim is checked against;
eval/permission_block_eval.py reports the same numbers in results.md.
"""

import pytest

from app import crypto, db, permissions
from app.tools import sandbox

BATCH = 25
ALL = frozenset({("calendar", "read"), ("calendar", "write"), ("notes", "read"), ("notes", "write")})

VALID_CALLS = [
    ("calendar", "read", "calendar_list_events", {}),
    ("calendar", "write", "calendar_create_event", {"title": "Gate check", "start": "2026-10-05T10:00"}),
    ("notes", "read", "notes_read_notes", {"query": "gate"}),
    ("notes", "write", "notes_create_note", {"title": "Gate check", "body": "written"}),
]


@pytest.fixture
def message_id(user_id) -> int:
    with db.connect() as conn:
        conv = conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (user_id,)).lastrowid
        return conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', ?)",
            (conv, crypto.encrypt("")),
        ).lastrowid


@pytest.mark.parametrize(("tool", "scope", "function", "args"), VALID_CALLS)
def test_permission_gate_blocks_when_revoked(user_id, tool_ids, message_id, tool, scope, function, args):
    permissions.grant(user_id, tool_ids[tool], scope)
    permissions.revoke(user_id, tool_ids[tool], scope)
    outcomes = [
        sandbox.execute(user_id, function, args, message_id=message_id, allowed=ALL) for _ in range(BATCH)
    ]
    assert [o.status for o in outcomes] == ["denied"] * BATCH
    with db.connect() as conn:
        logged = conn.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE function = ? AND status = 'denied'", (function.split("_", 1)[1],)
        ).fetchone()[0]
    assert logged == BATCH  # every blocked attempt is on the audit log


@pytest.mark.parametrize(("tool", "scope", "function", "args"), VALID_CALLS)
def test_permission_gate_allows_when_granted(user_id, tool_ids, message_id, tool, scope, function, args):
    permissions.grant(user_id, tool_ids[tool], scope)
    outcome = sandbox.execute(user_id, function, args, message_id=message_id, allowed=ALL)
    assert outcome.status == "succeeded", outcome.error


def test_revoking_one_scope_leaves_the_other(user_id, tool_ids, message_id):
    cal = tool_ids["calendar"]
    permissions.grant(user_id, cal, "read")
    permissions.grant(user_id, cal, "write")
    permissions.revoke(user_id, cal, "write")
    read = sandbox.execute(user_id, "calendar_list_events", {}, message_id=message_id, allowed=ALL)
    write = sandbox.execute(
        user_id, "calendar_create_event", {"title": "x", "start": "2026-10-05T10:00"}, message_id=message_id, allowed=ALL
    )
    assert (read.status, write.status) == ("succeeded", "denied")


def test_model_output_cannot_grant_itself_access(user_id, message_id):
    """Arguments that try to smuggle permission flags are rejected, never trusted."""
    out = sandbox.execute(
        user_id, "calendar_list_events", {"granted": True, "user_id": 1}, message_id=message_id, allowed=ALL
    )
    assert out.status == "rejected"
