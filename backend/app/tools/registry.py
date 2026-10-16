"""Every tool function the model can call, with the scope it needs.

The model sees each function as `<tool>_<function>` (OpenAI function names can't contain
dots). `tool` must match tools.name and `scope` a tool_scopes row, both seeded by
schema.sql.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .schemas import (
    CancelReminderArgs,
    CreateEventArgs,
    CreateNoteArgs,
    CreateReminderArgs,
    GoogleCreateEventArgs,
    GoogleListEventsArgs,
    ListEventsArgs,
    ListRemindersArgs,
    ReadDocumentArgs,
    ReadDriveArgs,
    ReadMailArgs,
    ReadNotesArgs,
    SearchDocumentsArgs,
    SearchDriveArgs,
    SearchMailArgs,
    NoArgs,
    PcClickArgs,
    PcClipboardArgs,
    PcFindFilesArgs,
    PcKeysArgs,
    PcListFilesArgs,
    PcMediaArgs,
    PcMoveArgs,
    PcOpenArgs,
    PcPathArgs,
    PcTypeArgs,
    PcVolumeArgs,
    PcWindowArgs,
    PcWriteFileArgs,
    ReadPageArgs,

    WebSearchArgs,
    ToolArgs,
)

GOOGLE_HOSTS = ("www.googleapis.com", "gmail.googleapis.com")
# How each tool is named to the user, in switches and in "turn on ..." messages.
DISPLAY_NAMES = {"gcal": "Google Calendar", "gmail": "Gmail", "gdrive": "Google Drive", "documents": "Files",
                 "web": "Web", "pc": "PC"}


def display_name(tool: str) -> str:
    return DISPLAY_NAMES.get(tool, tool.capitalize())


@dataclass(frozen=True)
class ToolFunction:
    tool: str
    function: str
    scope: str
    description: str
    args_model: type[ToolArgs]
    module: str
    account: str | None = None  # a connected account the tool needs ("google"); the gate passes its token
    hosts: tuple[str, ...] = ()  # the only hosts the sandbox lets this tool reach ("*": any public host)
    runs_on: str = "sandbox"  # "sandbox" on the server, or "desktop": the Custodian app on your PC
    confirm: bool = False  # the desktop app asks you before doing it (changes or reveals something)

    @property
    def exposed_name(self) -> str:
        return f"{self.tool}_{self.function}"

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.exposed_name,
                "description": self.description,
                "parameters": clean_schema(self.args_model.model_json_schema()),
            },
        }


FUNCTIONS: tuple[ToolFunction, ...] = (
    ToolFunction(
        tool="calendar",
        function="list_events",
        scope="read",
        description="List the user's calendar events for a day or a range of days.",
        args_model=ListEventsArgs,
        module="app.tools.calendar_tool",
    ),
    ToolFunction(
        tool="calendar",
        function="create_event",
        scope="write",
        description="Add an event to the user's calendar.",
        args_model=CreateEventArgs,
        module="app.tools.calendar_tool",
    ),
    ToolFunction(
        tool="notes",
        function="create_note",
        scope="write",
        description="Save a new note.",
        args_model=CreateNoteArgs,
        module="app.tools.notes_tool",
    ),
    ToolFunction(
        tool="notes",
        function="read_notes",
        scope="read",
        description="Search the user's notes by keyword, or list the most recent ones.",
        args_model=ReadNotesArgs,
        module="app.tools.notes_tool",
    ),
    ToolFunction(
        tool="reminders",
        function="create_reminder",
        scope="write",
        description="Set a reminder: Custodian notifies the user at that time (in the app, and on their phone if set up).",
        args_model=CreateReminderArgs,
        module="app.tools.reminders_tool",
    ),
    ToolFunction(
        tool="reminders",
        function="list_reminders",
        scope="read",
        description="List the user's upcoming reminders.",
        args_model=ListRemindersArgs,
        module="app.tools.reminders_tool",
    ),
    ToolFunction(
        tool="reminders",
        function="cancel_reminder",
        scope="write",
        description="Cancel a pending reminder by its id (from list_reminders).",
        args_model=CancelReminderArgs,
        module="app.tools.reminders_tool",
    ),
    ToolFunction(
        tool="documents",
        function="search_documents",
        scope="read",
        description="Search the files the user uploaded (PDFs, text, photos) by keyword; returns ids and snippets.",
        args_model=SearchDocumentsArgs,
        module="app.tools.documents_tool",
    ),
    ToolFunction(
        tool="documents",
        function="read_document",
        scope="read",
        description="Read part of an uploaded file by its id (from search_documents).",
        args_model=ReadDocumentArgs,
        module="app.tools.documents_tool",
    ),
    ToolFunction(
        tool="gcal",
        function="list_events",
        scope="read",
        description="List events on the user's Google Calendar for a day or a range of days.",
        args_model=GoogleListEventsArgs,
        module="app.tools.google_tools",
        account="google",
        hosts=GOOGLE_HOSTS,
    ),
    ToolFunction(
        tool="gcal",
        function="create_event",
        scope="write",
        description="Add an event to the user's Google Calendar.",
        args_model=GoogleCreateEventArgs,
        module="app.tools.google_tools",
        account="google",
        hosts=GOOGLE_HOSTS,
    ),
    ToolFunction(
        tool="gmail",
        function="search_messages",
        scope="read",
        description="Search the user's Gmail (Gmail search syntax); returns sender, subject, date and a snippet.",
        args_model=SearchMailArgs,
        module="app.tools.google_tools",
        account="google",
        hosts=GOOGLE_HOSTS,
    ),
    ToolFunction(
        tool="gmail",
        function="read_message",
        scope="read",
        description="Read one email by its id (from search_messages).",
        args_model=ReadMailArgs,
        module="app.tools.google_tools",
        account="google",
        hosts=GOOGLE_HOSTS,
    ),
    ToolFunction(
        tool="gdrive",
        function="search_files",
        scope="read",
        description="Search the user's Google Drive by file name or text.",
        args_model=SearchDriveArgs,
        module="app.tools.google_tools",
        account="google",
        hosts=GOOGLE_HOSTS,
    ),
    ToolFunction(
        tool="gdrive",
        function="read_file",
        scope="read",
        description="Read the text of a Google Doc, Sheet, Slides or text file by its id (from search_files).",
        args_model=ReadDriveArgs,
        module="app.tools.google_tools",
        account="google",
        hosts=GOOGLE_HOSTS,
    ),
    ToolFunction(
        tool="web",
        function="search",
        scope="read",
        description="Search the web (news, facts, prices, how-to, anything current). Returns titles, links and snippets; cite the links you use.",
        args_model=WebSearchArgs,
        module="app.tools.web_tools",
        hosts=("searxng",),
    ),
    ToolFunction(
        tool="web",
        function="read_page",
        scope="read",
        description="Read the text of one web page by its link (from web_search, or one the user gave).",
        args_model=ReadPageArgs,
        module="app.tools.web_tools",
        hosts=("*",),
    ),
    ToolFunction(tool="pc", function="system_info", scope="read", args_model=NoArgs, module="desktop", runs_on="desktop",
                 description="The PC's battery, CPU, memory, disk space, time, and the window in front."),
    ToolFunction(tool="pc", function="list_files", scope="read", args_model=PcListFilesArgs, module="desktop", runs_on="desktop",
                 description="List the files in a folder on the PC."),
    ToolFunction(tool="pc", function="find_files", scope="read", args_model=PcFindFilesArgs, module="desktop", runs_on="desktop",
                 description="Find files on the PC whose name contains some text."),
    ToolFunction(tool="pc", function="read_file", scope="read", args_model=PcPathArgs, module="desktop", runs_on="desktop", confirm=True,
                 description="Read a text file on the PC (the user confirms)."),
    ToolFunction(tool="pc", function="list_windows", scope="read", args_model=NoArgs, module="desktop", runs_on="desktop",
                 description="The titles of the open windows."),
    ToolFunction(tool="pc", function="get_clipboard", scope="read", args_model=NoArgs, module="desktop", runs_on="desktop", confirm=True,
                 description="The text on the clipboard (the user confirms)."),
    ToolFunction(tool="pc", function="screenshot", scope="read", args_model=NoArgs, module="desktop", runs_on="desktop", confirm=True,
                 description="Take a screenshot and describe what's on the screen (the user confirms)."),
    ToolFunction(tool="pc", function="open", scope="write", args_model=PcOpenArgs, module="desktop", runs_on="desktop",
                 description="Open an app ('notepad', 'chrome', 'spotify', 'settings'), a web address, or a file or folder."),
    ToolFunction(tool="pc", function="media", scope="write", args_model=PcMediaArgs, module="desktop", runs_on="desktop",
                 description="Media keys: play/pause, next, previous, mute, volume up or down."),
    ToolFunction(tool="pc", function="set_volume", scope="write", args_model=PcVolumeArgs, module="desktop", runs_on="desktop",
                 description="Set the PC's volume to a percentage."),
    ToolFunction(tool="pc", function="lock_screen", scope="write", args_model=NoArgs, module="desktop", runs_on="desktop",
                 description="Lock the PC."),
    ToolFunction(tool="pc", function="focus_window", scope="write", args_model=PcWindowArgs, module="desktop", runs_on="desktop",
                 description="Bring a window to the front by part of its title."),
    ToolFunction(tool="pc", function="close_window", scope="write", args_model=PcWindowArgs, module="desktop", runs_on="desktop", confirm=True,
                 description="Close a window by part of its title (the user confirms)."),
    ToolFunction(tool="pc", function="type_text", scope="write", args_model=PcTypeArgs, module="desktop", runs_on="desktop", confirm=True,
                 description="Type text into the window in front (the user confirms)."),
    ToolFunction(tool="pc", function="press_keys", scope="write", args_model=PcKeysArgs, module="desktop", runs_on="desktop", confirm=True,
                 description="Press a key or shortcut such as 'ctrl+s' or 'alt+tab' (the user confirms)."),
    ToolFunction(tool="pc", function="click", scope="write", args_model=PcClickArgs, module="desktop", runs_on="desktop", confirm=True,
                 description="Click at screen coordinates (the user confirms)."),
    ToolFunction(tool="pc", function="write_file", scope="write", args_model=PcWriteFileArgs, module="desktop", runs_on="desktop", confirm=True,
                 description="Create or overwrite a text file on the PC (the user confirms)."),
    ToolFunction(tool="pc", function="move_file", scope="write", args_model=PcMoveArgs, module="desktop", runs_on="desktop", confirm=True,
                 description="Move or rename a file or folder (the user confirms)."),
    ToolFunction(tool="pc", function="delete_file", scope="write", args_model=PcPathArgs, module="desktop", runs_on="desktop", confirm=True,
                 description="Move a file or folder to the Recycle Bin (the user confirms)."),
    ToolFunction(tool="pc", function="set_clipboard", scope="write", args_model=PcClipboardArgs, module="desktop", runs_on="desktop",
                 description="Put text on the clipboard."),
)

_BY_NAME = {f.exposed_name: f for f in FUNCTIONS}
_BY_NAME.update({f"{f.tool}.{f.function}": f for f in FUNCTIONS})


def find(name: str) -> ToolFunction | None:
    return _BY_NAME.get(name)


def for_scopes(allowed: frozenset[tuple[str, str]] | None) -> list[ToolFunction]:
    """The functions a skill may use; every function when allowed is None."""
    if allowed is None:
        return list(FUNCTIONS)
    return [f for f in FUNCTIONS if (f.tool, f.scope) in allowed]


SERVER_ONLY = {"title", "additionalProperties", "maxLength", "minLength", "maxItems", "minItems", "pattern"}


def clean_schema(node: Any, *, in_properties: bool = False) -> Any:
    """Tidy a pydantic JSON schema for function calling: drop titles and the limits only the
    server enforces (lengths, patterns - every call is validated against the full model
    anyway), and collapse Optional[X] (anyOf X | null) to X, which models handle more
    reliably. Shorter tool lists make for faster, steadier replies from a small model."""
    if isinstance(node, list):
        return [clean_schema(x) for x in node]
    if not isinstance(node, dict):
        return node
    if in_properties:  # keys here are field names, not schema keywords
        return {k: clean_schema(v) for k, v in node.items()}
    out = {k: clean_schema(v, in_properties=(k == "properties")) for k, v in node.items() if k not in SERVER_ONLY}
    any_of = out.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2 and {"type": "null"} in any_of:
        other = next(x for x in any_of if x != {"type": "null"})
        out.pop("anyOf")
        out = {**other, **out}
        if out.get("default") is None:
            out.pop("default", None)
    return out
