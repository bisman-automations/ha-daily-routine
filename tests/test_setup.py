"""End-to-end: the config entry loads, registers services and creates a status sensor."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daily_routine.const import DOMAIN

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


async def test_routine_entry_sets_up_and_creates_status_sensor(
    hass, enable_custom_integrations
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Morning curtains",
        data={"routine_id": "morning_covers"},
        options={
            "name": "Morning curtains",
            "trigger": "time",
            "time": "07:00",
            "days": WEEKDAYS,
            "action_1_service": "cover.open_cover",
            "action_1_entities": ["cover.living_room_curtains"],
        },
        unique_id="morning_covers",
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, "run_routine")
    assert hass.services.has_service(DOMAIN, "reload_routines")
    sensors = [
        s
        for s in hass.states.async_all("sensor")
        if s.attributes.get("routine_id") == "morning_covers"
    ]
    assert len(sensors) == 1
    assert sensors[0].state == "idle"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
