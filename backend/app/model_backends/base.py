"""ModelBackend interface - the model-call role.

Both backends take OpenAI-style chat messages plus OpenAI-style tool schemas and return
a ModelResponse, so the orchestrator never branches on which backend is active.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: Any  # JSON text from the model, or an already-parsed dict

    def arguments_text(self) -> str:
        import json

        return self.arguments if isinstance(self.arguments, str) else json.dumps(self.arguments)

    def to_message(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments_text()},
        }


@dataclass
class ModelResponse:
    content: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    model: str = ""
    # Token counts as the API reported them; None when it didn't (app/usage.py estimates them).
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ModelError(RuntimeError):
    """The model backend couldn't produce a response (network, auth, rate limit, bad reply)."""


class ModelBackend(ABC):
    name: str = "base"
    model_id: str = ""

    @abstractmethod
    def generate(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse:
        ...

    def close(self) -> None:  # noqa: B027 - optional hook
        pass
