"""Turns the replies you rated in the app - only those you marked "use this to improve the
model" - into fine-tuning data, which train_lora.py adds to the synthetic conversations:

- finetune/data/feedback.jsonl: the replies to learn (each three times: real usage counts more).
  A thumbs-up keeps the reply as it was; a thumbs-down with a correction uses your correction.
- finetune/data/pairs.jsonl: each correction as a preference pair - your correction preferred
  over the reply it replaced (train_lora.py --pairs), so a mistake you corrected doesn't come back.

    python finetune/from_feedback.py                         # reads your Custodian database (backend/.env)
    python finetune/from_feedback.py --server http://127.0.0.1:8000   # or asks the server (token in
                                                             # CUSTODIAN_TOKEN): for a server in Docker

Off-the-record chats, replies whose text looks like it holds a secret, and thumbs-downs
without a correction are skipped. The files stay on this machine (finetune/data is ignored
by git) - but they hold your own conversations, so treat them like your chat history.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def from_database() -> dict:
    from app import db, feedback
    from app.config import get_settings

    get_settings()  # loads backend/.env, so this reads your real database
    db.init_db()
    return {"through": None, "examples": feedback.training_examples(), "pairs": feedback.preference_pairs()}


def from_server(server: str, token: str) -> dict:
    import httpx

    resp = httpx.get(server.rstrip("/") + "/learning/data", headers={"Authorization": f"Bearer {token}"}, timeout=120)
    if resp.status_code >= 400:
        raise SystemExit(f"the server answered {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def write(data: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("feedback.jsonl", data["examples"]), ("pairs.jsonl", data["pairs"])):
        with (out_dir / name).open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    kinds = Counter(e["kind"] for e in data["examples"])
    return {"good": kinds["feedback_good"], "corrected": kinds["feedback_corrected"], "pairs": len(data["pairs"]),
            "through": data.get("through")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--server", help="ask this Custodian server instead of reading the database (CUSTODIAN_TOKEN)")
    parser.add_argument("--out", type=Path, default=HERE / "data")
    args = parser.parse_args()
    if args.server:
        token = os.environ.get("CUSTODIAN_TOKEN", "")
        if not token:
            raise SystemExit("set CUSTODIAN_TOKEN to a signed-in session's token")
        data = from_server(args.server, token)
    else:
        data = from_database()
    counts = write(data, args.out)
    print(f"{counts['good'] + counts['corrected']} rated replies -> {args.out / 'feedback.jsonl'} "
          f"({counts['good']} good, {counts['corrected']} corrected); {counts['pairs']} preference pairs -> "
          f"{args.out / 'pairs.jsonl'}")
    if not data["examples"]:
        print("Rate replies in the app and switch on 'Use this to improve the model' to add some.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
