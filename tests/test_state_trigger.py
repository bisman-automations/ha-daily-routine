"""State trigger: run when a device changes to a chosen state, and complete a to-do item."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.routines import normalize_routine
from custom_components.daily_routine.schema import vol
from homeassistant.core import HomeAssistant

from .conftest import make_routine

TODO_STEP = {
    "service": "todo.update_item",
    "target": {"entity_id": ["todo.groceries"]},
    "data": {"item": "Buy milk", "status": "completed"},
}


def _dinner_done(**overrides):
    raw = {
        "id": "milk_done",
        "name": "Dinner done",
        "trigger": "state_is",
        "watch_entity": "input_boolean.dinner_done",
        "watch_state": "on",
        "actions": [TODO_STEP],
    }
    raw.update(overrides)
    return raw


def test_state_trigger_needs_a_device_and_a_state() -> None:
    raw = _dinner_done()
    del raw["watch_entity"]
    with pytest.raises(vol.Invalid):
        normalize_routine(raw)
    with pytest.raises(vol.Invalid):
        normalize_routine(_dinner_done(watch_state=""))
    with pytest.raises(vol.Invalid):
        normalize_routine(_dinner_done(watch_entity="not an entity"))


def test_state_fields_are_kept_only_for_the_state_trigger() -> None:
    kept = normalize_routine(_dinner_done())
    assert kept["watch_entity"] == "input_boolean.dinner_done"
    assert kept["watch_states"] == ["on"]
    other = normalize_routine(
        make_routine(watch_entity="input_boolean.x", watch_state="on")
    )
    assert other["watch_entity"] is None
    assert other["watch_states"] == []


async def _setup(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, hass.data[DOMAIN]["coordinator"]


async def test_changing_to_the_state_completes_the_todo_item(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass.states.async_set("input_boolean.dinner_done", "off")
    calls = async_mock_service(hass, "todo", "update_item")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(_dinner_done())
    await hass.async_block_till_done()

    hass.states.async_set("input_boolean.dinner_done", "on")
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == ["todo.groceries"]
    assert calls[0].data["item"] == "Buy milk"
    assert calls[0].data["status"] == "completed"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_staying_in_the_state_does_not_run_again_but_coming_back_does(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass.states.async_set("input_boolean.dinner_done", "off")
    calls = async_mock_service(hass, "todo", "update_item")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(_dinner_done())
    await hass.async_block_till_done()

    hass.states.async_set("input_boolean.dinner_done", "on")
    await hass.async_block_till_done()
    hass.states.async_set(
        "input_boolean.dinner_done", "on", {"friendly_name": "Changed"}
    )
    await hass.async_block_till_done()
    assert len(calls) == 1, "an attribute change while already on must not run it again"

    hass.states.async_set("input_boolean.dinner_done", "off")
    await hass.async_block_till_done()
    hass.states.async_set("input_boolean.dinner_done", "on")
    await hass.async_block_till_done()
    assert len(calls) == 2

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_other_states_and_disabled_routines_do_not_run(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass.states.async_set("input_boolean.dinner_done", "off")
    calls = async_mock_service(hass, "todo", "update_item")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(_dinner_done())
    await coordinator.async_save_routine(_dinner_done(id="off_routine", enabled=False))
    await hass.async_block_till_done()

    hass.states.async_set("input_boolean.dinner_done", "unavailable")
    await hass.async_block_till_done()
    assert calls == []

    hass.states.async_set("input_boolean.dinner_done", "on")
    await hass.async_block_till_done()
    assert len(calls) == 1, "only the enabled routine runs"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
