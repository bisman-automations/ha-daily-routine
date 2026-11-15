"""1.31.0: phones charging and a sensor's level as conditions, routines
missed while Home Assistant was off, skip days, and the coming-home summary."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.daily_routine import coordinator as coordinator_module
from custom_components.daily_routine.coordinator import HomeRoutinesCoordinator
from custom_components.daily_routine.run_log import away_text, missed_today
from custom_components.daily_routine.schema import vol
from homeassistant.core import HomeAssistant
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


async def _started(hass: HomeAssistant, *routines: dict[str, Any]):
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = list(routines)
    coord._start_all(catch_up=False)  # noqa: SLF001
    await hass.async_block_till_done()
    return coord


async def test_charging_and_value_conditions(hass: HomeAssistant):
    routine = make_routine(
        trigger="manual",
        conditions=[
            {
                "kind": "charging",
                "entities": ["sensor.a_battery_state", "binary_sensor.b_is_charging"],
            },
            {"kind": "value", "entity_id": "sensor.rain", "below": 5},
        ],
    )
    coord = await _started(hass, routine)
    now = dt_util.now()
    hass.states.async_set("sensor.a_battery_state", "Charging".lower())
    hass.states.async_set("binary_sensor.b_is_charging", "off")
    hass.states.async_set("sensor.rain", "2")
    assert coord._check(routine, now) == (False, "charging_not_met")  # noqa: SLF001
    hass.states.async_set("binary_sensor.b_is_charging", "on")
    assert coord._check(routine, now)[0]  # noqa: SLF001
    hass.states.async_set("sensor.rain", "7")
    assert coord._check(routine, now) == (False, "value_not_met")  # noqa: SLF001
    hass.states.async_set("sensor.rain", "unavailable")
    assert not coord._check(routine, now)[0]  # noqa: SLF001
    with pytest.raises(vol.Invalid):
        make_routine(conditions=[{"kind": "value", "entity_id": "sensor.rain"}])
    with pytest.raises(vol.Invalid):
        make_routine(conditions=[{"kind": "charging", "entities": ["light.x"]}])


def test_missed_today():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=dt_util.get_default_time_zone())
    morning = make_routine(id="m", time="07:00:00")
    late = make_routine(id="l", time="18:00:00")
    ran = make_routine(id="r", time="08:00:00")
    catching = make_routine(id="c", time="11:50:00", catch_up_minutes=30)
    events = [
        {
            "at": now.replace(hour=8, minute=0).isoformat(),
            "routine_id": "r",
            "outcome": "ran",
        }
    ]
    assert missed_today(
        [morning, late, ran, catching], events, now, lambda _r: True
    ) == ["m"]
    assert missed_today([morning], events, now, lambda _r: False) == []


async def test_missed_report_and_run_them_now(hass: HomeAssistant):
    created = async_mock_service(hass, "persistent_notification", "create")
    async_mock_service(hass, "light", "turn_off")
    early = (dt_util.now() - timedelta(hours=1)).strftime("%H:%M:00")
    if early > dt_util.now().strftime("%H:%M:00"):
        pytest.skip("just after midnight")
    routine = make_routine(time=early)
    coord = await _started(hass, routine)
    coord._report_missed(dt_util.now())  # noqa: SLF001
    await hass.async_block_till_done()
    assert not created  # off by default
    coord.settings["missed_report"] = True
    coord._report_missed(dt_util.now())  # noqa: SLF001
    await hass.async_block_till_done()
    assert "Routine one" in created[-1].data["message"]
    assert await coord.async_run_missed() == ["routine_one"]


async def test_skip_dates(hass: HomeAssistant):
    today = dt_util.now().date()
    routine = make_routine(
        skip_dates=[
            today.isoformat(),
            "2001-01-01",
            (today + timedelta(days=3)).isoformat(),
        ]
    )
    assert routine["skip_dates"] == [
        today.isoformat(),
        (today + timedelta(days=3)).isoformat(),
    ]
    coord = await _started(hass, routine)
    assert not await coord._execute_routine(routine, dt_util.now(), scheduled=True)  # noqa: SLF001
    assert "skip_date" in coord.explain("routine_one")["reasons"]
    with pytest.raises(vol.Invalid):
        make_routine(skip_dates=["soon"])
    with pytest.raises(vol.Invalid):
        make_routine(
            skip_dates=[(today + timedelta(days=i)).isoformat() for i in range(40)]
        )


async def test_coming_home_summary(hass: HomeAssistant):
    created = async_mock_service(hass, "persistent_notification", "create")
    async_mock_service(hass, "light", "turn_off")
    coord = await _started(hass, make_routine(trigger="manual"))
    coord.settings["away_summary"] = True
    await coord.async_set_mode("away")
    assert coord.settings["away_since"]
    assert await coord.async_run("routine_one")
    await coord.async_set_mode("home")
    await hass.async_block_till_done()
    assert "While you were away: 1 run worked" in created[-1].data["message"]
    assert "away_since" not in coord.settings
    since = datetime(2026, 1, 1, tzinfo=dt_util.get_default_time_zone())
    assert (
        away_text([], since, []) == "Welcome home. No routines ran while you were away."
    )
