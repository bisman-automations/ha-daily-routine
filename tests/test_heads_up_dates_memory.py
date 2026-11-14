"""1.19.0: heads-up with Skip, only part of the year, remember and put back, fades."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.daily_routine import coordinator as coordinator_module
from custom_components.daily_routine.coordinator import (
    SKIP_ACTION,
    HomeRoutinesCoordinator,
)
from custom_components.daily_routine.routines import in_dates
from custom_components.daily_routine.schema import vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .conftest import make_routine


@pytest.fixture(autouse=True)
async def _stop(monkeypatch: pytest.MonkeyPatch):
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


def _at(local: str) -> datetime:
    return datetime.fromisoformat(local).replace(tzinfo=dt_util.get_default_time_zone())


async def _started(hass: HomeAssistant, *routines, **settings):
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = list(routines)
    if settings:
        await coord.async_set_settings({**coord.settings, **settings})
    coord._start_all(catch_up=False)  # noqa: SLF001
    await hass.async_block_till_done()
    return coord


# ----- only part of the year -----


def test_in_dates_wraps_the_new_year():
    assert in_dates(date(2026, 12, 25), "11-01", "03-31")
    assert in_dates(date(2027, 3, 31), "11-01", "03-31")
    assert not in_dates(date(2026, 7, 4), "11-01", "03-31")
    assert in_dates(date(2026, 7, 4), "06-01", "08-31")
    assert not in_dates(date(2026, 9, 1), "06-01", "08-31")
    assert in_dates(date(2026, 9, 1), None, None)


def test_dates_need_both_and_a_real_day():
    assert (
        make_routine(from_date="2026-11-01", until_date="03-31")["from_date"] == "11-01"
    )
    with pytest.raises(vol.Invalid):
        make_routine(from_date="11-01")
    with pytest.raises(vol.Invalid):
        make_routine(from_date="02-30", until_date="03-31")
    routine = make_routine()
    assert (
        routine["from_date"],
        routine["until_date"],
        routine["heads_up_minutes"],
    ) == (
        None,
        None,
        0,
    )


async def test_next_run_waits_for_the_first_day(hass: HomeAssistant, freezer):
    freezer.move_to(_at("2026-07-04 12:00:00"))
    coord = await _started(hass, make_routine(from_date="11-01", until_date="03-31"))
    assert coord.get_status("routine_one")["next_run"].startswith("2026-11-01T07:00")


# ----- heads-up -----


async def test_heads_up_with_a_skip_button(hass: HomeAssistant, freezer):
    freezer.move_to(_at("2026-11-14 06:00:00"))
    sent = async_mock_service(hass, "notify", "mobile_app_phone")
    calls = async_mock_service(hass, "light", "turn_off")
    coord = await _started(
        hass,
        make_routine(heads_up_minutes=10),
        notify_services=["notify.mobile_app_phone"],
    )
    hass.bus.async_listen(
        "mobile_app_notification_action",
        coord._on_notification_action,  # noqa: SLF001
    )
    freezer.move_to(_at("2026-11-14 06:50:01"))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    message = sent[-1].data
    assert message["message"] == "Routine 'Routine one' runs at 07:00."
    assert message["data"]["actions"] == [
        {"action": f"{SKIP_ACTION}routine_one", "title": "Skip it"}
    ]

    hass.bus.async_fire(
        "mobile_app_notification_action", {"action": f"{SKIP_ACTION}routine_one"}
    )
    await hass.async_block_till_done()
    assert coord._find("routine_one")["skip_next"] is True  # noqa: SLF001
    freezer.move_to(_at("2026-11-14 07:00:01"))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert not calls


# ----- remember and put back -----


async def test_remember_and_put_back(hass: HomeAssistant):
    hass.states.async_set(
        "light.lamp",
        "on",
        {"brightness": 120, "color_mode": "color_temp", "color_temp_kelvin": 2700},
    )
    hass.states.async_set("light.ceiling", "off")
    hass.states.async_set("switch.fan", "on")
    hass.states.async_set("sensor.temp", "21")
    lights = async_mock_service(hass, "light", "turn_on")
    switches = async_mock_service(hass, "switch", "turn_on")
    coord = await _started(hass)

    on = await coord.async_remember(
        "Before we left", ["light.lamp", "light.ceiling", "switch.fan", "sensor.temp"]
    )
    assert on == ["light.lamp", "switch.fan"]

    await coord.async_put_back("Before we left")
    assert lights[0].data == {
        "entity_id": ["light.lamp"],
        "brightness": 120,
        "color_temp_kelvin": 2700,
    }
    assert switches[0].data == {"entity_id": ["switch.fan"]}
    # Forgotten once they're back on.
    assert "Before we left" not in coord.memories
    with pytest.raises(vol.Invalid):
        await coord.async_put_back("Before we left")


async def test_remember_as_routine_steps(
    hass: HomeAssistant, enable_custom_integrations
):
    """The two steps are real actions, so routines (and automations) can use them."""
    from custom_components.daily_routine.const import DOMAIN
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, "remember_states")
    assert hass.services.has_service(DOMAIN, "put_back_states")


# ----- fades -----


async def test_a_fade_is_given_time_before_the_check(hass: HomeAssistant):
    hass.states.async_set("light.hall", "on", {"friendly_name": "Hall light"})

    async def turn_off(call: ServiceCall) -> None:
        # Like a light that reports off at the end of its fade.
        async_call_later(
            hass, 1.5, lambda _now: hass.states.async_set("light.hall", "off")
        )

    hass.services.async_register("light", "turn_off", turn_off)
    coord = await _started(
        hass,
        make_routine(
            actions=[
                {
                    "service": "light.turn_off",
                    "target": {"entity_id": ["light.hall"]},
                    "data": {"transition": 2},
                }
            ],
            retry_later_minutes=0,
        ),
        verify_seconds=1,
    )
    assert await coord.async_run("routine_one") is True
