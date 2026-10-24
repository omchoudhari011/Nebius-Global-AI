"""Every tool function the model can call, with the scope it needs.

The model sees each function as `<tool>_<function>` (OpenAI function names can't contain
dots). `tool` must match tools.name and `scope` a tool_scopes row, both seeded by
schema.sql.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, get_origin

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
    EmailArgs,
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
    PcNotificationsArgs,
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
    PcClickOnArgs,
    PcLookArgs,
    PcBrowserDownloadArgs,
    PcBrowserElementArgs,
    PcBrowserFillArgs,
    PcBrowserOpenArgs,
    PcBrowserTabArgs,
    PcBrowserTabsArgs,
    PcAlarmArgs,
    PcDndArgs,
    PcPlanArgs,
    PcSayArgs,
    PcSearchContentsArgs,
    ReadPageArgs,
    WebResearchArgs,
    WeatherArgs,
    PcWriteDocumentArgs,
    RoutineNameArgs,
    SaveRoutineArgs,
    WatchEmailArgs,
    WatchIdArgs,
    WatchPageArgs,
    HomeControlArgs,
    HomeListArgs,
    OutlookCreateEventArgs,
    OutlookListEventsArgs,
    OutlookReadMailArgs,
    OutlookSearchMailArgs,
    SpotifyControlArgs,
    SpotifyPlayArgs,
    SpotifySearchArgs,
    WebSearchArgs,
    ToolArgs,
)

GOOGLE_HOSTS = ("www.googleapis.com", "gmail.googleapis.com")
SPOTIFY_HOSTS = ("api.spotify.com",)
MICROSOFT_HOSTS = ("graph.microsoft.com",)
# How each tool is named to the user, in switches and in "turn on ..." messages.
DISPLAY_NAMES = {"gcal": "Google Calendar", "gmail": "Gmail", "gdrive": "Google Drive", "documents": "Files",
                 "web": "Web", "pc": "PC", "outlook": "Outlook",
                 "home": "Home", "spotify": "Spotify"}


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
    account: str | None = None  # a connected account the tool needs ("google", "spotify", ...); the gate passes its token
    hosts: tuple[str, ...] = ()  # the only hosts the sandbox lets this tool reach ("*": any public host,
    #                              "account": the connected account's own address - Home Assistant's)
    runs_on: str = "sandbox"  # "sandbox" on the server, or "desktop": the Custodian app on your PC
    confirm: bool = False  # you're asked before it's done (changes, reveals or sends something): on the PC, or
    #                        for a server tool where you asked (app/approvals.confirm)
    group: str = ""  # offered only when the message is about this (see GROUPS); "" = always
    query_vector: bool = False  # the gate embeds the `query` argument for it (search by meaning)
    timeout: float = 0  # seconds it may take, when that's longer than the usual limit

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
        tool="gmail",
        function="draft_email",
        scope="write",
        description="Save an email (or a reply, with reply_to_id) in Gmail's Drafts without sending it.",
        args_model=EmailArgs,
        module="app.tools.google_tools",
        account="google",
        hosts=GOOGLE_HOSTS,
    ),
    ToolFunction(
        tool="gmail",
        function="send_email",
        scope="write",
        description=("Send an email (or a reply, with reply_to_id) from the user's Gmail. They see the recipients and "
                     "the exact text and it's sent only if they allow it."),
        args_model=EmailArgs,
        module="app.tools.google_tools",
        account="google",
        hosts=GOOGLE_HOSTS,
        confirm=True,
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
    ToolFunction(
        tool="web",
        function="weather",
        scope="read",
        description=("The weather in a city now and for the next days (°C, rain chance, wind), from Open-Meteo. "
                     "Use this for any weather question, not web_search."),
        args_model=WeatherArgs,
        module="app.tools.web_tools",
        hosts=("geocoding-api.open-meteo.com", "api.open-meteo.com"),
    ),
    ToolFunction(
        tool="web",
        function="research",
        scope="read",
        description=("Research a topic for a report or a comparison: runs several web searches at once, reads the best "
                     "pages (from different sites) and returns what each says about it, numbered [1], [2], ... with "
                     "its link. One call instead of many searches and page reads."),
        args_model=WebResearchArgs,
        module="app.tools.web_tools",
        hosts=("searxng", "*"),
        timeout=50,
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
            ("write_document", "write", "typing", False, PcWriteDocumentArgs,
             "Make a Word document (.docx) with a title, headed sections, bullets and the sources as links, save it as a "
             "new file in Documents (never over another) and open it - for a report or anything longer to keep."),
            ("write_in_app", "write", "typing", True, PcWriteInAppArgs,
             "Open Notepad or Word and write text into a new document (the user confirms)."),
            ("notifications", "read", "messaging", True, PcNotificationsArgs,
             "The user's recent notifications (WhatsApp, Outlook, Teams, sites in Chrome, their phone's through Phone "
             "Link) and each app's unread count - for 'what did I miss?' or 'any new WhatsApp messages?' (the user "
             "confirms)."),
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
            ("look", "read", "screen", True, PcLookArgs,
             "Look at the screen and answer a question about it - 'what's on my screen?', 'what does this error say?' - "
             "for any app or image (the user confirms)."),
            ("click_on", "write", "screen", True, PcClickOnArgs,
             "Click something on the screen by how it looks - 'the blue Sign in button' - when pc_read_screen doesn't "
             "list it. It looks, clicks, then checks the screen changed (the user confirms once)."),
            # Custodian's own browser, driven directly (desktop browser.py): reliable web tasks.
            ("browser_open", "write", "browser", False, PcBrowserOpenArgs,
             "Open a page in Custodian's own browser, to read it or work on it with the other pc_browser tools."),
            ("browser_read", "read", "browser", False, PcBrowserTabArgs,
             "Read the page in Custodian's browser: its text, and its links, buttons and fields numbered by `ref`."),
            ("browser_click", "write", "browser", False, PcBrowserElementArgs,
             "Click a link or button in Custodian's browser by its ref (or name) from pc_browser_read. Anything that "
             "submits, buys, pays, sends, books or deletes asks the user first."),
            ("browser_fill", "write", "browser", False, PcBrowserFillArgs,
             "Type into a field (or pick in a list) in Custodian's browser by its ref or name; submit=true presses "
             "Enter to send it (the user confirms). Never passwords."),
            ("browser_download", "write", "browser", True, PcBrowserDownloadArgs,
             "Download a file - a link on the page, or an address - into Downloads (the user confirms)."),
            ("browser_tabs", "write", "browser", False, PcBrowserTabsArgs, "List, switch to or close tabs in Custodian's browser."),
            ("do_not_disturb", "write", "system", False, PcDndArgs,
             "Turn Windows' Do Not Disturb on (silence notifications) or off."),
            ("alarm", "write", "system", False, PcAlarmArgs,
             "Set an alarm on the PC for a time of day (the next time it comes round); it rings and speaks."),
            ("say", "write", "speak", False, PcSayArgs, "Say something out loud on the PC's speakers."),
            # Never offered to the model: a routine's single approval for all its steps (app/routines.py).
            ("confirm_plan", "write", "internal", True, PcPlanArgs, "Ask the user once to allow a list of steps."),
        )
    ),
    ToolFunction(
        tool="pc",
        function="search_file_contents",
        scope="read",
        description=("Search inside the files on the PC (the folders the user chose to index: Documents, Downloads, "
                     "Desktop) by what they're about or say - 'the PDF where I wrote about the Pune flat'. Returns the "
                     "files that match best, with the passage that matched. To find a file by its name, use pc_find_files."),
        args_model=PcSearchContentsArgs,
        module="app.tools.pc_files_tool",
        group="files",
        query_vector=True,
    ),
    ToolFunction(
        tool="watchers",
        function="watch_page",
        scope="write",
        description=("Keep an eye on a web page and tell the user when something happens: a price drops below or goes "
                     "above a number, the page changes, or some words appear or go ('In stock'). Needs the page's link "
                     "(find it with web_search first)."),
        args_model=WatchPageArgs,
        module="app.tools.watchers_tool",
    ),
    ToolFunction(
        tool="watchers",
        function="watch_email",
        scope="write",
        description="Tell the user when an email from someone arrives (needs Gmail connected and Gmail · read on).",
        args_model=WatchEmailArgs,
        module="app.tools.watchers_tool",
    ),
    ToolFunction(
        tool="watchers",
        function="list_watches",
        scope="read",
        description="List what Custodian is watching for the user.",
        args_model=NoArgs,
        module="app.tools.watchers_tool",
    ),
    ToolFunction(
        tool="watchers",
        function="stop_watch",
        scope="write",
        description="Stop watching something, by its id from list_watches.",
        args_model=WatchIdArgs,
        module="app.tools.watchers_tool",
    ),
    ToolFunction(
        tool="routines",
        function="save_routine",
        scope="write",
        description=("Create or replace a routine: a name they'll say to run it ('work mode', 'good night') and the "
                     "steps it does in order, each a short command. Saying the name later runs it."),
        args_model=SaveRoutineArgs,
        module="app.tools.routines_tool",
    ),
    ToolFunction(
        tool="routines",
        function="list_routines",
        scope="read",
        description="List the user's routines and the steps of each.",
        args_model=NoArgs,
        module="app.tools.routines_tool",
    ),
    ToolFunction(
        tool="routines",
        function="delete_routine",
        scope="write",
        description="Delete one of the user's routines by its name.",
        args_model=RoutineNameArgs,
        module="app.tools.routines_tool",
    ),
    ToolFunction(
        tool="spotify",
        function="search",
        scope="read",
        description="Search Spotify for songs, albums, artists or playlists; returns names and spotify: links.",
        args_model=SpotifySearchArgs,
        module="app.tools.spotify_tool",
        account="spotify",
        hosts=SPOTIFY_HOSTS,
        group="media",
    ),
    ToolFunction(
        tool="spotify",
        function="now_playing",
        scope="read",
        description="What's playing on Spotify right now, on which device, and at what volume.",
        args_model=NoArgs,
        module="app.tools.spotify_tool",
        account="spotify",
        hosts=SPOTIFY_HOSTS,
        group="media",
    ),
    ToolFunction(
        tool="spotify",
        function="play",
        scope="write",
        description=("Play an exact song, album, artist or playlist on Spotify (wherever Spotify is open: the PC, "
                     "phone or a speaker). Better than pc_play for Spotify when the account is connected."),
        args_model=SpotifyPlayArgs,
        module="app.tools.spotify_tool",
        account="spotify",
        hosts=SPOTIFY_HOSTS,
        group="media",
    ),
    ToolFunction(
        tool="spotify",
        function="control",
        scope="write",
        description="Pause, resume, skip, go back, set the volume, or turn shuffle or repeat on or off in Spotify.",
        args_model=SpotifyControlArgs,
        module="app.tools.spotify_tool",
        account="spotify",
        hosts=SPOTIFY_HOSTS,
        group="media",
    ),
    ToolFunction(
        tool="outlook",
        function="search_mail",
        scope="read",
        description="Search the user's Outlook (Microsoft 365) mail; returns sender, subject, date and a snippet.",
        args_model=OutlookSearchMailArgs,
        module="app.tools.outlook_tool",
        account="microsoft",
        hosts=MICROSOFT_HOSTS,
    ),
    ToolFunction(
        tool="outlook",
        function="read_mail",
        scope="read",
        description="Read one Outlook email by its id (from outlook_search_mail).",
        args_model=OutlookReadMailArgs,
        module="app.tools.outlook_tool",
        account="microsoft",
        hosts=MICROSOFT_HOSTS,
    ),
    ToolFunction(
        tool="outlook",
        function="list_events",
        scope="read",
        description="List events on the user's Outlook calendar for a day or a range of days.",
        args_model=OutlookListEventsArgs,
        module="app.tools.outlook_tool",
        account="microsoft",
        hosts=MICROSOFT_HOSTS,
    ),
    ToolFunction(
        tool="outlook",
        function="create_event",
        scope="write",
        description="Add an event to the user's Outlook calendar, optionally with a Teams meeting link.",
        args_model=OutlookCreateEventArgs,
        module="app.tools.outlook_tool",
        account="microsoft",
        hosts=MICROSOFT_HOSTS,
    ),
    ToolFunction(
        tool="home",
        function="list_devices",
        scope="read",
        description="List the lights, AC, fans, plugs and scenes in Home Assistant with their state and ids.",
        args_model=HomeListArgs,
        module="app.tools.home_tool",
        account="homeassistant",
        hosts=("account",),
    ),
    ToolFunction(
        tool="home",
        function="control",
        scope="write",
        description=("Control one device in Home Assistant by its id from home_list_devices: turn on or off, "
                     "brightness, the AC's temperature or mode, a fan's speed, or activate a scene."),
        args_model=HomeControlArgs,
        module="app.tools.home_tool",
        account="homeassistant",
        hosts=("account",),
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
               "charger", "power", "percent", "volume", "alarm", "wake me", "disturb", "dnd", "notifications", "focus"),
    "apps": ("window", "windows", "app", "apps", "program", "programs", "close", "quit", "exit", "kill", "shut down", "running", "switch to", "focus",
             "minimize", "in front", "tab", "tabs"),
    "files": ("file", "files", "folder", "folders", "downloads", "desktop", "documents", "pictures", "my resume", "cv",
              "pdf", "pdfs", "delete", "remove", "move", "copy", "rename", "organize", "organise", "sort", "clean",
              "tidy", "save a", "save it", "save the", "find", "zip", "photo", "photos", "wrote", "mention", "mentions",
              "mentioned", "notes", "doc", "docs", "spreadsheet", "presentation"),
    "typing": ("type", "write", "writing", "press", "key", "keys", "shortcut", "ctrl", "alt", "enter", "paste",
               "clipboard", "notepad", "word", "letter", "essay", "draft", "copy", "text", "report", "document",
               "research", "write up", "docx"),
    "messaging": ("whatsapp", "message", "messages", "msg", "send", "text", "texting", "tell", "reply", "wish", "ping",
                  "notification", "notifications", "miss", "missed", "unread", "new messages", "telegram", "teams"),
    "screen": ("on my screen", "on the screen", "on screen", "read the screen", "my screen", "click", "button", "fill",
               "form", "field", "screenshot", "you see", "showing", "sign in", "log in", "submit", "tick", "check box"),
    "speak": ("out loud", "aloud", "announce", "speak", "say it", "read it out"),
    "browser": ("browser", "web page", "webpage", "page", "website", "site", "form", "fill", "sign in", "log in", "login",
                "download", "tab", "tabs", "order", "book", "booking", "checkout", "cart", "ticket", "tickets", "apply",
                "register", "irctc", "amazon", "flipkart", "portal", "online", "submit"),
}
INTERNAL = "internal"  # never offered to the model; only Custodian's own code calls these


def groups_for(message: str | None) -> set[str]:
    text = " " + " ".join(re.findall(r"[a-z0-9]+", (message or "").lower())) + " "
    return {g for g, words in GROUPS.items() if any(f" {w} " in text for w in words)}


def offer(functions: list[ToolFunction], message: str | None, *, everything_if_unclear: bool = False) -> list[ToolFunction]:
    """`functions` narrowed to the groups the message is about. A message matching no group
    gets only the ungrouped ones (opening apps, sites and pages), unless `everything_if_unclear`."""
    functions = [f for f in functions if f.group != INTERNAL]
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


def _json_values(text: str) -> list | None:
    """Every JSON value in `text`, one after another ('[...]\n[...]', '{...}, {...}'), lists
    flattened one level; None when it isn't JSON."""
    decoder, values, at = json.JSONDecoder(strict=False), [], 0  # strict=False: a raw line break inside a string, as models write
    while at < len(text):
        while at < len(text) and text[at] in " \t\r\n,;":
            at += 1
        if at >= len(text):
            break
        try:
            value, at = decoder.raw_decode(text, at)
        except ValueError:
            return None
        values.extend(value if isinstance(value, list) else [value])
    return values or None


def _as_list(value: str) -> list:
    """'["a", "b"]', '["a"]\n["b"]', '[{...}]\n[{...}]', '"a" "b"', 'a; b' or 'a' -> a list."""
    text = value.strip()
    parsed = _json_values(text) if text[:1] in "[{\"" else None
    if parsed is not None:
        return [x for x in parsed if not (isinstance(x, str) and not x.strip())]
    quoted = re.findall(r'"([^"\n]+)"', text)
    if quoted:
        return quoted
    return [p.strip() for p in re.split(r"[\n;]+", text) if p.strip()]


def _list_fields(model: type) -> dict[str, int | None]:
    """The model's list fields, with their longest allowed length."""
    out = {}
    for name, field in model.model_fields.items():
        if get_origin(field.annotation) is list:
            limit = next((m.max_length for m in field.metadata if getattr(m, "max_length", None)), None)
            out[name] = limit
    return out


def fix_arguments(fn: ToolFunction, args: dict) -> dict:
    """Mend arguments a small model reliably gets slightly wrong, before validation: "Perfect by
    Ed Sheeran in spotify" is the song "Perfect by Ed Sheeran" on Spotify; a list sent as one
    string - '["iPhone camera"]\n["Pixel camera"]', or a single email address - is that list."""
    for name, limit in _list_fields(fn.args_model).items():
        if isinstance(args.get(name), str):
            items = _as_list(args[name])
            args = {**args, name: items[:limit] if limit else items}
    if fn.exposed_name == "pc_play" and isinstance(args.get("query"), str):
        found = _SERVICE_IN_QUERY.search(args["query"])
        if found:
            args = {**args, "query": args["query"][: found.start()].strip(), "service": found.group(1).lower()}
    return args
