"""Every tool function the model can call, through the real gate and sandbox: blocked while its
switch is off, working once it's on. A new function in app/tools/registry.py without a case
here fails test_every_registered_function_is_checked."""

import base64
from datetime import datetime, timedelta

import pytest

from app import db, documents, permissions
from app.crypto import encrypt
from app.tools import registry, sandbox

# Far enough ahead to be in the future whatever the machine's and the user's time zones are.
SOON = (datetime.now() + timedelta(days=2)).replace(second=0, microsecond=0).isoformat()

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
    ("routines_save_routine", {"name": "Work mode", "steps": ["open vs code", "volume 30", "do not disturb on"]},
     lambda r: r["saved"]["steps"] == ["open vs code", "volume 30", "do not disturb on"]),
    ("routines_list_routines", {}, lambda r: r["routines"][0]["name"] == "Work mode"),
    ("routines_delete_routine", {"name": "work mode"}, lambda r: r["deleted"] == "work mode"),
    ("pc_search_file_contents", {"query": "rent agreement"}, lambda r: r["files"] == [] and "note" in r),
    ("watchers_watch_page", {"url": "https://www.flipkart.com/p/123", "what": "Pixel 9", "condition": "below", "value": 50000},
     lambda r: r["watching"]["what"] == "Pixel 9"),
    ("watchers_watch_email", {"sender": "priya@example.com"}, lambda r: r["watching"]["what"] == "an email from priya@example.com"),
    ("watchers_list_watches", {}, lambda r: len(r["watches"]) == 2),
    ("watchers_stop_watch", {"id": 1}, lambda r: r["stopped"] == 1),
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
# Spotify, Outlook and Home Assistant need their accounts too: tests/test_integrations.py runs
# them against local fakes.
ACCOUNTS = {
    "spotify_search": ({"query": "Kesariya"}, "Spotify isn't connected"),
    "spotify_now_playing": ({}, "Spotify isn't connected"),
    "spotify_play": ({"query": "Kesariya"}, "Spotify isn't connected"),
    "spotify_control": ({"action": "next"}, "Spotify isn't connected"),
    "outlook_search_mail": ({"query": "invoice"}, "Microsoft 365 isn't connected"),
    "outlook_read_mail": ({"id": "AAMk1"}, "Microsoft 365 isn't connected"),
    "outlook_list_events": ({}, "Microsoft 365 isn't connected"),
    "outlook_create_event": ({"title": "Review", "start": "2030-01-02T11:00"}, "Microsoft 365 isn't connected"),
    "home_list_devices": ({}, "Home Assistant isn't connected"),
    "home_control": ({"entity_id": "light.bedroom", "action": "turn_off"}, "Home Assistant isn't connected"),
}


def test_every_registered_function_is_checked():
    assert {f.exposed_name for f in registry.FUNCTIONS} == ({name for name, _, _ in LOCAL} | set(GOOGLE) | set(PC) | WEB
                                                            | set(ACCOUNTS) | SCREEN)


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


@pytest.mark.parametrize("name", sorted(ACCOUNTS))
def test_account_tools_are_blocked_without_their_account(user_id, tool_ids, message_id, name):
    fn = registry.find(name)
    args, error = ACCOUNTS[name]
    permissions.set_granted(user_id, tool_ids[fn.tool], fn.scope, True)
    out = sandbox.execute(user_id, name, args, message_id=message_id, allowed=None)
    assert out.status == "denied" and error in out.error, out.error


# The web --------------------------------------------------------------------------------------

import json  # noqa: E402
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402

from app import desktop  # noqa: E402


class _FakeSearx(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"results": [{"title": "Canberra - Wikipedia", "url": "https://en.wikipedia.org/wiki/Canberra",
                                        "content": "Canberra is the capital city of Australia."}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def searx(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _FakeSearx)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("SEARXNG_URL", f"http://127.0.0.1:{server.server_port}")
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_web_search_reaches_only_the_search_engine_and_pages_only_the_public_internet(user_id, tool_ids, message_id, searx):
    assert sandbox.execute(user_id, "web_search", {"query": "capital of australia"}, message_id=message_id, allowed=None).status == "denied"
    permissions.set_granted(user_id, tool_ids["web"], "read", True)
    found = sandbox.execute(user_id, "web_search", {"query": "capital of australia"}, message_id=message_id, allowed=None)
    assert found.status == "succeeded", found.error
    assert found.result["results"][0]["url"] == "https://en.wikipedia.org/wiki/Canberra"
    local = sandbox.execute(user_id, "web_read_page", {"url": searx + "/"}, message_id=message_id, allowed=None)
    assert local.status == "failed" and "public internet" in local.error  # never this machine or the local network


# The PC, through the desktop app ----------------------------------------------------------------

PC = {
    "pc_system_info": {}, "pc_list_files": {"path": "~/Downloads"}, "pc_find_files": {"query": "invoice"},
    "pc_read_file": {"path": "~/notes.txt"}, "pc_list_windows": {}, "pc_get_clipboard": {}, "pc_screenshot": {},
    "pc_open": {"target": "notepad"}, "pc_media": {"action": "play_pause"}, "pc_set_volume": {"level": 30},
    "pc_lock_screen": {}, "pc_focus_window": {"title": "Chrome"}, "pc_close_window": {"title": "Notepad"},
    "pc_type_text": {"text": "hello"}, "pc_press_keys": {"keys": "ctrl+s"}, "pc_click": {"x": 10, "y": 20},
    "pc_write_file": {"path": "~/a.txt", "content": "hi"}, "pc_move_file": {"source": "~/a.txt", "destination": "~/b.txt"},
    "pc_delete_file": {"path": "~/b.txt"}, "pc_set_clipboard": {"text": "hi"},
    "pc_go_to": {"query": "Jägermeister Wikipedia"}, "pc_search_site": {"site": "amazon", "query": "earbuds"},
    "pc_play": {"query": "Kalyani"}, "pc_now_playing": {}, "pc_set_brightness": {"level": 60},
    "pc_set_theme": {"mode": "dark"}, "pc_open_settings": {"page": "bluetooth"}, "pc_timer": {"seconds": 300},
    "pc_list_apps": {}, "pc_close_app": {"name": "chrome"}, "pc_copy_file": {"source": "~/a.txt", "destination": "~/Desktop"},
    "pc_create_folder": {"path": "~/Desktop/Trips"}, "pc_organize_folder": {"path": "~/Downloads"},
    "pc_write_in_app": {"text": "Dear Sir,"}, "pc_read_screen": {}, "pc_click_element": {"name": "Send"},
    "pc_fill_field": {"name": "Search", "text": "hello"}, "pc_send_message": {"contact": "Tanishka", "text": "on my way"}, "pc_notifications": {"hours": 24, "app": "whatsapp"},
    "pc_do_not_disturb": {"on": True}, "pc_alarm": {"time": "07:00"}, "pc_say": {"text": "Good morning"},
    "pc_confirm_plan": {"title": "Run “Work mode”", "steps": ["Open vs code"]},
    "pc_browser_open": {"url": "https://www.irctc.co.in"}, "pc_browser_read": {}, "pc_browser_click": {"ref": "3"},
    "pc_browser_fill": {"name": "Search", "text": "earbuds"}, "pc_browser_download": {"url": "https://example.com/a.pdf"},
    "pc_browser_tabs": {},
}
WEB = {"web_search", "web_read_page"}
SCREEN = {"pc_look", "pc_click_on"}  # with the vision model: tests/test_vision.py


@pytest.fixture
def fake_desktop(user_id):
    """Plays the desktop app: answers every action, and records what it was asked."""
    desktop.reset()
    seen: list[dict] = []
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            action = desktop.next_action(user_id, "Test PC", wait=0.5)
            if action:
                seen.append(action)
                if action["function"] == "screenshot":
                    desktop.finish(user_id, action["id"], True, {"path": "~/Pictures/shot.png", "image_b64": "aGk="})
                else:
                    desktop.finish(user_id, action["id"], True, {"done": action["function"]})

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    desktop.touch(user_id, "Test PC")
    yield seen
    stop.set()
    thread.join(timeout=2)
    desktop.reset()


def test_every_pc_action_goes_through_the_gate_to_the_desktop_app(user_id, tool_ids, message_id, fake_desktop):
    for name, args in PC.items():
        fn = registry.find(name)
        permissions.set_granted(user_id, tool_ids["pc"], fn.scope, False)
        assert sandbox.execute(user_id, name, args, message_id=message_id, allowed=None).status == "denied", name
        permissions.set_granted(user_id, tool_ids["pc"], fn.scope, True)
        out = sandbox.execute(user_id, name, args, message_id=message_id, allowed=None)
        assert out.status == "succeeded", (name, out.error)
    asked = {a["function"]: a for a in fake_desktop}
    assert set(asked) == {name.removeprefix("pc_") for name in PC}
    assert asked["type_text"]["confirm"] is True and asked["open"]["confirm"] is False
    assert asked["delete_file"]["args"] == {"path": "~/b.txt"}
    assert asked["browser_click"]["confirm"] is False  # it asks on the PC only for a risky button (browser.py)
    assert asked["browser_download"]["confirm"] is True


def test_pc_actions_say_so_when_the_desktop_app_isnt_running(user_id, tool_ids, message_id):
    desktop.reset()
    permissions.set_granted(user_id, tool_ids["pc"], "write", True)
    out = sandbox.execute(user_id, "pc_open", {"target": "notepad"}, message_id=message_id, allowed=None)
    assert out.status == "failed" and "desktop app isn't running" in out.error
