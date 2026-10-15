"""Plan & usage: the plan in force, its limits and what's used, and the license key."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import db, plans, usage, vectors
from ..auth import require_session

router = APIRouter(prefix="/billing", tags=["billing"])


class LicenseRequest(BaseModel):
    key: str = Field(min_length=10, max_length=4000)


def billing_summary(user_id: int) -> dict:
    state = plans.current()
    plan = state.plan
    with db.connect() as conn:
        memories = conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ?", (user_id,)).fetchone()[0]
        active_tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'active'", (user_id,)).fetchone()[0]
    return {
        "plan": plan.to_dict(),
        "source": state.source,  # license | preview (development) | free
        "license": state.license.to_dict() if state.license else None,
        "problem": state.problem,
        "limits": {
            "memories": {"used": memories, "limit": plan.max_memories},
            "active_tasks": {"used": active_tasks, "limit": plan.max_active_tasks},
            "monthly_tokens": {"used": usage.month_tokens(user_id), "limit": plan.monthly_tokens},
            "semantic_search": plan.semantic_search,
        },
        "usage": usage.summary(user_id),
        "plans": [p.to_dict() for p in plans.PLANS.values()],
        "purchase_url": plans.PURCHASE_URL or None,
    }


@router.get("")
def get_billing(user_id: int = Depends(require_session)) -> dict:
    return billing_summary(user_id)


@router.post("/license")
def activate_license(body: LicenseRequest, user_id: int = Depends(require_session)) -> dict:
    """Check a license key offline and switch to its plan (400 when it isn't valid)."""
    plans.activate(user_id, body.key)
    vectors.start_backfill()  # search by meaning may have just been unlocked
    return billing_summary(user_id)


@router.delete("/license")
def remove_license(user_id: int = Depends(require_session)) -> dict:
    plans.deactivate(user_id)
    return billing_summary(user_id)
