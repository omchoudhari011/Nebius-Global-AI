"""The desktop app's side of the HUD: starts the HUD window's process (hud_window.py), tells it
what's happening, and hands what you do in it back to the app.

    hud = Hud(on_command)          # on_command({"cmd": "talk"}) etc., on the reader thread
    hud.start()                    # False if the HUD can't run here (then the small panel is used)
    hud.show("compact" | "full"); hud.hide()
    hud.state("listening"); hud.level(0.4); hud.heard(text); hud.reply(text)
    hud.send({"type": "stats", "data": {...}})

If the window process dies it's started again on the next show(); sending to it is never allowed
to fail the caller.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)
PACKAGE_PARENT = Path(__file__).resolve().parent.parent
REVIVE_SECONDS = 30


def available() -> bool:
    try:
        import webview  # noqa: F401
    except ImportError:
        return False
    return (Path(__file__).with_name("web") / "hud.html").exists()


class Hud:
    def __init__(self, on_command: Callable[[dict], None]) -> None:
        self.on_command = on_command
        self.proc: subprocess.Popen | None = None
        self.mode = "compact"
        self.visible = False
        self.ready = threading.Event()
        self._lock = threading.Lock()
        self._hide_timer: threading.Timer | None = None
        self._revived = -1e9  # when the HUD was last started again after going away (monotonic)

    # The process ------------------------------------------------------------------------------

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def revive(self) -> bool:
        """Running - or started again if it went away by itself (its window closed), at most every
        REVIVE_SECONDS. Stopped on purpose (stop(), proc None) it stays stopped."""
        if self.running():
            return True
        if self.proc is None or time.monotonic() - self._revived < REVIVE_SECONDS:
            return False
        self._revived = time.monotonic()
        log.info("the HUD went away; starting it again")
        self.proc = None
        return self.start()

    def start(self) -> bool:
        if self.running():
            return True
        if not available():
            return False
        self.ready.clear()
        try:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "custodian_desktop.hud_window"], cwd=str(PACKAGE_PARENT),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            log.warning("the HUD couldn't start: %s", exc)
            self.proc = None
            return False
        threading.Thread(target=self._read, args=(self.proc,), daemon=True, name="hud-reader").start()
        self.visible = False
        return True

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("event") == "ready":
                self.ready.set()
                continue
            if message.get("cmd") == "hide":  # ✕ or Esc: the window hides only when told to, so tell it
                self.hide()
            elif message.get("cmd") == "mode":
                self.show(message.get("mode", "compact"))
                continue
            try:
                self.on_command(message)
            except Exception:  # noqa: BLE001 - a command handler's bug mustn't kill the reader
                log.exception("HUD command %s failed", message)
        log.info("the HUD window closed")

    def stop(self) -> None:
        self.send({"type": "quit"})
        if self.proc is not None:
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def send(self, message: dict) -> None:
        if not self.running():
            return
        with self._lock:
            try:
                self.proc.stdin.write(json.dumps(message, default=str) + "\n")  # type: ignore[union-attr]
                self.proc.stdin.flush()  # type: ignore[union-attr]
            except (OSError, ValueError):
                log.info("the HUD window went away")

    # Showing --------------------------------------------------------------------------------------

    def show(self, mode: str | None = None, *, hide_after: float | None = None) -> None:
        if not self.running() and not self.start():
            return
        self.ready.wait(15)
        self.mode = mode or self.mode
        self.cancel_hide()
        self.send({"type": "show", "mode": self.mode})
        self.visible = True
        if hide_after and self.mode != "full":
            self.hide_later(hide_after)

    def hide(self) -> None:
        self.cancel_hide()
        self.send({"type": "hide"})
        self.visible = False

    def toggle_full(self) -> None:
        if self.visible and self.mode == "full":
            self.hide()
        else:
            self.show("full")

    def hide_later(self, seconds: float) -> None:
        """Hide the compact panel after `seconds` unless something shows it again (the full HUD stays)."""
        self.cancel_hide()
        if self.mode == "full":
            return
        self._hide_timer = threading.Timer(seconds, self.hide)
        self._hide_timer.daemon = True
        self._hide_timer.start()

    def cancel_hide(self) -> None:
        if self._hide_timer is not None:
            self._hide_timer.cancel()
            self._hide_timer = None

    # What's happening -------------------------------------------------------------------------------

    def state(self, state: str) -> None:
        self.send({"type": "state", "state": state})

    def level(self, value: float) -> None:
        self.send({"type": "level", "value": round(max(0.0, min(1.0, value)), 3)})

    def heard(self, text: str) -> None:
        self.send({"type": "heard", "text": text})

    def reply(self, text: str) -> None:
        self.send({"type": "reply", "text": text})

    def log(self, text: str, warn: bool = False) -> None:
        self.send({"type": "log", "text": text, "warn": warn})

    def alert(self, text: str) -> None:
        self.send({"type": "alert", "text": text})

    def offer(self, text: str | None, offer_id: str | None = None) -> None:
        self.send({"type": "offer", "text": text, "id": offer_id})


class Throttle:
    """At most one call per `every` seconds (the microphone level comes 12 times a second)."""

    def __init__(self, every: float) -> None:
        self.every, self.last = every, 0.0

    def ready(self) -> bool:
        now = time.monotonic()
        if now - self.last >= self.every:
            self.last = now
            return True
        return False
