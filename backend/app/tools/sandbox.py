"""Subprocess-based ToolExecutor: wall-clock timeout, no outbound network by default.
permissions.check() is called here, immediately before execution — never trusted
from the model's own output. Args are validated against the tool's pydantic schema
first; no eval/exec/shell=True anywhere. Step 3 — see docs/PLAN.md.
"""

from typing import Any, Callable

from .base import ToolExecutor


class SubprocessSandbox(ToolExecutor):
    def run(self, tool_fn: Callable, args: dict, timeout: int = 5) -> Any:
        raise NotImplementedError(
            "Step 3: validate args, permissions.check() plus the current skill's skill_tools allowlist, "
            "then call tool_fn(**args) under timeout; record every attempt, blocked or not, in tool_calls"
        )
