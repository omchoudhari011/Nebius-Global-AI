"""Tool #2 — local notes. Same gated pattern as calendar_tool.py — proves the tool
interface generalizes rather than being a one-off. Step 5 — see docs/PLAN.md.
"""


def create_note(user_id: int, title: str, body: str) -> dict:
    raise NotImplementedError("Step 5")


def read_notes(user_id: int, query: str) -> list[dict]:
    raise NotImplementedError("Step 5")
