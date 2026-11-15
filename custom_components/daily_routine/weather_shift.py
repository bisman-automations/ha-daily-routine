"""Hot days move a routine: earlier (or later) when the day will be hot.

A routine on a set time or the sun may have ``hot_shift`` {entity_id (a
weather), above (a temperature), minutes (-180 to 180)}. When that day's
forecast high is at or above ``above``, the day's run moves by ``minutes``,
such as closing the blinds an hour earlier on a 30° day. Without a forecast
for the day, it runs at its usual time. Pure.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.util import dt as dt_util

from .schema import vol

MAX_SHIFT = 180


def clean_hot_shift(
    raw: Any, trigger: str, scheduled: tuple[str, ...]
) -> dict[str, Any] | None:
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise vol.Invalid(
            "A hot-day change needs a weather, a temperature and minutes."
        )
    if trigger not in scheduled:
        raise vol.Invalid(
            "Only routines at a set time or the sun can move on hot days."
        )
    entity = str(raw.get("entity_id") or "")
    if not entity.startswith("weather."):
        raise vol.Invalid("Pick the weather for the hot-day change.")
    try:
        above = float(raw.get("above"))  # type: ignore[arg-type]
        minutes = int(raw.get("minutes"))  # type: ignore[arg-type]
    except (TypeError, ValueError) as err:
        raise vol.Invalid("Give the temperature and the minutes for hot days.") from err
    if not minutes or abs(minutes) > MAX_SHIFT:
        raise vol.Invalid(f"Move it 1 to {MAX_SHIFT} minutes on hot days.")
    return {"entity_id": entity, "above": above, "minutes": minutes}


def day_high(forecast: list[dict[str, Any]] | None, day: date) -> float | None:
    """The highest forecast temperature on that day, or None."""
    highs = []
    for item in forecast or []:
        when = dt_util.parse_datetime(str(item.get("datetime") or ""))
        temp = item.get("temperature")
        if when is None or not isinstance(temp, (int, float)):
            continue
        if dt_util.as_local(when).date() == day:
            highs.append(float(temp))
    return max(highs) if highs else None


def shift_minutes(
    rule: dict[str, Any] | None, forecast: list[dict[str, Any]] | None, day: date
) -> int:
    if not rule:
        return 0
    high = day_high(forecast, day)
    return int(rule["minutes"]) if high is not None and high >= rule["above"] else 0
