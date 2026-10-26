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


SITE_URLS = {"youtube": "https://www.youtube.com", "gmail": "https://mail.google.com", "whatsapp web": "https://web.whatsapp.com",
             "netflix": "https://www.netflix.com", "github": "https://github.com", "instagram": "https://www.instagram.com",
             "amazon": "https://www.amazon.in", "chatgpt": "https://chatgpt.com"}
VIDEO_TITLES = {"kalyani": "KALYANI (with Shreya Ghoshal) ARJN | KDS | FIFTY4 | RONN", "lofi music": "lofi hip hop radio - beats to relax/study to",
                "believer": "Imagine Dragons - Believer (Official Music Video)", "kesariya": "Kesariya - Brahmastra | Arijit Singh",
                "tum hi ho": "Tum Hi Ho - Aashiqui 2 | Arijit Singh", "python tutorial": "Python Tutorial for Beginners - Full Course"}
SPOTIFY_TITLES = {"shape of you": "Ed Sheeran - Shape of You", "kesariya": "Arijit Singh - Kesariya",
                  "my liked songs": "Coldplay - Viva La Vida", "lofi beats": "Lofi Fruits - Snowman"}


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
            "open": {"opened": SITE_URLS.get(str(args.get("target", "")).lower(), args.get("target")),
                     "kind": "web page" if str(args.get("target", "")).lower() in SITE_URLS or "://" in str(args.get("target"))
                     else "file" if "\\" in str(args.get("target")) else "app"},
            "go_to": {"opened": "https://duckduckgo.com/?q=%5C" + str(args.get("query", "")).replace(" ", "%20"),
                      "for": args.get("query"), "kind": "web page"},
            "search_site": {"opened": f"https://www.{args.get('site')}.com/search?q=" + str(args.get("query", "")).replace(" ", "+"),
                            "searched": args.get("site"), "for": args.get("query")},
            "play": ({"service": "youtube", "playing": VIDEO_TITLES.get(str(args.get("query")).lower(), str(args.get("query")).title()),
                      "url": "https://www.youtube.com/watch?v=z5y8Clp_TdE", "started": True}
                     if args.get("service", "youtube") == "youtube" else
                     {"service": "spotify", "searched": args.get("query"), "started": True,
                      "now_playing": [{"app": "Spotify", "title": SPOTIFY_TITLES.get(str(args.get("query")).lower(), str(args.get("query")).title())}]}),
            "now_playing": {"now_playing": [{"app": "Spotify", "title": "Arijit Singh - Kesariya"}]},
            "media": {"done": args.get("action"), **({"now_playing": [{"app": "Spotify", "title": "Arijit Singh - Kesariya"}]}
                                                      if args.get("action") in ("next", "previous") else {})},
            "set_brightness": {"brightness": args.get("level")},
            "set_theme": {"theme": args.get("mode")},
            "open_settings": {"opened": f"Settings: {args.get('page')}"},
            "timer": {"timer": args.get("label", "Timer"), "rings_at": "18:30:00", "seconds": args.get("seconds")},
            "list_apps": {"apps": [{"app": "chrome", "windows": 2, "memory_mb": 850}, {"app": "Spotify", "windows": 1, "memory_mb": 310},
                                   {"app": "Code", "windows": 1, "memory_mb": 290}, {"app": "WhatsApp.Root", "windows": 1, "memory_mb": 140}]},
            "close_app": {"closed": str(args.get("name", "")).lower(), "windows_closed": 2, "still_open": None, "running": False},
            "copy_file": {"copied": args.get("source"), "to": args.get("destination")},
            "move_file": {"moved": args.get("source"), "to": args.get("destination")},
            "create_folder": {"folder": args.get("path"), "already_existed": False},
            "organize_folder": {"folder": args.get("path"), "moved": {"Images": 12, "Documents": 7, "Installers": 3},
                                "files_moved": 22},
            "write_in_app": {"app": args.get("app", "notepad"), "window": "Untitled - Notepad" if args.get("app", "notepad") == "notepad"
                             else "Document1 - Word", "characters": len(str(args.get("text", "")))},
            "read_screen": {"window": "WhatsApp", "controls": [{"name": "Search or start a new chat", "type": "Edit"},
                            {"name": "Type a message", "type": "Edit"}, {"name": "Send", "type": "Button"},
                            {"name": "Attach", "type": "Button"}, {"name": "Voice call", "type": "Button"}],
                            "text": ["Mum", "Dinner at 8?"]},
            "click_element": {"clicked": args.get("name"), "type": "Button", "window": args.get("window") or "WhatsApp"},
            "send_message": {"sent_to": str(args.get("contact", "")).title(), "app": "WhatsApp",
                             "characters": len(str(args.get("text", "")))},
            "call": {"calling": str(args.get("contact", "")).title(), "app": "WhatsApp",
                     "kind": "video call" if args.get("video") else "voice call"},
            "fill_field": {"filled": args.get("name"), "type": "Edit", "characters": len(str(args.get("text", ""))),
                           "window": args.get("window") or "WhatsApp"},
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


SITES_ASKED = [("open youtube", "youtube", "YouTube"), ("open gmail", "gmail", "Gmail"), ("open whatsapp web", "whatsapp web", "WhatsApp Web"),
               ("open netflix", "netflix", "Netflix"), ("open github", "github", "GitHub"), ("go to youtube", "youtube", "YouTube"),
               ("open chatgpt", "chatgpt", "ChatGPT"), ("open instagram", "instagram", "Instagram")]
GO_TO = [("take me to the wikipedia of jagermister", "Jägermeister Wikipedia", "the Jägermeister Wikipedia page"),
         ("take me to the IRCTC login page", "IRCTC login", "the IRCTC login page"),
         ("show me the wikipedia page for Virat Kohli", "Virat Kohli Wikipedia", "Virat Kohli's Wikipedia page"),
         ("take me to the official python docs", "Python documentation", "the Python documentation"),
         ("go to the Pune weather page", "Pune weather", "the Pune weather page"),
         ("open the website of Tata Motors", "Tata Motors official website", "Tata Motors' website"),
         ("take me to the jagermeister website", "Jägermeister official website", "the Jägermeister website")]
SEARCH_SITE = [("search amazon for wireless earbuds", "amazon", "wireless earbuds", "Amazon"),
               ("search youtube for python tutorials", "youtube", "python tutorials", "YouTube"),
               ("find cheap running shoes on flipkart", "flipkart", "cheap running shoes", "Flipkart"),
               ("search google for best biryani near me", "google", "best biryani near me", "Google"),
               ("look up Kalyani on wikipedia", "wikipedia", "Kalyani", "Wikipedia"),
               ("show me pictures of the Taj Mahal", "images", "Taj Mahal", "Google Images"),
               ("search maps for coffee shops near me", "maps", "coffee shops near me", "Google Maps")]
PLAY = [("play kalyani from youtube", "kalyani", "youtube"), ("play kalyani on youtube", "kalyani", "youtube"),
        ("play kalyani now", "kalyani", "youtube"), ("play some lofi music", "lofi music", "youtube"),
        ("play believer by imagine dragons", "believer", "youtube"), ("play tum hi ho", "tum hi ho", "youtube"),
        ("play a python tutorial video", "python tutorial", "youtube"), ("play shape of you on spotify", "shape of you", "spotify"),
        ("play kesariya on spotify", "kesariya", "spotify"), ("play lofi beats on spotify", "lofi beats", "spotify")]
SYSTEM = [("set brightness to 50", "pc_set_brightness", {"level": 50}, "Set the brightness to 50%."),
          ("dim the screen", "pc_set_brightness", {"level": 30}, "Dimmed the screen to 30%."),
          ("make the screen brighter", "pc_set_brightness", {"level": 90}, "Turned the brightness up to 90%."),
          ("turn on dark mode", "pc_set_theme", {"mode": "dark"}, "Switched Windows to dark mode."),
          ("switch to light mode", "pc_set_theme", {"mode": "light"}, "Switched Windows to light mode."),
          ("open bluetooth settings", "pc_open_settings", {"page": "bluetooth"}, "Opened the Bluetooth settings."),
          ("i want to connect to a wifi network", "pc_open_settings", {"page": "wifi"}, "Opened the Wi-Fi settings - pick the network there."),
          ("open display settings", "pc_open_settings", {"page": "display"}, "Opened the display settings."),
          ("set a timer for 10 minutes", "pc_timer", {"seconds": 600, "label": "Timer"}, "Timer set for 10 minutes - it rings at 18:30."),
          ("start a 5 minute timer for the tea", "pc_timer", {"seconds": 300, "label": "Tea"}, "Started a 5-minute tea timer."),
          ("set a 30 second timer", "pc_timer", {"seconds": 30, "label": "Timer"}, "Timer set for 30 seconds.")]
WRITE_IN_APP = [
    ("write a leave letter for tomorrow in notepad", "notepad",
     "Dear Sir/Madam,\n\nI am writing to request leave for tomorrow due to personal reasons. I will make sure my work is covered "
     "and will be available on my phone if needed.\n\nThank you for your understanding.\n\nYours sincerely,\nOm Choudhari",
     "I wrote the leave letter in Notepad - check it and add the date before you send it."),
    ("open notepad and write a shopping list: milk, eggs, bread", "notepad", "Shopping list\n- Milk\n- Eggs\n- Bread",
     "Wrote your shopping list in Notepad."),
    ("write a short poem about rain in word", "word",
     "Rain\n\nSoft rain taps on the window pane,\nwashing the dust from the sleepy lane;\nthe world grows quiet, the air turns new,\n"
     "and every leaf holds a drop of blue.", "Wrote a short poem about rain in a new Word document."),
    ("type a note in notepad saying call the plumber at 5", "notepad", "Call the plumber at 5.", "Done - it's in Notepad."),
]


def jarvis_scenarios(world, per_kind: int, Scenario, Plan, pick, denied, mode) -> list:  # noqa: ANN001, N803, C901
    """Everyday "do it for me" requests, including the ones that tripped up v4, and multi-step tasks."""
    out = []
    PCW = {"pc": ("read", "write")}
    BOTH = {"pc": ("read", "write"), "web": ("read",)}

    def simple(text: str) -> Callable:
        return lambda results: denied(results) or text

    def declined_or(done: str, declined: str) -> Callable:
        def reply(results: list[dict]) -> str:
            last = results[-1]
            if last["status"] != "succeeded" and "declined" in (last.get("error") or ""):
                return declined
            return denied(results) or done
        return reply

    for _ in range(per_kind // 2):
        request, target, label = pick(SITES_ASKED)
        out.append(Scenario("pc_site", request, Plan([("pc_open", {"target": target})], simple(f"Opened {label}.")),
                            grants=PCW, setup=mode("ok"), expect_status={"open": "succeeded"}))
    for _ in range(per_kind // 2):
        request, query, label = pick(GO_TO)
        out.append(Scenario("pc_go_to", request, Plan([("pc_go_to", {"query": query})], simple(f"Opened {label} in your browser.")),
                            grants=PCW, setup=mode("ok"), expect_status={"go_to": "succeeded"}))
    for _ in range(per_kind // 2):
        request, site, query, label = pick(SEARCH_SITE)
        out.append(Scenario("pc_search_site", request,
                            Plan([("pc_search_site", {"site": site, "query": query})], simple(f"Here are the {label} results for “{query}”.")),
                            grants=PCW, setup=mode("ok"), expect_status={"search_site": "succeeded"}))

    def play_reply(results: list[dict]) -> str:
        early = denied(results)
        if early:
            return early
        r = results[0]["result"]
        if r["service"] == "spotify":
            playing = (r.get("now_playing") or [{}])[0].get("title")
            return f"Playing {playing} on Spotify." if r.get("started") and playing else (
                f"I opened Spotify's search for “{r['searched']}” but couldn't start it - pick a result to play.")
        return f"Playing “{r['playing']}” on YouTube." if r.get("started") else "I couldn't find that video; I opened the search results instead."

    for _ in range(per_kind):
        request, query, service = pick(PLAY)
        args = {"query": query} if service == "youtube" else {"query": query, "service": "spotify"}
        out.append(Scenario("pc_play", request, Plan([("pc_play", args)], play_reply), grants=PCW, setup=mode("ok"),
                            expect_status={"play": "succeeded"}))

    def playing_reply(results: list[dict]) -> str:
        playing = results[0]["result"].get("now_playing")
        return denied(results) or (f"You're listening to {playing[0]['title']} on {playing[0]['app']}." if playing else "Nothing is playing right now.")

    for _ in range(per_kind // 4):
        request = pick(["what's playing?", "what song is this?", "which song is playing right now?", "what music is playing?"])
        out.append(Scenario("pc_now_playing", request, Plan([("pc_now_playing", {})], playing_reply), grants=PCW,
                            setup=mode("ok"), expect_status={"now_playing": "succeeded"}))
    for _ in range(per_kind):
        request, function, args, text = pick(SYSTEM)
        out.append(Scenario("pc_system", request, Plan([(function, args)], simple(text)), grants=PCW, setup=mode("ok"),
                            expect_status={function.removeprefix("pc_"): "succeeded"}))

    def apps_reply(results: list[dict]) -> str:
        return denied(results) or "Running now:\n" + "\n".join(
            f"• {a['app'].removesuffix('.Root')} ({a['memory_mb']} MB)" for a in results[0]["result"]["apps"])

    for _ in range(per_kind // 4):
        request = pick(["what apps are running?", "which apps are open right now?", "show me the running apps"])
        out.append(Scenario("pc_apps", request, Plan([("pc_list_apps", {})], apps_reply), grants=PCW, setup=mode("ok"),
                            expect_status={"list_apps": "succeeded"}))
    for _ in range(per_kind // 3):
        request, name, label = pick([("close chrome", "chrome", "Chrome"), ("quit spotify", "spotify", "Spotify"),
                                     ("close whatsapp", "whatsapp", "WhatsApp"), ("kill vs code", "vs code", "VS Code")])
        decline = world.rng.random() < 0.3
        out.append(Scenario("pc_close_app", request,
                            Plan([("pc_close_app", {"name": name})],
                                 declined_or(f"Closed {label}.", f"You declined on the PC, so {label} is still open.")),
                            grants=PCW, setup=mode("decline" if decline else "ok"),
                            expect_status={"close_app": "failed" if decline else "succeeded"}))
    for _ in range(per_kind // 3):
        request, path, label = pick([("organize my downloads folder", "~/Downloads", "Downloads"),
                                     ("clean up my desktop", "~/Desktop", "desktop"), ("sort the files in my downloads", "~/Downloads", "Downloads")])
        decline = world.rng.random() < 0.3
        out.append(Scenario("pc_organize", request,
                            Plan([("pc_organize_folder", {"path": path})],
                                 declined_or(f"Sorted 22 files in your {label}: 12 into Images, 7 into Documents and 3 into Installers.",
                                             f"You declined on the PC, so your {label} is unchanged.")),
                            grants=PCW, setup=mode("decline" if decline else "ok"),
                            expect_status={"organize_folder": "failed" if decline else "succeeded"}))
    for _ in range(per_kind // 4):
        request, path, label = pick([("make a folder called Trips on my desktop", "~/Desktop/Trips", "Trips"),
                                     ("create a new folder named Projects in documents", "~/Documents/Projects", "Projects")])
        out.append(Scenario("pc_folder", request, Plan([("pc_create_folder", {"path": path})], simple(f"Created the {label} folder.")),
                            grants=PCW, setup=mode("ok"), expect_status={"create_folder": "succeeded"}))
    for _ in range(per_kind // 2):
        request, app, text, done = pick(WRITE_IN_APP)
        decline = world.rng.random() < 0.2
        out.append(Scenario("pc_write_in_app", request,
                            Plan([("pc_write_in_app", {"app": app, "text": text})],
                                 declined_or(done, "You declined on the PC, so nothing was written.")),
                            grants=PCW, setup=mode("decline" if decline else "ok"),
                            expect_status={"write_in_app": "failed" if decline else "succeeded"}))

    # Multi-step: each step uses what the one before returned. -------------------------------------
    def first_match(results: list[dict]) -> list:
        r = results[-1]
        return [("pc_open", {"target": r["result"]["matches"][0]})] if r["status"] == "succeeded" and r["result"]["matches"] else []

    for _ in range(per_kind // 2):
        request, query = pick([("find my resume and open it", "resume"), ("open my invoice pdf", "invoice"),
                               ("find the project report and open it", "report"), ("open my offer letter", "offer letter")])
        out.append(Scenario(
            "pc_multi_find_open", request,
            Plan([("pc_find_files", {"query": query})],
                 lambda results: denied(results[-1:]) or f"Opened {results[0]['result']['matches'][0].split(chr(92))[-1]}.",
                 then=[first_match]),
            grants=PCW, setup=mode("ok"), expect_status={"find_files": "succeeded", "open": "succeeded"}))

    def first_link(results: list[dict]) -> list:
        r = results[-1]
        return [("pc_open", {"target": r["result"]["results"][0]["url"]})] if r["status"] == "succeeded" and r["result"]["results"] else []

    for _ in range(per_kind // 3):
        question, query, _results, answer = pick([f for f in WEB_FACTS if "news" in f[0] or "who" in f[0] or "weather" in f[0]])
        request = pick([f"search {query} and open the first link", f"look up {query} and open the top result"])
        out.append(Scenario(
            "pc_multi_search_open", request,
            Plan([("web_search", {"query": query})],
                 lambda results, a=answer: f"{a}\nI opened the source in your browser.\nSource: {results[0]['result']['results'][0]['url']}",
                 then=[first_link]),
            grants=BOTH, setup=mode("ok"), expect_status={"search": "succeeded", "open": "succeeded"}))
    for _ in range(per_kind // 3):
        request, title = pick([("close all chrome windows and lock the pc", "chrome"), ("quit spotify and lock my computer", "spotify")])
        out.append(Scenario(
            "pc_multi_close_lock", request,
            Plan([("pc_close_app", {"name": title})], declined_or(f"Closed {title.title()} and locked the PC.",
                                                                  f"You declined closing {title.title()}, so I didn't lock the PC either."),
                 then=[lambda results: [("pc_lock_screen", {})] if results[-1]["status"] == "succeeded" else []]),
            grants=PCW, setup=mode("ok"), expect_status={"close_app": "succeeded", "lock_screen": "succeeded"}))
    for _ in range(per_kind // 3):
        request, query, level = pick([("play kalyani and set the volume to 30", "kalyani", 30),
                                      ("put on some lofi music at 20 percent volume", "lofi music", 20)])
        out.append(Scenario(
            "pc_multi_play_volume", request,
            Plan([("pc_play", {"query": query}), ("pc_set_volume", {"level": level})],
                 lambda results, lv=level: f"Playing “{results[0]['result']['playing']}” on YouTube, volume at {lv}%."),
            grants=PCW, setup=mode("ok"), expect_status={"play": "succeeded", "set_volume": "succeeded"}))

    def click_send(results: list[dict]) -> list:
        names = [c["name"] for c in results[-1]["result"]["controls"]] if results[-1]["status"] == "succeeded" else []
        return [("pc_click_element", {"name": "Send", "window": "WhatsApp"})] if "Send" in names else []

    for _ in range(per_kind // 3):
        request = pick(["click the send button in whatsapp", "press send in whatsapp", "hit the Send button on WhatsApp"])
        decline = world.rng.random() < 0.25
        out.append(Scenario(
            "pc_multi_screen", request,
            Plan([("pc_read_screen", {"window": "WhatsApp"})],
                 declined_or("Clicked Send in WhatsApp.", "You declined on the PC, so nothing was sent."), then=[click_send]),
            grants=PCW, setup=mode("decline" if decline else "ok"),
            expect_status={"read_screen": "succeeded", "click_element": "failed" if decline else "succeeded"}))

    # WhatsApp: the exact chat and words; sent only after "Allow once" (declines and wrong chats happen).
    people = ["Neha", "Arjun", "Riya", "Mom", "Vikram", "Ananya", "Rohit", "Simran", "Boss", "Kabir"]
    words = ["I'm stuck in traffic, will be late", "see you at the station", "please call me back", "happy anniversary!",
             "running 5 minutes late", "the report is ready", "can we meet tomorrow?", "I reached home safely",
             "don't forget the tickets", "thank you so much"]
    phrasings = ["send a whatsapp to {p} saying {w}", "whatsapp {p} {w}", "message {p} on whatsapp: {w}", "text {p} on whatsapp that {w}",
                 "send {p} a whatsapp message - {w}", "on whatsapp tell {p} {w}", "whatsapp {p}: {w}", "send a message to {p} on whatsapp saying {w}"]

    def whatsapp_reply(results: list[dict]) -> str:
        r = results[-1]
        error = r.get("error") or ""
        if r["status"] == "succeeded":
            return f"Sent to {r['result']['sent_to']} on WhatsApp."
        if "declined" in error:
            return "You declined on the PC, so nothing was sent."
        if "no WhatsApp chat called" in error:
            return "I couldn't find that chat in WhatsApp, so nothing was sent. Check the name as it's saved in WhatsApp."
        return denied(results) or "Nothing was sent."

    for _ in range(per_kind):
        person, text = pick(people), pick(words)
        request = pick(phrasings).format(p=person, w=text)
        outcome = world.rng.random()
        setup = mode("decline") if outcome < 0.2 else mode("ok")
        status = "failed" if outcome < 0.2 else "succeeded"
        out.append(Scenario("pc_whatsapp", request, Plan([("pc_send_message", {"contact": person, "text": text})], whatsapp_reply),
                            grants=PCW, setup=setup, expect_status={"send_message": status}))

    # Everyday phrasings that don't name the action.
    loose = [("put on {s}", "play"), ("i want to listen to {s}", "play"), ("can you play {s} for me", "play"), ("start {s} on youtube", "play")]
    for _ in range(per_kind // 2):
        template, _kind = pick(loose)
        query = pick(["believer", "kesariya", "tum hi ho", "lofi music"])
        out.append(Scenario("pc_play", template.format(s=query), Plan([("pc_play", {"query": query})], play_reply),
                            grants=PCW, setup=mode("ok"), expect_status={"play": "succeeded"}))
    for _ in range(per_kind // 3):
        app, target, label = pick([("calculator", "calculator", "the Calculator"), ("the terminal", "terminal", "Terminal"),
                                   ("paint", "paint", "Paint"), ("excel", "excel", "Excel"), ("word", "word", "Word")])
        request = pick(["fire up {a}", "bring up {a}", "can you open {a} for me", "i need {a}"]).format(a=app)
        out.append(Scenario("pc_open", request, Plan([("pc_open", {"target": target})], simple(f"Opened {label}.")),
                            grants=PCW, setup=mode("ok"), expect_status={"open": "succeeded"}))
    for _ in range(per_kind // 3):
        site, label = pick([("linkedin", "LinkedIn"), ("netflix", "Netflix"), ("github", "GitHub"), ("instagram", "Instagram")])
        request = pick(["i want to see {s}", "pull up {s}", "show me {s}"]).format(s=site)
        out.append(Scenario("pc_site", request, Plan([("pc_open", {"target": site})], simple(f"Opened {label}.")),
                            grants=PCW, setup=mode("ok"), expect_status={"open": "succeeded"}))

    def found_reply(results: list[dict]) -> str:
        pc_hit = next((r for r in results if r.get("function") == "find_files" and r["status"] == "succeeded"), None)
        if pc_hit and pc_hit["result"]["matches"]:
            return "On your PC:\n" + "\n".join(f"• {m}" for m in pc_hit["result"]["matches"][:10])
        return "I couldn't find it on your PC or in the files you've uploaded."

    for _ in range(per_kind // 3):  # "find my X" with no place: both the PC and the uploads, in one step
        thing = pick(["seminar ppt", "electricity bill", "passport scan", "salary slip"])
        out.append(Scenario(
            "files_find_both", pick(["find my {t}", "where's my {t}", "look for my {t} file"]).format(t=thing),
            Plan([("pc_find_files", {"query": thing.split()[-1]}), ("documents_search_documents", {"query": thing})], found_reply),
            grants={"pc": ("read", "write"), "documents": ("read",)}, setup=mode("ok"),
            expect_status={"find_files": "succeeded"}))

    def battery_then(results: list[dict]) -> list:
        r = results[-1]
        return [("pc_set_brightness", {"level": 40})] if r["status"] == "succeeded" and r["result"]["battery"]["percent"] < 80 else []

    for _ in range(per_kind // 4):
        out.append(Scenario(
            "pc_multi_battery", pick(["check my battery and if it's below 80 dim the screen to save power",
                                      "if my battery is under 80 percent, lower the brightness"]),
            Plan([("pc_system_info", {})],
                 lambda results: f"Your battery is at {results[0]['result']['battery']['percent']}%, so I dimmed the screen to 40% to save power.",
                 then=[battery_then]),
            grants=PCW, setup=mode("ok"), expect_status={"system_info": "succeeded", "set_brightness": "succeeded"}))
    return out


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
    def media_reply(text: str) -> Callable:
        def reply(results: list[dict]) -> str:
            early = denied(results)
            if early:
                return early
            playing = (results[0].get("result") or {}).get("now_playing")
            return f"{text.rstrip('.')} - now playing {playing[0]['title']}." if playing else text
        return reply

    for _ in range(per_kind // 2):
        request, action, steps, text = pick(PC_MEDIA)
        out.append(Scenario("pc_media", request, Plan([("pc_media", {"action": action, "steps": steps})], media_reply(text)),
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

    out += jarvis_scenarios(world, per_kind, Scenario, Plan, pick, denied, mode)

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
