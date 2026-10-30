"""Diagnostics download: routines, their status and settings, for fixing problems."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .runtime import get_coordinator

# Phone names and hidden profiles are personal, so they are redacted from the download.
TO_REDACT = {"notify_services", "hidden_people"}
# Routines name people and phones in their people, condition and step targets. Those ids
# are replaced wherever they appear, however deep.
PRIVATE_PREFIXES = ("person.", "notify.")
REDACTED = "**REDACTED**"


def _redact_people(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_people(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_people(item) for item in value]
    if isinstance(value, str) and value.startswith(PRIVATE_PREFIXES):
        return REDACTED
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = get_coordinator(hass)
    if coordinator is None:
        return {"loaded": False}
    routines = [dict(r) for r in coordinator.routines]
    return {
        "loaded": True,
        "paused": coordinator.paused,
        "settings": async_redact_data(coordinator.settings, TO_REDACT),
        "routines": _redact_people(routines),
        "statuses": _redact_people(
            {r["id"]: coordinator.get_status(r["id"]) for r in routines}
        ),
        "integration_entries": sorted(
            sub.unique_id or sub.subentry_id for sub in entry.subentries.values()
        ),
    }
