"""Settings tab: which people routines can use, and an optional notify service."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.daily_routine.const import DOMAIN, STORAGE_KEY
from custom_components.daily_routine.coordinator import list_people
from custom_components.daily_routine.schema import vol


async def _setup(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, hass.data[DOMAIN]["coordinator"]


async def test_hidden_people_are_flagged_and_saved(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    entry, coordinator = await _setup(hass)
    hass.states.async_set("person.alex", "home", {"source": "device_tracker.alex_phone"})
    hass.states.async_set("person.skylight", "unknown", {"friendly_name": "Skylight"})

    await coordinator.async_set_settings(
        {"hidden_people": ["person.skylight"], "notify_service": ""}
    )
    people = {
        p["entity_id"]: p
        for p in list_people(hass, frozenset(coordinator.settings["hidden_people"]))
    }
    assert people["person.skylight"]["hidden"] is True
    assert people["person.alex"]["hidden"] is False
    assert hass_storage[STORAGE_KEY]["data"]["settings"]["hidden_people"] == ["person.skylight"]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_settings_reject_bad_values(hass: HomeAssistant, enable_custom_integrations) -> None:
    entry, coordinator = await _setup(hass)

    with pytest.raises(vol.Invalid, match="person entities"):
        await coordinator.async_set_settings({"hidden_people": ["light.hall"]})
    with pytest.raises(vol.Invalid, match="notify service"):
        await coordinator.async_set_settings({"notify_service": "light.turn_on"})
    with pytest.raises(vol.Invalid, match="not available"):
        await coordinator.async_set_settings({"notify_service": "notify.missing_phone"})

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_notifications_also_go_to_the_chosen_notify_service(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    entry, coordinator = await _setup(hass)
    calls = async_mock_service(hass, "notify", "mobile_app_phone")

    await coordinator.async_set_settings({"notify_service": "notify.mobile_app_phone"})
    await coordinator._notify("Morning covers failed.")  # noqa: SLF001
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].data == {"title": "Daily Routine", "message": "Morning covers failed."}

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
