"""ModelBackend interface — the Hermes Agent / model-call role.
Matched implicitly by the Step 1 echo stub, formalized here in Step 4.
See docs/PLAN.md.
"""

from abc import ABC, abstractmethod


class ModelBackend(ABC):
    @abstractmethod
    def generate(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Returns {'reply': str, 'used': dict} or a proposed tool call in the same shape."""
        ...
