"""Tools: Home Assistant - the lights, AC, fans and plugs at home.

Runs in the sandbox like every tool. The gate adds your Home Assistant token and its address,
and lets this process reach that one address - the only local-network address any tool may
reach. Only lights, switches, fans, climate (AC), media players, scenes, helpers and
humidifiers are controlled: never locks, alarm panels, garage doors or covers
(schemas.HomeControlArgs).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .schemas import HOME_DOMAINS

MAX_DEVICES = 60


def _request(ctx: dict, path: str, body: dict | None = None):  # noqa: ANN202
    request = urllib.request.Request(
        ctx["api_base"].rstrip("/") + path, method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {ctx['access_token']}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:  # noqa: S310 - your own Home Assistant
            data = response.read(4_000_000)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise PermissionError("Home Assistant refused the token; connect it again in Tools & Data") from None
        if exc.code == 404:
            raise RuntimeError("Home Assistant doesn't have that device") from None
        raise RuntimeError(f"Home Assistant returned {exc.code}: {exc.read(200).decode('utf-8', 'replace')}") from None
    return json.loads(data) if data.strip() else None


def _device(state: dict) -> dict:
    attrs = state.get("attributes") or {}
    out = {"id": state.get("entity_id"), "name": attrs.get("friendly_name") or state.get("entity_id"), "state": state.get("state")}
    for key in ("brightness", "current_temperature", "temperature", "hvac_mode", "percentage"):
        if attrs.get(key) is not None:
            out[key] = attrs[key]
    if out.get("brightness") is not None:
        out["brightness"] = round(int(out["brightness"]) * 100 / 255)  # Home Assistant counts 0-255
    return out


def list_devices(ctx: dict, kind: str = "all", area: str = "") -> dict:
    states = _request(ctx, "/api/states") or []
    wanted = HOME_DOMAINS if kind == "all" else (kind,)
    devices = [_device(s) for s in states if str(s.get("entity_id", "")).split(".", 1)[0] in wanted]
    if area:
        needle = area.lower()
        devices = [d for d in devices if needle in str(d["name"]).lower() or needle in str(d["id"]).lower()]
    out: dict = {"devices": devices[:MAX_DEVICES]}
    if len(devices) > MAX_DEVICES:
        out["more"] = len(devices) - MAX_DEVICES
    return out


def control(ctx: dict, entity_id: str, action: str, value: float | None = None, mode: str | None = None) -> dict:
    domain = entity_id.split(".", 1)[0]
    if domain not in HOME_DOMAINS:
        raise PermissionError("Custodian never controls locks, alarms or doors")
    data: dict = {"entity_id": entity_id}
    if action in ("turn_on", "turn_off", "toggle"):
        service_domain, service = "homeassistant", action  # works for every kind of device
    elif action == "activate":
        service_domain, service = domain, "turn_on"
    elif action == "set_brightness":
        if domain != "light":
            raise ValueError("only lights have a brightness")
        service_domain, service = "light", "turn_on"
        data["brightness_pct"] = int(value or 0)
    elif action == "set_temperature":
        if domain != "climate":
            raise ValueError("only an AC or thermostat (climate) has a temperature")
        service_domain, service = "climate", "set_temperature"
        data["temperature"] = value
    elif action == "set_fan_speed":
        if domain != "fan":
            raise ValueError("only a fan has a speed")
        service_domain, service = "fan", "set_percentage"
        data["percentage"] = int(value or 0)
    elif action == "set_hvac_mode":
        if domain != "climate":
            raise ValueError("only an AC or thermostat (climate) has a mode")
        service_domain, service = "climate", "set_hvac_mode"
        data["hvac_mode"] = mode
    else:
        raise ValueError(f"unknown action {action!r}")
    _request(ctx, f"/api/services/{service_domain}/{service}", data)
    try:
        state = _request(ctx, f"/api/states/{entity_id}")
    except RuntimeError:
        state = None
    return {"done": action, "device": _device(state) if state else {"id": entity_id}}
