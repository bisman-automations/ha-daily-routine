"""Undo: how things were just before a routine ran, and putting them back."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant

# What Undo can put back. Other devices (speakers, scripts) are left alone.
UNDO_DOMAINS = ("light", "switch", "fan", "cover", "lock", "climate", "input_boolean")
# Undo is offered for this long after a run.
UNDO_FOR = timedelta(hours=2)
LIGHT_KEYS = ("brightness", "color_temp_kelvin", "hs_color")

# (domain, service, data, target) -> error or None
CallService = Callable[
    [str, str, dict[str, Any], dict[str, Any] | None], Awaitable[BaseException | None]
]


def snapshot(hass: HomeAssistant, entity_ids: list[str]) -> dict[str, dict[str, Any]]:
    """The state of each device a routine is about to change."""
    kept: dict[str, dict[str, Any]] = {}
    for entity_id in sorted(set(entity_ids)):
        domain = entity_id.split(".", 1)[0]
        state = hass.states.get(entity_id)
        if domain not in UNDO_DOMAINS or state is None:
            continue
        if state.state in ("unknown", "unavailable"):
            continue
        attrs: dict[str, Any] = {}
        if domain == "light" and state.state == "on":
            mode = state.attributes.get("color_mode")
            for key in LIGHT_KEYS:
                value = state.attributes.get(key)
                if value is None:
                    continue
                if key == "color_temp_kelvin" and mode != "color_temp":
                    continue
                if key == "hs_color" and mode in ("color_temp", "brightness", "onoff"):
                    continue
                attrs[key] = list(value) if isinstance(value, tuple) else value
        elif domain == "cover":
            position = state.attributes.get("current_position")
            if position is not None:
                attrs["position"] = position
        elif domain == "climate":
            for key in ("temperature", "target_temp_low", "target_temp_high"):
                if state.attributes.get(key) is not None:
                    attrs[key] = state.attributes[key]
        kept[entity_id] = {"state": state.state, "attrs": attrs}
    return kept


def _call_for(
    entity_id: str, saved: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    """The service calls that put one device back."""
    domain = entity_id.split(".", 1)[0]
    state = saved["state"]
    attrs = saved.get("attrs") or {}
    if domain in ("light", "switch", "fan", "input_boolean"):
        if state == "on":
            return [("turn_on", dict(attrs) if domain == "light" else {})]
        if state == "off":
            return [("turn_off", {})]
        return []
    if domain == "cover":
        if "position" in attrs:
            return [("set_cover_position", {"position": attrs["position"]})]
        if state in ("open", "opening"):
            return [("open_cover", {})]
        if state in ("closed", "closing"):
            return [("close_cover", {})]
        return []
    if domain == "lock":
        if state == "locked":
            return [("lock", {})]
        if state == "unlocked":
            return [("unlock", {})]
        return []
    if domain == "climate":
        calls = [("set_hvac_mode", {"hvac_mode": state})]
        if "temperature" in attrs:
            calls.append(("set_temperature", {"temperature": attrs["temperature"]}))
        elif "target_temp_low" in attrs and "target_temp_high" in attrs:
            calls.append(
                (
                    "set_temperature",
                    {
                        "target_temp_low": attrs["target_temp_low"],
                        "target_temp_high": attrs["target_temp_high"],
                    },
                )
            )
        return calls
    return []


async def restore(
    hass: HomeAssistant, saved: dict[str, dict[str, Any]], call: CallService
) -> list[str]:
    """Put every device back; returns the ones that couldn't be."""
    failed: list[str] = []
    for entity_id, before in saved.items():
        current = hass.states.get(entity_id)
        if current is None:
            continue  # removed since
        domain = entity_id.split(".", 1)[0]
        for service, data in _call_for(entity_id, before):
            if domain == "cover" and service == "set_cover_position":
                # Not every cover can move to a position: open or close instead.
                error = await call(domain, service, data, {"entity_id": [entity_id]})
                if error is None:
                    continue
                service = "open_cover" if data["position"] > 0 else "close_cover"
                data = {}
            error = await call(domain, service, data, {"entity_id": [entity_id]})
            if error is not None:
                failed.append(entity_id)
                break
    return failed
