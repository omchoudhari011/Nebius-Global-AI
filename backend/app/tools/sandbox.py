"""The tool gate: every tool call the model proposes goes through execute().

1. Log the attempt as a tool_calls row (status 'running') before any check, so blocked
   attempts are on record too. The arguments are stored encrypted, exactly as proposed.
2. Validate the arguments against the tool's pydantic model -> 'rejected' on failure.
3. Allowed? The tool must be enabled, the user's switch on (permissions.check(), read
   fresh right here, never from the model's output) and the current skill must list
   the scope (skill_tools) -> 'denied' otherwise.
4. Run it with the executor named in tools.executor. SubprocessSandbox runs the tool in
   a separate Python process (runner.py) with a wall-clock timeout, a scrubbed
   environment and no network -> 'succeeded', 'failed' or 'timed_out'.
5. Write the outcome, latency and encrypted result to the same row.

No eval/exec/shell=True anywhere.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from .. import db, permissions
from ..config import get_settings
from ..crypto import decrypt, encrypt, encrypt_json
from . import registry
from .base import ToolExecutor, ToolFailure, ToolRequest, ToolTimeout, UnavailableExecutor

log = logging.getLogger(__name__)

RUNNER = Path(__file__).resolve().parent / "runner.py"
MAX_OUTPUT_BYTES = 1_000_000
NETWORK_TIMEOUT_SECONDS = 25  # tools that call an outside service (several requests each)


class SubprocessSandbox(ToolExecutor):
    """Runs a tool in `python -I -B runner.py`: isolated mode, scrubbed env, wall-clock timeout."""

    def run(self, request: ToolRequest, timeout: float) -> Any:
        data_dir = Path(request.context["data_dir"])
        data_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "module": request.module,
                "function": request.function,
                "args": request.args,
                "context": request.context,
                "timeout": timeout,
            },
            default=str,
        ).encode("utf-8")
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [sys.executable, "-I", "-B", str(RUNNER)],
                input=payload,
                capture_output=True,
                timeout=timeout,
                env=_scrubbed_env(),
                cwd=data_dir,
                check=False,
                **kwargs,
            )
        except subprocess.TimeoutExpired:
            raise ToolTimeout(f"the tool took longer than {timeout:g} s and was stopped") from None

        if proc.stderr:
            log.debug("sandbox stderr for %s.%s: %s", request.module, request.function, proc.stderr[-2000:])
        if len(proc.stdout) > MAX_OUTPUT_BYTES:
            raise ToolFailure("the tool returned more than 1 MB of output")
        try:
            reply = json.loads(proc.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
            raise ToolFailure(f"the sandbox exited with code {proc.returncode}: {tail[0][:300]}") from None
        if not reply.get("ok"):
            raise ToolFailure(str(reply.get("error") or "the tool failed")[:500])
        return reply.get("result")


def _scrubbed_env() -> dict[str, str]:
    """Only what Python itself needs. API keys, APP_SECRET_KEY and SESSION_SECRET stay out."""
    env = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    for name in ("SYSTEMROOT", "WINDIR"):  # Windows can't start Python without these
        if os.environ.get(name):
            env[name] = os.environ[name]
    return env


class DesktopExecutor(ToolExecutor):
    """Runs a PC action through the Custodian desktop app (app/desktop.py). A screenshot comes
    back as an image and is turned into a description by the vision model, when one is set."""

    def run(self, request: ToolRequest, timeout: float) -> Any:
        from .. import desktop

        ctx = request.context
        try:
            result = desktop.run(ctx["user_id"], request.function, request.args,
                                 confirm=bool(ctx.get("confirm")), description=str(ctx.get("description", "")))
        except desktop.DesktopUnavailable as exc:
            raise ToolFailure(str(exc)) from None
        except TimeoutError as exc:
            raise ToolTimeout(str(exc)) from None
        except RuntimeError as exc:
            raise ToolFailure(str(exc)) from None
        if isinstance(result, dict) and result.get("image_b64"):
            result = _describe_screenshot(result)
        return result


def _describe_screenshot(result: dict) -> dict:
    from .. import documents

    image = result.pop("image_b64")
    try:
        import base64

        result["description"] = documents._image_text(base64.b64decode(image), "image/png")
    except Exception as exc:  # noqa: BLE001 - no vision model, or it failed: say so
        result["description"] = f"(The screen couldn't be described: {exc})"
    return result


DESKTOP = DesktopExecutor()

EXECUTORS: dict[str, ToolExecutor] = {
    "sandbox": SubprocessSandbox(),
    "openshell": UnavailableExecutor("openshell"),
    "nemoclaw": UnavailableExecutor("nemoclaw"),
}


@dataclass
class ToolCallOutcome:
    name: str  # what the model asked for
    status: str  # rejected | denied | succeeded | failed | timed_out | unknown
    tool_call_id: int | None = None  # None only for 'unknown' (no tool_scopes row to log against)
    tool: str | None = None
    function: str | None = None
    scope: str | None = None
    result: Any = None
    error: str | None = None
    latency_ms: int | None = None

    def to_model_content(self) -> str:
        """The tool message the model reads next."""
        body: dict[str, Any] = {"tool": self.tool, "function": self.function, "status": self.status}
        if self.result is not None:
            body["result"] = self.result
        if self.error:
            body["error"] = self.error
        return json.dumps(body, ensure_ascii=False, default=str)

    def summary(self) -> dict:
        return {
            "id": self.tool_call_id,
            "tool": self.tool,
            "function": self.function,
            "scope": self.scope,
            "status": self.status,
            "error": self.error,
        }


def _validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors()[:5]:
            where = ".".join(str(p) for p in err.get("loc", ())) or "arguments"
            parts.append(f"{where}: {err.get('msg', 'invalid')}")
        return "invalid arguments - " + "; ".join(parts)
    return f"invalid arguments - {exc}"


def _finish(call_id: int, status: str, *, result: Any = None, error: str | None = None, latency_ms: int | None = None) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE tool_calls
            SET status = ?, result = ?, error = ?, latency_ms = ?, finished_at = datetime('now')
            WHERE id = ?
            """,
            (status, encrypt_json(result) if status == "succeeded" else None, error, latency_ms, call_id),
        )


def execute(
    user_id: int,
    name: str,
    raw_args: Any,
    *,
    message_id: int,
    allowed: frozenset[tuple[str, str]] | None,
    skill_name: str | None = None,
) -> ToolCallOutcome:
    """Gate and run one proposed tool call. `allowed` is the current skill's
    (tool, scope) allowlist; None means no skill is in force (every scope allowed)."""
    fn = registry.find(name)
    if fn is None:
        log.warning("model proposed unknown tool %r", name)
        return ToolCallOutcome(name=name, status="unknown", error=f"There is no tool called {name!r}.")

    args_text = raw_args if isinstance(raw_args, str) else json.dumps(raw_args, ensure_ascii=False, default=str)
    with db.connect() as conn:
        tool = conn.execute("SELECT id, enabled, executor FROM tools WHERE name = ?", (fn.tool,)).fetchone()
        if tool is None:
            return ToolCallOutcome(name=name, status="unknown", error=f"The {fn.tool} tool isn't installed.")
        call_id = conn.execute(
            """
            INSERT INTO tool_calls (message_id, tool_id, scope, function, args, status)
            VALUES (?, ?, ?, ?, ?, 'running')
            """,
            (message_id, tool["id"], fn.scope, fn.function, encrypt(args_text)),
        ).lastrowid
    outcome = ToolCallOutcome(name=name, status="running", tool_call_id=call_id, tool=fn.tool, function=fn.function, scope=fn.scope)

    # 1. Arguments
    try:
        parsed = json.loads(args_text) if args_text.strip() else {}
        if not isinstance(parsed, dict):
            raise ValueError("arguments must be a JSON object")
        validated = fn.args_model.model_validate(registry.fix_arguments(fn, parsed))
    except (ValueError, ValidationError) as exc:
        outcome.status, outcome.error = "rejected", _validation_message(exc)
        _finish(call_id, outcome.status, error=outcome.error)
        return outcome

    # 2. Allowed? Checked fresh, immediately before running.
    label = f"{registry.display_name(fn.tool)} · {fn.scope}"
    if not tool["enabled"]:
        reason = f"The {fn.tool} tool is switched off on this server."
    elif not permissions.check(user_id, tool["id"], fn.scope):
        reason = f"Permission denied: the user hasn't switched on “{label}” in Tools & Data."
    elif allowed is not None and (fn.tool, fn.scope) not in allowed:
        reason = f"The {skill_name or 'current'} skill isn't allowed to use {fn.tool}:{fn.scope}."
    else:
        reason = None
    if reason:
        outcome.status, outcome.error = "denied", reason
        _finish(call_id, outcome.status, error=reason)
        return outcome

    # 3. Run. A tool that works on a connected account gets a short-lived access token (never
    #    the refresh token) and may reach only that service's hosts.
    settings = get_settings()
    context: dict[str, Any] = {
        "user_id": user_id,
        "data_dir": str(settings.tool_data_dir / fn.tool),
        "timezone": settings.timezone,
    }
    timeout = settings.tool_timeout_seconds
    if fn.account == "google":
        from .. import google_account

        try:
            context["access_token"] = google_account.access_token(user_id)
        except google_account.GoogleError as exc:
            outcome.status, outcome.error = "denied", str(exc)
            _finish(call_id, outcome.status, error=outcome.error)
            return outcome
        context["allow_hosts"] = list(fn.hosts)
        timeout = max(timeout, NETWORK_TIMEOUT_SECONDS)
    elif fn.hosts:  # the web tools: the local SearXNG, or any public page
        searx = os.getenv("SEARXNG_URL", "").strip() or "http://127.0.0.1:8888"
        context["searx_url"] = searx
        context["allow_hosts"] = [urlsplit(searx).hostname if h == "searxng" else h for h in fn.hosts]
        timeout = max(timeout, NETWORK_TIMEOUT_SECONDS)
    if fn.runs_on == "desktop":
        context["confirm"] = fn.confirm
        context["description"] = fn.description
    request = ToolRequest(module=fn.module, function=fn.function, args=validated.model_dump(mode="json"), context=context)
    executor = DESKTOP if fn.runs_on == "desktop" else EXECUTORS.get(tool["executor"]) or UnavailableExecutor(tool["executor"])
    started = time.perf_counter()
    try:
        outcome.result = executor.run(request, timeout)
        outcome.status = "succeeded"
    except ToolTimeout as exc:
        outcome.status, outcome.error = "timed_out", str(exc)
    except ToolFailure as exc:
        outcome.status, outcome.error = "failed", str(exc)
    except Exception as exc:  # noqa: BLE001 - recorded on the row, never raised into the chat
        log.exception("tool %s.%s crashed", fn.tool, fn.function)
        outcome.status, outcome.error = "failed", f"{type(exc).__name__}: {exc}"[:500]
    outcome.latency_ms = int((time.perf_counter() - started) * 1000)
    _finish(call_id, outcome.status, result=outcome.result, error=outcome.error, latency_ms=outcome.latency_ms)
    return outcome


def recover_stale() -> int:
    """Calls left 'running' by a crashed process can't finish any more; mark them failed."""
    with db.connect() as conn:
        cur = conn.execute(
            """
            UPDATE tool_calls SET status = 'failed', error = 'interrupted: the server restarted',
                                  finished_at = datetime('now')
            WHERE status = 'running'
            """
        )
    return cur.rowcount


def list_tool_calls(user_id: int, limit: int = 50, *, status: str | None = None) -> list[dict]:
    """The audit log, newest first, for the Tools & Data screen."""
    sql = """
        SELECT tc.*, t.name AS tool_name, c.id AS conversation_id, c.channel
        FROM tool_calls tc
        JOIN tools t ON t.id = tc.tool_id
        JOIN messages m ON m.id = tc.message_id
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.user_id = ?
    """
    params: list[Any] = [user_id]
    if status:
        sql += " AND tc.status = ?"
        params.append(status)
    sql += " ORDER BY tc.id DESC LIMIT ?"
    params.append(limit)
    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        args_text = decrypt(r["args"])
        try:
            args: Any = json.loads(args_text)
        except json.JSONDecodeError:
            args = args_text
        out.append(
            {
                "id": r["id"],
                "tool": r["tool_name"],
                "function": r["function"],
                "scope": r["scope"],
                "status": r["status"],
                "args": args,
                "error": r["error"],
                "latency_ms": r["latency_ms"],
                "conversation_id": r["conversation_id"],
                "channel": r["channel"],
                "created_at": db.iso(r["created_at"]),
                "finished_at": db.iso(r["finished_at"]),
            }
        )
    return out
