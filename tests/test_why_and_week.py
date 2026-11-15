"""1.27.0: why it won't run, sun limits, house modes it skips, the weekly
report, habit ideas with conditions, waiting for cheaper electricity, the
Kind of day sensor and a card per group."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.daily_routine import coordinator as coordinator_module
from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.coordinator import HomeRoutinesCoordinator, _clamp
from custom_components.daily_routine.habits import suggest
from custom_components.daily_routine.progress import step_label
from custom_components.daily_routine.runtime import get_coordinator
from custom_components.daily_routine.schema import vol
from custom_components.daily_routine.weekly import clean_weekly, weekly_text
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import make_routine


@pytest.fixture(autouse=True)
async def _stop(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(coordinator_module, "VERIFY_POLL_SECONDS", 0.01)
    monkeypatch.setattr(coordinator_module, "POWER_SETTLE_SECONDS", 0)
    monkeypatch.setattr(coordinator_module, "SECONDS_PER_MINUTE", 0.01)
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


async def test_why_it_wont_run(hass: HomeAssistant):
    routine = make_routine(
        skip_next=True,
        skip_modes=["guests"],
        conditions=[{"kind": "mode", "modes": ["home"]}],
    )
    coord = await _started(hass, routine)
    why = coord.explain("routine_one")
    assert why["reasons"] == ["skip_next"]
    assert why["next_run"]
    coord.mode = "guests"
    assert coord.explain("routine_one")["reasons"] == [
        "skip_next",
        "skip_mode",
        "wrong_mode",
    ]
    with pytest.raises(vol.Invalid):
        coord.explain("nope")


async def test_skipped_in_a_house_mode(hass: HomeAssistant):
    async_mock_service(hass, "light", "turn_off")
    coord = await _started(hass, make_routine(skip_modes=["guests"], skip_noop=False))
    coord.mode = "guests"
    now = dt_util.now()
    assert not await coord._execute_routine(  # noqa: SLF001
        coord.routines[0], now, scheduled=True
    )
    assert coord.get_history("routine_one")[0]["detail"] == "skip_mode"
    # Run now isn't held back.
    assert await coord.async_run("routine_one")
    assert coord.explain("routine_one")["last_skip"]["reason"] == "skip_mode"


def test_sun_limits():
    routine = make_routine(trigger="sunset", not_before="18:00", not_after="20:30")
    assert routine["not_before"] == "18:00:00"
    early = datetime(2026, 12, 1, 16, 45, tzinfo=_tz())
    late = datetime(2026, 6, 21, 21, 40, tzinfo=_tz())
    middle = datetime(2026, 9, 1, 19, 50, tzinfo=_tz())
    assert _clamp(early, routine).hour == 18
    assert (_clamp(late, routine).hour, _clamp(late, routine).minute) == (20, 30)
    assert _clamp(middle, routine) == middle
    # Only for sunrise and sunset.
    assert make_routine(trigger="time", not_before="18:00")["not_before"] is None


async def test_sun_limits_move_the_next_run(hass: HomeAssistant):
    routine = make_routine(trigger="sunset", not_before="23:30")
    coord = await _started(hass, routine)
    after = datetime(2026, 11, 15, 12, 0, tzinfo=_tz())
    when = coord._next_run_datetime(routine, after)  # noqa: SLF001
    assert when is not None and (when.hour, when.minute) == (23, 30)


def test_weekly_report_text():
    now = datetime(2026, 11, 15, 18, 0, tzinfo=_tz())
    events = [
        {
            "at": (now - timedelta(days=1)).isoformat(),
            "routine_id": "a",
            "name": "A",
            "outcome": "ran",
        },
        {
            "at": (now - timedelta(days=2)).isoformat(),
            "routine_id": "b",
            "name": "B",
            "outcome": "failed",
        },
        {
            "at": (now - timedelta(days=3)).isoformat(),
            "routine_id": "b",
            "name": "B",
            "outcome": "failed",
        },
        {
            "at": (now - timedelta(days=9)).isoformat(),
            "routine_id": "c",
            "name": "C",
            "outcome": "ran",
        },
    ]
    routines = [
        make_routine(id="a", name="A"),
        make_routine(id="b", name="B"),
        make_routine(id="c", name="C"),
        make_routine(id="m", name="M", trigger="manual"),
    ]
    text = weekly_text(events, routines, now)
    assert "This week: 1 ran, 2 failed, 0 skipped." in text
    assert "Failed most: B (2×)" in text
    assert "Didn't run: B, C" in text
    assert (
        clean_weekly({"enabled": True, "weekday": 0, "time": "08:30"})["weekday"] == 0
    )
    for bad in ({"weekday": 9}, {"time": "25:00"}, {"weekday": True}):
        with pytest.raises(vol.Invalid):
            clean_weekly(bad)


async def test_weekly_settings_and_send(
    hass: HomeAssistant, enable_custom_integrations
):
    calls = async_mock_service(hass, "persistent_notification", "create")
    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = get_coordinator(hass)
    await coord.async_set_settings(
        {
            **coord.settings,
            "weekly_report": {"enabled": True, "weekday": 6, "time": "18:00"},
        }
    )
    assert coord.settings["weekly_report"]["enabled"]
    coord.send_weekly(dt_util.now())
    await hass.async_block_till_done()
    assert any("This week" in str(c.data.get("message")) for c in calls)
    # The Kind of day sensor is there, Normal day with nothing set up.
    state = hass.states.get("sensor.kind_of_day")
    assert state is not None and state.state == "Normal day"


def test_habit_ideas_with_conditions():
    now = datetime(2026, 11, 14, 22, 0, tzinfo=_tz())
    events = [
        {
            "e": "light.porch",
            "on": True,
            "at": datetime(2026, 11, d, 18, 30, tzinfo=_tz()).isoformat(),
            "d": True,
            "h": d != 9,
        }
        for d in (2, 3, 4, 5, 6, 9)
    ]
    [idea] = suggest(events, now)
    assert idea["when_dark"] is True
    assert idea["someone_home"] is True  # 5 of 6
    events[0]["h"] = False
    [idea] = suggest(events, now)
    assert idea["someone_home"] is False
    for e in events:
        e.pop("d")
    assert suggest(events, now)[0]["when_dark"] is False


async def test_wait_for_cheaper_electricity(hass: HomeAssistant):
    calls = async_mock_service(hass, "switch", "turn_on")
    now = dt_util.now()
    start = now.replace(minute=0, second=0, microsecond=0)
    prices = [
        {"start": (start + timedelta(hours=h)).isoformat(), "value": p}
        for h, p in enumerate([0.30, 0.10, 0.20, 0.05])
    ]
    hass.states.async_set("sensor.price", "0.3", {"raw_today": prices})
    routine = make_routine(
        trigger="manual",
        skip_noop=False,
        actions=[
            {"wait_cheaper": {"entity_id": "sensor.price", "minutes": 90}},
            {
                "service": "switch.turn_on",
                "target": {"entity_id": ["switch.dishwasher"]},
            },
        ],
    )
    coord = await _started(hass, routine)
    seconds, note = coord._until_cheapest(routine["actions"][0]["wait_cheaper"])  # noqa: SLF001
    assert 0 < seconds <= 3600 and "cheapest" in note
    assert await coord.async_run("routine_one")
    assert len(calls) == 1
    assert "cheapest" in coord.get_history("routine_one")[0]["detail"]
    hass.states.async_set("sensor.price", "0.3", {})
    assert coord._until_cheapest({"entity_id": "sensor.price", "minutes": 60})[0] == 0  # noqa: SLF001
    assert step_label(hass, routine["actions"][0]) == (
        "Wait for the cheapest electricity in the next 1.5 hours"
    )
    with pytest.raises(vol.Invalid):
        make_routine(actions=[{"wait_cheaper": {"entity_id": ""}}])


async def test_status_carries_group(hass: HomeAssistant):
    coord = await _started(hass, make_routine(group="Bedtime"))
    await coord.async_pause_group("Bedtime", "2099-01-01")
    status = coord.get_status("routine_one")
    assert status["group"] == "Bedtime" and status["paused_until"] == "2099-01-01"
