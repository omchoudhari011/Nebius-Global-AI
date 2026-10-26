"""The HUD's own process: one frameless, always-on-top window showing web/hud.html (pywebview,
with Windows' built-in Edge WebView2).

    pythonw -m custodian_desktop.hud_window        (started by hud.py, never by hand)

It's a separate process because the desktop app's main thread already runs tkinter (the
confirmation dialogs) and a web view needs a main thread of its own. The app writes one JSON
message per line on stdin - {"type": "show", "mode": "compact" | "full"}, {"type": "hide"},
{"type": "quit"}, and everything else is passed to the page's hud.receive() - and reads what
you do in the HUD from stdout: {"cmd": "talk" | "mode" | "hide" | "offer", ...}. When the app
goes away (stdin closes), the window closes too.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

PAGE = Path(__file__).with_name("web") / "hud.html"
COMPACT = (440, 500)
MARGIN, TASKBAR = 18, 56


def emit(payload: dict) -> None:
    try:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):  # the app is gone
        pass


def display_scale() -> float:
    """Windows' display scaling (1.25 at 125%)."""
    try:
        import ctypes

        return ctypes.windll.user32.GetDpiForSystem() / 96 or 1.0
    except (AttributeError, OSError):
        return 1.0


def work_area() -> tuple[int, int, int, int]:
    """The main screen without the taskbar, in physical pixels (left, top, right, bottom)."""
    try:
        import ctypes
        from ctypes import wintypes

        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return rect.left, rect.top, rect.right, rect.bottom
    except (AttributeError, OSError):
        pass
    return 0, 0, 1920, 1080 - TASKBAR


class Bridge:
    """What the page can call (window.pywebview.api.command)."""

    def command(self, payload: dict) -> None:
        if isinstance(payload, dict) and payload.get("cmd") in ("talk", "mode", "hide", "offer"):
            emit({k: payload[k] for k in ("cmd", "mode", "id") if k in payload})


def main() -> None:
    import webview

    window = webview.create_window(
        "CUSTODIAN", url=PAGE.as_uri(), js_api=Bridge(), width=COMPACT[0], height=COMPACT[1],
        frameless=True, on_top=True, hidden=True, background_color="#03070d", resizable=True, focus=False,
    )
    loaded = threading.Event()
    window.events.loaded += loaded.set
    # pywebview runs pump() in a thread that isn't a daemon, so once the window has gone (it can, around
    # the PC sleeping) the process would wait on stdin forever with nothing to show, and the app would
    # keep sending to it. Ending the process lets the app see that and start a new HUD.
    window.events.closed += lambda: os._exit(0)

    def place(mode: str) -> None:
        screen, scale = webview.screens[0], display_scale()
        if mode == "full":  # screens are in physical pixels; resize() and move() scale what they're given
            window.resize(round(screen.width / scale), round(screen.height / scale))
            window.move(round(screen.x / scale), round(screen.y / scale))
        else:
            left, top, right, bottom = work_area()
            window.resize(*COMPACT)
            window.move(round(right / scale) - COMPACT[0] - MARGIN, round(bottom / scale) - COMPACT[1] - MARGIN)

    def to_page(message: dict) -> None:
        window.evaluate_js(f"window.hud && window.hud.receive({json.dumps(message)})")

    def pump() -> None:
        loaded.wait(30)
        emit({"event": "ready"})
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            kind = message.get("type")
            try:
                if kind == "show":
                    mode = "full" if message.get("mode") == "full" else "compact"
                    place(mode)
                    to_page({"type": "mode", "mode": mode})
                    window.show()
                elif kind == "hide":
                    window.hide()
                elif kind == "quit":
                    break
                else:
                    to_page(message)
            except Exception:  # noqa: BLE001 - one bad message mustn't close the HUD
                continue
        window.destroy()

    webview.start(pump, private_mode=True)
    os._exit(0)  # the window is gone: don't wait for pump()


if __name__ == "__main__":
    main()
