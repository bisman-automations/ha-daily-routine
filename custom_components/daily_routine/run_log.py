"""A short, saved log of every routine run, skip and failure, for the Activity tab.

It is kept apart from the routine store so it can grow and be pruned on its own.
"""

from __future__ import annotations

from collections import Counter, deque
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import storage
from homeassistant.util import dt as dt_util

from .const import DOMAIN

RUN_LOG_KEY = f"{DOMAIN}.runlog"
RUN_LOG_VERSION = 1
MAX_EVENTS = 5000
KEEP_DAYS = 30
SAVE_DELAY_SECONDS = 15
RECENT_LIMIT = 100
REPEAT_THRESHOLD = 3

WINDOWS = ("today", "7d", "30d")
OUTCOMES = ("ran", "failed", "skipped")


class RunLog:
    """Saved events, newest last. Each event: at, routine_id, name, outcome, reason, detail."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: storage.Store[dict[str, Any]] = storage.Store(
            hass, RUN_LOG_VERSION, RUN_LOG_KEY
        )
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        for event in data.get("events", []):
            if isinstance(event, dict) and event.get("outcome") in OUTCOMES:
                self._events.append(event)
        self._prune(dt_util.now())

    def record(
        self,
        routine_id: str,
        name: str,
        outcome: str,
        reason: str = "",
        detail: str = "",
    ) -> None:
        now = dt_util.now()
        self._events.append(
            {
                "at": now.isoformat(),
                "routine_id": routine_id,
                "name": name,
                "outcome": outcome,
                "reason": reason,
                "detail": detail,
            }
        )
        self._prune(now)
        self._store.async_delay_save(self._payload, SAVE_DELAY_SECONDS)

    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def forget_routine(self, routine_id: str) -> None:
        """Drop a deleted routine's events so the Activity tab does not show it."""
        kept = [e for e in self._events if e.get("routine_id") != routine_id]
        self._events.clear()
        self._events.extend(kept)
        self._store.async_delay_save(self._payload, SAVE_DELAY_SECONDS)

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(days=KEEP_DAYS)
        while self._events and _when(self._events[0]) < cutoff:
            self._events.popleft()

    def _payload(self) -> dict[str, Any]:
        return {"events": list(self._events)}


def _when(event: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(event["at"])


def window_start(window: str, now: datetime) -> datetime:
    """Where a window starts: midnight today, or a number of days back."""
    if window == "today":
        local = dt_util.as_local(now)
        return local.replace(hour=0, minute=0, second=0, microsecond=0)
    days = 7 if window == "7d" else 30
    return now - timedelta(days=days)


def _failure_streak(events: list[dict[str, Any]]) -> int:
    """How many of the newest events in a row are failures."""
    streak = 0
    for event in reversed(events):
        if event["outcome"] != "failed":
            break
        streak += 1
    return streak


def summarize(
    events: list[dict[str, Any]],
    window: str,
    now: datetime,
    routines: list[dict[str, Any]],
) -> dict[str, Any]:
    """Counts and lists for one window. Pure, so it can be tested without Home Assistant."""
    if window not in WINDOWS:
        window = "7d"
    since = window_start(window, now)
    in_window = [e for e in events if _when(e) >= since]
    ran = sum(1 for e in in_window if e["outcome"] == "ran")
    failed = sum(1 for e in in_window if e["outcome"] == "failed")
    skipped = sum(1 for e in in_window if e["outcome"] == "skipped")
    attempted = ran + failed

    by_routine = []
    for routine in routines:
        mine = [e for e in in_window if e.get("routine_id") == routine["id"]]
        everything = [e for e in events if e.get("routine_id") == routine["id"]]
        last_failure = next(
            (e for e in reversed(mine) if e["outcome"] == "failed"), None
        )
        last = mine[-1] if mine else None
        by_routine.append(
            {
                "id": routine["id"],
                "name": routine["name"],
                "enabled": routine["enabled"],
                "ran": sum(1 for e in mine if e["outcome"] == "ran"),
                "failed": sum(1 for e in mine if e["outcome"] == "failed"),
                "skipped": sum(1 for e in mine if e["outcome"] == "skipped"),
                "last_at": last["at"] if last else None,
                "last_outcome": last["outcome"] if last else None,
                "last_failure": (
                    {"at": last_failure["at"], "reason": last_failure["reason"]}
                    if last_failure
                    else None
                ),
                "failing_in_a_row": _failure_streak(everything),
            }
        )

    failure_reasons = Counter(
        e["reason"] for e in in_window if e["outcome"] == "failed"
    )
    skip_reasons = Counter(e["reason"] for e in in_window if e["outcome"] == "skipped")

    days: dict[str, dict[str, int]] = {}
    for event in in_window:
        key = dt_util.as_local(_when(event)).date().isoformat()
        bucket = days.setdefault(key, {"ran": 0, "failed": 0, "skipped": 0})
        bucket[event["outcome"]] += 1

    recent = [
        {
            k: e.get(k, "")
            for k in ("at", "routine_id", "name", "outcome", "reason", "detail")
        }
        for e in reversed(in_window[-RECENT_LIMIT:])
    ]

    return {
        "window": window,
        "totals": {
            "triggered": len(in_window),
            "ran": ran,
            "failed": failed,
            "skipped": skipped,
            "success_rate": round(100 * ran / attempted) if attempted else None,
        },
        "by_routine": by_routine,
        "failure_reasons": _ranked(failure_reasons),
        "skip_reasons": _ranked(skip_reasons),
        "by_day": [{"day": day, **counts} for day, counts in sorted(days.items())],
        "recent": recent,
        "quiet_routines": [
            r["name"]
            for r in by_routine
            if r["enabled"] and not (r["ran"] + r["failed"] + r["skipped"])
        ],
        "repeated_failures": [
            {"name": r["name"], "count": r["failing_in_a_row"]}
            for r in by_routine
            if r["failing_in_a_row"] >= REPEAT_THRESHOLD
        ],
    }


def _ranked(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"reason": reason, "count": count} for reason, count in counter.most_common()
    ]
