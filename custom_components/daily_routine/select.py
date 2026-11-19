"""The house mode: Home, Away, Night, Guests or Vacation."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .access import async_ensure_allowed
from .const import DOMAIN
from .routines import MODES
from .runtime import DailyRoutineConfigEntry

# Coordinator or push updates; nothing polls on its own.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DailyRoutineConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HouseModeSelect(entry.runtime_data)])


class HouseModeSelect(SelectEntity):
    """Routines can run only in some modes, and start when the mode changes."""

    # Named by its translation key, with its icons (one per mode) in
    # icons.json (1.34.1). It has no device, so the name is just "House mode"
    # (select.house_mode).
    _attr_has_entity_name = True
    _attr_unique_id = f"{DOMAIN}_house_mode"
    _attr_translation_key = "house_mode"
    _attr_should_poll = False
    _attr_options = list(MODES)

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator

    @property
    def current_option(self) -> str:
        return str(self.coordinator.mode)

    async def async_select_option(self, option: str) -> None:
        await async_ensure_allowed(self.hass, self._context)
        await self.coordinator.async_set_mode(option)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_update, now=True)
        )

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep running when the entity ID is changed (Home Assistant 2026.11+)."""
        parent = getattr(super(), "async_entity_id_changed", None)
        if parent is not None:
            parent(old_entity_id)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
