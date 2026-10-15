"""Argument models for every tool function - the single source of truth for what the
model may pass. The gate validates against these before a call reaches the sandbox;
unknown fields, wrong types, control characters and path-like values are rejected.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

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
