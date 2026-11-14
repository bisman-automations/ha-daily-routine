"""One device in more than one integration (1.21.0).

A SwitchBot curtain can be in Home Assistant through Matter, Bluetooth and the
cloud: three entities, three devices, which Home Assistant shows as linked
because they share an identifier or connection (a MAC address, for example).
A step treats those as one device with three ways to reach it, tried one at a
time in an order: the routine's own, else the one in Settings, else local
integrations before cloud ones.

Entities are only grouped when it's safe: every linked device comes from a
different config entry and has exactly one of the step's entities. A
two-channel switch (two entities on one device) is never grouped, so both
channels still get the command.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.loader import async_get_loaded_integration

_LOGGER = logging.getLogger(__name__)

# How close an integration is to the device, best first, when no order is set.
IOT_RANK = {
    "local_push": 0,
    "local_polling": 1,
    "assumed_state": 2,
    "cloud_push": 3,
    "cloud_polling": 4,
}


def integration_rank(hass: HomeAssistant, domain: str) -> int:
    try:
        iot_class = async_get_loaded_integration(hass, domain).iot_class
    except Exception:  # noqa: BLE001 - not loaded, or no manifest value
        return len(IOT_RANK)
    return IOT_RANK.get(iot_class or "", len(IOT_RANK))


def integration_name(hass: HomeAssistant, domain: str) -> str:
    try:
        return async_get_loaded_integration(hass, domain).name
    except Exception:  # noqa: BLE001
        return domain


def sort_key(
    hass: HomeAssistant, order: list[str], domain: str
) -> tuple[int, int, str]:
    """Chosen order first; then local before cloud; then by name."""
    if domain in order:
        return (0, order.index(domain), domain)
    return (1, integration_rank(hass, domain), domain)


def _device_keys(device: Any) -> set[tuple[str, ...]]:
    keys: set[tuple[str, ...]] = set()
    for identifier in device.identifiers:
        keys.add(("id", *map(str, identifier)))
    for connection in device.connections:
        keys.add(("conn", *map(str, connection)))
    return keys


def _clusters(devices: list[Any]) -> list[list[Any]]:
    """Devices that share any identifier or connection, joined up."""
    parent = {device.id: device.id for device in devices}

    def find(device_id: str) -> str:
        while parent[device_id] != device_id:
            parent[device_id] = parent[parent[device_id]]
            device_id = parent[device_id]
        return device_id

    seen: dict[tuple[str, ...], str] = {}
    for device in devices:
        for key in _device_keys(device):
            if key in seen:
                parent[find(device.id)] = find(seen[key])
            else:
                seen[key] = device.id
    groups: dict[str, list[Any]] = {}
    for device in devices:
        groups.setdefault(find(device.id), []).append(device)
    return list(groups.values())


def linked_groups(
    hass: HomeAssistant, entity_ids: Iterable[str], order: list[str]
) -> list[list[str]]:
    """The entities as devices: a list per device, its ways in, best first.

    Unlinked entities come back on their own, in the order given.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    entity_ids = list(entity_ids)
    platform: dict[str, str] = {}
    by_device: dict[str, list[str]] = {}
    devices: dict[str, Any] = {}
    for entity_id in entity_ids:
        entry = ent_reg.async_get(entity_id)
        if entry is None or entry.device_id is None:
            continue
        device = dev_reg.async_get(entry.device_id)
        if device is None:
            continue
        platform[entity_id] = entry.platform
        by_device.setdefault(device.id, []).append(entity_id)
        devices[device.id] = device

    grouped: dict[str, list[str]] = {}  # first entity -> its group
    member: set[str] = set()
    for cluster in _clusters(list(devices.values())):
        if len(cluster) < 2:
            continue
        if any(len(by_device[d.id]) != 1 for d in cluster):
            continue  # a device with several channels: not alternatives
        if len({_entry_id(d) for d in cluster}) != len(cluster):
            continue  # two devices of one integration: not the same thing twice
        ways = [by_device[d.id][0] for d in cluster]
        ways.sort(key=lambda e: sort_key(hass, order, platform[e]))
        for entity_id in ways:
            member.add(entity_id)
        grouped[min(ways, key=entity_ids.index)] = ways

    result: list[list[str]] = []
    for entity_id in entity_ids:
        if entity_id in grouped:
            result.append(grouped[entity_id])
        elif entity_id not in member:
            result.append([entity_id])
    return result


def linked_integrations(hass: HomeAssistant) -> list[str]:
    """Integrations with a device that's linked to another integration's device."""
    items = dr.async_get(hass).devices
    # A mapping of id to device before 2026.9, the devices themselves after.
    every = list(items.values()) if isinstance(items, Mapping) else list(items)
    devices = [d for d in every if d.entry_type is None]
    domains: set[str] = set()
    entries = hass.config_entries
    for cluster in _clusters(devices):
        cluster_domains = set()
        for device in cluster:
            entry_id = _entry_id(device)
            entry = entries.async_get_entry(entry_id) if entry_id else None
            if entry is not None:
                cluster_domains.add(entry.domain)
        if len(cluster_domains) > 1:
            domains |= cluster_domains
    return sorted(domains, key=lambda d: sort_key(hass, [], d))


def _entry_id(device: Any) -> str | None:
    """The device's config entry (one per device since Home Assistant 2026.8)."""
    return getattr(device, "config_entry_id", None)
