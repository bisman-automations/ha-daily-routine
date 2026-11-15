"""The house mode: Home, Away, Night, Guests or Vacation."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .access import async_ensure_allowed
from .const import DOMAIN
from .routines import MODES

# Coordinator or push updates; nothing polls on its own.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([HouseModeSelect(entry.runtime_data)])


class HouseModeSelect(SelectEntity):
    """Routines can run only in some modes, and start when the mode changes."""

    _attr_has_entity_name = False
    _attr_name = "House mode"
    _attr_unique_id = f"{DOMAIN}_house_mode"
    _attr_translation_key = "house_mode"
    _attr_should_poll = False
    _attr_options = list(MODES)
    _attr_icon = "mdi:home-switch-outline"

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
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_update))

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep running when the entity ID is changed (Home Assistant 2026.11+)."""
        parent = getattr(super(), "async_entity_id_changed", None)
        if parent is not None:
            parent(old_entity_id)

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
