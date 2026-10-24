"""Tools: Google Calendar, Gmail (reading, drafts, and sending after the user allows it) and
Google Drive (read only).

Run inside the sandbox like every tool, with two things the gate adds to `ctx` for these
only: a short-lived OAuth access token (never the refresh token) and `allow_hosts`, the
Google API hosts this process may reach - nothing else on the network. Each function is
gated by its own switch (Google Calendar · read, Gmail · read, ...), so connecting the
account doesn't by itself let the model read your mail.

Email and file contents are other people's words: the system prompt tells the model that
tool results are information, never instructions.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta
from html import unescape
from zoneinfo import ZoneInfo

CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
DRIVE_API = "https://www.googleapis.com/drive/v3"
HOSTS = ("www.googleapis.com", "gmail.googleapis.com")
MAX_TEXT = 6000


def _api(ctx: dict, default: str) -> str:
    return ctx.get("api_base") or default  # tests point this at a local fake


def _request(ctx: dict, url: str, *, method: str = "GET", body: dict | None = None, raw: bool = False):  # noqa: ANN202
    request = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {ctx['access_token']}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:  # noqa: S310 - fixed https hosts
            data = response.read(2_000_000)
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", "replace")
        if exc.code in (401, 403):
            raise PermissionError(f"Google refused the request ({exc.code}); reconnect Google in Tools & Data") from None
        raise RuntimeError(f"Google returned {exc.code}: {detail[:200]}") from None
    return data.decode("utf-8", "replace") if raw else json.loads(data)


def _text(value: str, limit: int = MAX_TEXT) -> str:
    value = re.sub(r"[ \t]+", " ", value).strip()
    return value if len(value) <= limit else value[:limit] + " …(cut)"


# Google Calendar ----------------------------------------------------------------------------

def _event(item: dict) -> dict:
    start, end = item.get("start", {}), item.get("end", {})
    video = next((e.get("uri") for e in (item.get("conferenceData") or {}).get("entryPoints", []) if e.get("entryPointType") == "video"), None)
    out = {
        "id": item.get("id"),
        "title": item.get("summary") or "(no title)",
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "location": item.get("location"),
    }
    if item.get("hangoutLink") or video:
        out["link"] = item.get("hangoutLink") or video  # the meeting's video link, for the heads-up before it
    return out


def list_events(ctx: dict, start_date: str | None = None, days: int = 1) -> dict:
    tz = ZoneInfo(ctx["timezone"])
    first = date.fromisoformat(start_date) if start_date else datetime.now(tz).date()
    last = first + timedelta(days=days - 1)
    query = urllib.parse.urlencode(
        {
            "timeMin": datetime.combine(first, time.min, tz).isoformat(),
            "timeMax": datetime.combine(last + timedelta(days=1), time.min, tz).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "50",
            "timeZone": ctx["timezone"],
        }
    )
    data = _request(ctx, f"{_api(ctx, CALENDAR_API)}/calendars/primary/events?{query}")
    return {"timezone": ctx["timezone"], "from": first.isoformat(), "to": last.isoformat(),
            "events": [_event(i) for i in data.get("items", [])]}


def create_event(ctx: dict, title: str, start: str, duration_minutes: int = 60, location: str | None = None) -> dict:
    tz = ZoneInfo(ctx["timezone"])
    begins = datetime.fromisoformat(start)
    if begins.tzinfo is None:
        begins = begins.replace(tzinfo=tz)
    body = {
        "summary": title,
        "start": {"dateTime": begins.isoformat(), "timeZone": ctx["timezone"]},
        "end": {"dateTime": (begins + timedelta(minutes=duration_minutes)).isoformat(), "timeZone": ctx["timezone"]},
    }
    if location:
        body["location"] = location
    data = _request(ctx, f"{_api(ctx, CALENDAR_API)}/calendars/primary/events", method="POST", body=body)
    return {"created": _event(data)}


# Gmail -------------------------------------------------------------------------------------

def _header(message: dict, name: str) -> str | None:
    for header in message.get("payload", {}).get("headers", []):
        if header.get("name", "").lower() == name.lower():
            return header.get("value")
    return None


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _body(part: dict) -> str:
    """The plain text of a message: text/plain if there is one, else text/html stripped."""
    plain, html = [], []

    def walk(node: dict) -> None:
        mime, data = node.get("mimeType", ""), node.get("body", {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            html.append(_decode(data))
        for child in node.get("parts", []) or []:
            walk(child)

    walk(part)
    if plain:
        return "\n".join(plain)
    return unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", "\n".join(html))))


def search_messages(ctx: dict, query: str = "", limit: int = 5) -> dict:
    base = _api(ctx, GMAIL_API)
    listing = _request(ctx, f"{base}/users/me/messages?" + urllib.parse.urlencode({"q": query, "maxResults": limit}))
    found = []
    for ref in listing.get("messages", [])[:limit]:
        message = _request(
            ctx,
            f"{base}/users/me/messages/{ref['id']}?format=metadata&metadataHeaders=From&metadataHeaders=Subject"
            "&metadataHeaders=Date",
        )
        found.append({"id": message["id"], "from": _header(message, "From"), "subject": _header(message, "Subject"),
                      "date": _header(message, "Date"), "snippet": unescape(message.get("snippet", ""))})
    return {"query": query, "messages": found}


def read_message(ctx: dict, id: str) -> dict:  # noqa: A002 - the model's argument name
    message = _request(ctx, f"{_api(ctx, GMAIL_API)}/users/me/messages/{id}?format=full")
    return {"id": id, "from": _header(message, "From"), "to": _header(message, "To"),
            "subject": _header(message, "Subject"), "date": _header(message, "Date"),
            "text": _text(_body(message.get("payload", {})))}


def _mime(ctx: dict, to: list[str], subject: str, body: str, cc: list[str], reply_to_id: str) -> dict:
    """The message as Gmail takes it ({"raw": base64url RFC 822, "threadId": ...}). A reply keeps the
    original's thread and subject, and says which message it answers."""
    from email.message import EmailMessage

    message = EmailMessage()
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    out: dict = {}
    if reply_to_id:
        original = _request(
            ctx, f"{_api(ctx, GMAIL_API)}/users/me/messages/{reply_to_id}?format=metadata"
                 "&metadataHeaders=Subject&metadataHeaders=Message-ID&metadataHeaders=References")
        their_id = _header(original, "Message-ID")
        if their_id:
            message["In-Reply-To"] = their_id
            message["References"] = " ".join(p for p in (_header(original, "References"), their_id) if p)
        before = _header(original, "Subject") or ""
        subject = subject or (before if before.lower().startswith("re:") else f"Re: {before}".strip())
        out["threadId"] = original.get("threadId")
    message["Subject"] = subject
    message.set_content(body)
    out["raw"] = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {k: v for k, v in out.items() if v}


def draft_email(ctx: dict, to: list[str], subject: str = "", body: str = "", cc: list[str] | None = None,
                reply_to_id: str = "") -> dict:
    """Save it in Gmail's Drafts, unsent - nothing leaves until they send it."""
    message = _mime(ctx, to, subject, body, cc or [], reply_to_id)
    draft = _request(ctx, f"{_api(ctx, GMAIL_API)}/users/me/drafts", method="POST", body={"message": message})
    return {"draft_id": draft.get("id"), "to": to, "cc": cc or [], "saved_in": "Gmail Drafts", "sent": False}


def send_email(ctx: dict, to: list[str], subject: str = "", body: str = "", cc: list[str] | None = None,
               reply_to_id: str = "") -> dict:
    """Send it (the gate asked the user first, with the exact recipients and text)."""
    message = _mime(ctx, to, subject, body, cc or [], reply_to_id)
    sent = _request(ctx, f"{_api(ctx, GMAIL_API)}/users/me/messages/send", method="POST", body=message)
    return {"id": sent.get("id"), "to": to, "cc": cc or [], "sent": True,
            **({"in_reply_to": reply_to_id} if reply_to_id else {})}


# Google Drive ------------------------------------------------------------------------------

EXPORTS = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}


def search_files(ctx: dict, query: str = "", limit: int = 5) -> dict:
    escaped = query.replace("\\", "\\\\").replace("'", "\\'")
    q = f"trashed = false and (name contains '{escaped}' or fullText contains '{escaped}')" if query else "trashed = false"
    fields = {"q": q, "pageSize": limit, "fields": "files(id,name,mimeType,modifiedTime)"}
    if not query:  # Drive can't sort a full-text search, only a plain listing
        fields["orderBy"] = "modifiedTime desc"
    params = urllib.parse.urlencode(fields)
    data = _request(ctx, f"{_api(ctx, DRIVE_API)}/files?{params}")
    return {"query": query, "files": [{"id": f["id"], "name": f["name"], "type": f.get("mimeType"),
                                       "modified": f.get("modifiedTime")} for f in data.get("files", [])]}


def read_file(ctx: dict, id: str) -> dict:  # noqa: A002 - the model's argument name
    base = _api(ctx, DRIVE_API)
    meta = _request(ctx, f"{base}/files/{id}?fields=id,name,mimeType")
    mime = meta.get("mimeType", "")
    if mime in EXPORTS:
        text = _request(ctx, f"{base}/files/{id}/export?" + urllib.parse.urlencode({"mimeType": EXPORTS[mime]}), raw=True)
    elif mime.startswith("text/") or mime in ("application/json", "application/csv"):
        text = _request(ctx, f"{base}/files/{id}?alt=media", raw=True)
    else:
        raise ValueError(f"{meta.get('name')} is a {mime} file; only Google Docs, Sheets, Slides and text files can be read")
    return {"id": id, "name": meta.get("name"), "type": mime, "text": _text(text)}
