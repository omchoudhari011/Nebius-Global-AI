"""Permission-block rate: proposes every tool function many times with its scope revoked
(each must be blocked) and a few times with it granted (none may be blocked), through the
same sandbox.execute() gate the orchestrator uses, then counts outcomes from tool_calls.

    python eval/permission_block_eval.py [--attempts 50] [--no-write]
"""

from __future__ import annotations

import argparse

from _common import setup, update_results

CALLS = [
    ("calendar", "read", "calendar_list_events", {}),
    ("calendar", "write", "calendar_create_event", {"title": "Eval", "start": "2026-10-05T10:00"}),
    ("notes", "read", "notes_read_notes", {"query": "eval"}),
    ("notes", "write", "notes_create_note", {"title": "Eval", "body": "permission eval"}),
]
ALL = frozenset((tool, scope) for tool, scope, _, _ in CALLS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempts", type=int, default=50, help="attempts per scope while revoked")
    parser.add_argument("--granted-attempts", type=int, default=5, help="attempts per scope while granted")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    user_id = setup("stub")
    from app import crypto, db, permissions
    from app.tools import sandbox

    with db.connect() as conn:
        tools = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM tools")}
        conv = conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (user_id,)).lastrowid
        message_id = conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', ?)", (conv, crypto.encrypt(""))
        ).lastrowid

    blocked = attempted_revoked = wrongly_blocked = attempted_granted = 0
    for tool, scope, function, call_args in CALLS:
        permissions.grant(user_id, tools[tool], scope)
        permissions.revoke(user_id, tools[tool], scope)
        for _ in range(args.attempts):
            out = sandbox.execute(user_id, function, call_args, message_id=message_id, allowed=ALL)
            attempted_revoked += 1
            blocked += out.status == "denied"
        permissions.grant(user_id, tools[tool], scope)
        for _ in range(args.granted_attempts):
            out = sandbox.execute(user_id, function, call_args, message_id=message_id, allowed=ALL)
            attempted_granted += 1
            wrongly_blocked += out.status != "succeeded"
        permissions.revoke(user_id, tools[tool], scope)
        print(f"{tool}:{scope}  revoked -> {args.attempts} attempts; granted -> {args.granted_attempts} attempts")

    with db.connect() as conn:
        logged = conn.execute("SELECT COUNT(*) FROM tool_calls WHERE status = 'denied'").fetchone()[0]
    rate = 100.0 * blocked / attempted_revoked
    summary = (
        f"{rate:.0f}% - {blocked}/{attempted_revoked} tool calls blocked with the scope revoked "
        f"({logged} logged as denied in tool_calls); {wrongly_blocked}/{attempted_granted} wrongly blocked with it granted"
    )
    if args.no_write:
        print(summary)
    else:
        update_results("Permission-block rate", summary)
    return 0 if blocked == attempted_revoked and wrongly_blocked == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
