"""The morning briefing: a step that says the day ahead on a speaker.

{"briefing": {"voice": tts entity, "speaker": media player, "weather":
weather entity or None, "calendars": [calendar entities], "day_type":
bool}}. The words are put together here from the weather's state and
today's events; the coordinator reads them and calls ``tts.speak``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import State
from homeassistant.util import dt as dt_util

WEATHER_WORDS = {
    "clear-night": "clear",
    "cloudy": "cloudy",
    "exceptional": "unusual weather",
    "fog": "foggy",
    "hail": "hail",
    "lightning": "thunderstorms",
    "lightning-rainy": "thunderstorms and rain",
    "partlycloudy": "partly cloudy",
    "pouring": "pouring rain",
    "rainy": "rain",
    "snowy": "snow",
    "snowy-rainy": "sleet",
    "sunny": "sunny",
    "windy": "windy",
    "windy-variant": "windy",
}
MAX_EVENTS = 5


def greeting(now: datetime) -> str:
    if now.hour < 12:
        return "Good morning."
    if now.hour < 18:
        return "Good afternoon."
    return "Good evening."


def weather_text(state: State | None, high: float | None = None) -> str:
    if state is None or state.state in ("unknown", "unavailable"):
        return ""
    words = WEATHER_WORDS.get(state.state, state.state.replace("-", " "))
    temp = state.attributes.get("temperature")
    unit = state.attributes.get("temperature_unit") or "°"
    text = f"Right now it's {words}"
    if isinstance(temp, (int, float)):
        text += f" and {round(temp)}{unit if unit.startswith('°') else ' ' + unit}"
    if high is not None:
        text += f", up to {round(high)} today"
    return text + "."


def events_text(events: list[dict[str, Any]], now: datetime) -> str:
    rows = []
    for event in events:
        title = str(event.get("summary") or "").strip()
        if not title:
            continue
        raw = str(event.get("start") or "")
        start = dt_util.parse_datetime(raw) if "T" in raw or " " in raw else None
        if start is None:
            rows.append((0, f"{title}, all day"))
            continue
        start = dt_util.as_local(start)
        if start.date() != now.date():
            continue
        rows.append((start.hour * 60 + start.minute, f"{title} at {start:%H:%M}"))
    if not rows:
        return "Nothing on the calendar today."
    rows.sort()
    listed = [text for _, text in rows[:MAX_EVENTS]]
    more = len(rows) - len(listed)
    joined = ", ".join(listed[:-1]) + (" and " if len(listed) > 1 else "") + listed[-1]
    return f"Today: {joined}" + (f", and {more} more." if more else ".")


def briefing_text(
    now: datetime,
    *,
    day_type: str | None,
    weather: str,
    events: str | None,
) -> str:
    parts = [greeting(now)]
    if day_type:
        parts.append(
            f"It's a {day_type.lower() if day_type != day_type.upper() else day_type}."
        )
    if weather:
        parts.append(weather)
    if events:
        parts.append(events)
    return " ".join(parts)
