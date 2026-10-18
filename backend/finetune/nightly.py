"""Learning from your corrections overnight, end to end. The desktop app starts this at night when
the PC has been idle for a while and the server says there's something new (app/learning.py), and
stops it the moment you're back.

1. Gets the replies you rated and shared, and your corrections as preference pairs, from the server.
2. Continues training the adapter in use on them, with a sample of the usual conversations so
   nothing else is forgotten (train_lora.py --init-adapter --pairs --replay, in Docker): ~30 min.
3. Exports it, installs it in Ollama next to the model you use, and compares the two (compare.py).
4. Switches only if the new one is at least as good on everything compare.py measures (decide());
   the old one stays as <current>-previous. Then tells the server, which leaves you a note.

    $env:CUSTODIAN_TOKEN = "<a session token>"; python finetune/nightly.py --server http://127.0.0.1:8000
    python finetune/nightly.py --server ... --force          # even with nothing new (a test run)

Needs what run_finetune.ps1 needs (an NVIDIA GPU, Docker with GPU support, Ollama), and one full
run of run_finetune.ps1 first: tonight's training continues the adapter that made your model.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND_DIR = HERE.parent
IMAGE = "custodian-finetune"
CONTAINER = "custodian-nightly"  # the desktop app stops it by this name when you come back
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_FRACTION = re.compile(r"(\d+)/(\d+)")


def _fractions(line: str) -> list[tuple[int, int]]:
    return [(int(a), int(b)) for a, b in _FRACTION.findall(line or "")]


def decide(scores: dict, current: str, candidate: str) -> tuple[bool, str]:
    """Switch only if the candidate is no worse: held-out first steps overall and for every kind
    of request (one conversation of noise allowed - v6 lost questions about files, 4/6 to 1/6),
    memory and new-chat recall, and no more first drafts caught making things up."""
    if current not in scores or candidate not in scores:
        return False, "the comparison didn't finish"
    old, new = scores[current], scores[candidate]
    reasons = []
    (old_ok, total), = _fractions(old["held_out"]) or [(0, 0)]
    (new_ok, _), = _fractions(new["held_out"]) or [(0, 0)]
    if new_ok < old_ok - 1:
        reasons.append(f"held-out first steps fell from {old_ok}/{total} to {new_ok}/{total}")
    for kind, was in old.get("per_kind", {}).items():
        now = new.get("per_kind", {}).get(kind)
        if now is None:
            continue
        (a, n), (b, _) = _fractions(was)[0], _fractions(now)[0]
        if b < a - 1:
            reasons.append(f"{kind} fell from {a}/{n} to {b}/{n}")
    for label, was in old.get("evals", {}).items():
        now = new.get("evals", {}).get(label, "")
        before, after = _fractions(was), _fractions(now)
        if not before or not after:
            continue
        if label == "Hallucination guard":  # first drafts the checks caught: fewer is better
            if after[0][0] > before[0][0] + 1:
                reasons.append(f"{label}: {after[0][0]} first drafts caught, was {before[0][0]}")
        elif any(b < a for (a, _), (b, _) in zip(before, after)):
            reasons.append(f"{label}: {now.split(';')[0]} (was {was.split(';')[0]})")
    if reasons:
        return False, "kept the current model: " + "; ".join(reasons)
    return True, f"switched: held-out {new_ok}/{total} (was {old_ok}/{total}), recall no worse"


class Server:
    def __init__(self, base: str, token: str) -> None:
        import httpx

        self._http = httpx.Client(base_url=base.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=120)

    def get(self, path: str) -> dict:
        resp = self._http.get(path)
        resp.raise_for_status()
        return resp.json()

    def report(self, outcome: str, summary: str, through: str | None = None, examples: int = 0) -> None:
        print(f"{outcome}: {summary}")
        try:
            self._http.post("/learning/result", json={"outcome": outcome, "summary": summary, "through": through,
                                                      "examples": examples}).raise_for_status()
        except Exception as exc:  # noqa: BLE001 - the run's own log still has it
            print(f"(couldn't tell the server: {exc})")


def step(title: str, command: list[str], **kwargs) -> None:  # noqa: ANN003
    print(f"\n== {title}\n   {' '.join(command)}", flush=True)
    subprocess.run(command, check=True, **kwargs)


def free_gpu() -> None:
    """Unload Ollama's models: a loaded chat model would push training out of GPU memory."""
    try:
        listed = subprocess.run(["ollama", "ps"], capture_output=True, text=True, check=False).stdout.splitlines()[1:]
    except OSError:
        return
    for line in listed:
        name = line.split()[0] if line.split() else ""
        if name:
            subprocess.run(["ollama", "stop", name], check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--server", required=True)
    parser.add_argument("--current", default="custodian-nemotron", help="the Ollama model in use")
    parser.add_argument("--name", default="custodian-nemotron-next", help="what to install tonight's model as")
    parser.add_argument("--replay", type=int, default=240)
    parser.add_argument("--max-len", type=int, default=2944)
    parser.add_argument("--force", action="store_true", help="run even with nothing new")
    args = parser.parse_args()
    token = os.environ.get("CUSTODIAN_TOKEN", "")
    if not token:
        raise SystemExit("set CUSTODIAN_TOKEN to a signed-in session's token")
    server = Server(args.server, token)

    status = server.get("/learning")
    if not status["enabled"]:
        print("learning overnight is switched off")
        return 0
    if not status["due"] and not args.force:
        server.report("skipped", f"nothing new to learn ({status['new_examples']} new ratings, needs {status['min_new']})")
        return 0

    data_dir, out = HERE / "data", HERE / "out"
    nightly = out / "nightly"
    try:
        sys.path.insert(0, str(HERE))
        from from_feedback import write

        data = server.get("/learning/data")
        counts = write(data, data_dir)
        examples = counts["good"] + counts["corrected"]
        print(f"{examples} rated replies, {counts['pairs']} corrections as pairs")
        if not (out / "adapter" / "adapter_config.json").exists():
            server.report("failed", "there's no adapter to continue yet: run finetune\\run_finetune.ps1 once first")
            return 1
        python = sys.executable
        if not (data_dir / "train.jsonl").exists():
            step("Building the usual conversations", [python, "finetune/make_dataset.py", "--per-kind", "32"], cwd=BACKEND_DIR)

        free_gpu()
        cache = Path.home() / ".cache" / "huggingface"
        cache.mkdir(parents=True, exist_ok=True)
        mounts = ["-v", f"{HERE}:/work/finetune", "-v", f"{cache}:/cache/huggingface"]
        if nightly.exists():
            shutil.rmtree(nightly)
        step("Building the training image", ["docker", "build", "-t", IMAGE, str(HERE)])
        step("Training on your corrections", [
            "docker", "run", "--rm", "--name", CONTAINER, "--gpus", "all", "--shm-size", "2g", *mounts, IMAGE,
            "python", "finetune/train_lora.py", "--qlora", "--init-adapter", "finetune/out/adapter",
            "--feedback", "finetune/data/feedback.jsonl", "--pairs", "finetune/data/pairs.jsonl",
            "--replay", str(args.replay), "--epochs", "1", "--max-len", str(args.max_len), "--out", "finetune/out/nightly",
        ])
        step("Merging and converting", ["docker", "run", "--rm", "--name", CONTAINER, *mounts, IMAGE,
                                        "python", "finetune/export_gguf.py", "--out", "finetune/out/nightly"])
        step("Installing in Ollama", [python, "finetune/install_ollama.py", "--gguf",
                                      str(nightly / "custodian-nemotron-Q4_K_M.gguf"), "--name", args.name], cwd=BACKEND_DIR)
        step("Comparing", [python, "finetune/compare.py", "--models", args.current, args.name,
                           "--json", str(nightly / "compare.json")], cwd=BACKEND_DIR)
        switch, why = decide(json.loads((nightly / "compare.json").read_text(encoding="utf-8")), args.current, args.name)
        if switch:
            step("Switching", ["ollama", "cp", args.current, f"{args.current}-previous"])
            step("Switching", ["ollama", "cp", args.name, args.current])
            previous = out / "adapter-previous"
            if previous.exists():
                shutil.rmtree(previous)
            shutil.move(str(out / "adapter"), previous)
            shutil.copytree(nightly / "adapter", out / "adapter")  # tomorrow night continues from this one
        server.report("switched" if switch else "kept", why, through=data.get("through"), examples=examples)
        return 0
    except subprocess.CalledProcessError as exc:
        server.report("failed", f"{' '.join(exc.cmd[:3])} stopped with exit code {exc.returncode}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
