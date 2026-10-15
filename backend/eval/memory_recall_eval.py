"""Seeds 20 facts among 300 everyday distractors (320 memories, far more than fit in one
prompt) via the same memory.write() used in production, asks 10 questions via the same
orchestrator.handle(), and reports n/10 correct.

"Recalled" means the expected memory was in the reply's context (used.memories) - that is
what the recall code controls: every memory that fits the prompt budget goes in, keyword
matches first, so this checks that the right one still gets in when they can't all fit.
With a real model (--model nebius) it also reports how many replies contain the answer.

    python eval/memory_recall_eval.py [--model stub|nebius] [--no-write]
"""

from __future__ import annotations

import argparse

from _common import backend_label, setup, update_results

MEMORIES = [
    ("semantic", "Dentist appointments are with Dr. Rao at Smile Clinic."),
    ("preference", "Prefers meetings before noon."),
    ("semantic", "Partner's name is Priya."),
    ("semantic", "Is allergic to peanuts."),
    ("semantic", "Car is a blue Honda City."),
    ("semantic", "Gym membership renews every March."),
    ("preference", "Favourite cuisine is Kerala food."),
    ("semantic", "Sister Meera lives in Toronto."),
    ("semantic", "The wifi router is in the study cupboard."),
    ("episodic", "Started taking vitamin D every morning in August."),
    ("preference", "Wants the daily summary kept short."),
    ("semantic", "Passport expires in June 2028."),
    ("episodic", "Runs 5 km on Tuesday and Thursday mornings."),
    ("semantic", "Works as a product designer at a studio in Bengaluru."),
    ("semantic", "Landlord is Mr. Sharma and rent is due on the 5th."),
    ("episodic", "Plays the guitar on weekends."),
    ("semantic", "Daughter's school pickup is at 3:30 pm."),
    ("preference", "Prefers window seats on flights."),
    ("preference", "Coffee order is a flat white with oat milk."),
    ("semantic", "Bank branch is HDFC Indiranagar."),
]

ITEMS = ["batteries", "light bulbs", "a doormat", "printer paper", "a phone charger", "tea towels",
         "a yoga mat", "a desk lamp", "coat hangers", "a water bottle"]
SHOPS = ["the hardware store", "the supermarket", "Decathlon", "the corner shop", "the mall", "the market"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DISTRACTORS = [f"Bought {item} at {shop} one {day}." for item in ITEMS for shop in SHOPS for day in DAYS][:300]

QUESTIONS = [  # (question, index of the memory that answers it, word the answer should contain)
    ("Who is my dentist?", 0, "rao"),
    ("What am I allergic to?", 3, "peanut"),
    ("Where does my sister live?", 7, "toronto"),
    ("When does my passport expire?", 11, "2028"),
    ("What car do I drive?", 4, "honda"),
    ("When is rent due?", 14, "5th"),
    ("What time is school pickup?", 16, "3:30"),
    ("What's my usual coffee order?", 18, "flat white"),
    ("Which seat do I like on flights?", 17, "window"),
    ("When do I go running?", 12, "tuesday"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["stub", "nebius"])
    parser.add_argument("--no-write", action="store_true", help="print only; don't update results.md")
    args = parser.parse_args()

    user_id = setup(args.model)
    from app import memory, orchestrator

    ids = [memory.write(user_id, text, kind) for kind, text in MEMORIES]
    for text in DISTRACTORS:  # written last, so they're the newest and fill the budget first
        memory.write(user_id, text, "episodic")
    recalled = answered = 0
    in_context: list[int] = []
    for question, index, expected in QUESTIONS:
        out = orchestrator.handle(user_id, question)
        hit = ids[index] in [m["id"] for m in out["used"]["memories"]]
        good = expected in out["reply"].lower()
        recalled += hit
        answered += good
        in_context.append(len(out["used"]["memories"]))
        print(f"[{'x' if hit else ' '}] recall  [{'x' if good else ' '}] answer  {question}  ->  {out['reply'][:80]!r}")

    label = backend_label()
    total = len(MEMORIES) + len(DISTRACTORS)
    summary = (
        f"{recalled}/{len(QUESTIONS)} questions recalled the right memory (20 facts among {total} memories; "
        f"about {round(sum(in_context) / len(in_context))} fit in each prompt, best matches first)"
    )
    if not label.startswith("stub"):
        summary += f"; {answered}/{len(QUESTIONS)} answers contained it ({label})"
    if not args.no_write:
        update_results("Memory recall accuracy", summary)
    else:
        print(summary)
    return 0 if recalled == len(QUESTIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
