"""Shared helpers for Daily Routine tests."""

from __future__ import annotations

from typing import Any

from custom_components.daily_routine.routines import normalize_routine


def make_routine(**overrides: Any) -> dict[str, Any]:
    """A normalised routine. Defaults: a light-off step at 07:00 every day."""
    raw: dict[str, Any] = {
        "id": "routine_one",
        "name": "Routine one",
        "trigger": "time",
        "time": "07:00:00",
        "actions": [
            {
                "service": "light.turn_off",
                "target": {"entity_id": ["light.hall"]},
                "data": {},
            }
        ],
    }
    raw.update(overrides)
    return normalize_routine(raw)
