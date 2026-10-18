"""Searching inside the PC's files: off until switched on, secrets never stored, found by meaning
and by keyword through the sandboxed tool, and gone when switched off."""

from __future__ import annotations

import re
import zlib

import pytest

from app import embeddings, pc_index, permissions, settings_store
from app.embeddings import Embedder
from app.tools import sandbox

SAME = [{"rent", "rental", "lease", "tenancy", "agreement", "contract"}, {"flat", "apartment", "house"},
        {"pune", "kothrud"}]


class Synonyms(Embedder):
    def __init__(self):
        super().__init__("fake-embed", "http://embeddings.invalid/v1")

    def _raw(self, texts):
        rows = []
        for text in texts:
            row = [0.0] * (len(SAME) + 64)
            for word in re.findall(r"[a-z]+", text.lower()):
                group = next((i for i, words in enumerate(SAME) if word in words), None)
                if group is not None:
                    row[group] += 1.0
                else:
                    row[len(SAME) + zlib.crc32(word.encode()) % 64] += 0.25
            rows.append(row)
        return rows


FILES = [
    {"path": r"C:\Users\me\Documents\Kothrud-flat.pdf", "name": "Kothrud-flat.pdf", "kind": "pdf", "modified": 1.0, "size": 900,
     "chunks": ["Leave and licence agreement for the flat in Kothrud, Pune. Monthly rent 18,000 rupees, deposit 1 lakh."]},
    {"path": r"C:\Users\me\Documents\groceries.txt", "name": "groceries.txt", "kind": "txt", "modified": 2.0, "size": 40,
     "chunks": ["Milk, eggs, bread and two kilos of onions."]},
    {"path": r"C:\Users\me\Desktop\logins.txt", "name": "logins.txt", "kind": "txt", "modified": 3.0, "size": 40,
     "chunks": ["bank password: Hunter2!Secret", "notes about the bank visit on Monday"]},
]


@pytest.fixture
def indexed(user_id, tool_ids):
    embeddings.set_embedder(Synonyms())
    settings_store.update(user_id, pc_index=True)
    permissions.set_granted(user_id, tool_ids["pc"], "read", True)
    out = pc_index.ingest(user_id, FILES, [])
    assert out == {"stored": 3, "removed": 0, "secret_passages_skipped": 1}
    return user_id


def _search(user_id: int, query: str) -> dict:
    from app import db
    from app.crypto import encrypt

    with db.connect() as conn:
        conv = conn.execute("INSERT INTO conversations (user_id) VALUES (?)", (user_id,)).lastrowid
        message = conn.execute("INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', ?)",
                               (conv, encrypt(""))).lastrowid
    out = sandbox.execute(user_id, "pc_search_file_contents", {"query": query}, message_id=message, allowed=None)
    assert out.status == "succeeded", out.error
    return out.result


def test_indexing_is_off_until_switched_on(user_id):
    assert pc_index.config(user_id) == {"enabled": False, "folders": [], "indexed": {}}
    with pytest.raises(pc_index.IndexOff):
        pc_index.ingest(user_id, FILES, [])


def test_a_file_is_found_by_what_it_means_not_just_its_words(indexed):
    found = _search(indexed, "rental contract for my Pune apartment")
    assert found["files"][0]["name"] == "Kothrud-flat.pdf" and "18,000 rupees" in found["files"][0]["passage"]
    assert found["files_indexed"] == 3


def test_keyword_search_works_without_an_embedding_model(indexed):
    embeddings.disable()
    found = _search(indexed, "onions")
    assert [f["name"] for f in found["files"]] == ["groceries.txt"]


def test_a_passage_that_looks_like_a_secret_is_never_stored(indexed):
    found = _search(indexed, "bank password")
    assert all("Hunter2" not in f["passage"] for f in found["files"])
    assert any("bank visit" in f["passage"] for f in found["files"])


def test_the_desktop_learns_what_is_indexed_and_changed_files_replace_old_passages(indexed):
    config = pc_index.config(indexed)
    assert config["enabled"] and config["indexed"][FILES[1]["path"]] == [2.0, 40]
    pc_index.ingest(indexed, [{**FILES[1], "modified": 5.0, "chunks": ["Shopping: rice and dal"]}], [FILES[0]["path"]])
    assert pc_index.status(indexed)["files"] == 2
    assert _search(indexed, "onions")["files"] == []
    assert [f["name"] for f in _search(indexed, "rice")["files"]] == ["groceries.txt"]


def test_switching_it_off_deletes_the_index(client):
    from app import auth

    user = auth.ensure_owner()
    embeddings.set_embedder(Synonyms())
    assert client.patch("/settings/prefs", json={"pc_index": True}).json()["pc_index"] is True
    batch = client.post("/desktop/index", json={"files": FILES[:2], "removed": []})
    assert batch.status_code == 200 and client.get("/pc-index").json()["files"] == 2
    client.patch("/settings/prefs", json={"pc_index": False})
    assert client.get("/pc-index").json()["files"] == 0
    assert client.post("/desktop/index", json={"files": FILES[:1], "removed": []}).status_code == 409
    assert pc_index.config(user)["enabled"] is False


def test_bad_settings_are_refused(client):
    assert client.patch("/settings/prefs", json={"pc_index": "yes"}).status_code == 400
    assert client.patch("/settings/prefs", json={"no_such_thing": True}).status_code == 400
    assert client.patch("/settings/prefs", json={"quiet_from": "25:00"}).status_code == 400
