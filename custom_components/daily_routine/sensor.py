"""One status sensor per routine. Sensors follow routines as they are added and removed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, SIGNAL_ROUTINES_CHANGED
from .devices import (
    async_sync_routine_devices,
    routine_identifier,
    routine_subentry_ids,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN]["coordinator"]
    entities: dict[str, RoutineStatusSensor] = {}

    @callback
    def _sync() -> None:
        wanted = {r["id"] for r in coordinator.routines}
        subentries = routine_subentry_ids(entry)
        # A sensor joins its routine's entry, so it waits until that entry exists.
        for rid in sorted(wanted - entities.keys()):
            if rid not in subentries:
                continue
            sensor = RoutineStatusSensor(coordinator, rid)
            entities[rid] = sensor
            async_add_entities([sensor], config_subentry_id=subentries[rid])
        for rid in sorted(entities.keys() - wanted):
            sensor = entities.pop(rid)
            registry = er.async_get(hass)
            if sensor.entity_id and registry.async_get(sensor.entity_id):
                registry.async_remove(sensor.entity_id)
            hass.async_create_task(sensor.async_remove(force_remove=True))
        async_sync_routine_devices(hass, entry, coordinator.routines)

    _sync()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_ROUTINES_CHANGED, _sync)
    )


class RoutineStatusSensor(SensorEntity):
    """Status of one routine: idle, waiting, running, skipped or error."""

    _attr_should_poll = False
    _attr_has_entity_name = False
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: Any, routine_id: str) -> None:
        self.coordinator = coordinator
        self.routine_id = routine_id
        self._attr_unique_id = f"{DOMAIN}_{routine_id}_status"
        self._attrs: dict[str, Any] = {}
        self._state = "idle"

    @property
    def name(self) -> str:
        routine = self.coordinator._find(self.routine_id)  # noqa: SLF001
        return f"{routine['name']} status" if routine else f"{self.routine_id} status"

    @property
    def suggested_object_id(self) -> str:
        routine = self.coordinator._find(self.routine_id)  # noqa: SLF001
        name = routine["name"] if routine else self.routine_id
        return f"{name} status"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={routine_identifier(self.routine_id)})

    @property
    def native_value(self) -> str:
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_update))
        self._handle_update()

    @callback
    def _handle_update(self) -> None:
        status = self.coordinator.get_status(self.routine_id)
        if not status:
            return
        self._state = status["state"]
        self._attrs = {
            "routine_id": self.routine_id,
            "name": status["name"],
            "enabled": status["enabled"],
            "room": status["room"],
            "trigger": status["trigger"],
            "paused": status["paused"],
            "last_run": status["last_run"],
            "last_result": status["last_result"],
            "next_run": status["next_run"],
            "clear_since": status["clear_since"],
            "conditions_ok": status["conditions_ok"],
            "block_reason": status["block_reason"],
            "waiting": status["waiting"],
            "repeat": status["repeat"],
            "time": status["time"],
            "days": status["days"],
        }
        if self.hass is not None:
            self.async_write_ha_state()
