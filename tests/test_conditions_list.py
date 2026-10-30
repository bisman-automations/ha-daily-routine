"""Extra conditions as one list: presence in or away (everyone or anyone), sensors quiet or active, and states."""

from __future__ import annotations

from datetime import datetime, timedelta

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.routines import normalize_routine
from custom_components.daily_routine.runtime import get_coordinator
from custom_components.daily_routine.schema import vol
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import make_routine

HALL = "binary_sensor.hall_motion"


async def _setup(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, get_coordinator(hass)


def _person(hass: HomeAssistant, entity: str, place: str) -> None:
    hass.states.async_set(
        entity, place, {"source": f"device_tracker.{entity.split('.')[1]}_phone"}
    )


def _presence(who: str, where: str, zone: str = "zone.home", people=("person.sam",)):
    return {
        "kind": "presence",
        "who": who,
        "where": where,
        "people": list(people),
        "zone": zone,
    }


def _sensors(mode: str, minutes: int):
    return {"kind": "sensors", "mode": mode, "minutes": minutes, "sensors": [HALL]}


# ----- presence in both directions -----


async def test_only_when_everyone_is_home(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    calls = async_mock_service(hass, "light", "turn_on")
    _person(hass, "person.sam", "home")
    _person(hass, "person.riley", "home")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(
            id="home_lights",
            name="Home lights",
            conditions=[
                _presence("everyone", "in", people=("person.sam", "person.riley"))
            ],
            actions=[
                {
                    "service": "light.turn_on",
                    "target": {"entity_id": ["light.hall"]},
                    "data": {},
                }
            ],
        )
    )
    await hass.async_block_till_done()
    assert await coordinator.async_run("home_lights") is True

    _person(hass, "person.riley", "Work")
    assert await coordinator.async_run("home_lights") is False
    assert len(calls) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_only_when_someone_is_at_work(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("zone.work", "0", {"friendly_name": "Work"})
    _person(hass, "person.sam", "home")
    _person(hass, "person.riley", "home")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(
            id="office",
            name="Office",
            conditions=[
                _presence(
                    "anyone",
                    "in",
                    zone="zone.work",
                    people=("person.sam", "person.riley"),
                )
            ],
        )
    )
    await hass.async_block_till_done()
    assert await coordinator.async_run("office") is False

    _person(hass, "person.riley", "Work")
    assert await coordinator.async_run("office") is True
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_an_unknown_person_blocks_a_presence_condition(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    async_mock_service(hass, "light", "turn_off")
    _person(hass, "person.sam", "home")
    _person(hass, "person.ghost", "home")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(
            id="gate",
            name="Gate",
            conditions=[
                _presence("everyone", "in", people=("person.sam", "person.ghost"))
            ],
        )
    )
    await hass.async_block_till_done()
    assert await coordinator.async_run("gate") is True
    _person(hass, "person.ghost", "unavailable")
    assert (
        await coordinator.async_run("gate") is False
    )  # an unavailable person blocks the run
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# ----- sensors: quiet and active -----


async def test_quiet_for_needs_the_full_minutes(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, freezer
) -> None:
    async_mock_service(hass, "light", "turn_off")
    freezer.move_to(datetime(2026, 10, 23, 9, 0, tzinfo=dt_util.UTC))
    hass.states.async_set(HALL, "off")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(id="quiet", name="Quiet", conditions=[_sensors("quiet", 10)])
    )
    await hass.async_block_till_done()
    assert await coordinator.async_run("quiet") is False  # off, but only just

    freezer.tick(timedelta(minutes=11))
    assert await coordinator.async_run("quiet") is True
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_active_within_runs_only_after_recent_motion(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, freezer
) -> None:
    async_mock_service(hass, "light", "turn_off")
    freezer.move_to(datetime(2026, 10, 23, 9, 0, tzinfo=dt_util.UTC))
    hass.states.async_set(HALL, "off")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(id="motion", name="Motion", conditions=[_sensors("active", 5)])
    )
    await hass.async_block_till_done()
    assert await coordinator.async_run("motion") is False  # no motion yet

    hass.states.async_set(HALL, "on")
    assert await coordinator.async_run("motion") is True  # motion right now
    hass.states.async_set(HALL, "off")
    freezer.tick(timedelta(minutes=2))
    assert await coordinator.async_run("motion") is True  # motion ended 2 minutes ago
    freezer.tick(timedelta(minutes=4))
    assert await coordinator.async_run("motion") is False  # motion ended 6 minutes ago
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_waiting_routine_runs_when_the_quiet_period_ends(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, freezer
) -> None:
    """A 07:00 routine waits while the hall is busy, then runs 10 quiet minutes after the last motion."""
    freezer.move_to(datetime(2026, 10, 23, 6, 59, tzinfo=dt_util.UTC))
    hass.states.async_set(HALL, "on")
    calls = async_mock_service(hass, "light", "turn_off")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(
            id="hall_off",
            name="Hall off",
            time="07:00:00",
            conditions=[_sensors("quiet", 10)],
            actions=[
                {
                    "service": "light.turn_off",
                    "target": {"entity_id": ["light.hall"]},
                    "data": {},
                }
            ],
        )
    )
    await hass.async_block_till_done()

    freezer.move_to(datetime(2026, 10, 23, 7, 0, 1, tzinfo=dt_util.UTC))
    async_fire_time_changed(hass, freezer())
    await hass.async_block_till_done()
    assert calls == []  # the 07:00 run is blocked by motion and now waits

    freezer.move_to(datetime(2026, 10, 23, 7, 2, tzinfo=dt_util.UTC))
    hass.states.async_set(HALL, "off")
    await hass.async_block_till_done()
    assert calls == []  # quiet, but not for 10 minutes yet

    freezer.move_to(datetime(2026, 10, 23, 7, 12, 1, tzinfo=dt_util.UTC))
    async_fire_time_changed(hass, freezer())
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# ----- combining, and old routines -----


async def test_any_of_several_conditions_is_enough_when_chosen(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, freezer
) -> None:
    async_mock_service(hass, "light", "turn_off")
    _person(hass, "person.sam", "home")
    hass.states.async_set(HALL, "on")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(
            id="either",
            name="Either",
            conditions=[_presence("everyone", "away"), _sensors("active", 5)],
            conditions_match="any",
        )
    )
    await hass.async_block_till_done()
    assert await coordinator.async_run("either") is True  # motion is enough
    hass.states.async_set(HALL, "off")
    assert await coordinator.async_run("either") is True  # motion ended a moment ago
    freezer.tick(timedelta(minutes=10))
    assert (
        await coordinator.async_run("either") is False
    )  # no motion lately, and someone is home
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def test_old_flags_become_conditions_on_load() -> None:
    routine = normalize_routine(
        {
            "id": "old",
            "name": "Old",
            "trigger": "time",
            "time": "07:00:00",
            "people": ["person.sam"],
            "require_everyone_away": True,
            "sensors": [HALL],
            "require_sensors_clear": True,
            "clear_minutes": 10,
            "require_states": [{"entity_id": "lock.front_door", "state": "locked"}],
            "actions": [{"service": "light.turn_off", "data": {}}],
        }
    )
    assert routine["conditions"] == [
        {
            "kind": "presence",
            "who": "everyone",
            "where": "away",
            "people": ["person.sam"],
            "zone": "zone.home",
        },
        {"kind": "sensors", "mode": "quiet", "minutes": 10, "sensors": [HALL]},
        {"kind": "state", "entity_id": "lock.front_door", "state": "locked"},
    ]
    assert "require_everyone_away" not in routine
    assert "require_states" not in routine


def test_a_bad_condition_is_rejected() -> None:
    import pytest

    with pytest.raises(vol.Invalid, match="presence condition"):
        normalize_routine(
            make_routine(
                conditions=[
                    {
                        "kind": "presence",
                        "who": "some",
                        "where": "in",
                        "people": ["person.sam"],
                    }
                ]
            )
        )
    with pytest.raises(vol.Invalid, match="at least 1 minute"):
        normalize_routine(
            make_routine(
                conditions=[
                    {
                        "kind": "sensors",
                        "mode": "active",
                        "minutes": 0,
                        "sensors": [HALL],
                    }
                ]
            )
        )
