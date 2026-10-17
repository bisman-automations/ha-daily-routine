"""Global pause switch: stops all scheduled and presence routines while on."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([RoutinesPausedSwitch(hass.data[DOMAIN]["coordinator"])])


class RoutinesPausedSwitch(SwitchEntity):
    """On = routines are paused. Manual runs still work."""

    _attr_has_entity_name = False
    _attr_name = "Pause all routines"
    _attr_unique_id = f"{DOMAIN}_paused"
    _attr_should_poll = False
    _attr_icon = "mdi:pause-circle-outline"

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.paused)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_paused(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_paused(False)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
