"""Starts Custodian for the desktop:

    .venv\\Scripts\\pythonw -m custodian_desktop        (desktop\\run-desktop.ps1 does this)

Threads: the window (main thread), the PC-action bridge (long-polls the server), the
microphone listener (wake word "Hey Jarvis"), the global hotkey, and the tray icon.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import threading
import time
import webbrowser
from concurrent.futures import Future, ThreadPoolExecutor

from PIL import Image, ImageDraw

from . import actions, config, jarvis, weather
from .client import Client, NotSignedIn
from .hud import Hud, Throttle
from .indexer import Indexer
from .learning import NightlyLearner
from .stats import SAMPLER
from .ui import Window
from .voice import Listener, Speaker, from_wav
from .voiceid import PHRASES, VoiceId, VoiceIdUnavailable
from .watchdog import Watchdog

log = logging.getLogger("custodian.desktop")
CONVERSATION_MINUTES = 15  # voice turns this close together continue the same chat
ACTS_ON_FRONT_WINDOW = {"type_text", "press_keys", "click", "click_element", "fill_field", "capture"}
AWAY_SECONDS = 3 * 3600  # this long without a key or mouse touch, and the next touch is "arriving"
MORNING = range(5, 12)  # hours when arriving gets the morning briefing
# A spoken "yes" to an offer ("Shall I open the meeting link?"), in English, Hindi or Marathi.
AFFIRMATIVE = re.compile(r"^\W*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|please|go\s+ahead|open\s+it|do\s+it|close\s+(?:it|them)|haan?|ha|ho|hoy|ji|chalo|kholo"
                         r"|हाँ|हां|हा|हो|होय|जी|खोलो|उघड)\b", re.IGNORECASE)


def _foreground() -> int:
    try:
        return actions.foreground()[1]
    except Exception:  # noqa: BLE001 - not on Windows (tests)
        return 0


def idle_seconds() -> float:
    """Seconds since the last key press or mouse move on this PC."""
    import ctypes

    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LastInput(ctypes.sizeof(LastInput), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


def uptime_seconds() -> float:
    import ctypes

    ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    return ctypes.windll.kernel32.GetTickCount64() / 1000


def _icon() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 60, 60), fill=(91, 79, 196))
    draw.ellipse((20, 20, 44, 44), outline=(255, 255, 255), width=5)
    draw.rectangle((38, 26, 48, 38), fill=(91, 79, 196))
    return image


class App:
    # Jarvis's state, also when an App is made without __init__ (tests): set per app in __init__.
    hud: Hud | None = None
    conversing = False
    address = "sir"
    day: dict = {}
    weather: dict | None = None
    front_seen: dict[str, float] = {}  # process name -> when it was last the app in front

    def __init__(self) -> None:
        self.settings = config.Settings.load()
        self.window = Window(on_quit=self.quit)
        self.client = Client(self.settings.server, config.get_token(self.settings.server))
        self.speaker = Speaker(config.APP_DIR / "voices", config.voice_name(self.settings.voice), self.settings.hindi_voice)
        self.voiceid = VoiceId(threshold=self.settings.voice_id_threshold)
        self.enrolling: list | None = None  # recordings so far while setting up voice ID
        self.hearing = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hearing")
        self.levels = Throttle(0.08)
        self.listener = Listener(self.on_speech, self.on_state, wake_word=self.settings.wake_word,
                                 wake_model=config.wake_model_path(self.settings.wake_model),
                                 on_early=self.hear_early if self.settings.fast_transcribe else None,
                                 barge_in=self.settings.barge_in, on_barge=self.speaker.stop, on_level=self.on_level,
                                 threshold=self.settings.wake_threshold, device=self.settings.microphone)
        # Jarvis: the HUD, how to address you (Custodian's settings), your day, the watchdog.
        self.hud = Hud(self.on_hud_command) if self.settings.hud else None
        self.address = "sir"
        self.day: dict = {}
        self.weather: dict | None = None
        self.watchdog = Watchdog()
        self.front_seen = {}
        self.offers: dict[str, object] = {}  # HUD button id -> what pressing it does
        self.conversing = False  # in a conversation: the next request needs no wake word
        self.conversation_id: int | None = None
        self.last_turn = 0.0
        actions.on_timer = self.timer_rang
        actions.on_say = self.say_aloud
        self.indexer = Indexer(lambda: self.client)  # search inside your files, when it's switched on
        self.learner = NightlyLearner(lambda: self.client, lambda: self.settings.server, idle_seconds)
        self.awaiting = None  # (callback, deadline) while listening for the answer to an offer
        self.paused = False
        self.stopping = threading.Event()
        self.tray = None

    # Signing in --------------------------------------------------------------------------------

    def ensure_signed_in(self) -> bool:
        while not self.stopping.is_set():
            if self.client.token:
                try:
                    self.client.me()
                    return True
                except NotSignedIn:
                    config.set_token(self.settings.server, None)
                    self.client.token = None
                except Exception as exc:  # noqa: BLE001 - server not up yet: wait for it
                    self.show("Can't reach the Custodian server", reply=str(exc)[:200], hide_after=6)
                    time.sleep(10)
                    continue
            answer = self.window.ask_sign_in(self.settings.server)
            if answer is None:
                self.quit()
                return False
            server, passcode = answer
            self.settings.server = server
            self.settings.save()
            self.client.close()
            self.client = Client(server)
            try:
                config.set_token(server, self.client.login(passcode))
            except Exception as exc:  # noqa: BLE001
                self.show("Sign-in failed", reply=str(exc), hide_after=8)
        return False

    # PC actions from the server -------------------------------------------------------------------

    def bridge(self) -> None:
        while not self.stopping.is_set():
            try:
                action = self.client.next_action()
            except NotSignedIn:
                if not self.ensure_signed_in():
                    return
                continue
            except Exception as exc:  # noqa: BLE001 - server restarting, network blip
                log.debug("bridge: %s", exc)
                time.sleep(5)
                continue
            if action:
                threading.Thread(target=self.handle_action, args=(action,), daemon=True).start()

    def handle_action(self, action: dict) -> None:
        function, args = action["function"], action["args"]
        if action.get("reply") is False:  # the server is telling, not asking: nothing goes back
            self.handle_notice(function, args)
            return
        if action.get("approved") == "remote":  # you allowed it in the app or on Telegram: show that, don't ask again
            details = actions.describe(function, args)
            log.info("ALLOWED REMOTELY %s %s", function, details[:200])
            self.show("Allowed from your phone", reply=details[:300], hide_after=6)
            if function in actions.SELF_CONFIRMING:  # it still checks what it found (the chat's real name) itself
                actions.confirm_hook = lambda _title, _details: True
        elif function in actions.SELF_CONFIRMING and (action.get("confirm") or function in actions.ASK_WHEN_RISKY):
            def ask(title: str, details: str) -> bool:
                allowed = self.window.confirm(title, details)
                log.info("%s %s %s", "ALLOWED" if allowed else "DECLINED", function, details[:200])
                return allowed

            actions.confirm_hook = ask  # the action asks with what it found (the real chat's name)
        elif action.get("confirm"):
            front = _foreground()
            details = actions.describe(function, args)
            title = args.get("title") if function == "confirm_plan" else action.get("description", function).split("(")[0].strip().rstrip(".")
            allowed = self.window.confirm(title, details)
            log.info("%s %s %s", "ALLOWED" if allowed else "DECLINED", function, details[:200])
            if not allowed:
                self.client.send_result(action["id"], False, error="The user declined this on the PC.")
                return
            if function in ACTS_ON_FRONT_WINDOW:
                actions.refocus(front)  # the dialog took the keyboard; give it back first
        try:
            result = actions.run(function, args, self.settings.protected)
            self.client.send_result(action["id"], True, result=result)
            if self.hud_on() and function != "confirm_plan":
                self.hud.log(self.action_label(function, args))
        except Exception as exc:  # noqa: BLE001 - reported to the model
            self.client.send_result(action["id"], False, error=f"{type(exc).__name__}: {exc}"[:500])
            if self.hud_on():
                self.hud.log(f"{self.action_label(function, args)} · failed", warn=True)
        finally:
            actions.confirm_hook = None  # an answer given for this action never carries over to the next

    def handle_notice(self, function: str, args: dict) -> None:
        """Things the server tells the app without waiting: index now, or say something."""
        if function == "index_now":
            self.indexer.index_now()
        elif function == "announce":
            self.announce(args)
        else:
            log.info("ignored an unknown notice from the server: %s", function)

    # Jarvis: the HUD, your day, the watchdog ------------------------------------------------------------

    def hud_on(self) -> bool:
        return self.hud is not None and self.hud.running()

    def show(self, status: str, heard: str | None = None, reply: str | None = None, *, hide_after: float | None = None) -> None:
        """What's happening, on the HUD when it runs, else on the small panel."""
        if not self.hud_on():
            self.window.show(status, heard=heard, reply=reply, hide_after=hide_after)
            return
        if heard is not None:
            self.hud.heard(heard)
        if reply is not None:
            self.hud.reply(reply)
        elif status and heard is None:
            self.hud.reply(status)
        if not self.hud.visible:
            self.hud.show("compact")
        if hide_after:
            self.hud.hide_later(hide_after)

    def on_level(self, value: float) -> None:
        if self.hud_on() and self.levels.ready():
            self.hud.level(value)

    def on_hud_command(self, message: dict) -> None:
        command = message.get("cmd")
        if command == "talk":
            self.talk_now()
        elif command == "offer":
            action = self.offers.pop(str(message.get("id")), None)
            if callable(action):
                threading.Thread(target=action, daemon=True).start()

    def toggle_hud(self) -> None:
        if self.hud is None:
            self.show("The HUD is off", reply="Switch it on in desktop.json (\"hud\": true) and restart.", hide_after=5)
            return
        if not self.hud.running() and not self.hud.start():
            self.show("The HUD can't run here", reply="It needs pywebview: run desktop\\run-desktop.ps1 once more.", hide_after=6)
            return
        self.hud.toggle_full()
        if self.hud.visible:
            self.push_vitals()
            self.push_day()

    def push_vitals(self) -> None:
        if self.hud_on():
            self.hud.send({"type": "stats", "data": SAMPLER.sample(with_apps=self.hud.mode == "full")})

    def push_day(self) -> None:
        if not self.hud_on():
            return
        if self.day:
            self.hud.send({"type": "day", "data": self.day})
        if self.weather:
            self.hud.send({"type": "weather", "data": self.weather})

    def vitals(self) -> None:
        """Every 2 s: which app is in front (so Jarvis knows what you aren't using), and the PC's
        vitals while the HUD shows. Every 30 s: the watchdog, and the vitals and weather to the
        server for the HUD in the phone app."""
        tick = 0
        while not self.stopping.is_set():
            try:
                front = actions.foreground_process()
                if front:
                    self.front_seen[front] = time.monotonic()
                if self.hud_on() and self.hud.visible:
                    apps = self.hud.mode == "full" and tick % 5 == 0  # the app list costs most: every 10 s
                    self.hud.send({"type": "stats", "data": SAMPLER.sample(with_apps=apps)})
                if tick % 15 == 0:
                    reading = SAMPLER.sample(with_apps=True)
                    threading.Thread(target=self.report, args=(reading,), daemon=True).start()
                    if self.settings.watchdog:
                        self.check_health(reading)
            except Exception:  # noqa: BLE001 - a bad reading mustn't stop the loop
                log.debug("vitals", exc_info=True)
            tick += 1
            self.stopping.wait(2)

    def report(self, reading: dict) -> None:
        try:
            self.client.report_vitals({**reading, "weather": self.weather} if self.weather else reading)
        except Exception as exc:  # noqa: BLE001 - the server is restarting or we're signed out: next time
            log.debug("couldn't report vitals: %s", exc)

    def check_health(self, reading: dict | None = None) -> None:
        reading = reading or SAMPLER.sample(with_apps=True)
        for kind, text in self.watchdog.check(reading, time.monotonic(), self.address):
            hogs = actions.memory_hogs(self.front_seen, time.monotonic()) if kind == "memory" else []
            if hogs:  # say which apps you aren't using hold the memory, and offer to close them
                text = jarvis.memory_offer(self.address, reading["memory"]["used_percent"], hogs)
            log.info("watchdog: %s", text)
            if self.hud_on():
                self.hud.alert(text)
            speaking = not self.day.get("quiet") and self.day.get("announce", True)
            if speaking:
                self.show("Jarvis", reply=text, hide_after=20 if hogs else 12)
                self.speak(text)
            if hogs:
                self.offer_to_close(hogs, listen=speaking and self.settings.speak_announcements and not self.paused)

    def offer_to_close(self, hogs: list[dict], *, listen: bool) -> None:
        """A HUD button (and, when Jarvis just said it, a spoken "yes") that closes those apps. Each
        is asked to close as if you clicked its X, so one with unsaved work can still ask."""
        done = threading.Event()

        def close() -> None:
            if done.is_set():
                return
            done.set()
            if self.hud_on():
                self.hud.offer(None)
            closed = []
            for app in hogs:
                try:
                    if actions.close_app(app["app"]).get("windows_closed"):
                        closed.append(jarvis.app_name(app["app"]))
                except actions.ActionError as exc:
                    log.info("couldn't close %s: %s", app["app"], exc)
            memory = SAMPLER.sample(with_apps=False)["memory"]["used_percent"]
            text = (f"Closed {jarvis.names(closed)}, {self.address}. Memory is at {round(memory)}% now." if closed
                    else f"They didn't close, {self.address}; one may be asking to save something.")
            log.info("freed memory: %s", text)
            if self.hud_on():
                self.hud.log(f"close app · {', '.join(closed) or 'none'}")
            self.show("Jarvis", reply=text, hide_after=8)
            self.speak(text)

        label = "Close " + jarvis.names([jarvis.app_name(a["app"]) for a in hogs])
        if self.hud_on():
            offer_id = f"offer-{time.monotonic_ns()}"
            self.offers[offer_id] = close
            self.hud.offer(label, offer_id)
        if listen:
            self.expect_reply(lambda said: close() if AFFIRMATIVE.search(said) else None)

    def refresh_day(self) -> None:
        """Every 5 minutes: your day and how to address you, from Custodian; every 30, the weather."""
        tick = 0
        while not self.stopping.is_set():
            try:
                self.day = self.client.hud()
                self.address = self.day.get("address") or self.address
                if tick % 6 == 0 and self.day.get("city"):
                    self.weather = weather.current(self.day["city"])
                if self.hud_on():
                    self.hud.send({"type": "link", "connected": True})
                self.push_day()
            except NotSignedIn:
                pass
            except Exception as exc:  # noqa: BLE001 - the server or the weather service is down: try later
                log.info("couldn't refresh the day: %s", exc)
                if self.hud_on():
                    self.hud.send({"type": "link", "connected": False})
            tick += 1
            self.stopping.wait(300)

    def greet(self) -> None:
        """Hello when the app starts - unless the morning briefing is about to say it."""
        if not self.settings.greet or (dt.datetime.now().hour in MORNING and uptime_seconds() < 15 * 60):
            return
        for _ in range(10):  # the day's details come from the server; wait a little for them
            if self.day:
                break
            self.stopping.wait(1)
        if self.day.get("quiet"):
            return
        battery = SAMPLER.sample(with_apps=False).get("battery")
        warnings = [f"the battery is at {battery['percent']}%"] if battery and not battery["charging"] and battery["percent"] <= 20 else []
        upcoming = next((e for e in self.day.get("events", []) if not e.get("past")), None)
        text = jarvis.greeting(self.address, warnings=warnings, next_event=upcoming)
        self.show("Jarvis", reply=text, hide_after=10)
        self.speak(text)

    @staticmethod
    def action_label(function: str, args: dict) -> str:
        """'open · chrome' for the HUD's activity list - never a message's or a file's text."""
        target = next((args[k] for k in ("target", "query", "name", "title", "contact", "site", "page", "mode", "level",
                                         "keys", "path", "seconds", "time") if args.get(k) not in (None, "")), None)
        return function.replace("_", " ") + (f" · {str(target)[:60]}" if target is not None else "")

    # Speaking first ---------------------------------------------------------------------------------

    def talk(self, text: str) -> None:
        """Say it, listening only for the wake word meanwhile (to be interrupted), not to itself."""
        self.listener.speaking(True)
        if self.hud_on():
            self.hud.state("speaking")
        try:
            self.speaker.say(text)
        finally:
            self.listener.speaking(False)
            if self.hud_on():
                self.hud.state("idle")

    def speak(self, text: str) -> None:
        if not self.settings.speak_announcements:
            return
        self.talk(text)

    def announce(self, notice: dict) -> None:
        """A reminder, a meeting about to start, a watcher's alert: show it, say it, and for a
        meeting with a link, offer to open it - by button, or by saying "yes"."""
        link = notice.get("link")
        opened = threading.Event()

        def open_link() -> None:
            if link and not opened.is_set():
                opened.set()
                actions.open(link)  # you pressed the button or said yes: that's the go-ahead
                self.show("Opening the meeting link", hide_after=4)
                if self.hud_on():
                    self.hud.offer(None)

        if self.hud_on():
            self.show(notice.get("title") or "Custodian", heard="", reply=" - ".join(
                p for p in (notice.get("title"), notice.get("body")) if p), hide_after=30)
            if link:
                offer_id = f"offer-{time.monotonic_ns()}"
                self.offers[offer_id] = open_link
                self.hud.offer("Open the link", offer_id)
        else:
            self.window.announce(notice.get("title") or "Custodian", notice.get("body"),
                                 "Open the link" if link else None, open_link if link else None)
        self.speak(notice.get("say") or notice.get("title") or "")
        if link and not self.paused and not opened.is_set() and self.settings.speak_announcements:
            self.expect_reply(lambda said: open_link() if AFFIRMATIVE.search(said) else None)

    def expect_reply(self, callback, seconds: float = 8) -> None:  # noqa: ANN001
        """Listen once, without the wake word, for an answer to what was just asked."""
        self.awaiting = (callback, time.monotonic() + seconds)
        self.listener.trigger()

    def morning_briefing(self) -> None:
        try:
            result = self.client.briefing()
        except Exception as exc:  # noqa: BLE001 - the server isn't up yet: no briefing today, no harm
            log.info("no morning briefing: %s", exc)
            return
        text = result.get("text")
        if not text:
            return
        self.show("Your morning", reply=text, hide_after=45)
        self.speak(text)

    def brief_now(self) -> None:
        try:
            text = self.client.briefing(force=True).get("text")
        except Exception as exc:  # noqa: BLE001
            self.show("No briefing", reply=str(exc)[:200], hide_after=6)
            return
        if text:
            self.show("Your briefing", reply=text, hide_after=45)
            self.speak(text)

    def wrap_up_now(self) -> None:
        """The tray's "Wrap up my day": the server makes it and hands it back to be said here (quiet
        hours or not - you asked for it)."""
        try:
            result = self.client.evening()
        except Exception as exc:  # noqa: BLE001
            self.show("No summary", reply=str(exc)[:200], hide_after=6)
            return
        if result.get("text") and not result.get("spoken"):
            self.show("Your day", reply=result["text"], hide_after=45)
            self.talk(result["text"])

    def check_updates(self) -> None:
        """The tray's "Check for updates": ask the server (which asks GitHub), and offer to update."""
        try:
            found = self.client.version(refresh=True)
        except Exception as exc:  # noqa: BLE001
            self.show("Couldn't check for updates", reply=str(exc)[:200], hide_after=6)
            return
        if found.get("error"):
            self.show("Couldn't check for updates", reply=found["error"], hide_after=8)
        elif not found.get("update_available"):
            self.show("Custodian is up to date", reply=f"Version {found.get('commit') or '?'}.", hide_after=6)
        else:
            news = "; ".join(found.get("new", [])[:3])
            text = f"{found['behind']} update{'s' if found['behind'] != 1 else ''} waiting: {news}"
            if self.hud_on():
                offer_id = f"offer-{time.monotonic_ns()}"
                self.offers[offer_id] = self.update_now
                self.show("Update available", reply=text, hide_after=30)
                self.hud.offer("Update now", offer_id)
            else:
                self.window.announce("Update available", text, "Update now", self.update_now)

    def update_now(self) -> None:
        """update.ps1 in a window of its own: it stops Jarvis, updates, and starts it again."""
        import subprocess

        script = config.ROOT_DIR / "update.ps1"
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                         cwd=str(config.ROOT_DIR), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))

    def watch_presence(self) -> None:
        """Brief you when you arrive in the morning: the first touch of the keyboard or mouse after
        AWAY_SECONDS away, or the first minutes after the PC starts. The server says it once a day."""
        away = uptime_seconds() < 15 * 60
        while not self.stopping.is_set():
            try:
                idle = idle_seconds()
            except Exception:  # noqa: BLE001 - not on Windows
                return
            if idle >= AWAY_SECONDS:
                away = True
            elif away and idle < 30:
                away = False
                if dt.datetime.now().hour in MORNING:
                    threading.Thread(target=self.morning_briefing, daemon=True).start()
            self.stopping.wait(15)

    # Talking ------------------------------------------------------------------------------------

    def on_state(self, state: str) -> None:
        if state in ("recording", "following up"):
            if self.hud_on():
                self.hud.state("listening")
                self.hud.cancel_hide()
                if state == "recording":
                    self.hud.heard("")
                if not self.hud.visible:
                    self.hud.show("compact")
            elif state == "recording":
                self.show("Listening…", heard="", reply="")
            threading.Thread(target=self.client.warm, daemon=True).start()  # load the model while you talk
        elif state == "didn't catch that":
            if self.hud_on():
                self.hud.state("idle")
            self.show("Didn't catch that", hide_after=2)
        elif state == "wake word unavailable":  # Windows blocked a file it needs, say: the hotkey still works
            self.show("“Hey Jarvis” isn't working right now", reply="Windows blocked a file the wake word needs. "
                      "Press Ctrl+Alt+Space to talk - I'll keep trying, and tell you when it's back.", hide_after=12)
        elif state == "wake word back":
            self.show("“Hey Jarvis” is working again", hide_after=5)
        elif state == "conversation over":  # nothing said after a reply: go quiet, back to the wake word
            self.conversing = False
            if self.hud_on():
                self.hud.state("idle")
                self.hud.hide_later(4)

    def hear(self, wav: bytes) -> str:
        """What was said - only after voice ID (when it's on) says it's you, so nobody else's
        words are even transcribed. Raises PermissionError for someone else's voice."""
        if self.settings.voice_id:
            try:
                owner, similarity = self.voiceid.is_owner(from_wav(wav))
            except VoiceIdUnavailable as exc:  # fails closed
                raise PermissionError(f"Voice ID is on, but {exc}.") from None
            log.info("voice ID: %.2f (%s)", similarity, "owner" if owner else "refused")
            if not owner:
                raise PermissionError("That didn't sound like you, so I didn't act on it.")
        return self.client.transcribe(wav)

    def hear_early(self, wav: bytes) -> Future:
        """At the first pause: start hearing what's been said so far (Listener's on_early)."""
        return self.hearing.submit(self.hear, wav)

    def on_speech(self, wav: bytes, early: Future | None = None) -> None:
        if self.enrolling is not None:  # setting up voice ID: this is one of the phrases
            self.enroll_step(wav)
            return
        awaiting, self.awaiting = self.awaiting, None
        if awaiting is not None and time.monotonic() < awaiting[1]:  # the answer to an offer, not a new request
            try:
                awaiting[0](self.client.transcribe(wav))  # "yes" only opens a link, and is too short for voice ID
            except Exception as exc:  # noqa: BLE001
                log.info("couldn't hear the answer: %s", exc)
            return
        following, self.conversing = self.conversing, False
        try:
            if self.hud_on():
                self.hud.state("thinking")
            else:
                self.show("Transcribing…")
            try:
                text = early.result() if early is not None else self.hear(wav)
            except PermissionError as exc:
                self.show("Not your voice" if "sound like you" in str(exc) else "Voice ID", reply=str(exc), hide_after=6)
                if self.settings.speak_replies:
                    self.talk(jarvis.not_your_voice() if "sound like you" in str(exc)
                              else "Voice ID can't check your voice right now.")
                return
            if following and len(text.strip(" .!?,")) < 2:  # a follow-up that was only noise
                self.on_state("conversation over")
                return
            if jarvis.ends_conversation(text):  # "that's all", "thank you": no request, just a polite close
                self.show("Jarvis", heard=text, reply=jarvis.sign_off(self.address), hide_after=4)
                if self.settings.speak_replies:
                    self.talk(jarvis.sign_off(self.address))
                if self.hud_on() and self.hud.mode != "full":
                    self.hud.hide_later(3)
                return
            command = jarvis.hud_command(text)
            if command:  # "show the HUD", "hide the HUD": the PC's own business
                if self.hud is not None and command == "show":
                    self.hud.show("full")
                    self.push_vitals()
                    self.push_day()
                elif self.hud_on():
                    self.hud.hide()
                return
            self.show("Thinking…", heard=text, reply="")
            if self.hud_on():
                self.hud.state("thinking")
            if time.monotonic() - self.last_turn > CONVERSATION_MINUTES * 60:
                self.conversation_id = None
            result = self.client.chat(text, self.conversation_id, voice=True)
            self.conversation_id, self.last_turn = result["conversation_id"], time.monotonic()
            reply = result["reply"]
            self.show("Custodian", heard=text, reply=reply)
            if self.settings.speak_replies:
                self.talk(reply)
            if self.settings.conversation_mode and not self.paused:
                self.conversing = True
                self.listener.follow_up(self.settings.follow_up_seconds)  # go on without the wake word
            elif self.hud_on():
                self.hud.hide_later(10)
            else:
                self.show("Custodian", heard=text, reply=reply, hide_after=10)
        except NotSignedIn:
            self.show("Signed out", reply="Sign in again from the tray icon.", hide_after=8)
        except Exception as exc:  # noqa: BLE001
            self.show("Something went wrong", reply=str(exc)[:300], hide_after=8)
            if self.hud_on():
                self.hud.state("idle")
            if self.settings.speak_replies:
                self.speaker.say(jarvis.failure(self.address))

    def timer_rang(self, label: str) -> None:
        log.info("timer rang: %s", label)
        self.show("⏰ Time's up", reply=label, hide_after=30)
        try:
            import winsound

            for _ in range(3):
                winsound.Beep(988, 300)
                time.sleep(0.15)
        except (ImportError, RuntimeError):
            pass
        if self.settings.speak_replies:
            self.speaker.say(f"Time's up: {label}." if label != "Timer" else "Time's up.")

    def say_aloud(self, text: str) -> None:
        """pc_say: speak it, whatever "Speak replies" is set to - it was asked for."""
        self.show("Custodian", reply=text, hide_after=8)
        self.talk(text)

    def talk_now(self) -> None:
        self.speaker.stop()  # pressing it while it speaks cuts the reply short
        self.listener.trigger()

    # Voice ID -------------------------------------------------------------------------------------

    def start_enrolling(self) -> None:
        """Record PHRASES, one each time you're asked, then save your voiceprint."""
        self.enrolling = []
        self.show("Set up voice ID", reply="I'll ask you to say three short phrases, after the beep each time.",
                         hide_after=4)
        threading.Timer(3, self._ask_phrase).start()

    def _ask_phrase(self) -> None:
        if self.enrolling is None:
            return
        n = len(self.enrolling)
        self.show(f"Voice ID {n + 1}/{len(PHRASES)}", reply=f"Say: “{PHRASES[n]}”")
        self.listener.trigger()

    def enroll_step(self, wav: bytes) -> None:
        assert self.enrolling is not None
        self.enrolling.append(from_wav(wav))
        if len(self.enrolling) < len(PHRASES):
            threading.Timer(1, self._ask_phrase).start()
            return
        recordings, self.enrolling = self.enrolling, None
        try:
            self.voiceid.enroll(recordings)
        except (ValueError, VoiceIdUnavailable) as exc:
            self.show("Voice ID wasn't set up", reply=str(exc), hide_after=10)
            return
        self.settings.voice_id = True
        self.settings.save()
        self.show("Voice ID is on", reply="From now on I only take spoken requests in your voice.", hide_after=6)

    # Tray, hotkey, lifetime ------------------------------------------------------------------------

    def start_tray(self) -> None:
        import pystray

        def toggle_pause(_icon, _item) -> None:  # noqa: ANN001
            self.paused = not self.paused
            self.listener.pause(self.paused)
            self.show("Microphone off" if self.paused else f"Listening for “{self.settings.wake_phrase}”", hide_after=3)

        def toggle_speech(_icon, _item) -> None:  # noqa: ANN001
            self.settings.speak_replies = not self.settings.speak_replies
            self.settings.save()

        def toggle_announcements(_icon, _item) -> None:  # noqa: ANN001
            self.settings.speak_announcements = not self.settings.speak_announcements
            self.settings.save()

        def toggle_voice_id(_icon, _item) -> None:  # noqa: ANN001
            if not self.settings.voice_id and not self.voiceid.enrolled():
                self.start_enrolling()  # turns it on when the voice is set up
                return
            self.settings.voice_id = not self.settings.voice_id
            self.settings.save()

        def toggle_barge_in(_icon, _item) -> None:  # noqa: ANN001
            self.settings.barge_in = self.listener.barge_in = not self.settings.barge_in
            self.settings.save()

        def toggle_conversation(_icon, _item) -> None:  # noqa: ANN001
            self.settings.conversation_mode = not self.settings.conversation_mode
            self.settings.save()

        def sign_out(_icon, _item) -> None:  # noqa: ANN001
            config.set_token(self.settings.server, None)
            self.client.token = None
            threading.Thread(target=self.ensure_signed_in, daemon=True).start()

        menu = pystray.Menu(
            pystray.MenuItem("Talk now  (Ctrl+Alt+Space)", lambda *_: self.talk_now(), default=True),
            pystray.MenuItem("Show the HUD  (Ctrl+Alt+J)", lambda *_: threading.Thread(target=self.toggle_hud, daemon=True).start()),
            pystray.MenuItem("Conversation mode (follow-ups without the wake word)", toggle_conversation,
                             checked=lambda _i: self.settings.conversation_mode),
            pystray.MenuItem("Microphone off", toggle_pause, checked=lambda _i: self.paused),
            pystray.MenuItem("Speak replies", toggle_speech, checked=lambda _i: self.settings.speak_replies),
            pystray.MenuItem("Speak reminders and alerts", toggle_announcements,
                             checked=lambda _i: self.settings.speak_announcements),
            pystray.MenuItem("Brief me now", lambda *_: threading.Thread(target=self.brief_now, daemon=True).start()),
            pystray.MenuItem("Wrap up my day now", lambda *_: threading.Thread(target=self.wrap_up_now, daemon=True).start()),
            pystray.MenuItem(f"Interrupt by saying “{self.settings.wake_phrase}”", toggle_barge_in,
                             checked=lambda _i: self.settings.barge_in),
            pystray.MenuItem("Only my voice (voice ID)", toggle_voice_id, checked=lambda _i: self.settings.voice_id),
            pystray.MenuItem("Set up voice ID again", lambda *_: self.start_enrolling()),
            pystray.MenuItem("Open Custodian", lambda *_: webbrowser.open(self.settings.server)),
            pystray.MenuItem("Check for updates", lambda *_: threading.Thread(target=self.check_updates, daemon=True).start()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sign out", sign_out),
            pystray.MenuItem("Quit", lambda *_: self.quit()),
        )
        self.tray = pystray.Icon("custodian", _icon(), "Custodian", menu)
        self.tray.run_detached()

    def start_hotkey(self) -> None:
        from pynput import keyboard

        keys = {self.settings.hotkey: self.talk_now}
        if self.settings.hud_hotkey:
            keys[self.settings.hud_hotkey] = lambda: threading.Thread(target=self.toggle_hud, daemon=True).start()
        keyboard.GlobalHotKeys(keys).start()

    def quit(self) -> None:
        self.stopping.set()
        self.learner.stopping.set()
        self.hearing.shutdown(wait=False, cancel_futures=True)
        self.listener.stop()
        self.speaker.stop()
        if self.hud is not None:
            self.hud.stop()
        if self.tray is not None:
            self.tray.stop()
        self.window.quit()

    def run(self) -> None:
        def start() -> None:
            restored = actions.restore_alarms()  # alarms set before a restart still ring
            if restored:
                log.info("restored %d alarm(s)", restored)
            if not self.ensure_signed_in():
                return
            threading.Thread(target=self.bridge, daemon=True, name="bridge").start()
            threading.Thread(target=self.listener.run, daemon=True, name="listener").start()
            threading.Thread(target=self.indexer.run, daemon=True, name="indexer").start()
            threading.Thread(target=self.watch_presence, daemon=True, name="presence").start()
            threading.Thread(target=self.learner.run, daemon=True, name="learning").start()
            if self.hud is not None and not self.hud.start():
                log.info("the HUD isn't available; using the small panel")
            threading.Thread(target=self.refresh_day, daemon=True, name="day").start()
            threading.Thread(target=self.vitals, daemon=True, name="vitals").start()
            self.start_hotkey()
            hint = f"Say “{self.settings.wake_phrase}” or press Ctrl+Alt+Space" if self.settings.wake_word else "Press Ctrl+Alt+Space"
            if self.settings.greet and self.settings.speak_announcements:
                threading.Thread(target=self.greet, daemon=True, name="greeting").start()
            else:
                self.show("Jarvis is online", reply=hint, hide_after=5)

        self.start_tray()
        threading.Thread(target=start, daemon=True, name="start").start()
        self.window.mainloop()


def main() -> None:
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=config.APP_DIR / "desktop.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    App().run()


if __name__ == "__main__":
    main()
