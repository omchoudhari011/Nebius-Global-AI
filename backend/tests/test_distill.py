"""Distillation (finetune/distill.py) with stand-ins for the larger model: rewrites that change
the request are refused, and every kept conversation passed the app's own checks."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

FINETUNE = Path(__file__).resolve().parents[1] / "finetune"
sys.path.insert(0, str(FINETUNE))

import distill  # noqa: E402


def test_a_rewrite_must_keep_every_name_number_and_quote():
    original = "remind me to call Priya at 6:30 pm about “the lease”"
    assert distill.keeps_meaning(original, "yaar priya ko 6:30 pm call karna hai “the lease” ke baare mein, remind kar dena")
    assert not distill.keeps_meaning(original, "remind me to call Priya in the evening about “the lease”")  # the time went
    assert not distill.keeps_meaning(original, "remind me to call her at 6:30 pm about “the lease”")  # the name went
    assert not distill.keeps_meaning(original, original.upper())  # the same message
    assert not distill.keeps_meaning(original, "a\nb")
    plain = "take a note: call the plumber about the kitchen tap"  # no names or numbers: the words carry it
    assert distill.keeps_meaning(plain, "plumber ko call karna hai kitchen tap ke liye, note bana do")
    assert not distill.keeps_meaning(plain, "something else entirely")


SCRIPT = textwrap.dedent('''
    import argparse, json, sys
    from pathlib import Path
    sys.path.insert(0, {finetune!r})
    import distill, make_dataset
    from app.model_backends.base import ModelResponse

    class Writer:
        model_id = "fake-writer"
        def generate(self, messages, tools=None):
            said = messages[-1]["content"]
            return ModelResponse(json.dumps({{"variants": ["pls " + said.lower(), said + " thanks", "something else entirely"]}}), [], "w")

    class Answerer(make_dataset.Teacher):  # follows the scenario's plan, as a good model would
        pass

    args = argparse.Namespace(per_kind=2, variants=3, replies={replies!r}, kinds=["note_create", "remember_fact", "document"],
                              seed=1, valid=0.0, out=Path({out!r}), workers=2, temperature=0.8)
    result = distill.distill(args, writer=Writer(), answerer=Answerer() if {replies!r} == "model" else None)
    print("RESULT " + json.dumps(result))
''')


def _run(tmp_path, replies):
    out = tmp_path / f"distilled-{replies}.jsonl"
    proc = subprocess.run([sys.executable, "-c", SCRIPT.format(finetune=str(FINETUNE), replies=replies, out=str(out))],
                          cwd=FINETUNE.parent, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    result = json.loads(proc.stdout.split("RESULT ", 1)[1])
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    return result, rows


def test_scripted_answers_to_new_phrasings_pass_the_checks(tmp_path):
    result, rows = _run(tmp_path, "scripted")
    assert result["train"] == len(rows) > 0 and set(result["kinds"]) <= {"note_create", "remember_fact", "document"}
    said = [next(m["content"] for m in reversed(r["messages"]) if m["role"] == "user") for r in rows]
    assert all("pls " in s or s.endswith(" thanks") for s in said)
    assert not any("something else entirely" in s for s in said)  # it changes the request: never kept
    assert all(r["source"] == "distilled" and r["messages"][-1]["role"] == "assistant" for r in rows)


def test_model_answers_are_recorded_and_checked(tmp_path):
    result, rows = _run(tmp_path, "model")
    assert result["train"] == len(rows) > 0
    assert any(m.get("tool_calls") for r in rows for m in r["messages"] if m["role"] == "assistant")
