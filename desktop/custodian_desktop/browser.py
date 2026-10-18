"""Real browser automation: Custodian drives Chrome (or Edge) directly through its DevTools
protocol, in a browser profile of its own, instead of pressing keys - so web tasks work reliably:
filling forms, reading pages you're signed into there, downloading, several steps in a row.

- The browser runs with its own profile (%APPDATA%\\Custodian\\browser-profile): sign in to the
  sites you want Custodian to use there once; your everyday profile is never touched. Its
  DevTools port listens on this PC only (127.0.0.1).
- Pages are read with their buttons, links and fields numbered (`ref`), so a click or a fill
  names exactly one element. What a page says is information, never instructions.
- Clicking a link or a tab, and typing into a search box, just happen. Anything that submits,
  buys, pays, sends, books, posts or deletes asks first - "Allow once" on the PC, naming the
  button and the page - and is never done if nobody answers (confirm_hook in actions.py).
  Password fields are never filled: you type those yourself.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = int(os.environ.get("CUSTODIAN_BROWSER_PORT", "9223"))
PROFILE = Path(os.environ.get("APPDATA", Path.home())) / "Custodian" / "browser-profile"
LOAD_SECONDS = 20
MAX_TEXT = 6000
MAX_ELEMENTS = 80
# What makes a click or an Enter "do something": it asks first.
RISKY = re.compile(r"\b(buy|pay|purchase|place (?:your )?order|order now|checkout|check out|submit|send|post|publish|"
                   r"book|reserve|confirm|delete|remove|transfer|donate|subscribe|unsubscribe|sign up|register|apply|"
                   r"save changes|accept|agree|continue to payment|proceed|tweet|reply)\b", re.I)


class BrowserError(RuntimeError):
    pass


def _executable() -> str:
    """Chrome, else Edge, else whatever CUSTODIAN_BROWSER names."""
    named = os.environ.get("CUSTODIAN_BROWSER", "").strip()
    if named:
        return named
    roots = [os.environ.get(v, "") for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    for root in filter(None, roots):
        for part in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"):
            path = Path(root) / part
            if path.exists():
                return str(path)
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found
    raise BrowserError("Chrome or Edge wasn't found; install Chrome, or set CUSTODIAN_BROWSER to its path")


def _http(path: str, method: str = "GET", timeout: float = 3):  # noqa: ANN202
    """The DevTools HTTP endpoints: JSON, or plain text ("Target activated")."""
    request = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - this PC's own DevTools port
        body = response.read()
    try:
        return json.loads(body or b"null")
    except ValueError:
        return body.decode("utf-8", "replace")


def _new_tab(url: str) -> dict:
    try:
        return _http(f"/json/new?{url}", method="PUT")
    except urllib.error.HTTPError as exc:  # browsers before Chrome 111 want GET
        if exc.code != 405:
            raise
        return _http(f"/json/new?{url}")


def running() -> bool:
    try:
        _http("/json/version", timeout=1)
        return True
    except OSError:
        return False


def ensure(headless: bool = False) -> None:
    """Start Custodian's browser if it isn't running (its own profile, its own port)."""
    if running():
        return
    PROFILE.mkdir(parents=True, exist_ok=True)
    args = [_executable(), f"--remote-debugging-port={PORT}", "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={PROFILE}", "--no-first-run", "--no-default-browser-check", "--disable-sync"]
    if headless or os.environ.get("CUSTODIAN_BROWSER_HEADLESS"):
        args += ["--headless=new", "--no-sandbox", "--disable-gpu"]
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603 - fixed argv
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if running():
            return
        time.sleep(0.25)
    raise BrowserError("Custodian's browser didn't start")


def pages() -> list[dict]:
    return [t for t in _http("/json/list") if t.get("type") == "page" and not t.get("url", "").startswith("devtools://")]


class Page:
    """One tab, over its DevTools WebSocket."""

    def __init__(self, target: dict) -> None:
        import websocket

        self.target = target
        self._ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=LOAD_SECONDS, suppress_origin=True)
        self._ids = itertools.count(1)

    def send(self, method: str, params: dict | None = None) -> dict:
        call = next(self._ids)
        self._ws.send(json.dumps({"id": call, "method": method, "params": params or {}}))
        while True:
            reply = json.loads(self._ws.recv())
            if reply.get("id") == call:
                if "error" in reply:
                    raise BrowserError(f"{method}: {reply['error'].get('message')}")
                return reply.get("result", {})

    def js(self, expression: str):  # noqa: ANN201
        result = self.send("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in result:
            raise BrowserError("the page's script failed: " + str(result["exceptionDetails"].get("text", ""))[:200])
        return result.get("result", {}).get("value")

    def wait_loaded(self, seconds: float = LOAD_SECONDS) -> None:
        deadline = time.monotonic() + seconds
        time.sleep(0.3)  # let a click start its navigation
        while time.monotonic() < deadline:
            try:
                if self.js("document.readyState") == "complete":
                    return
            except Exception:  # noqa: BLE001 - mid-navigation
                pass
            time.sleep(0.25)

    def close(self) -> None:
        self._ws.close()


def _page(tab: int | None = None) -> Page:
    ensure()
    tabs = pages()
    if not tabs:
        tabs = [_new_tab("about:blank")]
    if tab is not None and not 1 <= tab <= len(tabs):
        raise BrowserError(f"there's no tab {tab}; there are {len(tabs)}")
    return Page(tabs[(tab or 1) - 1])  # Chrome lists the tab used last first


# An element's name, as a person would read it: its label, placeholder, text or value.
LABEL_JS = r"""const label = (e) => {
    const byFor = e.id && document.querySelector(`label[for="${CSS.escape(e.id)}"]`);
    return (e.getAttribute('aria-label') || (byFor && byFor.innerText) || (e.closest('label') && e.closest('label').innerText)
      || e.getAttribute('placeholder') || e.getAttribute('title') || e.getAttribute('name') || e.innerText || e.value || e.getAttribute('alt') || '')
      .replace(/\s+/g, ' ').trim().slice(0, 80);
  };"""

# Marks every visible button, link and field with data-custodian-ref and lists them.
READ_JS = r"""
(() => {
  const max = %(max)d, refs = [];
  const visible = (e) => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none'; };
  %(label)s
  let n = 0;
  document.querySelectorAll('[data-custodian-ref]').forEach((e) => e.removeAttribute('data-custodian-ref'));
  for (const e of document.querySelectorAll('a[href], button, input, select, textarea, [role=button], [role=link], [role=tab], [role=menuitem], [role=checkbox], summary, [contenteditable=true]')) {
    if (refs.length >= max || !visible(e) || (e.type === 'hidden')) continue;
    const ref = String(++n);
    e.setAttribute('data-custodian-ref', ref);
    const kind = e.tagName === 'A' ? 'link' : e.tagName === 'SELECT' ? 'choice' : (e.tagName === 'INPUT' || e.tagName === 'TEXTAREA' || e.isContentEditable)
      ? (['submit', 'button', 'reset'].includes(e.type) ? 'button' : ['checkbox', 'radio'].includes(e.type) ? e.type : 'field') : (e.getAttribute('role') || 'button');
    const item = {ref, kind, name: label(e)};
    if (kind === 'field') { item.type = e.type || 'text'; if (e.type !== 'password' && e.value) item.value = String(e.value).slice(0, 80); }
    if (kind === 'checkbox' || kind === 'radio') item.checked = e.checked;
    if (e.tagName === 'A') item.href = e.href.slice(0, 200);
    refs.push(item);
  }
  return {title: document.title, url: location.href, text: (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').slice(0, %(text)d), elements: refs};
})()
"""

# What one element is: for the risk check and the confirmation's wording.
INFO_JS = r"""
(() => {
  const e = document.querySelector('[data-custodian-ref="%(ref)s"]');
  if (!e) return null;
  const form = e.form || e.closest('form');
  %(label)s
  const name = label(e);
  const submits = (e.tagName === 'BUTTON' && (e.type || 'submit') === 'submit' && !!form) || (e.tagName === 'INPUT' && ['submit', 'image'].includes(e.type));
  return {name, tag: e.tagName, type: e.type || '', submits, password: e.type === 'password', form: !!form, href: e.href || ''};
})()
"""

FIND_JS = r"""
(() => {
  %(label)s
  const want = %(want)s.toLowerCase(), all = [...document.querySelectorAll('[data-custodian-ref]')];
  const exact = all.find((e) => label(e).toLowerCase() === want);
  const partial = want && all.find((e) => label(e).toLowerCase().includes(want));
  return (exact || partial || null) && (exact || partial).getAttribute('data-custodian-ref');
})()
"""

FILL_JS = r"""
((ref, text) => {
  const e = document.querySelector(`[data-custodian-ref="${ref}"]`);
  e.scrollIntoView({block: 'center'}); e.focus();
  if (e.isContentEditable) { e.innerText = text; }
  else if (e.tagName === 'SELECT') {
    const option = [...e.options].find((o) => o.text.trim().toLowerCase() === text.toLowerCase() || o.value === text);
    if (!option) throw new Error('no such choice');
    e.value = option.value;
  } else {
    const proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(e, text);  // so React and friends see it
  }
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
})(%s, %s)
"""


def _ref(page: Page, ref: str | None, name: str | None) -> str:
    """The element's ref: as given, or the one whose name matches (after reading the page)."""
    if ref:
        digits = re.sub(r"[^0-9]", "", str(ref))  # it goes into the page's script: a number, nothing else
        if not digits:
            raise BrowserError(f"{ref!r} isn't an element's ref from pc_browser_read")
        return digits
    if not name:
        raise BrowserError("say which element: its ref from pc_browser_read, or its name")
    page.js(READ_JS % {"label": LABEL_JS, "max": MAX_ELEMENTS * 3, "text": 0})  # number the elements afresh
    found = page.js(FIND_JS % {"label": LABEL_JS, "want": json.dumps(name)})
    if not found:
        raise BrowserError(f"nothing called {name!r} is on the page; read it again")
    return found


def _info(page: Page, ref: str) -> dict:
    info = page.js(INFO_JS % {"label": LABEL_JS, "ref": re.sub(r"[^0-9]", "", ref)})
    if info is None:
        raise BrowserError(f"element {ref} isn't on the page any more; read it again")
    return info


def _ask(confirm_hook, title: str, details: str) -> None:  # noqa: ANN001
    if confirm_hook is None or not confirm_hook(title, details):
        raise BrowserError("The user declined this on the PC.")  # nobody to ask = never done


def _where(page: Page) -> str:
    return f"{page.js('document.title') or ''}\n{page.js('location.href')}"


# The actions (actions.py maps pc_browser_* here) --------------------------------------------------

def browser_open(url: str, new_tab: bool = True, **_: object) -> dict:
    if not re.match(r"^https?://", url):
        url = "https://" + url
    ensure()
    if new_tab or not pages():
        page = Page(_new_tab(url))
    else:
        page = _page()
        page.send("Page.navigate", {"url": url})
    try:
        page.wait_loaded()
        return {"opened": page.js("location.href"), "title": page.js("document.title")}
    finally:
        page.close()


def browser_read(tab: int | None = None, **_: object) -> dict:
    page = _page(tab)
    try:
        page.wait_loaded(5)
        out = page.js(READ_JS % {"label": LABEL_JS, "max": MAX_ELEMENTS, "text": MAX_TEXT})
        out["tabs"] = [{"tab": i + 1, "title": t.get("title", "")[:80]} for i, t in enumerate(pages())][:10]
        return out
    finally:
        page.close()


def browser_click(ref: str | None = None, name: str | None = None, *, confirm_hook=None, **_: object) -> dict:  # noqa: ANN001
    page = _page()
    try:
        ref = _ref(page, ref, name)
        info = _info(page, ref)
        if info["submits"] or RISKY.search(info["name"]):
            _ask(confirm_hook, "Click on a web page", f"Click “{info['name'] or 'this button'}” on\n{_where(page)}")
        before = page.js("location.href")
        page.js(f"(() => {{ const e = document.querySelector('[data-custodian-ref=\"{ref}\"]'); "
                "e.scrollIntoView({block: 'center'}); e.click(); return true; })()")
        page.wait_loaded()
        after = page.js("location.href")
        return {"clicked": info["name"] or f"element {ref}", "page": page.js("document.title"), "url": after,
                "navigated": after != before}
    finally:
        page.close()


def browser_fill(text: str, ref: str | None = None, name: str | None = None, submit: bool = False, *,
                 confirm_hook=None, **_: object) -> dict:  # noqa: ANN001
    page = _page()
    try:
        ref = _ref(page, ref, name)
        info = _info(page, ref)
        if info["password"]:
            raise BrowserError("Custodian never types passwords: type it yourself in Custodian's browser")
        if submit:  # Enter sends the form: ask first, with what will be sent where
            _ask(confirm_hook, "Send a form on a web page", f"Type into “{info['name']}”:\n{text[:300]}\n\nand send it on\n{_where(page)}")
        page.js(FILL_JS % (json.dumps(ref), json.dumps(text)))
        out = {"filled": info["name"] or f"field {ref}", "characters": len(text)}
        if submit:
            page.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13,
                                                 "text": "\r"})
            page.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
            page.wait_loaded()
            out.update(sent=True, page=page.js("document.title"), url=page.js("location.href"))
        return out
    finally:
        page.close()


def _downloads() -> Path:
    from .actions import known_folder

    return known_folder("downloads") or Path.home() / "Downloads"


def browser_download(url: str | None = None, ref: str | None = None, name: str | None = None, **_: object) -> dict:
    """Download a link (or the file at `url`) into Downloads, and wait for it to finish."""
    folder = _downloads()
    folder.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in folder.iterdir()}
    page = _page()
    try:
        page.send("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(folder)})
        if url:
            page.js(f"(() => {{ const a = document.createElement('a'); a.href = {json.dumps(url)}; a.download = ''; "
                    "document.body.appendChild(a); a.click(); a.remove(); return true; })()")
        else:
            ref = _ref(page, ref, name)
            page.js(f"document.querySelector('[data-custodian-ref=\"{ref}\"]').click()")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            new = [p for p in folder.iterdir() if p.name not in before]
            done = [p for p in new if not p.name.endswith((".crdownload", ".tmp", ".partial"))]
            if done and not any(p.name.endswith(".crdownload") for p in new):
                saved = max(done, key=lambda p: p.stat().st_mtime)
                return {"saved": str(saved), "bytes": saved.stat().st_size}
            time.sleep(0.5)
        raise BrowserError("the download didn't finish within two minutes")
    finally:
        page.close()


def browser_tabs(action: str = "list", tab: int | None = None, **_: object) -> dict:
    ensure()
    tabs = pages()
    if action != "list":
        if tab is None or not 1 <= tab <= len(tabs):
            raise BrowserError(f"say which tab, 1 to {len(tabs)}")
        target = tabs[tab - 1]
        _http(f"/json/{'activate' if action == 'switch' else 'close'}/{target['id']}")
        time.sleep(0.3)
        tabs = pages()
    return {"tabs": [{"tab": i + 1, "title": t.get("title", "")[:80], "url": t.get("url", "")[:200]} for i, t in enumerate(tabs)]}
