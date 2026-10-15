"""Plans: what each tier includes, and which one this instance is on.

The business model is open core, built around privacy rather than against it:

  Free   self-hosted with your own model: the lifetime memory with keyword search, calendar
         and notes, one scheduled task, up to 500 memories.
  Pro    a license key for your self-hosted server: search by meaning, up to 25 scheduled
         tasks, no memory limit.
  Cloud  we run the server and pay for the model: everything in Pro, plus a monthly model
         allowance (usage is metered by app/usage.py).

Export, erase, off-the-record chats, chat retention, withholding secrets from the model,
encryption and the accuracy checks are in every plan: privacy is never the upsell. Licenses
are verified offline (app/licensing.py), so no plan ever phones home.

Which plan applies - current():
  1. a valid license key: CUSTODIAN_LICENSE_KEY, else the key activated in the app
  2. in development, CUSTODIAN_PLAN previews a plan (default pro, so every feature works
     while you build)
  3. otherwise Free

Prices are what the app shows; change them here. CUSTODIAN_PURCHASE_URL is where "Get a
license" sends people (your store, see store/README.md); unset hides the link.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import db, licensing
from .config import get_settings

log = logging.getLogger(__name__)

def purchase_url() -> str | None:
    """Where "Get a license" sends people (CUSTODIAN_PURCHASE_URL), or None to hide the link."""
    return os.getenv("CUSTODIAN_PURCHASE_URL", "").strip() or None


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    price: str
    summary: str
    semantic_search: bool  # search memories and past chats by meaning, not just keywords
    max_memories: int | None  # None = no limit
    max_active_tasks: int | None
    monthly_tokens: int | None  # model tokens included per month; None = your own model, unmetered
    features: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "price": self.price,
            "summary": self.summary,
            "semantic_search": self.semantic_search,
            "max_memories": self.max_memories,
            "max_active_tasks": self.max_active_tasks,
            "monthly_tokens": self.monthly_tokens,
            "features": list(self.features),
        }


EVERY_PLAN = "Export, erase, off-the-record chats and secret withholding"

PLANS: dict[str, Plan] = {
    "free": Plan(
        id="free",
        name="Free",
        price="$0",
        summary="Self-hosted, with your own model",
        semantic_search=False,
        max_memories=500,
        max_active_tasks=1,
        monthly_tokens=None,
        features=("Lifetime memory with keyword search", "Calendar and notes", "1 scheduled task", "Up to 500 memories", EVERY_PLAN),
    ),
    "pro": Plan(
        id="pro",
        name="Pro",
        price="$5 / month, billed yearly",
        summary="Self-hosted, every feature",
        semantic_search=True,
        max_memories=None,
        max_active_tasks=25,
        monthly_tokens=None,
        features=("Search by meaning across memories and past chats", "Up to 25 scheduled tasks", "Unlimited memories", EVERY_PLAN),
    ),
    "cloud": Plan(
        id="cloud",
        name="Cloud",
        price="$12 / month",
        summary="We host it; the model is included",
        semantic_search=True,
        max_memories=None,
        max_active_tasks=25,
        monthly_tokens=5_000_000,
        features=("Everything in Pro", "Hosted and backed up for you", "5M model tokens a month included", EVERY_PLAN),
    ),
}


class PlanLimit(ValueError):
    """The action needs a higher plan; the message says which limit and how to lift it."""


@dataclass(frozen=True)
class PlanState:
    plan: Plan
    source: str  # 'license', 'preview' (development) or 'free'
    license: licensing.License | None = None
    problem: str | None = None  # why a license that was supplied isn't in use


_CACHE_SECONDS = 30.0
_lock = threading.Lock()
_cache: tuple[float, Path, PlanState] | None = None


def _stored_key() -> str | None:
    """The key activated in the app (single-user: the one settings row that has one)."""
    try:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT license_key FROM user_settings WHERE license_key IS NOT NULL ORDER BY user_id LIMIT 1"
            ).fetchone()
    except sqlite3.Error:  # no schema yet (first start, some tests)
        return None
    return row["license_key"] if row else None


def check_license(key: str) -> licensing.License:
    """Parse a key and make sure it's for a paid plan this build knows; raises LicenseError."""
    lic = licensing.parse(key)
    if lic.plan not in PLANS or lic.plan == "free":
        raise licensing.LicenseError(f"That license is for a plan this version doesn't know ({lic.plan!r}); update Custodian.")
    return lic


def _resolve() -> PlanState:
    settings = get_settings()
    problems: list[str] = []
    for key, where in ((settings.license_key, "CUSTODIAN_LICENSE_KEY"), (_stored_key(), "the app")):
        if not key:
            continue
        try:
            lic = check_license(key)
        except licensing.LicenseError as exc:
            problems.append(f"The license key from {where} isn't in use: {exc}")
            continue
        if lic.status() == "expired":
            problems.append(f"The {PLANS[lic.plan].name} license for {lic.licensee} expired on {lic.expires}.")
            continue
        return PlanState(PLANS[lic.plan], "license", lic)
    problem = problems[0] if problems else None
    if not settings.is_production:
        return PlanState(PLANS[settings.plan_preview or "pro"], "preview", problem=problem)
    return PlanState(PLANS["free"], "free", problem=problem)


def current() -> PlanState:
    """The plan in force, re-checked every 30 seconds (and at once after reset())."""
    global _cache
    path = db.db_path()
    now = time.monotonic()
    with _lock:
        if _cache is not None and _cache[1] == path and now - _cache[0] < _CACHE_SECONDS:
            return _cache[2]
    state = _resolve()
    with _lock:
        _cache = (now, path, state)
    return state


def reset() -> None:
    """Forget the cached plan (after a license change; tests)."""
    global _cache
    with _lock:
        _cache = None


def activate(user_id: int, key: str) -> PlanState:
    """Check a license key and keep it; raises LicenseError for a bad or expired one."""
    lic = check_license(key)
    if lic.status() == "expired":
        raise licensing.LicenseError(f"That license expired on {lic.expires}.")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO user_settings (user_id, license_key) VALUES (?, ?)
            ON CONFLICT (user_id) DO UPDATE SET license_key = excluded.license_key, updated_at = datetime('now')
            """,
            (user_id, "".join(key.split())),
        )
    reset()
    log.info("activated a %s license (%s)", lic.plan, lic.id)
    return current()


def deactivate(user_id: int) -> PlanState:
    with db.connect() as conn:
        conn.execute("UPDATE user_settings SET license_key = NULL, updated_at = datetime('now') WHERE user_id = ?", (user_id,))
    reset()
    return current()


# Limits ---------------------------------------------------------------------------------

def upgrade_hint(plan: Plan) -> str:
    """How to get past a limit of `plan`, as a sentence."""
    return "Pro lifts this limit: see Account." if plan.id == "free" else "Contact us if you need more."


def check_memory_room(conn: sqlite3.Connection, user_id: int) -> None:
    """Before a new memory is stored: raise PlanLimit when the plan's memory limit is reached."""
    plan = current().plan
    if plan.max_memories is None:
        return
    count = conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ?", (user_id,)).fetchone()[0]
    if count >= plan.max_memories:
        raise PlanLimit(
            f"The {plan.name} plan keeps up to {plan.max_memories} memories and you have {count}; "
            f"delete some in Tools & Data to make room. {upgrade_hint(plan)}"
        )


def check_task_room(conn: sqlite3.Connection, user_id: int, *, exclude_task_id: int | None = None) -> None:
    """Before a task becomes active: raise PlanLimit when the plan's active-task limit is reached."""
    plan = current().plan
    if plan.max_active_tasks is None:
        return
    count = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'active' AND id IS NOT ?", (user_id, exclude_task_id)
    ).fetchone()[0]
    if count >= plan.max_active_tasks:
        noun = "task" if plan.max_active_tasks == 1 else "tasks"
        raise PlanLimit(
            f"The {plan.name} plan runs {plan.max_active_tasks} scheduled {noun} at a time; pause one first. "
            f"{upgrade_hint(plan)}"
        )


def runnable_task_ids(conn: sqlite3.Connection, user_id: int) -> set[int] | None:
    """After a downgrade a user can have more active tasks than the plan allows; the oldest
    ones keep running and the rest are skipped (never deleted). None = no limit."""
    plan = current().plan
    if plan.max_active_tasks is None:
        return None
    rows = conn.execute(
        "SELECT id FROM tasks WHERE user_id = ? AND status = 'active' ORDER BY id LIMIT ?", (user_id, plan.max_active_tasks)
    ).fetchall()
    return {r["id"] for r in rows}


def semantic_search_allowed() -> bool:
    return current().plan.semantic_search
