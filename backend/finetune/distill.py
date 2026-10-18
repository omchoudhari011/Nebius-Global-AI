"""Learning from the big model (distillation): thousands of varied, realistic conversations for
the local model, written with the larger model on Nebius Token Factory and kept only when the
app's own accuracy checks pass them.

make_dataset.py's conversations are built from templates, so the local model learns those
phrasings and slips on new ones (v6 got worse at questions about uploaded files). Here, for every
scenario make_dataset.py has (same synthetic world, same tools, same expectations):

1. The larger model rewrites the user's message several ways a real person would say it -
   Hinglish, typos and no punctuation, voice-transcription style, indirect, very short, polite and
   long - and a rewrite is kept only if every name, number, time and quoted text survives
   (keeps_meaning()), so it still asks for exactly the same thing.
2. Each rewrite runs through the real agent loop (orchestrator.handle) with the larger model
   answering (`--replies model`, the default) or with make_dataset.py's scripted teacher
   (`--replies scripted`: new phrasings, the usual answers - cheaper, and never a wrong call).
3. The conversation is kept only if the accuracy checks pass the reply unchanged, every tool the
   scenario expects was called with the expected outcome, and memories were saved as expected.

Only the synthetic world is ever sent to the model service: no conversation of yours is used.

    $env:NEBIUS_API_KEY = "..."; python finetune/distill.py                  # -> finetune/data/distilled.jsonl
    python finetune/distill.py --per-kind 24 --variants 8 --replies scripted

train_lora.py adds finetune/data/distilled.jsonl to the training set when it's there, and
distilled_valid.jsonl is held out to check new phrasings: compare.py --valid finetune/data/distilled_valid.jsonl.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
DEFAULT_BASE_URL = "https://api.tokenfactory.nebius.com/v1"
STYLES = (
    "Hinglish (Hindi words in Latin letters mixed with English), as typed on a phone",
    "all lowercase, no punctuation, a typo or two",
    "spoken and transcribed by voice: filler words, no punctuation",
    "very short, like a command",
    "indirect - describe the need instead of naming the action",
    "polite and a little long, with a reason",
    "Indian English, casual",
    "as a follow-up thought, starting with 'oh and' or 'also'",
)
PARAPHRASE_PROMPT = """You help build test data for a personal assistant app used in India.
Rewrite the user's message {n} different ways a real person might say the same thing to the assistant.
Use these styles, one each, in order: {styles}.
Rules: keep exactly the same request. Keep every name, number, date, time, amount, place and quoted text
exactly as written (you may change their case). Don't add new facts, people, times or requests. Don't answer it.
Reply with JSON only: {{"variants": ["...", "..."]}}"""

_NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")
_QUOTED = re.compile(r"[“\"']([^”\"']{2,})[”\"']")
_PROPER = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-zA-Z]{2,}\b")
_COMMON = {"Remember", "Please", "Can", "Could", "What", "When", "Where", "Who", "How", "Hey", "Hello", "Set",
           "Add", "Tell", "Show", "Open", "The", "And", "Also", "Keep", "Give", "Find", "Make", "Put", "Turn",
           "Search", "Play", "Save", "Note", "Remind", "Today", "Tomorrow", "Monday", "Tuesday", "Wednesday",
           "Thursday", "Friday", "Saturday", "Sunday", "Just", "Any", "Did", "Does", "Is", "Are", "Do", "Hii", "Hyy"}


def anchors(text: str) -> set[str]:
    """What a rewrite must keep: numbers, quoted text and names (capitalised words that aren't
    the start of a sentence or everyday words)."""
    found = {m for m in _NUMBER.findall(text)}
    found |= {q.strip().lower() for q in _QUOTED.findall(text)}
    found |= {w.lower() for w in _PROPER.findall(text) if w not in _COMMON}
    return found


_WORD = re.compile(r"[a-z]{4,}")
_STOP = {"that", "this", "with", "have", "what", "when", "where", "which", "about", "remember", "please", "could",
         "would", "there", "their", "they", "your", "from", "into", "just", "like", "want", "need", "tell", "know",
         "give", "make", "some", "will", "should", "does", "today", "then", "than", "them", "also", "much", "many"}
MIN_SHARED_WORDS = 0.5  # of the original's other content words, matched by their first four letters


def content_words(text: str) -> set[str]:
    return {w[:4] for w in _WORD.findall(text.lower()) if w not in _STOP}


def keeps_meaning(original: str, variant: str) -> bool:
    variant = variant.strip()
    if not variant or len(variant) > 400 or "\n" in variant:
        return False
    if " ".join(variant.lower().split()) == " ".join(original.lower().split()):
        return False  # the same message adds nothing
    text = variant.lower()
    digits = set(_NUMBER.findall(variant))
    for anchor in anchors(original):
        if anchor[0].isdigit():
            if anchor not in digits:
                return False
        elif anchor not in text:
            return False
    wanted = content_words(original)
    if wanted and len(wanted & content_words(variant)) < MIN_SHARED_WORDS * len(wanted):
        return False  # what it's about went missing ("call the plumber about the kitchen tap")
    return True


class Recorder:
    """A ModelBackend around the larger model that records what it was sent, the way
    make_dataset.Teacher does, so make_dataset.run() can check and keep the conversation."""

    name = "recorder"

    def __init__(self, backend) -> None:  # noqa: ANN001 - a ModelBackend
        self.backend = backend
        self.model_id = backend.model_id
        self.plan = None
        self.calls: list = []

    def generate(self, messages, tools=None):  # noqa: ANN001
        if hasattr(self.backend, "plan"):  # a scripted stand-in for the larger model (tests)
            self.backend.plan = self.plan
        response = self.backend.generate(messages, tools)
        self.calls.append((copy.deepcopy(messages), tools, response))
        return response

    def close(self) -> None:
        pass


def paraphrases(backend, message: str, n: int) -> list[str]:  # noqa: ANN001
    styles = "; ".join(STYLES[:n])
    response = backend.generate([
        {"role": "system", "content": PARAPHRASE_PROMPT.format(n=n, styles=styles)},
        {"role": "user", "content": message},
    ])
    text = response.content.strip()
    text = text[text.find("{"): text.rfind("}") + 1] if "{" in text else text
    try:
        variants = json.loads(text).get("variants", [])
    except (ValueError, AttributeError):
        return []
    seen, kept = set(), []
    for variant in variants:
        if isinstance(variant, str) and keeps_meaning(message, variant) and variant.lower() not in seen:
            seen.add(variant.lower())
            kept.append(variant.strip())
    return kept


def called_as_planned(example: dict, scenario) -> bool:  # noqa: ANN001
    """With the larger model answering: it must have called every tool the scenario plans for."""
    wanted = {name for name, _ in scenario.plan.calls}
    made = {c["function"]["name"] for m in example["messages"] if m["role"] == "assistant"
            for c in m.get("tool_calls") or []}
    return wanted <= made


def large_backend(args: argparse.Namespace, temperature: float):  # noqa: ANN201
    from app.model_backends.nebius_nemotron_backend import NebiusNemotronBackend

    key = os.getenv("LARGE_MODEL_API_KEY", "").strip() or os.getenv("NEBIUS_API_KEY", "").strip()
    if not key and "127.0.0.1" not in args.base_url and "localhost" not in args.base_url:
        raise SystemExit("set NEBIUS_API_KEY (or LARGE_MODEL_API_KEY) for the larger model")
    return NebiusNemotronBackend(key, args.model, args.base_url, timeout=180, max_tokens=1536, temperature=temperature)


def distill(args: argparse.Namespace, writer=None, answerer=None) -> dict:  # noqa: ANN001 - ModelBackends (tests pass fakes)
    # Made before World(), which clears the model settings: varied rewrites, careful answers.
    writer = writer or large_backend(args, args.temperature)
    backend = answerer or (large_backend(args, 0.3) if args.replies == "model" else None)
    import make_dataset

    world = make_dataset.World(args.seed)
    from app.model_backends import set_backend

    scripted = world.teacher
    recorder = Recorder(backend) if backend is not None else None
    todo = [s for s in make_dataset.scenarios(world, args.per_kind) if s.message]
    if args.kinds:
        todo = [s for s in todo if s.kind in args.kinds]
    unique: dict[str, object] = {}
    for scenario in todo:  # one set of rewrites per distinct message
        unique.setdefault(scenario.message, scenario)
    print(f"{len(unique)} distinct messages from {len(todo)} scenarios; asking for {args.variants} rewrites each")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rewritten = dict(zip(unique, pool.map(lambda m: paraphrases(writer, m, args.variants), unique)))

    kept: list[dict] = []
    dropped: Counter[str] = Counter()
    world.teacher = recorder if args.replies == "model" else scripted  # make_dataset.run() records through it
    set_backend(world.teacher)
    for scenario in todo:
        for variant in rewritten.get(scenario.message, [])[: args.variants]:
            attempt = copy.copy(scenario)
            attempt.message = variant
            try:
                example, why = make_dataset.run(world, attempt)
            except Exception as exc:  # noqa: BLE001 - a model error drops one example, not the run
                example, why = None, f"{type(exc).__name__}: {exc}"
            if example is not None and args.replies == "model" and not called_as_planned(example, attempt):
                example, why = None, "didn't call the planned tools"
            if example is None:
                dropped[scenario.kind] += 1
                continue
            example["source"] = "distilled"
            kept.append(example)

    unique_rows = list({json.dumps(e["messages"][1:], sort_keys=True): e for e in kept}.values())
    world.rng.shuffle(unique_rows)
    cut = round(len(unique_rows) * args.valid)
    valid, train = unique_rows[:cut], unique_rows[cut:]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for path, rows in ((args.out, train), (args.out.with_name(args.out.stem + "_valid.jsonl"), valid)):
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    counts = Counter(e["kind"] for e in unique_rows)
    print(f"{'behaviour':<20} kept  dropped")
    for kind in sorted(set(counts) | set(dropped)):
        print(f"{kind:<20} {counts[kind]:>4}  {dropped[kind]:>7}")
    print(f"\n{len(train)} training and {len(valid)} held-out conversations -> {args.out}")
    return {"train": len(train), "valid": len(valid), "dropped": sum(dropped.values()), "kinds": dict(counts)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--per-kind", type=int, default=16, help="scenarios per behaviour (as make_dataset.py)")
    parser.add_argument("--variants", type=int, default=6, help=f"rewrites per message (up to {len(STYLES)})")
    parser.add_argument("--replies", choices=("model", "scripted"), default="model",
                        help="who answers the rewrites: the larger model, or make_dataset.py's scripted teacher")
    parser.add_argument("--kinds", nargs="*", help="only these behaviours, e.g. document note_read")
    parser.add_argument("--model", default=os.getenv("LARGE_MODEL_ID", "").strip() or DEFAULT_MODEL)
    parser.add_argument("--base-url", default=os.getenv("LARGE_MODEL_BASE_URL", "").strip() or DEFAULT_BASE_URL)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--workers", type=int, default=4, help="rewrite requests at once")
    parser.add_argument("--seed", type=int, default=20261018)
    parser.add_argument("--valid", type=float, default=0.1)
    parser.add_argument("--out", type=Path, default=HERE / "data" / "distilled.jsonl")
    args = parser.parse_args()
    args.variants = max(1, min(args.variants, len(STYLES)))
    result = distill(args)
    return 0 if result["train"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
