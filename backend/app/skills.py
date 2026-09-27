"""Versioned skill templates, loaded by name from the skills table.
orchestrator.py picks one by keyword match on the message, not a second model call.
Step 5 — see docs/PLAN.md.
"""


def load_skill(name: str) -> str:
    raise NotImplementedError(
        "Step 5: SELECT id, prompt_template FROM skills WHERE name = ? AND active = 1 "
        "ORDER BY version DESC LIMIT 1 (rows are immutable versions, see schema.sql)"
    )
