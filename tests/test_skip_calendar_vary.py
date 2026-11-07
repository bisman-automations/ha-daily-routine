"""Skip the next run, pause until a date, calendar days and varied times."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.coordinator import random_shift
from custom_components.daily_routine.routines import calendar_matches
from custom_components.daily_routine.runtime import get_coordinator
from custom_components.daily_routine.schema import vol
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.util import dt as dt_util

from .conftest import make_routine

CAL = "calendar.school"


def _at_utc(local_iso: str) -> datetime:
    return dt_util.as_utc(
        datetime.fromisoformat(local_iso).replace(
            tzinfo=dt_util.get_default_time_zone()
        )
    )


async def _setup(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, get_coordinator(hass)


async def _done(hass: HomeAssistant, entry) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# ----- validation -----


def test_new_fields_default_and_validate() -> None:
    routine = make_routine()
    assert routine["random_minutes"] == 0
    assert routine["paused_until"] is None
    assert routine["skip_next"] is False
    routine = make_routine(random_minutes=20, paused_until="2026-11-14", skip_next=True)
    assert (
        routine["random_minutes"],
        routine["paused_until"],
        routine["skip_next"],
    ) == (
        20,
        "2026-11-14",
        True,
    )
    with pytest.raises(vol.Invalid):
        make_routine(random_minutes=500)
    with pytest.raises(vol.Invalid):
        make_routine(paused_until="next week")


def test_calendar_condition_validates_and_matches() -> None:
    routine = make_routine(
        conditions=[{"kind": "calendar", "entity_id": CAL, "match": " No  school "}]
    )
    cond = routine["conditions"][0]
    assert cond == {
        "kind": "calendar",
        "entity_id": CAL,
        "when": "no_event",
        "match": "No school",
    }
    assert calendar_matches(cond, ["Swim lesson"]) is True
    assert calendar_matches(cond, ["NO SCHOOL – teacher day"]) is False
    any_event = {**cond, "when": "event", "match": ""}
    assert calendar_matches(any_event, []) is False
    assert calendar_matches(any_event, ["Anything"]) is True
    for bad in (
        {"kind": "calendar", "entity_id": "light.hall"},
        {"kind": "calendar", "entity_id": CAL, "when": "sometimes"},
    ):
        with pytest.raises(vol.Invalid):
            make_routine(conditions=[bad])


def test_random_shift_is_steady_and_bounded() -> None:
    shifts = {random_shift("lights", f"2026-11-{d:02d}", 15) for d in range(1, 29)}
    assert all(timedelta(minutes=-15) <= s <= timedelta(minutes=15) for s in shifts)
    assert len(shifts) > 5, "different days get different times"
    assert random_shift("lights", "2026-11-03", 15) == random_shift(
        "lights", "2026-11-03", 15
    )
    assert random_shift("lights", "2026-11-03", 0) == timedelta(0)


# ----- running -----


async def test_varied_time_moves_the_next_run(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, freezer
) -> None:
    freezer.move_to(_at_utc("2026-11-10T12:00:00"))
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(
            id="away_lights", name="Away lights", time="19:00", random_minutes=30
        )
    )
    status = coordinator.get_status("away_lights")
    next_run = datetime.fromisoformat(status["next_run"])
    base = datetime.fromisoformat("2026-11-10T19:00:00").replace(
        tzinfo=dt_util.get_default_time_zone()
    )
    assert next_run == base + random_shift("away_lights", "2026-11-10", 30)
    await _done(hass, entry)


async def test_skip_next_skips_once(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, freezer
) -> None:
    calls = async_mock_service(hass, "light", "turn_off")
    freezer.move_to(_at_utc("2026-11-10T06:59:00"))
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(make_routine())
    await coordinator.async_skip_next("routine_one")
    assert coordinator.get_routine("routine_one")["skip_next"] is True

    freezer.move_to(_at_utc("2026-11-10T07:00:01"))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls == []
    assert coordinator.get_routine("routine_one")["skip_next"] is False
    assert coordinator.get_history("routine_one")[0]["detail"] == "skipped_once"
    # Saved, so a restart doesn't skip again.
    stored = hass_storage[f"{DOMAIN}.routines"]["data"]["routines"][0]
    assert stored["skip_next"] is False

    freezer.move_to(_at_utc("2026-11-11T07:00:01"))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls) == 1
    await _done(hass, entry)


async def test_pause_until_skips_scheduled_runs_only(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, freezer
) -> None:
    calls = async_mock_service(hass, "light", "turn_off")
    freezer.move_to(_at_utc("2026-11-10T06:59:00"))
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(make_routine())
    await hass.services.async_call(
        DOMAIN, "pause_routines", {"until": "2026-11-11"}, blocking=True
    )
    assert coordinator.get_routine("routine_one")["paused_until"] == "2026-11-11"

    for day in ("2026-11-10", "2026-11-11"):
        freezer.move_to(_at_utc(f"{day}T07:00:01"))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert calls == []
    assert coordinator.get_history("routine_one")[0]["detail"] == "paused_until"

    # Run now still works while paused.
    assert await coordinator.async_run("routine_one") is True

    freezer.move_to(_at_utc("2026-11-12T07:00:01"))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls) == 2

    await hass.services.async_call(
        DOMAIN, "pause_routines", {"routine_ids": ["routine_one"]}, blocking=True
    )
    assert coordinator.get_routine("routine_one")["paused_until"] is None
    with pytest.raises(Exception, match="No routine"):
        await coordinator.async_pause_until(["nope"], "2026-12-01")
    await _done(hass, entry)


async def test_calendar_day_off(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set(CAL, "off", {"friendly_name": "School"})
    events: list[dict] = []

    async def get_events(call):
        return {CAL: {"events": list(events)}}

    hass.services.async_register(
        "calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY
    )
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(
            id="school_lights",
            name="School lights",
            conditions=[{"kind": "calendar", "entity_id": CAL, "match": "No school"}],
        )
    )
    await hass.async_block_till_done()
    assert await coordinator.async_run("school_lights") is True

    # A "No school" day starts: the calendar turns on, and it's read again.
    events.append({"summary": "No school"})
    hass.states.async_set(CAL, "on", {"friendly_name": "School"})
    await hass.async_block_till_done()
    assert await coordinator.async_run("school_lights") is False
    assert coordinator.get_status("school_lights")["block_reason"] == "calendar_not_met"

    # A calendar that can't be read fails closed.
    hass.states.async_set(CAL, "unavailable")
    await hass.async_block_till_done()
    events.clear()
    assert await coordinator.async_run("school_lights") is False
    assert len(calls) == 1
    await _done(hass, entry)


async def test_panel_commands(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, hass_ws_client
) -> None:
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(make_routine())
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/routine/skip_next", "routine_id": "routine_one"}
    )
    assert (await client.receive_json())["success"]
    assert coordinator.get_routine("routine_one")["skip_next"] is True
    await client.send_json_auto_id(
        {
            "type": f"{DOMAIN}/routines/pause_until",
            "routine_ids": ["routine_one"],
            "until": "2030-01-02",
        }
    )
    assert (await client.receive_json())["success"]
    await client.send_json_auto_id(
        {
            "type": f"{DOMAIN}/routines/pause_until",
            "routine_ids": ["routine_one"],
            "until": "soon",
        }
    )
    res = await client.receive_json()
    assert not res["success"]
    assert coordinator.get_routine("routine_one")["paused_until"] == "2030-01-02"
    await client.send_json_auto_id({"type": f"{DOMAIN}/overview"})
    overview = (await client.receive_json())["result"]
    assert overview["routines"][0]["skip_next"] is True
    await _done(hass, entry)


def test_calendar_condition_as_text() -> None:
    from custom_components.daily_routine.text_format import (
        conditions_to_text,
        parse_conditions_text,
    )

    conds = make_routine(
        conditions=[
            {"kind": "calendar", "entity_id": CAL, "match": "No school"},
            {"kind": "calendar", "entity_id": CAL, "when": "event"},
        ]
    )["conditions"]
    text = conditions_to_text(conds)
    assert (
        text
        == "calendar no_event | calendar.school | No school\ncalendar event | calendar.school"
    )
    assert make_routine(conditions=parse_conditions_text(text))["conditions"] == conds
