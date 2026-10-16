"""Tools: search the web and read a page - Custodian's general knowledge.

web_search asks the SearXNG instance on this machine (SEARXNG_URL), which queries public
search engines; read_page fetches one public web page and returns its readable text. Both
run in the sandbox: search may reach only the SearXNG host, and read_page only public
internet addresses (never this machine or the local network), redirects included.

Web pages are written by strangers. The system prompt tells the model that tool results are
information, never instructions - and a PC action a page asks for still needs your switch
and, for anything that changes something, your confirmation on the desktop.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html import unescape

MAX_PAGE_BYTES = 2_000_000
MAX_TEXT = 8000
USER_AGENT = "Mozilla/5.0 (compatible; Custodian personal assistant)"


def _get(url: str, *, accept: str) -> tuple[bytes, str, str]:
    """(body, content type, final URL) for an http(s) GET."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:  # noqa: S310 - scheme checked by the caller
            return response.read(MAX_PAGE_BYTES), response.headers.get("Content-Type", ""), response.geturl()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"the site answered {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"couldn't reach it ({exc.reason})") from None


def search(ctx: dict, query: str, limit: int = 5) -> dict:
    base = ctx["searx_url"].rstrip("/")
    body, _, _ = _get(f"{base}/search?" + urllib.parse.urlencode({"q": query, "format": "json"}), accept="application/json")
    data = json.loads(body)
    results = [
        {"title": r.get("title", "").strip(), "url": r.get("url"), "snippet": " ".join((r.get("content") or "").split())[:300]}
        for r in data.get("results", [])
        if r.get("url", "").startswith(("http://", "https://"))
    ][:limit]
    answers = [a if isinstance(a, str) else a.get("answer", "") for a in data.get("answers", [])][:2]
    boxes = [{"title": b.get("infobox"), "text": " ".join((b.get("content") or "").split())[:600]} for b in data.get("infoboxes", [])][:1]
    return {"query": query, "answers": [a for a in answers if a], "infobox": boxes, "results": results}


def _page_text(html: str) -> tuple[str, str]:
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    main = re.search(r"(?is)<(main|article)\b[^>]*>(.*)</\1>", html)  # skip menus and sidebars when marked
    if main and len(main.group(2)) > 500:
        html = main.group(2)
    html = re.sub(r"(?is)<(script|style|svg|noscript|head|nav|footer|form|iframe)\b.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|section|article|header|pre|blockquote)>", "\n", html)
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    lines = (" ".join(line.split()) for line in text.splitlines())
    # Sentences, not menus: lines of a few words are links, buttons and language lists.
    return (unescape(" ".join(title.group(1).split())) if title else ""), "\n".join(line for line in lines if len(line.split()) >= 5)


def read_page(ctx: dict, url: str) -> dict:
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("only http and https pages can be read")
    body, content_type, final = _get(url, accept="text/html,text/plain;q=0.9,*/*;q=0.1")
    charset = re.search(r"charset=([\w-]+)", content_type)
    raw = body.decode(charset.group(1) if charset else "utf-8", "replace")
    if "html" in content_type or raw.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        title, text = _page_text(raw)
    elif content_type.startswith("text/") or not content_type:
        title, text = "", raw
    else:
        raise ValueError(f"that link is a {content_type.split(';')[0]} file, not a web page")
    cut = len(text) > MAX_TEXT
    return {"url": final, "title": title, "text": text[:MAX_TEXT] + (" …(cut)" if cut else "")}
