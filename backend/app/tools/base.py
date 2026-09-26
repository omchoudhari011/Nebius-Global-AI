"""ToolExecutor interface — the OpenShell role until its own API is confirmed.
Step 3 — see docs/PLAN.md.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable


class ToolExecutor(ABC):
    @abstractmethod
    def run(self, tool_fn: Callable, args: dict, timeout: int = 5) -> Any:
        ...
