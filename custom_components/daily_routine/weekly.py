"""The weekly report: what ran in the last seven days, what failed most,
and which routines didn't run at all.

Settings (``settings["weekly_report"]``): enabled, the weekday (Monday 0)
and the time it's sent.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
import re
from typing import Any

from homeassistant.util import dt as dt_util

from .schema import vol

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
WEEKLY_DEFAULT = {"enabled": False, "weekday": 6, "time": "18:00"}
MAX_LISTED = 5


def clean_weekly(raw: Any) -> dict[str, Any]:
    if raw is None:
        return dict(WEEKLY_DEFAULT)
    if not isinstance(raw, dict):
        raise vol.Invalid("The weekly report settings must be an object.")
    time = str(raw.get("time") or "18:00")[:5]
    if not _TIME.match(time):
        raise vol.Invalid("Send the weekly report at a time like 18:00.")
    weekday = raw.get("weekday", 6)
    if (
        isinstance(weekday, bool)
        or not isinstance(weekday, int)
        or not 0 <= weekday <= 6
    ):
        raise vol.Invalid("Pick the day to send the weekly report.")
    return {"enabled": bool(raw.get("enabled")), "weekday": weekday, "time": time}


def weekly_or_default(raw: Any) -> dict[str, Any]:
    try:
        return clean_weekly(raw)
    except vol.Invalid:
        return dict(WEEKLY_DEFAULT)


def weekly_text(
    events: list[dict[str, Any]],
    routines: list[dict[str, Any]],
    now: datetime,
) -> str:
    """The last seven days in a few lines."""
    since = now - timedelta(days=7)
    week = [
        e
        for e in events
        if (at := dt_util.parse_datetime(str(e.get("at") or ""))) and at >= since
    ]
    ran = [e for e in week if e["outcome"] == "ran"]
    failed = [e for e in week if e["outcome"] == "failed"]
    skipped = [e for e in week if e["outcome"] == "skipped"]
    lines = [
        f"This week: {len(ran)} ran, {len(failed)} failed, {len(skipped)} skipped."
    ]
    if failed:
        most = Counter(e["name"] for e in failed).most_common(MAX_LISTED)
        lines.append("Failed most: " + "; ".join(f"{name} ({n}×)" for name, n in most))
    ran_ids = {e.get("routine_id") for e in ran}
    idle = [
        r["name"]
        for r in routines
        if r["enabled"]
        and r.get("kind") != "todo"
        and not r.get("companion_of")
        and r["trigger"] != "manual"
        and r["id"] not in ran_ids
    ]
    if idle:
        more = f" and {len(idle) - MAX_LISTED} more" if len(idle) > MAX_LISTED else ""
        lines.append("Didn't run: " + ", ".join(idle[:MAX_LISTED]) + more)
    elif routines:
        lines.append("Every routine ran at least once. 👍")
    return "\n".join(lines)
