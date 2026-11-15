"""1.25.0: run replays, tags and buttons, vacation, the daily summary,
"make it look like this", and waiting for a level."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.daily_routine import coordinator as coordinator_module
from custom_components.daily_routine.away import clean_summary, summary_text
from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.coordinator import HomeRoutinesCoordinator
from custom_components.daily_routine.progress import step_label
from custom_components.daily_routine.runtime import get_coordinator
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


def _light(service: str = "light.turn_off", entity: str = "light.hall"):
    return {"service": service, "target": {"entity_id": [entity]}, "data": {}}


async def _entry(
    hass: HomeAssistant, enable_custom_integrations
) -> HomeRoutinesCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return get_coordinator(hass)


# ----- replay -----


async def test_a_run_can_be_replayed_step_by_step(hass: HomeAssistant):
    async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.hall", "on", {"friendly_name": "Hall"})
    hass.states.async_set("light.den", "off")
    routine = make_routine(
        trigger="manual",
        skip_noop=True,
        actions=[
            _light(),
            _light(entity="light.den"),
            {"delay": 0},
            {
                **_light(),
                "only_if": {"kind": "state", "entity_id": "light.den", "state": "on"},
            },
        ],
    )
    coord = await _started(hass, routine)
    assert await coord.async_run("routine_one")
    steps = coord.get_history("routine_one")[0]["steps"]
    assert [s["result"] for s in steps] == ["ok", "skipped", "ok", "skipped"]
    assert steps[0]["label"].startswith("Turn off") and steps[0]["seconds"] >= 0
    assert steps[0]["devices"] == [
        {"entity_id": "light.hall", "name": "Hall", "state": "on"}
    ]
    assert steps[1]["note"] == "Already done"
    event = coord.run_log.events()[-1]
    assert event["outcome"] == "ran" and len(event["steps"]) == 4


async def test_a_failed_step_shows_in_the_replay(hass: HomeAssistant):
    async def broken(call):
        raise RuntimeError("no answer")

    hass.services.async_register("light", "turn_off", broken)
    hass.states.async_set("light.hall", "on")
    routine = make_routine(
        trigger="manual",
        skip_noop=False,
        retries=0,
        retry_later_minutes=0,
        actions=[_light(), _light()],
    )
    coord = await _started(hass, routine)
    assert not await coord.async_run("routine_one")
    steps = coord.get_history("routine_one")[0]["steps"]
    assert steps[0]["result"] == "failed" and steps[0]["note"]
    assert len(steps) == 1


# ----- tags and buttons -----


async def test_tag_and_button_triggers(hass: HomeAssistant):
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.hall", "on")
    hass.states.async_set(
        "event.pico", "2026-11-15T07:00:00+00:00", {"event_type": "single"}
    )
    tag = make_routine(id="tag", trigger="tag", watch_item="door-tag", skip_noop=False)
    button = make_routine(
        id="button",
        trigger="button",
        watch_entity="event.pico",
        watch_states=["double"],
        skip_noop=False,
    )
    coord = await _started(hass, tag, button)
    hass.bus.async_fire("tag_scanned", {"tag_id": "other"})
    hass.bus.async_fire("tag_scanned", {"tag_id": "door-tag"})
    await hass.async_block_till_done()
    assert len(calls) == 1
    # A single press isn't the double press it waits for.
    hass.states.async_set(
        "event.pico", "2026-11-15T07:01:00+00:00", {"event_type": "single"}
    )
    await hass.async_block_till_done()
    assert len(calls) == 1
    hass.states.async_set(
        "event.pico", "2026-11-15T07:02:00+00:00", {"event_type": "double"}
    )
    await hass.async_block_till_done()
    assert len(calls) == 2
    # Coming back from unavailable isn't a press.
    hass.states.async_set("event.pico", "unavailable")
    hass.states.async_set(
        "event.pico", "2026-11-15T07:03:00+00:00", {"event_type": "double"}
    )
    await hass.async_block_till_done()
    assert len(calls) == 2
    assert coord.get_status("button")["state"] != "running"


def test_tag_and_button_are_checked():
    with pytest.raises(vol.Invalid, match="tag"):
        make_routine(trigger="tag")
    with pytest.raises(vol.Invalid, match="button"):
        make_routine(trigger="button", watch_entity="light.x")
    routine = make_routine(trigger="button", watch_entity="event.pico")
    assert routine["watch_states"] == [] and routine["repeat"]


# ----- waiting for a level -----


async def test_wait_until_a_level_holds(hass: HomeAssistant, monkeypatch):
    monkeypatch.setattr(coordinator_module, "SECONDS_PER_MINUTE", 0.05)
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.hall", "on")
    hass.states.async_set("sensor.dishwasher_power", "120")
    wait = {
        "entity_id": "sensor.dishwasher_power",
        "below": 5,
        "for_minutes": 1,
        "minutes": 10,
        "on_timeout": "stop",
    }
    routine = make_routine(
        trigger="manual", skip_noop=False, actions=[{"wait_until": wait}, _light()]
    )
    assert routine["actions"][0]["wait_until"]["below"] == 5
    assert "under 5" in step_label(hass, routine["actions"][0])
    coord = await _started(hass, routine)
    task = hass.async_create_task(coord.async_run("routine_one"))
    await asyncio.sleep(0.01)
    hass.states.async_set("sensor.dishwasher_power", "2")
    await asyncio.sleep(0.01)
    hass.states.async_set("sensor.dishwasher_power", "50")  # not finished after all
    await asyncio.sleep(0.07)
    assert not calls
    hass.states.async_set("sensor.dishwasher_power", "1")
    assert await task
    assert len(calls) == 1
    with pytest.raises(vol.Invalid, match="or a level"):
        make_routine(actions=[{"wait_until": {"entity_id": "sensor.x"}}])


# ----- make it look like this -----


async def test_make_it_look_like_this_can_be_undone(hass: HomeAssistant):
    applied = async_mock_service(hass, "scene", "apply")
    hass.states.async_set("light.hall", "off")
    step = {
        "service": "scene.apply",
        "data": {"entities": {"light.hall": {"state": "on", "brightness": 120}}},
    }
    routine = make_routine(trigger="manual", skip_noop=False, actions=[step])
    assert step_label(hass, routine["actions"][0]) == "Set 1 device as saved"
    coord = await _started(hass, routine)
    assert await coord.async_run("routine_one")
    assert applied[0].data["entities"]["light.hall"]["brightness"] == 120
    assert "light.hall" in coord._undo["routine_one"]["states"]  # noqa: SLF001


# ----- vacation and the summary -----


def test_summary_text():
    day = dt_util.now().date()
    at = dt_util.now().isoformat()
    events = [
        {"at": at, "name": "Lights", "outcome": "ran"},
        {
            "at": at,
            "name": "Curtains",
            "outcome": "failed",
            "reason": "error:cover.close_cover",
        },
        {"at": at, "name": "Fan", "outcome": "skipped", "reason": "conditions_not_met"},
        {
            "at": (dt_util.now() - timedelta(days=2)).isoformat(),
            "name": "Old",
            "outcome": "ran",
        },
    ]
    words = {
        "error": "a device didn't change",
        "conditions_not_met": "its conditions weren't met",
    }
    text = summary_text(events, day, lambda r: words.get(r.split(":")[0], r))
    assert "1 ran, 1 failed, 1 skipped" in text
    assert "Curtains (a device didn't change)" in text
    assert "Fan (its conditions weren't met)" in text
    assert summary_text(events[:1], day, str) == "Today: all 1 ran. 👍"
    assert summary_text([], day, str) == "Nothing ran today."
    assert clean_summary(None)["enabled"] is False
    with pytest.raises(vol.Invalid):
        clean_summary({"time": "25:00"})
    with pytest.raises(vol.Invalid):
        clean_summary("x")


async def test_vacation_in_one_tap(
    hass: HomeAssistant, enable_custom_integrations, freezer
):
    freezer.move_to(
        datetime(2026, 11, 14, 12, 0, tzinfo=dt_util.get_default_time_zone())
    )
    coord = await _entry(hass, enable_custom_integrations)
    await coord.async_save_routine(
        {"id": "curtains", "name": "Curtains", "trigger": "time", "actions": [_light()]}
    )
    notes = async_mock_service(hass, "persistent_notification", "create")
    vacation = await coord.away.async_start(
        "2026-11-16", ["curtains"], lived_in=True, summary=True
    )
    assert vacation["until"] == "2026-11-16" and vacation["mode_was"] == "home"
    assert coord.mode == "vacation"
    assert coord.settings["lived_in"]["enabled"] is True
    assert coord.settings["daily_summary"]["enabled"] is True
    assert coord._find("curtains")["paused_until"] == "2026-11-16"  # noqa: SLF001
    with pytest.raises(vol.Invalid, match="passed"):
        await coord.away.async_start("2020-01-01", [], lived_in=True, summary=True)
    with pytest.raises(vol.Invalid, match="last day"):
        await coord.away.async_start("soon", [], lived_in=True, summary=True)
    # Changing the dates keeps what to put back.
    again = await coord.away.async_start(
        "2026-11-17", ["curtains"], lived_in=True, summary=True
    )
    assert again["mode_was"] == "home"
    # The summary goes out in the evening while away.
    freezer.move_to(
        datetime(2026, 11, 14, 21, 0, 0, tzinfo=dt_util.get_default_time_zone())
    )
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert any("summary" in str(n.data.get("notification_id")) for n in notes)
    # The day after the last day, it's all put back.
    freezer.move_to(
        datetime(2026, 11, 18, 0, 0, 30, tzinfo=dt_util.get_default_time_zone())
    )
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert coord.mode == "home"
    assert coord.settings["vacation"] is None
    assert coord.settings["lived_in"]["enabled"] is False
    assert coord.settings["daily_summary"]["enabled"] is False
    assert coord._find("curtains")["paused_until"] is None  # noqa: SLF001
    await coord.away.async_end()  # nothing to end: fine


async def test_back_home_early(
    hass: HomeAssistant, enable_custom_integrations, hass_ws_client
):
    coord = await _entry(hass, enable_custom_integrations)
    client = await hass_ws_client(hass)
    tomorrow = (dt_util.now().date() + timedelta(days=1)).isoformat()
    await client.send_json(
        {
            "id": 1,
            "type": f"{DOMAIN}/vacation/start",
            "until": tomorrow,
            "lived_in": False,
            "summary": False,
        }
    )
    assert (await client.receive_json())["success"]
    assert coord.settings["lived_in"]["enabled"] is False
    await coord.async_set_mode("night")  # someone changed it themselves
    await client.send_json({"id": 2, "type": f"{DOMAIN}/vacation/end"})
    assert (await client.receive_json())["success"]
    assert coord.mode == "night"  # left as they set it
    await client.send_json(
        {"id": 3, "type": f"{DOMAIN}/vacation/start", "until": "2020-01-01"}
    )
    assert not (await client.receive_json())["success"]
    await client.send_json({"id": 4, "type": f"{DOMAIN}/summary/send"})
    assert "Nothing ran today" in (await client.receive_json())["result"]["text"]
    # The summary's own settings save with the rest.
    await client.send_json(
        {
            "id": 5,
            "type": f"{DOMAIN}/settings/save",
            "settings": {
                **coord.settings,
                "daily_summary": {"enabled": True, "time": "20:30", "only_away": True},
            },
        }
    )
    saved = (await client.receive_json())["result"]["settings"]
    assert saved["daily_summary"] == {
        "enabled": True,
        "time": "20:30",
        "only_away": True,
    }
