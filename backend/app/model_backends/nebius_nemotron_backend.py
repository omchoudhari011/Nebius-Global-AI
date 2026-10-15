"""Nebius Token Factory backend serving an NVIDIA Nemotron model.

An OpenAI-compatible POST {NEBIUS_BASE_URL}/chat/completions with native function
calling. Retries rate limits and server errors with backoff. Some Nemotron builds emit
reasoning in <think> tags or tool calls as <tool_call>{...}</tool_call> text instead of
structured tool_calls; both are handled here so the orchestrator always gets the same shape.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid

import httpx

from .base import ModelBackend, ModelError, ModelResponse, ToolCallRequest

log = logging.getLogger(__name__)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL | re.IGNORECASE)
RETRY_STATUSES = {429, 500, 502, 503, 504}


class NebiusNemotronBackend(ModelBackend):
    name = "nebius"

    def __init__(
        self,
        api_key: str,
        model_id: str,
        base_url: str,
        *,
        timeout: float = 60.0,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        extra_body: dict | None = None,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.extra_body = extra_body or {}
        self.max_retries = max_retries
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            # Keep the connection between chat turns: a new one can cost seconds (TLS to a
            # cloud API, or Windows trying IPv6 first for "localhost" when Ollama is on IPv4).
            limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=300.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _post(self, payload: dict) -> dict:
        last_error = "no response"
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.post("chat/completions", json=payload)
            except httpx.TimeoutException:
                last_error = "the request timed out"
            except httpx.TransportError as exc:
                last_error = f"couldn't connect ({type(exc).__name__})"
            else:
                if resp.status_code < 400:
                    try:
                        return resp.json()
                    except ValueError:
                        raise ModelError("Nebius returned a response that isn't JSON") from None
                detail = _error_detail(resp)
                if resp.status_code in (401, 403):
                    raise ModelError(f"Nebius rejected the API key ({resp.status_code}): {detail}")
                if resp.status_code not in RETRY_STATUSES:
                    raise ModelError(f"Nebius returned {resp.status_code}: {detail}")
                last_error = f"Nebius returned {resp.status_code}: {detail}"
                retry_after = resp.headers.get("Retry-After", "")
                if attempt < self.max_retries and retry_after.isdigit():
                    time.sleep(min(float(retry_after), 10.0))
                    continue
            if attempt < self.max_retries:
                time.sleep(0.5 * 2**attempt)
        raise ModelError(last_error)

    def generate(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse:
        payload: dict = {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            **self.extra_body,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        data = self._post(payload)
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise ModelError("Nebius returned no choices") from None

        content = _THINK.sub("", message.get("content") or "")
        calls = [
            ToolCallRequest(
                id=tc.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                name=(tc.get("function") or {}).get("name", ""),
                arguments=(tc.get("function") or {}).get("arguments") or "{}",
            )
            for tc in message.get("tool_calls") or []
        ]
        if not calls and "<tool_call>" in content.lower():
            calls, content = _parse_text_tool_calls(content)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return ModelResponse(
            content=content.strip(),
            tool_calls=calls,
            model=data.get("model") or self.model_id,
            prompt_tokens=_count(usage.get("prompt_tokens")),
            completion_tokens=_count(usage.get("completion_tokens")),
        )


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        detail = body.get("detail") or body.get("error") or body
        if isinstance(detail, dict):
            detail = detail.get("message") or json.dumps(detail)
        return str(detail)[:300]
    except ValueError:
        return resp.text[:300]


def _parse_text_tool_calls(content: str) -> tuple[list[ToolCallRequest], str]:
    calls: list[ToolCallRequest] = []
    for block in _TOOL_CALL.findall(content):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            log.warning("unparseable <tool_call> block from the model")
            continue
        name = data.get("name") or ""
        if name:
            calls.append(
                ToolCallRequest(id=f"call_{uuid.uuid4().hex[:12]}", name=name, arguments=data.get("arguments") or {})
            )
    return calls, _TOOL_CALL.sub("", content)
