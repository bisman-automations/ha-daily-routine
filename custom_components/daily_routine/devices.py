"""Each routine is a service device under the Daily Routine integration, linked to the device it belongs to.

The link is the device registry's via_device, the same "Connected via" link other integrations use.
Routines without a chosen device link to the Daily Routine hub device.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

HUB_NAME = "Daily Routine"
ROUTINE_PREFIX = "routine_"


def hub_identifier(entry_id: str) -> tuple[str, str]:
    return (DOMAIN, entry_id)


def routine_identifier(routine_id: str) -> tuple[str, str]:
    return (DOMAIN, f"{ROUTINE_PREFIX}{routine_id}")


def _is_routine_device(device: dr.DeviceEntry) -> bool:
    return any(
        domain == DOMAIN and value.startswith(ROUTINE_PREFIX)
        for domain, value in device.identifiers
    )


@callback
def async_ensure_hub(hass: HomeAssistant, entry: ConfigEntry) -> dr.DeviceEntry:
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={hub_identifier(entry.entry_id)},
        name=HUB_NAME,
        model="Routines",
        entry_type=dr.DeviceEntryType.SERVICE,
    )


@callback
def async_sync_routine_devices(
    hass: HomeAssistant, entry: ConfigEntry, routines: list[dict[str, Any]]
) -> None:
    """Make one service device per routine, linked to its device, and drop devices of deleted routines."""
    registry = dr.async_get(hass)
    hub = async_ensure_hub(hass, entry)
    wanted = {routine_identifier(r["id"]) for r in routines}

    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if _is_routine_device(device) and not (device.identifiers & wanted):
            registry.async_remove_device(device.id)

    for routine in routines:
        target = registry.async_get(routine["device_id"]) if routine.get("device_id") else None
        via_id = target.id if target is not None else hub.id
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={routine_identifier(routine["id"])},
            name=routine["name"],
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        registry.async_update_device(device.id, name=routine["name"], via_device_id=via_id)
