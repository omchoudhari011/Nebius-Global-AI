import pytest

from app import db, memory, orchestrator, skills
from app.model_backends import ModelResponse, ToolCallRequest, set_backend
from app.model_backends.base import ModelBackend


def test_denied_then_allowed(user_id, grant):
    first = orchestrator.handle(user_id, "What's on my calendar today?")
    assert first["used"]["tools"][0]["status"] == "denied"
    assert "Tools & Data" in first["reply"]
    assert first["used"]["skill"]["name"] == "calendar"

    grant("calendar", "read")
    second = orchestrator.handle(user_id, "What's on my calendar today?", conversation_id=first["conversation_id"])
    assert second["used"]["tools"][0]["status"] == "succeeded"
    assert second["conversation_id"] == first["conversation_id"]


def test_a_short_follow_up_stays_on_the_topic_before_it(user_id):
    first = orchestrator.handle(user_id, "can you start the song on youtube")
    assert first["used"]["skill"]["name"] == "pc"
    # misheard; on its own it matches no skill, and the general one has no PC tools to act with
    after = orchestrator.handle(user_id, "to a silent", conversation_id=first["conversation_id"])
    assert after["used"]["skill"]["name"] == "pc"
    fresh = orchestrator.handle(user_id, "to a silent")  # a new chat: nothing to follow on from
    assert fresh["used"]["skill"]["name"] == skills.DEFAULT_SKILL
    longer = orchestrator.handle(user_id, "tell me a fun fact about the moon please", conversation_id=first["conversation_id"])
    assert longer["used"]["skill"]["name"] == skills.DEFAULT_SKILL  # a real new question picks for itself


def test_remember_saves_memory_linked_to_the_message(user_id):
    out = orchestrator.handle(user_id, "Remember that I prefer aisle seats")
    saved = out["used"]["remembered"]
    assert saved and saved[0]["type"] == "preference"
    stored = memory.get(user_id, saved[0]["id"])
    with db.connect() as conn:
        role = conn.execute("SELECT role FROM messages WHERE id = ?", (stored["source_message_id"],)).fetchone()["role"]
    assert role == "user"

    later = orchestrator.handle(user_id, "Which seats do I prefer?")
    assert saved[0]["id"] in [m["id"] for m in later["used"]["memories"]]
    assert "aisle" in later["reply"]


def test_secrets_are_not_remembered(user_id):
    out = orchestrator.handle(user_id, "Remember that my bank password is hunter2")
    assert out["used"]["remembered"] == []
    assert memory.list_memories(user_id) == []


def test_messages_are_encrypted_at_rest(user_id):
    orchestrator.handle(user_id, "Hello there, my blood type is O negative")
    with db.connect() as conn:
        for row in conn.execute("SELECT content FROM messages").fetchall():
            assert b"blood type" not in bytes(row["content"])


def test_other_users_conversation_is_not_found(user_id):
    with db.connect() as conn:
        other = conn.execute("INSERT INTO users (email) VALUES ('o@example.com')").lastrowid
        conv = conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (other,)).lastrowid
    with pytest.raises(orchestrator.ConversationNotFound):
        orchestrator.handle(user_id, "hi", conversation_id=conv)


class LoopingBackend(ModelBackend):
    """Proposes a tool call every time it's offered tools."""

    name = "looping"
    model_id = "looping-1"

    def __init__(self):
        self.calls = 0

    def generate(self, messages, tools=None):
        self.calls += 1
        if tools:
            return ModelResponse("", [ToolCallRequest(f"c{self.calls}", "notes_read_notes", {})], self.model_id)
        return ModelResponse("Final answer.", [], self.model_id)


def test_tool_rounds_are_capped(user_id):
    backend = LoopingBackend()
    set_backend(backend)
    out = orchestrator.handle(user_id, "find my notes")
    assert backend.calls == orchestrator.MAX_TOOL_ROUNDS + 1
    assert len(out["used"]["tools"]) == orchestrator.MAX_TOOL_ROUNDS
    # Every notes call was denied and the last answer doesn't say so; with no round left to
    # repair it, the reply carries a note instead (app/grounding.py).
    assert out["reply"].startswith("Final answer.")
    assert "Custodian check: Custodian couldn't read your notes" in out["reply"]
    assert out["used"]["checks"]["status"] == "flagged"


class EmptyBackend(ModelBackend):
    name = "empty"
    model_id = "empty-1"

    def generate(self, messages, tools=None):
        return ModelResponse("", [], self.model_id)


def test_empty_model_reply_gets_a_fallback(user_id):
    set_backend(EmptyBackend())
    assert orchestrator.handle(user_id, "hello")["reply"] == orchestrator.EMPTY_REPLY


def test_history_is_sent_to_the_model(user_id):
    seen = []

    class Recorder(ModelBackend):
        name, model_id = "rec", "rec-1"

        def generate(self, messages, tools=None):
            seen.append(messages)
            return ModelResponse("ok", [], self.model_id)

    set_backend(Recorder())
    first = orchestrator.handle(user_id, "first message")
    orchestrator.handle(user_id, "second message", conversation_id=first["conversation_id"])
    roles = [(m["role"], m["content"]) for m in seen[-1][1:]]
    assert roles == [("user", "first message"), ("assistant", "ok"), ("user", "second message")]


def test_task_with_rolled_back_skill_fails(user_id):
    skill = skills.load_skill("daily-summary")
    with db.connect() as conn:
        conn.execute("UPDATE skills SET active = 0 WHERE id = ?", (skill.id,))
    with pytest.raises(orchestrator.SkillUnavailable):
        orchestrator.handle(
            user_id, None, trigger="daily-summary",
            task=orchestrator.TaskContext(task_id=1, name="daily-summary", skill_id=skill.id),
        )


@pytest.mark.parametrize(
    "message, name",
    [
        ("HI MY SELF OM CHOUDHARI", "Om Choudhari"),
        ("my name is Om choudhari \nmake the spelling correct", "Om Choudhari"),
        ("Hello, my name's Ada Lovelace and I like maths", "Ada Lovelace"),
        ("Myself Priya", "Priya"),
        ("I'm called McKenzie", "McKenzie"),
        # however the greeting is spelled
        ("hyy my self om choudhari", "Om Choudhari"),
        ("hii myself om choudhari", "Om Choudhari"),
        ("heyy my self om", "Om"),
        ("hello there, myself Om Choudhari", "Om Choudhari"),
        ("good morning my self om choudhari", "Om Choudhari"),
        ("Namaste! Myself Priya Sharma", "Priya Sharma"),
        ("Hi, I'm Om", "Om"),
        ("hi im om choudhari", "Om Choudhari"),
        ("hello! I am Priya Sharma.", "Priya Sharma"),
        ("good morning, I'm Kavya!", "Kavya"),
    ],
)
def test_name_is_read_from_the_message(message, name):
    assert orchestrator._name_from(message) == name


@pytest.mark.parametrize(
    "message",
    [
        "do you remember my name",
        "my name is not important",
        "I hurt myself yesterday",
        "what is my name?",
        "hyy how are you",
        "hey myself is fine",
        "hi there, I did it myself",
        "hi I'm tired",
        "hey I'm back",
        "hi I'm looking for help",
        "hello I'm new here",
        "hi I'm a student",
        "hi I'm from Pune",
        "I'm Om",  # no greeting: too easily "I'm done"; left to the model
        "hello I'm Om, what can you do?",
    ],
)
def test_ordinary_messages_have_no_name(message):
    assert orchestrator._name_from(message) is None


class Recorder(ModelBackend):
    """Never calls `remember`; keeps the system prompt and user turn of each request."""

    name = "recorder"
    model_id = "recorder-1"

    def __init__(self):
        self.prompts: list[str] = []
        self.turns: list[str] = []

    def generate(self, messages, tools=None):
        self.prompts.append(messages[0]["content"])
        self.turns.append(messages[-1]["content"])
        return ModelResponse("OK.", [], self.model_id)


@pytest.mark.parametrize("question", ["whats my name", "What's my name?", "who am i?", "do you know my name"])
def test_a_name_given_in_one_chat_is_known_in_a_new_one(user_id, question):
    backend = Recorder()
    set_backend(backend)
    first = orchestrator.handle(user_id, "hyy my self om choudhari")  # the model never calls `remember`
    assert [m["content"] for m in first["used"]["remembered"]] == ["Their name is Om Choudhari."]

    later = orchestrator.handle(user_id, question)  # a new chat
    assert [(m["content"], m["matched"]) for m in later["used"]["memories"]] == [("Their name is Om Choudhari.", True)]
    assert "Their name is Om Choudhari." in backend.prompts[-1]
    assert orchestrator.grounding.NOTHING_SAVED_NOTE not in backend.turns[-1]


def test_a_name_said_in_passing_is_recalled_even_without_a_memory(user_id):
    set_backend(Recorder())
    first = orchestrator.handle(user_id, "hyy my self om choudhari")
    memory.delete(user_id, first["used"]["remembered"][0]["id"])  # say the memory was deleted
    later = orchestrator.handle(user_id, "whats my name")
    assert [e["content"] for e in later["used"]["earlier"]] == ["hyy my self om choudhari"]


class SilentBackend(ModelBackend):
    """A model that never calls `remember`, like small local models often don't."""

    name = "silent"
    model_id = "silent-1"

    def generate(self, messages, tools=None):
        return ModelResponse("Nice to meet you.", [], self.model_id)


def test_stated_name_is_saved_and_a_correction_replaces_it(user_id):
    set_backend(SilentBackend())
    first = orchestrator.handle(user_id, "hi my self om chodhari")
    assert [m["content"] for m in first["used"]["remembered"]] == ["Their name is Om Chodhari."]

    second = orchestrator.handle(user_id, "My name is Om Choudhari", conversation_id=first["conversation_id"])
    assert [m["content"] for m in second["used"]["remembered"]] == ["Their name is Om Choudhari."]
    assert [m["content"] for m in memory.list_memories(user_id)] == ["Their name is Om Choudhari."]

    later = orchestrator.handle(user_id, "do you remember my name")
    assert "Their name is Om Choudhari." in [m["content"] for m in later["used"]["memories"]]


def test_explicit_remember_is_saved_when_the_model_skips_it(user_id):
    set_backend(SilentBackend())
    out = orchestrator.handle(user_id, "Remember that I prefer window seats on my flights")
    assert [(m["type"], m["content"]) for m in out["used"]["remembered"]] == [
        ("preference", "They prefer window seats on their flights.")
    ]
    assert orchestrator.handle(user_id, "remember that my password is hunter2")["used"]["remembered"] == []


def test_model_saved_memory_is_not_duplicated(user_id):
    out = orchestrator.handle(user_id, "Remember that I prefer aisle seats")  # the stub calls `remember`
    assert len(out["used"]["remembered"]) == 1
    assert len(memory.list_memories(user_id)) == 1
