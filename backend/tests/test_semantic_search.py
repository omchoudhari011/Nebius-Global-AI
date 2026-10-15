"""Hybrid retrieval: search by meaning (app/vectors.py) alongside keyword search, with a
small deterministic embedder so no model is needed."""

import json
import re
import zlib

import httpx
import numpy as np
import pytest

from app import conversations, db, embeddings, history_search, memory, orchestrator, vectors
from app.crypto import decrypt_bytes, encrypt
from app.embeddings import Embedder
from app.model_backends import ModelResponse, set_backend
from app.model_backends.base import ModelBackend

SYNONYMS = [  # words in one group mean the same thing to FakeEmbedder
    {"umbrella", "umbrellas", "brolly"},
    {"shellfish", "seafood", "prawns", "prawn"},
    {"car", "vehicle", "parked", "park"},
]
BUCKETS = 64


class FakeEmbedder(Embedder):
    """Synonyms share a dimension; every other word lands in a hashed one with less weight."""

    def __init__(self, model: str = "fake-embed"):
        super().__init__(model, "http://embeddings.invalid/v1")
        self.sent: list[str] = []
        self.requests = 0

    def _raw(self, texts):
        self.requests += 1
        self.sent.extend(texts)
        rows = []
        for text in texts:
            row = [0.0] * (len(SYNONYMS) + BUCKETS)
            for word in re.findall(r"[a-z']+", text.lower()):
                group = next((i for i, words in enumerate(SYNONYMS) if word in words), None)
                if group is not None:
                    row[group] += 1.0
                else:
                    row[len(SYNONYMS) + zlib.crc32(word.encode()) % BUCKETS] += 0.3
            rows.append(row)
        return rows


class OkBackend(ModelBackend):
    name = "ok"
    model_id = "ok-1"

    def generate(self, messages, tools=None):
        return ModelResponse("OK.", [], self.model_id)


@pytest.fixture
def fake():
    embedder = FakeEmbedder()
    embeddings.set_embedder(embedder)
    set_backend(OkBackend())
    return embedder


def _earlier(out: dict) -> list[str]:
    return [e["content"] for e in out["used"]["earlier"]]


def test_a_synonym_finds_an_earlier_message(user_id, fake):
    orchestrator.handle(user_id, "I lent my blue umbrella to Kavya after dinner")
    out = orchestrator.handle(user_id, "who has my brolly?")  # a new chat; no word in common
    assert _earlier(out) == ["I lent my blue umbrella to Kavya after dinner"]


def test_keyword_search_alone_misses_it(user_id):
    set_backend(OkBackend())
    orchestrator.handle(user_id, "I lent my blue umbrella to Kavya after dinner")
    assert _earlier(orchestrator.handle(user_id, "who has my brolly?")) == []


def test_memories_match_by_meaning(user_id, fake):
    shellfish = memory.write(user_id, "Is allergic to prawns.", "semantic")
    memory.write(user_id, "Lives in Pune.", "semantic")
    out = orchestrator.handle(user_id, "is there any seafood I should avoid?")
    first = out["used"]["memories"][0]
    assert (first["id"], first["matched"]) == (shellfish, True)
    assert not any(m["matched"] for m in out["used"]["memories"][1:])


def test_a_match_must_stand_out(user_id, fake):
    chatter = [
        "the weather is lovely today", "let's plan the week", "I had a long day at work", "lunch was great",
        "remind me to drink more water", "I'm going for a walk", "the traffic was terrible", "I finished the report",
        "my phone battery died again", "I need to clean the kitchen", "it rained all afternoon", "dinner is at eight",
        "I parked the car near the station",
    ]
    for line in chatter:
        orchestrator.handle(user_id, line)
    assert _earlier(orchestrator.handle(user_id, "hello there")) == []  # nothing means the same
    assert _earlier(orchestrator.handle(user_id, "where is my vehicle?")) == ["I parked the car near the station"]


def test_vectors_are_stored_encrypted(user_id, fake):
    orchestrator.handle(user_id, "I lent my blue umbrella to Kavya after dinner")
    with db.connect() as conn:
        rows = conn.execute("SELECT model, vector FROM message_vectors WHERE vector IS NOT NULL").fetchall()
    assert rows and all(r["model"] == "fake-embed" for r in rows)
    for row in rows:
        assert bytes(row["vector"]).startswith(b"gAAAAA")  # a Fernet token
        assert len(np.frombuffer(decrypt_bytes(row["vector"]), dtype=np.float32)) == len(SYNONYMS) + BUCKETS


def test_secrets_are_never_sent_to_the_embedding_endpoint(user_id, fake):
    out = orchestrator.handle(user_id, "my bank password is hunter2")
    assert not any("hunter2" in text for text in fake.sent)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT vector FROM message_vectors WHERE message_id = (SELECT MIN(id) FROM messages WHERE role = 'user')"
        ).fetchone()
    assert row is not None and row["vector"] is None  # marked "never embed", so it isn't retried
    assert out["used"]["earlier"] == []


def test_existing_messages_and_memories_are_backfilled(user_id, fake):
    with db.connect() as conn:  # written before semantic search was switched on
        cid = conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (user_id,)).lastrowid
        for text in ["I parked the car on level 3", "my card pin is 4321"]:
            conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', ?)", (cid, encrypt(text)))
    memory.write(user_id, "Is allergic to prawns.", "semantic")

    assert vectors.index_missing() == 3
    assert vectors.index_missing() == 0  # nothing left, the secret included
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM message_vectors WHERE vector IS NULL").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM memory_vectors WHERE vector IS NOT NULL").fetchone()[0] == 1
    found = history_search.search(user_id, "where's my vehicle", query_vector=fake.query("where's my vehicle"))
    assert [f["content"] for f in found] == ["I parked the car on level 3"]


def test_an_edited_memory_is_embedded_again(user_id, fake):
    kept = memory.write(user_id, "Is allergic to prawns.", "semantic")
    vectors.index_missing()
    memory.update(user_id, kept, content="Keeps a spare umbrella in the car boot.")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0] == 0
    out = orchestrator.handle(user_id, "do I have a brolly anywhere?")
    assert (out["used"]["memories"][0]["id"], out["used"]["memories"][0]["matched"]) == (kept, True)
    out = orchestrator.handle(user_id, "is there any seafood I should avoid?")
    assert not out["used"]["memories"][0]["matched"]  # the old meaning is gone


def test_a_deleted_conversation_leaves_semantic_search(user_id, fake):
    first = orchestrator.handle(user_id, "I lent my blue umbrella to Kavya after dinner")
    orchestrator.handle(user_id, "who has my brolly?")
    assert conversations.delete_conversation(user_id, first["conversation_id"])
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM message_vectors").fetchone()[0] == 1  # only the question is left
    assert _earlier(orchestrator.handle(user_id, "who has my brolly now?")) == []


def test_changing_the_embedding_model_embeds_everything_again(user_id, fake):
    orchestrator.handle(user_id, "I lent my blue umbrella to Kavya after dinner")
    other = FakeEmbedder("fake-embed-v2")
    embeddings.set_embedder(other)
    out = orchestrator.handle(user_id, "who has my brolly?")
    assert _earlier(out) == ["I lent my blue umbrella to Kavya after dinner"]
    with db.connect() as conn:
        models = {r["model"] for r in conn.execute("SELECT model FROM message_vectors")}
    assert models == {"fake-embed", "fake-embed-v2"}


def test_keyword_search_carries_on_when_the_endpoint_is_down(user_id):
    calls = []

    def broken(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500, text="model not loaded")

    embeddings.set_embedder(Embedder("nomic-embed-text", "http://ollama.local/v1", transport=httpx.MockTransport(broken)))
    set_backend(OkBackend())
    orchestrator.handle(user_id, "I lent my blue umbrella to Kavya after dinner")
    out = orchestrator.handle(user_id, "who has my umbrella?")
    assert _earlier(out) == ["I lent my blue umbrella to Kavya after dinner"]  # keyword search still works
    assert len(calls) == 1  # after one failure the endpoint is left alone for a while


def test_the_embedding_request(user_id):
    seen = []

    def endpoint(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((request.url.path, request.headers.get("authorization"), body))
        data = [{"index": i, "embedding": [1.0, float(i)]} for i in range(len(body["input"]))]
        return httpx.Response(200, json={"object": "list", "data": data})

    embedder = Embedder("nomic-embed-text:latest", "https://api.example.com/v1/", "key-123", transport=httpx.MockTransport(endpoint))
    query, document = embedder.query_and_document("who has my brolly?")
    path, auth, body = seen[0]
    assert path == "/v1/embeddings" and auth == "Bearer key-123"
    assert body == {"model": "nomic-embed-text:latest", "input": ["search_query: who has my brolly?", "search_document: who has my brolly?"]}
    assert np.isclose(np.linalg.norm(query), 1.0) and np.isclose(np.linalg.norm(document), 1.0)


def test_fusion_puts_items_found_both_ways_first():
    assert vectors.fuse([1, 2, 3], [3, 4])[0] == 3
    assert vectors.fuse([], [7, 8]) == [7, 8]


def test_only_statements_worth_recalling_are_embedded(user_id, fake):
    for text in ["what is my name?", "thanks, that helps", "I lent my blue umbrella to Kavya after dinner"]:
        orchestrator.handle(user_id, text)
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT v.vector IS NOT NULL AS embedded FROM message_vectors v
            JOIN messages m ON m.id = v.message_id ORDER BY m.id
            """
        ).fetchall()
    assert [bool(r["embedded"]) for r in rows] == [False, False, True]
    sent_documents = [t for t in fake.sent if "umbrella" in t or "name" in t or "thanks" in t]
    assert sent_documents.count("I lent my blue umbrella to Kavya after dinner") == 2  # as a query and a document
    assert sent_documents.count("what is my name?") == 1  # only as a query
