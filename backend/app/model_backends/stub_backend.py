"""Deterministic, offline backend (MODEL_BACKEND=stub).

It speaks the same protocol as the real model - it proposes tool calls and then writes a
reply from their results - using simple keyword rules, so the whole loop (skills, the
permission gate, the sandbox, memory) runs and can be tested with no API key or network.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from itertools import count

from .base import ModelBackend, ModelResponse, ToolCallRequest

_ids = count(1)

_MEMORY_LINE = re.compile(r"^- \[memory #(\d+) · (\w+)\] (.+)$", re.MULTILINE)
# Messages recalled from earlier chats arrive quoted before the user's message, which follows
# NOW_LABEL (both mirror app/orchestrator.py; tests/test_lifetime_memory.py checks they match).
_EARLIER_LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2}): “(.+)”$", re.MULTILINE)
NOW_LABEL = "My message now:"
_NOW = re.compile(r"Current time: \w+ (\d{4}-\d{2}-\d{2})")
_PREFERENCE = re.compile(r"\b(prefer|prefers|like|likes|love|loves|hate|hates|want|wants|always|never|rather)\b", re.I)

_REMEMBER = re.compile(r"^(?:please\s+)?remember(?:\s+that)?[\s:,]+(.+)$", re.I | re.S)
_NOTE_CREATE = re.compile(
    r"^(?:please\s+)?(?:can you\s+)?(?:take|write|make|save|add|create|jot down)\s+(?:a\s+|this\s+)?note"
    r"(?:\s+(?:that|saying|about))?\s*:?\s*(.+)$",
    re.I | re.S,
)
_NOTE_PREFIX = re.compile(r"^note\s*:\s*(.+)$", re.I | re.S)
_EVENT_VERB = re.compile(r"\b(add|schedule|book|create|put|set up|plan)\b", re.I)
_EVENT_NOUN = re.compile(
    r"\b(meeting|event|appointment|call|lunch|dinner|breakfast|coffee|calendar|session|review|standup|interview|class)\b",
    re.I,
)
_EVENT_LEAD = re.compile(r"^(?:please\s+)?(?:can you\s+)?(?:add|schedule|book|create|put|set up|plan)\s+(?:a\s+|an\s+|the\s+|my\s+)?", re.I)
_EVENT_TAIL = re.compile(
    r"\s+(?:at|on|tomorrow|today|tonight|for|from|next|this|to my calendar|on my calendar|in my calendar)\b.*$",
    re.I | re.S,
)
_TIME = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_NOTES_READ = re.compile(r"\b(find|search|show|read|list|what|which|any|look|check)\b", re.I)
_CALENDAR = re.compile(
    r"\b(calendar|agenda|schedule|events?|meetings?|appointments?|busy|free)\b|what do i have|what'?s on|what is on",
    re.I,
)
_QUESTION_WORDS = ("what", "when", "where", "who", "which", "how", "do ", "does ", "did ", "is ", "are ", "am ", "can ")
_STOP = {
    "the", "and", "for", "you", "your", "what", "when", "where", "who", "which", "how", "does", "did", "are", "can",
    "have", "has", "had", "this", "that", "with", "about", "like", "know", "tell", "remember", "there", "their", "they",
}
_FILLER = {"my","the", "a", "an", "any", "all", "notes", "note", "about", "on", "for", "please"}


def _call(name: str, args: dict) -> ToolCallRequest:
    return ToolCallRequest(id=f"stub_{next(_ids)}", name=name, arguments=args)


def _today(system: str) -> date:
    m = _NOW.search(system)
    return date.fromisoformat(m.group(1)) if m else date.today()


def _when(text: str, today: date) -> str | None:
    """'tomorrow at 3pm' -> '2026-10-03T15:00' (no time found -> None)."""
    low = text.lower()
    day = today
    iso = _ISO_DATE.search(low)
    if iso:
        day = date.fromisoformat(iso.group(1))
    elif "tomorrow" in low:
        day = today + timedelta(days=1)
    else:
        for i, name in enumerate(_WEEKDAYS):
            if re.search(rf"\b{name}\b", low):
                ahead = (i - today.weekday()) % 7 or 7
                day = today + timedelta(days=ahead)
                break
    if "noon" in low:
        hour, minute = 12, 0
    else:
        m = _TIME.search(low)
        if not m:
            return None
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        meridiem = (m.group(3) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        elif not meridiem and 1 <= hour <= 6:
            hour += 12  # "at 3" almost always means the afternoon
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{day.isoformat()}T{hour:02d}:{minute:02d}"


def _sentence(text: str) -> str:
    text = text.strip().rstrip(".!")
    return text[:1].upper() + text[1:] if text else text


class StubBackend(ModelBackend):
    name = "stub"
    model_id = "stub-offline"

    def generate(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse:
        available = {t["function"]["name"] for t in tools or []}
        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=None)
        turn = messages[last_user + 1:] if last_user is not None else []
        results = [m for m in turn if m["role"] == "tool"]
        if results:
            return ModelResponse(self._summarize(results, system), [], self.model_id)
        raw = (messages[last_user]["content"] or "") if last_user is not None else ""
        earlier, _, text = raw.rpartition(NOW_LABEL + " ")  # ("", "", raw) when nothing was recalled
        calls, reply = self._plan(text, system, available, earlier)
        return ModelResponse(reply, calls, self.model_id)

    # Deciding what to do ---------------------------------------------------------------

    def _plan(self, text: str, system: str, available: set[str], earlier: str = "") -> tuple[list[ToolCallRequest], str]:
        stripped = text.strip()
        low = stripped.lower()
        today = _today(system)

        if low.startswith("scheduled task") or re.search(r"\b(summary|summari[sz]e|recap|briefing|rundown)\b", low):
            calls = []
            if "calendar_list_events" in available:
                calls.append(_call("calendar_list_events", {}))
            if "notes_read_notes" in available:
                calls.append(_call("notes_read_notes", {"query": ""}))
            return calls, "" if calls else "There's nothing I'm allowed to check for this task."

        m = _REMEMBER.match(stripped)
        if m and "remember" in available:
            fact = _sentence(m.group(1))
            kind = "preference" if _PREFERENCE.search(fact) else "semantic"
            return [_call("remember", {"content": fact, "type": kind})], ""

        m = _NOTE_CREATE.match(stripped) or _NOTE_PREFIX.match(stripped)
        if m and "notes_create_note" in available:
            body = m.group(1).strip()
            words = re.findall(r"[\w'’-]+", body)[:6]
            title = _sentence(" ".join(words)) or "Note"
            return [_call("notes_create_note", {"title": title, "body": body})], ""

        if _EVENT_VERB.search(low) and _EVENT_NOUN.search(low) and "calendar_create_event" in available:
            title = _EVENT_TAIL.sub("", _EVENT_LEAD.sub("", stripped)).strip(" .,!?")
            title = _sentence(title) or "Event"
            start = _when(stripped, today)
            if start is None:
                return [], f"What time should I put “{title}” on your calendar?"
            return [_call("calendar_create_event", {"title": title, "start": start})], ""

        answer = self._from_memory(low, system, earlier)
        if answer:
            return [], answer

        if re.search(r"\bnotes?\b", low) and _NOTES_READ.search(low) and "notes_read_notes" in available:
            m = re.search(r"\b(?:about|on|for|mentioning|with|regarding)\s+(.+?)[?.!]*$", stripped, re.I)
            words = [w for w in re.findall(r"[\w'’-]+", m.group(1)) if w.lower() not in _FILLER] if m else []
            return [_call("notes_read_notes", {"query": " ".join(words)})], ""

        if _CALENDAR.search(low) and "calendar_list_events" in available:
            args: dict = {}
            if "tomorrow" in low:
                args["start_date"] = (today + timedelta(days=1)).isoformat()
            if re.search(r"\bweek\b", low):
                args["days"] = 7
            return [_call("calendar_list_events", args)], ""

        return [], self._chat(low, system)

    @staticmethod
    def _from_memory(low: str, system: str, earlier: str = "") -> str | None:
        """Answer a question from a memory, or failing that something said in an earlier chat,
        that shares at least two content words with it."""
        if "?" not in low and not low.startswith(_QUESTION_WORDS):
            return None
        asked = {w for w in re.findall(r"\w+", low) if w not in _STOP and len(w) > 2}
        candidates = [(text, f"From what you've told me: {text}") for _, _, text in _MEMORY_LINE.findall(system)]
        candidates += [
            (text, f"On {day} you told me: “{text}”") for day, text in reversed(_EARLIER_LINE.findall(earlier))
        ]
        best, best_overlap = None, 1
        for text, answer in candidates:
            words = {w for w in re.findall(r"\w+", text.lower()) if w not in _STOP and len(w) > 2}
            overlap = len(asked & words)
            if overlap > best_overlap:
                best, best_overlap = answer, overlap
        return best

    def _chat(self, low: str, system: str) -> str:
        memories =[text for _, _, text in _MEMORY_LINE.findall(system)]
        if low in ("hi", "hello", "hey", "hi!", "hello!", "hey!"):
            return "Hi! I'm Custodian. I can check your calendar, add events, take and search notes, and remember things you tell me."
        if memories:
            lines = "\n".join(f"• {m}" for m in memories[:3])
            return f"Here's what I remember that might help:\n{lines}"
        return (
            "I'm running in offline mode (MODEL_BACKEND=stub), so I handle a few things: checking or adding "
            "calendar events, taking and searching notes, and remembering what you tell me (“Remember that …”). "
            "Connect a Nebius API key for full answers."
        )

    # Writing the reply from tool results --------------------------------------------------

    def _summarize(self, results: list[dict], system: str) -> str:
        today = _today(system)
        parts: list[str] = []
        for message in results:
            try:
                data = json.loads(message["content"])
            except (TypeError, json.JSONDecodeError):
                continue
            fn, status, result = data.get("function"), data.get("status"), data.get("result") or {}
            if status not in ("succeeded", "saved"):
                action = {
                    "list_events": "check your calendar",
                    "create_event": "add that to your calendar",
                    "create_note": "save that note",
                    "read_notes": "search your notes",
                    "remember": "remember that",
                }.get(fn or "", "do that")
                parts.append(f"I couldn't {action}. {data.get('error') or status}")
            elif fn == "remember":
                parts.append("Got it — I'll remember that.")
            elif fn == "list_events":
                parts.append(self._events(result, today))
            elif fn == "create_event":
                event = result.get("created", {})
                parts.append(f"Added “{event.get('title')}” on {_pretty(event.get('start', ''))}.")
            elif fn == "create_note":
                parts.append(f"Saved a note: “{result.get('created', {}).get('title')}”.")
            elif fn == "read_notes":
                notes = result.get("notes", [])
                query = result.get("query") or ""
                if not notes:
                    parts.append(f"I didn't find any notes{f' matching “{query}”' if query else ''}.")
                else:
                    lines = "\n".join(f"• {n['title']} — {n['body'][:80]}" for n in notes[:5])
                    parts.append(f"From your notes:\n{lines}")
        return "\n\n".join(parts) or "Done."

    @staticmethod
    def _events(result: dict, today: date) -> str:
        events = result.get("events", [])
        first = result.get("from", "")
        span = "today" if first == today.isoformat() and result.get("to") == first else (
            f"{first}" if result.get("to") == first else f"{first} to {result.get('to')}"
        )
        if not events:
            return f"Nothing on your calendar for {span}."
        lines = []
        for e in events:
            where = f" ({e['location']})" if e.get("location") else ""
            lines.append(f"• {_pretty(e['start'], with_day=result.get('to') != first)}–{e['end'][11:16]} {e['title']}{where}")
        return f"Your calendar for {span}:\n" + "\n".join(lines)


def _pretty(iso_value: str, *, with_day: bool = True) -> str:
    """'2026-10-03T15:00+05:30' -> 'Sat 3 Oct, 15:00'."""
    try:
        d = date.fromisoformat(iso_value[:10])
    except ValueError:
        return iso_value
    clock = iso_value[11:16]
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}, {clock}" if with_day else clock
