"""1.26.0: ideas from habits, power per routine, rooms someone is in,
volume ramps, kinds of day, routine groups and integration names."""

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
from custom_components.daily_routine.coordinator import HomeRoutinesCoordinator
from custom_components.daily_routine.day_types import clean_day_types, day_type
from custom_components.daily_routine.habits import suggest
from custom_components.daily_routine.panel import entity_integrations
from custom_components.daily_routine.routines import NORMAL_DAY, normalize_routine
from custom_components.daily_routine.runtime import get_coordinator
from custom_components.daily_routine.schema import vol
from homeassistant.core import Context, HomeAssistant, SupportsResponse
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
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


# ----- ideas from habits -----


def _at(day: int, hour: int, minute: int) -> str:
    return datetime(
        2026, 11, day, hour, minute, tzinfo=dt_util.get_default_time_zone()
    ).isoformat()


def test_a_habit_becomes_an_idea():
    now = datetime(2026, 11, 14, 22, 0, tzinfo=dt_util.get_default_time_zone())
    # Porch light on around 6:40 pm on six weekdays (Nov 2-6 and 9 are Mon-Fri).
    events = [
        {"e": "light.porch", "on": True, "at": _at(d, 18, 30 + (d % 3) * 5)}
        for d in (2, 3, 4, 5, 6, 9)
    ]
    # Once at another time doesn't move it; two days of the den aren't enough.
    events.append({"e": "light.porch", "on": True, "at": _at(10, 7, 0)})
    events += [{"e": "light.den", "on": False, "at": _at(d, 23, 0)} for d in (9, 10)]
    ideas = suggest(events, now)
    assert len(ideas) == 1
    idea = ideas[0]
    assert idea["entity_id"] == "light.porch" and idea["on"] is True
    assert idea["time"] == "18:35:00" and idea["count"] == 6
    assert idea["days"] == ["mon", "tue", "wed", "thu", "fri"]
    # Covered by a routine, or dismissed: not offered.
    assert not suggest(events, now, covered={("light.porch", True)})
    assert not suggest(events, now, dismissed={idea["id"]})
    # Too long ago.
    assert not suggest(events, now + timedelta(days=30))


async def test_changes_by_hand_are_noted(hass: HomeAssistant):
    coord = await _started(hass)
    coord.habits.start()
    hass.states.async_set("light.porch", "off")
    hass.states.async_set("light.porch", "on", context=Context(user_id="someone"))
    # An automation's change (it has a parent) isn't a habit.
    hass.states.async_set("light.porch", "off", context=Context(parent_id="auto"))
    hass.states.async_set("sensor.temp", "20")
    hass.states.async_set("sensor.temp", "21", context=Context(user_id="someone"))
    await hass.async_block_till_done()
    assert [(e["e"], e["on"]) for e in coord.habits.events] == [("light.porch", True)]
    await coord.habits.async_dismiss("light.porch:on:18:35")
    assert "light.porch:on:18:35" in coord.habits.dismissed


async def test_habit_covered_by_a_routine(hass: HomeAssistant):
    coord = await _started(
        hass,
        make_routine(
            actions=[
                {"service": "light.turn_on", "target": {"entity_id": ["light.porch"]}}
            ]
        ),
    )
    assert ("light.porch", True) in coord.habits.covered()
    assert ("light.porch", False) not in coord.habits.covered()


# ----- power -----


async def test_power_before_and_after_a_run(hass: HomeAssistant):
    async_mock_service(hass, "light", "turn_off")
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("test", "lamp")}
    )
    ents = er.async_get(hass)
    ents.async_get_or_create(
        "light", "test", "lamp", device_id=device.id, suggested_object_id="test_lamp"
    )
    ents.async_get_or_create(
        "sensor",
        "test",
        "lamp_power",
        device_id=device.id,
        suggested_object_id="test_lamp_power",
    )
    hass.states.async_set("light.test_lamp", "on")
    hass.states.async_set(
        "sensor.test_lamp_power",
        "0.06",
        {"device_class": "power", "unit_of_measurement": "kW"},
    )
    routine = make_routine(
        trigger="manual",
        skip_noop=False,
        actions=[
            {"service": "light.turn_off", "target": {"entity_id": ["light.test_lamp"]}}
        ],
    )
    coord = await _started(hass, routine)

    async def off(call):
        hass.states.async_set(
            "sensor.test_lamp_power",
            "2",
            {"device_class": "power", "unit_of_measurement": "W"},
        )

    hass.services.async_register("light", "turn_off", off)
    assert await coord.async_run("routine_one")
    await hass.async_block_till_done()
    power = coord.get_status("routine_one")["power"]
    assert power["last"]["before"] == 60 and power["last"]["after"] == 2
    assert power["average_change"] == -58 and power["runs"] == 1


# ----- rooms someone is in -----


async def test_a_step_only_in_rooms_someone_is_in(hass: HomeAssistant):
    calls = async_mock_service(hass, "light", "turn_on")
    areas = ar.async_get(hass)
    den = areas.async_create("Den")
    hall = areas.async_create("Hall")
    attic = areas.async_create("Attic")
    ents = er.async_get(hass)
    for area, name in ((den, "den"), (hall, "hall"), (attic, "attic")):
        ents.async_get_or_create(
            "light", "test", name, suggested_object_id=f"test_{name}"
        )
        ents.async_update_entity(f"light.test_{name}", area_id=area.id)
        hass.states.async_set(f"light.test_{name}", "off")
    for area, name, state in ((den, "den", "on"), (hall, "hall", "off")):
        ents.async_get_or_create(
            "binary_sensor",
            "test",
            f"{name}_motion",
            suggested_object_id=f"test_{name}_motion",
        )
        ents.async_update_entity(f"binary_sensor.test_{name}_motion", area_id=area.id)
        hass.states.async_set(
            f"binary_sensor.test_{name}_motion", state, {"device_class": "motion"}
        )
    step = {
        "service": "light.turn_on",
        "target": {"area_id": [den.id, hall.id, attic.id]},
        "rooms": "occupied",
    }
    coord = await _started(
        hass, make_routine(trigger="manual", skip_noop=False, actions=[step])
    )
    assert coord.room_occupied(den.id) is True
    assert coord.room_occupied(hall.id) is False
    assert coord.room_occupied(attic.id) is None  # no sensors: left alone
    assert await coord.async_run("routine_one")
    assert [c.data["area_id"] for c in calls] == [[den.id]]

    empty = make_routine(
        id="empty",
        trigger="manual",
        skip_noop=False,
        actions=[{**step, "rooms": "empty"}],
    )
    await coord.async_save_routine(empty)
    calls.clear()
    assert await coord.async_run("empty")
    assert [c.data["area_id"] for c in calls] == [[hall.id]]

    # Nobody anywhere: the step is skipped, not failed.
    hass.states.async_set(
        "binary_sensor.test_den_motion", "off", {"device_class": "motion"}
    )
    calls.clear()
    assert await coord.async_run("routine_one")
    assert not calls
    assert "no room was someone is in" in coord.get_history("routine_one")[0]["detail"]


def test_rooms_need_rooms():
    with pytest.raises(vol.Invalid):
        make_routine(
            actions=[
                {
                    "service": "light.turn_on",
                    "target": {"entity_id": ["light.a"]},
                    "rooms": "occupied",
                }
            ]
        )
    with pytest.raises(vol.Invalid):
        make_routine(
            actions=[
                {
                    "service": "light.turn_on",
                    "target": {"area_id": ["den"]},
                    "rooms": "sometimes",
                }
            ]
        )


# ----- volume ramp -----


async def test_volume_ramp(hass: HomeAssistant):
    calls = async_mock_service(hass, "media_player", "volume_set")
    hass.states.async_set("media_player.bedroom", "playing")
    routine = make_routine(
        trigger="manual",
        actions=[
            {
                "volume_ramp": {
                    "entity_id": ["media_player.bedroom"],
                    "from": 10,
                    "to": 30,
                    "minutes": 1,
                }
            }
        ],
    )
    coord = await _started(hass, routine)
    assert await coord.async_run("routine_one")
    levels = [c.data["volume_level"] for c in calls]
    assert levels[0] == 0.1 and levels[-1] == 0.3
    assert len(levels) == coordinator_module.RAMP_STEPS + 1
    assert levels == sorted(levels)


def test_volume_ramp_needs_speakers():
    with pytest.raises(vol.Invalid):
        make_routine(actions=[{"volume_ramp": {"entity_id": ["light.a"]}}])
    ramp = make_routine(actions=[{"volume_ramp": {"entity_id": "media_player.a"}}])[
        "actions"
    ][0]["volume_ramp"]
    assert ramp == {"entity_id": ["media_player.a"], "from": 0, "to": 30, "minutes": 10}


# ----- kinds of day -----


def test_day_types_are_checked():
    kinds = clean_day_types(
        [
            {
                "name": "Holiday",
                "calendar": "calendar.family",
                "words": "no school, holiday",
            },
            {"name": "School day", "calendar": "calendar.school", "words": []},
        ]
    )
    assert kinds[0]["words"] == ["no school", "holiday"]
    titles = {"calendar.family": ["Dentist"], "calendar.school": ["Classes"]}
    assert day_type(kinds, titles) == "School day"
    titles["calendar.family"] = ["NO SCHOOL - teacher day"]
    assert day_type(kinds, titles) == "Holiday"
    assert day_type(kinds, {"calendar.family": [], "calendar.school": []}) == NORMAL_DAY
    assert day_type(kinds, {"calendar.family": None, "calendar.school": []}) is None
    for bad in (
        [{"name": "", "calendar": "calendar.a"}],
        [{"name": "A", "calendar": "light.a"}],
        [{"name": NORMAL_DAY, "calendar": "calendar.a"}],
        [
            {"name": "A", "calendar": "calendar.a"},
            {"name": "a", "calendar": "calendar.b"},
        ],
    ):
        with pytest.raises(vol.Invalid):
            clean_day_types(bad)


async def test_day_type_condition(hass: HomeAssistant, enable_custom_integrations):
    hass.states.async_set("calendar.school", "off")
    events: list[dict] = [{"summary": "No school"}]

    async def get_events(call):
        return {"calendar.school": {"events": list(events)}}

    hass.services.async_register(
        "calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY
    )
    async_mock_service(hass, "light", "turn_off")
    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = get_coordinator(hass)
    await coord.async_set_settings(
        {
            **coord.settings,
            "day_types": [
                {
                    "name": "Day off",
                    "calendar": "calendar.school",
                    "words": ["no school"],
                }
            ],
        }
    )
    await hass.async_block_till_done()
    assert coord.day_type(dt_util.now()) == "Day off"
    await coord.async_save_routine(
        make_routine(
            id="wake",
            trigger="manual",
            skip_noop=False,
            conditions=[{"kind": "day_type", "types": [NORMAL_DAY]}],
        )
    )
    assert await coord.async_run("wake") is False
    assert coord.get_status("wake")["block_reason"] == "day_type_not_met"
    events.clear()
    await coord._refresh_calendars()  # noqa: SLF001
    assert coord.day_type(dt_util.now()) == NORMAL_DAY
    assert await coord.async_run("wake") is True


# ----- groups -----


async def test_groups(hass: HomeAssistant):
    a = make_routine(id="a", group=" Bedtime  ")
    b = make_routine(id="b", group="Bedtime")
    c = make_routine(id="c")
    assert a["group"] == "Bedtime" and c["group"] == ""
    coord = await _started(hass, a, b, c)
    assert await coord.async_pause_group("Bedtime", "2026-12-01") == ["a", "b"]
    assert [r["paused_until"] for r in coord.routines] == [
        "2026-12-01",
        "2026-12-01",
        None,
    ]
    await coord.async_pause_group("Bedtime", None)
    assert all(r["paused_until"] is None for r in coord.routines)
    with pytest.raises(vol.Invalid):
        await coord.async_pause_group("Nope", None)


# ----- integration names -----


async def test_integration_names(hass: HomeAssistant):
    ents = er.async_get(hass)
    entry = ents.async_get_or_create("light", "nonexistent_integration_xyz", "2")
    names = entity_integrations(hass)
    assert names[entry.entity_id] == "nonexistent_integration_xyz"


def test_routine_keeps_new_fields():
    raw = make_routine(group="Mornings")
    assert normalize_routine(raw)["group"] == "Mornings"


async def test_new_steps_in_words_and_text(hass: HomeAssistant):
    from custom_components.daily_routine.progress import step_label
    from custom_components.daily_routine.text_format import (
        conditions_to_text,
        parse_conditions_text,
        parse_steps_text,
        steps_to_text,
    )

    hass.states.async_set(
        "media_player.bedroom", "playing", {"friendly_name": "Bedroom"}
    )
    routine = make_routine(
        actions=[
            {"volume_ramp": {"entity_id": ["media_player.bedroom"], "to": 25}},
            {
                "service": "light.turn_on",
                "target": {"area_id": ["den"]},
                "rooms": "empty",
            },
        ],
        conditions=[{"kind": "day_type", "types": ["School day"]}],
    )
    assert step_label(hass, routine["actions"][0]) == "Turn Bedroom from 0% to 25%"
    steps = parse_steps_text(steps_to_text(routine["actions"]))
    conditions = parse_conditions_text(conditions_to_text(routine["conditions"]))
    again = make_routine(actions=steps, conditions=conditions)
    assert again["actions"] == routine["actions"]
    assert again["conditions"] == routine["conditions"]
