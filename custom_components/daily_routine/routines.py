"""Routine model: normalisation and validation. Pure functions, no Home Assistant state."""

from __future__ import annotations

import re
from typing import Any

from .const import (
    MAX_ACTIONS,
    TRIGGER_EVERYONE_LEAVES,
    TRIGGER_ROOM_CLEAR,
    TRIGGER_SOMEONE_ARRIVES,
    TRIGGER_TIME,
    TRIGGER_VALUE_ABOVE,
    TRIGGER_VALUE_BELOW,
    TRIGGERS,
    WEEKDAYS,
)
from .schema import vol

ROUTINE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
SERVICE_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")
ENTITY_ID_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9_]+$")
MAX_CATCH_UP_MINUTES = 180
MAX_RETRIES = 3
MAX_DELAY_SECONDS = 86400

# Services that put entities into a known state. A step whose entities are already
# in that state is skipped when "skip no-op steps" is on.
DESIRED_STATE: dict[str, str] = {
    "light.turn_on": "on",
    "light.turn_off": "off",
    "switch.turn_on": "on",
    "switch.turn_off": "off",
    "fan.turn_on": "on",
    "fan.turn_off": "off",
    "cover.open_cover": "open",
    "cover.close_cover": "closed",
    "lock.lock": "locked",
    "lock.unlock": "unlocked",
}

PRESENCE_TRIGGERS = (TRIGGER_EVERYONE_LEAVES, TRIGGER_SOMEONE_ARRIVES)
# Triggers that happen as things change, so they run every time (not once a day).
VALUE_TRIGGERS = (TRIGGER_VALUE_ABOVE, TRIGGER_VALUE_BELOW)
REPEATING_TRIGGERS = (
    TRIGGER_EVERYONE_LEAVES,
    TRIGGER_SOMEONE_ARRIVES,
    TRIGGER_ROOM_CLEAR,
    *VALUE_TRIGGERS,
)


def uses_conditions(routine: dict[str, Any]) -> bool:
    return bool(
        routine["require_everyone_away"]
        or routine["require_sensors_clear"]
        or routine["require_states"]
    )


def normalize_time(value: Any) -> str:
    """Return HH:MM:SS for HH:MM or HH:MM:SS input."""
    if not isinstance(value, str):
        raise vol.Invalid("Time must be HH:MM or HH:MM:SS.")
    parts = value.strip().split(":")
    try:
        if len(parts) not in (2, 3):
            raise ValueError
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
        if not (0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60):
            raise ValueError
    except ValueError as err:
        raise vol.Invalid("Time must be HH:MM or HH:MM:SS.") from err
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def parse_delay(value: Any) -> float:
    """Delay as seconds (number), 'SS', 'MM:SS' or 'HH:MM:SS'."""
    if isinstance(value, bool):
        raise vol.Invalid("Delay must be a number of seconds or HH:MM:SS.")
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value).strip()
        try:
            seconds = float(text)
        except ValueError:
            parts = text.split(":")
            if len(parts) not in (2, 3):
                raise vol.Invalid("Delay must be a number of seconds or HH:MM:SS.") from None
            try:
                nums = [int(p) for p in parts]
            except ValueError as err:
                raise vol.Invalid("Delay must be a number of seconds or HH:MM:SS.") from err
            seconds = (
                float(nums[0] * 60 + nums[1])
                if len(nums) == 2
                else float(nums[0] * 3600 + nums[1] * 60 + nums[2])
            )
    if seconds < 0 or seconds > MAX_DELAY_SECONDS:
        raise vol.Invalid("Delay must be between 0 and 86400 seconds.")
    return seconds


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return value
    raise vol.Invalid("Expected a list.")


def _entity_list(value: Any, label: str) -> list[str]:
    result: list[str] = []
    for item in _as_list(value):
        entity_id = str(item).strip()
        if not ENTITY_ID_RE.fullmatch(entity_id):
            raise vol.Invalid(f"{label}: '{entity_id}' is not an entity id.")
        if entity_id not in result:
            result.append(entity_id)
    return result


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise vol.Invalid(f"{label} must be a number.")
    try:
        return float(value)
    except (TypeError, ValueError) as err:
        raise vol.Invalid(f"{label} must be a number.") from err


def _int(value: Any, low: int, high: int, label: str) -> int:
    if isinstance(value, bool):
        raise vol.Invalid(f"{label} must be a whole number.")
    try:
        number = int(value or 0)
    except (TypeError, ValueError) as err:
        raise vol.Invalid(f"{label} must be a whole number.") from err
    if not low <= number <= high:
        raise vol.Invalid(f"{label} must be between {low} and {high}.")
    return number


def _normalize_action(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise vol.Invalid(f"Step {index} must be an object.")
    if "delay" in raw:
        return {"delay": parse_delay(raw["delay"])}

    service = str(raw.get("service") or "").strip()
    if not SERVICE_RE.fullmatch(service):
        raise vol.Invalid(f"Step {index}: service must look like domain.service.")

    target_raw = raw.get("target") or {}
    if not isinstance(target_raw, dict):
        raise vol.Invalid(f"Step {index}: target must be an object.")
    target: dict[str, Any] = {}
    entity_ids = _entity_list(target_raw.get("entity_id"), f"Step {index}")
    if entity_ids:
        target["entity_id"] = entity_ids
    area_ids = [str(a) for a in _as_list(target_raw.get("area_id"))]
    if area_ids:
        target["area_id"] = area_ids

    data = {} if raw.get("data") is None else raw["data"]
    if not isinstance(data, dict):
        raise vol.Invalid(f"Step {index}: data must be an object.")

    action: dict[str, Any] = {"service": service, "data": dict(data)}
    if target:
        action["target"] = target
    return action


def normalize_routine(raw: Any) -> dict[str, Any]:
    """Validate a routine from the UI or storage and fill in defaults."""
    if not isinstance(raw, dict):
        raise vol.Invalid("A routine must be an object.")

    rid = str(raw.get("id") or "").strip()
    if not ROUTINE_ID_RE.fullmatch(rid):
        raise vol.Invalid("Routine id may only use letters, digits, _ or - (max 64 characters).")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise vol.Invalid("Give the routine a name.")

    trigger = raw.get("trigger") or TRIGGER_TIME
    if trigger not in TRIGGERS:
        raise vol.Invalid(f"Unknown trigger '{trigger}'.")

    room = raw.get("room") or None
    if room is not None and not isinstance(room, str):
        raise vol.Invalid("Room must be an area id.")

    days_raw = raw.get("days")
    days = list(WEEKDAYS) if days_raw is None else [str(d) for d in _as_list(days_raw)]
    if not days:
        raise vol.Invalid("Pick at least one day.")
    if any(day not in WEEKDAYS for day in days):
        raise vol.Invalid("Days must be mon, tue, wed, thu, fri, sat or sun.")
    days = [day for day in WEEKDAYS if day in days]

    people = _entity_list(raw.get("people"), "People")
    sensors = _entity_list(raw.get("sensors"), "Sensors")
    require_everyone_away = bool(raw.get("require_everyone_away", False))
    require_sensors_clear = bool(raw.get("require_sensors_clear", False))
    clear_minutes = _int(raw.get("clear_minutes"), 0, 1440, "Clear minutes")

    if trigger == TRIGGER_EVERYONE_LEAVES:
        require_everyone_away = True
    if trigger == TRIGGER_ROOM_CLEAR:
        require_sensors_clear = True
        if clear_minutes < 1:
            raise vol.Invalid("'Room is clear' needs at least 1 minute of quiet.")
    if trigger in PRESENCE_TRIGGERS and not people:
        raise vol.Invalid("Pick at least one person for this trigger.")
    if require_everyone_away and not people:
        raise vol.Invalid("Pick the people who must be away.")
    if require_sensors_clear and not sensors:
        raise vol.Invalid("Pick the motion or occupancy sensors to watch.")

    require_states: list[dict[str, str]] = []
    for item in _as_list(raw.get("require_states")):
        if not isinstance(item, dict):
            raise vol.Invalid("Extra conditions must be objects.")
        entity_id = str(item.get("entity_id") or "").strip()
        state = str(item.get("state") or "").strip()
        if not ENTITY_ID_RE.fullmatch(entity_id) or not state:
            raise vol.Invalid("Each extra condition needs an entity and a state.")
        require_states.append({"entity_id": entity_id, "state": state})

    device_raw = raw.get("device_id")
    if device_raw is not None and not isinstance(device_raw, str):
        raise vol.Invalid("The linked device must be a device id.")
    device_id = (device_raw or "").strip() or None

    value_entity = str(raw.get("value_entity") or "").strip() or None
    value_threshold: float | None = None
    off_threshold: float | None = None
    if trigger in VALUE_TRIGGERS:
        if (
            not value_entity
            or not ENTITY_ID_RE.fullmatch(value_entity)
            or not value_entity.startswith("sensor.")
        ):
            raise vol.Invalid("Pick the sensor to watch.")
        if raw.get("value_threshold") in (None, ""):
            raise vol.Invalid("Set the level to watch for.")
        value_threshold = _number(raw.get("value_threshold"), "The level")
        if trigger == TRIGGER_VALUE_ABOVE and raw.get("off_threshold") not in (None, ""):
            off_threshold = _number(raw.get("off_threshold"), "The turn-off level")
            if off_threshold >= value_threshold:
                raise vol.Invalid("The turn-off level must be below the turn-on level.")

    active_from = normalize_time(raw.get("active_from")) if raw.get("active_from") else None
    active_to = normalize_time(raw.get("active_to")) if raw.get("active_to") else None
    if bool(active_from) != bool(active_to):
        raise vol.Invalid("Set both ends of 'only run between', or neither.")

    companion_of = str(raw.get("companion_of") or "").strip() or None

    actions_raw = _as_list(raw.get("actions"))
    if not actions_raw:
        raise vol.Invalid("Add at least one step.")
    if len(actions_raw) > MAX_ACTIONS:
        raise vol.Invalid(f"A routine can have at most {MAX_ACTIONS} steps.")
    actions = [_normalize_action(item, i + 1) for i, item in enumerate(actions_raw)]
    if not any("service" in action for action in actions):
        raise vol.Invalid("Add at least one service step (delays alone do nothing).")

    return {
        "id": rid,
        "name": name,
        "enabled": bool(raw.get("enabled", True)),
        "room": room,
        "device_id": device_id,
        "trigger": trigger,
        "value_entity": value_entity,
        "value_threshold": value_threshold,
        "off_threshold": off_threshold,
        "active_from": active_from,
        "active_to": active_to,
        "companion_of": companion_of,
        "time": normalize_time(raw.get("time") or "07:00:00"),
        "offset_minutes": _int(raw.get("offset_minutes"), -720, 720, "Offset"),
        "days": days,
        "people": people,
        "require_everyone_away": require_everyone_away,
        "sensors": sensors,
        "require_sensors_clear": require_sensors_clear,
        "clear_minutes": clear_minutes,
        "require_states": require_states,
        "repeat": bool(raw.get("repeat", trigger in REPEATING_TRIGGERS)),
        "catch_up_minutes": _int(raw.get("catch_up_minutes"), 0, MAX_CATCH_UP_MINUTES, "Catch-up"),
        "retries": _int(raw.get("retries"), 0, MAX_RETRIES, "Retries"),
        "skip_noop": bool(raw.get("skip_noop", True)),
        "notify_on_run": bool(raw.get("notify_on_run", False)),
        "notify_on_skip": bool(raw.get("notify_on_skip", False)),
        "notify_on_error": bool(raw.get("notify_on_error", True)),
        "actions": actions,
    }
