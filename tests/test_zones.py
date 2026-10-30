"""Presence triggers for any place (zone), not only home."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.routines import normalize_routine
from custom_components.daily_routine.runtime import get_coordinator
from custom_components.daily_routine.schema import vol
from homeassistant.core import HomeAssistant


def _leave(zone: str | None = None, **overrides):
    raw = {
        "id": "leave_work",
        "name": "Leave work",
        "trigger": "everyone_leaves",
        "people": ["person.alex"],
        "actions": [
            {
                "service": "lock.lock",
                "target": {"entity_id": ["lock.front"]},
                "data": {},
            }
        ],
    }
    if zone is not None:
        raw["zone"] = zone
    raw.update(overrides)
    return raw


def test_zone_defaults_to_home() -> None:
    assert normalize_routine(_leave())["zone"] == "zone.home"


def test_zone_must_be_a_zone_entity() -> None:
    with pytest.raises(vol.Invalid):
        normalize_routine(_leave("light.hall"))


async def _setup(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, get_coordinator(hass)


async def test_leaving_work_runs_when_the_last_person_goes(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    calls = async_mock_service(hass, "lock", "lock")
    hass.states.async_set("zone.work", "0", {"friendly_name": "Work"})
    hass.states.async_set(
        "person.alex", "Work", {"source": "device_tracker.alex_phone"}
    )
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(_leave("zone.work"))
    await hass.async_block_till_done()

    hass.states.async_set(
        "person.alex", "home", {"source": "device_tracker.alex_phone"}
    )
    await hass.async_block_till_done()
    assert len(calls) == 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_leaving_home_does_not_fire_for_someone_at_work(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    calls = async_mock_service(hass, "lock", "lock")
    hass.states.async_set("zone.work", "0", {"friendly_name": "Work"})
    hass.states.async_set(
        "person.alex", "home", {"source": "device_tracker.alex_phone"}
    )
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(_leave())
    await hass.async_block_till_done()

    hass.states.async_set(
        "person.alex", "Work", {"source": "device_tracker.alex_phone"}
    )
    await hass.async_block_till_done()
    assert len(calls) == 1, "leaving home should count someone at work as away"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def test_unknown_people_block_the_away_check(hass: HomeAssistant) -> None:
    from custom_components.daily_routine.coordinator import HomeRoutinesCoordinator

    coordinator = HomeRoutinesCoordinator.__new__(HomeRoutinesCoordinator)
    coordinator.hass = hass
    hass.states.async_set("zone.work", "0", {"friendly_name": "Work"})
    hass.states.async_set("person.alex", "unavailable")
    assert coordinator._all_away(["person.alex"], "zone.work") is False  # noqa: SLF001
    hass.states.async_set("person.alex", "home")
    assert coordinator._all_away(["person.alex"], "zone.work") is True  # noqa: SLF001
    assert coordinator._all_away(["person.alex"], "zone.gone") is False  # noqa: SLF001
