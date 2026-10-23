"""Diagnostics download: routines, their status and settings, for fixing problems."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

# Phone names and hidden profiles are personal, so they are redacted from the download.
TO_REDACT = {"notify_services", "hidden_people"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = hass.data.get(DOMAIN, {}).get("coordinator")
    if coordinator is None:
        return {"loaded": False}
    routines = [dict(r) for r in coordinator.routines]
    return {
        "loaded": True,
        "paused": coordinator.paused,
        "settings": async_redact_data(coordinator.settings, TO_REDACT),
        "routines": routines,
        "statuses": {r["id"]: coordinator.get_status(r["id"]) for r in routines},
        "integration_entries": sorted(
            sub.unique_id or sub.subentry_id for sub in entry.subentries.values()
        ),
    }
