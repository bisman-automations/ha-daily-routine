"""Routine model: normalisation and validation. Pure functions, no Home Assistant state."""

from __future__ import annotations

import re
from typing import Any

from .const import (
    MAX_ACTIONS,
    TRIGGER_EVERYONE_LEAVES,
    TRIGGER_PERSON_ARRIVES,
    TRIGGER_PERSON_LEAVES,
    TRIGGER_ROOM_CLEAR,
    TRIGGER_SOMEONE_ARRIVES,
    TRIGGER_STATE_IS,
    TRIGGER_TIME,
    TRIGGER_TODO_DONE,
    TRIGGER_VALUE_ABOVE,
    TRIGGER_VALUE_BELOW,
    TRIGGERS,
    WEEKDAYS,
)
from .schema import vol
from .todo import MAX_ITEM_LENGTH, normalize_todo_step

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

PRESENCE_TRIGGERS = (
    TRIGGER_EVERYONE_LEAVES,
    TRIGGER_SOMEONE_ARRIVES,
    TRIGGER_PERSON_LEAVES,
    TRIGGER_PERSON_ARRIVES,
)
# Fires when one of the chosen people leaves or arrives at the place (not only when everyone has).
PER_PERSON_TRIGGERS = (TRIGGER_PERSON_LEAVES, TRIGGER_PERSON_ARRIVES)
# Triggers that happen as things change, so they run every time (not once a day).
VALUE_TRIGGERS = (TRIGGER_VALUE_ABOVE, TRIGGER_VALUE_BELOW)
# Watch one device and run when it changes to a chosen state.
STATE_TRIGGERS = (TRIGGER_STATE_IS,)
# Watch a to-do list and run when an item on it is checked off.
TODO_DONE_TRIGGERS = (TRIGGER_TODO_DONE,)
WATCH_TRIGGERS = (*STATE_TRIGGERS, *TODO_DONE_TRIGGERS)
REPEATING_TRIGGERS = (
    *PRESENCE_TRIGGERS,
    TRIGGER_ROOM_CLEAR,
    *VALUE_TRIGGERS,
    *STATE_TRIGGERS,
    *TODO_DONE_TRIGGERS,
)


def uses_conditions(routine: dict[str, Any]) -> bool:
    """True when a run can be blocked and has to wait for things to line up."""
    return bool(routine["conditions"]) or routine["trigger"] in (
        TRIGGER_EVERYONE_LEAVES,
        TRIGGER_ROOM_CLEAR,
    )


CONDITION_KINDS = ("presence", "sensors", "state")
PRESENCE_WHO = ("everyone", "anyone")
PRESENCE_WHERE = ("in", "away")
SENSOR_MODES = ("quiet", "active")


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
                raise vol.Invalid(
                    "Delay must be a number of seconds or HH:MM:SS."
                ) from None
            try:
                nums = [int(p) for p in parts]
            except ValueError as err:
                raise vol.Invalid(
                    "Delay must be a number of seconds or HH:MM:SS."
                ) from err
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


def _item_name(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > MAX_ITEM_LENGTH:
        raise vol.Invalid("The to-do item name is too long.")
    return text


def _state_list(value: Any) -> list[str]:
    """States to watch for: a list, or text separated by commas. Duplicates are dropped."""
    items = value.split(",") if isinstance(value, str) else _as_list(value)
    states: list[str] = []
    for item in items:
        text = " ".join(str(item).split())
        if not text:
            continue
        if len(text) > 255:
            raise vol.Invalid("A state name is too long.")
        if text not in states:
            states.append(text)
    return states


def _normalize_action(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise vol.Invalid(f"Step {index} must be an object.")
    if "delay" in raw:
        return {"delay": parse_delay(raw["delay"])}
    if "todo" in raw:
        return {"todo": normalize_todo_step(raw["todo"], index)}

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


def _presence_condition(
    people: list[str], zone: str, who: str, where: str
) -> dict[str, Any]:
    return {
        "kind": "presence",
        "people": people,
        "zone": zone,
        "who": who,
        "where": where,
    }


def _sensor_condition(sensors: list[str], mode: str, minutes: int) -> dict[str, Any]:
    return {"kind": "sensors", "sensors": sensors, "mode": mode, "minutes": minutes}


def _normalize_condition(raw: Any, index: int) -> dict[str, Any]:
    """One extra condition: someone or everyone in or away from a place, sensors quiet or active, or a state."""
    if not isinstance(raw, dict):
        raise vol.Invalid(f"Condition {index} must be an object.")
    kind = str(raw.get("kind") or "")
    if kind == "presence":
        people = _entity_list(raw.get("people"), f"Condition {index}")
        if not people:
            raise vol.Invalid("Pick the people for this condition.")
        zone = str(raw.get("zone") or "zone.home")
        if not zone.startswith("zone."):
            raise vol.Invalid("Pick a place (zone) for this condition.")
        who = str(raw.get("who") or "everyone")
        where = str(raw.get("where") or "away")
        if who not in PRESENCE_WHO or where not in PRESENCE_WHERE:
            raise vol.Invalid("A presence condition is everyone or anyone, in or away.")
        return _presence_condition(people, zone, who, where)
    if kind == "sensors":
        sensors = _entity_list(raw.get("sensors"), f"Condition {index}")
        if not sensors:
            raise vol.Invalid(
                "Pick the motion or occupancy sensors for this condition."
            )
        mode = str(raw.get("mode") or "quiet")
        if mode not in SENSOR_MODES:
            raise vol.Invalid("A sensor condition is quiet or active.")
        minutes = _int(raw.get("minutes"), 0, 1440, "Sensor minutes")
        if mode == "active" and minutes < 1:
            raise vol.Invalid("'Active within' needs at least 1 minute.")
        return _sensor_condition(sensors, mode, minutes)
    if kind == "state":
        entity_id = str(raw.get("entity_id") or "").strip()
        state = str(raw.get("state") or "").strip()
        if not ENTITY_ID_RE.fullmatch(entity_id) or not state:
            raise vol.Invalid("Each extra condition needs an entity and a state.")
        return {"kind": "state", "entity_id": entity_id, "state": state}
    raise vol.Invalid(f"Condition {index}: pick what to check.")


def _conditions_from(
    raw: dict[str, Any],
    trigger: str,
    people: list[str],
    zone: str,
    sensors: list[str],
    clear_minutes: int,
) -> list[dict[str, Any]]:
    """The extra conditions. Routines saved before 1.8 used three flags; they become conditions here."""
    if raw.get("conditions") is not None:
        return [
            _normalize_condition(item, i + 1)
            for i, item in enumerate(_as_list(raw.get("conditions")))
        ]

    conditions: list[dict[str, Any]] = []
    if raw.get("require_everyone_away") and trigger != TRIGGER_EVERYONE_LEAVES:
        if not people:
            raise vol.Invalid("Pick the people who must be away.")
        conditions.append(_presence_condition(people, zone, "everyone", "away"))
    if raw.get("require_sensors_clear") and trigger != TRIGGER_ROOM_CLEAR:
        if not sensors:
            raise vol.Invalid("Pick the motion or occupancy sensors to watch.")
        conditions.append(_sensor_condition(sensors, "quiet", clear_minutes))
    for item in _as_list(raw.get("require_states")):
        if not isinstance(item, dict):
            raise vol.Invalid("Extra conditions must be objects.")
        conditions.append(
            _normalize_condition({"kind": "state", **item}, len(conditions) + 1)
        )
    return conditions


def normalize_routine(raw: Any) -> dict[str, Any]:
    """Validate a routine from the UI or storage and fill in defaults."""
    if not isinstance(raw, dict):
        raise vol.Invalid("A routine must be an object.")

    rid = str(raw.get("id") or "").strip()
    if not ROUTINE_ID_RE.fullmatch(rid):
        raise vol.Invalid(
            "Routine id may only use letters, digits, _ or - (max 64 characters)."
        )
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
    clear_minutes = _int(raw.get("clear_minutes"), 0, 1440, "Clear minutes")

    zone = raw.get("zone") or "zone.home"
    if not isinstance(zone, str) or not zone.startswith("zone."):
        raise vol.Invalid("Pick a place (zone) for this routine.")

    if trigger == TRIGGER_ROOM_CLEAR:
        if clear_minutes < 1:
            raise vol.Invalid("'Room is clear' needs at least 1 minute of quiet.")
        if not sensors:
            raise vol.Invalid("Pick the motion or occupancy sensors to watch.")
    if trigger in PRESENCE_TRIGGERS and not people:
        raise vol.Invalid("Pick at least one person for this trigger.")

    conditions = _conditions_from(raw, trigger, people, zone, sensors, clear_minutes)
    conditions_match = str(raw.get("conditions_match") or "all")
    if conditions_match not in ("all", "any"):
        raise vol.Invalid("Extra conditions must match 'all' or 'any'.")

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
        if trigger == TRIGGER_VALUE_ABOVE and raw.get("off_threshold") not in (
            None,
            "",
        ):
            off_threshold = _number(raw.get("off_threshold"), "The turn-off level")
            if off_threshold >= value_threshold:
                raise vol.Invalid("The turn-off level must be below the turn-on level.")

    watch_entity: str | None = None
    watch_states: list[str] = []
    watch_item = ""
    hold_minutes = 0
    if trigger in WATCH_TRIGGERS:
        watch_entity = str(raw.get("watch_entity") or "").strip() or None
        if trigger in TODO_DONE_TRIGGERS:
            if not watch_entity or not watch_entity.startswith("todo."):
                raise vol.Invalid("Pick the to-do list to watch.")
            watch_item = _item_name(raw.get("watch_item"))
        else:
            if not watch_entity or not ENTITY_ID_RE.fullmatch(watch_entity):
                raise vol.Invalid("Pick the device to watch.")
            watch_states = _state_list(raw.get("watch_states", raw.get("watch_state")))
            if not watch_states:
                raise vol.Invalid("Set the state to watch for.")
            hold_minutes = _int(raw.get("hold_minutes") or 0, 0, 1440, "The hold time")

    active_from = (
        normalize_time(raw.get("active_from")) if raw.get("active_from") else None
    )
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
    if not any("service" in action or "todo" in action for action in actions):
        raise vol.Invalid(
            "Add at least one service or to-do step (delays alone do nothing)."
        )

    kind = "todo" if raw.get("kind") == "todo" else "routine"
    if kind == "todo" and (
        trigger not in STATE_TRIGGERS or len(actions) != 1 or "todo" not in actions[0]
    ):
        raise vol.Invalid("A to-do rule watches one device and does one to-do step.")

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
        "kind": kind,
        "watch_entity": watch_entity,
        "watch_states": watch_states,
        "hold_minutes": hold_minutes,
        "watch_item": watch_item,
        "active_from": active_from,
        "active_to": active_to,
        "companion_of": companion_of,
        "time": normalize_time(raw.get("time") or "07:00:00"),
        "offset_minutes": _int(raw.get("offset_minutes"), -720, 720, "Offset"),
        "days": days,
        # Trigger settings: only the trigger that uses them keeps them.
        "people": people if trigger in PRESENCE_TRIGGERS else [],
        "zone": zone,
        "sensors": sensors if trigger == TRIGGER_ROOM_CLEAR else [],
        "clear_minutes": clear_minutes if trigger == TRIGGER_ROOM_CLEAR else 0,
        "conditions": conditions,
        "conditions_match": conditions_match,
        "repeat": bool(raw.get("repeat", trigger in REPEATING_TRIGGERS)),
        "catch_up_minutes": _int(
            raw.get("catch_up_minutes"), 0, MAX_CATCH_UP_MINUTES, "Catch-up"
        ),
        "retries": _int(raw.get("retries"), 0, MAX_RETRIES, "Retries"),
        "skip_noop": bool(raw.get("skip_noop", True)),
        "notify_on_run": bool(raw.get("notify_on_run", False)),
        "notify_on_skip": bool(raw.get("notify_on_skip", False)),
        "notify_on_error": bool(raw.get("notify_on_error", True)),
        "notify_on_health": bool(raw.get("notify_on_health", False)),
        "wait_when_unavailable": bool(raw.get("wait_when_unavailable", False)),
        "actions": actions,
    }
