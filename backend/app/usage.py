"""Usage metering: chat turns, task runs, model calls and tokens, per user and UTC day.

The orchestrator records every turn and every model call, with the prompt and completion
tokens the backend reports - or, when it reports none (the offline stub, some local
servers), an estimate from the text length, counted separately so the app can say the
figures are approximate. Only counts are stored, never content, and deleting chats doesn't
reset them. They feed the Plan & usage screen, cost estimates (only when MODEL_PRICE_*_PER_M
are set - an unknown price is never guessed) and the Cloud plan's monthly allowance.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import db, plans
from .config import get_settings
from .model_backends.base import ModelResponse

CHARS_PER_TOKEN = 4  # the usual rule of thumb for English text
_COUNTERS = ("chat_turns", "task_runs", "model_calls", "estimated_calls", "prompt_tokens", "completion_tokens")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _add(user_id: int, **amounts: int) -> None:
    columns = [c for c in _COUNTERS if amounts.get(c)]
    if not columns:
        return
    with db.connect() as conn:
        conn.execute(
            f"""
            INSERT INTO usage_daily (user_id, day, {', '.join(columns)}) VALUES (?, ?, {', '.join('?' * len(columns))})
            ON CONFLICT (user_id, day) DO UPDATE SET {', '.join(f'{c} = {c} + excluded.{c}' for c in columns)}
            """,
            [user_id, _today(), *(amounts[c] for c in columns)],
        )


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / CHARS_PER_TOKEN)) if text else 0


def record_turn(user_id: int, channel: str) -> None:
    _add(user_id, **{"task_runs" if channel == "task" else "chat_turns": 1})


def record_model_call(user_id: int, response: ModelResponse, sent: list[dict], tools: list[dict] | None) -> None:
    prompt, completion = response.prompt_tokens, response.completion_tokens
    estimated = prompt is None or completion is None
    if prompt is None:
        prompt = estimate_tokens(json.dumps(sent, ensure_ascii=False)) + (estimate_tokens(json.dumps(tools)) if tools else 0)
    if completion is None:
        completion = estimate_tokens(response.content) + sum(estimate_tokens(c.arguments_text()) for c in response.tool_calls)
    _add(user_id, model_calls=1, estimated_calls=int(estimated), prompt_tokens=prompt, completion_tokens=completion)


def _totals(user_id: int, since: str) -> dict:
    with db.connect() as conn:
        row = conn.execute(
            f"SELECT {', '.join(f'COALESCE(SUM({c}), 0) AS {c}' for c in _COUNTERS)} FROM usage_daily WHERE user_id = ? AND day >= ?",
            (user_id, since),
        ).fetchone()
    totals = {c: row[c] for c in _COUNTERS}
    totals["tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
    totals["cost_usd"] = _cost(totals["prompt_tokens"], totals["completion_tokens"])
    return totals


def _cost(prompt_tokens: int, completion_tokens: int) -> float | None:
    settings = get_settings()
    if settings.model_price_input_per_m is None or settings.model_price_output_per_m is None:
        return None
    return round(
        prompt_tokens / 1e6 * settings.model_price_input_per_m + completion_tokens / 1e6 * settings.model_price_output_per_m, 4
    )


def month_start() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-01")


def next_month_start() -> str:
    first = datetime.now(timezone.utc).replace(day=1)
    return (first + timedelta(days=32)).replace(day=1).strftime("%Y-%m-%d")


def month_tokens(user_id: int) -> int:
    return _totals(user_id, month_start())["tokens"]


def check_allowance(user_id: int) -> None:
    """Raise PlanLimit when the plan includes a monthly model allowance and it's used up."""
    plan = plans.current().plan
    if plan.monthly_tokens is None:
        return
    used = month_tokens(user_id)
    if used >= plan.monthly_tokens:
        raise plans.PlanLimit(
            f"This month's model allowance on the {plan.name} plan ({plan.monthly_tokens:,} tokens) is used up. "
            f"It renews on {next_month_start()}."
        )


def summary(user_id: int) -> dict:
    """Today and this month, for the Plan & usage screen."""
    settings = get_settings()
    month = _totals(user_id, month_start())
    return {
        "today": _totals(user_id, _today()),
        "month": month,
        "month_started": month_start(),
        "renews": next_month_start(),
        "estimated": month["estimated_calls"] > 0,  # some token counts are estimates, not reported figures
        "prices_set": settings.model_price_input_per_m is not None and settings.model_price_output_per_m is not None,
    }
