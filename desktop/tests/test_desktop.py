"""The desktop app's actions, confirmations and voice helpers (nothing here presses keys,
clicks or speaks)."""

import queue
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from custodian_desktop import actions
from custodian_desktop.__main__ import App
from custodian_desktop.voice import FRAME, RATE, Listener, Speaker, to_wav

PROTECTED = [r"C:\Windows", r"C:\Program Files"]


def test_files_can_be_listed_found_read_written_moved_and_recycled(tmp_path):
    (tmp_path / "invoice-march.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "notes.txt").write_text("call the plumber", encoding="utf-8")
    listing = actions.run("list_files", {"path": str(tmp_path), "pattern": "*.txt"}, PROTECTED)
    assert [i["name"] for i in listing["items"]] == ["notes.txt"]
    assert actions.run("find_files", {"query": "invoice", "folder": str(tmp_path)}, PROTECTED)["matches"] == [
        str(tmp_path / "invoice-march.pdf")]
    assert actions.run("read_file", {"path": str(tmp_path / "notes.txt")}, PROTECTED)["text"] == "call the plumber"
    with pytest.raises(actions.ActionError, match="isn't a text file"):
        (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02")
        actions.run("read_file", {"path": str(tmp_path / "bin.dat")}, PROTECTED)

    target = tmp_path / "sub" / "todo.txt"
    actions.run("write_file", {"path": str(target), "content": "buy milk"}, PROTECTED)
    with pytest.raises(actions.ActionError, match="already exists"):
        actions.run("write_file", {"path": str(target), "content": "x"}, PROTECTED)
    actions.run("write_file", {"path": str(target), "content": "buy eggs", "overwrite": True}, PROTECTED)
    assert target.read_text(encoding="utf-8") == "buy eggs"

    moved = actions.run("move_file", {"source": str(target), "destination": str(tmp_path)}, PROTECTED)
    assert Path(moved["to"]) == tmp_path / "todo.txt" and not target.exists()
    actions.run("delete_file", {"path": str(tmp_path / "todo.txt")}, PROTECTED)
    assert not (tmp_path / "todo.txt").exists()  # in the Recycle Bin, not gone for good


def test_protected_folders_and_whole_drives_are_never_changed(tmp_path):
    for function, args in [
        ("write_file", {"path": r"C:\Windows\evil.txt", "content": "x"}),
        ("delete_file", {"path": r"C:\Program Files\App\app.exe"}),
        ("move_file", {"source": str(tmp_path), "destination": r"C:\Windows\x"}),
    ]:
        with pytest.raises(actions.ActionError, match="never changes"):
            actions.run(function, args, PROTECTED)
    with pytest.raises(actions.ActionError, match="won't delete"):
        actions.run("delete_file", {"path": "C:\\"}, PROTECTED)
    with pytest.raises(actions.ActionError, match="won't delete"):
        actions.run("delete_file", {"path": "~"}, PROTECTED)


def test_a_folder_is_organized_by_type_without_overwriting(tmp_path):
    for name in ["trip.jpg", "bill.pdf", "song.mp3", "setup.exe", "notes.xyz", "half.crdownload"]:
        (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "bill.pdf").write_text("older", encoding="utf-8")
    result = actions.run("organize_folder", {"path": str(tmp_path)}, PROTECTED)
    assert result["moved"] == {"Images": 1, "Documents": 1, "Music": 1, "Installers": 1, "Other": 1}
    assert (tmp_path / "Documents" / "bill (2).pdf").exists() and (tmp_path / "Documents" / "bill.pdf").read_text() == "older"
    assert (tmp_path / "half.crdownload").exists()  # a download still in progress stays put
    with pytest.raises(actions.ActionError, match="never changes"):
        actions.run("organize_folder", {"path": r"C:\Windows"}, PROTECTED)


def test_files_and_folders_can_be_copied_and_created(tmp_path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    actions.run("create_folder", {"path": str(tmp_path / "Trips" / "2026")}, PROTECTED)
    copied = actions.run("copy_file", {"source": str(tmp_path / "a.txt"), "destination": str(tmp_path / "Trips")}, PROTECTED)
    assert Path(copied["to"]).read_text(encoding="utf-8") == "hi" and (tmp_path / "a.txt").exists()
    with pytest.raises(actions.ActionError, match="already exists"):
        actions.run("copy_file", {"source": str(tmp_path / "a.txt"), "destination": str(tmp_path / "Trips")}, PROTECTED)
    with pytest.raises(actions.ActionError, match="never changes"):
        actions.run("create_folder", {"path": r"C:\Windows\x"}, PROTECTED)


def test_sites_searches_and_pages_open_in_the_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(actions.webbrowser, "open", lambda url: opened.append(url))
    assert actions.run("open", {"target": "youtube"}, PROTECTED)["kind"] == "web page"
    actions.run("go_to", {"query": "Jägermeister Wikipedia"}, PROTECTED)
    actions.run("search_site", {"site": "amazon", "query": "wireless earbuds"}, PROTECTED)
    assert opened == ["https://www.youtube.com", "https://duckduckgo.com/?q=%5CJ%C3%A4germeister%20Wikipedia",
                      "https://www.amazon.in/s?k=wireless+earbuds"]


def test_play_opens_the_first_youtube_video(monkeypatch):
    import httpx

    opened = []
    monkeypatch.setattr(actions.webbrowser, "open", lambda url: opened.append(url))
    page = 'x"videoRenderer":{"videoId":"z5y8Clp_TdE","thumbnail":{},"title":{"runs":[{"text":"KALYANI \\u0026 more"}]}'
    monkeypatch.setattr(httpx, "get", lambda *a, **k: type("R", (), {"text": page})())
    result = actions.run("play", {"query": "kalyani"}, PROTECTED)
    assert result == {"service": "youtube", "playing": "KALYANI & more", "url": "https://www.youtube.com/watch?v=z5y8Clp_TdE",
                      "started": True}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: type("R", (), {"text": "nothing"})())
    assert actions.run("play", {"query": "zzzz"}, PROTECTED)["started"] is False  # shows the results instead


def test_the_app_meant_is_picked_not_a_file_named_after_it(monkeypatch):
    windows = [("whatsapp_bot.py - AI ML workshop - Visual Studio Code", "Code.exe", 1, 11),
               ("WhatsApp", "WhatsApp.Root.exe", 2, 22), ("Inbox - Gmail - Google Chrome", "chrome.exe", 3, 33),
               ("", "msedgewebview2.exe", 4, 44), ("Edge stuff - Microsoft Edge", "msedge.exe", 5, 55)]
    monkeypatch.setattr(actions, "_windows", lambda: windows)
    assert actions.best_window("whatsapp")[1] == "WhatsApp.Root.exe"
    assert actions.best_window("gmail")[1] == "chrome.exe"
    assert actions.app_process("whatsapp") == "WhatsApp.Root.exe"
    assert actions.app_process("edge") == "msedge.exe"  # not the web view other apps use
    assert actions.app_process("vs code") == "Code.exe"
    assert actions.best_window("telegram") is None
    assert "WhatsApp" in actions.describe("close_window", {"title": "whatsapp"}).split("\u201c")[1]


def test_windows_itself_is_never_closed(monkeypatch):
    monkeypatch.setattr(actions, "_windows", lambda: [("File Explorer", "explorer.exe", 9, 99)])
    with pytest.raises(actions.ActionError, match="won't close"):
        actions.run("close_app", {"name": "explorer"}, PROTECTED)


def test_a_timer_rings_through_the_app(monkeypatch):
    import threading

    rang = threading.Event()
    monkeypatch.setattr(actions, "on_timer", lambda label: rang.set() if label == "tea" else None)
    assert actions.run("timer", {"seconds": 1, "label": "tea"}, PROTECTED)["seconds"] == 1
    assert rang.wait(5)


def _undo(result: dict) -> None:
    """Run the undo step an action reported, as the server would."""
    step = result["undo"]
    actions.run(step["function"], step["args"], PROTECTED)


def test_moves_sorts_new_folders_copies_and_overwrites_can_be_undone(tmp_path, monkeypatch):
    recycled = []
    import send2trash

    monkeypatch.setattr(send2trash, "send2trash", lambda p: (recycled.append(p), Path(p).unlink()))
    monkeypatch.setattr(actions, "UNDO_DIR", tmp_path / "undo")
    (tmp_path / "a.txt").write_text("first", encoding="utf-8")
    moved = actions.run("move_file", {"source": str(tmp_path / "a.txt"), "destination": str(tmp_path / "b.txt")}, PROTECTED)
    _undo(moved)
    assert (tmp_path / "a.txt").exists() and not (tmp_path / "b.txt").exists()

    written = actions.run("write_file", {"path": str(tmp_path / "a.txt"), "content": "second", "overwrite": True}, PROTECTED)
    assert written["undo"]["function"] == "restore_backup"
    _undo(written)
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "first"

    folder = actions.run("create_folder", {"path": str(tmp_path / "Trips")}, PROTECTED)
    _undo(folder)
    assert not (tmp_path / "Trips").exists()
    assert "undo" not in actions.run("create_folder", {"path": str(tmp_path)}, PROTECTED)  # it was already there

    copied = actions.run("copy_file", {"source": str(tmp_path / "a.txt"), "destination": str(tmp_path / "c.txt")}, PROTECTED)
    _undo(copied)
    assert recycled == [str(tmp_path / "c.txt")]

    for name in ["trip.jpg", "bill.pdf"]:
        (tmp_path / name).write_text("x", encoding="utf-8")
    sorted_ = actions.run("organize_folder", {"path": str(tmp_path)}, PROTECTED)
    assert sorted_["undo"]["label"] == f"put the 3 sorted files back in {tmp_path.name}"
    _undo(sorted_)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt", "bill.pdf", "trip.jpg", "undo"]


def test_undo_steps_respect_the_protected_folders(tmp_path):
    with pytest.raises(actions.ActionError, match="never changes"):
        actions.run("undo_moves", {"moves": [[str(tmp_path / "x"), r"C:\Windows\x"]]}, PROTECTED)
    with pytest.raises(actions.ActionError, match="never changes"):
        actions.run("recycle_created", {"path": r"C:\Program Files\App"}, PROTECTED)
    with pytest.raises(actions.ActionError, match="saved copy is gone"):
        actions.run("restore_backup", {"backup": str(tmp_path / "elsewhere.txt"), "path": str(tmp_path / "a.txt")}, PROTECTED)


def test_volume_is_set_exactly_and_reports_how_to_put_it_back(monkeypatch):
    class Speakers:
        level, muted = 0.7, 1

        def GetMasterVolumeLevelScalar(self):  # noqa: N802 - the COM method's name
            return self.level

        def SetMasterVolumeLevelScalar(self, value, _context):  # noqa: N802
            self.level = value

        def GetMute(self):  # noqa: N802
            return self.muted

        def SetMute(self, value, _context):  # noqa: N802
            self.muted = value

    speakers = Speakers()
    monkeypatch.setattr(actions, "_speakers", lambda: speakers)
    result = actions.run("set_volume", {"level": 30}, PROTECTED)
    assert result == {"volume": 30, "undo": {"function": "set_volume", "args": {"level": 70},
                                             "label": "put the volume back to 70%"}}
    assert speakers.level == 0.3 and speakers.muted == 0  # asking for a level unmutes


def test_alarms_ring_at_the_next_time_and_survive_a_restart(tmp_path, monkeypatch):
    import datetime as dt

    monkeypatch.setattr(actions, "ALARMS_FILE", tmp_path / "alarms.json")
    soon = (dt.datetime.now() + dt.timedelta(minutes=5)).strftime("%H:%M")
    made = actions.run("alarm", {"time": soon, "label": "Stand up"}, PROTECTED)
    assert made["rings_at"].startswith(soon) and made["undo"]["function"] == "cancel_timer"
    assert [a["label"] for a in actions._load_alarms()] == ["Stand up"]
    earlier = (dt.datetime.now() - dt.timedelta(minutes=1)).strftime("%H:%M")
    assert actions.run("alarm", {"time": earlier}, PROTECTED)["rings_at"].endswith("tomorrow")

    for clock in list(actions._clocks.values()):  # "restart": the timers are gone, the file isn't
        clock.cancel()
    actions._clocks.clear()
    assert actions.restore_alarms() == 2
    _undo(made)
    assert [a["label"] for a in actions._load_alarms()] == ["Alarm"]
    with pytest.raises(actions.ActionError, match="already rang or was cancelled"):
        _undo(made)
    for clock in list(actions._clocks.values()):
        clock.cancel()


def test_a_routine_list_is_shown_whole_in_one_confirmation():
    shown = actions.describe("confirm_plan", {"title": "Run “Good night”", "steps": ["Close chrome", "Lock my pc"]})
    assert shown == "\u2022 Close chrome\n\u2022 Lock my pc"
    assert actions.run("confirm_plan", {"title": "x", "steps": ["a"]}, PROTECTED) == {"approved": True, "steps": 1}


def test_whatsapp_rows_and_names_are_read_strictly():
    assert actions._row_title("3 unread messages Tanishka 1:46 pm projects.csv") == "Tanishka"
    assert actions._row_title("Mum Yesterday Dinner at 8?") == "Mum"
    assert actions._same_person("tanishka", "Tanishka Sharma")
    assert not actions._same_person("mum", "Mumbai Trip")
    assert not actions._same_person("Tanishka", "+91 734 929 0925")


@pytest.mark.parametrize("said, chat, fits", [
    ("Mronal", "Mrunal Patil", True),  # how speech recognition wrote "Mrunal"
    ("Abhi Choudhury", "Abhi Chaudhari", True),
    ("abhi choudhary", "Abhi Choudhury", True),
    ("tanishka", "Tanishka Sharma", True),
    ("Abhi", "Abhishek", False),  # another person
    ("mum", "Mumbai Trip", False),
    ("Tanishka", "+91 734 929 0925", False),
])
def test_a_chat_name_fits_however_the_name_is_spelled_but_not_another_name(said, chat, fits):
    assert (actions.name_score(said, chat) >= actions.NAME_MATCH) is fits


class _WhatsApp:
    """WhatsApp's chat list and search, as _open_chat sees them through UI Automation."""

    def __init__(self, chats):
        self.chats, self.query, self.opened, self.searched = chats, "", None, []

    def first(self, name=None, control=None, *, contains=False):
        if control == "Edit" and name == actions.COMPOSER_PREFIX.strip():
            return SimpleNamespace(CurrentName=f"Type a message to {self.opened}") if self.opened else None
        return "search box" if control == "Edit" else None

    def set_value(self, element, text):
        self.query = text
        self.searched.append(text)

    def rows(self):  # WhatsApp finds a chat by its exact letters
        return [SimpleNamespace(CurrentName=f"{c} 1:46 pm see you", chat=c) for c in self.chats if self.query.lower() in c.lower()]


class COMError(Exception):  # what comtypes raises when a UI Automation call fails
    pass


def _gone():
    return COMError(actions.UIA_E_ELEMENTNOTAVAILABLE, "An event was unable to invoke any of the subscribers", (None,) * 5)


class _Redrawn:
    """A row WhatsApp replaced after it was found: reading it fails."""

    @property
    def CurrentName(self):  # noqa: N802 - UI Automation's name
        raise _gone()


class _Redrawing(_WhatsApp):
    """WhatsApp redrawing its list as each search's results come in: the first read after typing fails."""

    redrawing = False

    def set_value(self, element, text):
        super().set_value(element, text)
        self.redrawing = True

    def rows(self):
        if self.redrawing:
            self.redrawing = False
            return [_Redrawn()]
        return super().rows()


@pytest.fixture
def whatsapp(monkeypatch):
    def make(*chats, kind=_WhatsApp):
        app = kind(list(chats))
        monkeypatch.setattr(actions, "_click_center", lambda row: setattr(app, "opened", row.chat))
        monkeypatch.setattr(actions.time, "sleep", lambda seconds: None)
        monkeypatch.setattr(actions, "SEARCH_SECONDS", 0.05)
        return app
    return make


def test_a_misheard_or_misspelled_name_still_opens_the_one_chat_that_fits(whatsapp):
    app = whatsapp("Mrunal Patil", "Mr Sharma", "Tanishka")
    assert actions._open_chat(app, "Mronal") == "Mrunal Patil"  # "Mronal" lists nothing; "Mr" lists Mrunal
    assert app.searched == ["Mronal", "Mr"]
    app = whatsapp("Abhi Chaudhari", "Abhishek")
    assert actions._open_chat(app, "Abhi Choudhury") == "Abhi Chaudhari"
    assert actions._open_chat(whatsapp("Tanishka Sharma"), "tanishka") == "Tanishka Sharma"  # exact, as before


def test_when_two_chats_fit_as_well_it_asks_which_and_opens_neither(whatsapp):
    app = whatsapp("Rahul Sharma", "Rahul Verma")
    with pytest.raises(actions.ActionError, match="closest are 'Rahul Sharma', 'Rahul Verma'.*nothing was sent"):
        actions._open_chat(app, "Rahool")
    assert app.opened is None
    assert actions._open_chat(whatsapp("Zeenat"), "Zebra") is None  # nothing close: "no chat called ..."


def test_whatsapp_redrawing_its_list_mid_search_does_not_stop_it(whatsapp):
    app = whatsapp("Mrunal Patil", "Mr Sharma", kind=_Redrawing)
    assert actions._open_chat(app, "Mronal") == "Mrunal Patil"  # was: COMError "...invoke any of the subscribers"
    assert app.searched == ["Mronal", "Mr"]


def test_a_chat_redrawn_just_before_its_click_is_searched_for_again(whatsapp, monkeypatch):
    app = whatsapp("Tanishka Sharma")
    clicks = []

    def click(row):
        clicks.append(row.chat)
        if len(clicks) == 1:
            raise _gone()
        app.opened = row.chat

    monkeypatch.setattr(actions, "_click_center", click)
    assert actions._open_chat(app, "tanishka") == "Tanishka Sharma"
    assert clicks == ["Tanishka Sharma", "Tanishka Sharma"]


def test_a_message_said_to_a_misheard_name_shows_the_real_chat_before_sending(monkeypatch):
    sent = _fake_whatsapp(monkeypatch, "Mrunal Patil")
    asked = []
    monkeypatch.setattr(actions, "confirm_hook", lambda title, details: asked.append(details) or True)
    assert actions.run("send_message", {"contact": "Mronal", "text": "hi"}, PROTECTED)["sent_to"] == "Mrunal Patil"
    assert asked == ["To: Mrunal Patil\n\nhi"] and sent == ["hi"]


def _fake_whatsapp(monkeypatch, chat):
    sent = []
    monkeypatch.setattr(actions, "_whatsapp_window", lambda: 1)
    monkeypatch.setattr(actions, "refocus", lambda hwnd: None)
    monkeypatch.setattr(actions, "_open_chat", lambda ui, contact, nothing: chat)
    monkeypatch.setattr(actions, "_paste_into_focus", lambda text: sent.append(text))
    monkeypatch.setattr(actions, "_keystroke", lambda *keys: None)

    class UI:
        def __init__(self, hwnd):
            pass

        def first(self, *a, **k):
            return type("E", (), {"CurrentName": f"Type a message to {chat}", "SetFocus": lambda self: None})()

    monkeypatch.setattr(actions, "_Uia", UI)
    return sent


def test_a_message_goes_only_to_the_named_chat_and_only_when_allowed(monkeypatch):
    sent = _fake_whatsapp(monkeypatch, "Tanishka")
    asked = []
    monkeypatch.setattr(actions, "confirm_hook", lambda title, details: asked.append(details) or True)
    assert actions.run("send_message", {"contact": "tanishka", "text": "on my way"}, PROTECTED)["sent_to"] == "Tanishka"
    assert sent == ["on my way"] and asked == ["To: Tanishka\n\non my way"]

    monkeypatch.setattr(actions, "confirm_hook", lambda title, details: False)
    with pytest.raises(actions.ActionError, match="declined"):
        actions.run("send_message", {"contact": "Tanishka", "text": "second"}, PROTECTED)
    monkeypatch.setattr(actions, "confirm_hook", None)  # nobody to ask: never sent
    with pytest.raises(actions.ActionError, match="declined"):
        actions.run("send_message", {"contact": "Tanishka", "text": "third"}, PROTECTED)
    assert sent == ["on my way"]


def test_a_different_chat_is_never_messaged(monkeypatch):
    sent = _fake_whatsapp(monkeypatch, "+91 734 929 0925")
    monkeypatch.setattr(actions, "confirm_hook", lambda title, details: True)
    with pytest.raises(actions.ActionError, match="not 'Tanishka'; nothing was sent"):
        actions.run("send_message", {"contact": "Tanishka", "text": "hi"}, PROTECTED)
    assert sent == []


def test_when_whatsapp_keeps_redrawing_the_error_is_said_in_words_and_nothing_is_sent(monkeypatch):
    sent = _fake_whatsapp(monkeypatch, "Tanishka")
    monkeypatch.setattr(actions, "confirm_hook", lambda title, details: True)

    def redrawn(ui, contact, nothing):
        raise _gone()

    monkeypatch.setattr(actions, "_open_chat", redrawn)
    with pytest.raises(actions.ActionError) as error:
        actions.run("send_message", {"contact": "Tanishka", "text": "hi"}, PROTECTED)
    assert "redrew its window" in str(error.value) and "nothing was sent" in str(error.value)
    assert "subscribers" not in str(error.value) and sent == []


def _fake_call(monkeypatch, chat, *, started=True):
    """WhatsApp with `chat` open, for a call: what gets pressed, and what the user is asked."""
    _fake_whatsapp(monkeypatch, chat)
    pressed, asked = [], []
    monkeypatch.setattr(actions._Uia, "press", lambda self, element: pressed.append(element), raising=False)
    monkeypatch.setattr(actions, "_call_control", lambda ui, video: "Video call" if video else "Voice call")
    monkeypatch.setattr(actions, "_call_started", lambda ui, before: started)
    monkeypatch.setattr(actions, "_windows", lambda: [])
    monkeypatch.setattr(actions, "confirm_hook", lambda title, details: asked.append((title, details)) or True)
    return pressed, asked


def test_a_call_goes_only_to_the_chat_found_and_only_when_allowed(monkeypatch):
    pressed, asked = _fake_call(monkeypatch, "Piyush Sharma")
    assert actions.run("call", {"contact": "piyush"}, PROTECTED) == {"calling": "Piyush Sharma", "app": "WhatsApp",
                                                                     "kind": "voice call"}
    assert asked == [("Start a WhatsApp voice call", "To: Piyush Sharma")] and pressed == ["Voice call"]
    assert actions.run("call", {"contact": "Piyush", "video": True}, PROTECTED)["kind"] == "video call"
    assert pressed == ["Voice call", "Video call"]

    monkeypatch.setattr(actions, "confirm_hook", lambda title, details: False)
    with pytest.raises(actions.ActionError, match="declined"):
        actions.run("call", {"contact": "Piyush"}, PROTECTED)
    monkeypatch.setattr(actions, "confirm_hook", None)  # nobody to ask: never called
    with pytest.raises(actions.ActionError, match="declined"):
        actions.run("call", {"contact": "Piyush"}, PROTECTED)
    assert pressed == ["Voice call", "Video call"]


def test_a_different_chat_is_never_called_and_a_missing_button_is_said(monkeypatch):
    pressed, asked = _fake_call(monkeypatch, "+91 734 929 0925")
    with pytest.raises(actions.ActionError, match="not 'Piyush'; no call was made"):
        actions.run("call", {"contact": "Piyush"}, PROTECTED)
    assert asked == [] and pressed == []

    pressed, _asked = _fake_call(monkeypatch, "Family Group")
    monkeypatch.setattr(actions, "_call_control", lambda ui, video: None)
    monkeypatch.setattr(actions._Uia, "names", lambda self, control: ["Search", "Menu"], raising=False)
    with pytest.raises(actions.ActionError, match=r"no voice call button in the chat with Family Group \(a group.*no call was made"):
        actions.run("call", {"contact": "family group"}, PROTECTED)
    # a WhatsApp whose buttons are named otherwise: the error says what it has, to fix the names here
    monkeypatch.setattr(actions._Uia, "names", lambda self, control: ["Video call", "Search"], raising=False)
    with pytest.raises(actions.ActionError, match=r"its buttons about calls: 'Video call'\); no call was made"):
        actions.run("call", {"contact": "family group"}, PROTECTED)
    assert pressed == []


def test_a_call_whose_screen_never_shows_says_so(monkeypatch):
    pressed, _asked = _fake_call(monkeypatch, "Mum", started=False)
    result = actions.run("call", {"contact": "mum"}, PROTECTED)
    assert pressed == ["Voice call"] and result["call_screen_seen"] is False and "didn't show" in result["note"]


class _Header:
    """An open chat's header, as _call_control sees it: buttons, and a menu that Expand shows."""

    def __init__(self, buttons, menu=()):
        self.buttons, self.menu, self.expanded = list(buttons), list(menu), False

    def first(self, name=None, control=None, *, contains=False):
        shown = self.buttons + (self.menu if self.expanded else [])
        return next((n for n in shown if (name.lower() in n.lower() if contains else n == name)), None)

    def expand(self, element):
        self.expanded = bool(self.menu)
        return self.expanded


def test_the_call_button_is_whatsapps_own_never_a_note_in_the_chat(monkeypatch):
    monkeypatch.setattr(actions.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(actions, "_keystroke", lambda *keys: None)
    assert actions._call_control(_Header(["Video call", "Voice call"]), video=False) == "Voice call"
    assert actions._call_control(_Header(["Video call", "Voice call"]), video=True) == "Video call"
    assert actions._call_control(_Header(["Missed voice call at 10:02", "Voice call"]), video=False) == "Voice call"
    menu = _Header(["Call"], menu=["Voice call", "Video call"])  # one "Call" button with a menu of both
    assert actions._call_control(menu, video=True) == "Video call" and menu.expanded
    assert actions._call_control(_Header(["Call"]), video=False) is None  # not a menu: never pressed blind
    assert actions._call_control(_Header(["Search", "Menu"]), video=False) is None


def test_the_mouse_in_a_corner_stops_an_action_with_a_clear_message(monkeypatch):
    class FailSafeException(Exception):
        pass

    def boom(**_):
        raise FailSafeException("corner")

    monkeypatch.setitem(actions.ACTIONS, "press_keys", boom)
    with pytest.raises(actions.ActionError, match="emergency stop"):
        actions.run("press_keys", {"keys": "enter"}, PROTECTED)


def test_folder_names_open_the_folder_not_an_app(monkeypatch, tmp_path):
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "OneDrive" / "Documents").mkdir(parents=True)
    monkeypatch.setattr(actions.Path, "home", staticmethod(lambda: tmp_path))
    assert actions.known_folder("Downloads") == tmp_path / "Downloads"
    assert actions.known_folder("my pcs download folder") == tmp_path / "Downloads"
    assert actions.known_folder("the documents folder") == tmp_path / "OneDrive" / "Documents"  # moved by OneDrive
    assert actions.known_folder("notepad") is None and actions.known_folder("downloads manager") is None
    opened = []
    monkeypatch.setattr(actions.os, "startfile", lambda p: opened.append(p), raising=False)
    assert actions.run("open", {"target": "Downloads"}, PROTECTED) == {"opened": str(tmp_path / "Downloads"), "kind": "folder"}
    assert opened == [str(tmp_path / "Downloads")]


def test_unknown_actions_and_keys_are_refused():
    with pytest.raises(actions.ActionError, match="doesn't know"):
        actions.run("format_disk", {}, PROTECTED)
    with pytest.raises(actions.ActionError, match="unknown key"):
        actions.press_keys("ctrl+notakey")


def test_the_confirmation_shows_exactly_what_will_happen():
    assert "Move to the Recycle Bin:\nC:/x/report.docx" == actions.describe("delete_file", {"path": "C:/x/report.docx"})
    assert "hello world" in actions.describe("type_text", {"text": "hello world"})


class _Client:
    def __init__(self):
        self.results = []

    def send_result(self, action_id, ok, result=None, error=None):
        self.results.append((action_id, ok, result, error))


class _Window:
    def __init__(self, answer):
        self.answer, self.asked, self.shown = answer, [], []

    def confirm(self, title, details):
        self.asked.append((title, details))
        return self.answer

    def show(self, title, **kwargs):
        self.shown.append((title, kwargs))


def _app(answer: bool, tmp_path) -> App:
    app = App.__new__(App)
    app.client, app.window = _Client(), _Window(answer)
    app.settings = type("S", (), {"protected": PROTECTED})()
    return app


def test_a_declined_action_is_not_carried_out(tmp_path):
    app = _app(False, tmp_path)
    target = tmp_path / "keep.txt"
    target.write_text("keep me", encoding="utf-8")
    app.handle_action({"id": 7, "function": "delete_file", "args": {"path": str(target)}, "confirm": True,
                       "description": "Move a file or folder to the Recycle Bin (the user confirms)."})
    assert target.exists()
    assert app.window.asked[0][0] == "Move a file or folder to the Recycle Bin"
    assert app.client.results == [(7, False, None, "The user declined this on the PC.")]


def test_an_allowed_action_runs_and_reports_back(tmp_path):
    app = _app(True, tmp_path)
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    app.handle_action({"id": 8, "function": "list_files", "args": {"path": str(tmp_path)}, "confirm": False})
    (action_id, ok, result, error), = app.client.results
    assert action_id == 8 and ok and result["items"][0]["name"] == "a.txt" and app.window.asked == []


def test_an_action_allowed_in_the_app_runs_without_asking_and_says_so(tmp_path, monkeypatch):
    app = _app(False, tmp_path)  # the PC dialog would say no: it must not be shown at all
    done = []
    monkeypatch.setitem(actions.ACTIONS, "delete_file", lambda path, protected: done.append(path) or {"recycled": path})
    app.handle_action({"id": 9, "function": "delete_file", "args": {"path": "~/old.txt"}, "confirm": False,
                       "approved": "remote", "description": "Move a file or folder to the Recycle Bin (the user confirms)."})
    assert done == ["~/old.txt"] and app.window.asked == []
    assert app.window.shown[0][0] == "Allowed from your phone"
    assert app.client.results[0][:2] == (9, True)


def test_a_remote_ok_for_a_message_never_carries_over_to_the_next_one(tmp_path, monkeypatch):
    app = _app(False, tmp_path)
    hooks = []
    monkeypatch.setitem(actions.ACTIONS, "send_message", lambda **a: hooks.append(actions.confirm_hook) or {"sent_to": "Mum"})
    app.handle_action({"id": 10, "function": "send_message", "args": {"contact": "Mum", "text": "hi"}, "confirm": False,
                       "approved": "remote"})
    assert hooks[0]("Send a WhatsApp message", "To: Mum") is True
    assert actions.confirm_hook is None  # the next send asks again (or, with nobody to ask, never sends)


def test_a_call_is_asked_about_once_naming_the_chat_it_found(tmp_path, monkeypatch):
    app = _app(True, tmp_path)
    monkeypatch.setitem(actions.ACTIONS, "call", lambda **a: actions.confirm_hook("Start a WhatsApp voice call", "To: Piyush Sharma")
                        and {"calling": "Piyush Sharma"})
    app.handle_action({"id": 11, "function": "call", "args": {"contact": "piyush"}, "confirm": True,
                       "description": "Start a WhatsApp voice or video call to one chat by name."})
    assert app.window.asked == [("Start a WhatsApp voice call", "To: Piyush Sharma")]  # not a second, generic one
    assert app.client.results == [(11, True, {"calling": "Piyush Sharma"}, None)]


class _Announcer:
    """The window, speaker, listener and client an announcement touches."""

    def __init__(self):
        self.shown, self.said, self.triggered = [], [], 0
        self.paused = False

    def announce(self, title, body, button=None, on_button=None, **_):
        self.shown.append((title, body, button))
        self.on_button = on_button

    def show(self, *args, **kwargs):
        self.shown.append(args)

    def say(self, text):
        self.said.append(text)

    def pause(self, _paused):
        pass

    def speaking(self, _speaking):
        pass

    def trigger(self):
        self.triggered += 1

    def transcribe(self, _wav):
        return self.heard


def _announcing_app(monkeypatch) -> tuple[App, _Announcer]:
    fake = _Announcer()
    app = App.__new__(App)
    app.window = app.speaker = app.listener = app.client = fake
    app.settings = type("S", (), {"speak_announcements": True, "protected": PROTECTED, "voice_id": False})()
    app.paused, app.awaiting, app.enrolling = False, None, None
    opened = []
    monkeypatch.setattr(actions, "open", lambda target: opened.append(target))
    fake.opened = opened
    return app, fake


def test_a_meeting_heads_up_is_said_and_yes_opens_its_link(monkeypatch):
    app, fake = _announcing_app(monkeypatch)
    app.handle_action({"id": 3, "function": "announce", "reply": False, "args": {
        "kind": "meeting", "title": "“Design review” starts in 10 minutes", "body": "https://meet.google.com/abc",
        "say": "“Design review” starts in 10 minutes. Shall I open the meeting link?", "link": "https://meet.google.com/abc"}})
    assert fake.shown[0][2] == "Open the link" and fake.said == ["“Design review” starts in 10 minutes. Shall I open the meeting link?"]
    assert fake.triggered == 1  # listening for the answer, no wake word needed
    fake.heard = "Yes please"
    app.on_speech(b"wav")
    assert fake.opened == ["https://meet.google.com/abc"]
    fake.on_button()  # pressing the button too doesn't open it twice
    assert fake.opened == ["https://meet.google.com/abc"]


def test_no_to_the_offer_opens_nothing_and_isnt_sent_as_a_chat(monkeypatch):
    app, fake = _announcing_app(monkeypatch)
    app.announce({"kind": "meeting", "title": "Standup in 10 minutes", "say": "...", "link": "https://zoom.us/j/1"})
    fake.heard = "no, not now"
    app.on_speech(b"wav")
    assert fake.opened == [] and app.awaiting is None


def test_a_reminder_is_said_without_any_offer(monkeypatch):
    app, fake = _announcing_app(monkeypatch)
    app.announce({"kind": "reminder", "title": "Call mum", "say": "Reminder: Call mum."})
    assert fake.said == ["Reminder: Call mum."] and fake.triggered == 0 and fake.shown[0][2] is None
    app.settings.speak_announcements = False
    app.announce({"kind": "reminder", "title": "Pay rent", "say": "Reminder: Pay rent."})
    assert fake.said == ["Reminder: Call mum."]  # shown, not spoken


def test_speech_is_cleaned_before_it_is_spoken():
    text = "Canberra is the capital.\nSource: https://en.wikipedia.org/wiki/Canberra\n\nCustodian check: **no** event was added."
    assert Speaker.clean(text) == "Canberra is the capital. Note: no event was added."


def test_recording_stops_after_a_second_of_quiet():
    states = []
    listener = Listener(lambda wav: None, states.append, wake_word=False)
    frames: queue.Queue = queue.Queue()
    loud = (np.sin(np.linspace(0, 200, FRAME)) * 8000).astype(np.int16)
    quiet = np.zeros(FRAME, dtype=np.int16)
    for frame in [loud] * 15 + [quiet] * 20 + [loud] * 50:  # 1.2 s of speech, 1.6 s quiet, then more
        frames.put(frame)
    audio = listener._record(frames, floor=100.0)
    assert audio is not None and len(audio) <= FRAME * 30  # stopped at the pause, not at the later noise
    assert to_wav(audio)[:4] == b"RIFF" and len(audio) / RATE >= 1.0


def test_silence_after_the_wake_word_records_nothing():
    states = []
    listener = Listener(lambda wav: None, states.append, wake_word=False)
    frames: queue.Queue = queue.Queue()
    for _ in range(100):
        frames.put(np.zeros(FRAME, dtype=np.int16))
    assert listener._record(frames, floor=100.0) is None and states[-1] == "didn't catch that"


def test_enter_denies_and_allow_cannot_be_reached_by_keyboard_or_a_stray_click():
    import threading

    from custodian_desktop import ui

    window = ui.Window(on_quit=lambda: None)
    answers, seen = [], {}

    def widgets(root):
        for child in root.winfo_children():
            yield child
            yield from widgets(child)

    def poke():
        dialogs = [w for w in widgets(window.root) if isinstance(w, ui.tk.Toplevel) and w.title() == "Custodian wants to…"]
        if not dialogs:
            window.root.after(100, poke)
            return
        allow = next(w for w in widgets(dialogs[0]) if isinstance(w, ui.tk.Button) and w.cget("text") == "Allow once")
        seen.update(state=str(allow.cget("state")), takefocus=str(allow.cget("takefocus")))
        dialogs[0].event_generate("<Return>")  # a stray Enter

    worker = threading.Thread(target=lambda: (answers.append(window.confirm("Close a window", "details")), window.quit()))
    worker.start()
    window.root.after(200, poke)
    window.mainloop()
    worker.join(timeout=10)
    window.root.destroy()
    assert answers == [False]
    assert seen == {"state": "disabled", "takefocus": "0"}  # not yet clickable, never focusable


def test_the_real_dialog_denies_when_nobody_answers(monkeypatch):
    import threading

    from custodian_desktop import ui

    monkeypatch.setattr(ui, "CONFIRM_SECONDS", 2)
    window = ui.Window(on_quit=lambda: None)
    answers = []
    worker = threading.Thread(target=lambda: (answers.append(window.confirm("Take a screenshot", "details")), window.quit()))
    worker.start()
    window.mainloop()  # the dialog appears, nobody clicks, it times out
    worker.join(timeout=10)
    window.root.destroy()
    assert answers == [False]
