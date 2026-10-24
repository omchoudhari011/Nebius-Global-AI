"""Jarvis's manner on the PC side: the greeting when it starts, how a conversation ends, the
lines it says itself, and the few commands the PC handles without the server ("show the HUD",
"that's all"). What it says about your day and your PC comes from Custodian; this is only the
voice around it. The address ("sir") comes from Custodian's settings (GET /hud)."""

from __future__ import annotations

import datetime as dt
import random
import re

# A reply made only of these ends a conversation (and isn't sent to Custodian): "that's all",
# "thanks, that's it", "no, that will be all, Jarvis". Whisper also tends to hear "Thank you." in
# near-silence, which ends it too - harmless.
_CLOSING = re.compile(
    r"(?:that'?s\s+(?:all|it|everything)(?:\s+for\s+now)?|that\s+will\s+be\s+all|thank\s*(?:s|you)(?:\s+so\s+much|\s+very\s+much)?"
    r"|nothing(?:\s+else)?|no(?:\s+thanks?)?|nope|never\s*mind|stop(?:\s+listening)?|go\s+to\s+sleep|good\s*bye|bye|cancel"
    r"|we'?re\s+done|i'?m\s+done|bas(?:\s+itna\s+hi)?|dhanyavaad|shukriya|thik\s+hai|बस|धन्यवाद|शुक्रिया|ठीक\s+है)",
    re.IGNORECASE,
)
_SOFTENERS = re.compile(r"\b(?:ok(?:ay)?|alright|all\s+right|right|great|perfect|cool|fine|jarvis|sir|buddy|and|then|so)\b",
                        re.IGNORECASE)
_HUD_SHOW = re.compile(r"^\W*(?:(?:show|open|bring\s+up|display)\s+(?:the\s+|your\s+|my\s+)?(?:hud|heads[\s-]?up\s+display|dashboard|interface)"
                       r"|(?:go\s+)?full[\s-]?screen(?:\s+mode)?|hud(?:\s+on)?)\W*$", re.IGNORECASE)
_HUD_HIDE = re.compile(r"^\W*(?:(?:hide|close|dismiss)\s+(?:the\s+|your\s+|my\s+)?(?:hud|heads[\s-]?up\s+display|dashboard|interface)"
                       r"|exit\s+full[\s-]?screen|hud\s+off)\W*$", re.IGNORECASE)
_FILLER = re.compile(r"^\W*(?:hey\s+)?jarvis\W+", re.IGNORECASE)

SIGN_OFFS = ("Very well, {a}.", "As you wish, {a}.", "I'll be here, {a}.", "Of course, {a}.")
STANDING_BY = ("At your service, {a}.", "Yes, {a}?", "Standing by, {a}.")


def ends_conversation(said: str) -> bool:
    text = _SOFTENERS.sub(" ", said)
    pieces = [p.strip() for p in re.split(r"[,.;!?।]+|\s{2,}", text) if p.strip()]
    return bool(pieces) and all(_CLOSING.fullmatch(" ".join(p.split())) for p in pieces)


def hud_command(said: str) -> str | None:
    """"show"/"hide" for a request about the HUD itself, else None."""
    text = _FILLER.sub("", said.strip())
    if _HUD_SHOW.match(text):
        return "show"
    if _HUD_HIDE.match(text):
        return "hide"
    return None


def sign_off(address: str, rng: random.Random | None = None) -> str:
    return (rng or random).choice(SIGN_OFFS).format(a=address)


def part_of_day(hour: int) -> str:
    return "morning" if 4 <= hour < 12 else "afternoon" if hour < 17 else "evening"


def greeting(address: str, now: dt.datetime | None = None, *, warnings: list[str] | None = None,
             next_event: dict | None = None) -> str:
    """"Good evening, sir. Jarvis is online and all systems are normal." plus what's next today."""
    now = now or dt.datetime.now()
    if now.hour < 4:
        opening = f"Working late, {address}?"
    else:
        opening = f"Good {part_of_day(now.hour)}, {address}."
    state = ("Jarvis is online. " + (f"One thing: {warnings[0]}." if warnings else "All systems are normal."))
    upcoming = ""
    if next_event:
        upcoming = f" Your next appointment, {next_event['title']}, is at {next_event['time']}."
    return f"{opening} {state}{upcoming}"


APP_NAMES = {"chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "brave": "Brave", "code": "VS Code",
             "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint", "whatsapp.root": "WhatsApp",
             "whatsapp": "WhatsApp", "ms-teams": "Teams", "spotify": "Spotify", "discord": "Discord", "steam": "Steam",
             "steamwebhelper": "Steam", "docker desktop": "Docker Desktop", "notepad": "Notepad", "acrobat": "Acrobat", "obs64": "OBS", "vlc": "VLC"}


def app_name(process: str) -> str:
    """'whatsapp.root' -> 'WhatsApp': what you'd call an app, from its process name."""
    stem = process.lower().removesuffix(".exe")
    return APP_NAMES.get(stem, stem[:1].upper() + stem[1:])


def names(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def memory_offer(address: str, percent: float, apps: list[dict]) -> str:
    """'Memory is nearly full, sir: 95% in use. Steam and Discord are holding 1.8 GB and haven't
    been used for a while. Shall I close them?'"""
    held = sum(a["memory_mb"] for a in apps) / 1000
    one = len(apps) == 1
    return (f"Memory is nearly full, {address}: {round(percent)}% in use. {names([app_name(a['app']) for a in apps])} "
            f"{'is' if one else 'are'} holding {held:.1f} GB and {'hasn' if one else 'haven'}'t been used for a while. "
            f"Shall I close {'it' if one else 'them'}?")


def failure(address: str) -> str:
    return f"I'm afraid something went wrong, {address}."


def not_your_voice() -> str:
    return "I'm sorry, I only take instructions from my owner's voice."
