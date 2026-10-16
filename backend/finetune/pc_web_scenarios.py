"""PC-control and web-knowledge scenarios for make_dataset.py, with stand-ins for the
desktop app and the search engine so every example runs through the real gate.

FakeDesktop answers the PC actions the way the Custodian desktop app does - including
"the user declined this on the PC" for a confirmed action, and not being connected at all.
FakeSearx serves canned search results on 127.0.0.1, which the sandbox lets web_search reach.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlsplit

# (question, search query, results [(title, url, snippet)], answer) - answers say the fact
# first, then the sources they came from.
WEB_FACTS = [
    ("who is the CEO of NVIDIA?", "NVIDIA CEO",
     [("Jensen Huang - NVIDIA", "https://www.nvidia.com/en-us/about-nvidia/board-of-directors/jensen-huang/",
       "Jensen Huang founded NVIDIA in 1993 and has served since its inception as president and CEO.")],
     "Jensen Huang is NVIDIA's CEO; he co-founded the company in 1993."),
    ("what's the capital of Australia?", "capital of Australia",
     [("Canberra - Wikipedia", "https://en.wikipedia.org/wiki/Canberra", "Canberra is the capital city of Australia.")],
     "The capital of Australia is Canberra."),
    ("how many people live in Tokyo?", "Tokyo population",
     [("Tokyo - Wikipedia", "https://en.wikipedia.org/wiki/Tokyo",
       "Tokyo has a population of about 14 million; the Greater Tokyo Area has about 37 million people.")],
     "About 14 million people live in Tokyo itself, and about 37 million in the Greater Tokyo Area."),
    ("what is quantum computing?", "what is quantum computing",
     [("What is quantum computing? - IBM", "https://www.ibm.com/think/topics/quantum-computing",
       "Quantum computing uses qubits, which can be in superpositions of states, to solve some problems far faster than classical computers.")],
     "Quantum computing uses qubits - which can hold a mix of 0 and 1 at once - to solve certain problems much faster than ordinary computers."),
    ("how do I make masala chai?", "masala chai recipe",
     [("Masala Chai Recipe - Swasthi's Recipes", "https://www.indianhealthyrecipes.com/masala-tea-chai/",
       "Boil water with crushed ginger, cardamom and cloves, add tea leaves, then milk and sugar, and simmer for 2 to 3 minutes.")],
     "Boil water with crushed ginger, cardamom and cloves, add tea leaves, then milk and sugar, and simmer for 2-3 minutes before straining."),
    ("what's the weather in Pune today?", "Pune weather today",
     [("Pune weather - AccuWeather", "https://www.accuweather.com/en/in/pune/204848/weather-forecast/204848",
       "Today: partly cloudy, high 31°C, low 21°C, 10% chance of rain.")],
     "Pune today: partly cloudy, high 31°C and low 21°C, with a 10% chance of rain."),
    ("what's the latest news about the Indian space program?", "ISRO latest news",
     [("ISRO news - The Hindu", "https://www.thehindu.com/sci-tech/science/isro/",
       "ISRO announced the next Gaganyaan uncrewed test flight for early next year after completing parachute trials.")],
     "The latest: ISRO says the next uncrewed Gaganyaan test flight is planned for early next year, after its parachute trials."),
    ("who won the last cricket world cup?", "cricket world cup winner latest",
     [("ICC Men's Cricket World Cup - Wikipedia", "https://en.wikipedia.org/wiki/Cricket_World_Cup",
       "Australia won the most recent ICC Men's Cricket World Cup, beating India in the final.")],
     "Australia won the most recent men's Cricket World Cup, beating India in the final."),
    ("what does API stand for?", "API meaning",
     [("API - Wikipedia", "https://en.wikipedia.org/wiki/API", "An application programming interface (API) is a way for two or more computer programs to communicate.")],
     "API stands for application programming interface - a way for programs to talk to each other."),
    ("how far is the moon from earth?", "distance moon earth",
     [("Moon distance - NASA", "https://science.nasa.gov/moon/facts/", "The Moon is on average about 384,400 km from Earth.")],
     "The Moon is on average about 384,400 km from Earth."),
]

PC_INFO = [  # (question, which part of system_info the answer uses)
    ("what's my battery level?", "battery"), ("how much battery do I have left?", "battery"),
    ("how much disk space is free?", "disk"), ("is my laptop charging?", "battery"),
    ("how busy is my cpu?", "cpu"), ("how much free space on my C drive?", "disk"),
]
PC_OPEN = [  # (request, target, what it is)
    ("open notepad", "notepad", "Notepad"), ("launch chrome", "chrome", "Chrome"), ("open spotify", "spotify", "Spotify"),
    ("open the calculator", "calculator", "the Calculator"), ("open settings", "settings", "Settings"),
    ("open youtube.com", "youtube.com", "YouTube"), ("open my downloads folder", "~/Downloads", "your Downloads folder"),
    ("start vs code", "vs code", "VS Code"), ("open file explorer", "explorer", "File Explorer"),
    ("open github.com", "github.com", "GitHub"),
]
PC_MEDIA = [  # (request, action, steps, reply)
    ("pause the music", "play_pause", 1, "Paused."), ("play the music", "play_pause", 1, "Playing."),
    ("next song", "next", 1, "Skipped to the next track."), ("go back to the previous track", "previous", 1, "Back to the previous track."),
    ("mute the sound", "mute", 1, "Muted."), ("turn the volume up a bit", "volume_up", 5, "Turned the volume up a bit."),
    ("turn the volume down a little", "volume_down", 5, "Turned the volume down a little."),
    ("make it louder", "volume_up", 5, "Turned it up."), ("too loud, lower it", "volume_down", 5, "Turned it down."),
]
PC_VOLUME = [("set the volume to 40 percent", 40), ("volume 70%", 70), ("set volume to 20", 20), ("put the volume at 55 percent", 55)]
PC_FILES = [  # (request, function, args, reply builder key)
    ("what's in my downloads folder?", "list_files", {"path": "~/Downloads"}, "list"),
    ("show me what's on my desktop", "list_files", {"path": "~/Desktop"}, "list"),
    ("list the pdfs in my documents", "list_files", {"path": "~/Documents", "pattern": "*.pdf"}, "list"),
    ("find my resume on the pc", "find_files", {"query": "resume"}, "find"),
    ("find files named invoice", "find_files", {"query": "invoice"}, "find"),
]
PC_CONFIRMED = [  # (request, function, args, done reply, declined reply)
    ("type hello world", "type_text", {"text": "hello world"}, "Typed it.", "You declined on the PC, so I didn't type anything."),
    ("press ctrl+s to save", "press_keys", {"keys": "ctrl+s"}, "Pressed Ctrl+S.", "You declined on the PC, so I didn't press anything."),
    ("close notepad", "close_window", {"title": "Notepad"}, "Closed Notepad.", "You declined on the PC, so Notepad is still open."),
    ("delete old-report.txt from my desktop", "delete_file", {"path": "~/Desktop/old-report.txt"},
     "Moved old-report.txt to the Recycle Bin.", "You declined on the PC, so the file is still there."),
    ("save a file called todo.txt on my desktop that says buy milk", "write_file",
     {"path": "~/Desktop/todo.txt", "content": "buy milk"}, "Saved todo.txt on your desktop.", "You declined on the PC, so no file was saved."),
    ("take a screenshot and tell me what's on my screen", "screenshot", {},
     "Your screen shows a code editor with a Python file open and a terminal below it.",
     "You declined on the PC, so I didn't take a screenshot."),
]
PC_SIMPLE = [  # (request, function, args, reply)
    ("lock my pc", "lock_screen", {}, "Locked."), ("lock the screen", "lock_screen", {}, "Locked the screen."),
    ("what windows are open?", "list_windows", {}, "list_windows"), ("switch to chrome", "focus_window", {"title": "Chrome"}, "Switched to Chrome."),
    ("copy this to my clipboard: meeting at 5", "set_clipboard", {"text": "meeting at 5"}, "Copied it to your clipboard."),
]


class FakeDesktop:
    """Plays the desktop app for one user: mode 'ok', 'decline' (confirmed actions are
    declined) or 'offline' (not connected)."""

    def __init__(self, user_id: int) -> None:
        from app import desktop

        self.desktop, self.user_id, self.mode = desktop, user_id, "ok"
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            if self.mode == "offline":
                threading.Event().wait(0.2)
                continue
            action = self.desktop.next_action(self.user_id, "Home PC", wait=0.3)
            if not action:
                continue
            if self.mode == "decline" and action["confirm"]:
                self.desktop.finish(self.user_id, action["id"], False, error="The user declined this on the PC.")
            else:
                self.desktop.finish(self.user_id, action["id"], True, result=self.result(action["function"], action["args"]))

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if mode == "offline":
            self.desktop.reset()
        else:
            self.desktop.touch(self.user_id, "Home PC")

    @staticmethod
    def result(function: str, args: dict) -> dict:
        return {
            "system_info": {"time": "Thursday 15 October 2026, 18:20", "battery": {"percent": 76, "charging": False},
                            "cpu_percent": 12.0, "memory": {"used_percent": 58.0, "total_gb": 16.0},
                            "disks": [{"drive": "C:\\", "free_gb": 254.4, "total_gb": 885.9}], "active_window": "Chrome"},
            "list_files": {"folder": "C:\\Users\\you\\" + str(args.get("path", "~")).lstrip("~/\\").replace("/", "\\"),
                           "items": [{"name": "report-q3.pdf", "type": "file", "size_kb": 812.0, "modified": "2026-10-12 10:04"},
                                     {"name": "photos", "type": "folder", "size_kb": None, "modified": "2026-10-01 18:30"},
                                     {"name": "setup.exe", "type": "file", "size_kb": 52310.0, "modified": "2026-09-28 09:12"}],
                           "more": False},
            "find_files": {"query": args.get("query"), "matches": [f"C:\\Users\\you\\Documents\\{args.get('query')}-2026.pdf"], "more": False},
            "list_windows": {"windows": ["Inbox - Gmail - Google Chrome", "notes.txt - Notepad", "Spotify Premium"], "active": "Spotify Premium"},
            "screenshot": {"path": "C:\\Users\\you\\Pictures\\Custodian\\screenshot.png",
                           "description": "A code editor with a Python file open and a terminal below it."},
        }.get(function, {"done": function, **args})


class _Searx(BaseHTTPRequestHandler):
    facts: dict[str, list] = {}

    def do_GET(self):  # noqa: N802
        query = parse_qs(urlsplit(self.path).query).get("q", [""])[0].lower()
        results = next((r for q, r in self.facts.items() if q.lower() == query), [])
        body = json.dumps({"results": [{"title": t, "url": u, "content": s} for t, u, s in results]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # noqa: ANN002
        pass


def start_fake_searx() -> str:
    _Searx.facts = {query: results for _, query, results, _ in WEB_FACTS}
    server = HTTPServer(("127.0.0.1", 0), _Searx)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}"


def scenarios(world, per_kind: int, Scenario, Plan, pick) -> list:  # noqa: ANN001, N803 - make_dataset's types
    out = []
    desk: FakeDesktop = world.fake_desktop
    PCW = {"pc": ("read", "write")}

    def mode(m: str) -> Callable:
        return lambda w: desk.set_mode(m)

    def denied(results: list[dict]) -> str | None:
        r = results[0]
        if r["status"] == "denied":
            match = re.search(r"[“]([^”]+)[”]", r.get("error") or "")
            switch = match.group(1) if match else "PC"
            return f"I can't do that yet: switch on \u201c{switch}\u201d in Tools & Data."
        if r["status"] != "succeeded" and "isn't running" in (r.get("error") or ""):
            return "I can't reach your PC: the Custodian desktop app isn't running. Start it and ask me again."
        if r["status"] != "succeeded" and "declined" in (r.get("error") or ""):
            return None
        return None

    def info_reply(part: str) -> Callable:
        def reply(results: list[dict]) -> str:
            early = denied(results)
            if early:
                return early
            info = results[0]["result"]
            return {"battery": f"Your battery is at {info['battery']['percent']}%{', charging' if info['battery']['charging'] else ', not charging'}.",
                    "disk": f"You have {info['disks'][0]['free_gb']} GB free on C: (of {info['disks'][0]['total_gb']} GB).",
                    "cpu": f"Your CPU is at {info['cpu_percent']:.0f}% right now."}[part]
        return reply

    for _ in range(per_kind // 2):
        q, part = pick(PC_INFO)
        out.append(Scenario("pc_info", q, Plan([("pc_system_info", {})], info_reply(part)), grants={"pc": ("read",)},
                            setup=mode("ok"), expect_status={"system_info": "succeeded"}))

    def simple(text: str) -> Callable:
        return lambda results: denied(results) or text

    for _ in range(per_kind):
        request, target, label = pick(PC_OPEN)
        out.append(Scenario("pc_open", request, Plan([("pc_open", {"target": target})], simple(f"Opened {label}.")),
                            grants=PCW, setup=mode("ok"), expect_status={"open": "succeeded"}))
    for _ in range(per_kind // 2):
        request, action, steps, text = pick(PC_MEDIA)
        out.append(Scenario("pc_media", request, Plan([("pc_media", {"action": action, "steps": steps})], simple(text)),
                            grants=PCW, setup=mode("ok"), expect_status={"media": "succeeded"}))
    for _ in range(per_kind // 3):
        request, level = pick(PC_VOLUME)
        out.append(Scenario("pc_volume", request, Plan([("pc_set_volume", {"level": level})], simple(f"Set the volume to {level}%.")),
                            grants=PCW, setup=mode("ok"), expect_status={"set_volume": "succeeded"}))

    def files_reply(results: list[dict]) -> str:
        early = denied(results)
        if early:
            return early
        r = results[0]["result"]
        if "matches" in r:
            return "Found it:\n" + "\n".join(f"• {m}" for m in r["matches"]) if r["matches"] else "I didn't find any files with that name."
        return f"In {r['folder']}:\n" + "\n".join(f"• {i['name']}{' (folder)' if i['type'] == 'folder' else ''}" for i in r["items"])

    for _ in range(per_kind // 2):
        request, function, args, _ = pick(PC_FILES)
        out.append(Scenario("pc_files", request, Plan([(f"pc_{function}", args)], files_reply), grants={"pc": ("read",)},
                            setup=mode("ok"), expect_status={function: "succeeded"}))

    def confirmed_reply(done: str, declined: str) -> Callable:
        def reply(results: list[dict]) -> str:
            r = results[0]
            if r["status"] != "succeeded" and "declined" in (r.get("error") or ""):
                return declined
            return denied(results) or done
        return reply

    for _ in range(per_kind // 2):
        request, function, args, done, declined = pick(PC_CONFIRMED)
        scope = "read" if function == "screenshot" else "write"
        decline = world.rng.random() < 0.35
        out.append(Scenario("pc_confirmed", request, Plan([(f"pc_{function}", args)], confirmed_reply(done, declined)),
                            grants={"pc": (scope,)}, setup=mode("decline" if decline else "ok"),
                            expect_status={function: "failed" if decline else "succeeded"}))

    def windows_reply(results: list[dict]) -> str:
        return denied(results) or "Open windows:\n" + "\n".join(f"• {w}" for w in results[0]["result"]["windows"])

    for _ in range(per_kind // 3):
        request, function, args, text = pick(PC_SIMPLE)
        scope = "read" if function == "list_windows" else "write"
        out.append(Scenario("pc_simple", request,
                            Plan([(f"pc_{function}", args)], windows_reply if text == "list_windows" else simple(text)),
                            grants={"pc": (scope,)}, setup=mode("ok"), expect_status={function: "succeeded"}))

    for _ in range(per_kind // 4):  # the desktop app isn't running, or the switch is off: say so
        request, target, _ = pick(PC_OPEN)
        offline = world.rng.random() < 0.5
        out.append(Scenario("pc_unavailable", request, Plan([("pc_open", {"target": target})], simple("")),
                            grants=PCW if offline else {}, setup=mode("offline" if offline else "ok"),
                            expect_status={"open": "failed" if offline else "denied"}))

    def web_reply(answer: str) -> Callable:
        def reply(results: list[dict]) -> str:
            r = results[0]
            if r["status"] == "denied":
                return "I can't search the web yet: switch on \u201cWeb · read\u201d in Tools & Data."
            sources = "\n".join(f"Source: {x['url']}" for x in r["result"]["results"][:2])
            return f"{answer}\n{sources}" if sources else "I couldn't find that on the web."
        return reply

    for _ in range(per_kind):
        question, query, _, answer = pick(WEB_FACTS)
        granted = world.rng.random() < 0.85
        out.append(Scenario("web", question, Plan([("web_search", {"query": query})], web_reply(answer)),
                            grants={"web": ("read",)} if granted else {}, setup=mode("ok"),
                            expect_status={"search": "succeeded" if granted else "denied"}))
    return out
