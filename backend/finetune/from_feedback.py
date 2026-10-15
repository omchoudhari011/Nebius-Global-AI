"""Turns the replies you rated in the app - only those you marked "use this to improve the
model" - into fine-tuning data: finetune/data/feedback.jsonl, which train_lora.py adds to the
synthetic conversations (each one three times: real usage counts more).

    python finetune/from_feedback.py          # reads your Custodian database (backend/.env)

A thumbs-up keeps the reply as it was; a thumbs-down with a correction uses your correction.
Off-the-record chats, replies whose text looks like it holds a secret, and thumbs-downs
without a correction are skipped. The file stays on this machine (finetune/data is ignored
by git) - but it holds your own conversations, so treat it like your chat history.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    from app import db, feedback
    from app.config import get_settings

    get_settings()  # loads backend/.env, so this reads your real database
    db.init_db()
    examples = feedback.training_examples()
    out = HERE / "data" / "feedback.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for example in examples:
            f.write(json.dumps(example, ensure_ascii=False) + "\n")
    kinds = Counter(e["kind"] for e in examples)
    print(f"{len(examples)} rated replies -> {out} ({kinds['feedback_good']} good, {kinds['feedback_corrected']} corrected)")
    if not examples:
        print("Rate replies in the app and switch on 'Use this to improve the model' to add some.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
