"""Research for a report: several searches and pages at once (web_research), and a Word document
whose sources are only the ones really found (pc_write_document)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import grounding, orchestrator, skills
from app.model_backends import ModelResponse, ToolCallRequest, set_backend
from app.model_backends.base import ModelBackend
from app.tools import registry, sandbox
from app.tools.base import ToolRequest
from app.tools import web_tools

LAPTOP = ("<p>The Vivobook 16 costs Rs 62,990 and its battery lasted 11 hours in our review, the longest of any laptop "
          "under 80000 we tested this year.</p><p>Its 16-inch display is bright enough for a sunny room, and the laptop "
          "weighs 1.8 kg, light for its size; the keyboard is comfortable for long typing sessions.</p>"
          "<p>Subscribe to our newsletter for the latest deals and offers every single week of the year.</p>")


class _Web(BaseHTTPRequestHandler):
    port = 0

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/search"):
            here, there = f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"
            body = json.dumps({"results": [{"title": f"Review {n}", "url": f"{here}/page{n}", "content": "x"} for n in (1, 2, 3)]
                              + [{"title": "Guide", "url": f"{there}/guide", "content": "y"},
                                 {"title": "Broken", "url": f"{there}/missing", "content": "z"}],
                               "answers": []}).encode()
            kind = "application/json"
        elif self.path == "/missing":
            self.send_response(404)
            self.end_headers()
            return
        else:
            body = f"<html><title>{self.path[1:]}</title><body>{LAPTOP}</body></html>".encode()
            kind = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def web():
    server = HTTPServer(("127.0.0.1", 0), _Web)
    _Web.port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_research_reads_several_sites_at_once_and_keeps_what_matters(web, tmp_path):
    ctx = {"user_id": 1, "data_dir": str(tmp_path), "timezone": "UTC", "searx_url": web,
           "allow_hosts": ["127.0.0.1", "localhost"]}
    request = ToolRequest("app.tools.web_tools", "research",
                          {"topic": "laptops under 80000", "queries": ["best laptop under 80000", "laptop battery review"],
                           "pages": 5}, ctx)
    result = sandbox.SubprocessSandbox().run(request, 50)
    urls = [s["url"] for s in result["sources"]]
    assert [s["n"] for s in result["sources"]] == list(range(1, len(urls) + 1))
    assert sum("127.0.0.1" in u for u in urls) == 2  # at most two pages from one site
    assert any(u.endswith("/guide") for u in urls) and result["unread"][0].endswith("/missing")
    excerpt = result["sources"][0]["excerpt"]
    assert "Rs 62,990" in excerpt and "11 hours" in excerpt
    assert result["searched"] == ["best laptop under 80000", "laptop battery review", "laptops under 80000"]


def test_the_passages_about_the_topic_are_kept_and_the_rest_dropped():
    text = "\n".join(["Sign up for our newsletter to get the best deals in your inbox every week.",
                      "The Pixel 9 camera handles low light better than the iPhone 16 in every test we ran.",
                      "Cookies help us give you a better experience on this website, read more here."])
    kept = web_tools._excerpt(text, web_tools._keywords("pixel 9 iphone 16 camera"), limit=90)
    assert kept.startswith("The Pixel 9 camera") and "newsletter" not in kept


def test_a_report_request_is_routed_to_research_with_the_document_tool():
    said = "Research the best laptops under 80000 and put it in a Word document"
    assert skills.pick_skill_name(said) == "research"
    offered = {f.exposed_name for f in registry.offer(registry.for_scopes(None), said)}
    assert {"pc_write_document"} <= offered and registry.find("web_research").timeout >= 45


# The document's sources: only what a tool returned ------------------------------------------------

class _Script(ModelBackend):
    name = "script"
    model_id = "script-1"

    def __init__(self, *responses):
        self.responses = list(responses)

    def generate(self, messages, tools=None):
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def test_an_invented_source_never_reaches_the_document(user_id, monkeypatch):
    real = "https://www.example-reviews.in/laptops-under-80000"
    sent: list = []

    def execute(user_id, name, raw_args, **_):
        if name == "web_research":
            return sandbox.ToolCallOutcome(name=name, status="succeeded", tool="web", function="research",
                                           result={"sources": [{"n": 1, "title": "Review", "url": real, "excerpt": "..."}]})
        sent.append(raw_args)
        return sandbox.ToolCallOutcome(name=name, status="succeeded", tool="pc", function="write_document",
                                       result={"saved": "C:/Users/me/Documents/Laptops.docx"})

    monkeypatch.setattr(sandbox, "execute", execute)
    document = {"title": "Laptops", "sections": [{"heading": "Summary", "text": "Get the Vivobook [1]."}],
                "sources": [{"title": "Review", "url": real}, {"title": "Made up", "url": "https://invented.example/x"}]}
    set_backend(_Script(
        ModelResponse("", [ToolCallRequest("r1", "web_research", {"topic": "laptops", "queries": ["laptops"]})], "script-1"),
        ModelResponse("", [ToolCallRequest("d1", "pc_write_document", document)], "script-1"),
        ModelResponse("The Vivobook is the pick; the report is in Documents as Laptops.docx.", [], "script-1"),
    ))
    out = orchestrator.handle(user_id, "Research laptops under 80000 and put it in a Word document")
    assert [s["url"] for s in sent[0]["sources"]] == [real]
    assert "Laptops.docx" in out["reply"]


def test_keep_real_sources_leaves_a_clean_document_alone():
    evidence = grounding.Evidence(message="x", outcomes=[])
    args = {"title": "T", "sections": [], "sources": []}
    assert grounding.keep_real_sources(args, evidence) is args
    assert grounding.keep_real_sources("not json", evidence) == "not json"


# The weather -------------------------------------------------------------------------------------

def test_the_weather_comes_in_celsius_for_the_city_asked_about(monkeypatch):
    calls = []

    def fake_get(url, *, accept, timeout=12):
        calls.append(url)
        if "geocoding" in url:
            body = {"results": [{"name": "Pune", "admin1": "Maharashtra", "country": "India", "latitude": 18.5, "longitude": 73.9}]}
        else:
            body = {"current": {"temperature_2m": 31.5, "apparent_temperature": 33.5, "weather_code": 51, "wind_speed_10m": 6,
                                "relative_humidity_2m": 39},
                    "daily": {"time": ["2026-10-24", "2026-10-25"], "weather_code": [51, 3], "temperature_2m_max": [31.5, 32.4],
                              "temperature_2m_min": [23.0, 21.5], "precipitation_probability_max": [52, 36]}}
        return json.dumps(body).encode(), "application/json", url

    monkeypatch.setattr(web_tools, "_get", fake_get)
    out = web_tools.weather({}, "Pune", 2)
    assert out["place"] == "Pune, Maharashtra, India" and out["now"]["temperature_c"] == 31.5
    assert out["days"][1] == {"date": "2026-10-25", "condition": "overcast", "high_c": 32.4, "low_c": 21.5, "rain_chance": 36}
    assert "name=Pune" in calls[0] and "forecast_days=2" in calls[1]
    monkeypatch.setattr(web_tools, "_get", lambda url, **_: (b'{"results": []}', "application/json", url))
    with pytest.raises(ValueError, match="ask which city"):
        web_tools.weather({}, "Atlantis")
    assert skills.pick_skill_name("will it rain tomorrow in Mumbai?") == "web"
    assert registry.find("web_weather").hosts == ("geocoding-api.open-meteo.com", "api.open-meteo.com")


def test_ill_set_a_reminder_is_a_promise_the_check_holds_to():
    evidence = grounding.Evidence(message="remind me at 7", outcomes=[])
    assert "unverified_reminder" in [f.kind for f in grounding.review("Will do - I'll set a reminder for 07:00.", evidence)]


# Arguments a small model sends almost right ------------------------------------------------------

def test_lists_sent_as_one_string_are_mended_before_they_are_checked():
    research = registry.find("web_research")
    odd = '["iPhone 16 camera review"]\n["Pixel 9 camera review"]\n["camera comparison"]\n["one too many"]'
    assert registry.fix_arguments(research, {"topic": "x", "queries": odd})["queries"] == [
        "iPhone 16 camera review", "Pixel 9 camera review", "camera comparison"]  # cut to the 3 allowed
    email = registry.fix_arguments(registry.find("gmail_send_email"), {"to": "priya@example.com", "body": "Hi"})
    assert email["to"] == ["priya@example.com"]
    doc = registry.find("pc_write_document")
    sections = '[{"heading": "Summary", "text": "Get A [1]."}]\n[{"heading": "Top picks", "text": "- A\n- B"}]'
    fixed = doc.args_model.model_validate(registry.fix_arguments(doc, {"title": "T", "sections": sections}))
    assert [s.heading for s in fixed.sections] == ["Summary", "Top picks"]
    assert doc.args_model.model_validate({"title": "T", "body": "## Summary\nGet A [1]."}).body.startswith("## Summary")
    with pytest.raises(ValueError, match="give the document"):
        doc.args_model.model_validate({"title": "T"})


def test_an_excuse_is_checked_against_the_real_switches():
    def kinds(reply, switches, *outcomes):
        return [f.kind for f in grounding.review(reply, grounding.Evidence(message="x", switches=switches, outcomes=list(outcomes)))]

    assert "false_excuse" in kinds("I couldn't search the web: Web · read is off.", {"Web · read": True})
    assert "false_excuse" in kinds("I couldn't set it - the reminders tool wasn't available.", {"Reminders · write": True})
    assert kinds("I can't add events: Calendar · write is switched off in Tools & Data.", {"Calendar · write": False}) == []
    denied = sandbox.ToolCallOutcome(name="web_search", status="denied", tool="web", function="search")
    assert kinds("Web · read is switched off.", {"Web · read": True}, denied) == []  # it was denied: true
    assert grounding.drop_invented_sources("Pune is 28°C today. [1]", grounding.Evidence(message="x")) == "Pune is 28°C today."


# What a reply says about dates and reminders ------------------------------------------------------

def test_a_weekday_that_doesnt_match_its_date_is_put_right():
    from datetime import date

    today = date(2026, 10, 24)  # a Saturday
    assert grounding.fix_weekdays("Added for tomorrow, Sat 25 Oct, 15:00.", today) == "Added for tomorrow, Sun 25 Oct, 15:00."
    assert grounding.fix_weekdays("On Monday, 26th October.", today) == "On Monday, 26th October."  # already right
    assert grounding.fix_weekdays("fri 1 Jan", today) == "fri 1 Jan"  # 1 Jan 2027 is a Friday


def test_a_reminder_is_saved_as_the_task_and_confirmed_from_what_was_saved():
    from app import orchestrator
    from app.tools.reminders_tool import task_text

    assert task_text("दवाई लेने की याद दिलाना") == "दवाई लेना" and task_text("remind me to call mum") == "call mum"
    made = sandbox.ToolCallOutcome(name="reminders_create_reminder", status="succeeded", tool="reminders",
                                   function="create_reminder",
                                   result={"created": {"text": "दवाई लेना", "when": "Sun 25 Oct, 07:00", "repeat": None}})
    assert orchestrator._reminder_confirmation([made], hindi=True) == "ठीक है, याद दिलाऊँगा: दवाई लेना - रविवार, 25 अक्टूबर को 07:00 बजे।"
    made.result["created"]["text"] = "Take medicine"
    assert orchestrator._reminder_confirmation([made], hindi=False) == "Done - I'll remind you: Take medicine, Sun 25 Oct, 07:00."
    other = sandbox.ToolCallOutcome(name="calendar_create_event", status="succeeded", tool="calendar", function="create_event")
    assert orchestrator._reminder_confirmation([made, other], hindi=False) is None  # more than reminders: the model's reply
