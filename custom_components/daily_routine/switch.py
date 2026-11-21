"""Global pause switch: stops all scheduled and presence routines while on."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .access import async_ensure_allowed
from .const import DOMAIN
from .runtime import DailyRoutineConfigEntry

# Coordinator or push updates; nothing polls on its own.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DailyRoutineConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([RoutinesPausedSwitch(entry.runtime_data)])


# What an entity last wrote, before it has written anything.
_NOTHING = object()


class RoutinesPausedSwitch(SwitchEntity):
    """On = routines are paused. Manual runs still work."""

    # Named by its translation key, with its icons in icons.json (1.34.1). It
    # has no device, so the name is just "Pause all routines"
    # (switch.pause_all_routines).
    _attr_has_entity_name = True
    _attr_unique_id = f"{DOMAIN}_paused"
    _attr_translation_key = "pause_all"
    _attr_should_poll = False

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator
        self._written: Any = _NOTHING

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.paused)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await async_ensure_allowed(self.hass, self._context)
        await self.coordinator.async_set_paused(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await async_ensure_allowed(self.hass, self._context)
        await self.coordinator.async_set_paused(False)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_update, now=True)
        )

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep running when the entity ID is changed (Home Assistant 2026.11+).

        Nothing here is keyed on the entity ID, so the entity carries on as is.
        """
        parent = getattr(super(), "async_entity_id_changed", None)
        if parent is not None:
            parent(old_entity_id)

    @callback
    def _handle_update(self) -> None:
        # Told on every change in Daily Routine (1.31.1, at once); written
        # only when what it shows changed (1.34.2).
        shown = self.is_on
        if shown == self._written:
            return
        self._written = shown
        self.async_write_ha_state()
