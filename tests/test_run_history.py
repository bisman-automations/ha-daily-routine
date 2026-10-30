"""Recent runs per routine: what ran, what was skipped and why, and what failed."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.runtime import get_coordinator
from homeassistant.core import HomeAssistant

from .conftest import make_routine


async def _setup(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, get_coordinator(hass)


async def test_a_run_that_completes_is_recorded_as_ran(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    async_mock_service(hass, "light", "turn_off")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(make_routine(id="hall", name="Hall lights"))
    await hass.async_block_till_done()

    assert await coordinator.async_run("hall") is True
    history = coordinator.get_history("hall")
    assert [run["outcome"] for run in history] == ["ran"]
    assert history[0]["detail"] == ""
    assert coordinator.get_status("hall")["history"] == history
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_a_run_blocked_by_a_condition_is_recorded_with_its_reason(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass.states.async_set("input_boolean.guest_mode", "on")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(
            id="hall",
            name="Hall lights",
            require_states=[{"entity_id": "input_boolean.guest_mode", "state": "off"}],
        )
    )
    await hass.async_block_till_done()

    assert await coordinator.async_run("hall") is False
    history = coordinator.get_history("hall")
    assert history[0]["outcome"] == "skipped"
    assert history[0]["detail"] == "extra_condition_not_met"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_history_keeps_the_newest_runs_first_and_stops_at_the_limit(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    from custom_components.daily_routine.coordinator import HISTORY_LENGTH

    async_mock_service(hass, "light", "turn_off")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(make_routine(id="hall", name="Hall lights"))
    await hass.async_block_till_done()

    for _ in range(HISTORY_LENGTH + 5):
        await coordinator.async_run("hall")
    assert len(coordinator.get_history("hall")) == HISTORY_LENGTH
    assert (
        coordinator.get_history("hall")[0]["at"]
        >= coordinator.get_history("hall")[-1]["at"]
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_deleting_a_routine_clears_its_history(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    async_mock_service(hass, "light", "turn_off")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(make_routine(id="hall", name="Hall lights"))
    await hass.async_block_till_done()
    await coordinator.async_run("hall")

    await coordinator.async_delete_routine("hall")
    await hass.async_block_till_done()
    assert coordinator.get_history("hall") == []
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
