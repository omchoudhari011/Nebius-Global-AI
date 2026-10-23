"""The weather for the HUD, from Open-Meteo (open-meteo.com: free, no account or key). Only the
city's name (to find it) and its coordinates (for the forecast) are sent - nothing about you.
The city is Custodian's "home_city" setting, or the one your memories say you live in."""

from __future__ import annotations

import httpx

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
# WMO weather codes, as words.
CODES = {0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy", 51: "light drizzle",
         53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
         67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers",
         81: "rain showers", 82: "violent rain showers", 85: "snow showers", 86: "snow showers", 95: "thunderstorms",
         96: "thunderstorms with hail", 99: "thunderstorms with hail"}

_places: dict[str, dict] = {}


def describe(code: int | None) -> str:
    return CODES.get(code if code is not None else -1, "unknown")


def lookup(city: str, http: httpx.Client) -> dict | None:
    key = city.strip().lower()
    if key not in _places:
        found = http.get(GEOCODE, params={"name": city.strip(), "count": 1, "format": "json"}).json().get("results") or []
        if not found:
            return None
        place = found[0]
        _places[key] = {"name": place.get("name") or city, "country": place.get("country_code"),
                        "latitude": place["latitude"], "longitude": place["longitude"]}
    return _places[key]


def current(city: str, *, transport: httpx.BaseTransport | None = None) -> dict | None:
    """{"city", "temperature", "feels_like", "condition", "high", "low", "rain_chance", "wind_kmh"}, or None."""
    if not city:
        return None
    with httpx.Client(timeout=10, transport=transport) as http:
        place = lookup(city, http)
        if place is None:
            return None
        data = http.get(FORECAST, params={
            "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto", "forecast_days": 1,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        }).json()
    now, daily = data.get("current") or {}, data.get("daily") or {}

    def first(name: str):  # noqa: ANN202
        values = daily.get(name) or [None]
        return values[0]

    return {
        "city": place["name"],
        "temperature": now.get("temperature_2m"),
        "feels_like": now.get("apparent_temperature"),
        "condition": describe(now.get("weather_code")),
        "code": now.get("weather_code"),
        "wind_kmh": now.get("wind_speed_10m"),
        "high": first("temperature_2m_max"),
        "low": first("temperature_2m_min"),
        "rain_chance": first("precipitation_probability_max"),
    }
