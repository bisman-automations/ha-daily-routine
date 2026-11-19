"""Diagnostics download: routines, their status and settings, for fixing problems.

What's left out (1.34.1 widened this):

- Keys that hold secrets or personal details, wherever they sit: the guest
  Wi-Fi network and password, phone names, hidden profiles, and any code, PIN,
  password or token a step passes to a service (an alarm or lock ``code``).
- Person, phone (``notify.``) and tracker (``device_tracker.``) ids,
  however deep they sit, in the routines, their statuses and the settings
  (the daily summary and weekly report can name phones too).

The routines themselves (steps, times, conditions, messages) stay, since
that's what fixing a problem needs.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .runtime import get_coordinator

REDACTED = "**REDACTED**"
# Keys whose values are replaced wherever they appear, at any depth.
TO_REDACT = frozenset(
    {
        # Settings: phone names, hidden profiles and the guest Wi-Fi card.
        "notify_services",
        "hidden_people",
        "guest_wifi",
        "ssid",
        "password",
        # Service data a step may carry (an alarm panel or lock code, a
        # webhook or an API key for a REST command).
        "code",
        "pin",
        "passcode",
        "token",
        "access_token",
        "api_key",
        "webhook_id",
    }
)
# Routines name people and phones in their people, condition and step targets.
# Those ids are replaced wherever they appear, however deep.
PRIVATE_PREFIXES = ("person.", "notify.", "device_tracker.")


def _redact(value: Any) -> Any:
    """``value`` with ``TO_REDACT`` keys and private ids replaced, at any depth."""
    if isinstance(value, dict):
        return {
            key: REDACTED
            if isinstance(key, str) and key.lower() in TO_REDACT and item
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
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
        "settings": _redact(coordinator.settings),
        "routines": _redact(routines),
        "statuses": _redact(
            {r["id"]: coordinator.get_status(r["id"]) for r in routines}
        ),
        "integration_entries": sorted(
            sub.unique_id or sub.subentry_id for sub in entry.subentries.values()
        ),
    }
