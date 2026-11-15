"""Kinds of day (School day, Holiday, Work from home) read from calendars.

Settings hold a list of {name, calendar, words}: today is the first kind
whose calendar has an event today whose title has one of its words (any
event, with no words). A day with none of them is a Normal day. Pure: the
coordinator reads the calendars and passes today's titles in.
"""

from __future__ import annotations

from typing import Any

from .routines import ENTITY_ID_RE, MAX_DAY_TYPE_NAME, MAX_DAY_TYPES, NORMAL_DAY
from .schema import vol

MAX_WORDS = 10
MAX_WORD = 60


def clean_day_types(raw: Any) -> list[dict[str, Any]]:
    """Validate the Settings list. Raises vol.Invalid with a plain message."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise vol.Invalid("Kinds of day must be a list.")
    if len(raw) > MAX_DAY_TYPES:
        raise vol.Invalid(f"Up to {MAX_DAY_TYPES} kinds of day.")
    cleaned: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise vol.Invalid("Each kind of day needs a name and a calendar.")
        name = " ".join(str(item.get("name") or "").split())[:MAX_DAY_TYPE_NAME]
        calendar = str(item.get("calendar") or "").strip()
        if not name:
            raise vol.Invalid("Give each kind of day a name.")
        if name.casefold() == NORMAL_DAY.casefold():
            raise vol.Invalid(f"“{NORMAL_DAY}” is any day that's none of the others.")
        if any(d["name"].casefold() == name.casefold() for d in cleaned):
            raise vol.Invalid(f"There are two kinds of day called {name}.")
        if not ENTITY_ID_RE.fullmatch(calendar) or not calendar.startswith("calendar."):
            raise vol.Invalid(f"Pick the calendar for {name}.")
        raw_words = item.get("words") or []
        if isinstance(raw_words, str):
            raw_words = raw_words.split(",")
        words = [" ".join(str(w).split())[:MAX_WORD] for w in raw_words]
        words = list(dict.fromkeys(w for w in words if w))[:MAX_WORDS]
        cleaned.append({"name": name, "calendar": calendar, "words": words})
    return cleaned


def calendars(day_types: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(d["calendar"] for d in day_types))


def day_type(
    day_types: list[dict[str, Any]], titles: dict[str, list[str] | None]
) -> str | None:
    """Today's kind of day. None while a calendar it depends on is unread.

    ``titles`` maps each calendar to today's event titles (None: unread).
    Kinds are tried in order, so put the more specific ones first.
    """
    for kind in day_types:
        events = titles.get(kind["calendar"])
        if events is None:
            return None
        if not kind["words"]:
            if events:
                return kind["name"]
            continue
        words = [w.casefold() for w in kind["words"]]
        if any(w in title.casefold() for title in events for w in words):
            return kind["name"]
    return NORMAL_DAY
