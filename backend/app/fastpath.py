"""Instant, exact handling of the commands people say most: "next track", "pause", "volume 40",
"put on Kalyani", "lock my pc", "dark mode", "10 minute timer", "open notepad", "battery?".

A request that matches one of these patterns *exactly* skips the model: FastPath plays the
model's part for that turn - it proposes the one tool call, then words the reply from what
the desktop app reported. Everything else stays as it is: the call goes through the same
permission gate and audit log, the reply through the same accuracy checks. Anything not an
exact match - or an action that didn't succeed, which needs explaining - goes to the model.

~0.2 s instead of ~1.6 s, and the right action every time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .model_backends.base import ModelBackend, ModelResponse, ToolCallRequest

_FILLER = re.compile(r"^(?:hey jarvis|jarvis|hey custodian|ok|okay|please|can you|could you|would you|will you|"
                     r"just|now)\s+|\s+(?:please|now|for me|thanks|thank you)$")

APP_NAMES = {"notepad", "calculator", "calc", "chrome", "google chrome", "edge", "firefox", "spotify", "vs code", "vscode",
             "visual studio code", "word", "excel", "powerpoint", "outlook", "paint", "file explorer", "explorer",
             "task manager", "terminal", "settings", "camera", "clock", "store", "whatsapp", "teams", "discord", "zoom"}
SITE_NAMES = {"youtube", "gmail", "google", "wikipedia", "instagram", "facebook", "twitter", "x", "linkedin", "netflix",
              "amazon", "flipkart", "github", "chatgpt", "google maps", "maps", "google drive", "drive", "reddit",
              "hotstar", "prime video", "youtube music", "whatsapp web"}
PRETTY = {"vs code": "VS Code", "vscode": "VS Code", "youtube": "YouTube", "gmail": "Gmail", "github": "GitHub",
          "linkedin": "LinkedIn", "chatgpt": "ChatGPT", "whatsapp": "WhatsApp", "whatsapp web": "WhatsApp Web",
          "calc": "the Calculator", "calculator": "the Calculator", "x": "X"}


@dataclass
class Intent:
    function: str  # the exposed tool name, e.g. "pc_media"
    args: dict
    reply: Callable[[dict], str]  # from the succeeded result
    label: str = ""
    extra: dict = field(default_factory=dict)


def _now_playing(result: dict) -> str:
    playing = result.get("now_playing")
    return f" - now playing {playing[0]['title']}" if playing else ""


def _seconds(amount: str, unit: str) -> int:
    n = int(amount)
    return n * {"s": 1, "m": 60, "h": 3600}[unit[0]]


def _human(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600} hour{'s' if seconds != 3600 else ''}"
    if seconds % 60 == 0:
        return f"{seconds // 60} minute{'s' if seconds != 60 else ''}"
    return f"{seconds} seconds"


def _media(action: str, said: str, steps: int = 1) -> Intent:
    replies = {"next": "Skipped to the next track", "previous": "Back to the previous track", "pause": "Paused",
               "resume": "Resumed", "mute": "Muted", "unmute": "Unmuted", "volume_up": "Turned the volume up",
               "volume_down": "Turned the volume down"}
    return Intent("pc_media", {"action": action, "steps": steps}, lambda r, k=said: f"{replies[k]}{_now_playing(r)}.")


def _play_reply(r: dict) -> str:
    if r.get("service") == "spotify":
        playing = (r.get("now_playing") or [{}])[0].get("title")
        if r.get("started") and playing:
            return f"Playing {playing} on Spotify."
        return f"I opened Spotify's search for “{r.get('searched')}” - pick a result to play."
    if r.get("started"):
        return f"Playing “{r.get('playing')}” on YouTube."
    return "I couldn't find that video, so I opened the YouTube results."


def _battery_reply(r: dict) -> str:
    b = r.get("battery")
    if not b:
        return "This PC doesn't report a battery - it's probably a desktop."
    return f"Your battery is at {b['percent']}%" + (", charging." if b.get("charging") else ", not charging.")


def _open_label(target: str) -> str:
    return PRETTY.get(target, target.title() if target in SITE_NAMES else target.capitalize())


ENABLED = True  # off while generating fine-tuning data: the model must still learn these commands


def match(message: str | None) -> Intent | None:
    """The intent for an exact, unambiguous command, or None (let the model handle it)."""
    if not message or not ENABLED:
        return None
    text = re.sub(r"[.!?]+$", "", message.strip().lower()).replace("’", "'")
    text = " ".join(text.replace(",", " ").split())
    for _ in range(3):
        text = _FILLER.sub("", text).strip(" ,")
    words = r"(?:\s+(?:song|track|one|video))?"
    if re.fullmatch(rf"(?:play\s+)?(?:the\s+)?next{words}|skip(?:\s+this)?{words}", text):
        return _media("next", "next")
    if re.fullmatch(rf"(?:play\s+)?(?:the\s+)?previous{words}|go\s+back\s+a\s+(?:song|track)|last\s+song", text):
        return _media("previous", "previous")
    if re.fullmatch(r"pause(?:\s+(?:the\s+)?(?:music|song|video|it|playback|spotify|youtube))?|stop\s+(?:the\s+)?(?:music|song|video)", text):
        return _media("play_pause", "pause")
    if re.fullmatch(r"(?:resume|unpause|continue)(?:\s+(?:the\s+)?(?:music|song|video|playing|playback|it))?", text):
        return _media("play_pause", "resume")
    if re.fullmatch(r"mute(?:\s+(?:the\s+)?(?:pc|sound|audio|volume|computer|laptop))?", text):
        return _media("mute", "mute")
    if re.fullmatch(r"unmute(?:\s+(?:the\s+)?(?:pc|sound|audio|volume|computer|laptop))?", text):
        return _media("mute", "unmute")
    if re.fullmatch(r"(?:turn\s+(?:the\s+)?volume\s+up|volume\s+up|louder|turn\s+it\s+up|(?:increase|raise)\s+(?:the\s+)?volume)", text):
        return _media("volume_up", "volume_up", 5)
    if re.fullmatch(r"(?:turn\s+(?:the\s+)?volume\s+down|volume\s+down|quieter|turn\s+it\s+down|(?:decrease|lower|reduce)\s+(?:the\s+)?volume)", text):
        return _media("volume_down", "volume_down", 5)
    m = re.fullmatch(r"(?:(?:set|turn|make|change|put)\s+)?(?:the\s+)?volume\s+(?:to\s+|at\s+)?(\d{1,3})\s*(?:%|percent)?", text)
    if m and int(m.group(1)) <= 100:
        return Intent("pc_set_volume", {"level": int(m.group(1))}, lambda r: f"Volume set to {r.get('volume', '?')}%.")
    m = re.fullmatch(r"(?:(?:set|turn|make|change|put)\s+)?(?:the\s+)?(?:screen\s+)?brightness\s+(?:to\s+|at\s+)?(\d{1,3})\s*(?:%|percent)?", text)
    if m and int(m.group(1)) <= 100:
        return Intent("pc_set_brightness", {"level": int(m.group(1))}, lambda r: f"Brightness set to {r.get('brightness')}%.")
    m = re.fullmatch(r"(?:(?:turn\s+on|enable|switch\s+to|go\s+to|use)\s+)?(dark|light)\s+(?:mode|theme)(?:\s+on)?", text)
    if m:
        return Intent("pc_set_theme", {"mode": m.group(1)}, lambda r: f"Switched Windows to {r.get('theme')} mode.")
    if re.fullmatch(r"lock(?:\s+(?:my|the))?\s+(?:pc|computer|laptop|screen|system)|lock\s+it", text):
        return Intent("pc_lock_screen", {}, lambda r: "Locked.")
    m = (re.fullmatch(r"(?:(?:set|start)\s+)?(?:a\s+)?(\d{1,4})[\s-]?(seconds?|secs?|minutes?|mins?|hours?|hrs?)\s+timer", text)
         or re.fullmatch(r"(?:(?:set|start)\s+)?(?:a\s+)?timer\s+for\s+(\d{1,4})\s+(seconds?|secs?|minutes?|mins?|hours?|hrs?)", text))
    if m:
        seconds = _seconds(m.group(1), m.group(2))
        if 1 <= seconds <= 86_400:
            return Intent("pc_timer", {"seconds": seconds, "label": "Timer"},
                          lambda r, s=seconds: f"Timer set for {_human(s)} - it rings at {r.get('rings_at', 'the time')}.")
    if re.fullmatch(r"(?:what'?s|how\s+much\s+is|how\s+much)?\s*(?:my\s+)?battery(?:\s+(?:level|percentage|left|status))?|how\s+much\s+battery(?:\s+is\s+left|\s+do\s+i\s+have(?:\s+left)?)?", text):
        return Intent("pc_system_info", {}, _battery_reply)
    if re.fullmatch(r"what'?s\s+playing|what\s+song\s+is\s+(?:this|playing)|which\s+song\s+is\s+(?:this|playing)", text):
        return Intent("pc_now_playing", {}, lambda r: (f"You're listening to {r['now_playing'][0]['title']} on {r['now_playing'][0]['app']}."
                                                       if r.get("now_playing") else "Nothing is playing right now."))
    m = re.fullmatch(r"(?:play|put\s+on|start\s+playing)\s+(.+?)(?:\s+(?:on|in|from)\s+(youtube|spotify))?", text)
    if m and m.group(1) not in ("music", "the music", "it", "something", "a song", "some music", "songs", "again", "that"):
        args = {"query": m.group(1)} | ({"service": "spotify"} if m.group(2) == "spotify" else {})
        return Intent("pc_play", args, _play_reply)
    m = re.fullmatch(r"(?:open|launch|start|fire\s+up|bring\s+up)\s+(?:the\s+|my\s+)?(.+?)(?:\s+app)?", text)
    if m:
        target = m.group(1)
        if target in APP_NAMES or target in SITE_NAMES or re.fullmatch(r"(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?", target):
            return Intent("pc_open", {"target": target}, lambda r, t=target: f"Opened {_open_label(t)}.")
    return None


class FastPath(ModelBackend):
    """Plays the model for one turn: the matched call, then the reply from its result. Hands
    over to the real model when the action didn't succeed (it explains better)."""

    name = "fastpath"
    model_id = "custodian-fastpath"

    def __init__(self, intent: Intent, model: ModelBackend) -> None:
        self.intent, self.model = intent, model

    def generate(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse:
        import json

        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        results = [json.loads(m["content"]) for m in messages[last_user + 1:] if m["role"] == "tool"]
        if not results:
            return ModelResponse("", [ToolCallRequest("fast_1", self.intent.function, self.intent.args)], self.model_id)
        result = results[-1]
        if result.get("status") == "succeeded" and len(results) == 1:
            try:
                return ModelResponse(self.intent.reply(result.get("result") or {}), [], self.model_id)
            except (KeyError, TypeError, IndexError):
                pass
        return self.model.generate(messages, tools)  # failed, declined, denied: let the model explain

    def close(self) -> None:
        pass
