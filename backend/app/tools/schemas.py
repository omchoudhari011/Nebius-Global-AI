"""Argument models for every tool function - the single source of truth for what the
model may pass. The gate validates against these before a call reaches the sandbox;
unknown fields, wrong types, control characters and path-like values are rejected.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LINE_BREAK = re.compile(r"[\r\n  ]")
_TRAVERSAL = re.compile(r"(^|[\\/])\.\.([\\/]|$)")
_ABSOLUTE = re.compile(r"^(?:[\\/]|[a-zA-Z]:[\\/]|~[\\/]|file:)", re.IGNORECASE)


def _no_path(value: str) -> str:
    if _TRAVERSAL.search(value) or _ABSOLUTE.match(value):
        raise ValueError("looks like a file path; '..' segments and absolute paths aren't allowed")
    return value


def _single_line(value: str) -> str:
    if _LINE_BREAK.search(value) or _CONTROL.search(value):
        raise ValueError("must be a single line of plain text")
    return _no_path(value)


def _plain_text(value: str) -> str:
    if _CONTROL.search(value):
        raise ValueError("contains control characters")
    return value


SafeLine = Annotated[str, AfterValidator(_single_line)]
PlainText = Annotated[str, AfterValidator(_plain_text)]


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ListEventsArgs(ToolArgs):
    start_date: date | None = Field(
        default=None, description="First day to list, as YYYY-MM-DD. Leave out for today."
    )
    days: int = Field(default=1, ge=1, le=31, description="Number of days to list, starting at start_date.")


class CreateEventArgs(ToolArgs):
    title: SafeLine = Field(min_length=1, max_length=200, description="Short event title.")
    start: datetime = Field(
        description="Start time in ISO 8601, e.g. 2026-10-05T15:00. Without a UTC offset it is read in the user's time zone."
    )
    duration_minutes: int = Field(default=60, ge=5, le=1440, description="Length of the event in minutes.")
    location: SafeLine | None = Field(default=None, max_length=200, description="Where it happens, if known.")


class CreateNoteArgs(ToolArgs):
    title: SafeLine = Field(min_length=1, max_length=120, description="A short title of a few words.")
    body: PlainText = Field(min_length=1, max_length=10_000, description="The full note text.")


class ReadNotesArgs(ToolArgs):
    query: SafeLine = Field(
        default="", max_length=200, description="Keywords to search for. Empty returns the most recent notes."
    )
    limit: int = Field(default=10, ge=1, le=50, description="Maximum number of notes to return.")


class CreateReminderArgs(ToolArgs):
    text: SafeLine = Field(min_length=1, max_length=200, description="What to remind them about, e.g. 'Call mum'.")
    at: datetime = Field(
        description="When, in ISO 8601, e.g. 2026-10-05T18:00. Without a UTC offset it is read in the user's time zone."
    )
    repeat: Literal["daily", "weekdays", "weekly"] | None = Field(
        default=None, description="Only for a reminder that repeats; leave out for a one-off."
    )


class ListRemindersArgs(ToolArgs):
    include_done: bool = Field(default=False, description="Also list reminders that have already gone off.")
    limit: int = Field(default=20, ge=1, le=50)


class CancelReminderArgs(ToolArgs):
    id: int = Field(ge=1, description="The reminder's id, from list_reminders.")


class SearchDocumentsArgs(ToolArgs):
    query: SafeLine = Field(default="", max_length=200, description="Keywords to search for. Empty lists the newest files.")
    limit: int = Field(default=5, ge=1, le=20)


class ReadDocumentArgs(ToolArgs):
    id: int = Field(ge=1, description="The document's id, from search_documents.")
    part: int = Field(default=1, ge=1, le=500, description="Which part of a long document to read (parts are ~4,000 characters).")


class GoogleListEventsArgs(ListEventsArgs):
    pass


class GoogleCreateEventArgs(CreateEventArgs):
    pass


class SearchMailArgs(ToolArgs):
    query: SafeLine = Field(
        default="", max_length=200,
        description="A Gmail search, e.g. 'from:bank newer_than:7d' or 'flight booking'. Empty lists the newest messages.",
    )
    limit: int = Field(default=5, ge=1, le=20)


class ReadMailArgs(ToolArgs):
    id: SafeLine = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$", description="The message id, from search_messages.")


class SearchDriveArgs(ToolArgs):
    query: SafeLine = Field(default="", max_length=200, description="Words in the file's name or text. Empty lists recent files.")
    limit: int = Field(default=5, ge=1, le=20)


class ReadDriveArgs(ToolArgs):
    id: SafeLine = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$", description="The file id, from search_files.")


# The web ------------------------------------------------------------------------------------

class WebSearchArgs(ToolArgs):
    query: SafeLine = Field(min_length=1, max_length=300, description="What to search the web for.")
    limit: int = Field(default=5, ge=1, le=8)


class ReadPageArgs(ToolArgs):
    url: SafeLine = Field(min_length=8, max_length=2000, pattern=r"^https?://", description="The page's full http(s) address.")


# The PC (run by the Custodian desktop app) ----------------------------------------------------

PcPath = Annotated[str, AfterValidator(_plain_text)]


class NoArgs(ToolArgs):
    pass


class PcListFilesArgs(ToolArgs):
    path: PcPath = Field(default="~", max_length=500, description="Folder, e.g. '~/Downloads' or 'D:/Projects'. '~' is the home folder.")
    pattern: SafeLine = Field(default="*", max_length=100, description="Name filter such as '*.pdf'.")


class PcFindFilesArgs(ToolArgs):
    query: SafeLine = Field(min_length=1, max_length=200, description="Part of the file name to look for.")
    folder: PcPath = Field(default="~", max_length=500, description="Where to look (searched recursively).")


class PcPathArgs(ToolArgs):
    path: PcPath = Field(min_length=1, max_length=500, description="The file's path.")


class PcOpenArgs(ToolArgs):
    target: PcPath = Field(min_length=1, max_length=2000,
                           description="An app name ('notepad', 'chrome', 'spotify'), a web address, or a file or folder path.")


class PcMediaArgs(ToolArgs):
    action: Literal["play_pause", "next", "previous", "mute", "volume_up", "volume_down"]
    steps: int = Field(default=1, ge=1, le=20, description="For volume_up/volume_down: how many 2% steps.")


class PcVolumeArgs(ToolArgs):
    level: int = Field(ge=0, le=100, description="Volume in percent.")


class PcWindowArgs(ToolArgs):
    title: SafeLine = Field(min_length=1, max_length=200, description="Part of the window's title.")


class PcTypeArgs(ToolArgs):
    text: PlainText = Field(min_length=1, max_length=5000, description="Text to type into the active window.")


class PcKeysArgs(ToolArgs):
    keys: SafeLine = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9+ _-]+$",
                           description="A key or combination, e.g. 'enter', 'ctrl+s', 'alt+tab', 'win+d'.")


class PcClickArgs(ToolArgs):
    x: int = Field(ge=0, le=20000)
    y: int = Field(ge=0, le=20000)
    button: Literal["left", "right", "double"] = "left"


class PcWriteFileArgs(ToolArgs):
    path: PcPath = Field(min_length=1, max_length=500)
    content: PlainText = Field(max_length=200_000)
    overwrite: bool = Field(default=False, description="Replace the file if it already exists.")


class PcMoveArgs(ToolArgs):
    source: PcPath = Field(min_length=1, max_length=500)
    destination: PcPath = Field(min_length=1, max_length=500)


class PcClipboardArgs(ToolArgs):
    text: PlainText = Field(max_length=20_000)


class PcGoToArgs(ToolArgs):
    query: SafeLine = Field(min_length=1, max_length=300,
                            description="What page to open, in words, e.g. 'Jägermeister Wikipedia' or 'Pune weather'.")


SITES = Literal["google", "youtube", "amazon", "flipkart", "wikipedia", "github", "maps", "images", "news", "reddit",
                "spotify", "gmail", "x"]


class PcSearchSiteArgs(ToolArgs):
    site: SITES = Field(description="Where to search.")
    query: SafeLine = Field(min_length=1, max_length=300)


class PcPlayArgs(ToolArgs):
    query: SafeLine = Field(min_length=1, max_length=300, description="The song, video, artist or playlist.")
    service: Literal["youtube", "spotify"] = Field(default="youtube", description="Where to play it.")


class PcBrightnessArgs(ToolArgs):
    level: int = Field(ge=0, le=100, description="Screen brightness in percent.")


class PcThemeArgs(ToolArgs):
    mode: Literal["dark", "light"]


class PcSettingsArgs(ToolArgs):
    page: Literal["wifi", "bluetooth", "display", "sound", "battery", "apps", "update", "notifications", "network",
                  "personalization", "storage", "mouse", "keyboard", "time", "privacy", "home"]


class PcTimerArgs(ToolArgs):
    seconds: int = Field(ge=1, le=86_400, description="How long, in seconds (5 minutes = 300).")
    label: SafeLine = Field(default="Timer", max_length=100)


class PcAppArgs(ToolArgs):
    name: SafeLine = Field(min_length=1, max_length=100, description="The app's name, e.g. 'chrome' or 'spotify'.")


class PcCopyArgs(ToolArgs):
    source: PcPath = Field(min_length=1, max_length=500)
    destination: PcPath = Field(min_length=1, max_length=500, description="A folder, or the new file's path.")


class PcFolderArgs(ToolArgs):
    path: PcPath = Field(min_length=1, max_length=500, description="The folder, e.g. '~/Downloads'.")


class PcWriteInAppArgs(ToolArgs):
    app: Literal["notepad", "word", "wordpad"] = Field(default="notepad")
    text: PlainText = Field(min_length=1, max_length=20_000, description="The whole text to write.")


class PcScreenArgs(ToolArgs):
    window: SafeLine = Field(default="", max_length=200, description="Part of the window's title; empty = the one in front.")


class PcElementArgs(ToolArgs):
    name: SafeLine = Field(min_length=1, max_length=200, description="The button, link or field's name, from pc_read_screen.")
    window: SafeLine = Field(default="", max_length=200, description="Part of the window's title; empty = the one in front.")


class PcFillArgs(PcElementArgs):
    text: PlainText = Field(max_length=5000, description="What to put in the field.")


class PcSendMessageArgs(ToolArgs):
    contact: SafeLine = Field(min_length=1, max_length=100, description="The chat's name as saved in WhatsApp, e.g. 'Tanishka' or 'Mum'.")
    text: PlainText = Field(min_length=1, max_length=4000, description="The exact message to send.")
    app: Literal["whatsapp"] = "whatsapp"


class PcDndArgs(ToolArgs):
    on: bool = Field(default=True, description="true silences notifications; false lets them through again.")


class PcAlarmArgs(ToolArgs):
    time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$", description="When, as a 24-hour time, e.g. '07:00' or '18:30'.")
    label: SafeLine = Field(default="Alarm", max_length=100)


class PcSayArgs(ToolArgs):
    text: PlainText = Field(min_length=1, max_length=1000, description="What to say out loud on the PC's speakers.")


class PcPlanArgs(ToolArgs):
    title: SafeLine = Field(min_length=1, max_length=100)
    steps: list[SafeLine] = Field(min_length=1, max_length=12)


# Routines -----------------------------------------------------------------------------------

# What a routine's steps may do: everyday PC actions that are easy to reverse or harmless. Never
# deleting, writing or moving files, typing, clicking or sending messages - those wait for a
# person every time.
ROUTINE_FUNCTIONS = frozenset({
    "pc_open", "pc_go_to", "pc_search_site", "pc_play", "pc_media", "pc_set_volume", "pc_lock_screen", "pc_set_brightness",
    "pc_set_theme", "pc_open_settings", "pc_timer", "pc_alarm", "pc_focus_window", "pc_close_window", "pc_close_app",
    "pc_create_folder", "pc_organize_folder", "pc_do_not_disturb", "pc_say", "pc_browser_open",
})
ROUTINE_STEP_HELP = ("Write each step as a short command, such as 'open vs code', 'open github.com', 'play lofi beats on "
                     "spotify', 'volume 30', 'brightness 60', 'dark mode', 'do not disturb on', '25 minute timer', 'alarm "
                     "at 07:00', 'close chrome', 'lock my pc' or 'say good night'.")


class SaveRoutineArgs(ToolArgs):
    name: SafeLine = Field(min_length=1, max_length=60,
                           description="Its name, which is also what they say to run it, e.g. 'work mode' or 'good night'.")
    steps: list[SafeLine] = Field(min_length=1, max_length=12,
                                  description="What it does, in order. " + ROUTINE_STEP_HELP)
    phrases: list[SafeLine] = Field(default_factory=list, max_length=5,
                                    description="Other ways they'll say it, e.g. 'start work' (optional).")
    # Filled in by the check below from `steps` (anything sent in its place is overwritten): the
    # tool calls each step stands for, validated like any other call.
    parsed: SkipJsonSchema[list[dict]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _steps_are_actions(self) -> "SaveRoutineArgs":
        from .. import fastpath  # here: fastpath imports the model backends, which import this module's users
        from . import registry

        parsed, unclear = [], []
        for step in self.steps:
            intent = fastpath.parse(step, routine=True)
            fn = registry.find(intent.function) if intent and intent.then is None else None
            if fn is None or fn.exposed_name not in ROUTINE_FUNCTIONS:
                unclear.append(step)
                continue
            args = fn.args_model.model_validate(registry.fix_arguments(fn, intent.args)).model_dump(mode="json")
            parsed.append({"function": fn.exposed_name, "args": args, "said": step})
        if unclear:
            raise ValueError("these steps aren't actions a routine can do: " + "; ".join(f"'{s}'" for s in unclear)
                             + ". " + ROUTINE_STEP_HELP + " A routine can't delete or write files, type, click or send messages.")
        self.parsed = parsed
        return self


class RoutineNameArgs(ToolArgs):
    name: SafeLine = Field(min_length=1, max_length=60, description="The routine's name.")
