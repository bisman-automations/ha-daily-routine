"""Shared helpers for Daily Routine tests."""

from __future__ import annotations

from typing import Any

import pytest

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


@pytest.fixture(autouse=True)
def _no_step_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests mock services that don't change any state, so the check that a
    step worked is off unless a test turns it on (tests/test_step_failures.py)."""
    from custom_components.daily_routine import coordinator

    default, low, high, name = coordinator.TIMING_SETTINGS["verify_seconds"]
    monkeypatch.setitem(
        coordinator.TIMING_SETTINGS, "verify_seconds", (0, low, high, name)
    )
