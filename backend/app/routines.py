"""Running routines: say "work mode" and Custodian opens VS Code and your tabs, turns on Do Not
Disturb and puts on focus music, one step after another.

match() spots a routine's name (or one of its phrases) in a message - "work mode", "start work
mode", "run my good night routine". The orchestrator then lets RoutinePath play the model's part
for the turn, like the fast path does for a single command:

1. If any step would ask on the PC by itself (closing an app, sorting a folder), it first asks
   once for the whole list (pc_confirm_plan) - one "Allow once" instead of one per step.
2. Every step runs as an ordinary tool call: the same permission switches, skill allowlist,
   argument checks and audit log. Steps covered by an approved list don't ask again.
3. The reply says what was done, from the results - never more.

Routines live in the routines tool's database (app/tools/routines_tool.py); this module also
lists, saves and deletes them for the app's Routines screen.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import fastpath
from .config import get_settings
from .model_backends.base import ModelBackend, ModelResponse, ToolCallRequest
from .tools import registry, routines_tool
from .tools.schemas import SaveRoutineArgs

# Words around a routine's name that still mean "run it": "start work mode", "work mode on",
# "run my good night routine", "it's good night time". Any other word makes it a message about
# the routine ("add yoga to work mode"), not a request to run it.
WRAPPERS = frozenset("""
    run start begin do activate enable turn switch on to go into enter put me in set it's its time for let's lets the my our
    routine mode now please ok okay so right hey jarvis custodian
""".split())


class RoutineError(ValueError):
    """A routine can't be saved like that; the message is safe to show."""


def _folder() -> Path:
    folder = get_settings().tool_data_dir / "routines"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", fastpath.normalise(text))


def _asks_to_run(said: list[str], name: list[str]) -> bool:
    """`said` is `name` with nothing around it but WRAPPERS."""
    for start in range(len(said) - len(name) + 1):
        if name and said[start:start + len(name)] == name:
            rest = said[:start] + said[start + len(name):]
            return all(word in WRAPPERS for word in rest)
    return False


def _rows(user_id: int) -> list[dict]:
    if not (get_settings().tool_data_dir / "routines" / "routines.db").exists():
        return []
    conn = routines_tool.connect(_folder())
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM routines WHERE user_id = ?", (user_id,))]
    finally:
        conn.close()


def match(user_id: int, message: str | None) -> dict | None:
    """The routine a message asks to run, or None. Only a message that is (nearly) just the
    routine's name counts: "add yoga to my work mode routine" is about the routine, not a run."""
    if not message or len(message) > 120:
        return None
    said = _words(message)
    if not said:
        return None
    for row in _rows(user_id):
        names = [row["name"], *json.loads(row["phrases"])]
        if any(_asks_to_run(said, _words(name)) for name in names):
            return {**row, "steps": json.loads(row["steps"])}
    return None


# The app's Routines screen ---------------------------------------------------------------------

def list_for(user_id: int) -> list[dict]:
    rows = sorted(_rows(user_id), key=lambda r: r["name"].lower())
    return [{**routines_tool.describe(row), "needs_approval": needs_approval(json.loads(row["steps"]))} for row in rows]


def save(user_id: int, name: str, steps: list[str], phrases: list[str] | None = None) -> dict:
    """Check and save a routine from the app (the same checks as the model's tool call)."""
    from pydantic import ValidationError

    try:
        checked = SaveRoutineArgs.model_validate({"name": name, "steps": steps, "phrases": phrases or []})
    except ValidationError as exc:
        error = exc.errors()[0]
        raise RoutineError(str(error.get("ctx", {}).get("error") or error.get("msg") or "invalid routine")) from None
    conn = routines_tool.connect(_folder())
    try:
        try:
            saved, _ = routines_tool.store(conn, user_id, checked.name, checked.parsed, checked.phrases)
        except ValueError as exc:
            raise RoutineError(str(exc)) from None
    finally:
        conn.close()
    return saved


def delete(user_id: int, routine_id: int) -> bool:
    if not (get_settings().tool_data_dir / "routines" / "routines.db").exists():
        return False
    conn = routines_tool.connect(_folder())
    try:
        with conn:
            return conn.execute("DELETE FROM routines WHERE id = ? AND user_id = ?", (routine_id, user_id)).rowcount > 0
    finally:
        conn.close()


def get(user_id: int, routine_id: int) -> dict | None:
    return next((r for r in _rows(user_id) if r["id"] == routine_id), None)


def mark_run(routine_id: int) -> None:
    conn = routines_tool.connect(_folder())
    try:
        with conn:
            conn.execute("UPDATE routines SET last_run_at = datetime('now') WHERE id = ?", (routine_id,))
    finally:
        conn.close()


# Running ----------------------------------------------------------------------------------------

def needs_approval(steps: list[dict]) -> bool:
    return any((registry.find(s["function"]) or registry.find("pc_open")).confirm for s in steps)


def _label(step: dict) -> str:
    said = step.get("said") or step["function"].removeprefix("pc_").replace("_", " ")
    return said[:1].upper() + said[1:]


def _done_line(step: dict, result: dict) -> str:
    status = result.get("status")
    intent = fastpath.parse(step.get("said") or "", routine=True)
    if status == "succeeded" and intent is not None:
        try:
            return intent.reply(result.get("result") or {})
        except (KeyError, TypeError, IndexError, AttributeError):
            pass
    if status == "succeeded":
        return f"{_label(step)} - done."
    why = result.get("error") or status or "it didn't work"
    return f"Couldn't {(step.get('said') or step['function'])}: {why}"


class RoutinePath(ModelBackend):
    """Plays the model's part for one routine run (see the module docstring)."""

    name = "routine"
    model_id = "custodian-routine"

    def __init__(self, routine: dict) -> None:
        self.routine = routine
        self.steps: list[dict] = routine["steps"]
        self.ask_first = needs_approval(self.steps)
        self.preapproved: set[str] = set()  # tool call ids the approved list covers

    def _step_calls(self) -> list[ToolCallRequest]:
        calls = [ToolCallRequest(f"routine_{i}", step["function"], step["args"]) for i, step in enumerate(self.steps, 1)]
        if self.ask_first:
            self.preapproved = {c.id for c in calls}
        return calls

    def generate(self, messages: list[dict], tools: list[dict] | None = None) -> ModelResponse:
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        after = messages[last_user + 1:]
        results = [json.loads(m["content"]) for m in after if m["role"] == "tool"]
        rounds = sum(1 for m in after if m["role"] == "assistant" and m.get("tool_calls"))
        name = self.routine["name"]
        if tools and rounds == 0:
            if self.ask_first:
                plan = {"title": f"Run “{name}”", "steps": [_label(s)[:200] for s in self.steps]}
                return ModelResponse("", [ToolCallRequest("routine_plan", "pc_confirm_plan", plan)], self.model_id)
            return ModelResponse("", self._step_calls(), self.model_id)
        if tools and rounds == 1 and self.ask_first:
            approval = results[0]
            if approval.get("status") == "succeeded":
                return ModelResponse("", self._step_calls(), self.model_id)
            return ModelResponse(self._not_run(approval), [], self.model_id)
        step_results = results[1:] if self.ask_first else results
        return ModelResponse(self._summary(step_results), [], self.model_id)

    def _not_run(self, approval: dict) -> str:
        name, error = self.routine["name"], approval.get("error") or ""
        if approval.get("status") == "denied":
            return f"I couldn't run “{name}”: {error}"
        if "declined" in error.lower():
            return f"Okay - I didn't run “{name}”."
        return f"I couldn't run “{name}”: {error or 'the PC did not answer'}"

    def _summary(self, results: list[dict]) -> str:
        lines = [_done_line(step, result) for step, result in zip(self.steps, results)]
        missing = len(self.steps) - len(results)
        if missing > 0:
            lines.append(f"{missing} step{'s' if missing != 1 else ''} didn't run.")
        done = sum(1 for r in results if r.get("status") == "succeeded")
        head = (f"“{self.routine['name']}” is done." if done == len(self.steps)
                else f"“{self.routine['name']}”: {done} of {len(self.steps)} steps worked.")
        return head + "\n" + "\n".join(f"• {line}" for line in lines)
