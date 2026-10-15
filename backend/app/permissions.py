"""Default-deny, per-user, per-tool, per-scope switches.

check() runs inside app/tools/sandbox.py immediately before a tool executes, so the
model's own output is never trusted. grant()/revoke() upsert the one row per
(user, tool, scope); the permissions_log_* triggers append every change to
permission_events, so app code can't skip the history.
"""

from __future__ import annotations

import sqlite3

from . import db


class UnknownScope(LookupError):
    """The (tool_id, scope) pair isn't in tool_scopes."""


def check(user_id: int, tool_id: int, scope: str, *, conn: sqlite3.Connection | None = None) -> bool:
    with db.use(conn) as c:
        row = c.execute(
            "SELECT granted FROM permissions WHERE user_id = ? AND tool_id = ? AND scope = ?",
            (user_id, tool_id, scope),
        ).fetchone()
    return bool(row and row["granted"] == 1)


def set_granted(
    user_id: int, tool_id: int, scope: str, granted: bool, *, conn: sqlite3.Connection | None = None
) -> dict:
    with db.use(conn) as c:
        known = c.execute(
            "SELECT 1 FROM tool_scopes WHERE tool_id = ? AND scope = ?", (tool_id, scope)
        ).fetchone()
        if known is None:
            raise UnknownScope(f"tool {tool_id} has no scope {scope!r}")
        c.execute(
            """
            INSERT INTO permissions (user_id, tool_id, scope, granted) VALUES (?, ?, ?, ?)
            ON CONFLICT (user_id, tool_id, scope) DO UPDATE SET granted = excluded.granted
            """,
            (user_id, tool_id, scope, 1 if granted else 0),
        )
        return get(user_id, tool_id, scope, conn=c)


def grant(user_id: int, tool_id: int, scope: str, *, conn: sqlite3.Connection | None = None) -> dict:
    return set_granted(user_id, tool_id, scope, True, conn=conn)


def revoke(user_id: int, tool_id: int, scope: str, *, conn: sqlite3.Connection | None = None) -> dict:
    return set_granted(user_id, tool_id, scope, False, conn=conn)


_LIST_SQL = """
    SELECT t.id AS tool_id, t.name AS tool_name, t.category, t.enabled, t.executor,
           s.scope, s.description, COALESCE(p.granted, 0) AS granted
    FROM tool_scopes s
    JOIN tools t ON t.id = s.tool_id
    LEFT JOIN permissions p ON p.tool_id = s.tool_id AND p.scope = s.scope AND p.user_id = ?
"""


def _shape(row: sqlite3.Row) -> dict:
    return {
        "tool_id": row["tool_id"],
        "tool_name": row["tool_name"],
        "category": row["category"],
        "scope": row["scope"],
        "description": row["description"],
        "granted": bool(row["granted"]),
        "enabled": bool(row["enabled"]),
        "executor": row["executor"],
    }


def get(user_id: int, tool_id: int, scope: str, *, conn: sqlite3.Connection | None = None) -> dict:
    with db.use(conn) as c:
        row = c.execute(_LIST_SQL + " WHERE s.tool_id = ? AND s.scope = ?", (user_id, tool_id, scope)).fetchone()
    if row is None:
        raise UnknownScope(f"tool {tool_id} has no scope {scope!r}")
    return _shape(row)


def list_for_user(user_id: int, *, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Every tool scope with the user's current switch (missing row = off)."""
    with db.use(conn) as c:
        rows = c.execute(_LIST_SQL + " ORDER BY t.name, s.scope", (user_id,)).fetchall()
    return [_shape(r) for r in rows]


def events(user_id: int, limit: int = 50, *, conn: sqlite3.Connection | None = None) -> list[dict]:
    with db.use(conn) as c:
        rows = c.execute(
            """
            SELECT e.id, e.granted, e.changed_at, p.tool_id, t.name AS tool_name, p.scope
            FROM permission_events e
            JOIN permissions p ON p.id = e.permission_id
            JOIN tools t ON t.id = p.tool_id
            WHERE p.user_id = ?
            ORDER BY e.id DESC LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "tool_id": r["tool_id"],
            "tool_name": r["tool_name"],
            "scope": r["scope"],
            "granted": bool(r["granted"]),
            "changed_at": db.iso(r["changed_at"]),
        }
        for r in rows
    ]
