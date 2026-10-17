import json

import httpx
import pytest

from app.model_backends import ModelError
from app.model_backends.nebius_nemotron_backend import NebiusNemotronBackend


def make(handler, **kwargs):
    return NebiusNemotronBackend(
        "key-123",
        "nvidia/nemotron-3-super-120b-a12b",
        "https://api.example.test/v1",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def reply(message, status=200, headers=None):
    return httpx.Response(status, json={"model": "nvidia/nemotron-3-super-120b-a12b", "choices": [{"message": message}]}, headers=headers)


def test_sends_openai_request_and_parses_tool_calls():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return reply(
            {
                "content": None,
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "calendar_list_events", "arguments": "{}"}}],
            }
        )

    backend = make(handler, extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    tools = [{"type": "function", "function": {"name": "calendar_list_events", "parameters": {"type": "object"}}}]
    out = backend.generate([{"role": "user", "content": "hi"}], tools)
    assert seen["url"] == "https://api.example.test/v1/chat/completions"
    assert seen["auth"] == "Bearer key-123"
    assert seen["body"]["tools"] == tools and seen["body"]["tool_choice"] == "auto"
    assert seen["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert out.tool_calls[0].name == "calendar_list_events" and out.content == ""


def test_strips_reasoning_and_parses_text_tool_calls():
    content = '<think>plan...</think>Sure.<tool_call>{"name": "notes_read_notes", "arguments": {"query": "milk"}}</tool_call>'
    out = make(lambda r: reply({"content": content})).generate([{"role": "user", "content": "x"}], [])
    assert out.content == "Sure."
    assert out.tool_calls[0].name == "notes_read_notes"
    assert json.loads(out.tool_calls[0].arguments_text()) == {"query": "milk"}


def test_retries_rate_limits(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(429, json={"detail": "slow down"}, headers={"Retry-After": "1"})
        return reply({"content": "done"})

    assert make(handler).generate([{"role": "user", "content": "x"}]).content == "done"
    assert len(attempts) == 3


def test_an_unparseable_tool_call_is_asked_again_with_other_sampling(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: pytest.fail("no waiting: the server is fine, the output wasn't"))
    temperatures = []

    def handler(request):
        temperatures.append(json.loads(request.content)["temperature"])
        if len(temperatures) == 1:
            return httpx.Response(500, json={"error": {"message": "XML syntax error on line 3: unexpected EOF"}})
        return reply({"content": "done"})

    assert make(handler, temperature=0.3).generate([{"role": "user", "content": "x"}]).content == "done"
    assert temperatures[0] == 0.3 and temperatures[1] > 0.3


def test_auth_errors_are_not_retried():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(401, json={"detail": "bad key"})

    with pytest.raises(ModelError, match="API key"):
        make(handler).generate([{"role": "user", "content": "x"}])
    assert len(attempts) == 1


def test_network_failure_becomes_model_error(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)

    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(ModelError, match="couldn't connect"):
        make(handler).generate([{"role": "user", "content": "x"}])
