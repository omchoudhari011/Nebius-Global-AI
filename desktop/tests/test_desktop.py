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
