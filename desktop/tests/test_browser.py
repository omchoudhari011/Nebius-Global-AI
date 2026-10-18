"""Custodian's own browser over the DevTools protocol, against a real Chrome or Chromium and a local
shop page. Runs when CUSTODIAN_TEST_BROWSER names the browser to use (it starts headless, with a
throwaway profile); otherwise skipped."""

import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from custodian_desktop import actions, browser

BROWSER = os.environ.get("CUSTODIAN_TEST_BROWSER", "")
pytestmark = pytest.mark.skipif(not BROWSER, reason="set CUSTODIAN_TEST_BROWSER to a Chrome or Chromium to run these")

SHOP = b"""<!doctype html><title>Test shop</title>
<h1>Wireless earbuds</h1><p>Price: 1,999 rupees. In stock.</p>
<form action="/ordered" method="get">
  <label for=q>Search</label><input id=q name=q>
  <label for=pw>Password</label><input id=pw name=pw type=password>
  <button type=submit>Place order</button>
</form>
<a href="/reviews">Read reviews</a>
<a href="/invoice.txt" download>Invoice</a>"""


class _Shop(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/ordered"):
            body = b"<title>Order placed</title><p>Thanks! " + self.path.encode() + b"</p>"
        elif self.path == "/reviews":
            body = b"<title>Reviews</title><p>Great sound.</p>"
        elif self.path == "/invoice.txt":
            body = b"Invoice 42: 1999 rupees"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Disposition", "attachment; filename=invoice.txt")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        else:
            body = SHOP
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    home = tmp_path_factory.mktemp("browser")
    mp = pytest.MonkeyPatch()
    mp.setenv("CUSTODIAN_BROWSER", BROWSER)
    mp.setenv("CUSTODIAN_BROWSER_HEADLESS", "1")
    mp.setattr(browser, "PORT", _free_port())
    mp.setattr(browser, "PROFILE", home / "profile")
    mp.setattr(browser, "_downloads", lambda: home / "Downloads")
    server = HTTPServer(("127.0.0.1", 0), _Shop)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", home
    server.shutdown()
    try:  # close the headless browser
        for tab in browser.pages():
            browser._http(f"/json/close/{tab['id']}")
    except OSError:
        pass
    mp.undo()


def test_it_opens_reads_and_follows_a_link_without_asking(shop):
    base, _ = shop
    opened = actions.run("browser_open", {"url": base + "/"}, [])
    assert opened["title"] == "Test shop"
    page = actions.run("browser_read", {}, [])
    assert "1,999 rupees" in page["text"]
    names = {e["name"]: e for e in page["elements"]}
    assert names["Search"]["kind"] == "field" and names["Place order"]["kind"] == "button"
    assert names["Password"]["type"] == "password" and "value" not in names["Password"]
    actions.confirm_hook = None  # nobody to ask: a plain link still works
    clicked = actions.run("browser_click", {"ref": names["Read reviews"]["ref"]}, [])
    assert clicked["navigated"] and clicked["page"] == "Reviews"


def test_a_risky_click_or_send_asks_first_and_nobody_answering_means_no(shop):
    base, _ = shop
    actions.run("browser_open", {"url": base + "/", "new_tab": False}, [])
    page = actions.run("browser_read", {}, [])
    order = next(e["ref"] for e in page["elements"] if e["name"] == "Place order")
    actions.confirm_hook = None
    with pytest.raises(browser.BrowserError, match="declined"):
        actions.run("browser_click", {"ref": order}, [])
    asked = []
    actions.confirm_hook = lambda title, details: asked.append(details) or False
    with pytest.raises(browser.BrowserError, match="declined"):
        actions.run("browser_fill", {"name": "Search", "text": "earbuds", "submit": True}, [])
    assert "earbuds" in asked[0] and "Test shop" in asked[0]
    assert actions.run("browser_read", {}, [])["title"] == "Test shop"  # nothing was sent

    actions.confirm_hook = lambda title, details: asked.append(details) or True
    filled = actions.run("browser_fill", {"name": "Search", "text": "earbuds pro", "submit": False}, [])
    assert filled == {"filled": "Search", "characters": 11}
    done = actions.run("browser_click", {"name": "Place order"}, [])
    assert done["page"] == "Order placed" and "q=earbuds+pro" in done["url"]
    assert "Place order" in asked[-1]
    actions.confirm_hook = None


def test_passwords_are_never_typed(shop):
    base, _ = shop
    actions.run("browser_open", {"url": base + "/", "new_tab": False}, [])
    with pytest.raises(browser.BrowserError, match="never types passwords"):
        actions.run("browser_fill", {"name": "Password", "text": "hunter2"}, [])


def test_a_download_lands_in_downloads(shop):
    base, home = shop
    actions.run("browser_open", {"url": base + "/", "new_tab": False}, [])
    saved = actions.run("browser_download", {"name": "Invoice"}, [])
    assert saved["saved"].endswith("invoice.txt") and (home / "Downloads" / "invoice.txt").read_text() == "Invoice 42: 1999 rupees"


def test_tabs_can_be_listed_switched_and_closed(shop):
    base, _ = shop
    actions.run("browser_open", {"url": base + "/reviews"}, [])
    tabs = actions.run("browser_tabs", {}, [])["tabs"]
    assert len(tabs) >= 2 and tabs[0]["title"] == "Reviews"
    after = actions.run("browser_tabs", {"action": "close", "tab": 1}, [])["tabs"]
    assert len(after) == len(tabs) - 1
