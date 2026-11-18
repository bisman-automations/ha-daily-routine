"""Answers worked out from Home Assistant's registries, kept until they change (1.33.0).

Linked devices (``linked.py``) and the panel's device and integration lists
only change when a device, entity or area is added, changed or removed, or an
integration finishes loading (its name and how close it is to the device come
from its manifest). Working them out again for every step and every page load
is most of their cost on a big system (hundreds of devices), so they are kept
here and dropped as soon as one of those events is fired. Updates that
can't change an answer are let through without dropping anything: one of
Daily Routine's own devices (saving a routine updates it, and neither list
includes it), or a field nothing here reads (a new entity name, a firmware
version).

Caching is only on while something tracks the registries (the coordinator,
between ``async_setup`` and ``async_unload``). Without that, ``cached`` simply
builds the answer, so pure helpers and tests that use them on their own see
exactly the old behaviour.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from itertools import count
from secrets import token_hex
from typing import Any

from homeassistant.const import EVENT_COMPONENT_LOADED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

from .const import DOMAIN

DATA_REGISTRY_CACHE = "daily_routine_registry_cache"

# Every event that can change an answer kept here.
EVENTS = (
    dr.EVENT_DEVICE_REGISTRY_UPDATED,
    er.EVENT_ENTITY_REGISTRY_UPDATED,
    ar.EVENT_AREA_REGISTRY_UPDATED,
    EVENT_COMPONENT_LOADED,
)

# At most this many answers are kept (one per distinct step, mostly); past
# it everything is dropped and built again as needed.
MAX_VALUES = 1024

# Versions are never reused, not even after Home Assistant restarts: a page
# that kept the device list from before can't mistake an old version for
# the current one.
_RUN = token_hex(4)
_versions = count(1)


class RegistryCache:
    """Kept answers and the version of the registries they were built from."""

    def __init__(self) -> None:
        self.version = f"{_RUN}-{next(_versions)}"
        self._values: dict[Hashable, Any] = {}
        self._users = 0
        self._unsubs: list[Callable[[], None]] = []
        # How many answers were built (for tests and profiling).
        self.builds = 0

    def get[T](self, key: Hashable, build: Callable[[], T]) -> T:
        if key in self._values:
            value: T = self._values[key]
            return value
        if len(self._values) >= MAX_VALUES:
            self._values.clear()
        value = build()
        self.builds += 1
        self._values[key] = value
        return value

    @callback
    def clear(self, _event: Event[Any] | None = None) -> None:
        """Something in the registries changed: drop every kept answer."""
        self._values.clear()
        self.version = f"{_RUN}-{next(_versions)}"


def _ours(identifiers: Any) -> bool:
    return bool(identifiers) and all(domain == DOMAIN for domain, *_ in identifiers)


# What the kept answers read from a device (linked clusters, the panel's
# device list) and from an entity (each entity's integration, linked groups).
# An update that changes none of these leaves every answer as it was.
DEVICE_FIELDS = frozenset(
    {
        "area_id",
        "config_entries",
        "config_entries_subentries",
        "config_entry_id",
        "connections",
        "disabled_by",
        "entry_type",
        "identifiers",
        "is_composite_device",
        "model",
        "name",
        "name_by_user",
        "primary_config_entry",
    }
)
ENTITY_FIELDS = frozenset(
    {"config_entry_id", "device_id", "disabled_by", "entity_id", "platform"}
)


def _device_matters(hass: HomeAssistant) -> Callable[[Any], bool]:
    """A filter: true for device registry events worth dropping the cache for.

    Saving a routine updates that routine's own device. Neither the linked
    clusters nor the panel's device list include our own routine and hub
    devices, so those updates (and updates of fields nothing here reads,
    such as a firmware version) leave every answer as it was.
    """

    @callback
    def _matters(data: Any) -> bool:
        changes = data.get("changes")
        if data.get("action") != "update" or changes is None:
            return True
        if not DEVICE_FIELDS.intersection(changes):
            return False
        device = dr.async_get(hass).async_get(data.get("device_id") or "")
        if device is None or not _ours(device.identifiers):
            return True
        # It was someone else's device a moment ago: that matters.
        old = changes.get("identifiers")
        return old is not None and not _ours(old)

    return _matters


@callback
def _entity_matters(data: Any) -> bool:
    """A filter: true for entity registry events worth dropping the cache for
    (an entity added or removed, or its id, device, integration or whether
    it's turned off changed; a new name changes nothing kept)."""
    changes = data.get("changes")
    if data.get("action") != "update" or changes is None:
        return True
    return bool(ENTITY_FIELDS.intersection(changes))


@callback
def async_track_registries(hass: HomeAssistant) -> Callable[[], None]:
    """Turn the cache on until the returned function is called.

    Counted, so two coordinators (a test, a reload overlapping the old one)
    can each track and stop on their own.
    """
    cache: RegistryCache | None = hass.data.get(DATA_REGISTRY_CACHE)
    if cache is None:
        cache = hass.data[DATA_REGISTRY_CACHE] = RegistryCache()
    if not cache._users:
        cache._unsubs = [
            hass.bus.async_listen(
                name,
                cache.clear,
                event_filter=(
                    _device_matters(hass)
                    if name == dr.EVENT_DEVICE_REGISTRY_UPDATED
                    else _entity_matters
                    if name == er.EVENT_ENTITY_REGISTRY_UPDATED
                    else None
                ),
            )
            for name in EVENTS
        ]
    cache._users += 1
    stopped = False

    @callback
    def _stop() -> None:
        nonlocal stopped
        if stopped:
            return
        stopped = True
        cache._users -= 1
        if cache._users:
            return
        for unsub in cache._unsubs:
            unsub()
        cache._unsubs = []
        if hass.data.get(DATA_REGISTRY_CACHE) is cache:
            del hass.data[DATA_REGISTRY_CACHE]

    return _stop


def get_registry_cache(hass: HomeAssistant) -> RegistryCache | None:
    """The cache while it is on, else None."""
    cache: RegistryCache | None = hass.data.get(DATA_REGISTRY_CACHE)
    return cache if cache is not None and cache._users else None


def cached[T](hass: HomeAssistant, key: Hashable, build: Callable[[], T]) -> T:
    """``build()``, kept until the registries change (or just built, when off)."""
    if (cache := get_registry_cache(hass)) is None:
        return build()
    return cache.get(key, build)
