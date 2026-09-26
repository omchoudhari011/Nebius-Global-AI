"""Real backend — OpenAI-compatible chat completions call to Nebius Serverless,
serving an NVIDIA Nemotron model id. Returns the same shape StubBackend does, so
orchestrator.py never branches on which backend is active. Step 4 — see docs/PLAN.md.
"""

from .base import ModelBackend


class NebiusNemotronBackend(ModelBackend):
    def generate(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        raise NotImplementedError(
            "Step 4: httpx.AsyncClient().post(NEBIUS_URL, json={'model': MODEL_ID, 'messages': messages})"
        )
