"""Every tool function the model can call, with the scope it needs.

The model sees each function as `<tool>_<function>` (OpenAI function names can't contain
dots). `tool` must match tools.name and `scope` a tool_scopes row, both seeded by
schema.sql.
"""

from __future__ import annotations

import re
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
    PcAppArgs,
    PcBrightnessArgs,
    PcCopyArgs,
    PcElementArgs,
    PcFillArgs,
    PcFolderArgs,
    PcGoToArgs,
    PcPlayArgs,
    PcScreenArgs,
    PcSearchSiteArgs,
    PcSettingsArgs,
    PcThemeArgs,
    PcTimerArgs,
    PcWriteInAppArgs,
    PcSendMessageArgs,
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
    group: str = ""  # offered only when the message is about this (see GROUPS); "" = always

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
    *(
        ToolFunction(tool="pc", function=function, scope=scope, args_model=args, module="desktop", runs_on="desktop",
                     confirm=confirm, group=group, description=description)
        for function, scope, group, confirm, args, description in (
            # Always offered: opening things, and going to a web page by description.
            ("open", "write", "", False, PcOpenArgs,
             "Open an app ('notepad', 'chrome', 'spotify'), a site by name ('youtube', 'gmail'), a web address, or a file or folder."),
            ("go_to", "write", "", False, PcGoToArgs,
             "Open the best web page for a description in the browser, e.g. 'Jägermeister Wikipedia' or 'IRCTC login'."),
            ("search_site", "write", "web", False, PcSearchSiteArgs,
             "Open a site's search results in the browser, e.g. Amazon for 'wireless earbuds'."),
            ("play", "write", "media", False, PcPlayArgs,
             "Find a song or video and start playing it on YouTube (default) or Spotify."),
            ("now_playing", "read", "media", False, NoArgs, "What's playing now in Spotify or a YouTube tab."),
            ("media", "write", "media", False, PcMediaArgs,
             "Media keys: play/pause, next, previous, mute, volume up or down. The result says what's playing after."),
            ("set_volume", "write", "media", False, PcVolumeArgs, "Set the PC's volume to a percentage."),
            ("system_info", "read", "system", False, NoArgs,
             "The PC's battery, CPU, memory, disk space, time, and the window in front."),
            ("lock_screen", "write", "system", False, NoArgs, "Lock the PC."),
            ("set_brightness", "write", "system", False, PcBrightnessArgs, "Set the screen brightness."),
            ("set_theme", "write", "system", False, PcThemeArgs, "Switch Windows to dark or light mode."),
            ("open_settings", "write", "system", False, PcSettingsArgs,
             "Open a Windows Settings page (Wi-Fi, Bluetooth, display, sound, ...)."),
            ("timer", "write", "system", False, PcTimerArgs, "Start a countdown on the PC; it rings and shows a note when done."),
            ("list_windows", "read", "apps", False, NoArgs, "The titles of the open windows."),
            ("list_apps", "read", "apps", False, NoArgs, "The apps running with a window, and their memory use."),
            ("focus_window", "write", "apps", False, PcWindowArgs, "Bring a window to the front by part of its title."),
            ("close_window", "write", "apps", True, PcWindowArgs, "Close a window by part of its title (the user confirms)."),
            ("close_app", "write", "apps", True, PcAppArgs, "Quit an app and all its windows, e.g. 'chrome' (the user confirms)."),
            ("list_files", "read", "files", False, PcListFilesArgs, "List the files in a folder on the PC."),
            ("find_files", "read", "files", False, PcFindFilesArgs, "Find files on the PC whose name contains some text."),
            ("read_file", "read", "files", True, PcPathArgs, "Read a text file on the PC (the user confirms)."),
            ("write_file", "write", "files", True, PcWriteFileArgs, "Create or overwrite a text file on the PC (the user confirms)."),
            ("copy_file", "write", "files", False, PcCopyArgs, "Copy a file or folder."),
            ("move_file", "write", "files", True, PcMoveArgs, "Move or rename a file or folder (the user confirms)."),
            ("delete_file", "write", "files", True, PcPathArgs, "Move a file or folder to the Recycle Bin (the user confirms)."),
            ("create_folder", "write", "files", False, PcFolderArgs, "Create a folder."),
            ("organize_folder", "write", "files", True, PcFolderArgs,
             "Sort a folder's loose files into subfolders by type: Images, Documents, Videos, ... (the user confirms)."),
            ("write_in_app", "write", "typing", True, PcWriteInAppArgs,
             "Open Notepad or Word and write text into a new document (the user confirms)."),
            ("send_message", "write", "messaging", True, PcSendMessageArgs,
             "Send a WhatsApp message to one chat by name. The PC shows the chat it found and the text, and sends only if the user allows."),
            ("type_text", "write", "typing", True, PcTypeArgs, "Type text into the window in front (the user confirms)."),
            ("press_keys", "write", "typing", True, PcKeysArgs,
             "Press a key or shortcut such as 'ctrl+s' or 'alt+tab' (the user confirms)."),
            ("set_clipboard", "write", "typing", False, PcClipboardArgs, "Put text on the clipboard."),
            ("get_clipboard", "read", "typing", True, NoArgs, "The text on the clipboard (the user confirms)."),
            ("read_screen", "read", "screen", False, PcScreenArgs,
             "List the buttons, links, fields and text in a window, by name, to click or fill them."),
            ("click_element", "write", "screen", True, PcElementArgs,
             "Click a button or link by its name, from pc_read_screen (the user confirms)."),
            ("fill_field", "write", "screen", True, PcFillArgs, "Type into a text field by its name (the user confirms)."),
            ("screenshot", "read", "screen", True, NoArgs, "Take a screenshot and describe what's on the screen (the user confirms)."),
            ("click", "write", "screen", True, PcClickArgs, "Click at screen coordinates (the user confirms)."),
        )
    ),
)

# Which PC tools a message is offered: a small model picks better from a short list, and a
# shorter prompt answers faster. A message matching no group gets every PC tool.
GROUPS: dict[str, tuple[str, ...]] = {
    "web": ("search", "amazon", "flipkart", "wikipedia", "google", "github", "reddit", "maps", "map", "images",
            "news", "shopping", "buy", "website", "site", "page", "look up", "find on", "pictures", "photos of"),
    "media": ("play", "pause", "resume", "stop", "music", "song", "songs", "video", "videos", "track", "playlist",
              "album", "artist", "volume", "louder", "quieter", "loud", "mute", "unmute", "skip", "next", "previous",
              "spotify", "youtube", "playing", "listen", "watch"),
    "system": ("battery", "charging", "cpu", "processor", "ram", "memory", "disk", "storage", "space", "drive",
               "brightness", "bright", "dim", "dark", "light", "theme", "mode", "settings", "wifi", "wi fi",
               "bluetooth", "display", "sound", "timer", "countdown", "lock", "update", "network", "time", "plugged",
               "charger", "power", "percent", "volume"),
    "apps": ("window", "windows", "app", "apps", "program", "programs", "close", "quit", "exit", "kill", "shut down", "running", "switch to", "focus",
             "minimize", "in front", "tab", "tabs"),
    "files": ("file", "files", "folder", "folders", "downloads", "desktop", "documents", "pictures", "my resume", "cv",
              "pdf", "pdfs", "delete", "remove", "move", "copy", "rename", "organize", "organise", "sort", "clean",
              "tidy", "save a", "save it", "save the", "find", "zip", "photo", "photos"),
    "typing": ("type", "write", "writing", "press", "key", "keys", "shortcut", "ctrl", "alt", "enter", "paste",
               "clipboard", "notepad", "word", "letter", "essay", "draft", "copy", "text"),
    "messaging": ("whatsapp", "message", "messages", "msg", "send", "text", "texting", "tell", "reply", "wish", "ping"),
    "screen": ("on my screen", "on the screen", "on screen", "read the screen", "my screen", "click", "button", "fill",
               "form", "field", "screenshot", "you see", "showing", "sign in", "log in", "submit", "tick", "check box"),
}


def groups_for(message: str | None) -> set[str]:
    text = " " + " ".join(re.findall(r"[a-z0-9]+", (message or "").lower())) + " "
    return {g for g, words in GROUPS.items() if any(f" {w} " in text for w in words)}


def offer(functions: list[ToolFunction], message: str | None, *, everything_if_unclear: bool = False) -> list[ToolFunction]:
    """`functions` narrowed to the groups the message is about. A message matching no group
    gets only the ungrouped ones (opening apps, sites and pages), unless `everything_if_unclear`."""
    wanted = groups_for(message)
    if not wanted and everything_if_unclear:
        return functions
    return [f for f in functions if not f.group or f.group in wanted]

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


_SERVICE_IN_QUERY = re.compile(r"\s+(?:on|in|from|using)\s+(spotify|youtube)\s*$", re.I)


def fix_arguments(fn: ToolFunction, args: dict) -> dict:
    """Mend arguments a small model reliably gets slightly wrong, before validation: "Perfect by
    Ed Sheeran in spotify" is the song "Perfect by Ed Sheeran" on Spotify."""
    if fn.exposed_name == "pc_play" and isinstance(args.get("query"), str):
        found = _SERVICE_IN_QUERY.search(args["query"])
        if found:
            args = {**args, "query": args["query"][: found.start()].strip(), "service": found.group(1).lower()}
    return args
