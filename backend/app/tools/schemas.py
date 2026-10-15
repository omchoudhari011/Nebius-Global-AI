"""Argument models for every tool function - the single source of truth for what the
model may pass. The gate validates against these before a call reaches the sandbox;
unknown fields, wrong types, control characters and path-like values are rejected.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated

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
