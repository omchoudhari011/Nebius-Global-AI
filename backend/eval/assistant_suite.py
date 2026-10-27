"""The assistant's 1,000-case test - the part that runs through the server: the real model, the real agent loop,
gate, permissions and accuracy checks, with a stand-in PC (it answers PC actions the way the desktop app would,
and records each one and whether it asked first) and a stand-in web (search results and pages from a fixed set,
so answers can be checked; everything else about a tool call runs for real, in the sandbox).

    python eval/assistant_suite.py                       # every server-side case -> eval/results/server.json
    python eval/assistant_suite.py --only NLU-001,SEC-   # some, by id or id prefix
    python eval/assistant_suite.py --list                # the cases, without running them

The PC-side cases (real files, real Chrome, real windows, WhatsApp) are desktop/eval/live_suite.py; the two are
merged into one report by eval/suite_report.py. Case definitions: eval/assistant_cases.py.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "finetune"))
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

FULL = {"pc": ("read", "write"), "web": ("read",), "notes": ("read", "write"), "reminders": ("read", "write"),
        "documents": ("read",), "calendar": ("read", "write"), "routines": ("read", "write"), "watchers": ("read", "write")}


@dataclass
class Turn:
    said: str
    reply: str = ""
    calls: list[dict] = field(default_factory=list)  # {"tool", "function", "args", "confirm"}: PC and server tools that ran
    tools: list[dict] = field(default_factory=list)  # the turn's tool calls as Custodian recorded them (with status)
    checks: str = ""
    seconds: float = 0.0
    error: str = ""


@dataclass
class Run:
    turns: list[Turn]

    @property
    def last(self) -> Turn:
        return self.turns[-1]


Check = Callable[[Run], "str | None"]
HARNESS = None  # the running Harness, for the system checks


@dataclass
class Case:
    id: str
    category: str
    title: str
    turns: list[str]
    expect: str
    check: Check
    grants: dict | None = None  # Tools & Data switches (None: all of them on)
    desk: Callable[[str, dict], tuple[bool, object]] | None = None  # the stand-in PC's answer (None: it succeeds)
    pre: str = ""
    setup: Callable | None = None  # (world) -> None: memories, notes ...
    between: Callable | None = None  # (world, turn index) -> None, before each turn after the first
    voice: bool = False
    offline: bool = False  # the desktop app isn't connected
    layer: str = "model"
    model_down: bool = False  # the model errors on every call
    action_timeout: float | None = None  # seconds the server waits for the PC (with a PC that never answers)


# Checks ------------------------------------------------------------------------------------------------

def _fits(call: dict, function: str, want: dict) -> bool:
    if call["function"] != function:
        return False
    return all(str(v).lower() in json.dumps(call["args"], ensure_ascii=False).lower() if k == "*"
               else str(v).lower() in str(call["args"].get(k, "")).lower() for k, v in want.items())


def did(function: str, turn: int = -1, **want: str) -> Check:
    def check(run: Run) -> str | None:
        calls = run.turns[turn].calls
        if any(_fits(c, function, want) for c in calls):
            return None
        return f"expected {function}{want or ''}; ran {[(c['function'], c['args']) for c in calls][:4]}"
    return check


def did_any(*functions: str, turn: int = -1) -> Check:
    def check(run: Run) -> str | None:
        ran = [c["function"] for c in run.turns[turn].calls]
        return None if any(f in ran for f in functions) else f"expected one of {functions}; ran {ran}"
    return check


def didnt(*functions: str, every_turn: bool = True) -> Check:
    def check(run: Run) -> str | None:
        turns = run.turns if every_turn else [run.last]
        bad = [c["function"] for t in turns for c in t.calls if c["function"] in functions]
        return f"must not run {bad}" if bad else None
    return check


def no_pc(turn: int = -1) -> Check:
    def check(run: Run) -> str | None:
        pc = [c["function"] for c in run.turns[turn].calls if c["tool"] == "pc"]
        return f"no PC action expected; ran {pc}" if pc else None
    return check


def no_write(turn: int | None = None) -> Check:
    """Nothing changed or sent: no PC write, message, email, file change or setting."""
    writes = {"write_file", "move_file", "delete_file", "copy_file", "create_folder", "organize_folder", "send_message", "call",
              "type_text", "press_keys", "click", "click_element", "fill_field", "close_app", "close_window", "write_in_app",
              "send_email", "draft_email", "browser_click", "browser_fill", "set_theme", "set_brightness", "lock_screen",
              "browser_download", "write_document", "set_clipboard"}

    def check(run: Run) -> str | None:
        turns = run.turns if turn is None else [run.turns[turn]]
        bad = [(c["function"], c["args"]) for t in turns for c in t.calls if c["function"] in writes]
        return f"changed or sent something: {bad[:3]}" if bad else None
    return check


def says(pattern: str, turn: int = -1) -> Check:
    def check(run: Run) -> str | None:
        return None if re.search(pattern, run.turns[turn].reply, re.I | re.S) else f"reply lacks /{pattern}/: {run.turns[turn].reply[:160]!r}"
    return check


def lacks(pattern: str, turn: int | None = None) -> Check:
    def check(run: Run) -> str | None:
        turns = run.turns if turn is None else [run.turns[turn]]
        hit = next((t.reply for t in turns if re.search(pattern, t.reply, re.I | re.S)), None)
        return f"reply must not match /{pattern}/: {hit[:160]!r}" if hit else None
    return check


def honest(turn: int | None = None) -> Check:
    """The accuracy check passed (or corrected the draft), or the reply plainly says nothing was done."""
    def check(run: Run) -> str | None:
        from app import grounding

        turns = run.turns if turn is None else [run.turns[turn]]
        for t in turns:
            if t.error:
                return f"crashed: {t.error}"
            if t.checks not in ("passed", "corrected") and t.reply not in (grounding.nothing_done(), grounding.nothing_done(hindi=True)):
                return f"accuracy check {t.checks}: {t.reply[:160]!r}"
        return None
    return check


def asks(turn: int = -1) -> Check:
    def check(run: Run) -> str | None:
        return None if "?" in run.turns[turn].reply else f"expected a question back: {run.turns[turn].reply[:160]!r}"
    return check


def status(function: str, *wanted: str, turn: int = -1) -> Check:
    def check(run: Run) -> str | None:
        got = [t["status"] for t in run.turns[turn].tools if t["function"] == function]
        return None if any(s in wanted for s in got) else f"{function} status {got}, expected {wanted}"
    return check


def asked_first(function: str) -> Check:
    """The PC action went to the desktop marked "ask the user first"."""
    def check(run: Run) -> str | None:
        calls = [c for t in run.turns for c in t.calls if c["function"] == function]
        if not calls:
            return f"{function} never reached the PC"
        return None if all(c.get("confirm") for c in calls) else f"{function} reached the PC without asking first"
    return check


def replied() -> Check:
    def check(run: Run) -> str | None:
        for t in run.turns:
            if t.error:
                return f"crashed: {t.error}"
            if not t.reply.strip():
                return "empty reply"
        return None
    return check


def each(*checks: Check) -> Check:
    def check(run: Run) -> str | None:
        for c in checks:
            why = c(run)
            if why:
                return why
        return None
    return check


def either(*checks: Check) -> Check:
    def check(run: Run) -> str | None:
        whys = [c(run) for c in checks]
        return None if any(w is None for w in whys) else " / ".join(w for w in whys if w)
    return check


# The stand-in web ------------------------------------------------------------------------------------------

class Web:
    """Search results and pages from a fixed set: (url, title, text, keywords)."""

    pages: list[tuple[str, str, str, tuple[str, ...]]] = []

    @classmethod
    def search(cls, query: str) -> dict:
        words = set(re.findall(r"[a-z0-9]+", query.lower()))
        scored = sorted(((len(words & set(k)), p) for p in cls.pages for k in [p[3]]), key=lambda s: -s[0])
        results = [{"title": p[1], "url": p[0], "snippet": " ".join(p[2].split())[:300]} for n, p in scored if n >= 1][:5]
        return {"query": query, "answers": [], "infobox": [], "results": results}

    @classmethod
    def page(cls, url: str) -> dict | None:
        found = next((p for p in cls.pages if p[0].rstrip("/") == url.rstrip("/")), None)
        return {"url": found[0], "title": found[1], "text": found[2]} if found else None


# Running -----------------------------------------------------------------------------------------------------

class Harness:
    def __init__(self, model: str | None = None) -> None:
        from dotenv import dotenv_values
        from make_dataset import World

        env = dotenv_values(BACKEND / ".env")
        self.world = World(7)
        from app import desktop, fastpath
        from app.model_backends import set_backend
        from app.model_backends.nebius_nemotron_backend import NebiusNemotronBackend
        from app.tools import sandbox

        fastpath.ENABLED = True
        self.world.teacher.close = lambda: None
        self.model_id = model or env.get("NEBIUS_MODEL_ID") or "custodian-nemotron"
        self.backend = NebiusNemotronBackend(api_key="ollama", model_id=self.model_id, base_url="http://127.0.0.1:11434/v1",
                                             timeout=180, max_tokens=int(env.get("MODEL_MAX_TOKENS") or 1024),
                                             temperature=float(env.get("MODEL_TEMPERATURE") or 0.3),
                                             extra_body=json.loads(env.get("MODEL_EXTRA_BODY") or "{}"))
        set_backend(self.backend)
        self.desktop = desktop
        self.calls: list[dict] = []
        self.answer: Callable[[str, dict], tuple[bool, object]] = self.default_answer
        self.serving = threading.Event()
        self.world.fake_desktop.set_mode("offline")  # its own loop stands aside: serve() answers instead
        threading.Thread(target=self.serve, daemon=True).start()
        real_run = sandbox.SubprocessSandbox.run

        def run(executor, request, timeout):  # noqa: ANN001, ANN202 - the stand-in web, and a record of server tools
            self.calls.append({"tool": request.module.rsplit(".", 1)[-1], "function": request.function,
                               "args": dict(request.args), "confirm": False})
            if request.module == "app.tools.web_tools" and request.function == "search":
                return Web.search(str(request.args.get("query", "")))
            if request.module == "app.tools.web_tools" and request.function in ("read_page", "page_snapshot"):
                page = Web.page(str(request.args.get("url", "")))
                if page is not None:
                    return page
            if request.module == "app.tools.web_tools" and request.function == "weather":
                return {"city": request.args.get("city"), "now": {"temperature_c": 27, "conditions": "partly cloudy"},
                        "days": [{"date": "today", "max_c": 31, "min_c": 22, "conditions": "partly cloudy", "rain_chance": 20}]}
            return real_run(executor, request, timeout)

        sandbox.SubprocessSandbox.run = run  # type: ignore[method-assign]

    def default_answer(self, function: str, args: dict) -> tuple[bool, object]:
        return True, self.world.fake_desktop.result(function, args)

    def serve(self) -> None:
        """The desktop app's side of the bridge, while a case wants it connected."""
        while True:
            if not self.serving.is_set():
                time.sleep(0.05)
                continue
            action = self.desktop.next_action(self.world.user_id, "Home PC", wait=0.3)
            if not action or not action.get("reply", True):
                continue
            self.calls.append({"tool": "pc", "function": action["function"], "args": action["args"],
                               "confirm": bool(action.get("confirm"))})
            try:
                ok, out = self.answer(action["function"], action["args"])
            except Exception as exc:  # noqa: BLE001 - a stand-in's own bug: report it as the PC's error
                ok, out = False, f"{type(exc).__name__}: {exc}"
            if out == "__never__":  # the PC never answers (a hung app)
                continue
            self.desktop.finish(self.world.user_id, action["id"], ok, result=out if ok else None, error=None if ok else out)

    def run(self, case: Case) -> Run:
        from app import orchestrator

        self.world.reset(case.grants if case.grants is not None else FULL)
        if case.setup:
            case.setup(self.world)
        self.answer = case.desk or self.default_answer
        if case.offline:
            self.serving.clear()
            self.desktop.reset()
        else:
            self.desktop.touch(self.world.user_id, "Home PC")
            self.serving.set()
        from app import desktop as bridge
        from app.model_backends import ModelError

        def down(messages, tools=None):  # noqa: ANN001, ANN202 - the model server not answering
            raise ModelError("the model server isn't answering (test)")

        if case.model_down:  # (not set_backend: that closes the real backend's connection for good)
            self.backend.generate = down
        saved_timeout = (bridge.ACTION_TIMEOUT, bridge.CONFIRM_TIMEOUT)
        if case.action_timeout is not None:
            bridge.ACTION_TIMEOUT = bridge.CONFIRM_TIMEOUT = case.action_timeout
        turns, conversation = [], None
        for i, said in enumerate(case.turns):
            if i and case.between:
                case.between(self.world, i)
            self.calls.clear()
            turn = Turn(said)
            started = time.perf_counter()
            try:
                out = orchestrator.handle(self.world.user_id, said, conversation_id=conversation, voice=case.voice,
                                          origin="desktop" if case.voice else None)
                conversation = out["conversation_id"]
                turn.reply, turn.tools, turn.checks = out["reply"], out["used"]["tools"], out["used"]["checks"]["status"]
            except ModelError as exc:  # what POST /chat answers then (app/main.py), and the app shows
                turn.reply, turn.checks = f"The model didn't respond: {exc}", "api-error"
            except Exception as exc:  # noqa: BLE001 - a crash is a failed case, with where it happened
                turn.error = f"{type(exc).__name__}: {exc} | {traceback.format_exc(limit=3)[-300:]}"
            turn.seconds = time.perf_counter() - started
            turn.calls = list(self.calls)
            turns.append(turn)
        self.serving.clear()
        bridge.ACTION_TIMEOUT, bridge.CONFIRM_TIMEOUT = saved_timeout
        if case.model_down:
            del self.backend.generate  # the class's own again
        return Run(turns)


def main() -> int:
    import assistant_cases

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", default="", help="comma-separated ids or id prefixes")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--out", type=Path, default=HERE / "results" / "server.json")
    args = parser.parse_args()

    cases = assistant_cases.build()
    if args.only:
        wanted = [w.strip() for w in args.only.split(",") if w.strip()]
        cases = [c for c in cases if any(c.id == w or c.id.startswith(w) for w in wanted)]
    if args.list:
        for c in cases:
            print(f"{c.id:10} {c.category:28} {c.title} :: {' / '.join(c.turns)[:90]}")
        print(len(cases), "cases")
        return 0
    Web.pages = assistant_cases.WEB_PAGES
    global HARNESS
    harness = HARNESS = Harness(args.model)
    rows, started = [], time.perf_counter()
    assistant_cases.RESULTS = rows  # the system checks read this run's results
    cases.sort(key=lambda c: c.layer == "system")  # those go last
    for i, case in enumerate(cases, 1):
        if case.layer in ("server-unit", "system"):
            t0 = time.perf_counter()
            try:
                why = case.check(Run([]))
            except Exception as exc:  # noqa: BLE001
                why = f"{type(exc).__name__}: {exc}"
            rows.append(row(case, None, why, time.perf_counter() - t0))
            continue
        run = harness.run(case)
        try:
            why = case.check(run)
        except Exception as exc:  # noqa: BLE001 - a broken check is a failed case, never a pass
            why = f"check crashed: {type(exc).__name__}: {exc}"
        rows.append(row(case, run, why, sum(t.seconds for t in run.turns)))
        if i % 25 == 0 or i == len(cases):
            passed = sum(r["status"] == "pass" for r in rows)
            print(f"{i}/{len(cases)} passed {passed} - {(time.perf_counter() - started) / 60:.0f} min", flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"model": harness.model_id, "when": time.strftime("%Y-%m-%d %H:%M"), "cases": rows},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    lat = sorted(r["seconds"] for r in rows if r["layer"] == "model")
    passed = sum(r["status"] == "pass" for r in rows)
    print(f"\n{passed}/{len(rows)} passed; median {statistics.median(lat) if lat else 0:.2f} s per case -> {args.out}")
    return 0


def row(case: Case, run: Run | None, why: str | None, seconds: float) -> dict:
    return {
        "id": case.id, "category": case.category, "title": case.title, "layer": case.layer,
        "preconditions": case.pre or ("desktop app not connected" if case.offline else "stand-in PC connected; "
                                      + ("all Tools & Data switches on" if case.grants is None else f"switches: {case.grants}")),
        "input": case.turns, "expected": case.expect,
        "actual": [{"said": t.said, "reply": t.reply, "ran": [(c["tool"], c["function"], c["args"]) for c in t.calls],
                    "check": t.checks, "error": t.error} for t in run.turns] if run else "",
        "status": "pass" if why is None else "fail", "failure": why or "", "seconds": round(seconds, 2),
        "evidence": "recorded tool calls, reply and accuracy check above" if run else "assertion run in-process",
    }


if __name__ == "__main__":
    raise SystemExit(main())
