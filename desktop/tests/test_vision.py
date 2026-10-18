"""The vision model's eyes and hands on the PC: capture, then click where it pointed and report
how much of the screen changed (a stand-in pyautogui: nothing is clicked)."""

import sys
import types

import pytest
from PIL import Image

from custodian_desktop import actions


def _fake_pyautogui(monkeypatch, screens):
    clicks = []
    fake = types.SimpleNamespace(
        size=lambda: (2560, 1440),
        screenshot=lambda: screens.pop(0),
        click=lambda x, y, button="left": clicks.append((x, y, button)),
        doubleClick=lambda x, y: clicks.append((x, y, "double")),
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    monkeypatch.setattr(actions.time, "sleep", lambda _s: None)
    monkeypatch.setattr(actions, "_active_title", lambda: "Sign in - Google Chrome")
    return clicks


def test_capture_sends_a_small_copy_and_a_click_is_scaled_to_the_real_screen(monkeypatch):
    before = Image.new("RGB", (2560, 1440), "white")
    after = before.copy()
    after.paste((20, 20, 200), (0, 0, 1280, 720))  # a quarter of the screen changed
    clicks = _fake_pyautogui(monkeypatch, [before, after])
    shot = actions.run("capture", {"purpose": "click the Sign in button"}, [])
    assert (shot["width"], shot["height"]) == (1280, 720) and shot["window"] == "Sign in - Google Chrome"
    done = actions.run("click_at_checked", {"x": 640, "y": 300, "image_width": 1280, "image_height": 720}, [])
    assert clicks == [(1280, 600, "left")]
    assert 0.2 < done["changed"] < 0.3 and done["image_b64"]


def test_a_point_off_the_screen_is_never_clicked(monkeypatch):
    clicks = _fake_pyautogui(monkeypatch, [Image.new("RGB", (2560, 1440))])
    with pytest.raises(actions.ActionError, match="off the screen"):
        actions.run("click_at_checked", {"x": 1400, "y": 10, "image_width": 1280, "image_height": 720}, [])
    assert clicks == []


def test_the_dialog_says_a_screenshot_will_be_read():
    text = actions.describe("capture", {"purpose": "click the blue Sign in button"})
    assert text.startswith("Look at your screen to click the blue Sign in button") and "isn't kept" in text
