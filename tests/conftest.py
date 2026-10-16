"""Shared helpers for Daily Routine tests."""

from __future__ import annotations

from typing import Any

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def make_routine(**overrides: Any) -> dict[str, Any]:
    """Build a fully-populated routine dict (what options_to_routine produces)."""
    routine: dict[str, Any] = {
        "id": "routine_one",
        "name": "Routine one",
        "enabled": True,
        "trigger": "time",
        "time": "07:00:00",
        "offset_minutes": 0,
        "days": list(WEEKDAYS),
        "require_everyone_away": False,
        "require_sensors_clear": False,
        "clear_minutes": 0,
        "people": [],
        "sensors": [],
        "notify_on_run": False,
        "notify_on_skip": False,
        "actions": [
            {
                "service": "light.turn_off",
                "target": {"entity_id": ["light.hall"]},
                "data": {},
            }
        ],
    }
    routine.update(overrides)
    return routine
