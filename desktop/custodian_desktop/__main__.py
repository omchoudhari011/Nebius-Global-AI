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

from . import actions, config
from .client import Client, NotSignedIn
from .indexer import Indexer
from .learning import NightlyLearner
from .ui import Window
from .voice import Listener, Speaker, from_wav
from .voiceid import PHRASES, VoiceId, VoiceIdUnavailable

log = logging.getLogger("custodian.desktop")
CONVERSATION_MINUTES = 15  # voice turns this close together continue the same chat
ACTS_ON_FRONT_WINDOW = {"type_text", "press_keys", "click", "click_element", "fill_field", "capture"}
AWAY_SECONDS = 3 * 3600  # this long without a key or mouse touch, and the next touch is "arriving"
MORNING = range(5, 12)  # hours when arriving gets the morning briefing
# A spoken "yes" to an offer ("Shall I open the meeting link?"), in English, Hindi or Marathi.
AFFIRMATIVE = re.compile(r"^\W*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|please|go\s+ahead|open\s+it|do\s+it|haan?|ha|ho|hoy|ji|chalo|kholo"
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
    def __init__(self) -> None:
        self.settings = config.Settings.load()
        self.window = Window(on_quit=self.quit)
        self.client = Client(self.settings.server, config.get_token(self.settings.server))
        self.speaker = Speaker(config.APP_DIR / "voices", self.settings.voice, self.settings.hindi_voice)
        self.voiceid = VoiceId(threshold=self.settings.voice_id_threshold)
        self.enrolling: list | None = None  # recordings so far while setting up voice ID
        self.hearing = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hearing")
        self.listener = Listener(self.on_speech, self.on_state, wake_word=self.settings.wake_word,
                                 wake_model=config.wake_model_path(self.settings.wake_model),
                                 on_early=self.hear_early if self.settings.fast_transcribe else None,
                                 barge_in=self.settings.barge_in, on_barge=self.speaker.stop,
                                 threshold=self.settings.wake_threshold, device=self.settings.microphone)
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
                    self.window.show("Can't reach the Custodian server", reply=str(exc)[:200], hide_after=6)
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
                self.window.show("Sign-in failed", reply=str(exc), hide_after=8)
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
            self.window.show("Allowed from your phone", reply=details[:300], hide_after=6)
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
        except Exception as exc:  # noqa: BLE001 - reported to the model
            self.client.send_result(action["id"], False, error=f"{type(exc).__name__}: {exc}"[:500])
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

    # Speaking first ---------------------------------------------------------------------------------

    def talk(self, text: str) -> None:
        """Say it, listening only for the wake word meanwhile (to be interrupted), not to itself."""
        self.listener.speaking(True)
        try:
            self.speaker.say(text)
        finally:
            self.listener.speaking(False)

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
                self.window.show("Opening the meeting link", hide_after=4)

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
        self.window.show("Your morning", reply=text, hide_after=45)
        self.speak(text)

    def brief_now(self) -> None:
        try:
            text = self.client.briefing(force=True).get("text")
        except Exception as exc:  # noqa: BLE001
            self.window.show("No briefing", reply=str(exc)[:200], hide_after=6)
            return
        if text:
            self.window.show("Your briefing", reply=text, hide_after=45)
            self.speak(text)

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
        if state == "recording":
            self.window.show("Listening…", heard="", reply="")
            threading.Thread(target=self.client.warm, daemon=True).start()  # load the model while you talk
        elif state == "didn't catch that":
            self.window.show("Didn't catch that", hide_after=2)

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
        try:
            self.window.show("Transcribing…")
            try:
                text = early.result() if early is not None else self.hear(wav)
            except PermissionError as exc:
                self.window.show("Not your voice" if "sound like you" in str(exc) else "Voice ID", reply=str(exc), hide_after=6)
                if self.settings.speak_replies:
                    self.talk("Sorry, I only take requests from my owner's voice." if "sound like you" in str(exc)
                              else "Voice ID can't check your voice right now.")
                return
            self.window.show("Thinking…", heard=text, reply="")
            if time.monotonic() - self.last_turn > CONVERSATION_MINUTES * 60:
                self.conversation_id = None
            result = self.client.chat(text, self.conversation_id)
            self.conversation_id, self.last_turn = result["conversation_id"], time.monotonic()
            reply = result["reply"]
            self.window.show("Custodian", heard=text, reply=reply)
            if self.settings.speak_replies:
                self.talk(reply)
            self.window.show("Custodian", heard=text, reply=reply, hide_after=10)
        except NotSignedIn:
            self.window.show("Signed out", reply="Sign in again from the tray icon.", hide_after=8)
        except Exception as exc:  # noqa: BLE001
            self.window.show("Something went wrong", reply=str(exc)[:300], hide_after=8)
            if self.settings.speak_replies:
                self.speaker.say("Sorry, something went wrong.")

    def timer_rang(self, label: str) -> None:
        log.info("timer rang: %s", label)
        self.window.show("⏰ Time's up", reply=label, hide_after=30)
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
        self.window.show("Custodian", reply=text, hide_after=8)
        self.talk(text)

    def talk_now(self) -> None:
        self.speaker.stop()  # pressing it while it speaks cuts the reply short
        self.listener.trigger()

    # Voice ID -------------------------------------------------------------------------------------

    def start_enrolling(self) -> None:
        """Record PHRASES, one each time you're asked, then save your voiceprint."""
        self.enrolling = []
        self.window.show("Set up voice ID", reply="I'll ask you to say three short phrases, after the beep each time.",
                         hide_after=4)
        threading.Timer(3, self._ask_phrase).start()

    def _ask_phrase(self) -> None:
        if self.enrolling is None:
            return
        n = len(self.enrolling)
        self.window.show(f"Voice ID {n + 1}/{len(PHRASES)}", reply=f"Say: “{PHRASES[n]}”")
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
            self.window.show("Voice ID wasn't set up", reply=str(exc), hide_after=10)
            return
        self.settings.voice_id = True
        self.settings.save()
        self.window.show("Voice ID is on", reply="From now on I only take spoken requests in your voice.", hide_after=6)

    # Tray, hotkey, lifetime ------------------------------------------------------------------------

    def start_tray(self) -> None:
        import pystray

        def toggle_pause(_icon, _item) -> None:  # noqa: ANN001
            self.paused = not self.paused
            self.listener.pause(self.paused)
            self.window.show("Microphone off" if self.paused else f"Listening for “{self.settings.wake_phrase}”", hide_after=3)

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

        def sign_out(_icon, _item) -> None:  # noqa: ANN001
            config.set_token(self.settings.server, None)
            self.client.token = None
            threading.Thread(target=self.ensure_signed_in, daemon=True).start()

        menu = pystray.Menu(
            pystray.MenuItem("Talk now  (Ctrl+Alt+Space)", lambda *_: self.talk_now(), default=True),
            pystray.MenuItem("Microphone off", toggle_pause, checked=lambda _i: self.paused),
            pystray.MenuItem("Speak replies", toggle_speech, checked=lambda _i: self.settings.speak_replies),
            pystray.MenuItem("Speak reminders and alerts", toggle_announcements,
                             checked=lambda _i: self.settings.speak_announcements),
            pystray.MenuItem("Brief me now", lambda *_: threading.Thread(target=self.brief_now, daemon=True).start()),
            pystray.MenuItem(f"Interrupt by saying “{self.settings.wake_phrase}”", toggle_barge_in,
                             checked=lambda _i: self.settings.barge_in),
            pystray.MenuItem("Only my voice (voice ID)", toggle_voice_id, checked=lambda _i: self.settings.voice_id),
            pystray.MenuItem("Set up voice ID again", lambda *_: self.start_enrolling()),
            pystray.MenuItem("Open Custodian", lambda *_: webbrowser.open(self.settings.server)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sign out", sign_out),
            pystray.MenuItem("Quit", lambda *_: self.quit()),
        )
        self.tray = pystray.Icon("custodian", _icon(), "Custodian", menu)
        self.tray.run_detached()

    def start_hotkey(self) -> None:
        from pynput import keyboard

        keyboard.GlobalHotKeys({self.settings.hotkey: self.talk_now}).start()

    def quit(self) -> None:
        self.stopping.set()
        self.learner.stopping.set()
        self.hearing.shutdown(wait=False, cancel_futures=True)
        self.listener.stop()
        self.speaker.stop()
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
            self.start_hotkey()
            hint = f"Say “{self.settings.wake_phrase}” or press Ctrl+Alt+Space" if self.settings.wake_word else "Press Ctrl+Alt+Space"
            self.window.show("Custodian is ready", reply=hint, hide_after=5)

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
