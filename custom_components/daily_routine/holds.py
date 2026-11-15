"""Calendar holds: routines stand aside while an event is on.

``settings.holds`` lists {calendar, words}: while one of the calendar's
events is on right now (and its title has one of the words, or any event
with none), scheduled runs are skipped with reason ``calendar_hold``, such
as while "Babysitter" or "Vacation" is on. A routine with ``ignore_holds``
runs anyway.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .routines import ENTITY_ID_RE
from .schema import vol

MAX_HOLDS = 6


def clean_holds(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_HOLDS:
        raise vol.Invalid(f"Up to {MAX_HOLDS} calendar holds.")
    holds = []
    for item in raw:
        if not isinstance(item, dict):
            raise vol.Invalid("Each hold needs a calendar.")
        calendar = str(item.get("calendar") or "").strip()
        if not ENTITY_ID_RE.fullmatch(calendar) or not calendar.startswith("calendar."):
            raise vol.Invalid("Pick the calendar for each hold.")
        words = item.get("words") or []
        if isinstance(words, str):
            words = words.split(",")
        words = [" ".join(str(w).split())[:60] for w in words]
        holds.append({"calendar": calendar, "words": [w for w in words if w][:10]})
    return holds


def _when(raw: Any, end: bool) -> datetime | None:
    text = str(raw or "")
    at = dt_util.parse_datetime(text)
    if at is None:
        day = dt_util.parse_date(text)
        if day is None:
            return None
        return dt_util.start_of_local_day(day)
    return at if at.tzinfo else at.replace(tzinfo=dt_util.get_default_time_zone())


def active_hold(
    holds: list[dict[str, Any]],
    events: dict[str, list[dict[str, Any]]],
    now: datetime,
) -> str | None:
    """The title of an event holding routines back right now, or None."""
    for hold in holds:
        words = [w.casefold() for w in hold["words"]]
        for event in events.get(hold["calendar"]) or []:
            title = str(event.get("summary") or "")
            if words and not any(w in title.casefold() for w in words):
                continue
            start = _when(event.get("start"), False)
            end = _when(event.get("end"), True)
            if start is not None and end is not None and start <= now < end:
                return title or "an event"
    return None
