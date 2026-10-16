"""Scheduling, safety and clear-timer behaviour of the coordinator."""

from __future__ import annotations

from datetime import datetime

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.daily_routine import HomeRoutinesCoordinator
from custom_components.daily_routine.const import (
    EVENT_ROUTINE_SKIPPED,
    STORAGE_KEY,
)

from .conftest import make_routine

AWAY_ROUTINE = dict(
    trigger="everyone_leaves",
    require_everyone_away=True,
    require_sensors_clear=True,
    clear_minutes=15,
    people=["person.alex", "person.sam"],
    sensors=["binary_sensor.hall_motion"],
)


def _at(local: str):
    """Aware datetime in the test harness's local zone (where time triggers are evaluated)."""
    return datetime.fromisoformat(local).replace(tzinfo=dt_util.get_default_time_zone())


def _set_home(hass, *, home: bool) -> None:
    state = "home" if home else "not_home"
    hass.states.async_set("person.alex", state)
    hass.states.async_set("person.sam", state)


def _advance(hass, freezer, when: str) -> None:
    freezer.move_to(_at(when))
    async_fire_time_changed(hass, dt_util.utcnow())


async def test_time_routine_runs_at_its_time(hass, freezer) -> None:
    freezer.move_to(_at("2026-10-16 06:59:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [make_routine()]
    coord._start_all()
    await hass.async_block_till_done()
    assert not calls

    _advance(hass, freezer, "2026-10-16 07:00:01")
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_away_lights_wait_for_sensors_to_clear(hass, freezer) -> None:
    """Lights go off only after everyone has left AND the hall has been quiet for 15 min."""
    freezer.move_to(_at("2026-10-16 21:00:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    _set_home(hass, home=True)
    hass.states.async_set("binary_sensor.hall_motion", "on")
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [make_routine(id="away", **AWAY_ROUTINE)]
    coord._start_all()
    await hass.async_block_till_done()

    # Last person leaves while motion is still active -> waits, does not run.
    freezer.move_to(_at("2026-10-16 21:30:00"))
    _set_home(hass, home=False)
    await hass.async_block_till_done()
    assert not calls
    assert coord.get_status("away")["waiting"] is True

    # Motion stops at 21:40; the lights must still wait until 21:55.
    _advance(hass, freezer, "2026-10-16 21:40:00")
    hass.states.async_set("binary_sensor.hall_motion", "off")
    await hass.async_block_till_done()
    _advance(hass, freezer, "2026-10-16 21:54:59")
    await hass.async_block_till_done()
    assert not calls

    _advance(hass, freezer, "2026-10-16 21:55:00")
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert coord.get_status("away")["waiting"] is False


async def test_motion_restarts_the_clear_period(hass, freezer) -> None:
    freezer.move_to(_at("2026-10-16 21:00:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    _set_home(hass, home=True)
    hass.states.async_set("binary_sensor.hall_motion", "on")
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [make_routine(id="away", **AWAY_ROUTINE)]
    coord._start_all()
    await hass.async_block_till_done()

    freezer.move_to(_at("2026-10-16 21:30:00"))
    _set_home(hass, home=False)
    await hass.async_block_till_done()

    # Quiet 21:35 -> 21:45, then motion again at 21:45, quiet again at 21:50.
    _advance(hass, freezer, "2026-10-16 21:35:00")
    hass.states.async_set("binary_sensor.hall_motion", "off")
    await hass.async_block_till_done()
    _advance(hass, freezer, "2026-10-16 21:45:00")
    hass.states.async_set("binary_sensor.hall_motion", "on")
    await hass.async_block_till_done()
    _advance(hass, freezer, "2026-10-16 21:50:00")
    hass.states.async_set("binary_sensor.hall_motion", "off")
    await hass.async_block_till_done()

    # 21:55 is when the first quiet period would have finished; it must not run.
    _advance(hass, freezer, "2026-10-16 21:55:00")
    await hass.async_block_till_done()
    assert not calls

    _advance(hass, freezer, "2026-10-16 22:05:00")
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_waiting_routine_expires_at_midnight(hass, freezer) -> None:
    freezer.move_to(_at("2026-10-16 21:00:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    skipped = async_capture_events(hass, EVENT_ROUTINE_SKIPPED)
    _set_home(hass, home=True)
    hass.states.async_set("binary_sensor.hall_motion", "on")
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [make_routine(id="away", **AWAY_ROUTINE)]
    coord._start_all()
    await hass.async_block_till_done()

    freezer.move_to(_at("2026-10-16 22:00:00"))
    _set_home(hass, home=False)
    await hass.async_block_till_done()

    _advance(hass, freezer, "2026-10-17 00:00:01")
    await hass.async_block_till_done()
    assert not calls
    assert coord.get_status("away")["waiting"] is False
    assert any(e.data.get("result") == "conditions_not_met" for e in skipped)


async def test_manual_run_still_respects_conditions(hass) -> None:
    _set_home(hass, home=True)
    calls = async_mock_service(hass, "light", "turn_off")
    coord = HomeRoutinesCoordinator(hass)
    routine = make_routine(id="away", **AWAY_ROUTINE)
    coord.routines = [routine]
    coord._start_all()

    assert await coord._execute_routine(routine, dt_util.now(), scheduled=False) is False
    await hass.async_block_till_done()
    assert not calls


async def test_manual_run_ignores_daily_limit(hass) -> None:
    calls = async_mock_service(hass, "light", "turn_off")
    coord = HomeRoutinesCoordinator(hass)
    routine = make_routine()
    coord.routines = [routine]
    now = dt_util.now()
    assert await coord._execute_routine(routine, now, scheduled=False)
    assert await coord._execute_routine(routine, now, scheduled=False)
    await hass.async_block_till_done()
    assert len(calls) == 2


async def test_clear_timer_is_not_persisted(hass, hass_storage) -> None:
    hass.states.async_set("binary_sensor.hall_motion", "off")
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [make_routine(id="away", **AWAY_ROUTINE)]
    coord._start_all()
    assert coord._rt("away")["clear_since"] is not None

    await coord._async_save()
    saved = hass_storage[STORAGE_KEY]["data"]["runtime"]["away"]
    assert "clear_since" not in saved
    assert "pending_date" not in saved


async def test_clear_timer_restarts_from_observed_state_after_restart(hass) -> None:
    """A fresh coordinator must not inherit a clear period from before a restart."""
    hass.states.async_set("binary_sensor.hall_motion", "on")
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [make_routine(id="away", **AWAY_ROUTINE)]
    coord._start_all()
    assert coord._rt("away")["clear_since"] is None

    hass.states.async_set("binary_sensor.hall_motion", "off")
    await hass.async_block_till_done()
    assert coord._rt("away")["clear_since"] is not None
