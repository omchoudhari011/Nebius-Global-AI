"""Lifetime memory: every saved memory reaches every chat, and what the user said in earlier
conversations is recalled by keyword (app/history_search.py)."""

import pytest

from app import conversations, db, history_search, memory, orchestrator
from app.crypto import encrypt
from app.model_backends import ModelResponse, set_backend
from app.model_backends.base import ModelBackend


class RecordingBackend(ModelBackend):
    """Replies with a fixed text and keeps every system prompt and user turn it was sent."""

    name = "recording"
    model_id = "recording-1"

    def __init__(self, reply: str = "OK."):
        self.reply = reply
        self.prompts: list[str] = []
        self.turns: list[str] = []

    def generate(self, messages, tools=None):
        self.prompts.append(messages[0]["content"])
        self.turns.append(next(m["content"] for m in reversed(messages) if m["role"] == "user"))
        return ModelResponse(self.reply, [], self.model_id)


@pytest.fixture
def model():
    backend = RecordingBackend()
    set_backend(backend)
    return backend


def _earlier(out: dict) -> list[str]:
    return [e["content"] for e in out["used"]["earlier"]]


def test_a_new_chat_recalls_what_was_said_in_an_earlier_one(user_id, model):
    first = orchestrator.handle(user_id, "I parked the car on level 3 of the Phoenix mall")
    out = orchestrator.handle(user_id, "Where did I leave the car at Phoenix?")

    assert out["conversation_id"] != first["conversation_id"]
    assert _earlier(out) == ["I parked the car on level 3 of the Phoenix mall"]
    assert out["used"]["earlier"][0]["conversation_id"] == first["conversation_id"]
    # quoted in the user's turn, ahead of the new message - never in the system prompt
    turn = model.turns[-1]
    assert turn.startswith(orchestrator.EARLIER_HEADER)
    assert ": “I parked the car on level 3 of the Phoenix mall”" in turn
    assert turn.endswith(f"{orchestrator.NOW_LABEL} Where did I leave the car at Phoenix?")
    assert "Phoenix" not in model.prompts[-1]
    assert orchestrator.GUARD_NOTE in model.prompts[-1]

    thread = conversations.get_conversation(user_id, out["conversation_id"])
    assert thread["messages"][-1]["used"]["earlier"] == out["used"]["earlier"]


def test_only_the_users_own_words_are_recalled(user_id):
    set_backend(RecordingBackend("Your name is Om Chodhari."))  # a reply with a mistake in it
    orchestrator.handle(user_id, "my name is Om Choudhari")
    backend = RecordingBackend()
    set_backend(backend)

    out = orchestrator.handle(user_id, "what is my name")
    assert _earlier(out) == ["my name is Om Choudhari"]
    assert "Chodhari" not in backend.prompts[-1] + backend.turns[-1]


def test_messages_already_in_the_prompt_are_not_repeated(user_id, model):
    first = orchestrator.handle(user_id, "my boiler is a Vaillant")
    same_chat = orchestrator.handle(user_id, "which boiler do I have?", conversation_id=first["conversation_id"])
    assert _earlier(same_chat) == []  # already in this chat's history, and the question itself is skipped
    assert model.turns[-1] == "which boiler do I have?"  # nothing quoted, so the message goes as typed


def test_the_offline_model_reads_the_same_quote_format():
    from app.model_backends import stub_backend

    assert stub_backend.NOW_LABEL == orchestrator.NOW_LABEL
    block = orchestrator._format_earlier(
        [{"id": 1, "created_at": "2026-10-03 07:41:25", "content": 'he said “hi”\nthen   left'}], "Asia/Kolkata"
    )
    assert block.splitlines()[1] == '- 2026-10-03: “he said "hi" then left”'  # one line, its own quotes straightened
    assert stub_backend._EARLIER_LINE.findall(block) == [("2026-10-03", 'he said "hi" then left')]


def test_old_messages_of_a_long_chat_are_recalled(user_id, model, monkeypatch):
    monkeypatch.setattr(orchestrator, "HISTORY_LIMIT", 2)
    first = orchestrator.handle(user_id, "the boiler service is booked with Arun")
    cid = first["conversation_id"]
    orchestrator.handle(user_id, "unrelated chat about tea", conversation_id=cid)
    later = orchestrator.handle(user_id, "who services the boiler?", conversation_id=cid)
    assert _earlier(later) == ["the boiler service is booked with Arun"]


def test_repeated_words_are_recalled_once_as_the_newest_copy(user_id, model):
    orchestrator.handle(user_id, "my locker is number 118")
    second = orchestrator.handle(user_id, "my locker is number 118")
    out = orchestrator.handle(user_id, "which locker is mine?")
    assert [e["id"] for e in out["used"]["earlier"]] == [
        m["id"] for m in conversations.get_conversation(user_id, second["conversation_id"])["messages"] if m["role"] == "user"
    ]


def test_a_deleted_conversation_is_forgotten(user_id, model):
    first = orchestrator.handle(user_id, "my locker is number 118")
    orchestrator.handle(user_id, "which locker is mine?")  # the index now holds the locker message
    assert conversations.delete_conversation(user_id, first["conversation_id"])

    out = orchestrator.handle(user_id, "which locker is mine?")
    assert _earlier(out) == []
    assert history_search._index.conn.execute("SELECT COUNT(*) FROM said WHERE said MATCH '118'").fetchone()[0] == 0


def test_earlier_questions_are_not_recalled(user_id, model):
    for asked in ["whats my name", "do you remember my name", "What is my name?"]:
        orchestrator.handle(user_id, asked)
    orchestrator.handle(user_id, "I want you to know my name - Om Choudhari")
    out = orchestrator.handle(user_id, "tell me my name")
    assert _earlier(out) == ["I want you to know my name - Om Choudhari"]


@pytest.mark.parametrize(
    "text, question",
    [
        ("whats my name", True),
        ("Do you remember my name", True),
        ("where did I park?", True),
        ("Is it raining in Pune", True),
        ("Do remember that I like tea", False),
        ("Is allergic to peanuts", False),
        ("my name is Om Choudhari", False),
    ],
)
def test_question_detection(text, question):
    assert history_search.looks_like_question(text) is question


def test_messages_saved_by_another_process_are_found(user_id, model):
    orchestrator.handle(user_id, "planning the garden this spring")  # builds this process's index
    with db.connect() as conn:  # e.g. `python -m app.cli run-task`, writing to the same file
        cid = conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (user_id,)).lastrowid
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', ?)",
            (cid, encrypt("the spare key is under the blue pot")),
        )
    out = orchestrator.handle(user_id, "where is the spare key?")
    assert _earlier(out) == ["the spare key is under the blue pot"]


def test_an_index_from_a_replaced_database_is_thrown_away(user_id, model):
    orchestrator.handle(user_id, "the gate code changed today")
    orchestrator.handle(user_id, "what changed with the gate?")
    stale = history_search._index
    stale.conn.execute("UPDATE said SET content = 'the gate is open' WHERE content = 'the gate code changed today'")

    out = orchestrator.handle(user_id, "anything about the gate?")
    assert "the gate is open" not in _earlier(out)  # the stored message says something else
    assert history_search._index is not stale
    again = orchestrator.handle(user_id, "anything else about the gate?")
    assert "the gate code changed today" in _earlier(again)


def test_other_users_messages_are_never_recalled(user_id, model):
    with db.connect() as conn:
        other = conn.execute("INSERT INTO users (email) VALUES ('other@example.com')").lastrowid
        cid = conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (other,)).lastrowid
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', ?)",
            (cid, encrypt("my secret project is called Bluebird")),
        )
    out = orchestrator.handle(user_id, "what is the Bluebird project?")
    assert _earlier(out) == []


def test_every_memory_reaches_every_chat_with_matches_first(user_id, model):
    name = memory.write(user_id, "Their name is Om Choudhari.", "semantic")
    peanuts = memory.write(user_id, "Is allergic to peanuts.", "semantic")
    pune = memory.write(user_id, "Lives in Pune.", "semantic")

    out = orchestrator.handle(user_id, "what should I cook tonight?")  # shares no keyword with any memory
    assert {m["id"] for m in out["used"]["memories"]} == {name, peanuts, pune}
    assert not any(m["matched"] for m in out["used"]["memories"])
    assert "Their name is Om Choudhari." in model.prompts[-1]

    out = orchestrator.handle(user_id, "who am I?")  # no searchable word, but a name question finds the name
    assert [m["id"] for m in out["used"]["memories"] if m["matched"]] == [name]

    out = orchestrator.handle(user_id, "what am I allergic to?")
    assert out["used"]["memories"][0] == {"id": peanuts, "type": "semantic", "content": "Is allergic to peanuts.", "matched": True}
    assert len(out["used"]["memories"]) == 3


def test_the_memory_budget_keeps_the_matches(user_id, model, monkeypatch):
    monkeypatch.setattr(orchestrator, "MEMORY_BUDGET_CHARS", 400)
    target = memory.write(user_id, "The spare tyre is in the boot.", "semantic")  # the oldest of all
    for i in range(50):
        memory.write(user_id, f"Filler fact number {i} about nothing in particular.", "semantic")

    out = orchestrator.handle(user_id, "where is the spare tyre?")
    included = out["used"]["memories"]
    assert included[0]["id"] == target and included[0]["matched"]
    assert 1 < len(included) < 51
    assert sum(len(m["content"]) + 30 for m in included) <= 400


def test_the_offline_model_answers_from_an_earlier_chat(user_id):
    orchestrator.handle(user_id, "my bike is parked at gate 4 near the library")
    out = orchestrator.handle(user_id, "where is my bike parked?")
    assert "gate 4" in out["reply"]


def test_a_v2_database_is_upgraded_in_place(user_id):
    kept = memory.write(user_id, "Prefers aisle seats.", "preference")
    before = orchestrator.handle(user_id, "which seats do I prefer?")
    with db.connect() as conn:  # turn the file back into what schema v2 made
        conn.execute("DROP TABLE message_recalls")
        conn.execute("ALTER TABLE message_memories DROP COLUMN matched")
        conn.execute("DROP TABLE memory_vectors")
        conn.execute("DROP TABLE message_vectors")
        conn.execute("DROP TABLE user_settings")
        conn.execute("DROP TABLE usage_daily")
        conn.execute("DROP TABLE sessions")
        conn.execute("ALTER TABLE conversations DROP COLUMN private")
        conn.execute("ALTER TABLE messages DROP COLUMN checks")
        conn.execute("ALTER TABLE messages DROP COLUMN trace")
        for table in ("notifications", "message_feedback", "integrations"):
            conn.execute(f"DROP TABLE {table}")
        new_tools = "SELECT id FROM tools WHERE name IN ('reminders', 'documents', 'gcal', 'gmail', 'gdrive')"
        conn.execute(f"DELETE FROM skill_tools WHERE tool_id IN ({new_tools})")
        conn.execute(f"DELETE FROM permissions WHERE tool_id IN ({new_tools})")
        conn.execute(new_tools.replace("SELECT id FROM", "DELETE FROM", 1))
        conn.execute("PRAGMA user_version = 2")

    db.init_db()  # v2 -> v3 -> v4 -> v5 -> v6 -> v7

    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 7
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"message_recalls", "memory_vectors", "message_vectors", "user_settings", "usage_daily", "sessions",
                "notifications", "message_feedback", "integrations"} <= tables
        tools = {r["name"] for r in conn.execute("SELECT name FROM tools")}
        assert {"reminders", "documents", "gcal", "gmail", "gdrive"} <= tools
        assert conn.execute("SELECT private FROM conversations").fetchone()["private"] == 0
        links = conn.execute("SELECT memory_id, matched FROM message_memories").fetchall()
    assert [(r["memory_id"], r["matched"]) for r in links] == [(kept, 1)]  # old links count as matched
    thread = conversations.get_conversation(user_id, before["conversation_id"])
    assert thread["messages"][-1]["used"]["earlier"] == []
    assert orchestrator.handle(user_id, "which seats do I prefer?")["used"]["memories"][0]["id"] == kept


@pytest.mark.parametrize(
    "text, small_talk",
    [
        ("thanks, that helps", True),
        ("Good morning!", True),
        ("ok thank you so much", True),
        ("hello there", True),
        ("hey, I'm vegetarian", False),
        ("good news: I got the job", False),
        ("thanks for booking the Goa trip", False),
        ("remind me to drink more water", False),
    ],
)
def test_small_talk_detection(text, small_talk):
    assert history_search.looks_like_small_talk(text) is small_talk


def test_small_talk_looks_nothing_up(user_id, model):
    orchestrator.handle(user_id, "thanks to Kavya the umbrella came back")
    assert _earlier(orchestrator.handle(user_id, "thanks, that's all")) == []
