"""The everyday "do it for me" test: ~1000 requests a person would say, through the real model
and the whole agent loop, with a stand-in desktop app that records what it was asked to do
(nothing is opened, played or sent). Each case passes if the right action(s) ran with the
right details and the reply passed the accuracy checks; latency is measured end to end.

    python eval/agent_eval.py                         # 1000 cases, the model in backend/.env
    python eval/agent_eval.py --model custodian-nemotron-v4 --cases 200
    python eval/agent_eval.py --show-failures 40

The requests are worded differently from the fine-tuning data (finetune/pc_web_scenarios.py),
so this measures how the model handles new phrasings, not memory. Results: eval/agent_results.md.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "finetune"))
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


@dataclass
class Case:
    category: str
    message: str
    expect: list[str]  # functions that must run, in order (any order within one step is fine)
    check: Callable[[list[dict]], bool] = lambda calls: True  # the details: calls = [{"function", "args"}]
    none: bool = False  # no PC action should run at all


@dataclass
class Result:
    case: Case
    ok: bool
    seconds: float
    calls: list[dict] = field(default_factory=list)
    reply: str = ""
    why: str = ""


def _has(calls: list[dict], function: str, **want: str) -> bool:
    for c in calls:
        if c["function"] != function:
            continue
        if all(v.lower() in str(c["args"].get(k, "")).lower() for k, v in want.items()):
            return True
    return False


APPS = ["notepad", "calculator", "chrome", "spotify", "vs code", "file explorer", "paint", "task manager", "word", "excel",
        "edge", "settings", "the camera", "powerpoint", "terminal"]
APP_TARGET = {"the camera": "camera"}
SITES = ["youtube", "gmail", "netflix", "github", "instagram", "linkedin", "amazon", "flipkart", "chatgpt", "reddit",
         "wikipedia", "google drive", "whatsapp web", "google maps", "hotstar"]
PAGES = [("the wikipedia article on the Eiffel Tower", "eiffel"), ("the IPL points table", "ipl"), ("SBI net banking login", "sbi"),
         ("the official React docs", "react"), ("the Mumbai local train timetable", "mumbai"), ("Elon Musk's wikipedia page", "musk"),
         ("the Python 3 documentation", "python"), ("the Nebius website", "nebius"), ("the Aadhaar download page", "aadhaar"),
         ("the Pune university results page", "pune")]
SITE_SEARCHES = [("amazon", "a laptop stand", "Amazon"), ("flipkart", "redmi note 13", "Flipkart"), ("youtube", "how to make pasta", "YouTube"),
                 ("google", "best cafes in pune", "Google"), ("github", "fastapi examples", "GitHub"), ("maps", "petrol pump near me", "Maps"),
                 ("wikipedia", "black holes", "Wikipedia"), ("reddit", "mechanical keyboards", "Reddit"), ("images", "golden retriever puppies", "images"),
                 ("news", "chandrayaan", "news")]
SONGS = ["Kalyani", "Apna Bana Le", "Shape of You", "Blinding Lights", "Kesariya", "Lo-fi beats", "Perfect by Ed Sheeran", "Channa Mereya",
         "Bohemian Rhapsody", "Satranga", "Heeriye", "a relaxing piano playlist", "Arijit Singh songs", "the latest Taylor Swift song"]
CONTACTS = ["Tanishka", "Mum", "Rahul", "Priya", "Dad", "Aditya", "Sneha", "Karan"]
TEXTS = ["I'll be 10 minutes late", "happy birthday!", "did you reach home?", "call me when you're free", "the meeting moved to 4pm",
         "can you send me the notes", "dinner at 8?", "on my way"]
FILES = ["resume", "invoice", "project report", "aadhaar card", "offer letter", "bank statement", "ppt for the seminar", "marksheet"]
QUESTIONS = [("who is the CEO of NVIDIA", "nvidia"), ("what's the capital of Australia", "australia"), ("latest news about the Indian space program", "isro"),
             ("what's the weather in Pune today", "pune"), ("who won the last cricket world cup", "cricket"), ("how many people live in Tokyo", "tokyo")]


# How often each kind of request appears in each suite (a maker missing from a suite isn't in it).
SUITES: dict[str, dict[str, int]] = {
    "chrome": {"open_site": 12, "go_to": 12, "search_site": 12, "url": 10, "youtube": 10, "web": 8, "read_page": 5,
               "close_tab": 6, "chrome_multi": 12, "close_chrome": 4, "personal": 2},
    "pc": {"open_app": 10, "media": 8, "volume": 6, "system": 12, "info": 6, "apps": 8, "files": 12, "file_ops": 6,
           "write": 6, "whatsapp": 8, "screen": 5, "clipboard": 4, "screenshot": 4, "focus": 5, "keys": 5, "multi": 8,
           "personal": 2},
}
CHROME_URLS = [("github.com/omchoudhari011", "github.com"), ("https://news.ycombinator.com", "ycombinator"),
               ("localhost:3000", "localhost"), ("docs.python.org/3", "python.org"), ("mail.google.com", "mail.google"),
               ("https://www.irctc.co.in", "irctc"), ("stackoverflow.com", "stackoverflow"), ("x.com", "x.com")]


def build_cases(n: int, seed: int, suite: str = "everyday") -> list[Case]:
    rng = random.Random(seed)
    makers: list[Callable[[], Case]] = []
    named: dict[str, Callable[[], Case]] = {}

    def add(weight: int):  # noqa: ANN202
        def deco(fn: Callable[[], Case]) -> Callable[[], Case]:
            named[fn.__name__] = fn
            if suite == "everyday":
                makers.extend([fn] * weight)
            return fn
        return deco

    @add(8)
    def open_app() -> Case:
        app = rng.choice(APPS)
        msg = rng.choice(["open {}", "launch {}", "can you open {} for me", "start {}", "fire up {}", "please open {}"]).format(app)
        target = APP_TARGET.get(app, app).split()[-1] if app != "vs code" else "code"
        if app == "settings":  # the Settings home page is just as right
            return Case("open an app", msg, [], lambda c: any(x["function"] in ("open", "open_settings") for x in c))
        return Case("open an app", msg, ["open"], lambda c, t=target: any(t in str(x["args"].get("target", "")).lower() for x in c))

    @add(8)
    def open_site() -> Case:
        site = rng.choice(SITES)
        msg = rng.choice(["open {}", "go to {}", "take me to {}", "open {} in the browser", "i want to see {}"]).format(site)
        key = site.split()[0]
        return Case("open a website", msg, [],
                    lambda c, k=key: any(x["function"] in ("open", "go_to") and k in str(x["args"]).lower() for x in c))

    @add(7)
    def go_to() -> Case:
        page, key = rng.choice(PAGES)
        msg = rng.choice(["take me to {}", "open {}", "show me {}", "bring up {}"]).format(page)
        return Case("go to a page", msg, [],
                    lambda c, k=key: any(x["function"] in ("go_to", "open", "search_site") and k in str(x["args"]).lower() for x in c))

    @add(6)
    def search_site() -> Case:
        site, query, label = rng.choice(SITE_SEARCHES)
        msg = rng.choice(["search {label} for {q}", "find {q} on {label}", "look for {q} on {label}", "show me {q} on {label}"]).format(label=label, q=query)
        word = query.split()[-1]
        return Case("search a site", msg, [],
                    lambda c, w=word: any(x["function"] in ("search_site", "go_to") and w in str(x["args"]).lower() for x in c))

    @add(10)
    def play() -> Case:
        song = rng.choice(SONGS)
        spotify = rng.random() < 0.3
        msg = rng.choice(["play {}", "put on {}", "i want to listen to {}", "play {} for me", "can you play {}"]).format(song)
        if spotify:
            msg += rng.choice([" on spotify", " in spotify"])
        word = song.split()[-1].lower().strip("!")
        return Case("play music or video", msg, ["play"],
                    lambda c, w=word, s=spotify: _has(c, "play", query=w) and (not s or _has(c, "play", service="spotify")))

    @add(6)
    def media() -> Case:
        msg, action = rng.choice([("pause", "play_pause"), ("pause the song", "play_pause"), ("resume the music", "play_pause"),
                                  ("next track please", "next"), ("skip this song", "next"), ("play the previous song", "previous"),
                                  ("mute", "mute"), ("mute the pc", "mute"), ("volume up", "volume_up"), ("lower the volume a bit", "volume_down")])
        return Case("media keys", msg, ["media"], lambda c, a=action: _has(c, "media", action=a))

    @add(5)
    def volume() -> Case:
        level = rng.choice([10, 25, 30, 45, 50, 60, 75, 80, 100])
        msg = rng.choice(["set the volume to {}%", "volume {} percent", "make the volume {}", "turn the volume to {}"]).format(level)
        return Case("set the volume", msg, ["set_volume"], lambda c, lv=level: _has(c, "set_volume", level=str(lv)))

    @add(8)
    def system() -> Case:
        msg, function, check = rng.choice([
            ("set the brightness to 70", "set_brightness", lambda c: _has(c, "set_brightness", level="70")),
            ("make my screen dimmer", "set_brightness", lambda c: any(x["function"] == "set_brightness" and int(x["args"]["level"]) < 60 for x in c)),
            ("enable dark mode", "set_theme", lambda c: _has(c, "set_theme", mode="dark")),
            ("go back to light theme", "set_theme", lambda c: _has(c, "set_theme", mode="light")),
            ("open the wifi settings", "open_settings", lambda c: _has(c, "open_settings", page="wifi")),
            ("i need to pair my bluetooth earphones", "open_settings", lambda c: _has(c, "open_settings", page="bluetooth")),
            ("open sound settings", "open_settings", lambda c: _has(c, "open_settings", page="sound")),
            ("set a timer for 15 minutes", "timer", lambda c: _has(c, "timer", seconds="900")),
            ("timer for 2 minutes please", "timer", lambda c: _has(c, "timer", seconds="120")),
            ("remind me in 20 minutes with a timer", "timer", lambda c: _has(c, "timer", seconds="1200")),
            ("lock my laptop", "lock_screen", lambda c: _has(c, "lock_screen")),
        ])
        return Case("settings and timers", msg, [function], check)

    @add(5)
    def info() -> Case:
        msg = rng.choice(["how much battery is left", "is my laptop plugged in", "how much storage do i have left", "what's my cpu usage",
                          "how much ram is being used", "battery percentage?"])
        return Case("pc info", msg, ["system_info"])

    @add(6)
    def apps() -> Case:
        name = rng.choice(["chrome", "spotify", "whatsapp", "notepad", "vs code", "excel"])
        msg, expect = rng.choice([("which apps are running", ["list_apps"]), ("what programs are open right now", ["list_apps"]),
                                  (f"close {name}", None), (f"quit {name}", None), (f"shut down {name}", None)])
        if expect:
            return Case("apps and windows", msg, expect)
        key = name.split()[-1]
        return Case("apps and windows", msg, [],
                    lambda c, k=key: any(x["function"] in ("close_app", "close_window") and k in str(x["args"]).lower() for x in c))

    @add(7)
    def files() -> Case:
        f = rng.choice(FILES)
        msg, check = rng.choice([
            (f"find my {f}", lambda c, w=f.split()[0]: _has(c, "find_files", query=w)),
            (f"where is my {f} file", lambda c, w=f.split()[0]: _has(c, "find_files", query=w)),
            ("what's in my downloads", lambda c: _has(c, "list_files", path="download")),
            ("show the files on my desktop", lambda c: _has(c, "list_files", path="desktop")),
            ("tidy up my downloads folder", lambda c: _has(c, "organize_folder", path="download")),
            ("make a new folder called Invoices on the desktop", lambda c: _has(c, "create_folder", path="invoices")),
        ])
        return Case("files", msg, [], check)

    @add(5)
    def write() -> Case:
        app = rng.choice(["notepad", "word"])
        what = rng.choice(["an email asking my manager for a day off", "a short thank you note to my teacher",
                           "a to-do list for tomorrow", "a two line poem about the sea", "a complaint about a late delivery"])
        msg = rng.choice(["write {w} in {a}", "open {a} and write {w}", "draft {w} in {a}"]).format(w=what, a=app)
        return Case("write in an app", msg, ["write_in_app"],
                    lambda c, a=app: _has(c, "write_in_app", app=a) and len(str(next(x for x in c if x["function"] == "write_in_app")["args"].get("text", ""))) > 20)

    @add(10)
    def whatsapp() -> Case:
        who, text = rng.choice(CONTACTS), rng.choice(TEXTS)
        msg = rng.choice(["send a whatsapp to {w} saying {t}", "whatsapp {w}: {t}", "message {w} on whatsapp that {t}",
                          "tell {w} on whatsapp {t}", "send {w} a message on whatsapp - {t}", "text {w} on whatsapp \"{t}\""]).format(w=who, t=text)
        key = text.split()[-1].strip("?!\"").lower()
        return Case("whatsapp message", msg, ["send_message"],
                    lambda c, w=who, k=key: _has(c, "send_message", contact=w) and _has(c, "send_message", text=k))

    @add(5)
    def screen() -> Case:
        msg, name = rng.choice([("click the Send button in whatsapp", "send"), ("press the Attach button in whatsapp", "attach"),
                                ("click voice call in whatsapp", "voice call")])
        return Case("use the screen", msg, [], lambda c, n=name: _has(c, "click_element", name=n.split()[0]))

    @add(5)
    def web() -> Case:
        q, key = rng.choice(QUESTIONS)
        return Case("web question", q + "?", [], lambda c, k=key: _has(c, "search") or True)  # checked via the reply below

    @add(6)
    def multi() -> Case:
        kind = rng.choice(["find_open", "close_lock", "play_volume", "search_open"])
        if kind == "find_open":
            f = rng.choice(FILES)
            return Case("multi-step", rng.choice([f"find my {f} and open it", f"open my {f}"]), ["find_files", "open"])
        if kind == "close_lock":
            app = rng.choice(["chrome", "spotify"])
            return Case("multi-step", f"close {app} and then lock the pc", ["close_app", "lock_screen"])
        if kind == "play_volume":
            song = rng.choice(SONGS)
            return Case("multi-step", f"play {song} and set the volume to 40", ["play", "set_volume"],
                        lambda c: _has(c, "set_volume", level="40"))
        q, _key = rng.choice(QUESTIONS[:4])
        return Case("multi-step", f"search {q} and open the first link", ["open"])

    @add(4)
    def personal() -> Case:
        msg = rng.choice(["what's my name?", "remember that my favourite colour is green", "hi, how are you?",
                          "what should I cook tonight?", "tell me a joke", "thanks!"])
        return Case("no PC action", msg, [], none=True)

    # Chrome --------------------------------------------------------------------------------------
    def url() -> Case:
        address, key = rng.choice(CHROME_URLS)
        msg = rng.choice(["open {}", "go to {}", "open {} in chrome", "load {}"]).format(address)
        return Case("open a URL", msg, [], lambda c, k=key: any(x["function"] in ("open", "go_to") and k in str(x["args"]).lower() for x in c))

    def youtube() -> Case:
        song = rng.choice(SONGS)
        msg = rng.choice(["play {} on youtube", "open youtube and play {}", "put {} on youtube", "youtube {}"]).format(song)
        word = song.split()[-1].lower()
        return Case("play on YouTube", msg, [], lambda c, w=word: _has(c, "play", query=w) or _has(c, "search_site", query=w))

    def read_page() -> Case:
        page = rng.choice(["https://en.wikipedia.org/wiki/Canberra", "https://en.wikipedia.org/wiki/Jensen_Huang"])
        msg = rng.choice(["summarise {} for me", "what does {} say?", "read {} and tell me the main points"]).format(page)
        return Case("read a page", msg, [], lambda c: True)  # the reply is judged below: it must have read the page

    def close_tab() -> Case:
        msg = rng.choice(["close this tab", "close the current chrome tab", "close the tab"])
        return Case("close a tab", msg, [], lambda c: _has(c, "press_keys", keys="w") or _has(c, "close_window"))

    def close_chrome() -> Case:
        return Case("close Chrome", rng.choice(["close chrome", "quit chrome", "close all chrome windows"]), [],
                    lambda c: any(x["function"] in ("close_app", "close_window") and "chrome" in str(x["args"]).lower() for x in c))

    def chrome_multi() -> Case:
        kind = rng.choice(["search_open", "site_search", "go_play"])
        if kind == "search_open":
            q, _key = rng.choice(QUESTIONS[:4])
            return Case("search, then open", rng.choice([f"google {q} and open the top result", f"search {q} and open the first link"]), ["open"])
        if kind == "site_search":
            site, query, label = rng.choice(SITE_SEARCHES)
            return Case("search, then open", f"open {label} and search for {query}", [],
                        lambda c, w=query.split()[-1]: any(w in str(x["args"]).lower() for x in c if x["function"] in ("search_site", "go_to")))
        song = rng.choice(SONGS)
        return Case("search, then open", f"go to youtube and play {song}", [], lambda c, w=song.split()[-1].lower(): _has(c, "play", query=w))

    # PC ------------------------------------------------------------------------------------------
    def file_ops() -> Case:
        msg, check = rng.choice([
            ("delete old-notes.txt from my desktop", lambda c: _has(c, "delete_file", path="old-notes")),
            ("move report.pdf from downloads to documents", lambda c: _has(c, "move_file", source="report")),
            ("copy budget.xlsx from my desktop to documents", lambda c: _has(c, "copy_file", source="budget")),
            ("rename draft.txt on my desktop to final.txt", lambda c: _has(c, "move_file", destination="final")),
        ])
        return Case("change files", msg, [], check)

    def clipboard() -> Case:
        text = rng.choice(["meeting at 5pm", "my address is 21 MG Road", "https://example.com"])
        return Case("clipboard", rng.choice(["copy '{}' to my clipboard", "put {} on the clipboard"]).format(text), ["set_clipboard"])

    def screenshot() -> Case:
        return Case("screen", rng.choice(["take a screenshot", "what's on my screen right now?", "capture my screen"]), [],
                    lambda c: any(x["function"] in ("screenshot", "read_screen") for x in c))

    def focus() -> Case:
        app = rng.choice(["chrome", "spotify", "notepad", "whatsapp", "vs code"])
        return Case("switch windows", rng.choice(["switch to {}", "bring {} to the front", "go to the {} window"]).format(app), [],
                    lambda c, a=app.split()[-1]: any(x["function"] in ("focus_window", "open") and a in str(x["args"]).lower() for x in c))

    def keys() -> Case:
        msg, combo = rng.choice([("press ctrl+s", "s"), ("save this with ctrl s", "s"), ("undo that", "z"), ("select all", "a"),
                                 ("press enter", "enter"), ("show the desktop", "d")])
        return Case("keyboard", msg, ["press_keys"], lambda c, k=combo: _has(c, "press_keys", keys=k))

    if suite != "everyday":
        for name in ("url", "youtube", "read_page", "close_tab", "close_chrome", "chrome_multi", "file_ops", "clipboard",
                     "screenshot", "focus", "keys"):
            named[name] = locals()[name]
        for name, weight in SUITES[suite].items():
            makers.extend([named[name]] * weight)
    return [rng.choice(makers)() for _ in range(n)]


def judge(case: Case, calls: list[dict], used: dict, reply: str) -> tuple[bool, str]:
    names = [c["function"] for c in calls]
    if used["checks"]["status"] not in ("passed", "corrected"):
        return False, f"accuracy check {used['checks']['status']}"
    if not reply.strip() or reply.startswith("(no reply"):
        return False, "empty reply"
    if case.none:
        return (not calls, f"ran {names}" if calls else "")
    missing = [f for f in case.expect if f not in names]
    if missing:
        return False, f"didn't run {missing}; ran {names}"
    if case.expect and len(case.expect) > 1:  # in order
        positions = [names.index(f) for f in case.expect]
        if positions != sorted(positions):
            return False, f"wrong order {names}"
    if case.category == "web question" and "search" not in [t["function"] for t in used["tools"]]:
        return False, "answered without searching"
    if case.category == "read a page" and "read_page" not in [t["function"] for t in used["tools"]]:
        return False, "answered without reading the page"
    if not case.check(calls):
        return False, f"wrong details: {calls[:3]}"
    return True, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", type=int, default=1000)
    parser.add_argument("--suite", choices=["everyday", "chrome", "pc"], default="everyday",
                        help="everyday: a mix; chrome: browsing; pc: apps, settings, files, windows, messages")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--model", default=None, help="Ollama model (default: NEBIUS_MODEL_ID from backend/.env)")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--show-failures", type=int, default=25)
    parser.add_argument("--out", type=Path, default=HERE / "agent_results.md")
    args = parser.parse_args()

    from dotenv import dotenv_values

    model = args.model or dotenv_values(HERE.parent / ".env").get("NEBIUS_MODEL_ID") or "custodian-nemotron"
    from make_dataset import World  # a throwaway Custodian with the stand-in desktop and search engine

    world = World(args.seed)
    from app import fastpath, orchestrator

    fastpath.ENABLED = True  # the real app answers exact everyday commands without the model
    from app.model_backends import set_backend
    from app.model_backends.nebius_nemotron_backend import NebiusNemotronBackend

    env = dotenv_values(HERE.parent / ".env")  # the same settings the app uses
    import json as _json

    world.teacher.close = lambda: None  # the dataset's scripted model, replaced below
    set_backend(NebiusNemotronBackend(api_key="ollama", model_id=model, base_url=args.base_url, timeout=180,
                                      max_tokens=int(env.get("MODEL_MAX_TOKENS") or 1024),
                                      temperature=float(env.get("MODEL_TEMPERATURE") or 0.3),
                                      extra_body=_json.loads(env.get("MODEL_EXTRA_BODY") or "{}")))
    recorded: list[dict] = []
    desk = world.fake_desktop
    original = desk.result

    def record(function: str, a: dict) -> dict:
        recorded.append({"function": function, "args": a})
        return original(function, a)

    desk.result = record  # type: ignore[method-assign]

    cases = build_cases(args.cases, args.seed, args.suite)
    results: list[Result] = []
    started_all = time.perf_counter()
    for i, case in enumerate(cases, 1):
        world.reset({"pc": ("read", "write"), "web": ("read",), "notes": ("read", "write"), "reminders": ("read", "write"),
                     "documents": ("read",), "calendar": ("read", "write")})  # every switch on, as for a "full control" user
        desk.set_mode("ok")
        recorded.clear()
        started = time.perf_counter()
        try:
            out = orchestrator.handle(world.user_id, case.message)
        except Exception as exc:  # noqa: BLE001 - a crash is a failed case, with the reason
            results.append(Result(case, False, time.perf_counter() - started, why=f"{type(exc).__name__}: {exc}"[:200]))
            continue
        seconds = time.perf_counter() - started
        ok, why = judge(case, list(recorded), out["used"], out["reply"])
        results.append(Result(case, ok, seconds, list(recorded), out["reply"], why))
        if i % 50 == 0 or i == len(cases):
            passed = sum(r.ok for r in results)
            lat = sorted(r.seconds for r in results)
            print(f"{i}/{len(cases)}  passed {passed} ({passed / i:.0%})  median {statistics.median(lat):.1f}s  "
                  f"p95 {lat[int(len(lat) * 0.95) - 1]:.1f}s  elapsed {(time.perf_counter() - started_all) / 60:.0f} min", flush=True)

    write_report(results, model, args)
    return 0


def write_report(results: list[Result], model: str, args: argparse.Namespace) -> None:
    by_cat: dict[str, list[Result]] = defaultdict(list)
    for r in results:
        by_cat[r.case.category].append(r)
    lat = sorted(r.seconds for r in results)
    passed = sum(r.ok for r in results)
    title = {"everyday": "Everyday agent test", "chrome": "Chrome test", "pc": "PC operations test"}[args.suite]
    lines = [f"# {title}: {model}", "",
             f"{passed}/{len(results)} passed ({passed / len(results):.1%}) - median {statistics.median(lat):.2f} s, "
             f"p95 {lat[int(len(lat) * 0.95) - 1]:.2f} s per request, end to end (model, tools, checks; desktop actions stood in)", "",
             "| Category | Passed | Median s | p95 s |", "|---|---|---|---|"]
    for cat, rs in sorted(by_cat.items(), key=lambda kv: kv[0]):
        s = sorted(r.seconds for r in rs)
        lines.append(f"| {cat} | {sum(r.ok for r in rs)}/{len(rs)} | {statistics.median(s):.2f} | {s[max(0, int(len(s) * 0.95) - 1)]:.2f} |")
    reasons = Counter(r.why.split(":")[0] for r in results if not r.ok)
    lines += ["", "Why cases failed: " + ", ".join(f"{k} ({v})" for k, v in reasons.most_common(8)), "", "## Some failures", ""]
    for r in [r for r in results if not r.ok][: args.show_failures]:
        lines.append(f"- **{r.case.category}** “{r.case.message}” - {r.why}; reply: {r.reply[:140]!r}")
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:4 + len(by_cat) + 3]))
    print(f"\nwritten to {args.out}")


if __name__ == "__main__":
    raise SystemExit(main())
