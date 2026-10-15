"""Compares Ollama models on Custodian's job: the base model against the fine-tuned one.

1. Held-out behaviour: for every conversation in finetune/data/valid.jsonl (never trained on),
   the model gets the same prompt and tools and must take the right first step - call the
   right tool(s), save the right memory, or answer without tools. A `remember` call counts only
   if the memory is in the user's own words (the app's own check).
2. The app's evals against the real agent loop: hallucination guard (false first drafts), memory
   recall and new-chat recall (eval/*.py --no-write; results.md is left alone).

    python finetune/compare.py                                      # base vs custodian-nemotron
    python finetune/compare.py --models nemotron-3-nano:4b          # one model (a baseline)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND_DIR = HERE.parent
sys.path.insert(0, str(BACKEND_DIR))
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def first_step(example: dict) -> tuple[list[dict], dict]:
    """The prompt (everything before the first assistant turn) and that turn."""
    messages = example["messages"]
    first = next(i for i, m in enumerate(messages) if m["role"] == "assistant")
    return messages[:first], messages[first]


def judge(expected: dict, got, prompt: list[dict]) -> tuple[bool, str]:  # noqa: ANN001 - a ModelResponse
    from app import grounding

    want = sorted(c["function"]["name"] for c in expected.get("tool_calls") or [])
    have = sorted(c.name for c in got.tool_calls)
    if want != have:
        return False, f"expected {want or 'a reply'}, got {have or 'a reply'}"
    if "remember" in have:
        said = [m["content"] for m in prompt if m["role"] == "user"]
        for call in got.tool_calls:
            if call.name != "remember":
                continue
            args = call.arguments if isinstance(call.arguments, dict) else json.loads(call.arguments_text() or "{}")
            if grounding.unsupported_words(str(args.get("content", "")), said):
                return False, f"memory not in the user's words: {args.get('content')!r}"
    if not have and not got.content.strip():
        return False, "empty reply"
    return True, ""


def behaviour(model: str, base_url: str, valid: Path) -> tuple[str, Counter, list[str]]:
    from app.model_backends.nebius_nemotron_backend import NebiusNemotronBackend

    backend = NebiusNemotronBackend("ollama", model, base_url, timeout=180, temperature=0.0, max_tokens=512)
    ok, total, failures = Counter(), Counter(), []
    with open(valid, encoding="utf-8") as f:
        examples = [json.loads(line) for line in f if line.strip()]
    for example in examples:
        prompt, expected = first_step(example)
        got = backend.generate(prompt, example["tools"])
        passed, why = judge(expected, got, prompt)
        total[example["kind"]] += 1
        ok[example["kind"]] += passed
        if not passed:
            failures.append(f"{example['kind']}: {why}")
    backend.close()
    return f"{sum(ok.values())}/{sum(total.values())}", Counter({k: f"{ok[k]}/{total[k]}" for k in total}), failures


def eval_line(script: str, model: str, base_url: str) -> str:
    env = {**os.environ, "NEBIUS_MODEL_ID": model, "NEBIUS_BASE_URL": base_url, "MODEL_BACKEND": "nebius",
           "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, f"eval/{script}", "--model", "nebius", "--no-write"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    line = lines[-1] if lines else proc.stderr.strip().splitlines()[-1]
    return re.sub(r"\s*\(nebius \([^)]*\)\)\s*$", "", line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--models", nargs="+", default=["nemotron-3-nano:4b", "custodian-nemotron"])
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--valid", type=Path, default=HERE / "data" / "valid.jsonl")
    parser.add_argument("--skip-evals", action="store_true", help="only the held-out behaviour test")
    args = parser.parse_args()

    report = ["# Fine-tuning comparison", ""]
    for model in args.models:
        print(f"\n=== {model}", flush=True)
        score, per_kind, failures = behaviour(model, args.base_url, args.valid)
        print(f"held-out first step right: {score}")
        print("  " + ", ".join(f"{k} {v}" for k, v in sorted(per_kind.items())))
        for failure in failures[:12]:
            print(f"  - {failure}")
        report += [f"## {model}", "", f"- Held-out first step right: {score}",
                   "  - " + ", ".join(f"{k} {v}" for k, v in sorted(per_kind.items()))]
        if not args.skip_evals:
            for label, script in (("Hallucination guard", "grounding_eval.py"), ("Memory recall", "memory_recall_eval.py"),
                                  ("New-chat recall", "lifetime_recall_eval.py")):
                line = eval_line(script, model, args.base_url)
                print(f"{label}: {line}", flush=True)
                report.append(f"- {label}: {line}")
        report.append("")
    out = HERE / "out" / "compare.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(report), encoding="utf-8")
    print(f"\nwritten to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
