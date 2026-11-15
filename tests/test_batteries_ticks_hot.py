"""1.30.0: lived-in lights like last week, routines that need a look,
"{who}" in announcements, the battery check, hot-day times, checklists and
the month's power."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.daily_routine import coordinator as coordinator_module
from custom_components.daily_routine.battery import battery_text, low_batteries
from custom_components.daily_routine.coordinator import (
    HomeRoutinesCoordinator,
    _with_who,
)
from custom_components.daily_routine.energy import month_energy
from custom_components.daily_routine.lived_in import clean_settings, replay_wanted
from custom_components.daily_routine.run_log import needs_attention
from custom_components.daily_routine.schema import vol
from custom_components.daily_routine.weather_shift import day_high, shift_minutes
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import make_routine


@pytest.fixture(autouse=True)
async def _stop(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(coordinator_module, "VERIFY_POLL_SECONDS", 0.01)
    monkeypatch.setattr(coordinator_module, "POWER_SETTLE_SECONDS", 0)
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


def _tz():
    return dt_util.get_default_time_zone()


def test_replay_last_week():
    now = datetime(2026, 11, 15, 20, 0, tzinfo=_tz())
    week = now - timedelta(days=7)
    events = [
        {"e": "light.hall", "on": True, "at": (week - timedelta(hours=2)).isoformat()},
        {"e": "light.den", "on": True, "at": (week - timedelta(hours=1)).isoformat()},
        {
            "e": "light.den",
            "on": False,
            "at": (week - timedelta(minutes=5)).isoformat(),
        },
        {"e": "light.den", "on": True, "at": (week + timedelta(hours=1)).isoformat()},
        {"e": "light.other", "on": True, "at": week.isoformat()},
    ]
    lights = ["light.hall", "light.den"]
    assert replay_wanted(events, lights, now) == {
        "light.hall": True,
        "light.den": False,
    }
    assert replay_wanted(events, lights, now + timedelta(days=1)) is None
    assert (
        clean_settings({"modes": ["away"], "pattern": "last_week"})["pattern"]
        == "last_week"
    )
    with pytest.raises(vol.Invalid):
        clean_settings({"modes": ["away"], "pattern": "disco"})


def test_needs_attention():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=_tz())
    at = (now - timedelta(days=1)).isoformat()
    events = [
        *[{"at": at, "routine_id": "a", "outcome": "skipped"} for _ in range(3)],
        {"at": at, "routine_id": "a", "outcome": "ran"},
        *[{"at": at, "routine_id": "b", "outcome": "ran"} for _ in range(4)],
        {"at": at, "routine_id": "c", "outcome": "failed"},
    ]
    routines = [
        {"id": "a", "name": "A", "enabled": True},
        {"id": "b", "name": "B", "enabled": True},
        {"id": "c", "name": "C", "enabled": True},
    ]
    rows = needs_attention(events, now, routines)
    assert [r["routine_id"] for r in rows] == ["a"]
    assert rows[0]["score"] == 25 and rows[0]["skipped"] == 3


async def test_who_arrived_is_said(hass: HomeAssistant):
    speak = async_mock_service(hass, "tts", "speak")
    hass.states.async_set("person.sam", "not_home", {"friendly_name": "Sam"})
    routine = make_routine(
        trigger="someone_arrives",
        people=["person.sam"],
        repeat=True,
        actions=[
            {
                "service": "tts.speak",
                "target": {"entity_id": ["tts.piper"]},
                "data": {
                    "message": "{who} is home.",
                    "media_player_entity_id": "media_player.hall",
                },
            }
        ],
    )
    await _started(hass, routine)
    hass.states.async_set("person.sam", "home", {"friendly_name": "Sam"})
    await hass.async_block_till_done()
    assert speak and speak[-1].data["message"] == "Sam is home."
    assert _with_who({"service": "light.turn_on", "data": {}}, "X") == {
        "service": "light.turn_on",
        "data": {},
    }


def test_low_batteries():
    found = low_batteries(
        [
            ("Back door", "sensor", "12"),
            ("Hall", "sensor", "80"),
            ("Smoke alarm", "binary_sensor", "on"),
            ("Gone", "sensor", "unavailable"),
        ],
        20,
    )
    assert found == [("Smoke alarm", "low"), ("Back door", "12%")]
    assert battery_text(found) == "Low batteries: Smoke alarm (low), Back door 12%."


async def test_battery_step(hass: HomeAssistant):
    created = async_mock_service(hass, "persistent_notification", "create")
    hass.states.async_set(
        "sensor.door_battery", "9", {"device_class": "battery", "friendly_name": "Door"}
    )
    hass.states.async_set(
        "sensor.lamp_battery",
        "95",
        {"device_class": "battery", "friendly_name": "Lamp"},
    )
    routine = make_routine(trigger="manual", actions=[{"check_battery": {"below": 15}}])
    coord = await _started(hass, routine)
    assert await coord.async_run("routine_one")
    await hass.async_block_till_done()
    assert created[-1].data["message"] == "Low batteries: Door 9%."
    with pytest.raises(vol.Invalid):
        make_routine(actions=[{"check_battery": {"below": 0}}])


def test_hot_days():
    day = date(2026, 7, 1)
    forecast = [
        {"datetime": "2026-07-01T15:00:00+00:00", "temperature": 31},
        {"datetime": "2026-07-02T15:00:00+00:00", "temperature": 20},
    ]
    rule = {"entity_id": "weather.home", "above": 30, "minutes": -60}
    assert day_high(forecast, day) == 31
    assert shift_minutes(rule, forecast, day) == -60
    assert shift_minutes(rule, forecast, date(2026, 7, 2)) == 0
    assert shift_minutes(rule, None, day) == 0
    routine = make_routine(
        hot_shift={"entity_id": "weather.home", "above": 30, "minutes": -60}
    )
    assert routine["hot_shift"]["minutes"] == -60
    with pytest.raises(vol.Invalid):
        make_routine(trigger="manual", hot_shift=rule)
    with pytest.raises(vol.Invalid):
        make_routine(hot_shift={**rule, "minutes": 500})


async def test_hot_day_moves_the_time(hass: HomeAssistant):
    routine = make_routine(
        time="18:00:00",
        hot_shift={"entity_id": "weather.home", "above": 25, "minutes": -60},
    )
    coord = await _started(hass, routine)
    after = datetime(2026, 7, 1, 8, 0, tzinfo=_tz())
    assert coord.next_run_after(routine, after).hour == 18
    coord._forecast_cache["weather.home"] = [  # noqa: SLF001
        {"datetime": after.replace(hour=14).isoformat(), "temperature": 28}
    ]
    assert coord.next_run_after(routine, after).hour == 17


async def test_checklist_step(hass: HomeAssistant):
    lights = async_mock_service(hass, "light", "turn_off")
    routine = make_routine(
        trigger="manual",
        actions=[
            {"tick_list": {"items": ["Back door locked", "Stove off"], "minutes": 5}},
            {
                "service": "light.turn_off",
                "target": {"entity_id": ["light.hall"]},
                "data": {},
            },
        ],
    )
    coord = await _started(hass, routine)
    task = hass.async_create_task(coord.async_run("routine_one"))
    for _ in range(50):
        await asyncio.sleep(0)
        if coord.get_status("routine_one")["ticklist"]:
            break
    status = coord.get_status("routine_one")
    assert status["ticklist"]["items"] == ["Back door locked", "Stove off"]
    assert coord.async_tick_item("routine_one", "Stove off", True) == ["Stove off"]
    assert not lights
    coord.async_tick_item("routine_one", "Back door locked", True)
    assert await task
    assert len(lights) == 1
    assert coord.get_status("routine_one")["ticklist"] is None
    with pytest.raises(vol.Invalid):
        make_routine(actions=[{"tick_list": {"items": []}}])


def test_month_energy():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=_tz())
    at = (now - timedelta(days=2)).isoformat()
    old = (now - timedelta(days=40)).isoformat()
    power = {
        "night": [
            {"at": at, "before": 600, "after": 100},
            {"at": at, "before": 500, "after": 100},
            {"at": old, "before": 900, "after": 0},
        ],
        "morning": [{"at": at, "before": 50, "after": 300}],
        "gone": [{"at": at, "before": 1, "after": 0}],
    }
    result = month_energy(power, {"night": "Night", "morning": "Morning"}, now)
    assert [r["routine_id"] for r in result["rows"]] == ["night", "morning"]
    assert (
        result["rows"][0]["runs"] == 2 and result["rows"][0]["average_change"] == -450
    )
    assert result["turned_off"] == 900


async def test_tick_command_and_overview(
    hass: HomeAssistant, enable_custom_integrations, hass_ws_client
):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.daily_routine.const import DOMAIN

    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/overview"})
    overview = (await client.receive_json())["result"]
    assert overview["attention"] == [] and overview["energy"]["rows"] == []
    await client.send_json(
        {
            "id": 2,
            "type": f"{DOMAIN}/routine/tick",
            "routine_id": "x",
            "item": "y",
            "done": True,
        }
    )
    assert (await client.receive_json())["error"]["code"] == "not_found"
