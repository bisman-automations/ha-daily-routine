"""When planned triggers run: before a phone's alarm, before a calendar event,
the cheapest electricity hour, and whether the weather forecast matches.

Pure functions, so they're tested without Home Assistant running.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

# Attribute names price integrations use for hourly (or quarter-hourly) prices.
# Nord Pool, ENTSO-e, Energi Data Service, Tibber and others each use their own.
PRICE_LISTS = (
    "raw_today",
    "raw_tomorrow",
    "prices_today",
    "prices_tomorrow",
    "today",
    "tomorrow",
    "prices",
    "forecast",
    "data",
)
PRICE_START_KEYS = ("start", "startsAt", "time", "hour", "start_time", "datetime")
PRICE_VALUE_KEYS = ("value", "price", "total", "price_ct_per_kwh", "electricity_price")


def alarm_time(state: Any) -> datetime | None:
    """The time a timestamp sensor (a phone's Next alarm) or a date-time helper gives."""
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    when = dt_util.parse_datetime(str(state.state))
    if when is None and state.attributes.get("timestamp") is not None:
        try:
            return dt_util.utc_from_timestamp(float(state.attributes["timestamp"]))
        except TypeError, ValueError:
            return None
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=dt_util.get_default_time_zone())
    return when


def event_starts(events: Iterable[dict[str, Any]], match: str) -> list[datetime]:
    """Start times of the calendar events whose title holds the text (any, when empty).
    All-day events start at midnight."""
    needle = match.casefold()
    starts: list[datetime] = []
    for event in events:
        if needle and needle not in str(event.get("summary") or "").casefold():
            continue
        raw = str(event.get("start") or "")
        start = dt_util.parse_datetime(raw)
        if start is None:
            day = dt_util.parse_date(raw)
            if day is None:
                continue
            start = dt_util.start_of_local_day(day)
        elif start.tzinfo is None:
            start = start.replace(tzinfo=dt_util.get_default_time_zone())
        starts.append(start)
    return sorted(starts)


def _slot_list(value: Any, day: datetime | None) -> list[tuple[datetime, float]]:
    slots: list[tuple[datetime, float]] = []
    if not isinstance(value, list):
        return slots
    if value and all(isinstance(v, (int, float)) for v in value) and day is not None:
        # A plain list of numbers: one per hour (or quarter hour) from midnight.
        step = timedelta(hours=24 / len(value)) if len(value) in (24, 96) else None
        if step is None:
            return slots
        start = dt_util.start_of_local_day(day)
        return [(start + step * i, float(v)) for i, v in enumerate(value)]
    for item in value:
        if not isinstance(item, dict):
            continue
        start_raw = next((item[k] for k in PRICE_START_KEYS if k in item), None)
        price_raw = next((item[k] for k in PRICE_VALUE_KEYS if k in item), None)
        found = (
            start_raw
            if isinstance(start_raw, datetime)
            else dt_util.parse_datetime(str(start_raw))
        )
        try:
            price = float(price_raw)  # type: ignore[arg-type]
        except TypeError, ValueError:
            continue
        if found is None:
            continue
        if found.tzinfo is None:
            found = found.replace(tzinfo=dt_util.get_default_time_zone())
        slots.append((found, price))
    return slots


def price_slots(
    attributes: dict[str, Any], now: datetime
) -> list[tuple[datetime, float]]:
    """Every known price slot (start, price), oldest first, from a price sensor."""
    slots: dict[datetime, float] = {}
    for key in PRICE_LISTS:
        day = None
        if key in ("today", "prices_today", "raw_today"):
            day = now
        elif key in ("tomorrow", "prices_tomorrow", "raw_tomorrow"):
            day = now + timedelta(days=1)
        for start, price in _slot_list(attributes.get(key), day):
            slots.setdefault(start, price)
    return sorted(slots.items())


def cheapest_start(
    slots: list[tuple[datetime, float]], begin: datetime, end: datetime
) -> datetime | None:
    """The start of the cheapest slot that begins inside [begin, end)."""
    inside = [(price, start) for start, price in slots if begin <= start < end]
    if not inside:
        return None
    return min(inside)[1]


def forecast_matches(
    forecast: Iterable[dict[str, Any]],
    now: datetime,
    hours: int,
    kinds: list[str],
    below: float | None,
) -> bool:
    """Whether any forecast entry in the next hours has one of the kinds of
    weather, or a temperature below the level."""
    until = now + timedelta(hours=hours)
    for entry in forecast:
        when = dt_util.parse_datetime(str(entry.get("datetime") or ""))
        if when is None:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt_util.get_default_time_zone())
        if when > until or when + timedelta(hours=1) < now:
            continue
        if kinds and entry.get("condition") in kinds:
            return True
        if below is not None:
            low = entry.get("templow", entry.get("temperature"))
            try:
                if low is not None and float(low) < below:
                    return True
            except TypeError, ValueError:
                continue
    return False
