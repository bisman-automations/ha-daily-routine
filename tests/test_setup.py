"""End-to-end: the entry loads, services and the pause switch exist, and routines appear as sensors."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.coordinator import HomeRoutinesCoordinator
from custom_components.daily_routine.runtime import get_coordinator
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er


async def test_entry_sets_up_with_services_and_pause_switch(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, "run_routine")
    assert hass.services.has_service(DOMAIN, "reload_routines")
    assert hass.states.get("switch.pause_all_routines") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert get_coordinator(hass) is None
    # The actions stay registered, and say Daily Routine isn't running.
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "run_routine", {"routine_id": "x"}, blocking=True
        )
    assert err.value.translation_key == "not_loaded"


async def test_run_routine_unknown_id_raises(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "run_routine", {"routine_id": "nope"}, blocking=True
        )
    assert err.value.translation_key == "run_failed"


async def test_failed_setup_leaves_nothing_behind(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """A setup that fails part way stops what it started, and the retry works."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.daily_routine.HomeRoutinesCoordinator.async_sync_subentries",
            side_effect=RuntimeError("boom"),
        ),
        patch(
            "custom_components.daily_routine.HomeRoutinesCoordinator.async_unload",
            autospec=True,
            side_effect=HomeRoutinesCoordinator.async_unload,
        ) as unload,
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert unload.call_count == 1
    assert get_coordinator(hass) is None

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert get_coordinator(hass) is entry.runtime_data
    assert hass.states.get("switch.pause_all_routines") is not None


async def test_edit_routine_while_not_loaded_aborts(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """Editing a routine's entry while Daily Routine is off used to raise KeyError."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await get_coordinator(hass).async_save_routine(
        {
            "id": "covers",
            "name": "Morning covers",
            "trigger": "time",
            "time": "07:00:00",
            "actions": [
                {"service": "cover.open_cover", "target": {"entity_id": ["cover.a"]}}
            ],
        }
    )
    await hass.async_block_till_done()
    subentry = next(iter(entry.subentries.values()))
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_loaded"


async def test_saved_routine_gets_a_status_sensor_and_removal_drops_it(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = get_coordinator(hass)

    await coordinator.async_save_routine(
        {
            "id": "covers",
            "name": "Morning covers",
            "trigger": "time",
            "time": "07:00:00",
            "actions": [
                {"service": "cover.open_cover", "target": {"entity_id": ["cover.a"]}}
            ],
        }
    )
    await hass.async_block_till_done()
    sensor_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{DOMAIN}_covers_status"
    )
    assert sensor_id is not None
    sensor = hass.states.get(sensor_id)
    assert sensor is not None
    assert sensor.state == "idle"
    assert sensor.attributes["routine_id"] == "covers"

    await coordinator.async_delete_routine("covers")
    await hass.async_block_till_done()
    assert (
        er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{DOMAIN}_covers_status"
        )
        is None
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_pause_switch_drives_the_coordinator(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = get_coordinator(hass)

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.pause_all_routines"}, blocking=True
    )
    assert coordinator.paused is True
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.pause_all_routines"}, blocking=True
    )
    assert coordinator.paused is False

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_routines_are_service_devices_linked_to_their_device(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = get_coordinator(hass)
    registry = dr.async_get(hass)
    entities = er.async_get(hass)

    lamp_entry = MockConfigEntry(domain="test")
    lamp_entry.add_to_hass(hass)
    lamp = registry.async_get_or_create(
        config_entry_id=lamp_entry.entry_id,
        identifiers={("test", "hall_lamp")},
        name="Hall lamp",
    )
    hub = registry.async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert hub is not None
    assert hub.entry_type == dr.DeviceEntryType.SERVICE
    assert hub.manufacturer == "Daily Routine"

    linked = {
        "id": "hall",
        "name": "Hall lights off",
        "device_id": lamp.id,
        "trigger": "manual",
        "actions": [
            {"service": "light.turn_off", "target": {"entity_id": ["light.hall"]}}
        ],
    }
    await coordinator.async_save_routine(linked)
    await hass.async_block_till_done()
    routine_device = registry.async_get_device_by_identifier(
        (DOMAIN, "routine_hall"), entry.entry_id
    )
    assert routine_device is not None
    assert routine_device.entry_type == dr.DeviceEntryType.SERVICE
    assert routine_device.name == "Hall lights off"
    # A manufacturer and model, so the device list doesn't say "unknown".
    assert routine_device.manufacturer == "Daily Routine"
    assert routine_device.model == "Routine"
    # Shares the lamp's identifiers where Home Assistant allows it, otherwise is connected via it.
    assert (
        routine_device.via_device_id == lamp.id
        or ("test", "hall_lamp") in routine_device.identifiers
    )
    sensor_id = entities.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_hall_status")
    assert sensor_id is not None
    assert entities.async_get(sensor_id).device_id == routine_device.id

    await coordinator.async_save_routine(
        {
            "id": "covers",
            "name": "Morning covers",
            "actions": [
                {"service": "cover.open_cover", "target": {"entity_id": ["cover.a"]}}
            ],
        }
    )
    await hass.async_block_till_done()
    unlinked = registry.async_get_device_by_identifier(
        (DOMAIN, "routine_covers"), entry.entry_id
    )
    assert unlinked is not None and unlinked.via_device_id == hub.id
    covers_entry = next(s for s in entry.subentries.values() if s.unique_id == "covers")
    assert unlinked.config_entry_id == entry.entry_id
    assert unlinked.config_subentry_id == covers_entry.subentry_id

    await coordinator.async_delete_routine("hall")
    await hass.async_block_till_done()
    assert (
        registry.async_get_device_by_identifier(
            (DOMAIN, "routine_hall"), entry.entry_id
        )
        is None
    )
    assert (
        registry.async_get_device_by_identifier(
            ("test", "hall_lamp"), lamp_entry.entry_id
        )
        is not None
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_routines_only_use_people_with_location_tracking(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    from custom_components.daily_routine.coordinator import list_people  # noqa: PLC0415
    from custom_components.daily_routine.schema import vol  # noqa: PLC0415

    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = get_coordinator(hass)
    hass.states.async_set(
        "person.alex",
        "not_home",
        {"friendly_name": "Alex", "source": "device_tracker.alex_phone"},
    )
    hass.states.async_set("person.kid", "unknown", {"friendly_name": "Kid"})

    people = {p["entity_id"]: p["tracked"] for p in list_people(hass)}
    assert people == {"person.alex": True, "person.kid": False}

    base = {
        "id": "away_lights",
        "name": "Away lights",
        "trigger": "everyone_leaves",
        "actions": [
            {"service": "light.turn_off", "target": {"entity_id": ["light.hall"]}}
        ],
    }
    with pytest.raises(vol.Invalid, match="Kid has no location tracking"):
        await coordinator.async_save_routine(
            {**base, "people": ["person.alex", "person.kid"]}
        )
    saved = await coordinator.async_save_routine({**base, "people": ["person.alex"]})
    assert saved["people"] == ["person.alex"]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
