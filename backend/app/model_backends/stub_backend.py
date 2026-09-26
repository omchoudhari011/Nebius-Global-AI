"""Deterministic, offline backend — used until Nebius keys exist.
Carries forward the same echo behavior main.py used in Step 1.
Step 4 — see docs/PLAN.md.
"""

from .base import ModelBackend


class StubBackend(ModelBackend):
    def generate(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        last = messages[-1]["content"] if messages else ""
        return {"reply": f"echo: {last}", "used": {}}
