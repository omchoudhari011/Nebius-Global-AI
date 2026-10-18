"""Learning from your corrections overnight: starts backend\\finetune\\nightly.py when it's night,
the PC has been idle for half an hour, it's plugged in and the server says there's something new
to learn (GET /learning) - and stops it the moment you touch the keyboard or mouse, so training
never slows down what you're doing. At most one run a night; its output goes to
%APPDATA%\\Custodian\\nightly.log.

Needs this repository's backend next to the desktop app (backend\\.venv, as run_finetune.ps1 does)
and one full run of run_finetune.ps1 first. "Learn from my corrections overnight" in the app's
Jarvis settings switches it off.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from . import config

log = logging.getLogger(__name__)

NIGHT = range(1, 6)  # starts between 01:00 and 05:59
IDLE_TO_START = 30 * 60  # seconds without a key press or mouse move
BACK_AFTER = 60  # idle shorter than this while it runs: you're back, stop it
CHECK_EVERY = 10 * 60
WATCH_EVERY = 15
BACKEND = Path(__file__).resolve().parents[2] / "backend"
CONTAINER = "custodian-nightly"  # finetune/nightly.py's training container


def _plugged_in() -> bool:
    try:
        import psutil

        battery = psutil.sensors_battery()
    except Exception:  # noqa: BLE001 - no battery information: a desktop PC
        return True
    return battery is None or bool(battery.power_plugged)


def _python() -> Path:
    windows = BACKEND / ".venv" / "Scripts" / "python.exe"
    return windows if windows.exists() else BACKEND / ".venv" / "bin" / "python"


class NightlyLearner:
    def __init__(self, client: Callable, server: Callable[[], str], idle: Callable[[], float], *,
                 now: Callable[[], dt.datetime] = dt.datetime.now, plugged_in: Callable[[], bool] = _plugged_in,
                 spawn: Callable | None = None) -> None:
        self.client, self.server, self.idle, self.now, self.plugged_in = client, server, idle, now, plugged_in
        self.spawn = spawn or self._spawn
        self.process = None
        self.last_night: dt.date | None = None
        self.stopping = threading.Event()

    def ready(self) -> bool:
        return _python().exists() and (BACKEND / "finetune" / "nightly.py").exists()

    def should_start(self) -> bool:
        moment = self.now()
        tonight = (moment - dt.timedelta(hours=12)).date()  # one run per night, either side of midnight
        if self.process is not None or self.last_night == tonight or moment.hour not in NIGHT:
            return False
        if self.idle() < IDLE_TO_START or not self.plugged_in():
            return False
        try:
            status = self.client().learning_status()
        except Exception as exc:  # noqa: BLE001 - server not up: try again later
            log.info("learning: no status (%s)", exc)
            return False
        self.last_night = tonight  # asked once tonight: nothing new means nothing to do till tomorrow
        return bool(status.get("due"))

    def _spawn(self):  # noqa: ANN202 - subprocess.Popen
        client = self.client()
        env = {**os.environ, "CUSTODIAN_TOKEN": client.token or "", "PYTHONIOENCODING": "utf-8"}
        logfile = open(config.APP_DIR / "nightly.log", "a", encoding="utf-8")  # noqa: SIM115 - the child owns it
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.BELOW_NORMAL_PRIORITY_CLASS
        return subprocess.Popen([str(_python()), "finetune/nightly.py", "--server", self.server()],  # noqa: S603
                                cwd=BACKEND, env=env, stdout=logfile, stderr=subprocess.STDOUT, creationflags=flags)

    def start(self) -> None:
        log.info("learning: starting tonight's run")
        self.process = self.spawn()

    def stop(self, why: str) -> None:
        """You came back: stop the training container and the run, and say so to the server."""
        process, self.process = self.process, None
        if process is None:
            return
        log.info("learning: stopping (%s)", why)
        subprocess.run(["docker", "stop", "-t", "5", CONTAINER], capture_output=True, check=False)
        if process.poll() is None:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False)
            else:
                process.terminate()
        try:
            self.client().learning_result("stopped", f"Stopped: {why}. I'll try again another night.")
        except Exception:  # noqa: BLE001
            pass

    def tick(self) -> None:
        """One check: start, watch or tidy up. The loop calls it; tests call it directly."""
        if self.process is not None:
            if self.process.poll() is not None:  # finished: nightly.py reported its result itself
                log.info("learning: tonight's run ended (exit %s)", self.process.returncode)
                self.process = None
            elif self.idle() < BACK_AFTER:
                self.stop("you came back to the PC")
            return
        if self.should_start():
            self.start()

    def run(self) -> None:
        if not self.ready():
            log.info("learning overnight unavailable: no backend\\.venv next to the desktop app")
            return
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - never take the app down
                log.exception("learning")
            self.stopping.wait(WATCH_EVERY if self.process is not None else CHECK_EVERY)
        self.stop("Custodian was closed")
