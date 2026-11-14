"""Daily Routine's entities keep running when their entity ID is changed."""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.daily_routine as package
from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.runtime import get_coordinator
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity

LIFECYCLE = (
    "async_prepare_to_add_to_hass",
    "async_added_to_hass",
    "async_will_remove_from_hass",
)


def _entity_classes() -> list[type[Entity]]:
    found: list[type[Entity]] = []
    for info in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):
        module = importlib.import_module(info.name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(cls, Entity)
                and cls.__module__ == module.__name__
                and cls not in found
            ):
                found.append(cls)
    return found


def test_entities_handle_entity_id_changes() -> None:
    """Every class with its own add/remove code also has the rename hook.

    From Home Assistant 2026.11 a class without it is removed and added
    again when its entity ID changes (until 2027.11).
    """
    classes = _entity_classes()
    assert classes
    missing = [
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in classes
        if any(name in vars(cls) for name in LIFECYCLE)
        and "async_entity_id_changed" not in vars(cls)
    ]
    assert not missing, f"missing async_entity_id_changed: {missing}"


@pytest.mark.skipif(
    not hasattr(Entity, "async_entity_id_changed"),
    reason="Entity IDs are changed in place from Home Assistant 2026.11",
)
async def test_renamed_entities_keep_working(
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

    registry = er.async_get(hass)
    old_switch = "switch.pause_all_routines"
    old_sensor = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{DOMAIN}_covers_status"
    )
    assert old_sensor
    new_switch = "switch.renamed_pause"
    new_sensor = "sensor.renamed_covers_status"
    registry.async_update_entity(old_switch, new_entity_id=new_switch)
    registry.async_update_entity(old_sensor, new_entity_id=new_sensor)
    await hass.async_block_till_done()

    assert hass.states.get(old_switch) is None
    assert hass.states.get(old_sensor) is None
    assert hass.states.get(new_switch).state == "off"
    sensor = hass.states.get(new_sensor)
    assert sensor.state == "idle"
    assert sensor.attributes["paused"] is False

    # The switch still drives the coordinator, and both follow its updates.
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": new_switch}, blocking=True
    )
    await hass.async_block_till_done()
    assert coordinator.paused is True
    assert hass.states.get(new_switch).state == "on"
    assert hass.states.get(new_sensor).attributes["paused"] is True
    assert hass.states.get(old_switch) is None
    assert hass.states.get(old_sensor) is None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
