"""The bridge to the Custodian desktop app, which carries out PC actions on your computer.

The model's PC tool calls (registry functions with runs_on="desktop") pass the same gate as
every tool - your switch, the skill's allowlist, validated arguments - and then wait here for
the desktop app, which keeps a long-poll open:

    GET  /desktop/next          the next action for this account (or 204 after ~25 s)
    POST /desktop/results/{id}  what happened
    GET  /desktop/status        whether a desktop app is connected (for the app's settings)

The desktop app decides on its own side, with a dialog on your screen, whether to go ahead
with anything that changes or reveals something (registry `confirm`): the server can't skip
that step, and a denial comes back as "the user declined". If no desktop app has checked in
recently, the call fails at once with a clear message instead of hanging.
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
from dataclasses import dataclass, field

CONNECTED_SECONDS = 40  # a desktop app that polled within this long counts as connected
WAIT_SECONDS = 25  # how long one long-poll waits for an action
CONFIRM_TIMEOUT = 120  # an action that needs your OK waits this long for an answer
ACTION_TIMEOUT = 45


NOT_CONNECTED = ("The Custodian desktop app isn't running on the PC, so I can't control it right now. "
                 "Start it (desktop\\run-desktop.ps1) and ask again.")


class DesktopUnavailable(RuntimeError):
    """No desktop app is connected; the message is safe to show."""


@dataclass
class _Pending:
    id: int
    user_id: int
    function: str
    args: dict
    confirm: bool
    description: str
    done: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    reply: bool = True  # False: told, not asked - nobody waits for a result (post())
    expires: float | None = None  # monotonic time after which it's dropped undelivered
    approved: str | None = None  # "remote": you allowed it in the app or on Telegram (app/approvals.py)


_lock = threading.Lock()
_ids = itertools.count(1)
_queues: dict[int, queue.Queue[_Pending]] = {}
_pending: dict[int, _Pending] = {}
_seen: dict[int, dict] = {}  # user id -> {"at": monotonic time, "device": name}


def _queue(user_id: int) -> queue.Queue[_Pending]:
    with _lock:
        return _queues.setdefault(user_id, queue.Queue())


def touch(user_id: int, device: str | None = None) -> None:
    with _lock:
        previous = _seen.get(user_id, {})
        _seen[user_id] = {"at": time.monotonic(), "device": device or previous.get("device")}


def status(user_id: int) -> dict:
    with _lock:
        seen = _seen.get(user_id)
    connected = bool(seen and time.monotonic() - seen["at"] < CONNECTED_SECONDS)
    return {"connected": connected, "device": seen.get("device") if seen else None}


def run(user_id: int, function: str, args: dict, *, confirm: bool, description: str, approved: str | None = None) -> object:
    """Hand an action to the desktop app and wait for its result. Raises DesktopUnavailable,
    TimeoutError, or RuntimeError with the desktop's error (including "the user declined").
    `approved="remote"` says it was already allowed in the app: the PC shows that, without asking."""
    if not status(user_id)["connected"]:
        raise DesktopUnavailable(NOT_CONNECTED)
    action = _Pending(next(_ids), user_id, function, args, confirm, description, approved=approved)
    with _lock:
        _pending[action.id] = action
    _queue(user_id).put(action)
    try:
        if not action.done.wait(CONFIRM_TIMEOUT if confirm else ACTION_TIMEOUT):
            raise TimeoutError("The desktop app didn't answer in time" + (" (nobody confirmed it)" if confirm else ""))
    finally:
        with _lock:
            _pending.pop(action.id, None)
    result = action.result or {}
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "the desktop app couldn't do it")[:500])
    return result.get("result")


def post(user_id: int, function: str, args: dict, *, ttl: float = 600) -> bool:
    """Tell the desktop app something without waiting for an answer - speak an announcement,
    "index the files now". Dropped if no desktop app is connected, or if it isn't picked up
    within `ttl` seconds (a reminder read out an hour late is worse than none). Never asks first:
    only for things that change nothing on the PC."""
    if not status(user_id)["connected"]:
        return False
    action = _Pending(next(_ids), user_id, function, args, False, "", reply=False, expires=time.monotonic() + ttl)
    with _lock:
        _pending[action.id] = action
    _queue(user_id).put(action)
    return True


def next_action(user_id: int, device: str | None, wait: float = WAIT_SECONDS) -> dict | None:
    """For the desktop app's long-poll: the next action, or None after `wait` seconds."""
    touch(user_id, device)
    q = _queue(user_id)
    deadline = time.monotonic() + wait
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            action = q.get(timeout=min(remaining, 5))
        except queue.Empty:
            touch(user_id)
            continue
        with _lock:
            alive = action.id in _pending  # its caller may have given up already
            if not action.reply:  # told, not asked: nothing will come back for it
                _pending.pop(action.id, None)
        if alive and action.expires is not None and time.monotonic() > action.expires:
            continue
        if alive:
            out = {"id": action.id, "function": action.function, "args": action.args,
                   "confirm": action.confirm, "description": action.description, "reply": action.reply}
            if action.approved:
                out["approved"] = action.approved
            return out


def finish(user_id: int, action_id: int, ok: bool, result: object = None, error: str | None = None) -> bool:
    with _lock:
        action = _pending.get(action_id)
    if action is None or action.user_id != user_id:
        return False
    action.result = {"ok": ok, "result": result, "error": error}
    action.done.set()
    return True


def reset() -> None:
    """Forget every queue and connection (tests)."""
    with _lock:
        _queues.clear()
        _pending.clear()
        _seen.clear()
