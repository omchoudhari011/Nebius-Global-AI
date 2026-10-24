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


def _ascii_url(url: str) -> str:
    """'https://de.wikipedia.org/wiki/Jägermeister' as HTTP needs it: an IDNA host, a
    percent-encoded path and query (characters already encoded are left alone)."""
    parts = urllib.parse.urlsplit(url.strip())
    host = parts.hostname.encode("idna").decode("ascii") if parts.hostname else ""
    netloc = host + (f":{parts.port}" if parts.port else "")
    return urllib.parse.urlunsplit((parts.scheme, netloc, urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;="),
                                    urllib.parse.quote(parts.query, safe="=&%+/:,;@!$'()*?"), ""))


def _get(url: str, *, accept: str, timeout: float = 12) -> tuple[bytes, str, str]:
    """(body, content type, final URL) for an http(s) GET. Redirects are followed with the
    cookies they set (sites that redirect until you have one would otherwise loop); every hop
    is checked by the sandbox like the first."""
    request = urllib.request.Request(_ascii_url(url), headers={"User-Agent": USER_AGENT, "Accept": accept,
                                                               "Accept-Language": "en;q=0.9,*;q=0.5"})
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.read(MAX_PAGE_BYTES), response.headers.get("Content-Type", ""), response.geturl()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"the site answered {exc.code}" + (" (a redirect loop)" if 300 <= exc.code < 400 else "")) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"couldn't reach it ({exc.reason})") from None
    except UnicodeError as exc:
        raise RuntimeError(f"that link has characters it can't send ({exc})") from None


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


def page_snapshot(ctx: dict, url: str) -> dict:
    """Every line of a page's text, short ones too - prices and "In stock" are one or two words -
    for watching a page (app/watchers.py). Not a model tool: the watcher calls it in the sandbox."""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("only http and https pages can be watched")
    body, content_type, final = _get(url, accept="text/html,text/plain;q=0.9,*/*;q=0.1")
    charset = re.search(r"charset=([\w-]+)", content_type)
    raw = body.decode(charset.group(1) if charset else "utf-8", "replace")
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    html = re.sub(r"(?is)<(script|style|svg|noscript|head|iframe)\b.*?</\1>", " ", raw)
    html = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|td|span|section|article|header|pre|blockquote|button|a)>", "\n", html)
    lines = (" ".join(line.split()) for line in unescape(re.sub(r"<[^>]+>", " ", html)).splitlines())
    return {"url": final, "title": unescape(" ".join(title.group(1).split())) if title else "",
            "text": "\n".join(line for line in lines if line)[:40_000]}


def read_page(ctx: dict, url: str) -> dict:
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("only http and https pages can be read")
    final, title, text = _fetch_text(url)
    cut = len(text) > MAX_TEXT
    return {"url": final, "title": title, "text": text[:MAX_TEXT] + (" …(cut)" if cut else "")}


def _fetch_text(url: str, timeout: float = 12) -> tuple[str, str, str]:
    """(final URL, title, readable text) of a web page or plain-text file."""
    body, content_type, final = _get(url, accept="text/html,text/plain;q=0.9,*/*;q=0.1", timeout=timeout)
    charset = re.search(r"charset=([\w-]+)", content_type)
    raw = body.decode(charset.group(1) if charset else "utf-8", "replace")
    if "html" in content_type or raw.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        title, text = _page_text(raw)
        return final, title, text
    if content_type.startswith("text/") or not content_type:
        return final, "", raw
    raise ValueError(f"that link is a {content_type.split(';')[0]} file, not a web page")


# Research: several searches and pages at once, for a report -----------------------------------

EXCERPT = 1400  # characters kept from each page: the passages about the topic
SPARE = 3  # pages read beyond `pages`, so one that can't be read is replaced by the next
# Videos, and sites that show their text only to a signed-in browser: nothing to read as a page.
NOT_PAGES = ("youtube.com", "youtu.be", "reddit.com", "instagram.com", "facebook.com", "x.com", "twitter.com", "tiktok.com")
_WORD = re.compile(r"[a-z0-9][a-z0-9'-]{2,}")
_COMMON = {"the", "and", "for", "with", "that", "this", "what", "which", "best", "from", "about", "are", "how", "into",
           "under", "over", "your", "their", "there", "than", "then", "them", "they", "you", "can", "will", "should"}


def _keywords(*texts: str) -> set[str]:
    return {w for t in texts for w in _WORD.findall(t.lower()) if w not in _COMMON}


def _excerpt(text: str, keywords: set[str], limit: int = EXCERPT) -> str:
    """The paragraphs that say most about the topic, in the page's own order, up to `limit`."""
    paragraphs = [p for p in text.split("\n") if len(p.split()) >= 8]
    score = [len(keywords & set(_WORD.findall(p.lower()))) for p in paragraphs]
    scored = sorted(range(len(paragraphs)), key=lambda i: -score[i])
    keep, size = set(), 0
    for i in scored:
        if size >= limit or (score[i] == 0 and keep):  # nothing about the topic: only when nothing else is
            break
        keep.add(i)
        size += len(paragraphs[i])
    out = " ".join(paragraphs[i] for i in sorted(keep))
    return out[:limit] + (" …" if len(out) > limit else "")


def research(ctx: dict, topic: str, queries: list[str], pages: int = 5) -> dict:
    """Search the web several ways at once, read the best pages (several at a time, at most two
    from one site, so a report doesn't rest on one source), and return what each says about the
    topic, numbered for citing as [1], [2], ..."""
    from concurrent.futures import ThreadPoolExecutor

    searches = [q for q in dict.fromkeys([*queries, topic]) if q][:4]
    with ThreadPoolExecutor(max_workers=len(searches)) as pool:
        found = list(pool.map(lambda q: _safe(search, ctx, q, 8), searches))
    picked, per_site = [], {}
    for rank in range(8):  # the top result of each search first, then the second of each, ...
        for result in found:
            hits = (result or {}).get("results", [])
            if rank < len(hits):
                url = hits[rank]["url"]
                site = urllib.parse.urlsplit(url).hostname or ""
                if any(site == s or site.endswith("." + s) for s in NOT_PAGES):
                    continue
                if url not in {p["url"] for p in picked} and per_site.get(site, 0) < 2:
                    per_site[site] = per_site.get(site, 0) + 1
                    picked.append(hits[rank])
    picked = picked[:pages + SPARE]
    keywords = _keywords(topic, *queries)
    with ThreadPoolExecutor(max_workers=max(1, len(picked))) as pool:
        texts = list(pool.map(lambda hit: _safe(_fetch_text, hit["url"], 9), picked))
    sources, unread = [], []
    for hit, text in zip(picked, texts):
        excerpt = _excerpt(text[2], keywords) if text is not None else ""
        if len(excerpt) < 200:  # couldn't be read, or says next to nothing as text
            unread.append(hit["url"])
            continue
        if len(sources) < pages:
            sources.append({"n": len(sources) + 1, "title": text[1] or hit["title"], "url": text[0], "excerpt": excerpt})
    answers = [a for result in found for a in (result or {}).get("answers", [])][:2]
    return {"topic": topic, "searched": searches, "sources": sources, "answers": answers,
            **({"unread": unread} if unread else {}),
            **({"none": "No page could be read for this; try other words."} if not sources else {})}


def _safe(fn, *args):  # noqa: ANN001, ANN202 - one failed search or page mustn't sink the rest
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001
        return None
