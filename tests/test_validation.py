"""Pure validation tests for routine JSON."""

from __future__ import annotations

import json

import pytest
import voluptuous as vol

from custom_components.daily_routine import _validate_routines

from .conftest import make_routine


def _raw(*routines: dict) -> str:
    return json.dumps(list(routines))


def test_valid_routines_are_accepted() -> None:
    routines = _validate_routines(_raw(make_routine(), make_routine(id="away", name="Away")))
    assert [r["id"] for r in routines] == ["routine_one", "away"]


def test_delay_actions_are_accepted() -> None:
    routine = make_routine(actions=[{"delay": 5}, {"delay": "00:00:30"}, {"delay": "01:15"}])
    assert _validate_routines(_raw(routine))


@pytest.mark.parametrize(
    "overrides",
    [
        {"time": "25:00"},
        {"days": ["funday"]},
        {"clear_minutes": 2000},
        {"require_sensors_clear": True, "sensors": []},
        {"require_everyone_away": True, "people": []},
        {"actions": [{"service": "not a service"}]},
        {"id": "bad id!"},
    ],
)
def test_invalid_routines_are_rejected(overrides: dict) -> None:
    with pytest.raises(vol.Invalid):
        _validate_routines(_raw(make_routine(**overrides)))


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(vol.Invalid):
        _validate_routines(_raw(make_routine(), make_routine()))


def test_malformed_json_is_rejected() -> None:
    with pytest.raises(vol.Invalid):
        _validate_routines("[{")
