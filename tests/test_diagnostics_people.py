"""The diagnostics download leaves out person and notify ids, however deep they sit in a routine."""

from __future__ import annotations

import json

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.core import HomeAssistant

from .conftest import make_routine


async def test_person_and_phone_ids_are_not_in_the_download(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    async_mock_service(hass, "notify", "mobile_app_alex_phone")
    hass.states.async_set(
        "person.alex", "home", {"source": "device_tracker.alex_phone"}
    )
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN]["coordinator"]
    await coordinator.async_save_routine(
        make_routine(
            id="tell_alex",
            name="Tell Alex",
            actions=[
                {
                    "service": "notify.mobile_app_alex_phone",
                    "target": {},
                    "data": {"message": "Dinner is ready"},
                }
            ],
            conditions=[
                {
                    "kind": "presence",
                    "people": ["person.alex"],
                    "zone": "zone.home",
                    "who": "everyone",
                    "where": "in",
                }
            ],
        )
    )
    await hass.async_block_till_done()

    data = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(data)
    assert "person.alex" not in text
    assert "notify.mobile_app_alex_phone" not in text
    assert "Dinner is ready" in text, "the routine itself still shows up for support"
    assert "tell_alex" in data["statuses"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
