"""Full routine editor in Settings, plain-text steps, per-person triggers, export/import and the status poll."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.daily_routine.const import DOMAIN, ROUTINE_SUBENTRY
from custom_components.daily_routine.routines import normalize_routine
from custom_components.daily_routine.schema import vol
from custom_components.daily_routine.text_format import (
    conditions_to_text,
    parse_conditions_text,
    parse_steps_text,
    steps_to_text,
)

from .conftest import make_routine

# ----- plain-text steps and conditions -----


def test_steps_round_trip_through_text() -> None:
    routine = make_routine(
        actions=[
            {
                "service": "light.turn_off",
                "target": {"entity_id": ["light.hall", "light.porch"]},
                "data": {},
            },
            {"delay": 30.0},
            {"service": "cover.open_cover", "target": {"area_id": ["living_room"]}, "data": {}},
            {
                "service": "light.turn_on",
                "target": {"entity_id": ["light.kitchen"]},
                "data": {"brightness": 128},
            },
            {"service": "scene.turn_on", "data": {}},
        ]
    )
    text = steps_to_text(routine["actions"])
    assert text.splitlines() == [
        "light.turn_off | light.hall, light.porch",
        "wait 30",
        "cover.open_cover | area:living_room",
        'light.turn_on | light.kitchen | {"brightness": 128}',
        "scene.turn_on",
    ]
    again = normalize_routine({**routine, "actions": parse_steps_text(text)})
    assert again["actions"] == routine["actions"]


def test_step_lines_accept_wait_in_clock_form_and_skip_comments() -> None:
    actions = parse_steps_text("# bedtime\n\nwait 00:01:30\nlight.turn_off | light.hall\n")
    assert actions == [
        {"delay": "00:01:30"},
        {"service": "light.turn_off", "target": {"entity_id": ["light.hall"]}},
    ]


def test_bad_step_line_names_the_line() -> None:
    with pytest.raises(vol.Invalid, match="Line 2"):
        parse_steps_text("light.turn_on\n | light.hall")


def test_step_extra_settings_must_be_a_json_object() -> None:
    with pytest.raises(vol.Invalid, match="JSON object"):
        parse_steps_text("light.turn_on | light.hall | [1, 2]")


def test_conditions_round_trip_through_text() -> None:
    conditions = [
        {"entity_id": "lock.front_door", "state": "unlocked"},
        {"entity_id": "input_boolean.guest", "state": "off"},
    ]
    assert parse_conditions_text(conditions_to_text(conditions)) == conditions


def test_condition_line_without_equals_is_rejected() -> None:
    with pytest.raises(vol.Invalid, match="entity = state"):
        parse_conditions_text("lock.front_door unlocked")


# ----- the full editor in Settings -----


async def _setup(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, hass.data[DOMAIN]["coordinator"]


def _sub(entry, routine_id: str):
    return next(
        s
        for s in entry.subentries.values()
        if s.subentry_type == ROUTINE_SUBENTRY and s.unique_id == routine_id
    )


async def test_settings_editor_saves_people_sensors_conditions_and_options(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass.states.async_set("person.sam", "home", {"source": "device_tracker.sam_phone"})
    hass.states.async_set("binary_sensor.hall_motion", "off", {"device_class": "motion"})
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(make_routine(id="kitchen", name="Kitchen lights"))
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ROUTINE_SUBENTRY),
        context={"source": "reconfigure", "subentry_id": _sub(entry, "kitchen").subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Kitchen lights",
            "enabled": True,
            "trigger": "room_clear",
            "time": "07:00",
            "zone": "zone.home",
            "days": ["mon", "tue"],
            "people": ["person.sam"],
            "require_everyone_away": True,
            "sensors": ["binary_sensor.hall_motion"],
            "require_sensors_clear": True,
            "clear_minutes": 10,
            "active_from": "18:00",
            "active_to": "23:00",
            "conditions_match": "any",
            "require_states": "input_boolean.guest = off\nlock.front_door = locked",
            "catch_up_minutes": 30,
            "retries": 2,
            "repeat": True,
            "skip_noop": False,
            "notify_on_run": True,
            "notify_on_skip": False,
            "notify_on_error": True,
            "actions": "light.turn_off | light.hall",
        },
    )
    assert result["type"] == "abort"
    assert result["reason"] == "updated"
    await hass.async_block_till_done()

    routine = coordinator.get_routine("kitchen")
    assert routine is not None
    assert routine["days"] == ["mon", "tue"]
    assert routine["people"] == ["person.sam"]
    assert routine["sensors"] == ["binary_sensor.hall_motion"]
    assert routine["clear_minutes"] == 10
    assert routine["active_from"] == "18:00:00"
    assert routine["active_to"] == "23:00:00"
    assert routine["conditions_match"] == "any"
    assert routine["require_states"] == [
        {"entity_id": "input_boolean.guest", "state": "off"},
        {"entity_id": "lock.front_door", "state": "locked"},
    ]
    assert routine["catch_up_minutes"] == 30
    assert routine["retries"] == 2
    assert routine["repeat"] is True
    assert routine["skip_noop"] is False
    assert routine["notify_on_run"] is True
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_settings_editor_reports_a_bad_condition_line(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(make_routine(id="kitchen", name="Kitchen lights"))
    await hass.async_block_till_done()
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ROUTINE_SUBENTRY),
        context={"source": "reconfigure", "subentry_id": _sub(entry, "kitchen").subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Kitchen lights",
            "enabled": True,
            "trigger": "time",
            "time": "06:30",
            "zone": "zone.home",
            "require_states": "lock.front_door unlocked",
            "actions": "light.turn_off | light.hall",
        },
    )
    assert result["type"] == "form"
    assert result["errors"] == {"require_states": "invalid_conditions"}
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# ----- per-person leave and arrive triggers -----


def _person_routine(trigger: str, **overrides):
    raw = {
        "id": "sam_leaves",
        "name": "Sam leaves work",
        "trigger": trigger,
        "zone": "zone.work",
        "people": ["person.sam"],
        "actions": [
            {"service": "light.turn_off", "target": {"entity_id": ["light.hall"]}, "data": {}}
        ],
    }
    raw.update(overrides)
    return raw


async def test_one_person_leaving_a_place_runs_even_when_others_stay(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("zone.work", "0", {"friendly_name": "Work"})
    hass.states.async_set("person.sam", "Work", {"source": "device_tracker.sam_phone"})
    hass.states.async_set("person.riley", "home", {"source": "device_tracker.riley_phone"})
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        _person_routine("person_leaves", people=["person.sam", "person.riley"])
    )
    await hass.async_block_till_done()

    hass.states.async_set("person.riley", "Work", {"source": "device_tracker.riley_phone"})
    await hass.async_block_till_done()
    assert calls == []  # arriving at work is not leaving work

    hass.states.async_set("person.riley", "home", {"source": "device_tracker.riley_phone"})
    await hass.async_block_till_done()
    assert len(calls) == 1  # Riley left work; Sam was not involved

    hass.states.async_set("person.sam", "home", {"source": "device_tracker.sam_phone"})
    await hass.async_block_till_done()
    assert len(calls) == 2  # Sam was at work, so going home is a leave

    hass.states.async_set("person.sam", "Work", {"source": "device_tracker.sam_phone"})
    await hass.async_block_till_done()
    assert len(calls) == 2  # arriving at work does not run it
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_arriving_at_a_place_runs_for_the_chosen_person_only(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    calls = async_mock_service(hass, "light", "turn_on")
    hass.states.async_set("zone.work", "0", {"friendly_name": "Work"})
    hass.states.async_set("person.sam", "home", {"source": "device_tracker.sam_phone"})
    hass.states.async_set("person.riley", "home", {"source": "device_tracker.riley_phone"})
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        _person_routine(
            "person_arrives",
            id="sam_arrives",
            actions=[
                {"service": "light.turn_on", "target": {"entity_id": ["light.hall"]}, "data": {}}
            ],
        )
    )
    await hass.async_block_till_done()

    hass.states.async_set("person.riley", "Work", {"source": "device_tracker.riley_phone"})
    await hass.async_block_till_done()
    assert calls == []  # not a person on this routine

    hass.states.async_set("person.sam", "Work", {"source": "device_tracker.sam_phone"})
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_an_unavailable_person_does_not_trigger_a_leave(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("zone.work", "0", {"friendly_name": "Work"})
    hass.states.async_set("person.sam", "Work", {"source": "device_tracker.sam_phone"})
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(_person_routine("person_leaves"))
    await hass.async_block_till_done()

    hass.states.async_set("person.sam", "unavailable", {"source": "device_tracker.sam_phone"})
    await hass.async_block_till_done()
    assert calls == []
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def test_per_person_triggers_need_a_person() -> None:
    with pytest.raises(vol.Invalid, match="at least one person"):
        normalize_routine(_person_routine("person_leaves", people=[]))


# ----- export and import -----


async def test_export_leaves_out_turn_off_routines_and_import_restores_them(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass.states.async_set(
        "sensor.hall_humidity", "40", {"unit_of_measurement": "%", "device_class": "humidity"}
    )
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(
            id="fan",
            name="Fan",
            trigger="value_above",
            value_entity="sensor.hall_humidity",
            value_threshold=60,
            off_threshold=50,
            actions=[{"service": "fan.turn_on", "target": {"entity_id": ["fan.hall"]}, "data": {}}],
        )
    )
    await hass.async_block_till_done()
    exported = coordinator.export_routines()
    assert [r["id"] for r in exported] == ["fan"]

    await coordinator.async_delete_routine("fan")
    await hass.async_block_till_done()
    result = await coordinator.async_import_routines(exported)
    assert result == {"imported": 1, "problems": []}
    await hass.async_block_till_done()
    restored = coordinator.get_routine("fan")
    assert restored is not None and restored["off_threshold"] == 50.0
    assert coordinator.get_routine("fan_off") is not None  # rebuilt from its parent
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_import_reports_bad_routines_and_keeps_the_rest(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    entry, coordinator = await _setup(hass)
    good = {
        "id": "morning",
        "name": "Morning",
        "trigger": "time",
        "time": "07:00:00",
        "actions": [{"service": "light.turn_on", "data": {}}],
    }
    result = await coordinator.async_import_routines(
        [
            good,
            {"id": "broken", "name": "No steps", "actions": []},
            "not an object",
            {"id": "x", "companion_of": "morning"},
        ]
    )
    assert result["imported"] == 1
    reasons = {p["id"]: p["reason"] for p in result["problems"]}
    assert set(reasons) == {"broken", "?", "x"}
    assert coordinator.get_routine("morning") is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_import_drops_devices_that_are_not_on_this_install(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    entry, coordinator = await _setup(hass)
    result = await coordinator.async_import_routines(
        [
            {
                "id": "morning",
                "name": "Morning",
                "trigger": "time",
                "time": "07:00:00",
                "device_id": "device_from_another_home",
                "actions": [{"service": "light.turn_on", "data": {}}],
            }
        ]
    )
    assert result["imported"] == 1
    assert coordinator.get_routine("morning")["device_id"] is None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# ----- the light status poll -----


async def test_status_poll_carries_a_revision_that_moves_on_change(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, hass_ws_client
) -> None:
    entry, coordinator = await _setup(hass)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": f"{DOMAIN}/status"})
    before = (await client.receive_json())["result"]
    assert before["revision"] == coordinator.revision
    assert before["statuses"] == {}

    await coordinator.async_save_routine(make_routine(id="kitchen", name="Kitchen"))
    await hass.async_block_till_done()
    await client.send_json_auto_id({"type": f"{DOMAIN}/status"})
    after = (await client.receive_json())["result"]
    assert after["revision"] > before["revision"]
    assert "kitchen" in after["statuses"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
