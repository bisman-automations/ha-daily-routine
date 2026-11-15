"""Today: what already ran, and what's still planned, in time order."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import TRIGGER_MANUAL, TRIGGER_SUNRISE, TRIGGER_SUNSET, TRIGGER_TIME

if TYPE_CHECKING:
    from .coordinator import HomeRoutinesCoordinator

SCHEDULED = (TRIGGER_TIME, TRIGGER_SUNRISE, TRIGGER_SUNSET)


def today(coordinator: HomeRoutinesCoordinator, now: datetime) -> dict[str, Any]:
    """Runs today (newest last), runs still planned today, and routines that
    run when something happens."""
    start = dt_util.start_of_local_day(now)
    done: list[dict[str, Any]] = []
    for event in coordinator.run_log.events():
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
