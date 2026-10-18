"""Learning from your corrections overnight: the preference pairs, when a run is due, what it
downloads, what it reports, and when finetune/nightly.py switches models."""

import importlib.util
from pathlib import Path

from app import feedback, learning, notifications, orchestrator, settings_store
from app.model_backends import set_backend
from tests.test_everyday import Script, say

spec = importlib.util.spec_from_file_location("nightly", Path(__file__).resolve().parents[1] / "finetune" / "nightly.py")
nightly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nightly)


def _rate(user_id, reply, question, rating, correction=None, share=True):
    set_backend(Script(say(reply)))
    out = orchestrator.handle(user_id, question)
    feedback.save(user_id, out["message_id"], rating, correction, share)
    return out


def test_a_correction_becomes_a_preference_pair(user_id):
    _rate(user_id, "Lima.", "Capital of Peru?", -1, "The capital of Peru is Lima.")
    _rate(user_id, "Paris.", "Capital of France?", 1)  # a thumbs up is a plain example, not a pair
    _rate(user_id, "Rome.", "Capital of Italy?", -1, "The capital of Italy is Rome.", share=False)  # not shared
    _rate(user_id, "Hmm.", "Capital of Spain?", -1)  # no correction: nothing to prefer
    (pair,) = feedback.preference_pairs(user_id)
    assert pair["chosen"] == "The capital of Peru is Lima." and pair["rejected"] == "Lima."
    assert pair["messages"][-1] == {"role": "user", "content": "Capital of Peru?"}
    assert pair["messages"][0]["role"] == "system" and pair["tools"]


def test_a_run_is_due_after_a_few_new_ratings_and_then_marked_learned(user_id, client):
    assert client.get("/learning").json()["due"] is False
    for i in range(learning.MIN_NEW):
        _rate(user_id, f"Answer {i}.", f"Question {i}?", -1, f"A better answer {i}.")
    status = client.get("/learning").json()
    assert status["due"] and status["new_examples"] == learning.MIN_NEW and status["last_run"] is None

    data = client.get("/learning/data").json()
    assert len(data["examples"]) == learning.MIN_NEW and len(data["pairs"]) == learning.MIN_NEW and data["through"]
    result = client.post("/learning/result", json={"outcome": "switched", "summary": "held-out 117/133 (was 116/133)",
                                                   "through": data["through"], "examples": 3})
    assert result.status_code == 200
    after = client.get("/learning").json()
    assert after["due"] is False and after["new_examples"] == 0 and after["last_run"]["outcome"] == "switched"
    (note,) = [n for n in notifications.list_for(user_id) if n["kind"] == "learning"]
    assert "learned from 3" in note["title"]


def test_a_stopped_run_leaves_the_ratings_for_the_next_night(user_id, client):
    for i in range(learning.MIN_NEW):
        _rate(user_id, f"A{i}.", f"Q{i}?", 1)
    client.post("/learning/result", json={"outcome": "stopped", "summary": "you came back"})
    assert client.get("/learning").json()["due"] is True
    assert not [n for n in notifications.list_for(user_id) if n["kind"] == "learning"]  # no morning note for it
    assert client.post("/learning/result", json={"outcome": "deleted everything"}).status_code == 422


def test_switched_off_means_no_run_and_no_data(user_id, client):
    settings_store.update(user_id, nightly_learning=False)
    assert client.get("/learning").json()["due"] is False
    assert client.get("/learning/data").status_code == 409


def _scores(held, kinds, recall="10/10 questions recalled; 9/10 answers contained it", drafts="2/12 first drafts"):
    return {"held_out": held, "per_kind": kinds,
            "evals": {"Hallucination guard": drafts, "Memory recall": recall, "New-chat recall": "10/10 questions"}}


def test_nightly_switches_only_when_nothing_got_worse():
    base = _scores("116/133", {"files": "4/6", "memory": "20/22"})
    same = {"cur": base, "new": _scores("116/133", {"files": "4/6", "memory": "20/22"})}
    assert nightly.decide(same, "cur", "new")[0] is True
    noise = {"cur": base, "new": _scores("115/133", {"files": "3/6", "memory": "21/22"})}
    assert nightly.decide(noise, "cur", "new")[0] is True  # one conversation either way is noise
    v6 = {"cur": base, "new": _scores("118/133", {"files": "1/6", "memory": "22/22"})}
    ok, why = nightly.decide(v6, "cur", "new")
    assert not ok and "files fell from 4/6 to 1/6" in why  # better overall, but lost a whole kind
    forgot = {"cur": base, "new": _scores("117/133", {"files": "4/6", "memory": "20/22"},
                                          recall="10/10 questions recalled; 7/10 answers contained it")}
    assert nightly.decide(forgot, "cur", "new")[0] is False
    invents = {"cur": base, "new": _scores("117/133", {"files": "4/6", "memory": "20/22"}, drafts="5/12 first drafts")}
    assert nightly.decide(invents, "cur", "new")[0] is False
    assert nightly.decide({"cur": base}, "cur", "new") == (False, "the comparison didn't finish")
