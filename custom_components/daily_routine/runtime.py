"""Where the running coordinator lives: on the primary config entry.

Daily Routine has one storage-backed coordinator for the whole home. It is the
primary entry's ``runtime_data``. Leftover per-routine entries from 1.0.0 load
with ``runtime_data`` set to ``None``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import HomeRoutinesCoordinator

type DailyRoutineConfigEntry = ConfigEntry[HomeRoutinesCoordinator | None]

# The entry id that owns the coordinator, claimed as soon as its setup starts.
DATA_PRIMARY: HassKey[str] = HassKey(f"{DOMAIN}_primary")


@callback
def get_coordinator(hass: HomeAssistant) -> HomeRoutinesCoordinator | None:
    """The running coordinator, or None while Daily Routine isn't loaded."""
    entry_id = hass.data.get(DATA_PRIMARY)
    if entry_id is None:
        return None
    entry = hass.config_entries.async_get_entry(entry_id)
    return getattr(entry, "runtime_data", None) if entry is not None else None
