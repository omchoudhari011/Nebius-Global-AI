"""Paraphrase recall: can a new chat find something said weeks ago when the question shares
no keywords with it? That is what search by meaning adds to keyword search.

Seeds 40 past conversations over 60 days - 10 facts said in passing among everyday chatter,
encrypted as production stores them - embeds them, then asks 10 questions worded
differently from their facts ("Who has my brolly?" for "I lent my blue umbrella to Kavya"),
each in a new chat. Reports how many found the fact with keyword search alone and with
hybrid search, how many earlier messages small talk pulls in (noise), and, with a real model
(--model nebius), how many answers contained the fact.

Needs EMBEDDING_MODEL (e.g. nomic-embed-text in Ollama); without one it's skipped.

    python eval/semantic_recall_eval.py [--model stub|nebius] [--no-write]
"""

from __future__ import annotations

import argparse
import time

from _common import backend_label, setup, update_results
from lifetime_recall_eval import seed

FACTS = [  # (said in passing, question with no keyword in common, word the answer should contain)
    ("I lent my blue umbrella to Kavya after dinner", "Who has my brolly?", "kavya"),
    ("I parked on level 3 at pillar B7 in the Phoenix mall car park", "Where did I leave the vehicle?", "b7"),
    ("my daughter's piano exam is on 14 November", "When is my kid's music test?", "november"),
    ("the plumber Ramesh said the tap washer we need is 15mm", "What size spare does the leaking faucet take?", "15"),
    ("we booked the Goa trip for the second week of December", "When is our beach holiday?", "december"),
    ("Mrs. Iyer next door keeps our spare house key", "Who can let me in if I get locked out?", "iyer"),
    ("I started reading The Midnight Library on the flight home", "Which novel am I in the middle of?", "midnight"),
    ("the car service at the Honda workshop cost 8,400 rupees", "How much did the vehicle maintenance bill come to?", "8,400"),
    ("my sister Meera moved to Toronto last spring", "Which city does my sibling live in now?", "toronto"),
    ("I'm allergic to shellfish, especially prawns", "Is there any seafood I should avoid?", "shellfish"),
]
SMALL_TALK = ["hello", "tell me a joke", "thanks, that's all", "good night", "how are you today?"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["stub", "nebius"])
    parser.add_argument("--no-write", action="store_true", help="print only; don't update results.md")
    args = parser.parse_args()

    user_id = setup(args.model)
    from app import db, embeddings, history_search, orchestrator, vectors

    embedder = embeddings.get_embedder()
    if embedder is None:
        print("skipped: set EMBEDDING_MODEL (e.g. nomic-embed-text in Ollama) to measure search by meaning")
        return 0

    fact_ids = seed(user_id, FACTS)
    started = time.perf_counter()
    while vectors.index_missing(256):
        pass
    with db.connect() as conn:
        stored = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    print(f"embedded {stored // 2} user messages with {embedder.model} in {time.perf_counter() - started:.1f} s")

    keyword_only = sum(
        fact_ids[i] in [hit["id"] for hit in history_search.search(user_id, question)]
        for i, (_, question, _) in enumerate(FACTS)
    )
    recalled = answered = 0
    for i, (_, question, expected) in enumerate(FACTS):
        out = orchestrator.handle(user_id, question)  # a new chat every time
        hit = fact_ids[i] in [e["id"] for e in out["used"]["earlier"]]
        good = expected in out["reply"].lower()
        recalled += hit
        answered += good
        print(f"[{'x' if hit else ' '}] recall  [{'x' if good else ' '}] answer  {question}  ->  {out['reply'][:70]!r}")
    noise_keyword = [len(history_search.search(user_id, line)) for line in SMALL_TALK]
    noise = [len(orchestrator.handle(user_id, line)["used"]["earlier"]) for line in SMALL_TALK]

    label = backend_label()
    summary = (
        f"{recalled}/{len(FACTS)} found with keyword + meaning search vs {keyword_only}/{len(FACTS)} with keywords alone "
        f"(questions worded differently from facts said in passing, among {stored} messages in 40 chats over 60 days; "
        f"{embedder.model}); small talk pulled in {sum(noise) / len(noise):.1f} earlier messages on average "
        f"({sum(noise_keyword) / len(noise_keyword):.1f} with keywords alone)"
    )
    if not label.startswith("stub"):
        summary += f"; {answered}/{len(FACTS)} answers contained the fact ({label})"
    if not args.no_write:
        update_results("Paraphrase recall", summary)
    else:
        print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
