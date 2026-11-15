"""1.29.0: the leaving-home checklist, quiet hours for speakers, guest Wi-Fi,
a routine's day-by-day history and the dry run of tomorrow."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.daily_routine import coordinator as coordinator_module
from custom_components.daily_routine.checklist import checklist_text, left_open
from custom_components.daily_routine.coordinator import HomeRoutinesCoordinator
from custom_components.daily_routine.guests import clean_guest_wifi, guest_card
from custom_components.daily_routine.quiet_hours import clean_quiet, in_quiet, is_loud
from custom_components.daily_routine.run_log import daily_counts
from custom_components.daily_routine.schema import vol
from custom_components.daily_routine.timeline import tomorrow
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


def test_left_open_words():
    found = left_open(
        {
            "binary_sensor.back_door": ("Back door", "on"),
            "binary_sensor.window": ("Window", "off"),
            "lock.front": ("Front door", "unlocked"),
            "cover.garage": ("Garage", "closed"),
            "binary_sensor.shed": ("Shed", "unavailable"),
            "binary_sensor.gone": None,
        }
    )
    assert found == [
        ("Back door", "open"),
        ("Front door", "unlocked"),
        ("Shed", "not reporting"),
        ("binary_sensor.gone", "not reporting"),
    ]
    assert checklist_text(found[:2]) == "Still open: Back door, Front door (unlocked)."


async def test_check_open_step(hass: HomeAssistant):
    created = async_mock_service(hass, "persistent_notification", "create")
    hass.states.async_set(
        "binary_sensor.back_door", "on", {"friendly_name": "Back door"}
    )
    hass.states.async_set("lock.front", "locked", {"friendly_name": "Front door"})
    step = {"check_open": {"entity_id": ["binary_sensor.back_door", "lock.front"]}}
    routine = make_routine(trigger="manual", actions=[step])
    assert routine["actions"][0]["check_open"]["stop"] is False
    coord = await _started(hass, routine)
    assert await coord.async_run("routine_one")
    await hass.async_block_till_done()
    assert created and created[-1].data["message"] == "Still open: Back door."

    # All shut: nothing is sent.
    hass.states.async_set(
        "binary_sensor.back_door", "off", {"friendly_name": "Back door"}
    )
    count = len(created)
    assert await coord.async_run("routine_one")
    await hass.async_block_till_done()
    assert not [c for c in created[count:] if "Still open" in c.data["message"]]

    with pytest.raises(vol.Invalid):
        make_routine(actions=[{"check_open": {"entity_id": ["sensor.temp"]}}])
    with pytest.raises(vol.Invalid):
        make_routine(actions=[{"check_open": {"entity_id": []}}])


def test_quiet_hours_rules():
    quiet = clean_quiet({"enabled": True, "from": "22:00", "until": "07:00"})
    at = datetime(2026, 11, 15, 23, 0)
    assert in_quiet(quiet, at)
    assert in_quiet(quiet, at.replace(hour=6, minute=59))
    assert not in_quiet(quiet, at.replace(hour=7))
    assert not in_quiet({**quiet, "enabled": False}, at)
    day = clean_quiet({"enabled": True, "from": "13:00", "until": "15:00"})
    assert in_quiet(day, at.replace(hour=14)) and not in_quiet(day, at)
    with pytest.raises(vol.Invalid):
        clean_quiet({"from": "22:00", "until": "22:00"})
    assert is_loud({"service": "tts.speak"})
    assert is_loud({"service": "media_player.play_media"})
    assert is_loud({"volume_ramp": {}}) and is_loud({"briefing": {}})
    assert not is_loud({"service": "media_player.turn_off"})
    assert not is_loud(
        {"service": "media_player.volume_set", "data": {"volume_level": 0}}
    )
    assert not is_loud({"service": "light.turn_on"})


async def test_quiet_hours_skip_speaker_steps(hass: HomeAssistant):
    speak = async_mock_service(hass, "tts", "speak")
    lights = async_mock_service(hass, "light", "turn_on")
    actions = [
        {
            "service": "tts.speak",
            "target": {"entity_id": ["tts.piper"]},
            "data": {"message": "Hi"},
        },
        {
            "service": "light.turn_on",
            "target": {"entity_id": ["light.hall"]},
            "data": {},
        },
    ]
    routine = make_routine(trigger="manual", actions=actions)
    coord = await _started(hass, routine)
    now = dt_util.now()
    start = (now - timedelta(minutes=5)).strftime("%H:%M")
    end = (now + timedelta(minutes=30)).strftime("%H:%M")
    coord.settings["quiet_hours"] = {"enabled": True, "from": start, "until": end}
    assert await coord.async_run("routine_one")
    assert not speak and len(lights) == 1

    coord.routines = [make_routine(trigger="manual", actions=actions, loud_ok=True)]
    assert await coord.async_run("routine_one")
    assert len(speak) == 1


def test_guest_wifi():
    wifi = clean_guest_wifi({"ssid": " Guests ", "password": "welcome123"})
    assert wifi == {"ssid": "Guests", "password": "welcome123"}
    assert clean_guest_wifi({"ssid": "", "password": ""}) is None
    with pytest.raises(vol.Invalid):
        clean_guest_wifi({"ssid": "", "password": "x"})
    assert guest_card({"guest_wifi": wifi}, "guests") == wifi
    assert guest_card({"guest_wifi": wifi}, "home") is None


def test_daily_counts():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=dt_util.get_default_time_zone())
    events = [
        {"at": now.isoformat(), "routine_id": "a", "outcome": "ran"},
        {"at": now.isoformat(), "routine_id": "a", "outcome": "skipped"},
        {
            "at": (now - timedelta(days=2)).isoformat(),
            "routine_id": "a",
            "outcome": "failed",
        },
        {"at": now.isoformat(), "routine_id": "b", "outcome": "ran"},
        {
            "at": (now - timedelta(days=40)).isoformat(),
            "routine_id": "a",
            "outcome": "ran",
        },
    ]
    days = daily_counts(events, now, 7, "a")
    assert len(days) == 7 and days[-1]["day"] == "2026-11-15"
    assert days[-1] == {"day": "2026-11-15", "ran": 1, "failed": 0, "skipped": 1}
    assert days[-3]["failed"] == 1 and days[0]["ran"] == 0
    assert daily_counts(events, now, 7)[-1]["ran"] == 2


async def test_tomorrow_dry_run(hass: HomeAssistant):
    morning = make_routine(id="morning", name="Morning", time="07:00:00")
    evening = make_routine(
        id="evening",
        name="Evening",
        time="21:30:00",
        actions=[
            {
                "service": "light.turn_on",
                "target": {"entity_id": ["light.hall"]},
                "data": {},
            },
            {"delay": 60},
        ],
    )
    manual = make_routine(id="by_hand", name="By hand", trigger="manual")
    coord = await _started(hass, evening, morning, manual)
    plan = tomorrow(coord, dt_util.now())
    expected = (dt_util.now() + timedelta(days=1)).date().isoformat()
    assert plan["date"] == expected
    assert [r["name"] for r in plan["runs"]] == ["Morning", "Evening"]
    assert all(r["at"].startswith(expected) for r in plan["runs"])
    assert len(plan["runs"][1]["steps"]) == 2
    assert plan["watching"] == []


async def test_settings_and_commands(
    hass: HomeAssistant, enable_custom_integrations, hass_ws_client
):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.daily_routine.const import DOMAIN
    from custom_components.daily_routine.runtime import get_coordinator

    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = get_coordinator(hass)
    assert coord is not None
    assert coord.settings["quiet_hours"]["enabled"] is False
    await coord.async_set_settings(
        {
            "quiet_hours": {"enabled": True, "from": "21:30", "until": "06:45"},
            "guest_wifi": {"ssid": "Visitors", "password": "hello1234"},
        }
    )
    assert coord.settings["quiet_hours"]["from"] == "21:30"
    with pytest.raises(vol.Invalid):
        await coord.async_set_settings({"guest_wifi": {"ssid": "x" * 40}})
    await coord.async_save_routine(
        {
            "id": "r",
            "name": "R",
            "trigger": "time",
            "time": "08:00:00",
            "actions": [
                {"service": "light.turn_on", "target": {"entity_id": ["light.hall"]}}
            ],
        }
    )
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/plan/tomorrow"})
    plan = (await client.receive_json())["result"]
    assert [r["routine_id"] for r in plan["runs"]] == ["r"]
    assert plan["runs"][0]["steps"]
    await client.send_json(
        {"id": 2, "type": f"{DOMAIN}/routine/history", "routine_id": "r", "days": 7}
    )
    assert len((await client.receive_json())["result"]["days"]) == 7
    await client.send_json({"id": 3, "type": f"{DOMAIN}/overview"})
    assert (await client.receive_json())["result"]["guest_wifi"] is None
    await coord.async_set_mode("guests")
    await client.send_json({"id": 4, "type": f"{DOMAIN}/overview"})
    assert (await client.receive_json())["result"]["guest_wifi"]["ssid"] == "Visitors"
