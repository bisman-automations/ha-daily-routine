"""One status sensor per routine. Sensors follow routines as they are added and removed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_ROUTINES_CHANGED
from .devices import (
    async_sync_routine_devices,
    routine_identifier,
    routine_subentry_ids,
)
from .runtime import DailyRoutineConfigEntry

# Coordinator or push updates; nothing polls on its own.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DailyRoutineConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    if coordinator is None:
        # Platforms are only set up for the primary entry, which always has
        # the coordinator; a leftover 1.0.0 entry has nothing to show.
        return
    entities: dict[str, RoutineStatusSensor] = {}

    @callback
    def _sync() -> None:
        # The routines' devices first (1.34.1): a new sensor is named after its
        # routine's device, and its entity ID is made from that name, so the
        # device has to exist when the sensor is added. Made after, a new
        # routine's sensor got an ID without the routine's name in it
        # (sensor.daily_routine_status instead of sensor.morning_status).
        async_sync_routine_devices(hass, entry, coordinator.routines)
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
            entry.async_create_task(hass, sensor.async_remove(force_remove=True))

    async_add_entities([KindOfDaySensor(coordinator)])
    _sync()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_ROUTINES_CHANGED, _sync)
    )


# What an entity last wrote, before it has written anything.
_NOTHING = object()


class RoutineStatusSensor(SensorEntity):
    """Status of one routine: idle, waiting, asking, held, running, skipped or error.

    Named the Home Assistant way (1.34.1): the routine's own device carries the
    routine's name, and this entity adds only "Status" (``translation_key``), so
    the full name is "<routine> Status". Before, the entity named itself
    "<routine> status" as well, and Home Assistant 2026.9 and newer put the
    device name in front of that too ("Morning Morning status"). Renaming the
    routine renames its device, and with it this name. The icon is in
    icons.json. Entity IDs already in the registry are kept as they are.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "status"

    def __init__(self, coordinator: Any, routine_id: str) -> None:
        self.coordinator = coordinator
        self.routine_id = routine_id
        self._attr_unique_id = f"{DOMAIN}_{routine_id}_status"
        self._attrs: dict[str, Any] = {}
        self._state = "idle"
        self._written: tuple[str, dict[str, Any]] | None = None

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
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        """Keep running when the entity ID is changed (Home Assistant 2026.11+).

        Nothing here is keyed on the entity ID, so the entity carries on as is.
        """
        parent = getattr(super(), "async_entity_id_changed", None)
        if parent is not None:
            parent(old_entity_id)

    @callback
    def _handle_update(self) -> None:
        status = self.coordinator.sensor_status(self.routine_id)
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
        # Nothing it shows changed: no state to write.
        written = (self._state, self._attrs)
        if written == self._written:
            return
        self._written = written
        if self.hass is not None:
            self.async_write_ha_state()


class KindOfDaySensor(SensorEntity):
    """Today's kind of day (Settings → Kinds of day), for automations and
    other integrations: School day, Holiday… or Normal day."""

    # Named by its translation key, with its icon in icons.json (1.34.1). It
    # has no device, so the name is just "Kind of day" (sensor.kind_of_day).
    _attr_has_entity_name = True
    _attr_unique_id = f"{DOMAIN}_kind_of_day"
    _attr_translation_key = "kind_of_day"
    _attr_should_poll = False

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator
        self._written: Any = _NOTHING

    @property
    def native_value(self) -> str | None:
        return self.coordinator.day_type(dt_util.now())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "kinds": [
                t["name"] for t in self.coordinator.settings.get("day_types") or []
            ]
        }

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
        # Told on every change in Daily Routine (1.31.1, at once); written
        # only when what it shows changed (1.34.2).
        shown = (self.native_value, self.extra_state_attributes)
        if shown == self._written:
            return
        self._written = shown
        self.async_write_ha_state()
