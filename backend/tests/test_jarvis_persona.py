"""Jarvis: the manner of spoken turns from the PC, the system status report, and the HUD feed."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

import pytest

from app import desktop, fastpath, hud, memory, orchestrator, permissions, persona, settings_store
from app.config import get_settings
from app.model_backends import ModelResponse, set_backend
from app.model_backends.base import ModelBackend
from app.tools import calendar_tool

STATS = {"cpu_percent": 12.0, "memory": {"used_percent": 61.0, "total_gb": 16.0},
         "battery": {"percent": 84, "charging": True}, "disks": [{"drive": "C:\\", "free_gb": 120.5, "total_gb": 476.0}],
         "network": {"online": True}, "gpu": {"percent": 3}, "top_apps": [{"app": "chrome", "cpu_percent": 6}]}


class Recorder(ModelBackend):
    name = "recorder"
    model_id = "recorder-1"

    def __init__(self, reply: str = "Certainly.") -> None:
        self.reply, self.prompts = reply, []

    def generate(self, messages, tools=None):
        self.prompts.append(messages[0]["content"])
        return ModelResponse(self.reply, [], self.model_id)


@pytest.fixture
def pc(user_id, tool_ids):
    desktop.reset()
    stop = threading.Event()
    answers = {"system_info": STATS, "open": {"opened": "chrome", "kind": "app"}}

    def loop():
        while not stop.is_set():
            action = desktop.next_action(user_id, "Test PC", wait=0.3)
            if action:
                desktop.finish(user_id, action["id"], True, answers.get(action["function"], {"done": True}))

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    desktop.touch(user_id, "Test PC")
    for scope in ("read", "write"):
        permissions.set_granted(user_id, tool_ids["pc"], scope, True)
    yield
    stop.set()
    thread.join(timeout=2)  # it may still be polling: reset only once it's done, or the next test sees a "connected" PC
    desktop.reset()


@pytest.mark.parametrize("reply, dressed", [
    ("Opened Chrome.", "Opened Chrome, sir."),
    ("Volume set to 30%.", "Volume set to 30%, sir."),
    ("Locked", "Locked, sir."),
    ("Skipped to the next track - now playing Kalyani.", "Skipped to the next track - now playing Kalyani, sir."),
    ("All systems normal. Processor at 12%.", "All systems normal, sir. Processor at 12%."),
    ("Yes, sir. Opened Chrome.", "Yes, sir. Opened Chrome."),  # already addressed
    ("Here are your reminders:\n• Pay rent", "Here are your reminders:\n• Pay rent"),  # a list stays as it is
])
def test_short_replies_get_the_address(reply, dressed):
    assert persona.dress(reply, "sir") == dressed


def test_the_status_report_reads_like_jarvis_and_warns_only_when_needed():
    assert persona.status_reply(STATS, "sir") == (
        "All systems normal, sir. Processor at 12%, memory at 61%, graphics at 3%, battery 84% and charging. "
        "120.5 GB free on C:. Online.")
    hot = {**STATS, "cpu_percent": 97, "battery": {"percent": 14, "charging": False},
           "top_apps": [{"app": "chrome", "cpu_percent": 71}]}
    said = persona.status_reply(hot)
    assert said.startswith("A few things need attention: the processor is running hot, the battery is low.")
    assert said.endswith("chrome is the busiest app.")


@pytest.mark.parametrize("said", ["system status", "status report", "run diagnostics", "how's my pc", "how is the computer doing",
                                  "Jarvis, system report please", "are all systems ok"])
def test_status_requests_take_the_instant_path(said):
    intent = fastpath.match(said)
    assert intent is not None and intent.function == "pc_system_info"
    assert intent.reply(STATS).startswith("All systems normal.")


def test_a_spoken_request_to_the_pc_is_answered_as_jarvis(user_id, pc):
    out = orchestrator.handle(user_id, "system status", origin="desktop", voice=True)
    assert out["reply"].startswith("All systems normal, sir.") and out["used"]["checks"]["status"] == "passed"
    out = orchestrator.handle(user_id, "open chrome", origin="desktop", voice=True)
    assert out["reply"] == "Opened Chrome, sir."
    backend = Recorder()
    set_backend(backend)
    orchestrator.handle(user_id, "tell me a joke", origin="desktop", voice=True)
    assert "speaking out loud as Jarvis" in backend.prompts[-1] and "“sir”" in backend.prompts[-1]


def test_typed_chats_and_the_plain_persona_keep_custodians_own_voice(user_id, pc):
    assert orchestrator.handle(user_id, "open chrome", origin="app")["reply"] == "Opened Chrome."
    assert orchestrator.handle(user_id, "open chrome", origin="desktop")["reply"] == "Opened Chrome."  # typed, not spoken
    settings_store.update(user_id, persona="plain")
    assert orchestrator.handle(user_id, "open chrome", origin="desktop", voice=True)["reply"] == "Opened Chrome."
    settings_store.update(user_id, persona="jarvis", address="boss")
    assert orchestrator.handle(user_id, "open chrome", origin="desktop", voice=True)["reply"] == "Opened Chrome, boss."
    with pytest.raises(settings_store.SettingError):
        settings_store.update(user_id, address="<script>")


def test_the_hud_shows_today_and_what_was_done_never_message_text(user_id, tool_ids, pc):
    memory.write(user_id, "Lives in Pune.", "semantic")
    folder = get_settings().tool_data_dir / "calendar"
    folder.mkdir(parents=True, exist_ok=True)
    later = (datetime.now() + timedelta(hours=1)).replace(second=0, microsecond=0)
    if later.date() != datetime.now().date():
        later = datetime.now().replace(hour=23, minute=59, second=0, microsecond=0)
    calendar_tool.create_event({"user_id": user_id, "data_dir": str(folder), "timezone": "Asia/Kolkata"},
                               "Design review", later.isoformat(), 30, "https://meet.google.com/abc-defg-hij")
    from app.tools import sandbox
    from tests.test_routines_activity import _message

    msg = _message(user_id)
    sandbox.execute(user_id, "pc_send_message", {"contact": "Tanishka", "text": "secret plans"}, message_id=msg, allowed=None)
    snap = hud.snapshot(user_id)
    assert snap["city"] == "Pune" and snap["address"] == "sir" and snap["persona"] == "jarvis"
    assert snap["events"] == []  # Calendar · read is off: nothing is read
    permissions.set_granted(user_id, tool_ids["calendar"], "read", True)
    snap = hud.snapshot(user_id)
    assert [e["title"] for e in snap["events"]] == ["Design review"] and snap["events"][0]["link"].startswith("https://meet")
    assert snap["activity"][0]["label"] == "send message · Tanishka" and "secret" not in str(snap)
    settings_store.update(user_id, home_city="Mumbai")
    assert hud.snapshot(user_id)["city"] == "Mumbai"


def test_the_hud_route_needs_a_session(client, anon_client):
    assert anon_client.get("/hud").status_code == 401
    body = client.get("/hud").json()
    assert {"events", "reminders", "activity", "address", "city", "unread"} <= set(body)
