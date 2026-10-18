"""The desktop app's actions, confirmations and voice helpers (nothing here presses keys,
clicks or speaks)."""

import queue
from pathlib import Path

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


def test_whatsapp_rows_and_names_are_read_strictly():
    assert actions._row_title("3 unread messages Tanishka 1:46 pm projects.csv") == "Tanishka"
    assert actions._row_title("Mum Yesterday Dinner at 8?") == "Mum"
    assert actions._same_person("tanishka", "Tanishka Sharma")
    assert not actions._same_person("mum", "Mumbai Trip")
    assert not actions._same_person("Tanishka", "+91 734 929 0925")


def _fake_whatsapp(monkeypatch, chat):
    sent = []
    monkeypatch.setattr(actions, "_whatsapp_window", lambda: 1)
    monkeypatch.setattr(actions, "refocus", lambda hwnd: None)
    monkeypatch.setattr(actions, "_open_chat", lambda ui, contact: chat)
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
        self.answer, self.asked = answer, []

    def confirm(self, title, details):
        self.asked.append((title, details))
        return self.answer


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
