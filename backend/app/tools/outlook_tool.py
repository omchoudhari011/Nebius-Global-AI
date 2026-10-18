"""Tools: Outlook mail (read only) and calendar in Microsoft 365, through Microsoft Graph.

Runs in the sandbox like every tool; the gate adds a short-lived access token and lets this
process reach only graph.microsoft.com. Email is other people's words: the system prompt
tells the model that tool results are information, never instructions.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta
from html import unescape
from zoneinfo import ZoneInfo

API = "https://graph.microsoft.com/v1.0"
HOSTS = ("graph.microsoft.com",)
MAX_TEXT = 6000


def _request(ctx: dict, path: str, *, method: str = "GET", body: dict | None = None, query: dict | None = None,
             headers: dict | None = None):  # noqa: ANN202
    url = (ctx.get("api_base") or API) + path + ("?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote) if query else "")
    request = urllib.request.Request(
        url, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {ctx['access_token']}", "Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:  # noqa: S310 - fixed https host
            data = response.read(2_000_000)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise PermissionError(f"Microsoft refused the request ({exc.code}); reconnect Microsoft 365 in Tools & Data") from None
        raise RuntimeError(f"Microsoft Graph returned {exc.code}: {exc.read(300).decode('utf-8', 'replace')}") from None
    return json.loads(data) if data.strip() else {}


def _text(value: str, limit: int = MAX_TEXT) -> str:
    value = re.sub(r"[ \t]+", " ", value).strip()
    return value if len(value) <= limit else value[:limit] + " …(cut)"


def _html_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", value)
    return unescape(re.sub(r"<[^>]+>", " ", value))


def _sender(message: dict) -> str:
    who = (message.get("from") or {}).get("emailAddress") or {}
    return f"{who.get('name', '')} <{who.get('address', '')}>".strip()


def search_mail(ctx: dict, query: str = "", unread_only: bool = False, limit: int = 5) -> dict:
    params = {"$top": str(limit), "$select": "id,subject,from,receivedDateTime,bodyPreview,isRead"}
    headers = None
    if query:
        params["$search"] = f'"{query.replace(chr(34), "")}"'  # $search can't be combined with $orderby
        headers = {"ConsistencyLevel": "eventual"}
    else:
        params["$orderby"] = "receivedDateTime desc"
    if unread_only:
        params["$filter"] = "isRead eq false"
    found = _request(ctx, "/me/messages", query=params, headers=headers)
    return {"messages": [{"id": m.get("id"), "from": _sender(m), "subject": m.get("subject"),
                          "date": m.get("receivedDateTime"), "unread": not m.get("isRead"),
                          "snippet": _text(m.get("bodyPreview") or "", 300)} for m in found.get("value", [])]}


def read_mail(ctx: dict, id: str) -> dict:  # noqa: A002 - the tool's argument name
    m = _request(ctx, f"/me/messages/{urllib.parse.quote(id, safe='')}",
                 query={"$select": "subject,from,toRecipients,receivedDateTime,body"})
    body = m.get("body") or {}
    text = _html_text(body.get("content", "")) if body.get("contentType") == "html" else body.get("content", "")
    return {"from": _sender(m), "subject": m.get("subject"), "date": m.get("receivedDateTime"),
            "to": [r.get("emailAddress", {}).get("address") for r in m.get("toRecipients", [])],
            "text": _text(text)}


def _event(item: dict) -> dict:
    out = {"id": item.get("id"), "title": item.get("subject") or "(no title)",
           "start": (item.get("start") or {}).get("dateTime"), "end": (item.get("end") or {}).get("dateTime"),
           "location": (item.get("location") or {}).get("displayName") or None}
    link = (item.get("onlineMeeting") or {}).get("joinUrl")
    if link:
        out["link"] = link
    return out


def list_events(ctx: dict, start_date: str | None = None, days: int = 1) -> dict:
    tz = ZoneInfo(ctx["timezone"])
    first = date.fromisoformat(start_date) if start_date else datetime.now(tz).date()
    start = datetime.combine(first, time.min, tz)
    end = start + timedelta(days=days)
    found = _request(ctx, "/me/calendarView", query={
        "startDateTime": start.isoformat(), "endDateTime": end.isoformat(), "$orderby": "start/dateTime", "$top": "50",
        "$select": "id,subject,start,end,location,onlineMeeting"}, headers={"Prefer": f'outlook.timezone="{ctx["timezone"]}"'})
    return {"from": first.isoformat(), "days": days, "events": [_event(e) for e in found.get("value", [])]}


def create_event(ctx: dict, title: str, start: str, duration_minutes: int = 60, location: str | None = None,
                 online_meeting: bool = False) -> dict:
    tz = ZoneInfo(ctx["timezone"])
    begin = datetime.fromisoformat(start)
    begin = begin.replace(tzinfo=tz) if begin.tzinfo is None else begin.astimezone(tz)
    finish = begin + timedelta(minutes=duration_minutes)
    body: dict = {"subject": title,
                  "start": {"dateTime": begin.replace(tzinfo=None).isoformat(), "timeZone": ctx["timezone"]},
                  "end": {"dateTime": finish.replace(tzinfo=None).isoformat(), "timeZone": ctx["timezone"]}}
    if location:
        body["location"] = {"displayName": location}
    if online_meeting:
        body.update(isOnlineMeeting=True, onlineMeetingProvider="teamsForBusiness")
    return {"created": _event(_request(ctx, "/me/events", method="POST", body=body))}
