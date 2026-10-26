"""Everyday commands that skip the model: exact matches only, through the same gate and checks."""

import threading

import pytest

from app import db, desktop, fastpath, orchestrator, permissions
from app.model_backends import ModelResponse, set_backend
from app.model_backends.base import ModelBackend


@pytest.mark.parametrize(
    "said, function, args",
    [
        ("next track please", "pc_media", {"action": "next", "steps": 1}),
        ("Hey Jarvis, skip this song", "pc_media", {"action": "next", "steps": 1}),
        ("pause the music", "pc_media", {"action": "play_pause", "steps": 1}),
        ("resume", "pc_media", {"action": "play_pause", "steps": 1}),
        ("Can you just stop?", "pc_media", {"action": "play_pause", "steps": 1}),  # right after a song started
        ("stop playing", "pc_media", {"action": "play_pause", "steps": 1}),
        ("volume up", "pc_media", {"action": "volume_up", "steps": 5}),
        ("set the volume to 40%", "pc_set_volume", {"level": 40}),
        ("volume 70 percent", "pc_set_volume", {"level": 70}),
        ("put on Bohemian Rhapsody", "pc_play", {"query": "bohemian rhapsody"}),
        ("play Perfect by Ed Sheeran on spotify", "pc_play", {"query": "perfect by ed sheeran", "service": "spotify"}),
        ("lock my pc", "pc_lock_screen", {}),
        ("dark mode", "pc_set_theme", {"mode": "dark"}),
        ("set a timer for 10 minutes", "pc_timer", {"seconds": 600, "label": "Timer"}),
        ("5 minute timer", "pc_timer", {"seconds": 300, "label": "Timer"}),
        ("set brightness to 30", "pc_set_brightness", {"level": 30}),
        ("open notepad", "pc_open", {"target": "notepad"}),
        ("open youtube.com", "pc_open", {"target": "youtube.com"}),
        ("what's my battery level?", "pc_system_info", {}),
        ("what's playing?", "pc_now_playing", {}),
        ("load localhost:3000", "pc_open", {"target": "localhost:3000"}),
        ("open https://www.irctc.co.in in chrome", "pc_open", {"target": "https://www.irctc.co.in"}),
        ("open gmail in chrome", "pc_open", {"target": "gmail"}),
        ("youtube Apna Bana Le", "pc_play", {"query": "apna bana le"}),
        ("find redmi note 13 on Flipkart", "pc_search_site", {"site": "flipkart", "query": "redmi note 13"}),
        ("open Amazon and search for a laptop stand", "pc_search_site", {"site": "amazon", "query": "a laptop stand"}),
        ("search youtube for pasta recipes", "pc_search_site", {"site": "youtube", "query": "pasta recipes"}),
        ("google who is the CEO of NVIDIA and open the top result", "web_search", {"query": "who is the ceo of nvidia"}),
        ("close this tab", "pc_press_keys", {"keys": "ctrl+w"}),
        ("refresh the page", "pc_press_keys", {"keys": "f5"}),
        ("press ctrl+s", "pc_press_keys", {"keys": "ctrl+s"}),
        ("undo that", "pc_press_keys", {"keys": "ctrl+z"}),
        ("show the desktop", "pc_press_keys", {"keys": "win+d"}),
        ("make my screen dimmer", "pc_set_brightness", {"level": 30}),
        ("switch to chrome", "pc_focus_window", {"title": "chrome"}),
        ("bring up the calculator", "pc_open", {"target": "calculator"}),
        ("open youtube and play kalyani", "pc_play", {"query": "kalyani"}),
        ("open my pcs download folder", "pc_open", {"target": "downloads"}),
        ("show me my documents folder", "pc_open", {"target": "documents"}),
        ("open desktop", "pc_open", {"target": "desktop"}),
        ("Open WhatsApp from my PC.", "pc_open", {"target": "whatsapp"}),
    ],
)
def test_exact_everyday_commands_are_recognised(said, function, args):
    intent = fastpath.match(said)
    assert intent is not None and (intent.function, intent.args) == (function, args)


@pytest.mark.parametrize("said", [
    "open my notes", "play the music", "set the volume to 400", "open the report I was working on yesterday",
    "next week's meetings", "what's my name", "pause for a second and tell me a joke", "lock in the meeting at 3",
    "play it", "open", "volume", "timer", "open report.pdf", "load my notes", "search for my notes on cooking",
    "find my resume", "play Kalyani and set the volume to 40", "close chrome and then lock the pc",
    "open notepad and write a poem",
])
def test_anything_else_goes_to_the_model(said):
    assert fastpath.match(said) is None


class NoModel(ModelBackend):
    name = "none"
    model_id = "none"

    def __init__(self):
        self.calls = 0

    def generate(self, messages, tools=None):
        self.calls += 1
        return ModelResponse("The desktop app couldn't do that just now.", [], self.model_id)


@pytest.fixture
def pc_on(user_id):
    with db.connect() as conn:
        tool = conn.execute("SELECT id FROM tools WHERE name = 'pc'").fetchone()["id"]
    permissions.grant(user_id, tool, "read")
    permissions.grant(user_id, tool, "write")
    desktop.reset()
    stop, outcome = threading.Event(), {"ok": True}

    def loop():
        while not stop.is_set():
            action = desktop.next_action(user_id, "Test PC", wait=0.3)
            if action:
                result = {"done": action["args"]["action"], "now_playing": [{"app": "Spotify", "title": "Arijit Singh - Kesariya"}]}
                desktop.finish(user_id, action["id"], outcome["ok"], result if outcome["ok"] else None,
                               None if outcome["ok"] else "the media keys aren't responding")

    worker = threading.Thread(target=loop, daemon=True)
    worker.start()
    desktop.touch(user_id, "Test PC")
    yield outcome
    stop.set()
    worker.join(timeout=2)
    desktop.reset()


def test_a_fast_command_runs_without_the_model_and_is_still_checked(user_id, pc_on):
    model = NoModel()
    set_backend(model)
    out = orchestrator.handle(user_id, "next track please")
    assert model.calls == 0
    assert out["reply"] == "Skipped to the next track - now playing Arijit Singh - Kesariya."
    assert [(t["function"], t["status"]) for t in out["used"]["tools"]] == [("media", "succeeded")]
    assert out["used"]["checks"]["status"] == "passed"


def test_a_failed_fast_command_is_explained_by_the_model(user_id, pc_on):
    pc_on["ok"] = False
    model = NoModel()
    set_backend(model)
    out = orchestrator.handle(user_id, "next track")
    assert model.calls == 1 and out["reply"] == "The desktop app couldn't do that just now."


def test_with_the_switch_off_the_gate_still_blocks_it(user_id):
    model = NoModel()
    set_backend(model)
    out = orchestrator.handle(user_id, "next track")
    assert [(t["function"], t["status"]) for t in out["used"]["tools"]] == [("media", "denied")]
    assert model.calls == 1  # the model explains the switch is off


def test_search_then_open_takes_the_top_link():
    intent = fastpath.match("search ISRO news and open the first link")
    step = intent.then({"results": [{"url": "https://www.isro.gov.in/news", "title": "ISRO"}]})
    assert step == ("pc_open", {"target": "https://www.isro.gov.in/news"})
    assert intent.then({"results": []}) is None  # nothing found: the model says so


# As people say it out loud (the voice lab's suite: voice-lab/suite.py) -----------------------------

@pytest.mark.parametrize(("said", "function", "args"), [
    ("set the volume to forty", "pc_set_volume", {"level": 40}),
    ("volume twenty five", "pc_set_volume", {"level": 25}),
    ("set a timer for ten minutes", "pc_timer", {"seconds": 600}),
    ("set a timer for half an hour", "pc_timer", {"seconds": 1800}),
    ("wake me up at seven in the morning", "pc_alarm", {"time": "07:00"}),
    ("wake me up at six thirty in the evening", "pc_alarm", {"time": "18:30"}),
    ("what's playing right now", "pc_now_playing", {}),
    ("play seven nation army", "pc_play", {"query": "seven nation army"}),  # a name keeps its words
    ("दस मिनट का टाइमर लगाओ", "pc_timer", {"seconds": 600}),
    ("10 मिनिट का टाइमर लगा", "pc_timer", {"seconds": 600}),
    ("गाना बंद करो", "pc_media", {"action": "play_pause"}),
    ("एक गाना बजाओ", "pc_play", {}),
])
def test_commands_as_theyre_said(said, function, args):
    intent = fastpath.match(said)
    if function is None:
        return  # not an exact command: the model takes it, and that's fine
    assert intent is not None and intent.function == function
    for key, value in args.items():
        assert intent.args[key] == value
