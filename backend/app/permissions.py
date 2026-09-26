"""PermissionGate — default-deny per-user, per-tool, per-scope grants.
Called from inside app/tools/sandbox.py, never trusted from the model's own output.
Step 3 — see docs/PLAN.md.
"""


def check(user_id: int, tool_id: int, scope: str) -> bool:
    raise NotImplementedError("Step 3: parameterized SELECT granted FROM permissions WHERE ...; no row -> False")


def grant(user_id: int, tool_id: int, scope: str) -> None:
    raise NotImplementedError("Step 3: upsert into permissions")


def revoke(user_id: int, tool_id: int, scope: str) -> None:
    raise NotImplementedError("Step 3: upsert into permissions")
