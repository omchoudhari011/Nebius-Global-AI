"""Lifetime chat recall: can a brand-new chat use something the user only mentioned in
passing in an old conversation - never saved as a memory?

Seeds 40 past conversations spread over the last 60 days: 10 facts said in passing, mixed
in with everyday chatter and earlier questions, encrypted exactly as production stores
messages. Then asks 10 questions, each in a new chat, via the same orchestrator.handle().

"Recalled" means the message with the fact was in the reply's context (used.earlier) - what
app/history_search.py controls. With a real model (--model nebius) it also reports how many
replies contain the answer.

    python eval/lifetime_recall_eval.py [--model stub|nebius] [--no-write]
"""

from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone

from _common import backend_label, setup, update_results

FACTS = [  # (said in passing, question asked weeks later in a new chat, word the answer should contain)
    ("I parked on level 3 at pillar B7 in the Phoenix mall car park", "Where did I park at Phoenix mall?", "b7"),
    ("Kavya borrowed my blue umbrella after dinner", "Who has my umbrella?", "kavya"),
    ("the plumber Ramesh said the tap washer we need is 15mm", "What size tap washer do we need?", "15"),
    ("I started reading The Midnight Library on the flight home", "Which book am I reading?", "midnight"),
    ("my daughter's piano exam is on 14 November", "When is the piano exam?", "november"),
    ("we booked the Goa trip for the second week of December", "When is our Goa trip?", "december"),
    ("the car service at the Honda workshop cost 8,400 rupees", "How much did the car service cost?", "8,400"),
    ("a film camera would be a great birthday gift for Arjun", "What should I get Arjun for his birthday?", "camera"),
    ("Mrs. Iyer next door keeps our spare house key", "Who keeps our spare house key?", "iyer"),
    ("the electricity bill is paid from the joint account on the 12th", "Which account pays the electricity bill?", "joint"),
]

CHATTER = [
    "thanks, that helps", "the weather is lovely today", "let's plan the week", "I had a long day at work",
    "lunch was great", "remind me to drink more water", "I'm going for a walk", "the traffic was terrible",
    "good morning", "I finished the report", "my phone battery died again", "we watched a movie last night",
    "I need to clean the kitchen", "the meeting ran late", "I'm feeling tired", "dinner is at eight tonight",
    "the kids have a holiday tomorrow", "I tried a new recipe", "the internet was slow this morning",
    "I want to start exercising", "my back hurts a little", "it rained all afternoon",
]
EARLIER_QUESTIONS = ["what's on my calendar today?", "can you help me plan dinner", "what time is it in London?",
                     "how do I make chai?", "do you remember what I said yesterday"]


def seed(user_id: int, facts: list[tuple[str, str, str]] = FACTS) -> dict[int, int]:
    """Writes the past conversations; returns {fact index: message id}."""
    from app import db
    from app.crypto import encrypt

    rng = random.Random(7)
    now = datetime.now(timezone.utc)
    days_ago = sorted(rng.sample(range(2, 60), 40), reverse=True)
    fact_at = dict(zip(rng.sample(range(40), len(facts)), range(len(facts))))  # conversation -> fact
    ids: dict[int, int] = {}
    with db.connect() as conn:
        for n, ago in enumerate(days_ago):
            when = now - timedelta(days=ago, hours=rng.randint(0, 10))
            stamp = db.to_db_time(when)
            cid = conn.execute(
                "INSERT INTO conversations (user_id, channel, started_at) VALUES (?, 'chat', ?)", (user_id, stamp)
            ).lastrowid
            lines = rng.sample(CHATTER, 4) + [rng.choice(EARLIER_QUESTIONS)]
            if n in fact_at:
                lines.insert(rng.randint(0, len(lines)), facts[fact_at[n]][0])
            for i, text in enumerate(lines):
                at = db.to_db_time(when + timedelta(minutes=2 * i))
                mid = conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
                    (cid, encrypt(text), at),
                ).lastrowid
                conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
                    (cid, encrypt("Okay."), at),
                )
                if n in fact_at and text == facts[fact_at[n]][0]:
                    ids[fact_at[n]] = mid
    return ids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["stub", "nebius"])
    parser.add_argument("--no-write", action="store_true", help="print only; don't update results.md")
    args = parser.parse_args()

    user_id = setup(args.model)
    from app import db, orchestrator

    fact_ids = seed(user_id)
    with db.connect() as conn:
        stored = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    recalled = answered = 0
    for index, (_, question, expected) in enumerate(FACTS):
        out = orchestrator.handle(user_id, question)  # a new chat every time
        hit = fact_ids[index] in [e["id"] for e in out["used"]["earlier"]]
        good = expected in out["reply"].lower()
        recalled += hit
        answered += good
        print(f"[{'x' if hit else ' '}] recall  [{'x' if good else ' '}] answer  {question}  ->  {out['reply'][:80]!r}")

    label = backend_label()
    summary = (
        f"{recalled}/{len(FACTS)} questions asked in a new chat found the right earlier message "
        f"({len(FACTS)} facts said in passing, never saved as memories, among {stored} messages in 40 chats over 60 days)"
    )
    if not label.startswith("stub"):
        summary += f"; {answered}/{len(FACTS)} answers contained it ({label})"
    if not args.no_write:
        update_results("Lifetime chat recall", summary)
    else:
        print(summary)
    return 0 if recalled == len(FACTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
