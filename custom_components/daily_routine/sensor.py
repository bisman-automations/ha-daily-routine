"""Status sensors for Daily Routine — one device per routine."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_NAME, CONF_ROUTINE_ID, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the status sensor for this routine entry."""
    coordinator = hass.data[DOMAIN]["coordinator"]
    routine_id = entry.data.get(CONF_ROUTINE_ID) or entry.unique_id or entry.entry_id
    name = entry.options.get(CONF_NAME) or entry.title or routine_id

    # Legacy JSON entry may define multiple routines
    if "routines_json" in entry.options and not entry.data.get(CONF_ROUTINE_ID):
        entities = []
        for routine in coordinator.routines:
            if routine.get("entry_id") == entry.entry_id:
                entities.append(
                    RoutineStatusSensor(coordinator, routine["id"], routine["name"], entry.entry_id)
                )
        if entities:
            async_add_entities(entities)
        return

    async_add_entities([RoutineStatusSensor(coordinator, routine_id, name, entry.entry_id)])


class RoutineStatusSensor(SensorEntity, RestoreEntity):
    """Expose the current status of a single routine."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator, routine_id: str, name: str, entry_id: str) -> None:
        self.coordinator = coordinator
        self.routine_id = routine_id
        self._attr_unique_id = f"{DOMAIN}_{routine_id}_status"
        self._attr_name = "Status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, routine_id)},
            name=name,
            manufacturer="Daily Routine",
            model="Routine",
            sw_version="1.0.0",
            configuration_url="homeassistant://navigate/daily-routine",
        )
        self._entry_id = entry_id
        self._state = "idle"
        self._attrs: dict[str, Any] = {}

    @property
    def native_value(self) -> str:
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state:
            self._state = last.state
            self._attrs = dict(last.attributes)
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_update))
        self._handle_update()

    @callback
    def _handle_update(self) -> None:
        status = self.coordinator.get_status(self.routine_id)
        if not status:
            return
        self._state = status.get("state", "idle")
        self._attrs = {
            "routine_id": self.routine_id,
            "name": status.get("name"),
            "enabled": status.get("enabled"),
            "last_run": status.get("last_run"),
            "last_result": status.get("last_result"),
            "next_run": status.get("next_run"),
            "conditions_ok": status.get("conditions_ok"),
            "block_reason": status.get("block_reason"),
            "waiting": status.get("waiting"),
            "clear_since": status.get("clear_since"),
            "time": status.get("time"),
            "days": status.get("days"),
        }
        self.async_write_ha_state()
