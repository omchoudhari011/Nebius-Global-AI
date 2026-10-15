"""ToolExecutor interface: something that runs one tool function in isolation.

tools.executor picks the implementation for each tool. `sandbox` is the subprocess
runner in sandbox.py; `openshell` and `nemoclaw` are reserved for those runtimes and
report themselves unavailable until an adapter is added.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolRequest:
    module: str  # e.g. "app.tools.calendar_tool"
    function: str  # e.g. "list_events"
    args: dict[str, Any]  # already validated against the tool's pydantic model
    context: dict[str, Any]  # user_id, data_dir, timezone - set by the gate, never by the model


class ToolTimeout(Exception):
    """The tool ran past its wall-clock limit and was killed."""


class ToolFailure(Exception):
    """The tool raised, crashed, or its executor isn't available."""


class ToolExecutor(ABC):
    @abstractmethod
    def run(self, request: ToolRequest, timeout: float) -> Any:
        """Run the tool and return its JSON-serializable result, or raise ToolTimeout / ToolFailure."""


class UnavailableExecutor(ToolExecutor):
    def __init__(self, name: str) -> None:
        self.name = name

    def run(self, request: ToolRequest, timeout: float) -> Any:
        raise ToolFailure(f"the {self.name!r} executor isn't available in this deployment")
