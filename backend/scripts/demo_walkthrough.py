"""No-UI walkthrough of the whole loop against a throwaway database: recall a memory,
propose a tool call, get blocked with the scope off, succeed once it's switched on, recall
something said in passing from a new chat, and run the scheduled daily summary through the
same code path.

    python scripts/demo_walkthrough.py [--model stub|nebius]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
from _common import backend_label, setup  # noqa: E402


def show(title: str, out: dict) -> None:
    used = out["used"]
    tools = ", ".join(f"{t['tool']}.{t['function']} -> {t['status']}" for t in used["tools"]) or "none"
    matched = [m for m in used["memories"] if m["matched"]]
    memories = ", ".join(f"#{m['id']}" for m in matched) or "none matched"
    if len(used["memories"]) > len(matched):
        memories += f" (+{len(used['memories']) - len(matched)} more in context)"
    earlier = f" | earlier chats: {len(used['earlier'])}" if used["earlier"] else ""
    skill = f"{used['skill']['name']} v{used['skill']['version']}" if used["skill"] else "none"
    print(f"\n> {title}\n{out['reply']}\n  [skill: {skill} | memories: {memories}{earlier} | tools: {tools}]")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["stub", "nebius"])
    args = parser.parse_args()

    user_id = setup(args.model)
    from app import orchestrator, permissions, scheduler, tasks
    from app.demo import seed

    seed()
    print(f"Model: {backend_label()}. Every permission starts off.")

    conv = None

    def say(text: str) -> None:
        nonlocal conv
        out = orchestrator.handle(user_id, text, conversation_id=conv)
        conv = out["conversation_id"]
        show(text, out)

    say("Remember that I'm vegetarian")
    say("I lent my blue umbrella to Kavya after dinner")
    say("What's on my calendar today?")

    calendar = next(p for p in permissions.list_for_user(user_id) if p["tool_name"] == "calendar")
    permissions.grant(user_id, calendar["tool_id"], "read")
    print("\n(switched on Calendar · read)")
    say("What's on my calendar today?")
    say("Add dinner with Priya on Saturday at 8pm")  # write is still off

    conv = None
    print("\n(new chat)")
    say("Who has my blue umbrella?")  # said in passing in the first chat, never saved as a memory

    task = next(t for t in tasks.list_tasks(user_id) if t["name"] == "daily-summary")
    run = tasks.get_run(user_id, scheduler.run_task(task["id"]))
    from app import conversations

    output = conversations.get_conversation(user_id, run["conversation_id"])
    print(f"\n> Scheduled task daily-summary: run {run['id']} {run['status']}")
    print(output["messages"][-1]["content"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
