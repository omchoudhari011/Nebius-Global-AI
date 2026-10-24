"""Keeping replies and memories tied to what actually happened: the hallucination guards.

Models, small local ones especially, say "I've added it to your calendar" when the call was
denied, describe a calendar they never read, or save a "memory" the user never said - which
then comes back as fact in every later chat. A prompt alone doesn't stop that, so the server
checks the work:

- unsupported_words(): a memory the model saves has to be something the user said, in their
  words. Every number and name in it must appear in what the user said (this message, earlier
  turns of this chat, or their earlier messages quoted in the prompt), and at most a quarter
  of its other content words may be new. A rejected memory goes back to the model with the
  reason, so it can save it again in the user's words.
- review(): each final reply is compared with the turn's evidence - which tool calls
  succeeded, which were denied or failed, what was saved to memory, which memories were in
  the prompt. A reply that claims an action that didn't happen, answers as if a blocked tool
  had worked, describes the calendar without reading it, or cites a memory that doesn't exist
  gets one repair round, with tools still offered so a skipped step can still be done. If the
  rewrite fails too, a "Custodian check:" note is added to the reply, so a false claim never
  reaches the user unflagged.

The checks are conservative patterns: they catch the common false claims cheaply and
deterministically, without asking a second model to judge the first, and they would rather
miss an unusual phrasing than flag a true statement.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import memory, sensitive, statements

# Added to every system prompt, after the skill's own text and the safety note.
ACCURACY_NOTE = (
    "Accuracy: state facts about the user only when they appear in what you remember, the quoted earlier "
    "messages, this conversation or this turn's tool results; otherwise say you don't know. Never say you added, "
    "saved, created, scheduled or remembered something unless a tool result in this turn says it succeeded, and if "
    "a tool was denied or failed, say so plainly. Custodian checks every reply against the tool results."
)
OFF_THE_RECORD_NOTE = (
    "This chat is off the record: nothing in it is saved to memory or recalled in later chats, and it is deleted a "
    "day after the last message. Don't offer to remember anything."
)
# Put before a personal question that memory search found no match for, in the user's turn.
# It doesn't claim nothing is known: a memory worded differently can still be in the prompt.
NOTHING_SAVED_NOTE = (
    "(No saved memory or earlier message shares a word with this question. If a memory in your instructions, "
    "this conversation or a tool answers it anyway, use that; otherwise say you don't know rather than guessing.)"
)
REPAIR_HEADER = "(Custodian accuracy check - this is not from the user.) Your reply can't be sent as written:"
REPAIR_FOOTER = (
    "If a step is still needed and you have its tool, call it now. Otherwise rewrite the reply so it says only what "
    "the tool results and memories support, and tell the user plainly what didn't happen. Write to the user directly "
    "and don't mention this check."
)
NOTE_PREFIX = "Custodian check: "


# Memories must be the user's own words ---------------------------------------------------

# Words a memory may use although the user didn't say them: they frame a fact, not state one.
_FRAMING = frozenset(
    """
    name named called prefer prefers preferred preference preferences like likes liked enjoy enjoys enjoyed love loves
    loved dislike dislikes favorite favourite user users person owner usually generally currently also often sometimes
    really mentioned said says told stated
    """.split()
)
_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+)$")


def _content_terms(text: str) -> list[tuple[str, bool]]:
    """A memory's content words as (word, strict). Numbers and names - capitalised words that
    don't start a sentence - are strict: every one must appear in what the user said."""
    terms: dict[str, bool] = {}
    for match in re.finditer(r"\w+", text):
        raw, word = match.group(0), match.group(0).lower()
        if word in memory.STOPWORDS or word in _FRAMING or (len(word) < 2 and not word.isdigit()):
            continue
        strict = any(ch.isdigit() for ch in word) or (raw[0].isupper() and not _SENTENCE_START.search(text[: match.start()]))
        terms[word] = terms.get(word, False) or strict
    return list(terms.items())


def _stem_index(texts: list[str]) -> sqlite3.Connection:
    """An in-memory FTS5 table of `texts`, stemmed like memory search."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE said USING fts5(content, tokenize = 'porter unicode61')")
    conn.executemany("INSERT INTO said (content) VALUES (?)", [(t,) for t in texts if t and t.strip()])
    return conn


def mentioned_in(question: str, texts: list[str]) -> bool:
    """Whether any of `texts` (this chat's earlier turns) shares a content word with `question`."""
    query = memory.fts_query(question)
    if query is None or not texts:
        return False
    conn = _stem_index(texts)
    try:
        return conn.execute("SELECT 1 FROM said WHERE said MATCH ? LIMIT 1", (query,)).fetchone() is not None
    finally:
        conn.close()


def unsupported_words(content: str, sources: list[str]) -> list[str]:
    """Why `content` doesn't count as something the user said - every number or name they
    didn't say, plus the other new words when more than a quarter of them are new - or []
    when it does. Words match by stem with FTS5's porter tokenizer (the same as memory
    search), so "Prefers meetings before noon" is supported by "I prefer meetings before noon"."""
    terms = _content_terms(content)
    if not terms:
        return []
    conn = _stem_index(sources)
    try:
        missing = [
            (word, strict)
            for word, strict in terms
            if not conn.execute("SELECT 1 FROM said WHERE said MATCH ? LIMIT 1", (f'"{word}"',)).fetchone()
        ]
    finally:
        conn.close()
    if any(strict for _, strict in missing) or len(missing) > len(terms) // 4:
        return [word for word, _ in missing]
    return []


# What a turn really did ------------------------------------------------------------------

@dataclass
class Evidence:
    """What happened in one turn, for review()."""

    message: str | None  # None for a scheduled task
    private: bool = False
    calendar_question: bool = False  # the turn asks what's on the calendar (or is the daily summary)
    memory_ids: set[int] = field(default_factory=set)  # in the prompt or saved this turn
    outcomes: list[Any] = field(default_factory=list)  # sandbox.ToolCallOutcome for every gated call
    saved_memories: int = 0
    rejected_memories: list[str] = field(default_factory=list)

    def attempts(self, tool: str, function: str) -> list[Any]:
        return [o for o in self.outcomes if o.tool == tool and o.function == function]

    def succeeded(self, tool: str, function: str) -> bool:
        return any(o.status == "succeeded" for o in self.attempts(tool, function))


@dataclass(frozen=True)
class Finding:
    kind: str
    repair: str  # told to the model in the repair round
    note: str  # told to the user if the rewrite still has the problem


# Reading claims out of a reply -------------------------------------------------------------

_SENTENCES = re.compile(r"[^.!?\n]+[.!?]*")
_CLAUSE_BREAK = re.compile(
    r"[;:]|\s[-–—]\s|,\s*(?=(?:but|though|although|however|so|and|yet)\b)|\s+(?=(?:but|although|though|however)\b)", re.I
)
# Before a verb, these turn a statement into an offer, a condition or a denial.
_HEDGE = re.compile(
    r"\b(?:not|no|never|nothing|none|neither|nor|couldn'?t|can'?t|cannot|can|could|would|should|shall|may|might|must"
    r"|unable|wasn'?t|weren'?t|didn'?t|doesn'?t|don'?t|won'?t|isn'?t|aren'?t|hasn'?t|haven'?t|failed|denied|blocked"
    r"|without|want|wants|wanted|if|once|unless|whether|ready to|able to|try|tried|trying)\b|n't\b",
    re.I,
)
# A description of the calendar that is only a guess or a condition isn't a claim to have read it.
_UNCERTAIN = re.compile(
    r"\b(?:if|unless|whether|might|may|could|would|should|can'?t|cannot|couldn'?t|unable|not sure|don'?t know"
    r"|haven'?t|didn'?t|probably|perhaps|maybe)\b",
    re.I,
)
_OTHER_SUBJECT = re.compile(r"\b(?:you|you've|you'd|you're|they|they've|he|she|someone|anyone)\b", re.I)
# An action claim: the verb opens the clause ("Added “Lunch”…"), or follows "I"/"has been"/... closely.
_CUE_BEFORE = re.compile(
    r"(?:^[\s\W]*(?:(?:done|ok|okay|great|sure|alright|all set|got it|perfect)\b[\s\W]*)?"
    r"|\b(?:i|i've|i have|we|we've|has been|have been|has now been|was|were|is now|are now|now|successfully|already"
    r"|just|also)\b[\s,]*(?:[\w'’]+[\s,]+){0,3})$",
    re.I,
)
_PROMISE = r"\b(?:i'll|i will|i'm going to|i am going to|let me|i'm|i am|going to)\s+(?:now\s+|just\s+|also\s+|quickly\s+|go ahead and\s+)?"

_EVENT_NOUNS = re.compile(
    r"\b(?:calendar|events?|meetings?|appointments?|invite|invitation|reservation|booking|lunch|dinner|breakfast"
    r"|calls?|stand-?up|interview|session|class|reminders?)\b",
    re.I,
)
_NOTE_NOUNS = re.compile(r"\b(?:notes?|notebook|memo)\b", re.I)
_TIME = re.compile(
    r"\b(?:[01]?\d|2[0-3])[:.][0-5]\d\b|\b(?:1[0-2]|0?[1-9])\s?(?:am|pm|a\.m\.|p\.m\.)(?!\w)|\bnoon\b|\bmidnight\b", re.I
)
_WHEN = re.compile(
    r"\b(?:today|tomorrow|tonight|(?:mon|tues|wednes|thurs|fri|satur|sun)day)\b|\b(?:mon|tue|wed|thu|fri|sat|sun)\s+\d{1,2}\b"
    r"|\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\b(?:jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}\b",
    re.I,
)
# A calendar claim needs a calendar word or a time or day ("Added “Team sync” on Sat 3 Oct, 15:00").
_EVENT_CONTEXT = re.compile(f"{_EVENT_NOUNS.pattern}|{_TIME.pattern}|{_WHEN.pattern}", re.I)


@dataclass(frozen=True)
class _Action:
    kind: str
    tool: str
    function: str
    nouns: re.Pattern
    done: str  # past forms
    promised: str  # base and -ing forms
    didnt: str  # what the user is told didn't happen
    also: tuple[tuple[str, str], ...] = ()  # other tool functions that do the same thing (Google Calendar)


_ACTIONS = (
    _Action(
        "unverified_event", "calendar", "create_event", _EVENT_CONTEXT,
        r"added|scheduled|created|booked|put|set|blocked|placed|entered|rescheduled|moved|arranged|reserved",
        r"add|schedule|create|book|put|set up|block|reschedule|move|adding|scheduling|creating|booking|putting",
        "no event was added to your calendar",
        also=(("gcal", "create_event"),),
    ),
    _Action(
        "unverified_note", "notes", "create_note", _NOTE_NOUNS,
        r"(?:jotted|noted|wrote|written)\s+(?:it\s+|that\s+|this\s+)?down"
        r"|saved|created|added|wrote|written|jotted|noted|made|taken|took|stored|recorded|kept|logged|filed",
        r"save|create|add|write|jot|note|make|take|store|saving|creating|adding|writing|jotting|noting",
        "no note was saved",
    ),
)

_MEMORY_CLAIM = re.compile(
    r"\b(?:i'?ll|i will|i'm going to)\s+(?:always\s+)?(?:remember|keep (?:that|this|it) in mind|make a note of)"
    r"|\bi(?:'ve| have)\s+(?:now\s+)?(?:saved|stored|noted|remembered|memori[sz]ed)\b(?![^.!?]{0,40}\b(?:calendar|notes?|events?)\b)"
    r"|\b(?:saved|stored|added|committed)\s+(?:that|this|it)\s+(?:to|in)\s+(?:my |your )?(?:long[- ]term )?memory"
    r"|\b(?:it's|that's|this is)\s+(?:now\s+)?(?:saved|stored|remembered|in my memory)"
    r"|\bgot it\b[^.!?]{0,30}\bremember"
    r"|^\s*noted\b",
    re.I | re.M,
)
# A reminder is true only when the reminders tool set one in this turn.
_REMINDER_CLAIM = re.compile(
    r"\b(?:i'?ll|i will|i'm going to|i am going to)\s+(?:also\s+)?(?:remind|notify|alert|ping)\s+you\b"
    r"|\b(?:reminder|alarm|alert|notification)\s+(?:is|has been|was)\s+(?:now\s+|all\s+)?set\b"
    r"|\bi(?:'ve| have)\s+(?:also\s+)?set\s+(?:up\s+)?(?:a|an|the|your)?\s*(?:reminder|alarm|alert|notification)s?\b",
    re.I,
)
# Custodian can notify you (reminders) but can't send messages, emails or calls.
# Something done on the PC: true only when a pc tool call succeeded this turn.
_PC_CLAIM = re.compile(
    r"\b(?:i(?:'ve| have)?|done[\s,!-]*i(?:'ve)?)\s+(?:just\s+|now\s+)?(?:opened|launched|started|closed|locked|muted|unmuted|paused"
    r"|resumed|played|skipped|typed|pressed|clicked|turned (?:up|down)"
    r"|set the volume|raised the volume|lowered the volume|taken a screenshot|took a screenshot"
    r"|(?:deleted|moved|renamed|copied|created) (?:the |your |that |a |this )?(?:file|folder))\b"
    r"|\b(?:volume|the music|playback|your (?:pc|computer|screen)|spotify|chrome|notepad)\s+(?:is|has been)\s+(?:now\s+)?"
    r"(?:set|muted|unmuted|paused|playing|locked|open|opened|closed)\b"
    # The short form a reply starts with: "Opened YouTube.", "Playing “Kalyani” on YouTube."
    r"|(?:^\W*|[.!?]\s+)(?:done[\s,!-]+)?(?:opened|launched|closed|quit|locked|muted|unmuted|paused|resumed|skipped|typed"
    r"|pressed|clicked|dimmed|playing\s+\S.*\bon (?:youtube|spotify)|set the (?:volume|brightness)|switched windows"
    r"|turned (?:the volume|the brightness|it) (?:up|down)|sorted \d+ files|created the \S.* folder|started an? \S.*timer"
    r"|timer set|wrote (?:the |a |your )?\S.* in (?:notepad|word)|filled|moved (?:it|the file)|copied (?:it|the file))\b",
    re.I | re.M,
)
# An email sent or drafted: true only when Gmail sent or saved it in this turn.
_EMAIL_SENT_CLAIM = re.compile(
    r"\b(?:i(?:'ve| have)?\s+(?:just\s+|now\s+)?)(?:sent|emailed|mailed|replied to)\b(?!\s+you\b)"
    r"|\b(?:e-?mail|reply|response)\s+(?:is|has been|was)\s+(?:now\s+)?sent\b"
    r"|(?:^\W*|[.!?]\s+)(?:done[\s,!-]+)?sent (?:the |your |an? )?(?:e-?mail|reply|response)\b",
    re.I | re.M,
)
_DRAFT_CLAIM = re.compile(
    r"\b(?:saved|left|put|created)\s+(?:it\s+|a\s+|the\s+|your\s+)?(?:\w+\s+)?(?:as\s+a\s+)?drafts?\b"
    r"|\bdraft\s+(?:is|has been|was)\s+(?:now\s+)?(?:saved|ready|in)\b|\bin (?:your )?drafts\b",
    re.I,
)
_CONTACT_CLAIM = re.compile(
    r"\b(?:i'?ll|i will|i'm going to|i am going to)\s+(?:also\s+)?(?:message|text|email|e-mail|call|phone|whatsapp)\s+you\b",
    re.I,
)
# Tools that read something, and what to call it when a read failed.
_READS = (
    ("calendar", "list_events", "calendar"), ("gcal", "list_events", "Google Calendar"),
    ("notes", "read_notes", "notes"), ("reminders", "list_reminders", "reminders"),
    ("documents", "search_documents", "files"), ("documents", "read_document", "files"),
    ("gmail", "search_messages", "email"), ("gmail", "read_message", "email"),
    ("gdrive", "search_files", "Google Drive"), ("gdrive", "read_file", "Google Drive"),
)
_ACKNOWLEDGED = re.compile(
    r"\b(?:couldn'?t|could not|can'?t|cannot|unable|not able|wasn'?t able|no access|don'?t have (?:access|permission)"
    r"|do not have (?:access|permission)|denied|blocked|switched off|turned off|switch(?:ed)? (?:it )?on|turn (?:it )?on"
    r"|enable|permission|tools (?:&|and) data|isn'?t (?:on|enabled|allowed)|not (?:enabled|allowed|switched on)|failed"
    r"|error|didn'?t work|not available|unavailable|timed out"
    r"|(?:haven'?t|have not|didn'?t|did not|not yet)\s+(?:checked|looked|read|seen))\b",
    re.I,
)
_EMPTY_CALENDAR = re.compile(
    r"\b(?:nothing (?:on|scheduled|planned|booked)|no (?:events|meetings|appointments|plans)"
    r"|(?:calendar|schedule|day) (?:is|looks) (?:clear|empty|free|open)|you'?re (?:free|not busy)|you have nothing)\b",
    re.I,
)
_MEMORY_REF = re.compile(r"\bmemory\s*#\s*(\d+)", re.I)

_ABOUT_USER = re.compile(r"\b(?:i|i'm|im|i've|i'd|me|my|mine|myself)\b", re.I)
_ACTION_REQUEST = re.compile(
    r"\b(?:add|schedule|book|create|put|save|take|write|jot|remember|remind|set up|cancel|delete|move|reschedule|make)\b", re.I
)
_SCHEDULE_WORDS = re.compile(
    r"\b(?:agenda|schedule|busy|free|what'?s on|anything on|do i have|have i got|summary|summari[sz]e|recap|brief(?:ing)?"
    r"|rundown|my day)\b",
    re.I,
)


def asks_about_user(message: str) -> bool:
    """A question about the user themselves ("what's my sister's name?"), not a request to do something."""
    return statements.looks_like_question(message) and bool(_ABOUT_USER.search(message)) and not _ACTION_REQUEST.search(message)


def asks_about_calendar(message: str | None, skill_name: str | None) -> bool:
    """The turn is about what's on the calendar: a calendar question, or the daily summary."""
    if skill_name not in ("calendar", "daily-summary"):
        return False
    if message is None or statements.looks_like_question(message):
        return True
    return bool(_SCHEDULE_WORDS.search(message)) and not _ACTION_REQUEST.search(message)


def _statements(reply: str) -> list[str]:
    """The reply's clauses, leaving out questions."""
    clauses: list[str] = []
    for sentence in _SENTENCES.findall(reply):
        if sentence.rstrip().endswith("?"):
            continue
        clauses.extend(part for part in _CLAUSE_BREAK.split(sentence) if part and part.strip())
    return clauses


_REMINDER_WORDS = re.compile(r"\b(?:reminders?|remind(?:ing|ed)?|alarms?)\b", re.I)


def _claims(clause: str, action: _Action) -> bool:
    if action.kind == "unverified_event" and _REMINDER_WORDS.search(clause) and not _EVENT_NOUNS.search(_REMINDER_WORDS.sub("", clause)):
        return False  # "I've set a reminder for 6pm" is a reminder claim (checked on its own), not an event
    for match in re.finditer(rf"\b(?:{action.done})\b", clause, re.I):
        if not (action.nouns.search(clause) or match.group(0).lower().endswith("down")):
            continue
        before = clause[: match.start()]
        if _HEDGE.search(before) or _OTHER_SUBJECT.search(before) or re.match(r"\s*(?:not|nothing|no)\b", clause[match.end():], re.I):
            continue
        if _CUE_BEFORE.search(before):
            return True
    promise = re.search(_PROMISE + rf"(?:{action.promised})\b", clause, re.I)
    return bool(promise and action.nouns.search(clause) and not _HEDGE.search(clause[: promise.start()]))


def _says(reply: str, pattern: re.Pattern) -> bool:
    """A statement (not a question, offer or denial) in `reply` matches `pattern`."""
    for sentence in _SENTENCES.findall(reply):
        if sentence.rstrip().endswith("?"):
            continue
        match = pattern.search(sentence)
        if match and not _HEDGE.search(sentence[: match.start()]):
            return True
    return False


def _states_calendar(reply: str) -> bool:
    """Says what is (or isn't) on the calendar: an event with a time, or "nothing scheduled"."""
    for sentence in _SENTENCES.findall(reply):
        if sentence.rstrip().endswith("?") or _UNCERTAIN.search(sentence):
            continue
        if _EMPTY_CALENDAR.search(sentence) or (_TIME.search(sentence) and _EVENT_NOUNS.search(sentence)):
            return True
    return False


def _why_not(evidence: Evidence, tool: str, function: str) -> str:
    """Why a tool call didn't succeed, in a few words for the user."""
    attempts = evidence.attempts(tool, function)
    if not attempts:
        return f"the {tool} tool was never actually called"
    last = attempts[-1]
    if last.status == "denied":
        switch = re.search(r"“([^”]+)”", last.error or "")
        return f"{switch.group(1)} is switched off in Tools & Data" if switch else (last.error or "it was blocked").rstrip(".")
    if last.status == "rejected":
        return "the request to the tool was invalid"
    if last.status == "timed_out":
        return "the tool took too long and was stopped"
    return f"the {tool} tool failed" + (f" ({last.error[:120]})" if last.error else "")


# "Source: https://..." - a citation must be something a tool returned (or the user gave).
_SOURCE_LINE = re.compile(r"(?im)^\W*(?:sources?|references?|via)\s*:?\s*(.+)$")
_URL = re.compile(r"https?://[^\s)\]>,;\"'`]+")


def drop_invented_sources(reply: str, evidence: Evidence) -> str:
    """`reply` without the cited links that no tool returned this turn (and the user didn't
    give): a source line keeps its real links and goes if none are left."""
    seen = (evidence.message or "") + json.dumps([getattr(o, "result", None) for o in evidence.outcomes],
                                                 ensure_ascii=False, default=str)

    def keep(match: re.Match) -> str:
        line = match.group(0)
        urls = _URL.findall(match.group(1))
        invented = [u for u in urls if u.rstrip(".") not in seen]
        if not invented:
            return line
        if len(invented) == len(urls):
            return ""
        for url in invented:
            line = re.sub(r"[,\s]*" + re.escape(url), "", line, count=1)
        return line

    cleaned = _SOURCE_LINE.sub(keep, reply)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip() if cleaned != reply else reply


def keep_real_sources(arguments: Any, evidence: Evidence) -> Any:
    """A document's arguments (pc_write_document) without the sources whose links no tool returned
    this turn (and the user didn't give) - the same rule as a reply's "Source:" lines."""
    try:
        args = arguments if isinstance(arguments, dict) else json.loads(arguments or "{}")
    except (TypeError, ValueError):
        return arguments
    if not isinstance(args, dict) or not isinstance(args.get("sources"), list):
        return arguments
    seen = (evidence.message or "") + json.dumps([getattr(o, "result", None) for o in evidence.outcomes],
                                                 ensure_ascii=False, default=str)
    real = [s for s in args["sources"] if isinstance(s, dict) and str(s.get("url", "")).rstrip(".") in seen]
    return {**args, "sources": real} if len(real) != len(args["sources"]) else arguments


def review(reply: str, evidence: Evidence) -> list[Finding]:
    """The claims in `reply` that this turn's evidence doesn't support (empty = none found)."""
    findings: list[Finding] = []
    clauses = _statements(reply)

    reminder_set = evidence.succeeded("reminders", "create_reminder")
    for action in _ACTIONS:
        ways = ((action.tool, action.function), *action.also)
        # "I've set a reminder to call mum at 6pm" is about the reminder that was set, not an event.
        considered = [c for c in clauses if not (reminder_set and action.kind == "unverified_event" and _REMINDER_WORDS.search(c))]
        if not any(evidence.succeeded(*w) for w in ways) and any(_claims(c, action) for c in considered):
            tried = next((w for w in reversed(ways) if evidence.attempts(*w)), ways[0])
            why = _why_not(evidence, *tried)
            findings.append(
                Finding(
                    action.kind,
                    f"You said this was done, but no {tried[0]} {tried[1]} call succeeded in this turn ({why}).",
                    f"{action.didnt[0].upper()}{action.didnt[1:]}: {why}.",
                )
            )

    alarm_set = evidence.succeeded("pc", "alarm") or evidence.succeeded("pc", "timer")  # the PC rings: also a reminder
    if not (evidence.succeeded("reminders", "create_reminder") or alarm_set) and _says(reply, _REMINDER_CLAIM):
        why = _why_not(evidence, "reminders", "create_reminder")
        findings.append(
            Finding(
                "unverified_reminder",
                f"You promised a reminder, but no reminders create_reminder call succeeded in this turn ({why}). "
                "Call it if you can, or say plainly that no reminder was set.",
                f"No reminder was set: {why}.",
            )
        )
    pc_done = any(o.tool == "pc" and o.status == "succeeded" for o in evidence.outcomes)
    if not pc_done and _says(reply, _PC_CLAIM):
        tried = [o for o in evidence.outcomes if o.tool == "pc"]
        why = _why_not(evidence, "pc", tried[-1].function) if tried else "no PC action was actually carried out"
        findings.append(
            Finding(
                "unverified_pc_action",
                f"You said you did something on their PC, but no pc action succeeded in this turn ({why}). Call the "
                "pc tool if you can, or say plainly what didn't happen.",
                f"Nothing was done on your PC: {why}.",
            )
        )
    message_sent = evidence.succeeded("gmail", "send_email") or evidence.succeeded("pc", "send_message")
    if not message_sent and _says(reply, _EMAIL_SENT_CLAIM):
        tried = [o for o in evidence.outcomes if (o.tool, o.function) in (("gmail", "send_email"), ("pc", "send_message"))]
        why = _why_not(evidence, tried[-1].tool, tried[-1].function) if tried else "nothing was sent"
        findings.append(
            Finding(
                "unverified_send",
                f"You said it was sent, but no gmail send_email (or pc send_message) call succeeded in this turn ({why}). "
                "Say plainly that it wasn't sent, or save it as a draft.",
                f"Nothing was sent: {why}.",
            )
        )
    if not evidence.succeeded("gmail", "draft_email") and _says(reply, _DRAFT_CLAIM):
        why = _why_not(evidence, "gmail", "draft_email")
        findings.append(
            Finding(
                "unverified_draft",
                f"You said a draft was saved, but no gmail draft_email call succeeded in this turn ({why}).",
                f"No draft was saved: {why}.",
            )
        )
    if _says(reply, _CONTACT_CLAIM):
        findings.append(
            Finding(
                "unsupported_capability",
                "You promised to message, email or call them, but Custodian can't send messages, emails or calls. "
                "Offer a reminder instead.",
                "Custodian can't send messages, emails or calls; it can set a reminder instead.",
            )
        )

    if evidence.saved_memories == 0 and _says(reply, _MEMORY_CLAIM):
        if evidence.private:
            why = "this chat is off the record, so nothing from it is saved to memory"
        elif evidence.message and sensitive.looks_secret(evidence.message):
            why = "Custodian never keeps passwords, PINs, card, account or ID numbers in memory"
        elif evidence.rejected_memories:
            why = evidence.rejected_memories[-1]
        else:
            why = ""
        if why:
            findings.append(Finding("unverified_memory", f"You said you'd remember this, but nothing was saved: {why}.", f"Nothing was saved to memory: {why}."))

    for tool, function, what in _READS:
        if evidence.attempts(tool, function) and not evidence.succeeded(tool, function) and not _ACKNOWLEDGED.search(reply):
            why = _why_not(evidence, tool, function)
            findings.append(
                Finding(
                    "unchecked_read",
                    f"Your {tool} {function} call didn't succeed ({why}), yet the reply doesn't say so. Don't describe "
                    f"their {what} as if you had read it: say plainly that you couldn't check it, and why.",
                    f"Custodian couldn't read your {what} for this answer ({why}), so it wasn't checked against it.",
                )
            )

    if (
        evidence.calendar_question
        and not evidence.attempts("calendar", "list_events")
        and not evidence.attempts("gcal", "list_events")
        and not any(f.kind == "unverified_event" for f in findings)
        and not _ACKNOWLEDGED.search(reply)
        and _states_calendar(reply)
    ):
        findings.append(
            Finding(
                "unread_calendar",
                "You described their calendar without reading it in this turn. Call calendar_list_events first, or say "
                "you haven't checked it.",
                "Your calendar wasn't actually checked for this answer, so treat any events above as unverified.",
            )
        )

    unknown = sorted({int(n) for n in _MEMORY_REF.findall(reply)} - evidence.memory_ids)
    if unknown:
        listed = ", ".join(f"#{n}" for n in unknown)
        findings.append(
            Finding(
                "unknown_memory",
                f"You cited memory {listed}, which doesn't exist. Cite only memories listed in your instructions.",
                f"There is no memory {listed}; that reference was made up.",
            )
        )
    return findings


def repair_request(findings: list[Finding]) -> str:
    """The message that asks the model to fix its draft (sent as a user turn, never saved)."""
    return "\n".join([REPAIR_HEADER, *(f"- {f.repair}" for f in findings), REPAIR_FOOTER])


def annotate(reply: str, findings: list[Finding]) -> str:
    """The reply with a "Custodian check:" note for every problem the rewrite didn't fix."""
    notes = "\n".join(NOTE_PREFIX + f.note for f in findings)
    return f"{reply.rstrip()}\n\n{notes}" if reply.strip() else notes
