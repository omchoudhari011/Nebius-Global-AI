"""Checks what you asked Custodian to watch (app/tools/watchers_tool.py stores it): every few
minutes, each watch that's due is checked and, when it happens, you're told - in the inbox, on
your phone if set up, and out loud on the PC.

- A page is read through the sandboxed web reader (web_tools.page_snapshot: public internet only,
  never this machine or your network), so a watched link can't reach anything private.
- A price is taken from the page itself - the first amount of money after the product's name -
  never made up by a model: "below 60000" fires only on a number that's on the page.
- "changes" compares a fingerprint of the page's text (just the lines about what you named, when
  there are some, so ads and timestamps don't count); the first check only records it.
- An email watch searches Gmail with the Gmail · read switch; the first check records what's
  already there, so only new mail is announced.

A watch that can't be checked six times in a row stops ("failing") and says why, once.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db, google_account, notifications, permissions
from .config import get_settings
from .tools import watchers_tool
from .tools.base import ToolRequest

log = logging.getLogger(__name__)

MAX_FAILURES = 6
MONEY = re.compile(r"(?:₹|rs\.?|inr|\$|usd|€|eur|£|gbp)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
                   r"|\b([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?:rupees|inr|usd|eur|gbp)\b", re.I)
# Amounts that aren't what it costs now: the list price, a discount, an instalment, delivery.
NOT_THE_PRICE_BEFORE = re.compile(r"(?:m\.?r\.?p\.?|list\s+price|was|emi|save|saving|cashback|discount|coupon|delivery|shipping"
                                  r"|off\s+up\s+to|up\s+to|extra)\W*(?:of\W*)?(?:from\W*)?$", re.I)
NOT_THE_PRICE_AFTER = re.compile(r"^\W*(?:off|/\s*month|per\s+month|a\s+month|cashback|discount|emi)", re.I)
_STOP = frozenset("the a an of for in on at to and price cost new latest buy best".split())


def _folder() -> Path:
    return get_settings().tool_data_dir / "watchers"


def _sandbox(module: str, function: str, args: dict, context: dict) -> dict:
    from .tools import sandbox

    settings = get_settings()
    base = {"user_id": context.pop("user_id"), "data_dir": str(settings.tool_data_dir / context.pop("tool")),
            "timezone": settings.timezone}
    return sandbox.SubprocessSandbox().run(ToolRequest(module=module, function=function, args=args, context={**base, **context}),
                                           sandbox.NETWORK_TIMEOUT_SECONDS)


def amounts(text: str, *, prices_only: bool = False) -> list[tuple[int, float]]:
    """(position, value) of every amount of money in `text`; Indian groupings like 1,29,900 included.
    `prices_only` leaves out list prices (M.R.P.), discounts, instalments and delivery charges."""
    found = []
    for match in MONEY.finditer(text):
        raw = (match.group(1) or match.group(2)).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        before = text[max(0, match.start() - 24):match.start()].rsplit("\n", 1)[-1]  # the words on its own line
        after = text[match.end():match.end() + 14].split("\n", 1)[0]
        if prices_only and (NOT_THE_PRICE_BEFORE.search(before) or NOT_THE_PRICE_AFTER.search(after)):
            continue
        if value > 0:
            found.append((match.start(), value))
    return found


def price_for(text: str, what: str) -> float | None:
    """The first amount after the first mention of `what`'s most telling word (else the first
    amount on the page), or None when the page shows no amounts."""
    found = amounts(text, prices_only=True) or amounts(text)
    if not found:
        return None
    words = sorted((w for w in re.findall(r"[\w']+", what.lower()) if w not in _STOP and len(w) > 1), key=len, reverse=True)
    lower = text.lower()
    anchor = next((lower.find(w) for w in words if lower.find(w) >= 0), -1)
    if anchor >= 0:
        after = [value for position, value in found if anchor <= position <= anchor + 3000]
        if after:
            return after[0]
    return found[0][1]


def fingerprint(text: str, what: str) -> str:
    words = [w for w in re.findall(r"[\w']+", what.lower()) if w not in _STOP and len(w) > 2]
    lines = [line for line in text.lower().splitlines() if any(w in line for w in words)] if words else []
    basis = "\n".join(lines) if lines else text.lower()
    basis = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b|\b\d+\s+(?:seconds?|minutes?|hours?)\s+ago\b", "", basis)
    return hashlib.sha256(basis.encode()).hexdigest()


def _money(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _check_page(row) -> tuple[str | None, str | None, str]:  # noqa: ANN001 - sqlite3.Row
    """(alert title or None, alert body, the value to remember)."""
    snap = _sandbox("app.tools.web_tools", "page_snapshot", {"url": row["url"]},
                    {"user_id": row["user_id"], "tool": "web", "allow_hosts": ["*"]})
    text, condition, what = snap["text"], row["condition"], row["what"]
    if condition in ("below", "above"):
        value = price_for(text, what)
        if value is None:
            raise RuntimeError("no price or amount could be found on the page")
        hit = value < row["value"] if condition == "below" else value > row["value"]
        if hit:
            return (f"{what}: now {_money(value)}",
                    f"That's {condition} the {_money(row['value'])} you asked about. {row['url']}", str(value))
        return None, None, str(value)
    if condition == "changes":
        mark = fingerprint(text, what)
        if row["last_value"] and row["last_value"] != mark:
            return f"{what}: the page changed", row["url"], mark
        return None, None, mark
    present = row["text"].lower() in text.lower()
    if (condition == "appears" and present) or (condition == "disappears" and not present):
        verb = "is on" if condition == "appears" else "is gone from"
        return f"{what}: “{row['text']}” {verb} the page now", row["url"], "yes" if present else "no"
    return None, None, "yes" if present else "no"


def _check_email(row) -> list[tuple[str, str]] | tuple[str, list[str]]:  # noqa: ANN001
    """New emails from the sender since the last check, as (title, body) alerts, and the ids seen."""
    user_id = row["user_id"]
    if not google_account.status(user_id)["connected"]:
        raise RuntimeError("Google isn't connected, so Gmail can't be checked")
    with db.connect() as conn:
        tool = conn.execute("SELECT id FROM tools WHERE name = 'gmail'").fetchone()
        if not permissions.check(user_id, tool["id"], "read", conn=conn):
            raise RuntimeError("the Gmail · read switch is off")
    query = f"from:{row['text']} newer_than:3d" + (f" subject:({row['subject']})" if row["subject"] else "")
    result = _sandbox("app.tools.google_tools", "search_messages", {"query": query, "limit": 10},
                      {"user_id": user_id, "tool": "gmail", "access_token": google_account.access_token(user_id),
                       "allow_hosts": ["gmail.googleapis.com"]})
    messages = result.get("messages", [])
    seen = watchers_tool.seen_ids(row)
    ids = [m.get("id") for m in messages if m.get("id")]
    fresh = [m for m in messages if m.get("id") not in seen] if row["last_checked_at"] else []
    alerts = [(f"New email from {m.get('from') or row['text']}", m.get("subject") or "(no subject)") for m in fresh[:3]]
    return alerts, (ids + seen)[:100]


def check_due(now: datetime | None = None, limit: int = 20) -> int:
    """Check every watch that's due. Returns how many alerts were sent."""
    if not (_folder() / "watchers.db").exists():
        return 0
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime(db.DB_TIME_FORMAT)
    conn = watchers_tool.connect(_folder())
    try:
        due = conn.execute("SELECT * FROM watches WHERE status = 'active' AND next_check_at <= ? ORDER BY next_check_at LIMIT ?",
                           (stamp, limit)).fetchall()
    finally:
        conn.close()
    sent = 0
    for row in due:
        status, fired, failures, remember = "active", None, 0, row["last_value"]
        try:
            if row["kind"] == "page":
                title, body, remember = _check_page(row)
                alerts = [(title, body)] if title else []
                if alerts and row["condition"] in ("below", "above", "appears", "disappears"):
                    status = "done"  # it happened: stop watching
            else:
                alerts, ids = _check_email(row)
                remember = json.dumps(ids)
        except Exception as exc:  # noqa: BLE001 - recorded; the watch stops after MAX_FAILURES
            failures = row["failures"] + 1
            alerts = []
            log.info("watch %s couldn't be checked: %s", row["id"], exc)
            if failures >= MAX_FAILURES:
                status = "failing"
                notifications.add(row["user_id"], "watch", f"I stopped watching {row['what']}",
                                  f"It couldn't be checked {failures} times in a row: {str(exc)[:200]}", f"watch:{row['id']}")
        for title, body in alerts:
            notifications.add(row["user_id"], "watch", title, body, f"watch:{row['id']}")
            fired, sent = stamp, sent + 1
        conn = watchers_tool.connect(_folder())
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE watches SET status = ?, last_value = ?, last_checked_at = ?, next_check_at = ?, failures = ?,
                      fired_at = COALESCE(?, fired_at) WHERE id = ?
                    """,
                    (status, remember, stamp, (now + timedelta(minutes=row["every_minutes"])).strftime(db.DB_TIME_FORMAT),
                     failures, fired, row["id"]),
                )
        finally:
            conn.close()
    return sent


def list_for(user_id: int) -> list[dict]:
    if not (_folder() / "watchers.db").exists():
        return []
    conn = watchers_tool.connect(_folder())
    try:
        rows = conn.execute("SELECT * FROM watches WHERE user_id = ? AND status IN ('active', 'failing') ORDER BY id DESC",
                            (user_id,)).fetchall()
    finally:
        conn.close()
    return [watchers_tool.describe(r) for r in rows]


def stop(user_id: int, watch_id: int) -> bool:
    if not (_folder() / "watchers.db").exists():
        return False
    conn = watchers_tool.connect(_folder())
    try:
        with conn:
            return conn.execute("UPDATE watches SET status = 'stopped' WHERE id = ? AND user_id = ? AND status IN ('active', 'failing')",
                                (watch_id, user_id)).rowcount > 0
    finally:
        conn.close()
