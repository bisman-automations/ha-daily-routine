"""1.18.0: a step that can't change every device doesn't sink the run."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.daily_routine import coordinator as coordinator_module
from custom_components.daily_routine.coordinator import (
    TRY_AGAIN_ACTION,
    HomeRoutinesCoordinator,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .conftest import make_routine


@pytest.fixture(autouse=True)
async def _fast(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(coordinator_module, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(coordinator_module, "VERIFY_POLL_SECONDS", 0.01)
    created: list[HomeRoutinesCoordinator] = []
    original_init = HomeRoutinesCoordinator.__init__

    def recording_init(self, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(HomeRoutinesCoordinator, "__init__", recording_init)
    yield
    for coord in created:
        await coord.async_unload(save=False)


LIGHTS = ["light.kitchen", "light.hall", "light.porch"]


def _lights(hass: HomeAssistant, unreachable: set[str], *, ignored=frozenset()):
    """light.turn_off that raises for unreachable lights and turns the rest off
    (or, for ignored ones, accepts the call and does nothing)."""
    calls: list[list[str]] = []

    async def turn_off(call: ServiceCall) -> None:
        ids = list(call.data["entity_id"])
        calls.append(ids)
        if unreachable & set(ids):
            raise HomeAssistantError("Failed to connect after 4 attempt(s)")
        for entity_id in ids:
            if entity_id not in ignored:
                hass.states.async_set(
                    entity_id, "off", {"friendly_name": _name(entity_id)}
                )

    hass.services.async_register("light", "turn_off", turn_off)
    return calls


def _name(entity_id: str) -> str:
    return entity_id.split(".")[1].title() + " light"


async def _coord(hass: HomeAssistant, routine, **settings) -> HomeRoutinesCoordinator:
    for entity_id in LIGHTS:
        hass.states.async_set(entity_id, "on", {"friendly_name": _name(entity_id)})
    hass.states.async_set("switch.fan", "on", {"friendly_name": "Fan"})
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [routine]
    if settings:
        await coord.async_set_settings({**coord.settings, **settings})
    coord._start_all(catch_up=False)  # noqa: SLF001
    await hass.async_block_till_done()
    return coord


def _two_steps(**overrides):
    return make_routine(
        actions=[
            {"service": "light.turn_off", "target": {"entity_id": LIGHTS}, "data": {}},
            {
                "service": "switch.turn_off",
                "target": {"entity_id": ["switch.fan"]},
                "data": {},
            },
        ],
        **overrides,
    )


async def test_the_devices_that_answer_still_change(hass: HomeAssistant, caplog):
    _lights(hass, {"light.hall"})
    switch = async_mock_service(hass, "switch", "turn_off")
    coord = await _coord(hass, _two_steps(retry_later_minutes=0))

    assert await coord.async_run("routine_one") is False
    assert hass.states.get("light.kitchen").state == "off"
    assert hass.states.get("light.porch").state == "off"
    assert hass.states.get("light.hall").state == "on"
    # Stops there by default: the fan step didn't run.
    assert not switch
    last = coord.get_history("routine_one")[0]
    assert last["outcome"] == "failed"
    assert last["detail"] == "Hall light couldn't be reached"
    # One short line, no traceback.
    lines = [r for r in caplog.records if "Hall light" in r.getMessage()]
    assert len(lines) == 1 and lines[0].exc_info is None


async def test_keep_going_after_a_failed_step(hass: HomeAssistant):
    _lights(hass, {"light.hall"})
    switch = async_mock_service(hass, "switch", "turn_off")
    coord = await _coord(
        hass, _two_steps(on_step_error="continue", retry_later_minutes=0)
    )
    assert await coord.async_run("routine_one") is False
    assert len(switch) == 1
    assert coord.get_status("routine_one")["state"] == "error"


async def test_tries_again_later_then_finishes_the_rest(hass: HomeAssistant, freezer):
    unreachable = {"light.hall"}
    calls = _lights(hass, unreachable)
    switch = async_mock_service(hass, "switch", "turn_off")
    coord = await _coord(hass, _two_steps(retry_later_minutes=5))

    assert await coord.async_run("routine_one") is False
    assert "trying again in 5 min" in coord.get_history("routine_one")[0]["detail"]

    unreachable.clear()  # it's back
    calls.clear()
    freezer.tick(timedelta(minutes=5, seconds=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()

    # Only the device that failed, then the step that hadn't run.
    assert calls == [["light.hall"]]
    assert len(switch) == 1
    assert coord.get_status("routine_one")["state"] == "idle"
    assert coord.get_history("routine_one")[0]["detail"].startswith("Tried again: done")


async def test_gives_up_after_three_later_tries(hass: HomeAssistant, freezer):
    calls = _lights(hass, {"light.hall"})
    coord = await _coord(
        hass,
        make_routine(
            actions=[
                {
                    "service": "light.turn_off",
                    "target": {"entity_id": ["light.hall"]},
                    "data": {},
                }
            ],
            retry_later_minutes=5,
        ),
    )
    await coord.async_run("routine_one")
    for _ in range(5):
        freezer.tick(timedelta(minutes=5, seconds=1))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
    assert len(calls) == 4  # the run, then three later tries
    assert "trying again" not in coord.get_history("routine_one")[0]["detail"]


async def test_later_try_waits_for_conditions(hass: HomeAssistant, freezer):
    unreachable = {"light.hall"}
    calls = _lights(hass, unreachable)
    hass.states.async_set("input_boolean.away", "on")
    coord = await _coord(
        hass,
        make_routine(
            actions=[
                {
                    "service": "light.turn_off",
                    "target": {"entity_id": ["light.hall"]},
                    "data": {},
                }
            ],
            require_states=[{"entity_id": "input_boolean.away", "state": "on"}],
            retry_later_minutes=5,
        ),
    )
    await coord.async_run("routine_one")
    hass.states.async_set("input_boolean.away", "off")  # someone came home
    unreachable.clear()
    calls.clear()
    freezer.tick(timedelta(minutes=5, seconds=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert not calls
    assert coord.get_history("routine_one")[0]["outcome"] == "skipped"


async def test_a_stuck_device_has_a_time_limit(hass: HomeAssistant):
    async def hang(call: ServiceCall) -> None:
        await asyncio.sleep(3600)

    hass.services.async_register("light", "turn_off", hang)
    coord = await _coord(
        hass, make_routine(retry_later_minutes=0), step_timeout_seconds=1
    )
    hass.states.async_set("light.hall", "on", {"friendly_name": "Hall light"})
    assert await asyncio.wait_for(coord.async_run("routine_one"), 5) is False
    assert (
        coord.get_history("routine_one")[0]["detail"]
        == "Hall light couldn't be reached"
    )


async def test_checks_that_it_worked(hass: HomeAssistant):
    calls = _lights(hass, set(), ignored={"light.porch"})
    coord = await _coord(
        hass,
        make_routine(
            actions=[
                {
                    "service": "light.turn_off",
                    "target": {"entity_id": LIGHTS},
                    "data": {},
                }
            ],
            retry_later_minutes=0,
        ),
        verify_seconds=1,
    )
    assert await coord.async_run("routine_one") is False
    # Sent once more to the one that didn't change.
    assert calls[-1] == ["light.porch"]
    assert coord.get_history("routine_one")[0]["detail"] == "Porch light didn't change"


async def test_try_again_button(hass: HomeAssistant):
    unreachable = {"light.hall"}
    calls = _lights(hass, unreachable)
    sent = async_mock_service(hass, "notify", "mobile_app_phone")
    coord = await _coord(
        hass,
        make_routine(
            actions=[
                {
                    "service": "light.turn_off",
                    "target": {"entity_id": LIGHTS},
                    "data": {},
                }
            ],
            retry_later_minutes=0,
        ),
        notify_services=["notify.mobile_app_phone"],
    )
    hass.bus.async_listen(
        "mobile_app_notification_action",
        coord._on_notification_action,  # noqa: SLF001
    )
    await coord.async_run("routine_one")
    await hass.async_block_till_done()
    message = sent[-1].data
    assert message["message"] == (
        "Routine 'Routine one' failed: Hall light couldn't be reached."
    )
    action = message["data"]["actions"][0]
    assert action == {"action": f"{TRY_AGAIN_ACTION}routine_one", "title": "Try again"}

    unreachable.clear()
    calls.clear()
    hass.bus.async_fire("mobile_app_notification_action", {"action": action["action"]})
    await hass.async_block_till_done()
    assert calls == [["light.hall"]]
    assert coord.get_status("routine_one")["state"] == "idle"


def test_routines_saved_before_have_the_defaults():
    import voluptuous as vol

    from custom_components.daily_routine.routines import normalize_routine

    routine = make_routine()
    assert routine["on_step_error"] == "stop"
    assert routine["retry_later_minutes"] == 5
    with pytest.raises(vol.Invalid):
        normalize_routine({**routine, "on_step_error": "maybe"})
