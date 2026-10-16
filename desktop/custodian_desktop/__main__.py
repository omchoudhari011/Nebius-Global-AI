"""Starts Custodian for the desktop:

    .venv\\Scripts\\pythonw -m custodian_desktop        (desktop\\run-desktop.ps1 does this)

Threads: the window (main thread), the PC-action bridge (long-polls the server), the
microphone listener (wake word "Hey Jarvis"), the global hotkey, and the tray icon.
"""

from __future__ import annotations

import logging
import threading
import time
import webbrowser

from PIL import Image, ImageDraw

from . import actions, config
from .client import Client, NotSignedIn
from .ui import Window
from .voice import Listener, Speaker

log = logging.getLogger("custodian.desktop")
CONVERSATION_MINUTES = 15  # voice turns this close together continue the same chat


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
        self.speaker = Speaker(config.APP_DIR / "voices", self.settings.voice)
        self.listener = Listener(self.on_speech, self.on_state, wake_word=self.settings.wake_word,
                                 threshold=self.settings.wake_threshold, device=self.settings.microphone)
        self.conversation_id: int | None = None
        self.last_turn = 0.0
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
        if action.get("confirm"):
            allowed = self.window.confirm(action.get("description", function).split("(")[0].strip().rstrip("."),
                                          actions.describe(function, args))
            log.info("%s %s %s", "ALLOWED" if allowed else "DECLINED", function, actions.describe(function, args)[:200])
            if not allowed:
                self.client.send_result(action["id"], False, error="The user declined this on the PC.")
                return
        try:
            result = actions.run(function, args, self.settings.protected)
            self.client.send_result(action["id"], True, result=result)
        except Exception as exc:  # noqa: BLE001 - reported to the model
            self.client.send_result(action["id"], False, error=f"{type(exc).__name__}: {exc}"[:500])

    # Talking ------------------------------------------------------------------------------------

    def on_state(self, state: str) -> None:
        if state == "recording":
            self.window.show("Listening…", heard="", reply="")
        elif state == "didn't catch that":
            self.window.show("Didn't catch that", hide_after=2)

    def on_speech(self, wav: bytes) -> None:
        try:
            self.window.show("Transcribing…")
            text = self.client.transcribe(wav)
            self.window.show("Thinking…", heard=text, reply="")
            if time.monotonic() - self.last_turn > CONVERSATION_MINUTES * 60:
                self.conversation_id = None
            result = self.client.chat(text, self.conversation_id)
            self.conversation_id, self.last_turn = result["conversation_id"], time.monotonic()
            reply = result["reply"]
            self.window.show("Custodian", heard=text, reply=reply)
            if self.settings.speak_replies:
                self.listener.pause(True)  # don't hear itself
                try:
                    self.speaker.say(reply)
                finally:
                    self.listener.pause(self.paused)
            self.window.show("Custodian", heard=text, reply=reply, hide_after=10)
        except NotSignedIn:
            self.window.show("Signed out", reply="Sign in again from the tray icon.", hide_after=8)
        except Exception as exc:  # noqa: BLE001
            self.window.show("Something went wrong", reply=str(exc)[:300], hide_after=8)
            if self.settings.speak_replies:
                self.speaker.say("Sorry, something went wrong.")

    def talk_now(self) -> None:
        self.speaker.stop()  # pressing it while it speaks cuts the reply short
        self.listener.trigger()

    # Tray, hotkey, lifetime ------------------------------------------------------------------------

    def start_tray(self) -> None:
        import pystray

        def toggle_pause(_icon, _item) -> None:  # noqa: ANN001
            self.paused = not self.paused
            self.listener.pause(self.paused)
            self.window.show("Microphone off" if self.paused else "Listening for “Hey Jarvis”", hide_after=3)

        def toggle_speech(_icon, _item) -> None:  # noqa: ANN001
            self.settings.speak_replies = not self.settings.speak_replies
            self.settings.save()

        def sign_out(_icon, _item) -> None:  # noqa: ANN001
            config.set_token(self.settings.server, None)
            self.client.token = None
            threading.Thread(target=self.ensure_signed_in, daemon=True).start()

        menu = pystray.Menu(
            pystray.MenuItem("Talk now  (Ctrl+Alt+Space)", lambda *_: self.talk_now(), default=True),
            pystray.MenuItem("Microphone off", toggle_pause, checked=lambda _i: self.paused),
            pystray.MenuItem("Speak replies", toggle_speech, checked=lambda _i: self.settings.speak_replies),
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
        self.listener.stop()
        self.speaker.stop()
        if self.tray is not None:
            self.tray.stop()
        self.window.quit()

    def run(self) -> None:
        def start() -> None:
            if not self.ensure_signed_in():
                return
            threading.Thread(target=self.bridge, daemon=True, name="bridge").start()
            threading.Thread(target=self.listener.run, daemon=True, name="listener").start()
            self.start_hotkey()
            hint = "Say “Hey Jarvis” or press Ctrl+Alt+Space" if self.settings.wake_word else "Press Ctrl+Alt+Space"
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
