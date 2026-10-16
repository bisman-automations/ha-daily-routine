"""End-to-end: the entry loads, services and the pause switch exist, and routines appear as sensors."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daily_routine.const import DOMAIN


async def test_entry_sets_up_with_services_and_pause_switch(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, "run_routine")
    assert hass.services.has_service(DOMAIN, "reload_routines")
    assert hass.states.get("switch.pause_all_routines") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, "run_routine")


async def test_saved_routine_gets_a_status_sensor_and_removal_drops_it(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN]["coordinator"]

    await coordinator.async_save_routine(
        {
            "id": "covers",
            "name": "Morning covers",
            "trigger": "time",
            "time": "07:00:00",
            "actions": [{"service": "cover.open_cover", "target": {"entity_id": ["cover.a"]}}],
        }
    )
    await hass.async_block_till_done()
    sensor = hass.states.get("sensor.morning_covers_status")
    assert sensor is not None
    assert sensor.state == "idle"
    assert sensor.attributes["routine_id"] == "covers"

    await coordinator.async_delete_routine("covers")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.morning_covers_status") is None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_pause_switch_drives_the_coordinator(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN]["coordinator"]

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
    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN]["coordinator"]
    registry = dr.async_get(hass)
    entities = er.async_get(hass)

    lamp_entry = MockConfigEntry(domain="test")
    lamp_entry.add_to_hass(hass)
    lamp = registry.async_get_or_create(
        config_entry_id=lamp_entry.entry_id,
        identifiers={("test", "hall_lamp")},
        name="Hall lamp",
    )
    hub = registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert hub is not None
    assert hub.entry_type == dr.DeviceEntryType.SERVICE
    switch = entities.async_get("switch.pause_all_routines")
    assert switch is not None and switch.device_id == hub.id

    linked = {
        "id": "hall",
        "name": "Hall lights off",
        "device_id": lamp.id,
        "trigger": "manual",
        "actions": [{"service": "light.turn_off", "target": {"entity_id": ["light.hall"]}}],
    }
    await coordinator.async_save_routine(linked)
    await hass.async_block_till_done()
    routine_device = registry.async_get_device(identifiers={(DOMAIN, "routine_hall")})
    assert routine_device is not None
    assert routine_device.entry_type == dr.DeviceEntryType.SERVICE
    assert routine_device.name == "Hall lights off"
    assert routine_device.via_device_id == lamp.id
    sensor_id = entities.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_hall_status")
    assert sensor_id is not None
    assert entities.async_get(sensor_id).device_id == routine_device.id

    await coordinator.async_save_routine(
        {
            "id": "covers",
            "name": "Morning covers",
            "actions": [{"service": "cover.open_cover", "target": {"entity_id": ["cover.a"]}}],
        }
    )
    await hass.async_block_till_done()
    unlinked = registry.async_get_device(identifiers={(DOMAIN, "routine_covers")})
    assert unlinked is not None and unlinked.via_device_id == hub.id

    await coordinator.async_delete_routine("hall")
    await hass.async_block_till_done()
    assert registry.async_get_device(identifiers={(DOMAIN, "routine_hall")}) is None
    assert registry.async_get_device(identifiers={("test", "hall_lamp")}) is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
