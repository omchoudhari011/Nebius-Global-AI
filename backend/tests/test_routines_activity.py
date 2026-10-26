"""Routines (one phrase, several PC actions, one approval) and the activity log with undo."""

from __future__ import annotations

import threading

import pytest

from app import activity, desktop, orchestrator, permissions, routines
from app.tools import registry, sandbox
from app.tools.schemas import SaveRoutineArgs


@pytest.fixture
def pc(user_id, tool_ids):
    """A desktop app that does what it's asked and records it. `answers` sets a reply per function."""
    desktop.reset()
    seen: list[dict] = []
    answers: dict = {}
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            action = desktop.next_action(user_id, "Test PC", wait=0.3)
            if not action:
                continue
            seen.append(action)
            answer = answers.get(action["function"], {"done": action["function"]})
            if isinstance(answer, Exception):
                desktop.finish(user_id, action["id"], False, error=str(answer))
            else:
                desktop.finish(user_id, action["id"], True, answer(action) if callable(answer) else answer)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    desktop.touch(user_id, "Test PC")
    for scope in ("read", "write"):
        permissions.set_granted(user_id, tool_ids["pc"], scope, True)
    yield type("PC", (), {"seen": seen, "answers": answers})
    stop.set()
    thread.join(timeout=2)
    desktop.reset()


# Saving -----------------------------------------------------------------------------------------

def test_steps_in_plain_words_become_exact_tool_calls():
    checked = SaveRoutineArgs.model_validate({"name": "Work mode", "steps": [
        "open vs code", "open github.com", "do not disturb on", "play lofi beats on spotify", "volume 30", "close chrome",
        "alarm at 7am", "say time to focus"]})
    assert [(s["function"], s["args"]) for s in checked.parsed] == [
        ("pc_open", {"target": "vs code"}), ("pc_open", {"target": "github.com"}), ("pc_do_not_disturb", {"on": True}),
        ("pc_play", {"query": "lofi beats", "service": "spotify"}), ("pc_set_volume", {"level": 30}),
        ("pc_close_app", {"name": "chrome"}), ("pc_alarm", {"time": "07:00", "label": "Alarm"}),
        ("pc_say", {"text": "time to focus"}),
    ]


@pytest.mark.parametrize("step", ["delete my downloads", "send a whatsapp to mum", "type hello", "do my taxes"])
def test_steps_a_routine_must_never_do_are_refused(step):
    with pytest.raises(ValueError, match="aren't actions a routine can do"):
        SaveRoutineArgs.model_validate({"name": "Bad", "steps": ["open notepad", step]})


def test_parsed_steps_cant_be_smuggled_in():
    checked = SaveRoutineArgs.model_validate({"name": "X", "steps": ["open notepad"],
                                              "parsed": [{"function": "pc_delete_file", "args": {"path": "~"}}]})
    assert checked.parsed == [{"function": "pc_open", "args": {"target": "notepad"}, "said": "open notepad"}]


def test_the_routine_tool_saves_lists_and_replaces_by_name(user_id, tool_ids):
    permissions.set_granted(user_id, tool_ids["routines"], "write", True)
    message_id = _message(user_id)
    first = sandbox.execute(user_id, "routines_save_routine", {"name": "Good night", "steps": ["dark mode", "lock my pc"]},
                            message_id=message_id, allowed=None)
    assert first.status == "succeeded" and first.result["replaced"] is False
    again = sandbox.execute(user_id, "routines_save_routine", {"name": "good NIGHT", "steps": ["lock my pc"]},
                            message_id=message_id, allowed=None)
    assert again.result["replaced"] is True
    assert [r["steps"] for r in routines.list_for(user_id)] == [["lock my pc"]]
    bad = sandbox.execute(user_id, "routines_save_routine", {"name": "Oops", "steps": ["delete everything"]},
                          message_id=message_id, allowed=None)
    assert bad.status == "rejected" and "aren't actions a routine can do" in bad.error


def _message(user_id: int) -> int:
    from app import db
    from app.crypto import encrypt

    with db.connect() as conn:
        conv = conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (user_id,)).lastrowid
        return conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', ?)",
                            (conv, encrypt(""))).lastrowid


# Matching and running ---------------------------------------------------------------------------

@pytest.mark.parametrize("said, runs", [
    ("work mode", True), ("Work mode.", True), ("start work mode", True), ("run my work mode routine", True),
    ("work mode on", True), ("hey jarvis, work mode please", True), ("let's focus", True),
    ("add yoga to my work mode routine", False), ("what does work mode do?", False), ("work", False),
])
def test_a_routine_runs_when_its_name_or_a_phrase_is_said(user_id, said, runs):
    routines.save(user_id, "Work mode", ["open vs code"], ["let's focus"])
    assert (routines.match(user_id, said) is not None) is runs


def test_a_routine_runs_every_step_in_order_without_a_dialog_when_none_needs_one(user_id, pc):
    routines.save(user_id, "Work mode", ["open vs code", "volume 30", "do not disturb on"])
    pc.answers["set_volume"] = {"volume": 30}
    pc.answers["do_not_disturb"] = {"do_not_disturb": True}
    out = orchestrator.handle(user_id, "work mode")
    assert [(a["function"], a["confirm"]) for a in pc.seen] == [("open", False), ("set_volume", False), ("do_not_disturb", False)]
    assert out["reply"].startswith("“Work mode” is done.") and "Volume set to 30%." in out["reply"]
    assert out["used"]["checks"]["status"] == "passed"
    assert routines.list_for(user_id)[0]["last_run_at"] is not None


def test_one_approval_covers_every_step_that_would_ask(user_id, pc):
    routines.save(user_id, "Good night", ["close chrome", "close spotify", "lock my pc"])
    out = orchestrator.handle(user_id, "good night")
    plan, *steps = pc.seen
    assert plan["function"] == "confirm_plan" and plan["confirm"] is True
    assert plan["args"]["steps"] == ["Close chrome", "Close spotify", "Lock my pc"]
    assert [(a["function"], a["confirm"]) for a in steps] == [("close_app", False), ("close_app", False), ("lock_screen", False)]
    assert "is done" in out["reply"]


def test_a_declined_approval_runs_nothing(user_id, pc):
    routines.save(user_id, "Good night", ["close chrome", "lock my pc"])
    pc.answers["confirm_plan"] = RuntimeError("The user declined this on the PC.")
    out = orchestrator.handle(user_id, "good night")
    assert [a["function"] for a in pc.seen] == ["confirm_plan"]
    assert out["reply"] == "Okay - I didn't run “Good night”."


def test_a_failed_step_is_reported_and_the_rest_still_run(user_id, pc):
    routines.save(user_id, "Focus", ["open vs code", "volume 20"])
    pc.answers["open"] = RuntimeError("VS Code isn't installed")
    pc.answers["set_volume"] = {"volume": 20}
    out = orchestrator.handle(user_id, "focus")
    assert "1 of 2 steps worked" in out["reply"] and "VS Code isn't installed" in out["reply"]


def test_routine_steps_still_need_their_switches(user_id, tool_ids, pc):
    routines.save(user_id, "Focus", ["volume 20"])
    permissions.set_granted(user_id, tool_ids["pc"], "write", False)
    out = orchestrator.handle(user_id, "focus")
    assert pc.seen == [] and "PC · write" in out["reply"]


# Undo and activity ------------------------------------------------------------------------------

def test_an_undo_record_is_kept_out_of_the_models_view_and_undo_that_puts_it_back(user_id, pc):
    pc.answers["set_volume"] = lambda a: ({"volume": a["args"]["level"], "undo": {
        "function": "set_volume", "args": {"level": 70}, "label": "put the volume back to 70%"}}
        if a["args"]["level"] == 30 else {"volume": a["args"]["level"]})
    first = orchestrator.handle(user_id, "volume 30")
    assert first["reply"] == "Volume set to 30%."
    item = activity.recent(user_id)[0]
    assert item["can_undo"] and item["undo"] == "put the volume back to 70%" and "undo" not in item["result"]

    undone = orchestrator.handle(user_id, "undo that")  # right after an action: Custodian's undo, not Ctrl+Z
    assert pc.seen[-1]["function"] == "set_volume" and pc.seen[-1]["args"] == {"level": 70}
    assert pc.seen[-1]["confirm"] is False
    assert undone["reply"] == "Done - I put the volume back to 70%."
    assert activity.recent(user_id, 5)[-1]["undone_at"] is not None or any(a["undone_at"] for a in activity.recent(user_id))
    with pytest.raises(activity.NothingToUndo):
        activity.undo(user_id)


def test_undo_that_presses_ctrl_z_when_custodian_did_nothing_recently(user_id, pc):
    orchestrator.handle(user_id, "undo that")
    assert [(a["function"], a["args"]) for a in pc.seen] == [("press_keys", {"keys": "ctrl+z"})]


def test_only_known_undo_steps_are_ever_stored():
    assert activity.clean_undo({"function": "delete_file", "args": {"path": "~"}}) is None
    assert activity.clean_undo({"function": "restore_deleted", "args": {"path": "C:/a.txt"}, "label": "restore a.txt"}) == {
        "function": "restore_deleted", "args": {"path": "C:/a.txt"}, "label": "restore a.txt"}


def test_search_finds_what_was_sent_to_someone(user_id, pc):
    msg = _message(user_id)
    sandbox.execute(user_id, "pc_send_message", {"contact": "Tanishka", "text": "running late, 10 mins"}, message_id=msg, allowed=None)
    sandbox.execute(user_id, "pc_open", {"target": "notepad"}, message_id=msg, allowed=None)
    found = activity.search(user_id, "what did I send Tanishka yesterday", days=2)
    assert [f["function"] for f in found] == ["send_message"] and found[0]["args"]["text"] == "running late, 10 mins"


def test_the_activity_api_lists_and_undoes(client, pc):
    from app import auth

    user = auth.ensure_owner()
    pc.answers["set_theme"] = {"theme": "dark", "undo": {"function": "set_theme", "args": {"mode": "light"},
                                                        "label": "switch back to light mode"}}
    client.post("/chat", json={"message": "dark mode"})
    listed = client.get("/activity").json()
    assert listed[0]["function"] == "set_theme" and listed[0]["can_undo"]
    done = client.post(f"/activity/{listed[0]['id']}/undo")
    assert done.status_code == 200 and done.json()["undone"] == "switch back to light mode"
    assert client.post(f"/activity/{listed[0]['id']}/undo").status_code == 409
    assert user


def test_the_routines_api_checks_steps(client):
    bad = client.post("/routines", json={"name": "Bad", "steps": ["format my disk"]})
    assert bad.status_code == 400 and "aren't actions a routine can do" in bad.json()["detail"]
    made = client.post("/routines", json={"name": "Morning", "steps": ["open gmail", "brightness 80"]})
    assert made.status_code == 201
    assert [r["name"] for r in client.get("/routines").json()] == ["Morning"]
    assert client.delete(f"/routines/{made.json()['id']}").status_code == 204


def test_internal_plan_function_is_never_offered():
    offered = registry.offer(registry.for_scopes(None), "do anything", everything_if_unclear=True)
    assert "pc_confirm_plan" not in {f.exposed_name for f in offered}


@pytest.mark.parametrize("said", ["What did I miss?", "Any new WhatsApp messages?", "did I get any notifications",
                                  "show my unread messages"])
def test_what_did_i_miss_reaches_the_notifications_on_the_pc(said):
    from app import approvals, skills

    assert skills.pick_skill_name(said) == "pc"
    offered = {f.exposed_name for f in registry.offer(registry.for_scopes(None), said)}
    assert "pc_notifications" in offered
    assert registry.find("pc_notifications").confirm  # other people's messages: only after you allow it
    assert approvals.describe("notifications", {"hours": 24, "app": "WhatsApp"}) == (
        "Send your WhatsApp notifications from the last 24 hours, and the unread counts, to Custodian")


@pytest.mark.parametrize("said", ["open whatsapp and call to piyush", "call Mrunal on WhatsApp", "video call Tanishka",
                                  "call piyush"])
def test_a_call_reaches_whatsapp_on_the_pc_and_is_always_asked_about(said):
    from app import approvals, skills

    assert skills.pick_skill_name(said) == "pc"
    offered = {f.exposed_name for f in registry.offer(registry.for_scopes(None), said)}
    assert "pc_call" in offered
    assert registry.find("pc_call").confirm  # it reaches someone: only after you allow it
    assert approvals.describe("call", {"contact": "Piyush", "video": True}) == "Start a WhatsApp video call\nTo: Piyush"


def test_a_button_to_click_is_not_a_call_to_someone_guessed():
    offered = {f.exposed_name for f in registry.offer(registry.for_scopes(None), "click voice call in whatsapp")}
    assert "pc_call" not in offered and {"pc_read_screen", "pc_click_element"} <= offered
