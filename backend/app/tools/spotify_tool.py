"""Tools: Spotify, through its Web API with your connected account - exact songs, albums and
playlists by search instead of clicking the app, on whichever device is playing (the PC, a phone,
a speaker).

Runs in the sandbox like every tool; the gate adds a short-lived access token and lets this
process reach only api.spotify.com. Playing needs Spotify open on some device (Spotify Premium).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.spotify.com/v1"
HOSTS = ("api.spotify.com",)


def _api(ctx: dict) -> str:
    return ctx.get("api_base") or API  # tests point this at a local fake


def _request(ctx: dict, path: str, *, method: str = "GET", body: dict | None = None, query: dict | None = None):  # noqa: ANN202
    url = _api(ctx) + path + ("?" + urllib.parse.urlencode(query) if query else "")
    request = urllib.request.Request(
        url, method=method, data=json.dumps(body).encode() if body is not None else (b"" if method != "GET" else None),
        headers={"Authorization": f"Bearer {ctx['access_token']}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:  # noqa: S310 - fixed https host
            data = response.read(1_000_000)
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", "replace")
        if (exc.code == 404 and "device" in detail.lower()) or "NO_ACTIVE_DEVICE" in detail:
            raise RuntimeError("Spotify isn't open on any device; open Spotify on the PC or phone and try again") from None
        if exc.code == 403 and "premium" in detail.lower():
            raise RuntimeError("Spotify lets apps start music only with Spotify Premium") from None
        if exc.code in (401, 403):
            raise PermissionError(f"Spotify refused the request ({exc.code}); reconnect Spotify in Tools & Data") from None
        raise RuntimeError(f"Spotify returned {exc.code}: {detail[:200]}") from None
    return json.loads(data) if data.strip() else {}


def _item(kind: str, item: dict) -> dict:
    out = {"name": item.get("name"), "uri": item.get("uri"), "kind": kind}
    if kind in ("track", "album"):
        out["by"] = ", ".join(a.get("name", "") for a in item.get("artists", []))
    if kind == "playlist":
        out["by"] = (item.get("owner") or {}).get("display_name")
    return out


def search(ctx: dict, query: str, kind: str = "track", limit: int = 5) -> dict:
    found = _request(ctx, "/search", query={"q": query, "type": kind, "limit": limit})
    items = [i for i in (found.get(f"{kind}s") or {}).get("items", []) if i]
    return {"query": query, "results": [_item(kind, i) for i in items]}


def now_playing(ctx: dict) -> dict:
    state = _request(ctx, "/me/player")
    if not state or not state.get("item"):
        return {"playing": False, "note": "nothing is playing on Spotify"}
    item = state["item"]
    return {"playing": bool(state.get("is_playing")), **_item("track", item),
            "device": (state.get("device") or {}).get("name"), "volume": (state.get("device") or {}).get("volume_percent"),
            "shuffle": state.get("shuffle_state")}


def play(ctx: dict, query: str | None = None, kind: str = "track", uri: str | None = None) -> dict:
    if not uri and not query:
        _request(ctx, "/me/player/play", method="PUT")
        return {"resumed": True}
    chosen = None
    if not uri:
        results = search(ctx, query or "", kind, 1)["results"]
        if not results:
            raise RuntimeError(f"Spotify found no {kind} for {query!r}; nothing was played")
        chosen = results[0]
        uri = chosen["uri"]
    body = {"uris": [uri]} if uri.startswith("spotify:track:") else {"context_uri": uri}
    _request(ctx, "/me/player/play", method="PUT", body=body)
    return {"playing": chosen or {"uri": uri}}


def control(ctx: dict, action: str, volume: int | None = None) -> dict:
    if action == "volume":
        if volume is None:
            raise ValueError("say what volume, 0 to 100")
        _request(ctx, "/me/player/volume", method="PUT", query={"volume_percent": volume})
        return {"volume": volume}
    path, method, query = {
        "pause": ("/me/player/pause", "PUT", None),
        "resume": ("/me/player/play", "PUT", None),
        "next": ("/me/player/next", "POST", None),
        "previous": ("/me/player/previous", "POST", None),
        "shuffle_on": ("/me/player/shuffle", "PUT", {"state": "true"}),
        "shuffle_off": ("/me/player/shuffle", "PUT", {"state": "false"}),
        "repeat_on": ("/me/player/repeat", "PUT", {"state": "context"}),
        "repeat_off": ("/me/player/repeat", "PUT", {"state": "off"}),
    }[action]
    _request(ctx, path, method=method, query=query)
    return {"done": action}
