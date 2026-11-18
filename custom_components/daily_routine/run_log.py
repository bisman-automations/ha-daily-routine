"""A short, saved log of every routine run, skip and failure, for the Activity tab.

It is kept apart from the routine store so it can grow and be pruned on its own.

Speed (1.33.0): the log keeps each event's time, read once, next to it
(``times``), and knows whether the events are in time order (they are,
unless the clock went back). Readers get the log itself (``view``) or only
the part since a time (``since``, found by bisection) instead of a copy of
every event, and ``summarize`` goes through the events once instead of once
per routine.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter, deque
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from itertools import islice
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
MAX_STEPS = 30
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
        # Each event's time, read once, in the same places as _events.
        self._times: deque[datetime] = deque(maxlen=MAX_EVENTS)
        # True while every event is at or after the one before it, so a
        # window can be found by bisection.
        self.ordered = True
        # Goes up with every event, so the Activity tab knows when to read again.
        self.count = 0

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        for event in data.get("events", []):
            if isinstance(event, dict) and event.get("outcome") in OUTCOMES:
                try:
                    at = _when(event)
                except TypeError, ValueError, KeyError:
                    continue  # no readable time: it can't be shown or counted
                self._append(event, at)
        self._prune(dt_util.now())

    def _append(self, event: dict[str, Any], at: datetime) -> None:
        if self._times and at < self._times[-1]:
            self.ordered = False
        self._events.append(event)
        self._times.append(at)

    def record(
        self,
        routine_id: str,
        name: str,
        outcome: str,
        reason: str = "",
        detail: str = "",
        steps: list[dict[str, Any]] | None = None,
    ) -> None:
        now = dt_util.now()
        event: dict[str, Any] = {
            "at": now.isoformat(),
            "routine_id": routine_id,
            "name": name,
            "outcome": outcome,
            "reason": reason,
            "detail": detail,
        }
        if steps:
            # Each step of the run, for replaying it (the first ones).
            event["steps"] = steps[:MAX_STEPS]
        self._append(event, now)
        self.count += 1
        self._prune(now)
        self._store.async_delay_save(self._payload, SAVE_DELAY_SECONDS)

    def events(self) -> list[dict[str, Any]]:
        """A copy of every event, oldest first (for callers that keep it)."""
        return list(self._events)

    def view(self) -> Sequence[dict[str, Any]]:
        """Every event, oldest first, without copying. Read it, don't change it."""
        return self._events

    def times(self) -> Sequence[datetime]:
        """Each event's time, in the same places as ``view()``."""
        return self._times

    def since(self, start: datetime) -> list[dict[str, Any]]:
        """The events at or after a time, oldest first (only those are copied)."""
        if self.ordered:
            first = bisect_left(self._times, start)
            return list(islice(self._events, first, None))
        return [
            e for e, at in zip(self._events, self._times, strict=True) if at >= start
        ]

    def forget_routine(self, routine_id: str) -> None:
        """Drop a deleted routine's events so the Activity tab does not show it."""
        kept = [
            (e, at)
            for e, at in zip(self._events, self._times, strict=True)
            if e.get("routine_id") != routine_id
        ]
        self._events.clear()
        self._times.clear()
        self.ordered = True
        for event, at in kept:
            self._append(event, at)
        self._store.async_delay_save(self._payload, SAVE_DELAY_SECONDS)

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(days=KEEP_DAYS)
        while self._times and self._times[0] < cutoff:
            self._events.popleft()
            self._times.popleft()

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


def _window(
    events: Sequence[dict[str, Any]],
    times: Sequence[datetime] | None,
    since: datetime,
    ordered: bool,
) -> tuple[list[dict[str, Any]], list[datetime]]:
    """The events at or after ``since`` and their times, oldest first.

    ``times`` (each event's time, read once) saves reading every time again;
    with ``ordered`` (the events are in time order) the start is found by
    bisection instead of looking at every event.
    """
    if times is None:
        times = [_when(e) for e in events]
        ordered = False
    if ordered:
        first = bisect_left(times, since)
        return list(islice(events, first, None)), list(islice(times, first, None))
    pairs = [(e, at) for e, at in zip(events, times, strict=True) if at >= since]
    return [e for e, _at in pairs], [at for _e, at in pairs]


def summarize(
    events: Sequence[dict[str, Any]],
    window: str,
    now: datetime,
    routines: list[dict[str, Any]],
    times: Sequence[datetime] | None = None,
    ordered: bool = False,
) -> dict[str, Any]:
    """Counts and lists for one window. Pure, so it can be tested without Home Assistant.

    One pass over the window (and one over every event, for failures in a
    row) instead of one per routine; ``times``/``ordered`` as in ``_window``
    (RunLog.times() and RunLog.ordered).
    """
    if window not in WINDOWS:
        window = "7d"
    since = window_start(window, now)
    in_window, in_times = _window(events, times, since, ordered)

    totals = {"ran": 0, "failed": 0, "skipped": 0}
    # Per routine id: its counts, newest event and newest failure in the window.
    rows: dict[Any, dict[str, Any]] = {}
    failure_reasons: Counter[str] = Counter()
    skip_reasons: Counter[str] = Counter()
    days: dict[str, dict[str, int]] = {}
    for event, at in zip(in_window, in_times, strict=True):
        outcome = event["outcome"]
        row = rows.get(event.get("routine_id"))
        if row is None:
            row = rows[event.get("routine_id")] = {
                "ran": 0,
                "failed": 0,
                "skipped": 0,
                "last": None,
                "last_failure": None,
            }
        row["last"] = event
        if outcome in totals:
            totals[outcome] += 1
            row[outcome] += 1
        if outcome == "failed":
            row["last_failure"] = event
            failure_reasons[event["reason"]] += 1
        elif outcome == "skipped":
            skip_reasons[event["reason"]] += 1
        key = dt_util.as_local(at).date().isoformat()
        bucket = days.setdefault(key, {"ran": 0, "failed": 0, "skipped": 0})
        if outcome in bucket:
            bucket[outcome] += 1

    # Failures in a row count every event, not only the window's.
    streaks: dict[Any, int] = {}
    for event in events:
        rid = event.get("routine_id")
        streaks[rid] = streaks.get(rid, 0) + 1 if event["outcome"] == "failed" else 0

    ran, failed, skipped = totals["ran"], totals["failed"], totals["skipped"]
    attempted = ran + failed
    by_routine = []
    for routine in routines:
        row = rows.get(routine["id"]) or {}
        last = row.get("last")
        last_failure = row.get("last_failure")
        by_routine.append(
            {
                "id": routine["id"],
                "name": routine["name"],
                "enabled": routine["enabled"],
                "ran": row.get("ran", 0),
                "failed": row.get("failed", 0),
                "skipped": row.get("skipped", 0),
                "last_at": last["at"] if last else None,
                "last_outcome": last["outcome"] if last else None,
                "last_failure": (
                    {"at": last_failure["at"], "reason": last_failure["reason"]}
                    if last_failure
                    else None
                ),
                "failing_in_a_row": streaks.get(routine["id"], 0),
            }
        )

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


def daily_counts(
    events: Sequence[dict[str, Any]],
    now: datetime,
    days: int = 30,
    routine_id: str | None = None,
    times: Sequence[datetime] | None = None,
) -> list[dict[str, Any]]:
    """Ran, skipped and failed per day for the last ``days`` days, oldest
    first, with a row for every day (quiet days are zero). Pure.

    ``times``: each event's time, already read (RunLog.times()).
    """
    today = dt_util.as_local(now).date()
    first = today - timedelta(days=days - 1)
    counts = {
        (first + timedelta(days=i)).isoformat(): {"ran": 0, "failed": 0, "skipped": 0}
        for i in range(days)
    }
    when = iter(times) if times is not None else None
    for event in events:
        at = next(when) if when is not None else None
        if routine_id is not None and event.get("routine_id") != routine_id:
            continue
        key = dt_util.as_local(at or _when(event)).date().isoformat()
        if key in counts:
            counts[key][event["outcome"]] += 1
    return [{"day": day, **row} for day, row in counts.items()]


# A routine needs a look when, of its last ATTENTION_DAYS days of tries
# (at least ATTENTION_MIN), this share or more failed or were skipped.
ATTENTION_DAYS = 14
ATTENTION_MIN = 3
ATTENTION_SHARE = 0.5


def needs_attention(
    events: Sequence[dict[str, Any]],
    now: datetime,
    routines: list[dict[str, Any]],
    times: Sequence[datetime] | None = None,
    ordered: bool = False,
) -> list[dict[str, Any]]:
    """Enabled routines that keep failing or being skipped, worst first.

    ``times``/``ordered`` as in ``_window``: only the last ATTENTION_DAYS are
    looked at, found by bisection when the events are in time order.
    """
    since = now - timedelta(days=ATTENTION_DAYS)
    counts: dict[str, dict[str, int]] = {}
    # The newest failure's reason, so the list says why and not only how often.
    last_problem: dict[str, str] = {}
    recent, _times = _window(events, times, since, ordered)
    for event in recent:
        row = counts.setdefault(
            event["routine_id"], {"ran": 0, "failed": 0, "skipped": 0}
        )
        row[event["outcome"]] += 1
        if event["outcome"] == "failed" and event.get("reason"):
            last_problem[event["routine_id"]] = event["reason"]
    found = []
    for routine in routines:
        if not routine.get("enabled", True):
            continue
        found_row = counts.get(routine["id"])
        if not found_row:
            continue
        row = found_row
        tries = row["ran"] + row["failed"] + row["skipped"]
        bad = row["failed"] + row["skipped"]
        if tries >= ATTENTION_MIN and bad / tries >= ATTENTION_SHARE:
            found.append(
                {
                    "routine_id": routine["id"],
                    "name": routine["name"],
                    "tries": tries,
                    **row,
                    "score": round(100 * row["ran"] / tries),
                    "last_problem": last_problem.get(routine["id"]),
                }
            )
    return sorted(found, key=lambda r: r["score"])


def away_text(
    events: Sequence[dict[str, Any]], since: datetime, routines: list[dict[str, Any]]
) -> str:
    """What ran while the house was Away or on Vacation, in one line."""
    names = {r["id"]: r["name"] for r in routines}
    recent = [e for e in events if _when(e) >= since]
    if not recent:
        return "Welcome home. No routines ran while you were away."
    ran = sum(1 for e in recent if e["outcome"] == "ran")
    failed = sorted(
        {
            names.get(e["routine_id"], e.get("name", ""))
            for e in recent
            if e["outcome"] == "failed"
        }
    )
    skipped = sum(1 for e in recent if e["outcome"] == "skipped")
    parts = [f"{ran} {'run' if ran == 1 else 'runs'} worked"]
    if failed:
        parts.append(f"{', '.join(failed)} failed")
    if skipped:
        parts.append(f"{skipped} skipped")
    return "Welcome home. While you were away: " + ", ".join(parts) + "."


def missed_today(
    routines: list[dict[str, Any]],
    events: Sequence[dict[str, Any]],
    now: datetime,
    allowed_today: Callable[[dict[str, Any]], bool],
) -> list[str]:
    """Routines at a set time whose time today has passed with nothing in the
    run log since (not run, skipped or failed): missed while Home Assistant
    was off. Ones still inside their catch-up window are left to catch up."""
    local = dt_util.as_local(now)
    missed = []
    for routine in routines:
        if routine["trigger"] != "time" or not routine.get("enabled", True):
            continue
        if not allowed_today(routine):
            continue
        hour, minute, *_ = (int(p) for p in routine["time"].split(":"))
        target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target > local:
            continue
        if local < target + timedelta(minutes=routine.get("catch_up_minutes") or 0):
            continue
        if any(e["routine_id"] == routine["id"] and _when(e) >= target for e in events):
            continue
        missed.append(routine["id"])
    return missed
