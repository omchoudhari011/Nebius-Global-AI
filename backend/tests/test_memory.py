from datetime import datetime, timedelta, timezone

import pytest

from app import db, memory


def test_write_and_recall_with_stemming(user_id):
    dentist = memory.write(user_id, "Dentist appointments are on Fridays at 4pm", "semantic")
    memory.write(user_id, "Likes oat milk in coffee", "preference")
    hits = memory.recall(user_id, "When is my next dentist appointment?")
    assert [h["id"] for h in hits][0] == dentist
    assert hits[0]["content"] == "Dentist appointments are on Fridays at 4pm"


def test_content_is_encrypted_at_rest(user_id):
    memory_id = memory.write(user_id, "Passport number is in the blue folder", "semantic")
    with db.connect() as conn:
        raw = conn.execute("SELECT content FROM memories WHERE id = ?", (memory_id,)).fetchone()["content"]
    assert b"Passport" not in bytes(raw)


def test_recall_is_scoped_to_the_user(user_id):
    with db.connect() as conn:
        other = conn.execute("INSERT INTO users (email) VALUES ('other@example.com')").lastrowid
    memory.write(other, "Other person's secret garden", "semantic")
    assert memory.recall(user_id, "secret garden") == []
    assert len(memory.recall(other, "secret garden")) == 1


def test_expired_memories_are_skipped_and_purged(user_id):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    gone = memory.write(user_id, "Trip to Goa next week", "episodic", expires_at=past)
    kept = memory.write(user_id, "Trip to Goa photos are on the NAS", "semantic")
    assert [m["id"] for m in memory.recall(user_id, "Goa trip")] == [kept]
    assert memory.purge_expired() == 1
    with db.connect() as conn:
        assert conn.execute("SELECT 1 FROM memories_fts WHERE rowid = ?", (gone,)).fetchone() is None


def test_duplicates_are_reused(user_id):
    first = memory.write(user_id, "Prefers  window seats", "preference")
    assert memory.write(user_id, "Prefers window seats", "preference") == first


def test_delete_removes_search_copy(user_id):
    memory_id = memory.write(user_id, "Gym locker code hint: birthday", "semantic")
    assert memory.delete(user_id, memory_id)
    with db.connect() as conn:
        assert conn.execute("SELECT 1 FROM memories_fts WHERE rowid = ?", (memory_id,)).fetchone() is None
    assert not memory.delete(user_id, memory_id)


def test_update_replaces_search_copy(user_id):
    memory_id = memory.write(user_id, "Favourite tea is chamomile", "preference")
    memory.update(user_id, memory_id, content="Favourite tea is masala chai")
    assert memory.recall(user_id, "chamomile") == []
    assert memory.recall(user_id, "masala chai")[0]["id"] == memory_id


@pytest.mark.parametrize(
    "query",
    [
        "'; DROP TABLE memories; --",
        'quote " inside',
        "NEAR(a b) OR * AND -x ^y",
        "column:filter",
        "",
        "the and of",
    ],
)
def test_hostile_queries_never_break_recall(user_id, query):
    memory.write(user_id, "Robert'); DROP TABLE memories;-- likes tea", "semantic")
    memory.recall(user_id, query)  # must not raise
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


def test_invalid_input(user_id):
    with pytest.raises(memory.InvalidMemory):
        memory.write(user_id, "   ", "semantic")
    with pytest.raises(memory.InvalidMemory):
        memory.write(user_id, "fine", "gossip")
    with pytest.raises(memory.InvalidMemory):
        memory.write(user_id, "x" * (memory.MAX_MEMORY_CHARS + 1))


def test_preferences_are_returned_regardless_of_keywords(user_id):
    memory.write(user_id, "Keep answers short", "preference")
    memory.write(user_id, "Lives in Pune", "semantic")
    assert [m["content"] for m in memory.preferences(user_id)] == ["Keep answers short"]
