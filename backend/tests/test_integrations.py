"""Spotify, Outlook and Home Assistant through the real sandbox against local fakes; allowing a PC
action from the app or Telegram; Telegram itself; and the phone as a remote."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app import accounts, approvals, db, desktop, notifications, settings_store, telegram
from app.crypto import encrypt
from app.tools import registry, sandbox
from app.tools.base import ToolFailure, ToolRequest


class _Fake(BaseHTTPRequestHandler):
    """Answers from the server's `routes`: {(method, path): body}; records every request."""

    def _answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.seen.append((self.command, self.path, self.headers.get("Authorization"), body))
        if self.headers.get("Authorization") != f"Bearer {self.server.token}":
            self.send_response(401)
            self.end_headers()
            return
        found = self.server.routes.get((self.command, urlsplit(self.path).path))
        if found is None:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(found).encode() if found != "" else b""
        self.send_response(200 if data else 204)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = _answer

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_service():
    servers = []

    def make(token: str, routes: dict):
        server = HTTPServer(("127.0.0.1", 0), _Fake)
        server.token, server.routes, server.seen = token, routes, []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server, f"http://127.0.0.1:{server.server_port}"

    yield make
    for server in servers:
        server.shutdown()


def _run(module: str, function: str, args: dict, ctx: dict):
    return sandbox.SubprocessSandbox().run(ToolRequest(module=module, function=function, args=args, context=ctx), 20)


@pytest.fixture
def message_id(user_id):
    with db.connect() as conn:
        conv = conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (user_id,)).lastrowid
        return conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', ?)",
                            (conv, encrypt(""))).lastrowid


# Connecting accounts ---------------------------------------------------------------------------

@pytest.fixture
def spotify_app(monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "shh")

    def handler(request):
        if request.url.path == "/api/token":
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                assert form["code_verifier"]  # PKCE
                return httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600})
            return httpx.Response(200, json={"access_token": "at-2", "expires_in": 3600})
        if request.url.path == "/v1/me":
            return httpx.Response(200, json={"display_name": "Om", "id": "om11"})
        return httpx.Response(404)

    monkeypatch.setattr(accounts, "_transport", httpx.MockTransport(handler))


def test_connecting_spotify_stores_encrypted_tokens_and_refreshes_them(user_id, client, spotify_app):
    start = client.get("/integrations/spotify/start").json()["url"]
    query = parse_qs(urlsplit(start).query)
    assert query["code_challenge_method"] == ["S256"] and "user-modify-playback-state" in query["scope"][0]
    page = client.get("/integrations/spotify/callback", params={"code": "c-1", "state": query["state"][0]})
    assert "Spotify is connected" in page.text and "as Om" in page.text
    listed = {i["provider"]: i for i in client.get("/integrations").json()}
    assert listed["spotify"]["connected"] and listed["spotify"]["account"] == "Om"
    assert not listed["microsoft"]["connected"] and not listed["telegram"]["connected"]
    with db.connect() as conn:
        assert b"rt-1" not in bytes(conn.execute("SELECT refresh_token FROM integrations").fetchone()[0])
    assert accounts.access_token("spotify", user_id) == "at-1"
    with db.connect() as conn:
        conn.execute("UPDATE integrations SET expires_at = '2000-01-01 00:00:00'")
    assert accounts.access_token("spotify", user_id) == "at-2"
    assert "spotify" in accounts.connected(user_id)
    assert client.delete("/integrations/spotify").status_code == 204
    assert "spotify" not in accounts.connected(user_id)


def test_a_forged_callback_and_an_unconfigured_provider_are_refused(client, spotify_app):
    assert "isn&#x27;t valid" in client.get("/integrations/spotify/callback", params={"code": "c", "state": "forged"}).text
    assert client.get("/integrations/microsoft/start").status_code == 409  # no MICROSOFT_CLIENT_ID
    assert client.get("/integrations/dropbox/start").status_code == 422


def test_account_tools_are_offered_only_once_connected(user_id, spotify_app):
    from app import orchestrator
    from app.model_backends import set_backend
    from app.model_backends.base import ModelResponse

    seen = []

    class Look:
        model_id = "look"

        def close(self):
            pass

        def generate(self, messages, tools=None):
            seen.append({t["function"]["name"] for t in tools or []})
            return ModelResponse(content="OK.", tool_calls=[])

    set_backend(Look())
    orchestrator.handle(user_id, "play some music on spotify please")
    assert not any(name.startswith("spotify_") for name in seen[-1])
    with db.connect() as conn:
        conn.execute("INSERT INTO integrations (user_id, provider, scopes, refresh_token) VALUES (?, 'spotify', 's', ?)",
                     (user_id, encrypt("rt")))
    orchestrator.handle(user_id, "play some music on spotify please")
    assert {"spotify_play", "spotify_control"} <= seen[-1]


# The tools, in the sandbox ---------------------------------------------------------------------

def test_spotify_plays_the_exact_song_it_finds(tmp_path, fake_service):
    track = {"name": "Kesariya", "uri": "spotify:track:abc123", "artists": [{"name": "Arijit Singh"}]}
    server, base = fake_service("at-test", {
        ("GET", "/search"): {"tracks": {"items": [track]}},
        ("PUT", "/me/player/play"): "",
        ("GET", "/me/player"): {"is_playing": True, "item": track, "device": {"name": "OM-PC", "volume_percent": 40}},
        ("PUT", "/me/player/volume"): "",
    })
    ctx = {"user_id": 1, "data_dir": str(tmp_path), "timezone": "UTC", "access_token": "at-test",
           "allow_hosts": ["127.0.0.1"], "api_base": base}
    played = _run("app.tools.spotify_tool", "play", {"query": "Kesariya", "kind": "track", "uri": None}, ctx)
    assert played["playing"] == {"name": "Kesariya", "uri": "spotify:track:abc123", "kind": "track", "by": "Arijit Singh"}
    assert json.loads(server.seen[-1][3]) == {"uris": ["spotify:track:abc123"]}
    now = _run("app.tools.spotify_tool", "now_playing", {}, ctx)
    assert now["playing"] and now["device"] == "OM-PC" and now["by"] == "Arijit Singh"
    assert _run("app.tools.spotify_tool", "control", {"action": "volume", "volume": 25}, ctx) == {"volume": 25}
    assert "volume_percent=25" in server.seen[-1][1]
    with pytest.raises(ToolFailure, match="not allowed"):  # no other host
        _run("app.tools.spotify_tool", "now_playing", {}, {**ctx, "api_base": "http://example.com"})


def test_outlook_mail_and_calendar(tmp_path, fake_service):
    server, base = fake_service("at-ms", {
        ("GET", "/me/messages"): {"value": [{"id": "AAMk1", "subject": "Invoice 42", "isRead": False,
                                             "receivedDateTime": "2026-10-17T09:00:00Z", "bodyPreview": "Please pay",
                                             "from": {"emailAddress": {"name": "Acme", "address": "bills@acme.example"}}}]},
        ("GET", "/me/messages/AAMk1"): {"subject": "Invoice 42", "body": {"contentType": "html", "content": "<p>Pay <b>5000</b></p>"},
                                        "from": {"emailAddress": {"name": "Acme", "address": "bills@acme.example"}},
                                        "toRecipients": [{"emailAddress": {"address": "om@example.com"}}]},
        ("POST", "/me/events"): {"id": "ev1", "subject": "Review", "start": {"dateTime": "2030-01-02T11:00:00"},
                                 "end": {"dateTime": "2030-01-02T12:00:00"},
                                 "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/x"}},
    })
    ctx = {"user_id": 1, "data_dir": str(tmp_path), "timezone": "Asia/Kolkata", "access_token": "at-ms",
           "allow_hosts": ["127.0.0.1"], "api_base": base}
    found = _run("app.tools.outlook_tool", "search_mail", {"query": "invoice", "unread_only": False, "limit": 5}, ctx)
    assert found["messages"][0]["from"] == "Acme <bills@acme.example>" and found["messages"][0]["unread"]
    assert "%24search=%22invoice%22" in server.seen[-1][1]
    read = _run("app.tools.outlook_tool", "read_mail", {"id": "AAMk1"}, ctx)
    assert "Pay 5000" in " ".join(read["text"].split()) and "<b>" not in read["text"]
    made = _run("app.tools.outlook_tool", "create_event", {"title": "Review", "start": "2030-01-02T11:00",
                                                          "duration_minutes": 60, "location": None, "online_meeting": True}, ctx)
    assert made["created"]["link"].startswith("https://teams.microsoft.com")
    sent = json.loads(server.seen[-1][3])
    assert sent["start"] == {"dateTime": "2030-01-02T11:00:00", "timeZone": "Asia/Kolkata"} and sent["isOnlineMeeting"]


def test_home_assistant_is_reached_at_its_own_address_through_the_gate(user_id, tool_ids, message_id, fake_service, grant):
    light = {"entity_id": "light.bedroom", "state": "on", "attributes": {"friendly_name": "Bedroom light", "brightness": 255}}
    server, base = fake_service("ha-" + "t" * 30, {
        ("GET", "/api/"): {"message": "API running."},
        ("GET", "/api/states"): [light, {"entity_id": "lock.front_door", "state": "locked", "attributes": {}},
                                 {"entity_id": "climate.ac", "state": "cool", "attributes": {"friendly_name": "Hall AC",
                                                                                              "temperature": 24}}],
        ("POST", "/api/services/light/turn_on"): [],
        ("GET", "/api/states/light.bedroom"): {**light, "attributes": {**light["attributes"], "brightness": 76}},
    })
    with pytest.raises(accounts.AccountError, match="didn't accept"):
        accounts.connect_home(user_id, base, "wrong-" + "x" * 30)
    assert accounts.connect_home(user_id, base + "/", "ha-" + "t" * 30) == {"account": base}
    grant("home", "read")
    listed = sandbox.execute(user_id, "home_list_devices", {}, message_id=message_id, allowed=None)
    assert listed.status == "succeeded", listed.error
    assert [d["id"] for d in listed.result["devices"]] == ["light.bedroom", "climate.ac"]  # never the lock
    assert listed.result["devices"][0]["brightness"] == 100
    grant("home", "write")
    dimmed = sandbox.execute(user_id, "home_control", {"entity_id": "light.bedroom", "action": "set_brightness", "value": 30},
                             message_id=message_id, allowed=None)
    assert dimmed.status == "succeeded", dimmed.error
    assert dimmed.result["device"]["brightness"] == 30
    assert json.loads(server.seen[-2][3]) == {"entity_id": "light.bedroom", "brightness_pct": 30}
    unlock = sandbox.execute(user_id, "home_control", {"entity_id": "lock.front_door", "action": "turn_off"},
                             message_id=message_id, allowed=None)
    assert unlock.status == "rejected" and "never locks" in unlock.error


# Allowing a PC action from the app or Telegram -------------------------------------------------------

@pytest.fixture
def fake_pc(user_id):
    desktop.reset()
    seen: list[dict] = []
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            action = desktop.next_action(user_id, "Test PC", wait=0.3)
            if action:
                seen.append(action)
                desktop.finish(user_id, action["id"], True, {"done": action["function"]})

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    desktop.touch(user_id, "Test PC")
    yield seen
    stop.set()
    thread.join(timeout=2)
    desktop.reset()


def _from(origin: str, user_id: int, message_id: int, outcome: list) -> threading.Thread:
    def turn():
        token = approvals.origin.set(origin)
        try:
            outcome.append(sandbox.execute(user_id, "pc_close_app", {"name": "chrome"}, message_id=message_id, allowed=None))
        finally:
            approvals.origin.reset(token)

    thread = threading.Thread(target=turn, daemon=True)
    thread.start()
    return thread


def _wait_for_approval(client) -> dict:
    for _ in range(100):
        waiting = client.get("/approvals").json()
        if waiting:
            return waiting[0]
        time.sleep(0.05)
    raise AssertionError("no approval appeared")


def test_an_action_asked_for_in_the_app_is_allowed_there_and_the_pc_doesnt_ask_again(user_id, client, grant, message_id, fake_pc):
    grant("pc", "write")
    outcome: list = []
    thread = _from("app", user_id, message_id, outcome)
    waiting = _wait_for_approval(client)
    assert "Quit" in waiting["details"] and "chrome" in waiting["details"] and waiting["origin"] == "app"
    assert fake_pc == []  # nothing reaches the PC before you answer
    assert client.post(f"/approvals/{waiting['id']}", json={"allow": True}).json() == {"id": waiting["id"], "allowed": True}
    thread.join(timeout=10)
    assert outcome[0].status == "succeeded"
    assert fake_pc[0]["function"] == "close_app" and fake_pc[0]["confirm"] is False and fake_pc[0]["approved"] == "remote"
    assert client.post(f"/approvals/{waiting['id']}", json={"allow": False}).status_code == 404  # answered already


def test_a_denied_action_never_reaches_the_pc(user_id, client, grant, message_id, fake_pc):
    grant("pc", "write")
    outcome: list = []
    thread = _from("app", user_id, message_id, outcome)
    client.post(f"/approvals/{_wait_for_approval(client)['id']}", json={"allow": False})
    thread.join(timeout=10)
    assert outcome[0].status == "failed" and "declined" in outcome[0].error
    assert fake_pc == []


def test_with_remote_approvals_off_or_from_the_pc_the_pc_asks(user_id, client, grant, message_id, fake_pc):
    grant("pc", "write")
    for origin in ("desktop", "task"):
        outcome: list = []
        _from(origin, user_id, message_id, outcome).join(timeout=10)
        assert fake_pc[-1]["confirm"] is True and "approved" not in fake_pc[-1]
    settings_store.update(user_id, remote_approvals=False)
    outcome = []
    _from("app", user_id, message_id, outcome).join(timeout=10)
    assert fake_pc[-1]["confirm"] is True and client.get("/approvals").json() == []


def test_no_approval_is_asked_for_when_the_pc_is_off(user_id, client, grant, message_id):
    desktop.reset()
    grant("pc", "write")
    outcome: list = []
    _from("app", user_id, message_id, outcome).join(timeout=10)
    assert outcome[0].status == "failed" and "isn't running" in outcome[0].error


def test_the_chat_route_tells_the_desktop_app_from_the_phone(client):
    from app.routers.chat import origin_of

    class Req:
        def __init__(self, headers):
            self.headers = headers

    assert origin_of(Req({"X-Custodian-Device": "Custodian desktop on OM-PC"})) == "desktop"
    assert origin_of(Req({})) == "app"


# Telegram ----------------------------------------------------------------------------------------

@pytest.fixture
def bot(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    sent: list[tuple[str, dict]] = []

    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        sent.append((method, body))
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "custodian_om_bot"}})
        if method == "sendMessage":
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})
        return httpx.Response(200, json={"ok": True, "result": True})

    monkeypatch.setattr(telegram, "_transport", httpx.MockTransport(handler))
    telegram.start(poll=False)
    return sent


def _text(chat: int, text: str, username: str = "om") -> dict:
    return {"message": {"chat": {"id": chat, "type": "private"}, "from": {"username": username}, "text": text}}


def _replies(sent, chat):
    return [b["text"] for m, b in sent if m == "sendMessage" and b["chat_id"] == chat]


def test_only_the_chat_that_sent_the_code_can_use_the_bot(user_id, client, bot):
    link = client.post("/integrations/telegram/link").json()
    assert link["bot"] == "custodian_om_bot" and len(link["code"]) == 8
    telegram.handle_update(_text(999, "hello"))
    assert "private Custodian" in _replies(bot, 999)[-1]
    for _ in range(telegram.MAX_WRONG_CODES):
        telegram.handle_update(_text(999, "/link 00000000"))
    telegram.handle_update(_text(999, f"/link {link['code']}"))  # too many wrong guesses: ignored even with the code
    assert telegram.user_for(999) is None
    telegram.handle_update(_text(42, f"/link {link['code'].lower()}"))
    assert "Linked" in _replies(bot, 42)[-1] and telegram.user_for(42) == user_id
    telegram.handle_update(_text(43, f"/link {link['code']}"))  # single use
    assert telegram.user_for(43) is None
    status = {i["provider"]: i for i in client.get("/integrations").json()}["telegram"]
    assert status["connected"] and status["account"] == "@om"
    telegram.handle_update({"message": {"chat": {"id": 42, "type": "group"}, "text": "lock my pc"}})  # groups: never
    assert len(_replies(bot, 42)) == 1


def test_a_linked_chat_talks_to_custodian_and_allows_pc_actions_with_buttons(user_id, client, bot, grant, message_id, fake_pc):
    telegram.handle_update(_text(42, "/link " + telegram.link_code(user_id)["code"]))
    telegram.handle_update(_text(42, "What can you do?"))
    for _ in range(100):
        if len(_replies(bot, 42)) >= 2:
            break
        time.sleep(0.05)
    assert len(_replies(bot, 42)) == 2  # the stub model answered in the chat

    grant("pc", "write")
    outcome: list = []
    thread = _from("telegram", user_id, message_id, outcome)
    for _ in range(100):
        asked = [b for m, b in bot if m == "sendMessage" and b.get("reply_markup")]
        if asked:
            break
        time.sleep(0.05)
    buttons = asked[0]["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in buttons] == ["Allow once", "Deny"] and "chrome" in asked[0]["text"]
    assert client.get("/approvals").json()[0]["origin"] == "telegram"
    telegram.handle_update({"callback_query": {"id": "cb1", "data": "ok:999", "message": {"chat": {"id": 42}}}})  # not it
    telegram.handle_update({"callback_query": {"id": "cb2", "data": buttons[0]["callback_data"],
                                               "message": {"chat": {"id": 7}}}})  # someone else's chat
    assert thread.is_alive()
    telegram.handle_update({"callback_query": {"id": "cb3", "data": buttons[0]["callback_data"],
                                               "message": {"chat": {"id": 42}}}})
    thread.join(timeout=10)
    assert outcome[0].status == "succeeded" and fake_pc[0]["approved"] == "remote"
    assert "Allowed" in _replies(bot, 42)[-1]


def test_alerts_are_forwarded_to_the_linked_chat_unless_switched_off(user_id, bot):
    telegram.handle_update(_text(42, "/link " + telegram.link_code(user_id)["code"]))
    notifications.add(user_id, "reminder", "Call mum", "6 pm")
    assert _replies(bot, 42)[-1] == "⏰ Call mum\n6 pm"
    settings_store.update(user_id, telegram_alerts=False)
    notifications.add(user_id, "reminder", "Stretch")
    assert _replies(bot, 42)[-1] == "⏰ Call mum\n6 pm"


def test_telegram_is_listed_where_data_goes_and_erased(user_id, client, bot):
    telegram.handle_update(_text(42, "/link " + telegram.link_code(user_id)["code"]))
    other = client.get("/privacy").json()["other_services"]
    assert any(d["name"] == "Telegram" and d["host"] == "api.telegram.org" and d["sends"] for d in other)
    assert client.post("/privacy/erase", json={"confirm": "ERASE"}).status_code in (200, 204)
    assert telegram.user_for(42) is None


# The phone as a remote ------------------------------------------------------------------------

def test_the_remote_runs_a_command_and_keeps_one_chat(user_id, client, fake_pc, grant):
    grant("pc", "write")
    first = client.post("/remote", json={"command": "volume 30"}).json()
    assert first["desktop"] is True and fake_pc[-1]["function"] == "set_volume" and fake_pc[-1]["args"] == {"level": 30}
    second = client.post("/remote", json={"command": "next track"}).json()
    assert second["conversation_id"] == first["conversation_id"]
    assert fake_pc[-1]["function"] == "media"
    assert client.post("/remote", json={"command": "  "}).status_code == 400
