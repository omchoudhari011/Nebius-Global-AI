"""Which Custodian this is, and whether a newer one is waiting on GitHub.

The install is a git checkout (install.ps1, Custodian Setup.exe), so the version is its commit,
and an update is the commits on its upstream branch it doesn't have yet. `git fetch` runs at most
every CHECK_HOURS, in the background, and never asks for anything: with no sign-in it already has,
the check just says it couldn't look. Updating itself is update.ps1's job (Start menu: Update
Custodian; the tray: Update now), not the server's.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone

from .config import BACKEND_DIR

log = logging.getLogger(__name__)

ROOT = BACKEND_DIR.parent
CHECK_HOURS = 12
_lock = threading.Lock()
_checked: dict = {}  # the last check: {"at": monotonic, "behind": n, "new": [...], "error": ...}
_checking = threading.Event()


def _git(*args: str, timeout: float = 20) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    done = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=timeout, env=env,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip()[:200] or f"git {args[0]} failed")
    return done.stdout.strip()


def current() -> dict:
    try:
        commit, date, subject = _git("log", "-1", "--format=%h%x09%cI%x09%s").split("\t", 2)
        return {"commit": commit, "date": date, "subject": subject, "git": True}
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        return {"commit": None, "git": False}


def _check() -> None:
    try:
        _git("fetch", "--quiet", timeout=45)
        behind = int(_git("rev-list", "--count", "HEAD..@{u}") or 0)
        new = _git("log", "--format=%s", "-n", "8", "HEAD..@{u}").splitlines() if behind else []
        result = {"behind": behind, "new": new, "error": None}
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        log.info("couldn't check for updates: %s", exc)
        result = {"behind": None, "new": [], "error": "couldn't reach GitHub (no network, or git isn't signed in)"}
    with _lock:
        _checked.clear()
        _checked.update(result, at=time.monotonic(), checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    _checking.clear()


def status(refresh: bool = False) -> dict:
    """This version, and what the last check found; starts a check when the last is old (or asked)."""
    with _lock:
        last = dict(_checked)
    stale = not last or time.monotonic() - last["at"] > CHECK_HOURS * 3600
    if (refresh or stale) and current()["git"] and not _checking.is_set():
        _checking.set()
        threading.Thread(target=_check, daemon=True, name="update-check").start()
    out = {**current(), "checking": _checking.is_set()}
    if last:
        out.update({k: last[k] for k in ("behind", "new", "error", "checked_at")})
        out["update_available"] = bool(last.get("behind"))
    return out


def wait_for_check(timeout: float = 60) -> None:
    """For a caller that wants the answer now ("Check for updates"): wait for a running check."""
    deadline = time.monotonic() + timeout
    while _checking.is_set() and time.monotonic() < deadline:
        time.sleep(0.2)
