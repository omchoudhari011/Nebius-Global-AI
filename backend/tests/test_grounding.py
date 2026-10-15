"""The hallucination guards (app/grounding.py): memories must be the user's own words, and
every reply is checked against what the turn really did, with one repair round."""

from types import SimpleNamespace

import pytest

from app import conversations, grounding, memory, orchestrator, scheduler, tasks
from app.model_backends import ModelResponse, ToolCallRequest, set_backend
from app.model_backends.base import ModelBackend

LUNCH = {"title": "Lunch with Ravi", "start": "2026-10-16T13:00"}


class Script(ModelBackend):
    """Plays back fixed responses in order (the last one repeats) and records every request."""

    name = "script"
    model_id = "script-1"

    def __init__(self, *responses: ModelResponse):
        self.responses = list(responses)
        self.requests: list[tuple[list[dict], list[dict] | None]] = []

    def generate(self, messages, tools=None):
        self.requests.append((list(messages), tools))  # a copy: the loop keeps appending to the list
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def say(text: str) -> ModelResponse:
    return ModelResponse(text, [], "script-1")


def call(name: str, args: dict) -> ModelResponse:
    return ModelResponse("", [ToolCallRequest(f"c-{name}", name, args)], "script-1")


# A false claim gets one repair round --------------------------------------------------------

def test_a_false_claim_is_rewritten_before_the_user_sees_it(user_id):
    backend = Script(
        call("calendar_create_event", LUNCH),  # denied: Calendar · write is off
        say("I've added Lunch with Ravi to your calendar for tomorrow at 1pm."),
        say("I couldn't add it: Calendar · write is switched off in Tools & Data."),
    )
    set_backend(backend)
    out = orchestrator.handle(user_id, "Add lunch with Ravi tomorrow at 1pm")

    assert out["reply"] == "I couldn't add it: Calendar · write is switched off in Tools & Data."
    checks = out["used"]["checks"]
    assert checks["status"] == "corrected"
    assert [(i["kind"], i["fixed"]) for i in checks["items"]] == [("unverified_event", True)]
    repair = backend.requests[2][0][-1]
    assert repair["role"] == "user" and repair["content"].startswith(grounding.REPAIR_HEADER)
    assert backend.requests[2][1] is not None  # tools are still offered in the repair round

    # The false draft never reaches the thread, or the history of the next turn.
    thread = conversations.get_conversation(user_id, out["conversation_id"])
    assert thread["messages"][-1]["content"] == out["reply"]
    recorder = Script(say("OK."))
    set_backend(recorder)
    orchestrator.handle(user_id, "thanks", conversation_id=out["conversation_id"])
    assert "I've added" not in str(recorder.requests[0][0])


def test_a_claim_that_survives_the_rewrite_is_flagged_in_the_reply(user_id):
    set_backend(Script(call("calendar_create_event", LUNCH), say("Done! I've added Lunch with Ravi for 1pm tomorrow.")))
    out = orchestrator.handle(user_id, "Add lunch with Ravi tomorrow at 1pm")

    assert out["reply"].startswith("Done! I've added Lunch with Ravi")
    assert out["reply"].endswith(
        "Custodian check: No event was added to your calendar: Calendar · write is switched off in Tools & Data."
    )
    assert out["used"]["checks"]["status"] == "flagged"
    stored = conversations.get_conversation(user_id, out["conversation_id"])["messages"][-1]
    assert stored["content"] == out["reply"]  # the note is saved with the reply, so later turns see it too
    assert stored["used"]["checks"]["status"] == "flagged"


def test_the_repair_round_can_still_do_the_step_it_skipped(user_id, grant):
    grant("calendar", "write")
    set_backend(
        Script(
            say("Done! I've added Lunch with Ravi for 1pm tomorrow."),  # never called the tool
            call("calendar_create_event", LUNCH),
            say("Added “Lunch with Ravi” on Fri 16 Oct, 13:00."),
        )
    )
    out = orchestrator.handle(user_id, "Add lunch with Ravi tomorrow at 1pm")
    assert out["used"]["checks"]["status"] == "corrected"
    assert [(t["function"], t["status"]) for t in out["used"]["tools"]] == [("create_event", "succeeded")]
    assert out["reply"] == "Added “Lunch with Ravi” on Fri 16 Oct, 13:00."


def test_promising_a_reminder_is_caught(user_id):
    set_backend(Script(say("Sure, I'll remind you tomorrow morning.")))
    out = orchestrator.handle(user_id, "remind me to call mum tomorrow")
    assert out["used"]["checks"]["status"] == "flagged"
    assert "No reminder was set: the reminders tool was never actually called" in out["reply"]


def test_a_reminder_that_was_set_is_not_flagged(user_id, grant):
    grant("reminders", "write")
    set_backend(Script(call("reminders_create_reminder", {"text": "Call mum", "at": "2099-01-01T18:00"}),
                       say("Done - I'll remind you to call mum at 18:00 on 1 Jan.")))
    out = orchestrator.handle(user_id, "remind me to call mum on 1 Jan 2099 at 6pm")
    assert out["used"]["tools"][0]["status"] == "succeeded"
    assert out["used"]["checks"]["status"] == "passed"


def test_promising_to_text_them_is_caught(user_id):
    set_backend(Script(say("Sure, I'll text you the list tonight.")))
    out = orchestrator.handle(user_id, "send me the shopping list")
    assert "Custodian can't send messages, emails or calls" in out["reply"]


def test_answering_from_a_blocked_tool_is_caught(user_id):
    set_backend(Script(call("calendar_list_events", {}), say("You have a team standup at 10:00 and lunch at 1pm.")))
    out = orchestrator.handle(user_id, "What's on my calendar today?")
    assert out["used"]["checks"]["status"] == "flagged"
    assert "couldn't read your calendar for this answer (Calendar · read is switched off" in out["reply"]


def test_describing_the_calendar_without_reading_it_is_caught(user_id, grant):
    grant("calendar", "read")
    set_backend(Script(say("You're free all day: nothing scheduled.")))
    out = orchestrator.handle(user_id, "Am I busy today?")
    assert [i["kind"] for i in out["used"]["checks"]["items"]] == ["unread_calendar"]


def test_a_made_up_memory_reference_is_caught(user_id):
    kept = memory.write(user_id, "Prefers green tea.", "preference")
    set_backend(Script(say(f"As memory #{kept + 40} says, you like coffee.")))
    out = orchestrator.handle(user_id, "what do I like to drink?")
    assert [i["kind"] for i in out["used"]["checks"]["items"]] == ["unknown_memory"]
    set_backend(Script(say(f"As memory #{kept} says, you prefer green tea.")))
    assert orchestrator.handle(user_id, "what do I like to drink?")["used"]["checks"]["status"] == "passed"


def test_the_offline_model_passes_its_own_checks(user_id, grant):
    out = orchestrator.handle(user_id, "What's on my calendar today?")  # denied, and the stub says so
    assert out["used"]["checks"]["status"] == "passed"
    grant("calendar", "write")
    out = orchestrator.handle(user_id, "Add lunch tomorrow at 1pm")
    assert out["used"]["checks"]["status"] == "passed" and out["reply"].startswith("Added")
    out = orchestrator.handle(user_id, "Remember that I prefer aisle seats")
    assert out["used"]["checks"]["status"] == "passed" and out["used"]["remembered"]


def test_every_prompt_carries_the_accuracy_rules(user_id):
    backend = Script(say("Hello!"))
    set_backend(backend)
    orchestrator.handle(user_id, "hi")
    assert grounding.ACCURACY_NOTE in backend.requests[0][0][0]["content"]


# Memories must be the user's own words -------------------------------------------------------

def test_a_memory_the_user_never_said_is_refused(user_id):
    backend = Script(
        call("remember", {"content": "Their sister Priya lives in Mumbai."}),  # invented names
        call("remember", {"content": "Their sister Meera lives in Pune."}),
        say("Got it."),
    )
    set_backend(backend)
    out = orchestrator.handle(user_id, "my sister is Meera and she lives in Pune")

    assert [m["content"] for m in memory.list_memories(user_id)] == ["Their sister Meera lives in Pune."]
    assert out["used"]["checks"]["memories_not_saved"] == 1
    refusal = backend.requests[1][0][-1]
    assert refusal["role"] == "tool" and "priya" in refusal["content"] and "mumbai" in refusal["content"]


def test_scheduled_runs_save_no_memories(user_id):
    set_backend(Script(call("remember", {"content": "Has a dentist appointment on Friday."}), say("Done.")))
    task = tasks.create_task(user_id, "nightly", "general", "0 21 * * *")
    scheduler.run_task(task["id"])
    assert memory.list_memories(user_id) == []


def test_claiming_to_remember_after_a_refusal_is_caught(user_id):
    set_backend(Script(call("remember", {"content": "Is vegetarian."}), say("Got it, I'll remember that you're vegetarian.")))
    out = orchestrator.handle(user_id, "I don't eat meat")
    assert out["used"]["checks"]["status"] == "flagged"
    assert "Nothing was saved to memory" in out["reply"]


@pytest.mark.parametrize(
    "said, saved, supported",
    [
        ("I prefer meetings before noon", "Prefers meetings before noon.", True),
        ("I lent my umbrella to Kavya", "Lent their umbrella to Kavya.", True),
        ("Hi, I am Om", "Their name is Om.", True),
        ("I like tea more than coffee", "User prefers tea over coffee.", True),
        ("my daughter's birthday is on 4 May", "Their daughter's birthday is on 4 May.", True),
        ("I'm flying to Goa next week", "Is flying to Goa on 22 October.", False),  # an invented date
        ("I lent my umbrella to Kavya", "Lent their umbrella to Priya.", False),  # the wrong name
        ("my car is a red Honda City", "Drives a blue Honda City bought in 2019.", False),
    ],
)
def test_memory_grounding(said, saved, supported):
    assert (grounding.unsupported_words(saved, [said]) == []) is supported


# Saying "I don't know" -------------------------------------------------------------------------

class Recorder(ModelBackend):
    name = "recorder"
    model_id = "recorder-1"

    def __init__(self):
        self.turns: list[str] = []

    def generate(self, messages, tools=None):
        self.turns.append(messages[-1]["content"])
        return ModelResponse("OK.", [], self.model_id)


def test_a_personal_question_with_nothing_on_file_invites_i_dont_know(user_id):
    backend = Recorder()
    set_backend(backend)
    orchestrator.handle(user_id, "what's my sister's name?")
    assert backend.turns[-1] == f"{grounding.NOTHING_SAVED_NOTE}\n\n{orchestrator.NOW_LABEL} what's my sister's name?"

    memory.write(user_id, "Their sister is called Meera.", "semantic")
    orchestrator.handle(user_id, "what's my sister's name?")
    assert backend.turns[-1] == "what's my sister's name?"  # a memory matches: no note

    orchestrator.handle(user_id, "add a dentist appointment for me")  # a request, not a question
    assert grounding.NOTHING_SAVED_NOTE not in backend.turns[-1]


@pytest.mark.parametrize(
    "reply, outcomes, flagged",
    [
        ("I've added Lunch with Ravi to your calendar for 1pm tomorrow.", [], True),
        ("Added “Team sync” on Sat 3 Oct, 15:00.", [], True),
        ("Got it, I've jotted that down.", [], True),
        ("Would you like me to add it to your calendar?", [], False),
        ("If you tell me the time, I'll add it to your calendar.", [], False),
        ("I found your note about the plumber that you saved on Monday.", [], False),
        ("Added “Team sync” on Sat 3 Oct, 15:00.", [("calendar", "create_event", "succeeded")], False),
        ("I can't send notifications, but I can add a calendar event.", [], False),
    ],
)
def test_claim_detection(reply, outcomes, flagged):
    evidence = grounding.Evidence(
        message="hi", outcomes=[SimpleNamespace(tool=t, function=f, status=s, error=None) for t, f, s in outcomes]
    )
    assert bool(grounding.review(reply, evidence)) is flagged


def test_a_reminder_with_a_time_is_not_mistaken_for_a_calendar_event(user_id, grant):
    grant("reminders", "write")
    set_backend(Script(call("reminders_create_reminder", {"text": "Stretch", "at": "2099-01-01T23:35"}),
                       say("I've set a reminder to stretch my back for 2 minutes from now (23:35).")))
    out = orchestrator.handle(user_id, "remind me to stretch my back in 2 minutes")
    assert out["used"]["checks"]["status"] == "passed"
