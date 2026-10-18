"""Allowing a PC action from where you asked for it - the phone app, the web app, Telegram -
instead of only in the dialog on the PC.

When a turn didn't come from the desktop app itself and "remote_approvals" is on (the default;
Tools & Data), a PC action that needs your OK (closing an app, deleting a file, sending a
WhatsApp message) waits here for you to allow or deny it in the app - or on Telegram, with the
buttons on the bot's message - for up to two minutes. Allowed, it goes to the PC without a second
dialog, and the PC shows that it was allowed remotely; denied or unanswered, it never reaches the
PC. The PC still checks what it can on its own side: a WhatsApp message goes only to a chat whose
name matches.

Turn the setting off to keep every such approval on the PC: then a stolen phone session alone
can't approve anything.
"""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field

WAIT_SECONDS = 120


@dataclass
class Approval:
    id: int
    user_id: int
    title: str
    details: str
    origin: str  # "app" or "telegram": where it was asked for
    created: float = field(default_factory=time.monotonic)
    allowed: bool | None = None
    done: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        return {"id": self.id, "title": self.title, "details": self.details, "origin": self.origin,
                "seconds_left": max(0, int(WAIT_SECONDS - (time.monotonic() - self.created)))}


_lock = threading.Lock()
_ids = itertools.count(1)
_pending: dict[int, Approval] = {}
_listeners: list = []  # callables(approval): Telegram sends its buttons


def on_request(callback) -> None:  # noqa: ANN001
    _listeners.append(callback)


def request(user_id: int, title: str, details: str, origin: str = "app") -> Approval:
    approval = Approval(next(_ids), user_id, title, details, origin)
    with _lock:
        _pending[approval.id] = approval
    for callback in list(_listeners):
        try:
            callback(approval)
        except Exception:  # noqa: BLE001 - the app can still answer it
            pass
    return approval


def wait(approval: Approval, timeout: float = WAIT_SECONDS) -> bool:
    """True only when it was allowed in time."""
    approval.done.wait(timeout)
    with _lock:
        _pending.pop(approval.id, None)
    return approval.allowed is True


def decide(user_id: int, approval_id: int, allow: bool) -> bool:
    with _lock:
        approval = _pending.get(approval_id)
        if approval is None or approval.user_id != user_id or approval.done.is_set():
            return False
        approval.allowed = bool(allow)
    approval.done.set()
    return True


def pending(user_id: int) -> list[dict]:
    with _lock:
        waiting = [a for a in _pending.values() if a.user_id == user_id and not a.done.is_set()]
    return [a.public() for a in sorted(waiting, key=lambda a: a.id)]


def reset() -> None:
    with _lock:
        _pending.clear()


# What it's asking, in plain words (the PC's own dialog says the same, see desktop actions.describe).

_SHOWN = {
    "type_text": lambda a: f"Type this into the window in front:\n\n{a['text'][:500]}",
    "press_keys": lambda a: f"Press {a['keys']}",
    "click": lambda a: f"Click at x={a['x']}, y={a['y']}",
    "write_file": lambda a: f"{'Overwrite' if a.get('overwrite') else 'Create'} the file\n{a['path']}\n\n{a['content'][:400]}",
    "move_file": lambda a: f"Move\n{a['source']}\nto\n{a['destination']}",
    "delete_file": lambda a: f"Move to the Recycle Bin:\n{a['path']}",
    "close_window": lambda a: f"Close the window “{a['title']}” (unsaved work may be lost)",
    "close_app": lambda a: f"Quit {a['name']} and close all its windows (it may ask to save)",
    "read_file": lambda a: f"Read the file\n{a['path']}\nand send its text to Custodian",
    "get_clipboard": lambda a: "Send the text on your clipboard to Custodian",
    "screenshot": lambda a: "Take a screenshot and send it to Custodian to describe",
    "organize_folder": lambda a: f"Sort the loose files in\n{a['path']}\ninto subfolders by type",
    "write_in_app": lambda a: f"Open {a.get('app', 'notepad')} and write:\n\n{a['text'][:500]}",
    "click_element": lambda a: f"Click “{a['name']}”" + (f" in “{a['window']}”" if a.get("window") else ""),
    "fill_field": lambda a: f"Type into “{a['name']}”:\n\n{a['text'][:300]}",
    "send_message": lambda a: f"Send a WhatsApp message\nTo: {a['contact']}\n\n{a['text'][:1500]}",
    "confirm_plan": lambda a: "\n".join(f"• {s}" for s in a["steps"][:12]),
}


def describe(function: str, args: dict) -> str:
    try:
        return _SHOWN[function](args)
    except (KeyError, TypeError):
        return f"{function.replace('_', ' ')}: {args}"
