"""Your other settings - the ones that aren't permissions or privacy - as one JSON object per
user (user_settings.prefs). Each key has a default and a check; a missing key is its default.

    get(user_id)                  every setting, defaults filled in
    update(user_id, **changes)    validated; unknown keys and bad values raise SettingError
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from . import db

_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
VOICE_LANGUAGES = ("auto", "en", "hi", "mr")


class SettingError(ValueError):
    """A setting can't take that value; the message is safe to show."""


def _bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise SettingError("must be true or false")
    return value


def _time(value: Any) -> str:
    if not isinstance(value, str) or not _TIME.match(value):
        raise SettingError("must be a time like 07:30")
    return value


def _minutes(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 120:
        raise SettingError("must be a whole number of minutes from 0 to 120")
    return value


def _folders(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 12 or not all(isinstance(v, str) and 1 <= len(v) <= 300 for v in value):
        raise SettingError("must be a list of 1 to 12 folders")
    if any("\n" in v or "\x00" in v for v in value):
        raise SettingError("folder names must be single lines")
    return [v.strip() for v in value]


def _language(value: Any) -> str:
    if value not in VOICE_LANGUAGES:
        raise SettingError(f"must be one of {', '.join(VOICE_LANGUAGES)}")
    return value


# key -> (default, check)
SETTINGS: dict[str, tuple[Any, Callable[[Any], Any]]] = {
    # Proactive: spoken on the PC (the desktop app) and shown in the inbox.
    "announce": (True, _bool),                 # speak reminders, meeting heads-ups and watcher alerts on the PC
    "quiet_from": ("23:00", _time),            # ...but not between these times
    "quiet_until": ("07:00", _time),
    "briefing": (True, _bool),                 # a spoken summary of your day when you first use the PC in the morning
    "meeting_heads_up": (10, _minutes),        # minutes before a calendar event; 0 = off
    # Search inside your PC's files by meaning: off until you switch it on.
    "pc_index": (False, _bool),
    "pc_index_folders": (["~/Documents", "~/Downloads", "~/Desktop"], _folders),
    # Learning from the replies you corrected, overnight while the PC is idle.
    "nightly_learning": (True, _bool),
    # PC actions asked for from your phone or Telegram can be allowed there, not only on the PC.
    "remote_approvals": (True, _bool),
    # Reminders, meeting heads-ups, watcher alerts and finished tasks also as Telegram messages, once linked.
    "telegram_alerts": (True, _bool),
    # The language you speak to the desktop app in ("auto" lets Whisper tell).
    "voice_language": ("auto", _language),
}


def defaults() -> dict:
    return {key: (list(default) if isinstance(default, list) else default) for key, (default, _) in SETTINGS.items()}


def get(user_id: int, *, conn=None) -> dict:  # noqa: ANN001 - sqlite3.Connection
    with db.use(conn) as c:
        row = c.execute("SELECT prefs FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
    stored = json.loads(row["prefs"]) if row and row["prefs"] else {}
    out = defaults()
    out.update({k: v for k, v in stored.items() if k in SETTINGS})
    return out


def value(user_id: int, key: str) -> Any:
    return get(user_id)[key]


def update(user_id: int, **changes: Any) -> dict:
    """Change some settings; every other one keeps its value. Returns all of them."""
    checked = {}
    for key, raw in changes.items():
        if key not in SETTINGS:
            raise SettingError(f"there's no setting called {key!r}")
        try:
            checked[key] = SETTINGS[key][1](raw)
        except SettingError as exc:
            raise SettingError(f"{key} {exc}") from None
    with db.connect() as conn:
        row = conn.execute("SELECT prefs FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
        stored = json.loads(row["prefs"]) if row and row["prefs"] else {}
        stored.update(checked)
        conn.execute(
            """
            INSERT INTO user_settings (user_id, prefs) VALUES (?, ?)
            ON CONFLICT (user_id) DO UPDATE SET prefs = excluded.prefs, updated_at = datetime('now')
            """,
            (user_id, json.dumps(stored)),
        )
    return get(user_id)


def internal(user_id: int, key: str) -> Any:
    """Something Custodian keeps for itself next to your settings (when it last briefed you)."""
    with db.connect() as conn:
        row = conn.execute("SELECT prefs FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
    return (json.loads(row["prefs"]) if row and row["prefs"] else {}).get(f"_{key}")


def set_internal(user_id: int, key: str, value: Any) -> None:
    with db.connect() as conn:
        row = conn.execute("SELECT prefs FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
        stored = json.loads(row["prefs"]) if row and row["prefs"] else {}
        stored[f"_{key}"] = value
        conn.execute(
            """
            INSERT INTO user_settings (user_id, prefs) VALUES (?, ?)
            ON CONFLICT (user_id) DO UPDATE SET prefs = excluded.prefs, updated_at = datetime('now')
            """,
            (user_id, json.dumps(stored)),
        )


def in_quiet_hours(prefs: dict, now_hhmm: str) -> bool:
    """Whether `now_hhmm` ('22:15') falls in the quiet hours, which may run past midnight."""
    start, end = prefs["quiet_from"], prefs["quiet_until"]
    if start == end:
        return False
    return start <= now_hhmm < end if start < end else now_hhmm >= start or now_hhmm < end
