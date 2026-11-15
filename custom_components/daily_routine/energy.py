"""How much power a routine's devices use before and after a run.

Before a run, the power sensors on the devices the routine changes are read
(sensors with device class power on the same device). A minute after it
finishes they're read again, so a "Goodnight" can say it took the house from
640 W to 85 W. Devices without a power sensor aren't counted.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

# Readings kept per routine.
KEEP_RUNS = 20
# How long after a run the power is read again (devices settle).
SETTLE_SECONDS = 60
_SCALE = {"W": 1.0, "kW": 1000.0, "mW": 0.001, "MW": 1_000_000.0}


def power_sensors(hass: HomeAssistant, entity_ids: list[str]) -> list[str]:
    """Power sensors on the devices of these entities (or the entities themselves)."""
    registry = er.async_get(hass)
    devices = set()
    found: list[str] = []
    for entity_id in entity_ids:
        entry = registry.async_get(entity_id)
        if entry is not None and entry.device_id:
            devices.add(entry.device_id)
        elif _is_power(hass, entity_id):
            found.append(entity_id)
    for device_id in sorted(devices):
        for entry in er.async_entries_for_device(registry, device_id):
            if entry.domain == "sensor" and _is_power(hass, entry.entity_id):
                found.append(entry.entity_id)
    return list(dict.fromkeys(found))


def _is_power(hass: HomeAssistant, entity_id: str) -> bool:
    state = hass.states.get(entity_id)
    return (
        state is not None
        and state.domain == "sensor"
        and state.attributes.get("device_class") == SensorDeviceClass.POWER
    )


def read_watts(hass: HomeAssistant, sensors: list[str]) -> float | None:
    """The sensors' power added up in watts. None when none can be read."""
    total = 0.0
    read = False
    for entity_id in sensors:
        state = hass.states.get(entity_id)
        if state is None:
            continue
        scale = _SCALE.get(str(state.attributes.get("unit_of_measurement") or "W"))
        if scale is None:
            continue
        try:
            total += float(state.state) * scale
        except TypeError, ValueError:
            continue
        read = True
    return round(total, 1) if read else None


def summary(readings: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The last reading and the average change, for the routine's card."""
    if not readings:
        return None
    changes = [r["after"] - r["before"] for r in readings]
    return {
        "last": readings[-1],
        "average_change": round(sum(changes) / len(changes), 1),
        "runs": len(readings),
    }
