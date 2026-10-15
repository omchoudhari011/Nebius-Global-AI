"""Runs 10 sample turns through orchestrator.handle() with every permission on, then reads
the timings the app itself records - messages.latency_ms (model) and tool_calls.latency_ms
(sandboxed tools) - plus wall-clock time per turn, and reports p50/p95.

    python eval/latency_eval.py [--model stub|nebius] [--no-write]
"""

from __future__ import annotations

import argparse
import time

from _common import backend_label, p50_p95, setup, update_results

TURNS = [
    "What's on my calendar today?",
    "Add a call with Sam tomorrow at 11am",
    "Take a note: renew the car insurance before Friday",
    "Find my notes about insurance",
    "Remember that I prefer tea over coffee",
    "What's on my calendar tomorrow?",
    "Hello",
    "Give me a summary of my day",
    "Show my latest notes",
    "What do I prefer to drink?",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["stub", "nebius"])
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    user_id = setup(args.model)
    from app import db, orchestrator, permissions

    for row in permissions.list_for_user(user_id):
        permissions.grant(user_id, row["tool_id"], row["scope"])

    totals, model_ms, tool_ms = [], [], []
    conversation_id = None
    for text in TURNS:
        with db.connect() as conn:
            before_msg = conn.execute("SELECT COALESCE(MAX(id), 0) FROM messages").fetchone()[0]
            before_call = conn.execute("SELECT COALESCE(MAX(id), 0) FROM tool_calls").fetchone()[0]
        started = time.perf_counter()
        out = orchestrator.handle(user_id, text, conversation_id=conversation_id)
        totals.append((time.perf_counter() - started) * 1000)
        conversation_id = out["conversation_id"]
        with db.connect() as conn:
            model_ms.append(
                conn.execute(
                    "SELECT COALESCE(SUM(latency_ms), 0) FROM messages WHERE id > ? AND role = 'assistant'", (before_msg,)
                ).fetchone()[0]
            )
            calls = conn.execute(
                "SELECT latency_ms FROM tool_calls WHERE id > ? AND latency_ms IS NOT NULL", (before_call,)
            ).fetchall()
        tool_ms.extend(r[0] for r in calls)
        print(f"{totals[-1]:7.0f} ms  {text}")

    t50, t95 = p50_p95(totals)
    m50, m95 = p50_p95([float(v) for v in model_ms])
    c50, c95 = p50_p95([float(v) for v in tool_ms])
    summary = (
        f"turn {t50:.0f} / {t95:.0f} ms; model {m50:.0f} / {m95:.0f} ms per turn; "
        f"tool call {c50:.0f} / {c95:.0f} ms ({len(tool_ms)} sandboxed calls) - {len(TURNS)} turns, {backend_label()}"
    )
    if args.no_write:
        print(summary)
    else:
        update_results("Latency p50 / p95", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
