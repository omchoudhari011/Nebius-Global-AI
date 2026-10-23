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
URL = r"(?:https?://)?(?:localhost|[\w-]+(?:\.[\w-]+)+)(?::\d{2,5})?(?:/\S*)?"
SEARCHABLE = {"google": "google", "youtube": "youtube", "amazon": "amazon", "flipkart": "flipkart", "wikipedia": "wikipedia",
              "github": "github", "maps": "maps", "google maps": "maps", "reddit": "reddit", "news": "news",
              "google news": "news", "images": "images", "google images": "images", "spotify": "spotify", "gmail": "gmail",
              "x": "x", "twitter": "x"}
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
    reply: Callable[[dict], str]  # from the last succeeded result
    then: Callable[[dict], tuple[str, dict] | None] | None = None  # a second step from the first result
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


# Everyday commands in Hindi and Marathi - in Devanagari as Whisper writes them, and in the Latin
# letters people type ("chrome kholo") - turned into the English command they mean, so they get
# the same instant handling. Anything else in those languages goes to the model as it is.
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_NAMES_IN_DEVANAGARI = {
    "क्रोम": "chrome", "गूगल क्रोम": "chrome", "यूट्यूब": "youtube", "यूट्युब": "youtube", "नोटपैड": "notepad",
    "नोटपॅड": "notepad", "स्पॉटिफाई": "spotify", "स्पोटिफाई": "spotify", "व्हाट्सएप": "whatsapp", "व्हॉट्सॲप": "whatsapp",
    "वॉट्सऐप": "whatsapp", "कैलकुलेटर": "calculator", "कॅल्क्युलेटर": "calculator", "गूगल": "google", "जीमेल": "gmail",
    "वीएस कोड": "vs code", "एक्सेल": "excel", "वर्ड": "word", "एज": "edge", "कैमरा": "camera", "कॅमेरा": "camera",
    "सेटिंग्स": "settings", "डाउनलोड्स": "downloads", "डाउनलोड": "downloads", "डॉक्युमेंट्स": "documents",
    # "गाना बजाओ" is "play some music": which music is the model's call, as in English
    "गाना": "music", "गाने": "songs", "गाणं": "music", "गाणे": "music", "गाणी": "songs", "म्यूजिक": "music",
    "कुछ गाने": "some music", "कोई गाना": "a song", "gaana": "music", "gaane": "songs", "music": "music",
}
_TRANSLATIONS: tuple[tuple[str, str], ...] = (
    # media
    (r"(?:अगला|अगले|पुढचं|पुढचे|पुढील)\s+(?:गाना|गाने|गाणं|गाणे)(?:\s+(?:बजाओ|लगाओ|चलाओ|लाव|वाजव))?|agla\s+gaana|next\s+gaana", "next song"),
    (r"(?:पिछला|पिछले|मागचं|मागचे|मागील)\s+(?:गाना|गाने|गाणं|गाणे)(?:\s+(?:बजाओ|लगाओ|चलाओ|लाव|वाजव))?|pichla\s+gaana", "previous song"),
    (r"(?:गाना|गाणं|गाणे|म्यूजिक|music|gaana)?\s*(?:रोको|रोक\s+दो|रुको|थांबव|थांबवा|ruko|roko|rok\s+do)", "pause"),
    (r"(?:आवाज़|आवाज|वॉल्यूम|awaaz|awaz|volume)\s+(?:बढ़ाओ|बढाओ|तेज़\s+करो|तेज\s+करो|वाढव|वाढवा|badhao|badha\s+do|tez\s+karo)", "volume up"),
    (r"(?:आवाज़|आवाज|वॉल्यूम|awaaz|awaz|volume)\s+(?:कम\s+करो|कम\s+कर\s+दो|धीमी\s+करो|कमी\s+कर|कमी\s+करा|kam\s+karo|kam\s+kar\s+do|dheemi\s+karo)", "volume down"),
    (r"(?:आवाज़|आवाज|वॉल्यूम|awaaz|awaz|volume)\s+(\d{1,3})\s*(?:%|प्रतिशत|टक्के)?\s*(?:करो|कर\s+दो|कर|करा|karo|kar\s+do)", r"volume \1"),
    (r"(?:आवाज़|आवाज)\s+बंद\s+(?:करो|कर|करा)|म्यूट\s+(?:करो|कर|करा)|mute\s+karo|awaaz\s+band\s+karo", "mute"),
    (r"(.+?)\s+(?:बजाओ|बजा\s+दो|चलाओ|चला\s+दो|लगाओ|लगा\s+दो|वाजव|वाजवा|लाव|लावा|bajao|baja\s+do|chalao|chala\s+do|lagao|laga\s+do)", r"play \1"),
    # the PC
    (r"(?:कंप्यूटर|कम्प्यूटर|पीसी|लैपटॉप|लॅपटॉप|संगणक|स्क्रीन|pc|computer|laptop|screen)\s+(?:लॉक|lock)\s+(?:करो|कर\s+दो|कर|करा|karo|kar\s+do)", "lock my pc"),
    (r"(\d{1,4})\s+(?:मिनट|मिनिट|मिनिटांचा|minute|minat)\s+(?:का|चा|ka)?\s*(?:टाइमर|टायमर|timer)\s*(?:लगाओ|लगा\s+दो|लाव|लावा|सेट\s+करो|lagao|laga\s+do|set\s+karo)?", r"\1 minute timer"),
    (r"(?:डार्क|dark)\s+(?:मोड|mode)(?:\s+(?:करो|कर|चालू\s+कर|on\s+karo|karo))?", "dark mode"),
    (r"(?:लाइट|लाईट|light)\s+(?:मोड|mode)(?:\s+(?:करो|कर|चालू\s+कर|on\s+karo|karo))?", "light mode"),
    (r"(?:बैटरी|बॅटरी|battery)\s+(?:कितनी\s+है|किती\s+आहे|kitni\s+hai|kitna\s+hai)", "battery"),
    (r"(.+?)\s+(?:बंद\s+करो|बंद\s+कर\s+दो|बंद\s+कर|बंद\s+करा|band\s+karo|band\s+kar\s+do)", r"close \1"),
    (r"(.+?)\s+(?:खोलो|खोल\s+दो|खोलिए|खोलना|उघड|उघडा|kholo|khol\s+do|kholiye)", r"open \1"),
)


def translate(text: str) -> str:
    """'क्रोम खोलो' -> 'open chrome', 'volume badhao' -> 'volume up'. Text in other words is
    returned as it was."""
    text = text.translate(_DEVANAGARI_DIGITS)
    for pattern, english in _TRANSLATIONS:
        found = re.fullmatch(pattern, text)
        if found:
            text = found.expand(english).strip()
            break
    else:
        return text
    words = text.split(" ", 1)
    if len(words) == 2:  # the name in the command: "open क्रोम" -> "open chrome"
        name = words[1].strip()
        text = f"{words[0]} {_NAMES_IN_DEVANAGARI.get(name, name)}"
    return text


def normalise(message: str) -> str:
    """Lower case, no closing punctuation or commas, and no "hey jarvis", "please", "can you"."""
    text = re.sub(r"[.!?।]+$", "", message.strip().lower()).replace("’", "'")
    text = " ".join(text.replace(",", " ").split())
    for _ in range(3):
        text = _FILLER.sub("", text).strip(" ,")
    return text


def _alarm_reply(r: dict) -> str:
    return f"Alarm set for {r.get('rings_at', 'then')}" + (f" ({r['label']})." if r.get("label") not in (None, "Alarm") else ".")


def match(message: str | None) -> Intent | None:
    """The intent for an exact, unambiguous command, or None (let the model handle it)."""
    if not message or not ENABLED:
        return None
    return parse(message)


def parse(message: str, *, routine: bool = False) -> Intent | None:
    """match() without the on/off switch: also how a routine's steps ("open vs code", "volume 30")
    become tool calls (app/routines.py), where `routine` also allows a bare "say ..."."""
    text = translate(normalise(message))
    compound = re.search(r"\s(?:and|then|after\s+that|also)\s", text) is not None
    two_step = re.fullmatch(r".+\s+and\s+open\s+the\s+(?:first|top).*|(?:open|go\s+to)\s+\S+(?:\s+\S+)?\s+and\s+(?:search|play)\s.+", text)
    if compound and not two_step:
        return None  # several things at once: the model plans them
    if re.fullmatch(r"undo\s+(?:what\s+you\s+(?:just\s+)?did|the\s+last\s+(?:thing|action|change|step)|your\s+last\s+action)"
                    r"|put\s+(?:it|that|everything)\s+back(?:\s+(?:how|the\s+way)\s+it\s+was)?|revert\s+(?:that|it|the\s+last\s+change)"
                    r"|take\s+that\s+back|reverse\s+(?:that|it)", text):
        return Intent("undo_last_action", {}, lambda r: f"Done - I {r.get('undone', 'undid that')}.")
    m = re.fullmatch(r"(?:turn\s+(on|off)\s+|switch\s+(on|off)\s+|enable\s+|disable\s+)?(?:do\s+not\s+disturb|dnd|focus\s+assist)"
                     r"(?:\s+mode)?(?:\s+(on|off))?|(silence|mute)\s+(?:my\s+|all\s+)?notifications|(unmute|allow)\s+(?:my\s+)?notifications", text)
    if m:
        on = not (m.group(1) == "off" or m.group(2) == "off" or m.group(3) == "off" or m.group(5)
                  or re.match(r"disable\s", text))
        return Intent("pc_do_not_disturb", {"on": on},
                      lambda r: "Do Not Disturb is on - notifications are silenced." if r.get("do_not_disturb")
                      else "Do Not Disturb is off.")
    m = re.fullmatch(r"(?:set\s+)?(?:an?\s+|my\s+)?alarm\s+(?:for|at)\s+(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m|p\.m)?(?:\s+tomorrow)?"
                     r"|wake\s+me\s+(?:up\s+)?at\s+(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m|p\.m)?(?:\s+tomorrow)?", text)
    if m:
        hour, minute, half = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(4), m.group(5), m.group(6))
        hour, minute = int(hour), int(minute or 0)
        if half and half.startswith("p") and hour < 12:
            hour += 12
        elif half and half.startswith("a") and hour == 12:
            hour = 0
        if hour <= 23 and minute <= 59:
            return Intent("pc_alarm", {"time": f"{hour:02d}:{minute:02d}", "label": "Alarm"}, _alarm_reply)
    m = re.fullmatch(r"(?:say|announce|speak)\s+(.+?)\s+(?:out\s+loud|aloud|on\s+(?:my\s+|the\s+)?(?:pc|computer|laptop|speakers?))"
                     + (r"|(?:say|announce|speak)\s+(.+)" if routine else ""), text)
    if m and len(m.group(1) or m.group(2) or "") <= 300:
        said = m.group(1) or m.group(2)
        return Intent("pc_say", {"text": said}, lambda r, s=said: f"Said “{s}” on your PC.")
    m = re.fullmatch(r"(?:close|quit|exit|kill|shut\s+down|shut)\s+(?:the\s+|my\s+)?(.+?)(?:\s+app|\s+application|\s+window)?", text)
    if m and m.group(1) in APP_NAMES - {"settings", "explorer", "file explorer"}:
        app = m.group(1)

        def closed(r: dict, a=app) -> str:
            if r.get("still_open"):
                return f"I asked {_open_label(a)} to close, but it's still open - it may be asking you to save something."
            return f"Closed {_open_label(a)}."
        return Intent("pc_close_app", {"name": app}, closed)
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
    if re.fullmatch(r"(?:dim\s+(?:the\s+|my\s+)?screen|make\s+(?:the\s+|my\s+)?screen\s+(?:dimmer|darker)|(?:lower|reduce|decrease)\s+(?:the\s+)?brightness)", text):
        return Intent("pc_set_brightness", {"level": 30}, lambda r: "Dimmed the screen to 30%.")
    if re.fullmatch(r"(?:make\s+(?:the\s+|my\s+)?screen\s+brighter|brighten\s+(?:the\s+|my\s+)?screen|(?:raise|increase)\s+(?:the\s+)?brightness)", text):
        return Intent("pc_set_brightness", {"level": 90}, lambda r: "Turned the brightness up to 90%.")
    keys = {r"undo(?:\s+that|\s+it)?": ("ctrl+z", "Undone."), r"redo(?:\s+that|\s+it)?": ("ctrl+y", "Redone."),
            r"select\s+all": ("ctrl+a", "Selected everything."), r"copy\s+(?:that|it|this)": ("ctrl+c", "Copied."),
            r"paste(?:\s+(?:it|that|this))?": ("ctrl+v", "Pasted."),
            r"save(?:\s+(?:this|it|the\s+file|my\s+work))?(?:\s+with\s+ctrl\s*\+?\s*s)?": ("ctrl+s", "Saved."),
            r"show\s+(?:me\s+)?(?:the\s+)?desktop|minimi[sz]e\s+everything": ("win+d", "Showing the desktop."),
            r"switch\s+windows|alt\s*\+?\s*tab": ("alt+tab", "Switched windows."),
            r"press\s+enter|hit\s+enter": ("enter", "Pressed Enter."), r"press\s+(?:escape|esc)": ("esc", "Pressed Escape."),
            r"take\s+a\s+screenshot\s+with\s+(?:the\s+)?keyboard": ("win+shift+s", "Opened the screenshot tool.")}
    for pattern, (combo, said) in keys.items():
        if re.fullmatch(pattern, text):
            return Intent("pc_press_keys", {"keys": combo}, lambda r, w=said: w)
    m = re.fullmatch(r"(?:press|hit)\s+((?:ctrl|control|alt|shift|win|windows)(?:\s*\+\s*|\s+)[a-z0-9]+(?:(?:\s*\+\s*|\s+)[a-z0-9]+)?)", text)
    if m:
        combo = re.sub(r"\s*\+\s*|\s+", "+", m.group(1)).replace("control", "ctrl").replace("windows", "win")
        return Intent("pc_press_keys", {"keys": combo}, lambda r, c=combo: f"Pressed {c.title()}.")
    m = re.fullmatch(r"(?:switch\s+to|go\s+to\s+the)\s+(?:the\s+)?(.+?)(?:\s+window)?|bring\s+(?:the\s+)?(.+?)(?:\s+window)?\s+to\s+the\s+front", text)
    if m and (m.group(1) or m.group(2)) and (m.group(1) or m.group(2)) in APP_NAMES:
        title = m.group(1) or m.group(2)
        return Intent("pc_focus_window", {"title": title}, lambda r, t=title: f"Switched to {_open_label(t)}.")
    if re.fullmatch(r"lock(?:\s+(?:my|the))?\s+(?:pc|computer|laptop|screen|system)|lock\s+it", text):
        return Intent("pc_lock_screen", {}, lambda r: "Locked.")
    m = (re.fullmatch(r"(?:(?:set|start)\s+)?(?:a\s+)?(\d{1,4})[\s-]?(seconds?|secs?|minutes?|mins?|hours?|hrs?)\s+timer", text)
         or re.fullmatch(r"(?:(?:set|start)\s+)?(?:a\s+)?timer\s+for\s+(\d{1,4})\s+(seconds?|secs?|minutes?|mins?|hours?|hrs?)", text)
         or re.fullmatch(r"(?:remind\s+me|ring|beep)\s+in\s+(\d{1,4})\s+(seconds?|secs?|minutes?|mins?|hours?|hrs?)\s+with\s+a\s+timer", text))
    if m:
        seconds = _seconds(m.group(1), m.group(2))
        if 1 <= seconds <= 86_400:
            return Intent("pc_timer", {"seconds": seconds, "label": "Timer"},
                          lambda r, s=seconds: f"Timer set for {_human(s)} - it rings at {r.get('rings_at', 'the time')}.")
    if re.fullmatch(r"(?:what'?s|how\s+much\s+is|how\s+much)?\s*(?:my\s+)?battery(?:\s+(?:level|percentage|left|status))?|how\s+much\s+battery(?:\s+is\s+left|\s+do\s+i\s+have(?:\s+left)?)?", text):
        return Intent("pc_system_info", {}, _battery_reply)
    if re.fullmatch(r"(?:(?:give\s+me\s+a\s+|run\s+(?:a\s+)?)?(?:system|systems|pc|computer|laptop)\s+(?:status|report|check|diagnostics?)(?:\s+report)?"
                    r"|status\s+report|(?:run\s+)?(?:a\s+)?diagnostics?|how'?s\s+(?:my|the)\s+(?:pc|computer|laptop|system)(?:\s+doing)?"
                    r"|how\s+is\s+(?:my|the)\s+(?:pc|computer|laptop|system)(?:\s+doing)?|(?:are\s+)?all\s+systems\s+(?:ok|okay|normal|go))", text):
        from .persona import status_reply

        return Intent("pc_system_info", {}, status_reply)
    if re.fullmatch(r"what'?s\s+playing|what\s+song\s+is\s+(?:this|playing)|which\s+song\s+is\s+(?:this|playing)", text):
        return Intent("pc_now_playing", {}, lambda r: (f"You're listening to {r['now_playing'][0]['title']} on {r['now_playing'][0]['app']}."
                                                       if r.get("now_playing") else "Nothing is playing right now."))
    browser = r"(?:\s+(?:in|on)\s+(?:chrome|the\s+browser|a\s+new\s+tab|edge))?"
    m = re.fullmatch(rf"(?:open|go\s+to|load|visit|take\s+me\s+to|bring\s+up|pull\s+up)\s+({URL}){browser}", text)
    if m and not re.search(r"\.(?:pdf|txt|docx?|xlsx?|pptx?|png|jpe?g|gif|mp[34]|wav|zip|exe|msi|py|md|csv|json|html?)$", m.group(1)):
        return Intent("pc_open", {"target": m.group(1)}, lambda r, u=m.group(1): f"Opened {u}.")
    m = re.fullmatch(rf"(?:open|go\s+to|load|visit|take\s+me\s+to|pull\s+up)\s+(?:the\s+)?(.+?)(?:\s+website|\s+site)?{browser}", text)
    if m and m.group(1) in SITE_NAMES:
        return Intent("pc_open", {"target": m.group(1)}, lambda r, t=m.group(1): f"Opened {_open_label(t)}.")
    m = re.fullmatch(r"(?:(?:search|google|look\s+up)\s+(?:for\s+)?)(.+?)\s+and\s+open\s+the\s+(?:first|top)(?:\s+(?:result|link|one|hit))?", text)
    if m:
        return Intent("web_search", {"query": m.group(1)},
                      lambda r: f"Opened the top result: {r.get('opened')}.",
                      then=lambda r: (("pc_open", {"target": r["results"][0]["url"]}) if r.get("results") else None))
    m = (re.fullmatch(r"(?:search|find|look\s+for|look\s+up|show\s+me)\s+(.+?)\s+on\s+(.+)", text)
         or re.fullmatch(r"(?:open|go\s+to)\s+(.+?)\s+and\s+search(?:\s+for)?\s+(.+)", text)
         or re.fullmatch(r"search\s+(.+?)\s+for\s+(.+)", text))
    if m:
        first, second = m.group(1), m.group(2)
        site, query = (second, first) if second in SEARCHABLE else (first, second) if first in SEARCHABLE else (None, None)
        if site and query and site != "youtube" or (site == "youtube" and query):
            return Intent("pc_search_site", {"site": SEARCHABLE[site], "query": query},
                          lambda r, q=query, w=site: f"Here are the {_open_label(w)} results for “{q}”.")
    m = re.fullmatch(r"(?:youtube|(?:open|go\s+to)\s+youtube\s+and\s+play)\s+(.+)", text)
    if m:
        return Intent("pc_play", {"query": m.group(1)}, _play_reply)
    if re.fullmatch(r"close\s+(?:this|the|current|that)?\s*(?:chrome\s+|browser\s+)?tab", text):
        return Intent("pc_press_keys", {"keys": "ctrl+w"}, lambda r: "Closed the tab.")
    if re.fullmatch(r"(?:open\s+)?(?:a\s+)?new\s+tab", text):
        return Intent("pc_press_keys", {"keys": "ctrl+t"}, lambda r: "Opened a new tab.")
    if re.fullmatch(r"reopen\s+(?:the\s+)?(?:last\s+)?closed\s+tab", text):
        return Intent("pc_press_keys", {"keys": "ctrl+shift+t"}, lambda r: "Reopened the last closed tab.")
    if re.fullmatch(r"(?:refresh|reload)(?:\s+(?:the|this))?(?:\s+page)?", text):
        return Intent("pc_press_keys", {"keys": "f5"}, lambda r: "Refreshed the page.")
    m = re.fullmatch(r"(?:play|put\s+on|start\s+playing)\s+(.+?)(?:\s+(?:on|in|from)\s+(youtube|spotify))?", text)
    if m and m.group(1) not in ("music", "the music", "it", "something", "a song", "some music", "songs", "again", "that"):
        args = {"query": m.group(1)} | ({"service": "spotify"} if m.group(2) == "spotify" else {})
        return Intent("pc_play", args, _play_reply)
    m = re.fullmatch(r"(?:open|show\s+me|go\s+to)\s+(?:my\s+|the\s+)?(?:pc'?s\s+|pcs\s+|computer'?s\s+|laptop'?s\s+)?"
                     r"(downloads?|documents|desktop|pictures|photos|music|videos)(?:\s+folder)?", text)
    if m:  # the folder, not an app called "Downloads" (the desktop app knows where each one is)
        folder = {"download": "downloads", "photos": "pictures"}.get(m.group(1), m.group(1))
        return Intent("pc_open", {"target": folder}, lambda r, f=folder: f"Opened your {f.capitalize()} folder.")
    m = re.fullmatch(r"(?:open|launch|start|fire\s+up|bring\s+up)\s+(?:the\s+|my\s+)?(.+?)(?:\s+app)?", text)
    if m:
        target = m.group(1)
        if target in APP_NAMES or target in SITE_NAMES:
            return Intent("pc_open", {"target": target}, lambda r, t=target: f"Opened {_open_label(t)}.")
    return None


class FastPath(ModelBackend):
    """Plays the model for one turn: the matched call, then the reply from its result. Hands
    over to the real model when the action didn't succeed (it explains better)."""

    name = "fastpath"
    model_id = "custodian-fastpath"

    def __init__(self, intent: Intent, model: ModelBackend, *, address: str | None = None) -> None:
        self.intent, self.model = intent, model
        self.address = address  # spoken to Jarvis: "Opened Chrome, sir." (app/persona.py)

    def _reply(self, result: dict) -> str:
        text = self.intent.reply(result)
        if self.address:
            from .persona import dress

            text = dress(text, self.address)
        return text

    def generate(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse:
        import json

        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        results = [json.loads(m["content"]) for m in messages[last_user + 1:] if m["role"] == "tool"]
        if not results:
            return ModelResponse("", [ToolCallRequest("fast_1", self.intent.function, self.intent.args)], self.model_id)
        result = results[-1]
        if result.get("status") == "succeeded":
            try:
                if len(results) == 1 and self.intent.then is not None:
                    step = self.intent.then(result.get("result") or {})
                    if step is not None:
                        return ModelResponse("", [ToolCallRequest("fast_2", *step)], self.model_id)
                if len(results) <= 2:
                    return ModelResponse(self._reply(result.get("result") or {}), [], self.model_id)
            except (KeyError, TypeError, IndexError):
                pass
        return self.model.generate(messages, tools)  # failed, declined, denied: let the model explain

    def close(self) -> None:
        pass
