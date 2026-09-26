"""The agent loop — recall memory, load skill, call the model, route a proposed tool
call through the permission gate, return the result. Created in Step 2 (replacing the
main.py echo stub), extended through Step 5. See docs/PLAN.md.
"""


def handle(user_id: int, message: str | None, trigger: str = "chat") -> dict:
    raise NotImplementedError(
        "Step 2: memory.recall() -> build context -> model_backend.generate() -> return "
        "{'reply': ..., 'used': {'memories': [...], 'tools': [...], 'skill': None}}"
    )
