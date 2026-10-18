"""Custodian speaking first: announcements on the PC, the morning briefing, meeting heads-ups
and watchers (prices, pages, emails)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import db, desktop, memory, notifications, permissions, proactive, settings_store, watchers
from app.config import get_settings
from app.model_backends import ModelResponse, set_backend
from app.model_backends.base import ModelBackend
from app.tools import calendar_tool, reminders_tool, sandbox, watchers_tool


@pytest.fixture
def pc(user_id):
    """A connected desktop app; returns the notices it was handed."""
    desktop.reset()
    desktop.touch(user_id, "Test PC")
    notices: list[dict] = []
    real_post = desktop.post

    def post(uid, function, args, *, ttl=600):
        notices.append({"function": function, **args})
        return real_post(uid, function, args, ttl=ttl)

    yield type("PC", (), {"notices": notices, "post": staticmethod(post)})
    desktop.reset()


@pytest.fixture
def daytime(monkeypatch):
    """Outside the default quiet hours (23:00-07:00), whatever the real clock says."""
    monkeypatch.setattr(proactive, "_now_local", lambda: datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc))


def test_a_reminder_coming_due_is_also_said_on_the_pc(user_id, pc, monkeypatch, daytime):
    monkeypatch.setattr(desktop, "post", pc.post)
    folder = get_settings().tool_data_dir / "reminders"
    folder.mkdir(parents=True, exist_ok=True)
    ctx = {"user_id": user_id, "data_dir": str(folder), "timezone": "Asia/Kolkata"}
    reminders_tool.create_reminder(ctx, "Call mum", (datetime.now().astimezone() + timedelta(minutes=2)).isoformat())
    assert notifications.deliver_due(datetime.now(timezone.utc) + timedelta(minutes=3)) == 1
    assert pc.notices == [{"function": "announce", "kind": "reminder", "title": "Call mum", "body": pc.notices[0]["body"],
                           "say": "Reminder: Call mum."}]


def test_nothing_is_said_in_quiet_hours_or_when_announcements_are_off(user_id, pc, monkeypatch):
    monkeypatch.setattr(desktop, "post", pc.post)
    monkeypatch.setattr(proactive, "_now_local", lambda: datetime(2026, 10, 18, 23, 30, tzinfo=timezone.utc))
    assert proactive.announce(user_id, "reminder", "Call mum") is False
    monkeypatch.setattr(proactive, "_now_local", lambda: datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc))
    settings_store.update(user_id, announce=False)
    assert proactive.announce(user_id, "reminder", "Call mum") is False
    settings_store.update(user_id, announce=True, quiet_from="09:00", quiet_until="11:00")
    assert proactive.announce(user_id, "reminder", "Call mum") is False
    assert pc.notices == []


def test_a_told_notice_is_delivered_once_and_never_answered(user_id, pc):
    assert desktop.post(user_id, "announce", {"say": "hello"}) is True
    action = desktop.next_action(user_id, "Test PC", wait=1)
    assert action["function"] == "announce" and action["reply"] is False
    assert desktop.finish(user_id, action["id"], True) is False  # nobody waits for it
    assert desktop.next_action(user_id, "Test PC", wait=0.5) is None


def test_an_undelivered_notice_expires(user_id, pc):
    desktop.post(user_id, "announce", {"say": "stale"}, ttl=0.01)
    import time

    time.sleep(0.05)
    assert desktop.next_action(user_id, "Test PC", wait=0.5) is None


def _event(user_id: int, title: str, minutes_from_now: int, location: str | None = None) -> None:
    folder = get_settings().tool_data_dir / "calendar"
    folder.mkdir(parents=True, exist_ok=True)
    start = (datetime.now().astimezone() + timedelta(minutes=minutes_from_now)).replace(second=0, microsecond=0)
    calendar_tool.create_event({"user_id": user_id, "data_dir": str(folder), "timezone": "Asia/Kolkata"}, title,
                               start.isoformat(), 30, location)


def test_a_meeting_gets_one_heads_up_with_its_link(user_id, tool_ids, pc, monkeypatch, daytime):
    monkeypatch.setattr(desktop, "post", pc.post)
    _event(user_id, "Design review", 8, "https://meet.google.com/abc-defg-hij")
    _event(user_id, "Lunch", 90)
    assert proactive.meeting_heads_up() == 0  # the Calendar · read switch is off: nothing is read
    permissions.set_granted(user_id, tool_ids["calendar"], "read", True)
    assert proactive.meeting_heads_up() == 1
    assert proactive.meeting_heads_up() == 0  # once per meeting
    note = notifications.list_for(user_id)[0]
    assert note["kind"] == "meeting" and note["title"].startswith("“Design review” starts in")
    assert pc.notices[0]["link"] == "https://meet.google.com/abc-defg-hij"
    assert pc.notices[0]["say"].endswith("Shall I open the meeting link?")


class Briefer(ModelBackend):
    name, model_id = "briefer", "briefer-1"

    def generate(self, messages, tools=None):
        return ModelResponse("Your day is free, and nothing is due.", [], self.model_id)


def test_the_morning_briefing_greets_by_name_once_a_day(user_id):
    set_backend(Briefer())
    memory.write(user_id, "Their name is Om Choudhari.", "semantic")
    first = proactive.briefing(user_id)
    assert first["text"].endswith("Your day is free, and nothing is due.") and ", Om." in first["text"]
    assert proactive.briefing(user_id) == {"skipped": "already briefed today"}
    assert "text" in proactive.briefing(user_id, force=True)
    settings_store.update(user_id, briefing=False)
    assert proactive.briefing(user_id) == {"skipped": "the morning briefing is switched off"}


# Watchers ---------------------------------------------------------------------------------------

def test_prices_are_read_from_the_page_never_invented():
    page = "Apple iPhone 16 (128 GB) - Black\nM.R.P.: ₹79,900\n₹69,900\nEMI from ₹3,389\nBank offer ₹4,000 off"
    assert [v for _, v in watchers.amounts("₹1,29,900 or Rs. 450.50 or 300 rupees")] == [129900.0, 450.5, 300.0]
    assert watchers.price_for(page, "iPhone 16 price") == 69900.0  # not the M.R.P., the EMI or the offer
    assert watchers.price_for("iPhone 16 ₹58,999", "iPhone 16") == 58999.0  # "16" is the model, not a price
    assert watchers.price_for("no numbers here", "iPhone") is None


def test_a_page_fingerprint_ignores_clocks_and_looks_at_what_was_named():
    one = "Concert tickets: sold out\nUpdated 10:31\nAd: buy shoes"
    two = "Concert tickets: sold out\nUpdated 10:47\nAd: buy a car"
    assert watchers.fingerprint(one, "concert tickets") == watchers.fingerprint(two, "concert tickets")
    assert watchers.fingerprint(one, "concert tickets") != watchers.fingerprint("Concert tickets: on sale", "concert tickets")


def _watch(user_id: int, tool_ids, function: str, args: dict) -> dict:
    with db.connect() as conn:
        conv = conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (user_id,)).lastrowid
        from app.crypto import encrypt

        message = conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', ?)",
                               (conv, encrypt(""))).lastrowid
    permissions.set_granted(user_id, tool_ids["watchers"], "write", True)
    out = sandbox.execute(user_id, function, args, message_id=message, allowed=None)
    assert out.status == "succeeded", out.error
    return out.result


def _due_now() -> None:
    conn = watchers_tool.connect(get_settings().tool_data_dir / "watchers")
    with conn:
        conn.execute("UPDATE watches SET next_check_at = datetime('now', '-1 minute')")
    conn.close()


def test_a_price_watch_tells_once_when_the_price_drops_below(user_id, tool_ids, pc, monkeypatch, daytime):
    monkeypatch.setattr(desktop, "post", pc.post)
    made = _watch(user_id, tool_ids, "watchers_watch_page",
                  {"url": "https://www.amazon.in/dp/B0DGHYDZR9", "what": "iPhone 16", "condition": "below", "value": 60000})
    assert made["watching"]["rule"] == "tell you when it's below 60000"
    prices = iter(["iPhone 16 ₹69,900", "iPhone 16 ₹58,999"])
    monkeypatch.setattr(watchers, "_sandbox", lambda *a, **k: {"text": next(prices)})
    assert watchers.check_due() == 0
    assert watchers.list_for(user_id)[0]["last_seen"] == "69900.0"
    _due_now()
    assert watchers.check_due() == 1
    assert notifications.list_for(user_id)[0]["title"] == "iPhone 16: now 58,999"
    assert pc.notices[-1]["kind"] == "watch"
    assert watchers.list_for(user_id) == []  # done: it happened


def test_a_page_that_cant_be_read_stops_being_watched_after_six_tries(user_id, tool_ids, monkeypatch):
    _watch(user_id, tool_ids, "watchers_watch_page",
           {"url": "https://example.com/tickets", "what": "tickets", "condition": "appears", "text": "On sale"})

    def blocked(*_a, **_k):
        raise RuntimeError("the site answered 503")

    monkeypatch.setattr(watchers, "_sandbox", blocked)
    for _ in range(watchers.MAX_FAILURES):
        _due_now()
        watchers.check_due()
    assert watchers.list_for(user_id)[0]["status"] == "failing"
    assert notifications.list_for(user_id)[0]["title"] == "I stopped watching tickets"


def test_an_email_watch_announces_only_new_mail(user_id, tool_ids, monkeypatch):
    _watch(user_id, tool_ids, "watchers_watch_email", {"sender": "priya@example.com"})
    monkeypatch.setattr(watchers.google_account, "status", lambda uid: {"connected": True})
    monkeypatch.setattr(watchers.google_account, "access_token", lambda uid: "token")
    permissions.set_granted(user_id, tool_ids["gmail"], "read", True)
    inbox = [[{"id": "m1", "from": "Priya", "subject": "Old"}],
             [{"id": "m2", "from": "Priya", "subject": "Dinner on Friday?"}, {"id": "m1", "from": "Priya", "subject": "Old"}]]
    monkeypatch.setattr(watchers, "_sandbox", lambda *a, **k: {"messages": inbox.pop(0)})
    assert watchers.check_due() == 0  # what was already there doesn't count
    _due_now()
    assert watchers.check_due() == 1
    note = notifications.list_for(user_id)[0]
    assert note["title"] == "New email from Priya" and note["body"] == "Dinner on Friday?"


def test_watches_need_their_switch_and_can_be_stopped_from_the_app(client, tool_ids):
    made = client.post("/chat", json={"message": "hello"})
    assert made.status_code == 200
    assert client.get("/watches").json() == []
    assert client.delete("/watches/99").status_code == 404


def test_the_watch_tool_refuses_a_rule_without_its_number():
    with pytest.raises(ValueError, match="needs the number"):
        watchers_tool.watch_page({"user_id": 1, "data_dir": str(Path(get_settings().tool_data_dir))}, "https://x.com", "x", "below")
