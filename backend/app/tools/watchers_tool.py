"""Tool: things Custodian keeps an eye on for you - a price dropping below a number, a page
changing, some words appearing ("in stock"), an email from someone - kept in
DATA_DIR/tools/watchers/watchers.db.

The tool only stores them. The server checks each one when it's due (app/watchers.py): it
reads the page through the sandboxed web reader (public internet only) or searches Gmail with
the Gmail switch, and tells you - in the inbox, on your phone, and out loud on the PC - when it
happens. A watch on a number ends once it's met; one on changes or new emails keeps going.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

MAX_WATCHES = 30


def connect(data_dir: str | Path) -> sqlite3.Connection:
    """The watches database; also read and written by app/watchers.py (outside the sandbox)."""
    conn = sqlite3.connect(Path(data_dir) / "watchers.db", timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA secure_delete = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS watches (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          kind TEXT NOT NULL CHECK (kind IN ('page', 'email')),
          what TEXT NOT NULL,                  -- in their words: "iPhone 16 price", "an email from Priya"
          url TEXT,                            -- page watches
          condition TEXT NOT NULL,             -- below | above | changes | appears | disappears | email
          value REAL,                          -- for below / above
          text TEXT,                           -- for appears / disappears; the sender for email
          subject TEXT,                        -- email: words the subject must have
          every_minutes INTEGER NOT NULL DEFAULT 60,
          status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'done', 'stopped', 'failing')),
          last_value TEXT,                     -- what was seen last (a price, a page fingerprint, email ids)
          last_checked_at TEXT,
          next_check_at TEXT NOT NULL DEFAULT (datetime('now')),
          failures INTEGER NOT NULL DEFAULT 0,
          fired_at TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS watches_due ON watches(status, next_check_at);
        """
    )
    return conn


def describe(row: sqlite3.Row) -> dict:
    condition = row["condition"]
    if condition in ("below", "above"):
        rule = f"tell you when it's {condition} {row['value']:g}"
    elif condition == "changes":
        rule = "tell you when the page changes"
    elif condition in ("appears", "disappears"):
        rule = f"tell you when “{row['text']}” {'appears on' if condition == 'appears' else 'is gone from'} the page"
    else:
        rule = f"tell you when an email from {row['text']} arrives" + (f" about “{row['subject']}”" if row["subject"] else "")
    seen = row["last_value"]
    return {
        "id": row["id"], "kind": row["kind"], "what": row["what"], "url": row["url"], "rule": rule,
        "status": row["status"], "every_minutes": row["every_minutes"],
        "last_seen": None if row["kind"] == "email" or condition == "changes" else seen,
        "last_checked_at": row["last_checked_at"].replace(" ", "T") + "Z" if row["last_checked_at"] else None,
        "fired_at": row["fired_at"].replace(" ", "T") + "Z" if row["fired_at"] else None,
    }


def _add(ctx: dict, **fields) -> dict:
    conn = connect(ctx["data_dir"])
    try:
        with conn:
            active = conn.execute("SELECT COUNT(*) FROM watches WHERE user_id = ? AND status = 'active'",
                                  (ctx["user_id"],)).fetchone()[0]
            if active >= MAX_WATCHES:
                raise ValueError(f"there are already {MAX_WATCHES} watches; stop one first")
            columns = ["user_id", *fields]
            watch_id = conn.execute(
                f"INSERT INTO watches ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                [ctx["user_id"], *fields.values()],
            ).lastrowid
        row = conn.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
    finally:
        conn.close()
    return {"watching": describe(row), "note": "the first check runs within a few minutes"}


def watch_page(ctx: dict, url: str, what: str, condition: str, value: float | None = None, text: str | None = None,
               every_minutes: int = 60) -> dict:
    if condition in ("below", "above") and value is None:
        raise ValueError(f"'{condition}' needs the number to compare with, in `value`")
    if condition in ("appears", "disappears") and not text:
        raise ValueError(f"'{condition}' needs the words to look for, in `text`")
    return _add(ctx, kind="page", what=what, url=url, condition=condition, value=value, text=text,
                every_minutes=every_minutes)


def watch_email(ctx: dict, sender: str, subject: str | None = None, every_minutes: int = 15) -> dict:
    return _add(ctx, kind="email", what=f"an email from {sender}", condition="email", text=sender, subject=subject,
                every_minutes=every_minutes)


def list_watches(ctx: dict) -> dict:
    conn = connect(ctx["data_dir"])
    try:
        rows = conn.execute("SELECT * FROM watches WHERE user_id = ? AND status IN ('active', 'failing') ORDER BY id",
                            (ctx["user_id"],)).fetchall()
    finally:
        conn.close()
    return {"watches": [describe(r) for r in rows]}


def stop_watch(ctx: dict, id: int) -> dict:  # noqa: A002 - the model's argument name
    conn = connect(ctx["data_dir"])
    try:
        with conn:
            changed = conn.execute("UPDATE watches SET status = 'stopped' WHERE id = ? AND user_id = ? AND status IN ('active', 'failing')",
                                   (id, ctx["user_id"])).rowcount
    finally:
        conn.close()
    if not changed:
        raise ValueError(f"there's no active watch #{id}")
    return {"stopped": id}


def seen_ids(row: sqlite3.Row) -> list[str]:
    try:
        return json.loads(row["last_value"] or "[]")
    except ValueError:
        return []
