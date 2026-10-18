"""Deeper memory: the people, places and things memories name, and facts that change."""

from __future__ import annotations

import pytest

from app import memory, memory_graph, orchestrator
from app.memory_graph import Found
from app.model_backends import ModelResponse, set_backend
from app.model_backends.base import ModelBackend, ToolCallRequest


@pytest.mark.parametrize("text, expected", [
    ("Their sister Meera lives in Pune.", [Found("Meera", "person", "sister"), Found("Pune", "place", "lives in", "Meera")]),
    ("Works at Infosys as a designer.", [Found("Infosys", "organization", "works at")]),
    ("Their dog is called Bruno.", [Found("Bruno", "pet", "dog")]),
    ("Meera is their best friend.", [Found("Meera", "person", "best friend")]),
    ("Their sister's name is Riya.", [Found("Riya", "person", "sister")]),
    ("Is working on a startup called Custodian.", [Found("Custodian", "project", "works on")]),
    ("Is learning Spanish on Mondays.", []),
])
def test_entities_come_from_the_memorys_own_words(text, expected):
    assert memory_graph.extract(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("Lives in Pune.", ("home", "self", "pune")),
    ("Moved from Pune to Mumbai.", ("home", "self", "mumbai")),
    ("Is moving to Pune next month.", None),  # a plan, not a change yet
    ("Their sister Meera lives in Delhi.", ("home", "meera", "delhi")),
    ("Their favourite restaurant is Toit in Indiranagar.", ("favourite restaurant", "self", "toit in indiranagar")),
    ("Works at Infosys as a designer.", ("work", "self", "infosys")),
    ("Is vegetarian.", ("diet", "self", "vegetarian")),
    ("Became vegan last year.", ("diet", "self", "vegan")),
    ("Is allergic to peanuts.", None),
])
def test_what_a_memory_says_about_something_that_can_change(text, expected):
    assert memory_graph.attribute(text) == expected


def test_a_newer_fact_replaces_the_older_one_and_keeps_it_as_history(user_id):
    old = memory.write(user_id, "Lives in Pune.", "semantic")
    sister = memory.write(user_id, "Their sister Meera lives in Delhi.", "semantic")
    new = memory.write(user_id, "Moved from Pune to Mumbai.", "semantic")
    memory_graph.process_pending()
    kept = memory.get(user_id, old)
    assert kept["superseded_by"] == new and kept["valid_until"] is not None
    assert memory.get(user_id, sister)["superseded_by"] is None  # her home isn't theirs
    everything = [m["id"] for m, _ in memory.for_context(user_id, "hello there", budget_chars=5000)]
    assert old not in everything and new in everything  # history comes in only when asked about
    asked = [m["id"] for m in memory.search(user_id, "where did I live in Pune before")]
    assert old in asked


def test_deleting_the_newer_fact_makes_the_older_one_true_again(user_id):
    old = memory.write(user_id, "Works at Infosys.", "semantic")
    new = memory.write(user_id, "Works at Google.", "semantic")
    memory_graph.process_pending()
    assert memory.get(user_id, old)["superseded_by"] == new
    memory.delete(user_id, new)
    restored = memory.get(user_id, old)
    assert restored["superseded_by"] is None and restored["valid_until"] is None


def test_my_sister_finds_memories_about_her_that_never_say_sister(user_id):
    memory.write(user_id, "Their sister is called Meera.", "semantic")
    about = memory.write(user_id, "Meera works at Infosys as a designer.", "semantic")
    memory.write(user_id, "Is allergic to peanuts.", "semantic")
    memory_graph.process_pending()
    assert about in memory_graph.related_memory_ids(user_id, "what does my sister do?")
    people = {e["name"]: e for e in memory_graph.graph(user_id)}
    assert people["Meera"]["relation"] == "sister" and {"relation": "works at", "to": "Infosys"} in people["Meera"]["links"]


class Recorder(ModelBackend):
    name, model_id = "recorder", "recorder-1"

    def __init__(self, calls=None):
        self.prompts, self.calls = [], list(calls or [])

    def generate(self, messages, tools=None):
        self.prompts.append(messages[0]["content"])
        if self.calls and tools:
            return ModelResponse("", [self.calls.pop(0)], self.model_id)
        return ModelResponse("Noted.", [], self.model_id)


def test_the_prompt_shows_related_memories_and_marks_history(user_id):
    memory.write(user_id, "Their sister is called Meera.", "semantic")
    about = memory.write(user_id, "Meera works at Infosys as a designer.", "semantic")
    old = memory.write(user_id, "Lives in Pune.", "semantic")
    new = memory.write(user_id, "Lives in Mumbai.", "semantic")
    memory_graph.process_pending()
    backend = Recorder()
    set_backend(backend)
    out = orchestrator.handle(user_id, "what does my sister do? and where did I live in Pune")
    matched = {m["id"] for m in out["used"]["memories"] if m.get("matched", True)}
    assert about in matched
    assert f"[memory #{old} · semantic · no longer true since" in backend.prompts[0]
    assert f"replaced by #{new}]" in backend.prompts[0]


def test_the_model_can_say_a_new_memory_replaces_one_it_was_shown(user_id):
    old = memory.write(user_id, "Their car is a red Honda City.", "semantic")
    set_backend(Recorder([ToolCallRequest("c1", "remember", {"content": "Their car is a blue Nexon EV.", "replaces": old})]))
    orchestrator.handle(user_id, "I sold the Honda, my car is a blue Nexon EV now")
    assert memory.get(user_id, old)["superseded_by"] is not None


def test_the_map_is_listed_for_the_app(client):
    client.post("/memories", json={"content": "Their dog is called Bruno.", "type": "semantic"})
    listed = client.get("/memory/graph").json()
    assert listed[0]["name"] == "Bruno" and listed[0]["kind"] == "pet" and listed[0]["relation"] == "dog"
    assert listed[0]["memories"][0]["content"] == "Their dog is called Bruno." and listed[0]["memories"][0]["current"]
