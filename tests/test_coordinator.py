"""Scheduling, safety and execution behaviour of the coordinator."""

from __future__ import annotations

from datetime import datetime

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.daily_routine import coordinator as coordinator_module
from custom_components.daily_routine.const import EVENT_ROUTINE_SKIPPED, STORAGE_KEY
from custom_components.daily_routine.coordinator import (
    HomeRoutinesCoordinator,
    area_entity_ids,
    list_areas,
)

from .conftest import make_routine

ROOM_QUIET = dict(
    trigger="room_clear",
    clear_minutes=15,
    sensors=["binary_sensor.hall_motion"],
)

AWAY_ROUTINE = dict(
    trigger="everyone_leaves",
    require_everyone_away=True,
    require_sensors_clear=True,
    clear_minutes=15,
    people=["person.alex", "person.sam"],
    sensors=["binary_sensor.hall_motion"],
)


@pytest.fixture(autouse=True)
def _stop_coordinators(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch):
    """Cancel every timer and listener a test's coordinators started, before the harness checks for leftovers."""
    created: list[HomeRoutinesCoordinator] = []
    original_init = HomeRoutinesCoordinator.__init__

    def recording_init(self: HomeRoutinesCoordinator, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(HomeRoutinesCoordinator, "__init__", recording_init)
    yield
    for coord in created:
        coord._drop_all_handles()  # noqa: SLF001


def _at(local: str) -> datetime:
    """Aware datetime in the harness's local zone (where time triggers are evaluated)."""
    return datetime.fromisoformat(local).replace(tzinfo=dt_util.get_default_time_zone())


def _advance(hass: HomeAssistant, freezer, when: str) -> None:
    freezer.move_to(_at(when))
    async_fire_time_changed(hass, dt_util.utcnow())


def _set_home(hass: HomeAssistant, *, home: bool) -> None:
    state = "home" if home else "not_home"
    hass.states.async_set("person.alex", state)
    hass.states.async_set("person.sam", state)


async def _started(hass: HomeAssistant, *routines) -> HomeRoutinesCoordinator:
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = list(routines)
    coord._start_all(catch_up=False)  # noqa: SLF001
    await hass.async_block_till_done()
    return coord


async def test_time_routine_runs_at_its_time(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(_at("2026-10-16 06:59:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    await _started(hass, make_routine())
    assert not calls

    _advance(hass, freezer, "2026-10-16 07:00:01")
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_away_lights_wait_for_sensors_to_clear(hass: HomeAssistant, freezer) -> None:
    """Lights go off only after everyone has left AND the hall has been quiet for 15 min."""
    freezer.move_to(_at("2026-10-16 21:00:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    _set_home(hass, home=True)
    hass.states.async_set("binary_sensor.hall_motion", "on")
    coord = await _started(hass, make_routine(id="away", **AWAY_ROUTINE))

    freezer.move_to(_at("2026-10-16 21:30:00"))
    _set_home(hass, home=False)
    await hass.async_block_till_done()
    assert not calls
    assert coord.get_status("away")["waiting"] is True

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


async def test_motion_restarts_the_clear_period(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(_at("2026-10-16 21:00:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    _set_home(hass, home=True)
    hass.states.async_set("binary_sensor.hall_motion", "on")
    await _started(hass, make_routine(id="away", **AWAY_ROUTINE))

    freezer.move_to(_at("2026-10-16 21:30:00"))
    _set_home(hass, home=False)
    await hass.async_block_till_done()

    _advance(hass, freezer, "2026-10-16 21:35:00")
    hass.states.async_set("binary_sensor.hall_motion", "off")
    await hass.async_block_till_done()
    _advance(hass, freezer, "2026-10-16 21:45:00")
    hass.states.async_set("binary_sensor.hall_motion", "on")
    await hass.async_block_till_done()
    _advance(hass, freezer, "2026-10-16 21:50:00")
    hass.states.async_set("binary_sensor.hall_motion", "off")
    await hass.async_block_till_done()

    _advance(hass, freezer, "2026-10-16 21:55:00")
    await hass.async_block_till_done()
    assert not calls

    _advance(hass, freezer, "2026-10-16 22:05:00")
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_repeat_runs_again_on_a_second_departure(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(_at("2026-10-16 18:00:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    _set_home(hass, home=True)
    await _started(
        hass,
        make_routine(id="away", trigger="everyone_leaves", people=["person.alex", "person.sam"]),
    )

    freezer.move_to(_at("2026-10-16 18:30:00"))
    _set_home(hass, home=False)
    await hass.async_block_till_done()
    assert len(calls) == 1

    freezer.move_to(_at("2026-10-16 19:00:00"))
    _set_home(hass, home=True)
    await hass.async_block_till_done()

    freezer.move_to(_at("2026-10-16 19:30:00"))
    _set_home(hass, home=False)
    await hass.async_block_till_done()
    assert len(calls) == 2


async def test_without_repeat_a_second_departure_is_ignored(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(_at("2026-10-16 18:00:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    _set_home(hass, home=True)
    await _started(
        hass,
        make_routine(
            id="away", trigger="everyone_leaves", people=["person.alex", "person.sam"], repeat=False
        ),
    )
    freezer.move_to(_at("2026-10-16 18:30:00"))
    _set_home(hass, home=False)
    await hass.async_block_till_done()
    _set_home(hass, home=True)
    await hass.async_block_till_done()
    freezer.move_to(_at("2026-10-16 19:30:00"))
    _set_home(hass, home=False)
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_room_clear_runs_once_per_quiet_period(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(_at("2026-10-16 20:00:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("binary_sensor.kitchen_motion", "on")
    routine = make_routine(
        id="kitchen",
        trigger="room_clear",
        sensors=["binary_sensor.kitchen_motion"],
        clear_minutes=15,
    )
    await _started(hass, routine)

    _advance(hass, freezer, "2026-10-16 20:05:00")
    hass.states.async_set("binary_sensor.kitchen_motion", "off")
    await hass.async_block_till_done()
    _advance(hass, freezer, "2026-10-16 20:19:59")
    await hass.async_block_till_done()
    assert not calls
    _advance(hass, freezer, "2026-10-16 20:20:00")
    await hass.async_block_till_done()
    assert len(calls) == 1

    _advance(hass, freezer, "2026-10-16 20:30:00")
    hass.states.async_set("binary_sensor.kitchen_motion", "on")
    await hass.async_block_till_done()
    _advance(hass, freezer, "2026-10-16 20:31:00")
    hass.states.async_set("binary_sensor.kitchen_motion", "off")
    await hass.async_block_till_done()
    _advance(hass, freezer, "2026-10-16 20:46:00")
    await hass.async_block_till_done()
    assert len(calls) == 2


async def test_room_clear_does_not_fire_after_a_restart(hass: HomeAssistant, freezer) -> None:
    """Sensors already quiet when HA starts must not trigger a room-clear run by themselves."""
    freezer.move_to(_at("2026-10-16 02:00:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("binary_sensor.kitchen_motion", "off")
    await _started(
        hass,
        make_routine(
            id="kitchen",
            trigger="room_clear",
            sensors=["binary_sensor.kitchen_motion"],
            clear_minutes=5,
        ),
    )
    _advance(hass, freezer, "2026-10-16 03:00:00")
    await hass.async_block_till_done()
    assert not calls


async def test_waiting_routine_expires_at_midnight(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(_at("2026-10-16 21:00:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    skipped = async_capture_events(hass, EVENT_ROUTINE_SKIPPED)
    _set_home(hass, home=True)
    hass.states.async_set("binary_sensor.hall_motion", "on")
    coord = await _started(hass, make_routine(id="away", **AWAY_ROUTINE))

    freezer.move_to(_at("2026-10-16 22:00:00"))
    _set_home(hass, home=False)
    await hass.async_block_till_done()

    _advance(hass, freezer, "2026-10-17 00:00:01")
    await hass.async_block_till_done()
    assert not calls
    assert coord.get_status("away")["waiting"] is False
    assert any(e.data.get("result") == "conditions_not_met" for e in skipped)


async def test_pause_stops_scheduled_runs_but_not_manual(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(_at("2026-10-16 06:59:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    coord = await _started(hass, make_routine())
    await coord.async_set_paused(True)

    _advance(hass, freezer, "2026-10-16 07:00:01")
    await hass.async_block_till_done()
    assert not calls

    assert await coord.async_run("routine_one") is True
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_manual_run_still_respects_conditions(hass: HomeAssistant) -> None:
    _set_home(hass, home=True)
    calls = async_mock_service(hass, "light", "turn_off")
    coord = await _started(hass, make_routine(id="away", **AWAY_ROUTINE))
    assert await coord.async_run("away") is False
    await hass.async_block_till_done()
    assert not calls


async def test_manual_run_ignores_daily_limit(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "light", "turn_off")
    coord = await _started(hass, make_routine())
    assert await coord.async_run("routine_one")
    assert await coord.async_run("routine_one")
    await hass.async_block_till_done()
    assert len(calls) == 2


async def test_catch_up_runs_a_missed_time_routine(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(_at("2026-10-16 07:10:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [make_routine(catch_up_minutes=30)]
    coord._start_all(catch_up=True)  # noqa: SLF001
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_catch_up_window_is_respected(hass: HomeAssistant, freezer) -> None:
    freezer.move_to(_at("2026-10-16 08:00:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [make_routine(catch_up_minutes=30)]
    coord._start_all(catch_up=True)  # noqa: SLF001
    await hass.async_block_till_done()
    assert not calls


async def test_no_op_steps_are_skipped(hass: HomeAssistant) -> None:
    hass.states.async_set("light.hall", "off")
    calls = async_mock_service(hass, "light", "turn_off")
    coord = await _started(hass, make_routine())
    assert await coord.async_run("routine_one")
    await hass.async_block_till_done()
    assert not calls


async def test_no_op_filter_only_targets_entities_still_to_change(hass: HomeAssistant) -> None:
    hass.states.async_set("light.hall", "off")
    hass.states.async_set("light.kitchen", "on")
    calls = async_mock_service(hass, "light", "turn_off")
    coord = await _started(
        hass,
        make_routine(
            actions=[
                {
                    "service": "light.turn_off",
                    "target": {"entity_id": ["light.hall", "light.kitchen"]},
                }
            ]
        ),
    )
    await coord.async_run("routine_one")
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_failed_step_is_retried(hass: HomeAssistant, monkeypatch) -> None:
    monkeypatch.setattr(coordinator_module, "RETRY_DELAY_SECONDS", 0)
    attempts: list[int] = []

    async def flaky(call) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise HomeAssistantError("device busy")

    hass.services.async_register("light", "turn_off", flaky)
    coord = await _started(hass, make_routine(retries=2))
    assert await coord.async_run("routine_one")
    await hass.async_block_till_done()
    assert len(attempts) == 2


async def test_failure_after_retries_is_reported(hass: HomeAssistant, monkeypatch) -> None:
    monkeypatch.setattr(coordinator_module, "RETRY_DELAY_SECONDS", 0)

    async def broken(call) -> None:
        raise HomeAssistantError("offline")

    hass.services.async_register("light", "turn_off", broken)
    coord = await _started(hass, make_routine(retries=1))
    assert await coord.async_run("routine_one") is False
    assert coord.get_status("routine_one")["state"] == "error"


async def test_extra_condition_blocks_a_run(hass: HomeAssistant) -> None:
    hass.states.async_set("input_boolean.guest_mode", "on")
    coord = await _started(
        hass,
        make_routine(require_states=[{"entity_id": "input_boolean.guest_mode", "state": "off"}]),
    )
    ok, reason = coord._check(coord.routines[0], dt_util.now())  # noqa: SLF001
    assert (ok, reason) == (False, "extra_condition_not_met")
    hass.states.async_set("input_boolean.guest_mode", "off")
    ok, _ = coord._check(coord.routines[0], dt_util.now())  # noqa: SLF001
    assert ok


async def test_areas_and_their_entities(hass: HomeAssistant) -> None:
    area = ar.async_get(hass).async_create("Kitchen")
    ent_reg = er.async_get(hass)
    light = ent_reg.async_get_or_create(
        "light", "test", "kitchen_1", suggested_object_id="kitchen_light"
    )
    ent_reg.async_update_entity(light.entity_id, area_id=area.id)
    hass.states.async_set(light.entity_id, "on", {"friendly_name": "Kitchen light"})

    assert light.entity_id in area_entity_ids(hass, area.id)
    rooms = {room["name"]: room for room in list_areas(hass)}
    assert "Kitchen" in rooms
    assert rooms["Kitchen"]["entities"][0]["name"] == "Kitchen light"


async def test_preview_describes_a_dry_run(hass: HomeAssistant) -> None:
    hass.states.async_set("light.hall", "on")
    calls = async_mock_service(hass, "light", "turn_off")
    coord = await _started(
        hass,
        make_routine(
            actions=[
                {"delay": 2},
                {"service": "light.turn_off", "target": {"entity_id": ["light.hall"]}},
            ]
        ),
    )
    plan = await coord.preview(
        make_routine(
            actions=[
                {"delay": 2},
                {"service": "light.turn_off", "target": {"entity_id": ["light.hall"]}},
            ]
        )
    )
    await hass.async_block_till_done()
    assert not calls
    assert plan["steps"][0] == {"type": "delay", "seconds": 2.0}
    assert plan["steps"][1]["outcome"] == "would_run"
    assert plan["steps"][1]["entities"] == ["light.hall"]


async def test_clear_timer_is_not_persisted(hass: HomeAssistant, hass_storage) -> None:
    hass.states.async_set("binary_sensor.hall_motion", "off")
    coord = await _started(hass, make_routine(id="away", **ROOM_QUIET))
    assert coord._rt("away")["clear_since"] is not None  # noqa: SLF001

    await coord._async_save()  # noqa: SLF001
    saved = hass_storage[STORAGE_KEY]["data"]["runtime"]["away"]
    assert "clear_since" not in saved
    assert "pending_date" not in saved


async def test_clear_timer_starts_from_observed_state_after_restart(hass: HomeAssistant) -> None:
    hass.states.async_set("binary_sensor.hall_motion", "on")
    coord = await _started(hass, make_routine(id="away", **ROOM_QUIET))
    assert coord._rt("away")["clear_since"] is None  # noqa: SLF001

    hass.states.async_set("binary_sensor.hall_motion", "off")
    await hass.async_block_till_done()
    assert coord._rt("away")["clear_since"] is not None  # noqa: SLF001


async def test_save_and_delete_routines(hass: HomeAssistant, hass_storage) -> None:
    coord = HomeRoutinesCoordinator(hass)
    coord._start_all(catch_up=False)  # noqa: SLF001
    raw = {
        "id": "covers",
        "name": "Covers",
        "room": "living_room",
        "trigger": "time",
        "time": "07:00",
        "actions": [{"service": "cover.open_cover", "target": {"area_id": ["living_room"]}}],
    }
    saved = await coord.async_save_routine(raw)
    assert saved["time"] == "07:00:00"
    assert coord.get_status("covers")["room"] == "living_room"
    assert hass_storage[STORAGE_KEY]["data"]["routines"][0]["id"] == "covers"

    await coord.async_delete_routine("covers")
    assert coord.get_status("covers") is None


@pytest.mark.parametrize("bad", [{"id": "x", "name": "", "actions": []}])
async def test_saving_invalid_routine_is_rejected(hass: HomeAssistant, bad) -> None:
    coord = HomeRoutinesCoordinator(hass)
    with pytest.raises(Exception):  # noqa: B017 - voluptuous.Invalid
        await coord.async_save_routine(bad)
