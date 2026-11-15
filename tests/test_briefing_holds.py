"""1.28.0: the morning briefing, forecast conditions, kinds of day per
person, Undo the last hour, calendar holds and how reliable devices are."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.daily_routine import coordinator as coordinator_module
from custom_components.daily_routine.briefing import (
    briefing_text,
    events_text,
    weather_text,
)
from custom_components.daily_routine.coordinator import HomeRoutinesCoordinator
from custom_components.daily_routine.day_types import (
    clean_day_types,
    day_type,
    for_person,
)
from custom_components.daily_routine.holds import active_hold, clean_holds
from custom_components.daily_routine.schema import vol
from homeassistant.core import HomeAssistant, State, SupportsResponse
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


def test_briefing_words():
    now = datetime(2026, 11, 16, 7, 0, tzinfo=_tz())
    weather = State(
        "weather.home", "cloudy", {"temperature": 7.6, "temperature_unit": "°C"}
    )
    assert (
        weather_text(weather, 12.2) == "Right now it's cloudy and 8°C, up to 12 today."
    )
    assert weather_text(None) == ""
    events = [
        {"summary": "Swim", "start": "2026-11-16T15:30:00"},
        {"summary": "Dentist", "start": "2026-11-16T09:00:00"},
        {"summary": "Teacher day", "start": "2026-11-16"},
        {"summary": "Tomorrow", "start": "2026-11-17T09:00:00"},
    ]
    assert events_text(events, now) == (
        "Today: Teacher day, all day, Dentist at 09:00 and Swim at 15:30."
    )
    assert events_text([], now) == "Nothing on the calendar today."
    text = briefing_text(now, day_type="School day", weather="Sunny.", events=None)
    assert text == "Good morning. It's a school day. Sunny."


async def test_briefing_step(hass: HomeAssistant):
    calls = async_mock_service(hass, "tts", "speak")
    hass.states.async_set("weather.home", "sunny", {"temperature": 20})
    hass.states.async_set("calendar.family", "off")

    async def get_events(call):
        return {
            "calendar.family": {
                "events": [
                    {
                        "summary": "Swim",
                        "start": dt_util.now().replace(hour=23, minute=0).isoformat(),
                    }
                ]
            }
        }

    hass.services.async_register(
        "calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY
    )
    routine = make_routine(
        trigger="manual",
        actions=[
            {
                "briefing": {
                    "voice": "tts.piper",
                    "speaker": "media_player.kitchen",
                    "weather": "weather.home",
                    "calendars": ["calendar.family"],
                }
            }
        ],
    )
    coord = await _started(hass, routine)
    assert await coord.async_run("routine_one")
    assert len(calls) == 1
    data = calls[0].data
    assert data["entity_id"] == ["tts.piper"]
    assert data["media_player_entity_id"] == "media_player.kitchen"
    assert "sunny and 20" in data["message"] and "Swim at 23:00" in data["message"]
    with pytest.raises(vol.Invalid):
        make_routine(actions=[{"briefing": {"voice": "tts.a", "speaker": "light.a"}}])


async def test_forecast_condition(hass: HomeAssistant):
    async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("weather.home", "sunny")
    routine = make_routine(
        trigger="manual",
        skip_noop=False,
        conditions=[
            {
                "kind": "forecast",
                "entity_id": "weather.home",
                "conditions": ["rainy"],
                "hours": 6,
            }
        ],
    )
    coord = await _started(hass, routine)
    # Not read yet: fails closed.
    assert not await coord.async_run("routine_one")
    assert coord.get_status("routine_one")["block_reason"] == "forecast_not_met"
    soon = (dt_util.now() + timedelta(hours=2)).isoformat()
    coord._forecast_cache["weather.home"] = [{"datetime": soon, "condition": "sunny"}]  # noqa: SLF001
    assert await coord.async_run("routine_one")
    coord._forecast_cache["weather.home"] = [{"datetime": soon, "condition": "rainy"}]  # noqa: SLF001
    assert not await coord.async_run("routine_one")
    assert coord._forecast_entities() == {"weather.home"}  # noqa: SLF001
    with pytest.raises(vol.Invalid):
        make_routine(conditions=[{"kind": "forecast", "entity_id": "weather.home"}])


def test_kinds_of_day_per_person():
    kinds = clean_day_types(
        [
            {"name": "School day", "calendar": "calendar.kid", "person": "person.kid"},
            {"name": "Work day", "calendar": "calendar.family", "words": ["work"]},
        ]
    )
    assert kinds[0]["person"] == "person.kid" and "person" not in kinds[1]
    titles = {"calendar.kid": ["Classes"], "calendar.family": []}
    assert day_type(for_person(kinds, "person.kid"), titles) == "School day"
    assert day_type(for_person(kinds, None), titles) == "Normal day"
    with pytest.raises(vol.Invalid):
        clean_day_types([{"name": "A", "calendar": "calendar.a", "person": "light.a"}])
    cond = make_routine(
        conditions=[
            {"kind": "day_type", "types": ["School day"], "person": "person.kid"}
        ]
    )["conditions"][0]
    assert cond["person"] == "person.kid"


def test_holds():
    holds = clean_holds(
        [{"calendar": "calendar.family", "words": "babysitter, vacation"}]
    )
    now = datetime(2026, 11, 15, 20, 0, tzinfo=_tz())
    events = {
        "calendar.family": [
            {
                "summary": "Dinner",
                "start": "2026-11-15T19:00:00",
                "end": "2026-11-15T21:00:00",
            },
            {
                "summary": "Babysitter",
                "start": "2026-11-15T19:30:00",
                "end": "2026-11-15T23:00:00",
            },
        ]
    }
    assert active_hold(holds, events, now) == "Babysitter"
    assert active_hold(holds, events, now.replace(hour=23, minute=30)) is None
    assert (
        active_hold(clean_holds([{"calendar": "calendar.family"}]), events, now)
        == "Dinner"
    )
    allday = {
        "calendar.family": [
            {"summary": "Vacation", "start": "2026-11-15", "end": "2026-11-16"}
        ]
    }
    assert active_hold(holds, allday, now) == "Vacation"
    with pytest.raises(vol.Invalid):
        clean_holds([{"calendar": "light.a"}])


async def test_a_hold_skips_scheduled_runs(hass: HomeAssistant):
    async_mock_service(hass, "light", "turn_off")
    coord = await _started(
        hass,
        make_routine(skip_noop=False),
        make_routine(id="always", skip_noop=False, ignore_holds=True),
    )
    coord.settings["holds"] = [{"calendar": "calendar.family", "words": []}]
    now = dt_util.now()
    coord._upcoming["calendar.family"] = [  # noqa: SLF001
        {
            "summary": "Babysitter",
            "start": (now - timedelta(hours=1)).isoformat(),
            "end": (now + timedelta(hours=1)).isoformat(),
        }
    ]
    assert not await coord._execute_routine(coord.routines[0], now, scheduled=True)  # noqa: SLF001
    assert coord.get_history("routine_one")[0]["detail"] == "calendar_hold:Babysitter"
    assert "calendar_hold" in coord.explain("routine_one")["reasons"]
    assert coord.get_status("routine_one")["hold"] == "Babysitter"
    assert await coord._execute_routine(coord.routines[1], now, scheduled=True)  # noqa: SLF001


async def test_undo_the_last_hour_and_reliability(hass: HomeAssistant):
    async def turn_off(call):
        for entity_id in call.data["entity_id"]:
            hass.states.async_set(entity_id, "off")

    async def turn_on(call):
        for entity_id in call.data["entity_id"]:
            hass.states.async_set(entity_id, "on")

    hass.services.async_register("light", "turn_off", turn_off)
    hass.services.async_register("light", "turn_on", turn_on)
    hass.states.async_set("light.hall", "on")
    hass.states.async_set("light.den", "on")
    coord = await _started(
        hass,
        make_routine(trigger="manual"),
        make_routine(
            id="den",
            trigger="manual",
            actions=[
                {"service": "light.turn_off", "target": {"entity_id": ["light.den"]}}
            ],
        ),
    )
    assert await coord.async_run("routine_one")
    assert await coord.async_run("den")
    await hass.async_block_till_done()
    assert hass.states.get("light.hall").state == "off"
    names = await coord.async_undo_recent(60)
    await hass.async_block_till_done()
    assert sorted(names) == ["Routine one", "Routine one"] or set(names) == {
        "Routine one"
    }
    assert hass.states.get("light.hall").state == "on"
    assert hass.states.get("light.den").state == "on"
    rel = coord.get_status("routine_one")["reliability"]
    assert rel is not None and rel["score"] == 100 and rel["worst"] == []
