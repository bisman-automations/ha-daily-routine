"""Pure validation of routines from the panel or storage."""

from __future__ import annotations

import pytest

from custom_components.daily_routine.routines import (
    normalize_routine,
    parse_delay,
    uses_conditions,
)
from custom_components.daily_routine.schema import vol

from .conftest import make_routine


def test_defaults_are_filled_in() -> None:
    routine = make_routine()
    assert routine["enabled"] is True
    assert routine["days"] == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    assert routine["repeat"] is False
    assert routine["notify_on_error"] is True
    assert routine["time"] == "07:00:00"


def test_repeat_defaults_on_for_presence_and_room_triggers() -> None:
    assert (
        make_routine(trigger="everyone_leaves", people=["person.a"])["repeat"] is True
    )
    assert (
        make_routine(
            trigger="room_clear", sensors=["binary_sensor.m"], clear_minutes=5
        )["repeat"]
        is True
    )


def test_room_trigger_forces_sensor_clear() -> None:
    routine = make_routine(
        trigger="room_clear", sensors=["binary_sensor.m"], clear_minutes=5
    )
    assert routine["clear_minutes"] == 5
    assert routine["sensors"] == ["binary_sensor.m"]
    assert uses_conditions(routine)


def test_everyone_leaves_forces_everyone_away() -> None:
    routine = make_routine(trigger="everyone_leaves", people=["person.a"])
    assert routine["people"] == ["person.a"]
    assert uses_conditions(routine)


def test_time_accepts_hh_mm() -> None:
    assert make_routine(time="06:30")["time"] == "06:30:00"


@pytest.mark.parametrize(
    "overrides",
    [
        {"time": "25:00"},
        {"days": []},
        {"days": ["funday"]},
        {"clear_minutes": 2000},
        {"trigger": "room_clear", "sensors": ["binary_sensor.m"], "clear_minutes": 0},
        {"trigger": "room_clear", "sensors": []},
        {"trigger": "everyone_leaves", "people": []},
        {"require_sensors_clear": True, "sensors": []},
        {"actions": []},
        {"actions": [{"delay": 5}]},
        {"actions": [{"service": "not a service"}]},
        {"actions": [{"service": "light.turn_off", "data": []}]},
        {"id": "bad id!"},
        {"name": "  "},
        {"catch_up_minutes": 999},
        {"retries": 10},
        {"people": ["not-an-entity"]},
        {"require_states": [{"entity_id": "input_boolean.guest", "state": ""}]},
    ],
)
def test_invalid_routines_are_rejected(overrides: dict) -> None:
    raw = {
        "id": "r1",
        "name": "R1",
        "actions": [
            {"service": "light.turn_off", "target": {"entity_id": ["light.a"]}}
        ],
    }
    raw.update(overrides)
    with pytest.raises(vol.Invalid):
        normalize_routine(raw)


def test_delay_forms() -> None:
    assert parse_delay(5) == 5.0
    assert parse_delay("00:00:30") == 30.0
    assert parse_delay("01:15") == 75.0
    assert parse_delay("45") == 45.0
    with pytest.raises(vol.Invalid):
        parse_delay("soon")
    with pytest.raises(vol.Invalid):
        parse_delay(-1)


def test_linked_device_is_optional_and_must_be_an_id() -> None:
    assert make_routine()["device_id"] is None
    assert make_routine(device_id="  ")["device_id"] is None
    assert make_routine(device_id="abc123")["device_id"] == "abc123"
    with pytest.raises(vol.Invalid):
        make_routine(device_id={"id": "abc"})


def test_wait_defaults_to_the_end_of_the_day() -> None:
    routine = make_routine()
    assert routine["wait"] == "day"
    assert routine["wait_until"] == "12:00:00"
    assert make_routine(wait="until", wait_until="9:30")["wait_until"] == "09:30:00"


def test_an_unknown_wait_is_refused() -> None:
    with pytest.raises(vol.Invalid, match="until the end of the day"):
        make_routine(wait="forever")


def test_the_routine_form_keeps_the_wait() -> None:
    from custom_components.daily_routine.config_flow import (
        _form_from_routine,
        _routine_from_form,
    )

    routine = make_routine(wait="until", wait_until="10:15")
    values = _form_from_routine(routine)
    assert values["wait"] == "until"
    assert values["wait_until"] == "10:15"
    merged = _routine_from_form(
        routine,
        {**values, "wait": "no", "wait_until": "08:45"},
        routine["actions"],
        routine["conditions"],
    )
    assert merged["wait"] == "no"
    assert merged["wait_until"] == "08:45:00"
