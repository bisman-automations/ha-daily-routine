"""Migrate v1.0.0 config entries (one per routine, or a JSON option) into storage."""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import LEGACY_CONF_ROUTINE_ID, LEGACY_CONF_ROUTINES_JSON, WEEKDAYS

_LOGGER = logging.getLogger(__name__)


def _legacy_entry_to_raw(entry: ConfigEntry) -> dict[str, Any]:
    options = entry.options
    rid = entry.data[LEGACY_CONF_ROUTINE_ID]
    actions: list[dict[str, Any]] = []
    for n in (1, 2, 3):
        service = (options.get(f"action_{n}_service") or "").strip()
        entities = options.get(f"action_{n}_entities") or []
        delay = options.get(f"action_{n}_delay") or 0
        if service:
            action: dict[str, Any] = {"service": service}
            if entities:
                action["target"] = {"entity_id": list(entities)}
            actions.append(action)
        if n < 3 and delay:
            actions.append({"delay": float(delay)})
    return {
        "id": rid,
        "name": options.get("name") or entry.title or rid,
        "enabled": options.get("enabled", True),
        "room": None,
        "trigger": options.get("trigger") or "time",
        "time": options.get("time") or "07:00:00",
        "offset_minutes": int(options.get("offset_minutes") or 0),
        "days": list(options.get("days") or WEEKDAYS),
        "require_everyone_away": bool(options.get("require_everyone_away", False)),
        "people": list(options.get("people") or []),
        "require_sensors_clear": bool(options.get("require_sensors_clear", False)),
        "sensors": list(options.get("sensors") or []),
        "clear_minutes": int(options.get("clear_minutes") or 0),
        "notify_on_run": bool(options.get("notify_on_run", False)),
        "notify_on_skip": bool(options.get("notify_on_skip", False)),
        "actions": actions,
    }


def legacy_routines(entries: list[ConfigEntry]) -> list[dict[str, Any]]:
    """Raw routine dicts from every v1.0.0-style entry, including the JSON option."""
    raws: list[dict[str, Any]] = []
    for entry in entries:
        if entry.data.get(LEGACY_CONF_ROUTINE_ID):
            raws.append(_legacy_entry_to_raw(entry))
        elif entry.options.get(LEGACY_CONF_ROUTINES_JSON):
            try:
                parsed = json.loads(str(entry.options[LEGACY_CONF_ROUTINES_JSON]))
            except json.JSONDecodeError:
                _LOGGER.error(
                    "Legacy routines JSON in %s is not valid; skipped.", entry.title
                )
                continue
            if isinstance(parsed, list):
                raws.extend(item for item in parsed if isinstance(item, dict))
    return raws


def is_legacy(entry: ConfigEntry) -> bool:
    return bool(
        entry.data.get(LEGACY_CONF_ROUTINE_ID)
        or entry.options.get(LEGACY_CONF_ROUTINES_JSON)
    )
