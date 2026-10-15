"""Runs every eval and rewrites results.md from the output.

    python eval/run_all.py [--model stub|nebius]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND_DIR = HERE.parent


def run(*args: str) -> int:
    print(f"\n$ python {' '.join(args)}", flush=True)
    return subprocess.call([sys.executable, *args], cwd=BACKEND_DIR)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["stub", "nebius"], default="stub")
    args = parser.parse_args()

    failures = 0
    failures += run("eval/memory_recall_eval.py", "--model", args.model) != 0
    failures += run("eval/lifetime_recall_eval.py", "--model", args.model) != 0
    failures += run("eval/semantic_recall_eval.py", "--model", args.model) != 0  # skipped without EMBEDDING_MODEL
    failures += run("eval/latency_eval.py", "--model", args.model) != 0
    failures += run("eval/permission_block_eval.py") != 0
    failures += run("eval/grounding_eval.py") != 0  # scripted false claims: no model needed
    if args.model == "nebius":
        failures += run("eval/grounding_eval.py", "--model", "nebius") != 0

    print("\n$ pytest eval/test_security.py", flush=True)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "eval/test_security.py", "-q", "-p", "no:cacheprovider"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    print(proc.stdout[-2000:])
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", proc.stdout)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", proc.stdout)) else 0
    total = passed + failed
    sys.path.insert(0, str(HERE))
    from _common import update_results

    update_results(
        "Security-test pass rate",
        f"{passed}/{total} ({100 * passed / total:.0f}%) - SQL injection in memories, path-traversal arguments, "
        "sandbox network/file/process/secret isolation; guardrails: secrets never saved or recalled, recalled "
        "text kept out of the system prompt, memory writes per reply capped, task frequency and daily runs limited"
        if total
        else "no tests ran",
    )
    failures += failed > 0 or total == 0
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
