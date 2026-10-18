"""Seeing the screen: pc_look and pc_click_on through the real gate, with a stand-in desktop app
and vision model - look, find, click, check."""

import json
import threading

import httpx
import pytest

from app import db, desktop, vision
from app.crypto import encrypt
from app.tools import sandbox


def test_the_models_answer_is_read_in_every_shape_it_comes_in():
    assert vision.parse_point('{"found": true, "x": 640, "y": 360, "label": "Sign in"}', 1280, 720) == {
        "x": 640, "y": 360, "label": "Sign in"}
    assert vision.parse_point('```json\n[{"bbox_2d": [100, 200, 300, 260], "label": "Sign in"}]\n```', 1280, 720)["x"] == 200
    assert vision.parse_point('{"point": [0.5, 0.25]}', 1280, 720) == {"x": 640, "y": 180, "label": ""}  # a fraction
    assert vision.parse_point('{"x": 900, "y": 950}', 640, 360) == {"x": 576, "y": 342, "label": ""}  # a 0-1000 grid
    assert vision.parse_point('{"found": false, "seen": "a login page"}', 1280, 720) is None
    assert vision.parse_point('{"x": 5000, "y": 5000}', 1280, 720) is None
    assert vision.parse_point("I think it's at the top", 1280, 720) is None


@pytest.fixture
def message_id(user_id):
    with db.connect() as conn:
        conv = conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (user_id,)).lastrowid
        return conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', ?)",
                            (conv, encrypt(""))).lastrowid


@pytest.fixture
def pc(user_id):
    """The desktop app: a 1280x720 screenshot, and a click that changes `changed` of the screen."""
    desktop.reset()
    state = {"seen": [], "changed": 0.08}
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            action = desktop.next_action(user_id, "Test PC", wait=0.3)
            if not action:
                continue
            state["seen"].append(action)
            if action["function"] == "capture":
                result = {"image_b64": "c2NyZWVu", "width": 1280, "height": 720, "window": "Sign in - Google Chrome"}
            else:
                result = {"clicked": [action["args"]["x"] * 2, action["args"]["y"] * 2], "changed": state["changed"],
                          "image_b64": "YWZ0ZXI="}
            desktop.finish(user_id, action["id"], True, result)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    desktop.touch(user_id, "Test PC")
    yield state
    stop.set()
    thread.join(timeout=2)
    desktop.reset()


@pytest.fixture
def eyes(monkeypatch):
    """The vision model: answers from `replies` by what it's asked, and records the images."""
    monkeypatch.setenv("SCREEN_VISION_MODEL", "qwen2.5vl:7b")
    monkeypatch.setenv("VISION_BASE_URL", "http://127.0.0.1:11434/v1")
    asked = []
    replies = {"find": '{"found": true, "x": 640, "y": 300, "label": "Sign in"}', "after": "The Google account chooser is open.",
               "look": "Chrome is showing Google's sign-in page."}

    def handler(request):
        body = json.loads(request.content)
        prompt = body["messages"][0]["content"][0]["text"]
        image = body["messages"][0]["content"][1]["image_url"]["url"]
        kind = "find" if "Find this" in prompt else "after" if "just after" in prompt else "look"
        asked.append((kind, image))
        return httpx.Response(200, json={"choices": [{"message": {"content": replies[kind]}}]})

    monkeypatch.setattr(vision, "_transport", httpx.MockTransport(handler))
    return asked, replies


def test_look_answers_from_the_screenshot_after_one_ok(user_id, grant, message_id, pc, eyes):
    grant("pc", "read")
    out = sandbox.execute(user_id, "pc_look", {"question": "what's on my screen?"}, message_id=message_id, allowed=None)
    assert out.status == "succeeded", out.error
    assert out.result == {"answer": "Chrome is showing Google's sign-in page.", "window": "Sign in - Google Chrome"}
    (capture,) = pc["seen"]
    assert capture["function"] == "capture" and capture["confirm"] is True  # the PC asks first
    assert eyes[0][0][1] == "data:image/png;base64,c2NyZWVu"


def test_click_on_looks_finds_clicks_and_checks_with_a_single_ok(user_id, grant, message_id, pc, eyes):
    grant("pc", "write")
    out = sandbox.execute(user_id, "pc_click_on", {"target": "the blue Sign in button"}, message_id=message_id, allowed=None)
    assert out.status == "succeeded", out.error
    assert out.result == {"clicked": "Sign in", "at": [640, 300], "screen_changed": True,
                          "now": "The Google account chooser is open."}
    capture, click = pc["seen"]
    assert capture["confirm"] is True and click["confirm"] is False  # one OK for look, click and check
    assert click["function"] == "click_at_checked" and click["args"] == {"x": 640, "y": 300, "image_width": 1280,
                                                                         "image_height": 720, "button": "left"}
    assert [k for k, _ in eyes[0]] == ["find", "after"] and eyes[0][1][1].endswith("YWZ0ZXI=")


def test_a_click_that_changes_nothing_says_so(user_id, grant, message_id, pc, eyes):
    grant("pc", "write")
    pc["changed"] = 0.0
    out = sandbox.execute(user_id, "pc_click_on", {"target": "Sign in"}, message_id=message_id, allowed=None)
    assert out.result["screen_changed"] is False and "may not have worked" in out.result["note"]


def test_nothing_is_clicked_when_the_target_isnt_there(user_id, grant, message_id, pc, eyes):
    grant("pc", "write")
    eyes[1]["find"] = '{"found": false, "seen": "a YouTube video"}'
    out = sandbox.execute(user_id, "pc_click_on", {"target": "the Sign in button"}, message_id=message_id, allowed=None)
    assert out.status == "failed" and "couldn't see the Sign in button" in out.error and "YouTube" in out.error
    assert [a["function"] for a in pc["seen"]] == ["capture"]


def test_without_a_vision_model_nobody_is_asked_and_nothing_is_captured(user_id, grant, message_id, pc):
    grant("pc", "read")
    out = sandbox.execute(user_id, "pc_look", {}, message_id=message_id, allowed=None)
    assert out.status == "failed" and "SCREEN_VISION_MODEL" in out.error
    assert pc["seen"] == []
