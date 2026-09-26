"""Tool #1 — local, fake calendar (no OAuth needed for a demo). Step 3 — see docs/PLAN.md."""


def list_events(user_id: int) -> list[dict]:
    raise NotImplementedError("Step 3")


def create_event(user_id: int, title: str, when: str) -> dict:
    raise NotImplementedError("Step 3")
