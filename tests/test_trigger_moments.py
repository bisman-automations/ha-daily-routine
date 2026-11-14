"""1.20.0: a trigger is a moment. Waits from event triggers end with the moment,
and trackers or sensors dropping out for a moment aren't moves or crossings."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.daily_routine.coordinator import HomeRoutinesCoordinator
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import make_routine

PEOPLE = ["person.alex", "person.sam"]
LOCKED = {
    "kind": "state",
    "entity_id": "lock.garage",
    "state": "locked",
}


@pytest.fixture(autouse=True)
async def _stop(monkeypatch: pytest.MonkeyPatch):
    created: list[HomeRoutinesCoordinator] = []
    original_init = HomeRoutinesCoordinator.__init__

    def recording_init(self, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(HomeRoutinesCoordinator, "__init__", recording_init)
    yield
    for coord in created:
        await coord.async_unload(save=False)


async def _started(hass: HomeAssistant, *routines) -> HomeRoutinesCoordinator:
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = list(routines)
    coord._start_all(catch_up=False)  # noqa: SLF001
    await hass.async_block_till_done()
    return coord


def _people(hass: HomeAssistant, *states: str) -> None:
    for person, state in zip(PEOPLE, states, strict=True):
        hass.states.async_set(person, state)


def _arrives(**overrides):
    return make_routine(
        trigger="someone_arrives",
        people=PEOPLE,
        repeat=True,
        conditions=[LOCKED],
        actions=[
            {
                "service": "lock.unlock",
                "target": {"entity_id": ["lock.garage"]},
                "data": {},
            }
        ],
        **overrides,
    )


async def test_a_waiting_arrival_is_dropped_when_everyone_leaves_again(
    hass: HomeAssistant,
):
    _people(hass, "not_home", "not_home")
    hass.states.async_set("lock.garage", "unlocked")
    unlocks = async_mock_service(hass, "lock", "unlock")
    coord = await _started(hass, _arrives(wait="day"))

    _people(hass, "home", "not_home")  # arrives, but the lock isn't locked: waits
    await hass.async_block_till_done()
    assert coord.get_status("routine_one")["waiting"]

    _people(hass, "not_home", "not_home")  # gone again
    hass.states.async_set("lock.garage", "locked")  # the conditions now pass
    await hass.async_block_till_done()
    assert not unlocks
    assert coord.get_history("routine_one")[0]["detail"] == "trigger_ended"


async def test_a_short_wait_ends_after_its_minutes(hass: HomeAssistant, freezer):
    _people(hass, "not_home", "not_home")
    hass.states.async_set("lock.garage", "unlocked")
    unlocks = async_mock_service(hass, "lock", "unlock")
    coord = await _started(hass, _arrives(wait="minutes", wait_minutes=5))

    _people(hass, "home", "not_home")
    await hass.async_block_till_done()
    assert coord.get_status("routine_one")["waiting"]
    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert not coord.get_status("routine_one")["waiting"]

    hass.states.async_set("lock.garage", "locked")  # hours later
    await hass.async_block_till_done()
    assert not unlocks


async def test_a_short_wait_runs_when_the_conditions_pass_in_time(hass: HomeAssistant):
    _people(hass, "not_home", "not_home")
    hass.states.async_set("lock.garage", "unlocked")
    unlocks = async_mock_service(hass, "lock", "unlock")
    await _started(hass, _arrives(wait="minutes", wait_minutes=5))
    _people(hass, "home", "not_home")
    await hass.async_block_till_done()
    hass.states.async_set("lock.garage", "locked")
    await hass.async_block_till_done()
    assert len(unlocks) == 1


async def test_a_tracker_dropping_out_is_not_an_arrival(hass: HomeAssistant):
    _people(hass, "not_home", "not_home")
    hass.states.async_set("lock.garage", "locked")
    unlocks = async_mock_service(hass, "lock", "unlock")
    await _started(hass, _arrives(wait="no"))

    _people(hass, "unavailable", "not_home")
    await hass.async_block_till_done()
    _people(hass, "not_home", "not_home")
    await hass.async_block_till_done()
    assert not unlocks

    _people(hass, "unavailable", "not_home")
    await hass.async_block_till_done()
    _people(hass, "home", "not_home")  # a real arrival, through unavailable
    await hass.async_block_till_done()
    assert len(unlocks) == 1


async def test_trackers_loading_after_a_restart_are_not_leaving(hass: HomeAssistant):
    _people(hass, "unknown", "unknown")
    lights = async_mock_service(hass, "light", "turn_off")
    await _started(
        hass,
        make_routine(trigger="everyone_leaves", people=PEOPLE, repeat=True, wait="no"),
    )
    _people(hass, "not_home", "not_home")
    await hass.async_block_till_done()
    assert not lights
    _people(hass, "home", "not_home")
    await hass.async_block_till_done()
    _people(hass, "not_home", "not_home")  # a real departure
    await hass.async_block_till_done()
    assert len(lights) == 1


async def test_a_person_leaving_through_unavailable_counts_once(hass: HomeAssistant):
    hass.states.async_set("person.alex", "home")
    lights = async_mock_service(hass, "light", "turn_off")
    await _started(
        hass,
        make_routine(trigger="person_leaves", people=["person.alex"], repeat=True),
    )
    hass.states.async_set("person.alex", "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set("person.alex", "not_home")
    await hass.async_block_till_done()
    assert len(lights) == 1
    hass.states.async_set("person.alex", "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set("person.alex", "not_home")
    await hass.async_block_till_done()
    assert len(lights) == 1


async def test_a_device_dropping_out_and_back_is_not_a_change(hass: HomeAssistant):
    hass.states.async_set("binary_sensor.door", "on")
    lights = async_mock_service(hass, "light", "turn_off")
    await _started(
        hass,
        make_routine(
            trigger="state_is",
            watch_entity="binary_sensor.door",
            watch_states=["on"],
            repeat=True,
        ),
    )
    hass.states.async_set("binary_sensor.door", "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.door", "on")
    await hass.async_block_till_done()
    assert not lights
    hass.states.async_set("binary_sensor.door", "off")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.door", "on")
    await hass.async_block_till_done()
    assert len(lights) == 1


async def test_going_unavailable_still_counts_when_it_is_the_chosen_state(
    hass: HomeAssistant,
):
    hass.states.async_set("binary_sensor.door", "on")
    lights = async_mock_service(hass, "light", "turn_off")
    await _started(
        hass,
        make_routine(
            trigger="state_is",
            watch_entity="binary_sensor.door",
            watch_states=["unavailable"],
            repeat=True,
        ),
    )
    hass.states.async_set("binary_sensor.door", "unavailable")
    await hass.async_block_till_done()
    assert len(lights) == 1


async def test_a_sensor_coming_back_only_crosses_from_its_last_value(
    hass: HomeAssistant,
):
    hass.states.async_set("sensor.humidity", "70")
    lights = async_mock_service(hass, "light", "turn_off")
    await _started(
        hass,
        make_routine(
            trigger="value_above",
            value_entity="sensor.humidity",
            value_threshold=60,
            repeat=True,
        ),
    )
    hass.states.async_set("sensor.humidity", "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.humidity", "72")  # was above already
    await hass.async_block_till_done()
    assert not lights
    hass.states.async_set("sensor.humidity", "50")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.humidity", "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.humidity", "65")  # below, then above: crossed
    await hass.async_block_till_done()
    assert len(lights) == 1


async def test_not_again_within_minutes(hass: HomeAssistant, freezer):
    hass.states.async_set("binary_sensor.door", "off")
    lights = async_mock_service(hass, "light", "turn_off")
    coord = await _started(
        hass,
        make_routine(
            trigger="state_is",
            watch_entity="binary_sensor.door",
            watch_states=["on"],
            repeat=True,
            cooldown_minutes=10,
        ),
    )
    for _ in range(3):  # opened and closed three times in a row
        hass.states.async_set("binary_sensor.door", "on")
        await hass.async_block_till_done()
        hass.states.async_set("binary_sensor.door", "off")
        await hass.async_block_till_done()
    assert len(lights) == 1
    assert coord.get_history("routine_one")[0]["detail"] == "cooldown"

    freezer.tick(timedelta(minutes=11))
    hass.states.async_set("binary_sensor.door", "on")
    await hass.async_block_till_done()
    assert len(lights) == 2
    # Run now isn't held back.
    assert await coord.async_run("routine_one") is True
