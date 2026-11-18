"""Today: what already ran, and what's still planned, in time order."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import TRIGGER_MANUAL, TRIGGER_SUNRISE, TRIGGER_SUNSET, TRIGGER_TIME
from .progress import step_label
from .routines import PLANNED_TRIGGERS

if TYPE_CHECKING:
    from .coordinator import HomeRoutinesCoordinator

SCHEDULED = (TRIGGER_TIME, TRIGGER_SUNRISE, TRIGGER_SUNSET, *PLANNED_TRIGGERS)


def today(coordinator: HomeRoutinesCoordinator, now: datetime) -> dict[str, Any]:
    """Runs today (newest last), runs still planned today, and routines that
    run when something happens."""
    start = dt_util.start_of_local_day(now)
    done: list[dict[str, Any]] = []
    # Only today's events are read (1.33.0), not a copy of the whole log.
    for event in coordinator.run_log.since(start):
        at = dt_util.parse_datetime(event["at"])
        if at is None or at < start:
            continue
        done.append(
            {
                "at": event["at"],
                "routine_id": event["routine_id"],
                "name": event["name"],
                "outcome": event["outcome"],
                "reason": event.get("reason") or "",
                "detail": event.get("detail") or "",
            }
        )
    planned: list[dict[str, Any]] = []
    watching: list[dict[str, Any]] = []
    for routine in coordinator.routines:
        if routine.get("kind") == "todo" or not routine["enabled"]:
            continue
        triggers = [routine, *coordinator.alias_routines(routine["id"])]
        times = [coordinator.planned_run(t["id"]) for t in triggers]
        for at in sorted(t for t in times if t is not None):
            if at.date() == now.date() and at > now:
                planned.append(
                    {
                        "at": at.isoformat(),
                        "routine_id": routine["id"],
                        "name": routine["name"],
                        "room": routine["room"],
                        "group": routine.get("group", ""),
                        "skip_next": bool(routine.get("skip_next")),
                        "paused_until": coordinator.paused_until(routine),
                    }
                )
        if any(t["trigger"] not in (*SCHEDULED, TRIGGER_MANUAL) for t in triggers):
            watching.append(
                {
                    "routine_id": routine["id"],
                    "name": routine["name"],
                    "room": routine["room"],
                    "group": routine.get("group", ""),
                }
            )
    planned.sort(key=lambda p: p["at"])
    return {
        "date": now.date().isoformat(),
        "done": done,
        "planned": planned,
        "watching": watching,
        "mode": coordinator.mode,
        "paused": coordinator.paused,
    }


# A routine with a short repeat (every few minutes) lists at most this many
# of tomorrow's runs.
MAX_RUNS_A_DAY = 6


def tomorrow(coordinator: HomeRoutinesCoordinator, now: datetime) -> dict[str, Any]:
    """A dry run of tomorrow: every planned run in order, with its steps.

    Only routines on a clock (times, sunrise and sunset, alarms, calendar
    events, cheapest prices) can be planned; the ones that wait for
    something to happen are listed apart. Nothing is run.
    """
    local = dt_util.as_local(now)
    start = dt_util.start_of_local_day(local) + timedelta(days=1)
    end = start + timedelta(days=1)
    day = start.date()
    names = {r["id"]: r["name"] for r in coordinator.routines}
    runs: list[dict[str, Any]] = []
    watching: list[dict[str, Any]] = []
    for routine in coordinator.routines:
        if routine.get("kind") == "todo" or not routine["enabled"]:
            continue
        triggers = [routine, *coordinator.alias_routines(routine["id"])]
        paused = coordinator.paused_until(routine)
        steps = [
            step_label(coordinator.hass, action, names) for action in routine["actions"]
        ]
        found = 0
        for trigger in triggers:
            cursor = start - timedelta(microseconds=1)
            while found < MAX_RUNS_A_DAY:
                at = coordinator.next_run_after(trigger, cursor)
                if at is None or at >= end:
                    break
                found += 1
                runs.append(
                    {
                        "at": at.isoformat(),
                        "routine_id": routine["id"],
                        "name": routine["name"],
                        "room": routine["room"],
                        "steps": steps,
                        "paused": bool(paused and paused >= day.isoformat()),
                        "skip_modes": routine.get("skip_modes") or [],
                    }
                )
                cursor = at
        if not found and any(
            t["trigger"] not in (*SCHEDULED, TRIGGER_MANUAL) for t in triggers
        ):
            watching.append({"routine_id": routine["id"], "name": routine["name"]})
    runs.sort(key=lambda r: r["at"])
    return {
        "date": day.isoformat(),
        "runs": runs,
        "watching": watching,
        "paused": coordinator.paused,
    }
