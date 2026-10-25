"""Jarvis on the PC: the conversation manner, follow-ups without the wake word, the watchdog, the
vitals, the weather and the HUD's messages (nothing here opens a window, speaks or uses the mic)."""

import datetime as dt
import json
import queue

import httpx
import numpy as np
import pytest

from custodian_desktop import jarvis, weather
from custodian_desktop.__main__ import App
from custodian_desktop.hud import Hud
from custodian_desktop.stats import Sampler
from custodian_desktop.voice import FRAME, Listener
from custodian_desktop.watchdog import REPEAT_AFTER, SUSTAINED, Watchdog

LOUD = (np.sin(np.linspace(0, 200, FRAME)) * 8000).astype(np.int16)
QUIET = np.zeros(FRAME, dtype=np.int16)


# The manner -------------------------------------------------------------------------------------

@pytest.mark.parametrize("said", ["That's all.", "thank you", "Thanks Jarvis", "no thanks", "nothing else", "okay that's it",
                                  "Jarvis, that will be all", "goodbye", "never mind", "bas", "धन्यवाद", "Thank you."])
def test_these_end_a_conversation(said):
    assert jarvis.ends_conversation(said)


@pytest.mark.parametrize("said", ["thank you, now open chrome", "stop the music", "nothing is playing, play something",
                                  "what's all this", "thanks for the reminder, set another for 6"])
def test_requests_that_merely_contain_those_words_still_go_through(said):
    assert not jarvis.ends_conversation(said)


@pytest.mark.parametrize("said, command", [("show the HUD", "show"), ("Jarvis, full screen", "show"), ("open your interface", "show"),
                                           ("hide the hud", "hide"), ("exit full screen", "hide"), ("show me my downloads", None)])
def test_the_hud_is_shown_and_hidden_by_voice(said, command):
    assert jarvis.hud_command(said) == command


def test_the_greeting_fits_the_time_and_the_day():
    assert jarvis.greeting("sir", dt.datetime(2026, 10, 24, 19, 5)) == "Good evening, sir. Jarvis is online. All systems are normal."
    late = jarvis.greeting("boss", dt.datetime(2026, 10, 24, 2, 30), warnings=["the battery is at 12%"],
                           next_event={"title": "Standup", "time": "09:30"})
    assert late == "Working late, boss? Jarvis is online. One thing: the battery is at 12%. Your next appointment, Standup, is at 09:30."


# Follow-ups -------------------------------------------------------------------------------------

def test_a_follow_up_needs_no_wake_word_and_silence_ends_it_quietly():
    states, levels = [], []
    listener = Listener(lambda wav: None, states.append, wake_word=False, on_level=levels.append)
    listener.follow_up(seconds=0.5)
    frames: queue.Queue = queue.Queue()
    for frame in [QUIET] * 20:
        frames.put(frame)
    assert listener._record(frames, floor=100.0) is None
    assert states == ["following up", "conversation over"]  # not "didn't catch that"
    assert levels[-1] == 0.0


def test_a_follow_up_that_is_spoken_is_recorded_and_its_level_reaches_the_hud():
    states, levels = [], []
    listener = Listener(lambda wav: None, states.append, wake_word=False, on_level=levels.append)
    listener.follow_up()
    frames: queue.Queue = queue.Queue()
    for frame in [LOUD] * 15 + [QUIET] * 20:
        frames.put(frame)
    assert listener._record(frames, floor=100.0) is not None
    assert states == ["following up"] and max(levels) > 0.5


class _Client:
    def __init__(self):
        self.chats = []

    def transcribe(self, wav):
        return self.said

    def chat(self, text, conversation_id=None, *, voice=False):
        self.chats.append((text, voice))
        return {"conversation_id": 3, "reply": "Opened Chrome, sir."}


class _Window:
    def __init__(self):
        self.shown = []

    def show(self, title, **kwargs):
        self.shown.append((title, kwargs.get("reply")))


class _Listener:
    def __init__(self):
        self.followed = []

    def follow_up(self, seconds=6.0):
        self.followed.append(seconds)


def _app(said: str, conversation_mode: bool = True) -> App:
    app = App.__new__(App)
    app.client, app.window, app.listener = _Client(), _Window(), _Listener()
    app.client.said = said
    app.settings = type("S", (), {"voice_id": False, "speak_replies": False, "conversation_mode": conversation_mode,
                                  "follow_up_seconds": 6.0})()
    app.awaiting = app.enrolling = None
    app.conversation_id, app.last_turn, app.paused = None, 0.0, False
    return app


def test_a_spoken_request_is_sent_as_voice_and_the_conversation_stays_open():
    app = _app("open chrome")
    app.on_speech(b"wav")
    assert app.client.chats == [("open chrome", True)]
    assert app.listener.followed == [6.0] and app.conversing


def test_without_conversation_mode_it_goes_back_to_the_wake_word():
    app = _app("open chrome", conversation_mode=False)
    app.on_speech(b"wav")
    assert app.listener.followed == [] and not app.conversing


def test_thats_all_closes_the_conversation_without_asking_custodian():
    app = _app("that's all, thanks")
    app.conversing = True
    app.on_speech(b"wav")
    assert app.client.chats == [] and app.listener.followed == [] and not app.conversing
    assert app.window.shown[-1][1] in [s.format(a="sir") for s in jarvis.SIGN_OFFS]


def test_noise_heard_as_a_follow_up_is_dropped():
    app = _app(".")
    app.conversing = True
    app.on_speech(b"wav")
    assert app.client.chats == [] and not app.conversing


def test_a_recording_with_no_words_is_didnt_catch_that_not_something_went_wrong():
    app = _app("")  # the server heard no words: a cough, a "hmm" (Client.transcribe gives "")
    app.on_speech(b"wav")
    assert app.client.chats == [] and app.window.shown[-1][0] == "Didn't catch that"


def test_the_client_reads_nothing_heard_as_no_words(monkeypatch):
    import httpx

    from custodian_desktop.client import Client

    def reply(request):
        return httpx.Response(400, json={"detail": "Nothing was heard in that recording."})

    client = Client("http://server", "token")
    client._http = httpx.Client(base_url="http://server", transport=httpx.MockTransport(reply))
    assert client.transcribe(b"wav") == ""
    client._http = httpx.Client(base_url="http://server", transport=httpx.MockTransport(
        lambda request: httpx.Response(400, json={"detail": "That recording couldn't be read."})))
    with pytest.raises(RuntimeError):  # a real problem still says so
        client.transcribe(b"wav")


def test_the_hud_activity_list_names_actions_never_message_text():
    assert App.action_label("send_message", {"contact": "Tanishka", "text": "secret plans"}) == "send message · Tanishka"
    assert App.action_label("set_volume", {"level": 30}) == "set volume · 30"
    assert App.action_label("lock_screen", {}) == "lock screen"


# The watchdog -----------------------------------------------------------------------------------

def test_a_low_battery_is_mentioned_once_per_step_and_again_after_charging():
    dog = Watchdog()
    reading = {"battery": {"percent": 19, "charging": False, "minutes_left": 42}}
    first = dog.check(reading, 0.0)
    assert first == [("battery", "Sir, the battery is at 19%, about 42 minutes left. You may want to plug in.")]
    assert dog.check(reading, 60.0) == []  # same step: said once
    assert dog.check({"battery": {"percent": 9, "charging": False}}, 120.0)[0][1].startswith("Sir, the battery is at 9%")
    dog.check({"battery": {"percent": 80, "charging": True}}, 200.0)  # charged
    assert dog.check({"battery": {"percent": 18, "charging": False}}, 300.0)  # the next time it runs low, said again


def test_the_processor_must_stay_high_and_the_warning_names_the_app():
    dog = Watchdog()
    busy = {"cpu_percent": 97, "top_apps": [{"app": "chrome", "cpu_percent": 71}]}
    assert dog.check(busy, 0.0) == []
    assert dog.check({"cpu_percent": 40}, SUSTAINED / 2) == []  # it dipped: the clock starts again
    assert dog.check(busy, SUSTAINED / 2 + 1) == []
    said = dog.check(busy, SUSTAINED * 2)
    assert said == [("cpu", "The processor has been above 90% for a couple of minutes, sir. chrome is using the most.")]
    assert dog.check(busy, SUSTAINED * 3) == []  # not again so soon
    assert dog.check(busy, SUSTAINED * 2 + REPEAT_AFTER) != []


def test_a_nearly_full_drive_is_mentioned():
    said = Watchdog().check({"disks": [{"drive": "C:\\", "free_gb": 9.0, "total_gb": 476.0}]}, 0.0)
    assert said == [("disk C:\\", "Drive C: is almost full, sir: 9 GB left.")]


# Freeing memory ---------------------------------------------------------------------------------

class _Proc:
    def __init__(self, name, mb):
        self.info = {"name": name, "memory_info": type("M", (), {"rss": mb * 1_000_000})}


WINDOWS = [("Inbox - Chrome", "chrome.exe", 1, 11), ("Steam", "steam.exe", 2, 12), ("Notes", "notepad.exe", 3, 13),
           ("Explorer", "explorer.exe", 4, 14), ("main.py - VS Code", "Code.exe", 5, 15)]
PROCESSES = [_Proc("chrome.exe", 900), _Proc("chrome.exe", 700), _Proc("steam.exe", 600), _Proc("notepad.exe", 20),
             _Proc("explorer.exe", 400), _Proc("Code.exe", 1200), _Proc("svchost.exe", 2000)]


def test_the_apps_offered_for_closing_are_big_unused_and_never_windows_own():
    from custodian_desktop import actions

    now = 10_000.0
    seen = {"code.exe": now - 5, "chrome.exe": now - 3600}  # VS Code is in use; Chrome was an hour ago
    hogs = actions.memory_hogs(seen, now, windows=WINDOWS, processes=PROCESSES)
    assert hogs == [{"process": "chrome.exe", "app": "chrome", "memory_mb": 1600},  # all its processes together
                    {"process": "steam.exe", "app": "steam", "memory_mb": 600}]
    # Notepad is too small to matter; Explorer and svchost are Windows' own.
    in_use = {"chrome.exe": now, "steam.exe": now, "code.exe": now}
    assert actions.memory_hogs(in_use, now, windows=WINDOWS, processes=PROCESSES) == []


def test_the_memory_warning_names_the_apps_and_offers_to_close_them():
    hogs = [{"app": "chrome", "memory_mb": 1600}, {"app": "steamwebhelper", "memory_mb": 600}]
    assert jarvis.memory_offer("sir", 94.6, hogs) == ("Memory is nearly full, sir: 95% in use. Chrome and Steam are "
                                                      "holding 2.2 GB and haven't been used for a while. Shall I close them?")
    assert jarvis.memory_offer("boss", 93, hogs[:1]).endswith("Chrome is holding 1.6 GB and hasn't been used for a while. "
                                                              "Shall I close it?")
    assert jarvis.app_name("WhatsApp.Root.exe") == "WhatsApp" and jarvis.app_name("figma") == "Figma"


class _Hud:
    visible, mode = True, "compact"

    def __init__(self):
        self.alerts, self.offers, self.logs = [], [], []

    def running(self):
        return True

    def revive(self):
        return True

    def alert(self, text):
        self.alerts.append(text)

    def offer(self, label, offer_id=None):
        self.offers.append((label, offer_id))

    def log(self, text, warn=False):
        self.logs.append(text)

    def heard(self, text): pass
    def reply(self, text): pass
    def show(self, mode): pass
    def hide_later(self, seconds): pass
    def state(self, state): pass


def test_saying_yes_to_the_memory_offer_closes_the_apps(monkeypatch):
    from custodian_desktop import __main__ as main
    from custodian_desktop import actions

    app = _app("")
    app.hud, app.offers, app.front_seen, app.watchdog = _Hud(), {}, {}, Watchdog()
    app.day, app.address = {"quiet": False, "announce": True}, "sir"
    app.settings.speak_announcements, app.paused = True, False
    spoken, closed, expected = [], [], []
    app.speak = spoken.append
    app.expect_reply = lambda callback, seconds=8: expected.append(callback)
    full = {"memory": {"used_percent": 95.0}, "cpu_percent": 10}
    monkeypatch.setattr(main.SAMPLER, "sample", lambda **_: full)
    monkeypatch.setattr(actions, "memory_hogs", lambda seen, now: [{"app": "chrome", "memory_mb": 1600}])
    monkeypatch.setattr(actions, "close_app", lambda name: closed.append(name) or {"windows_closed": 1})
    app.watchdog.high_since["memory"] = main.time.monotonic() - SUSTAINED - 1  # high for two minutes already
    app.check_health()
    assert spoken[-1].endswith("Chrome is holding 1.6 GB and hasn't been used for a while. Shall I close it?")
    assert app.hud.offers[-1][0] == "Close Chrome" and len(expected) == 1

    expected[0]("No.")
    assert closed == []
    full["memory"]["used_percent"] = 78.0
    expected[0]("Yes, close it.")
    assert closed == ["chrome"] and spoken[-1] == "Closed Chrome, sir. Memory is at 78% now."
    app.offers.popitem()[1]()  # pressing the HUD button too does nothing more
    assert closed == ["chrome"]


# Vitals and weather -----------------------------------------------------------------------------

def test_a_reading_has_everything_the_hud_and_the_status_report_use():
    sampler = Sampler()
    sampler.sample(with_apps=True)
    reading = sampler.sample(with_apps=True)
    assert {"cpu_percent", "memory", "gpu", "battery", "disks", "network", "uptime_hours", "top_apps"} <= set(reading)
    assert reading["network"]["down_kbps"] >= 0 and 0 <= reading["memory"]["used_percent"] <= 100
    assert sampler.fresh(30)


def test_the_weather_comes_from_open_meteo_by_city_name():
    seen = []

    def api(request):
        seen.append(request.url.host)
        if "geocoding" in request.url.host:
            return httpx.Response(200, json={"results": [{"name": "Pune", "country_code": "IN", "latitude": 18.5, "longitude": 73.9}]})
        return httpx.Response(200, json={"current": {"temperature_2m": 25.9, "apparent_temperature": 28, "weather_code": 3,
                                                     "wind_speed_10m": 4.5},
                                         "daily": {"temperature_2m_max": [33.7], "temperature_2m_min": [23.7],
                                                   "precipitation_probability_max": [41]}})

    weather._places.clear()
    now = weather.current("pune", transport=httpx.MockTransport(api))
    assert now == {"city": "Pune", "temperature": 25.9, "feels_like": 28, "condition": "overcast", "code": 3, "wind_kmh": 4.5,
                   "high": 33.7, "low": 23.7, "rain_chance": 41}
    assert seen == ["geocoding-api.open-meteo.com", "api.open-meteo.com"]
    assert weather.current("") is None


# The HUD link -----------------------------------------------------------------------------------

def test_a_hud_whose_window_went_away_is_started_again_but_not_over_and_over(monkeypatch):
    hud = Hud(lambda message: None)
    started = []
    monkeypatch.setattr(hud, "start", lambda: started.append(1) or True)

    class Gone:
        def poll(self):
            return 0  # the process ended: its window closed (around the PC sleeping)

        def wait(self, timeout=None):
            return 0

    assert hud.revive() is False and started == []  # never started: nothing to bring back
    hud.proc = Gone()
    assert hud.revive() is True and started == [1]
    hud.proc = Gone()
    assert hud.revive() is False and started == [1]  # it went again at once: wait before the next try
    hud.stop()
    assert hud.revive() is False and started == [1]  # stopped on purpose: stays stopped


def test_hud_messages_are_one_json_line_each_and_a_missing_window_never_breaks_the_app():
    hud = Hud(lambda message: None)
    hud.send({"type": "state", "state": "listening"})  # not started: nothing happens, nothing raises

    class Pipe:
        def __init__(self):
            self.lines = []

        def write(self, text):
            self.lines.append(text)

        def flush(self):
            pass

    class Proc:
        stdin = Pipe()

        def poll(self):
            return None

    hud.proc = Proc()
    hud.level(1.7)
    hud.reply("Opened Chrome, sir.")
    assert [json.loads(line) for line in Proc.stdin.lines] == [{"type": "level", "value": 1.0},
                                                              {"type": "reply", "text": "Opened Chrome, sir."}]


def test_check_for_updates_offers_update_now_only_when_one_is_waiting(monkeypatch):
    app = _app("")
    app.hud, app.offers = None, {}  # no HUD: the small panel says it
    started = []
    monkeypatch.setattr(app, "update_now", lambda: started.append(True), raising=False)
    app.client.version = lambda refresh=False: {"commit": "61cddc7", "update_available": False, "behind": 0}
    app.check_updates()
    assert app.offers == {} and app.window.shown[-1] == ("Custodian is up to date", "Version 61cddc7.")
    app.hud = _Hud()
    app.client.version = lambda refresh=False: {"commit": "61cddc7", "update_available": True, "behind": 2,
                                                "new": ["Faster replies", "A new HUD"]}
    app.check_updates()
    assert app.hud.offers[-1][0] == "Update now"
    app.offers.popitem()[1]()
    assert started == [True]
