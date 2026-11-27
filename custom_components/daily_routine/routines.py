"""Routine model: normalisation and validation. Pure functions, no Home Assistant state."""

from __future__ import annotations

from datetime import date
import re
from typing import Any

from homeassistant.util import dt as dt_util

from .battery import DEFAULT_BELOW
from .checklist import CHECKABLE, MAX_CHECKED
from .const import (
    MAX_ACTIONS,
    MAX_RANDOM_MINUTES,
    TRIGGER_ALARM,
    TRIGGER_BUTTON,
    TRIGGER_CALENDAR_EVENT,
    TRIGGER_CHEAPEST,
    TRIGGER_EVERYONE_LEAVES,
    TRIGGER_FORECAST,
    TRIGGER_MODE,
    TRIGGER_PERSON_ARRIVES,
    TRIGGER_PERSON_LEAVES,
    TRIGGER_ROOM_CLEAR,
    TRIGGER_SOMEONE_ARRIVES,
    TRIGGER_STATE_IS,
    TRIGGER_SUNRISE,
    TRIGGER_SUNSET,
    TRIGGER_TAG,
    TRIGGER_TIME,
    TRIGGER_TODO_DONE,
    TRIGGER_VALUE_ABOVE,
    TRIGGER_VALUE_BELOW,
    TRIGGERS,
    WEEKDAYS,
)
from .schema import vol
from .todo import MAX_ITEM_LENGTH, normalize_todo_step
from .weather_shift import clean_hot_shift

ROUTINE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
SERVICE_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")
ENTITY_ID_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9_]+$")
MAX_CATCH_UP_MINUTES = 180
MAX_RETRIES = 3
MAX_DELAY_SECONDS = 86400
MAX_GROUP_NAME = 40

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

# Curtains and blinds that report a position count as closed up to this many
# percent open: many stop at 1 to 3 % when closed, and Home Assistant then calls
# them "open", so "Open" was skipped as already done and "Close" looked failed.
COVER_CLOSED_POSITION = 5


# Open and close steps for covers can set their own limits (closed_at,
# open_at); these are the steps that take them.
COVER_STEPS = ("cover.open_cover", "cover.close_cover")


def cover_is(
    state: str,
    position: Any,
    expected: str,
    closed_at: int = COVER_CLOSED_POSITION,
    open_at: int | None = None,
) -> bool | None:
    """Whether an open or closed cover shows ``expected`` ("open" or "closed"),
    judged by its position when it reports one; None when the position doesn't
    decide (no position, moving, unknown, or another expected state).

    It counts as closed at ``closed_at`` percent open or less, and as open at
    ``open_at`` or more (by default, anything that isn't closed)."""
    if expected not in ("open", "closed") or state not in ("open", "closed"):
        return None
    if isinstance(position, bool) or not isinstance(position, (int, float)):
        return None
    if expected == "closed":
        return position <= closed_at
    return position >= open_at if open_at is not None else position > closed_at


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
# Triggers planned ahead from what another entity says (an alarm, a calendar
# event, electricity prices), like a time trigger.
PLANNED_TRIGGERS = (TRIGGER_ALARM, TRIGGER_CALENDAR_EVENT, TRIGGER_CHEAPEST)
# A tag scanned or a button pressed: a moment, run each time.
PRESS_TRIGGERS = (TRIGGER_TAG, TRIGGER_BUTTON)
REPEATING_TRIGGERS = (
    TRIGGER_MODE,
    *PRESS_TRIGGERS,
    TRIGGER_FORECAST,
    *PRESENCE_TRIGGERS,
    TRIGGER_ROOM_CLEAR,
    *VALUE_TRIGGERS,
    *STATE_TRIGGERS,
    *TODO_DONE_TRIGGERS,
)


def uses_conditions(routine: dict[str, Any]) -> bool:
    """True when a run can be blocked and has to wait for things to line up."""
    return (
        bool(routine["conditions"])
        or routine["trigger"] in (TRIGGER_EVERYONE_LEAVES, TRIGGER_ROOM_CLEAR)
        or any(
            t["trigger"] == TRIGGER_EVERYONE_LEAVES
            for t in routine.get("more_triggers") or []
        )
    )


# "Or also when": more triggers for the same routine. Each keeps its own
# settings below; the days, hours, conditions and steps are the routine's.
MAX_MORE_TRIGGERS = 4
MORE_TRIGGER_KINDS = (
    TRIGGER_TIME,
    "sunrise",
    "sunset",
    TRIGGER_EVERYONE_LEAVES,
    TRIGGER_SOMEONE_ARRIVES,
    TRIGGER_PERSON_LEAVES,
    TRIGGER_PERSON_ARRIVES,
    TRIGGER_VALUE_ABOVE,
    TRIGGER_VALUE_BELOW,
    TRIGGER_STATE_IS,
    TRIGGER_SUNRISE,
    TRIGGER_SUNSET,
    TRIGGER_MODE,
    TRIGGER_ALARM,
    TRIGGER_CALENDAR_EVENT,
    TRIGGER_FORECAST,
    TRIGGER_CHEAPEST,
    TRIGGER_TAG,
    TRIGGER_BUTTON,
)
TRIGGER_FIELDS = (
    "trigger",
    "time",
    "offset_minutes",
    "people",
    "zone",
    "value_entity",
    "value_threshold",
    "watch_entity",
    "watch_states",
    "hold_minutes",
    "modes",
    "watch_item",
    "forecast_hours",
)


# When a scheduled run's conditions aren't met: wait for them until the end of
# the day, wait until a time, or skip the run.
WAIT_MODES = ("day", "until", "minutes", "no")

CONDITION_KINDS = (
    "presence",
    "sensors",
    "state",
    "calendar",
    "mode",
    "workday",
    "weather",
    "day_type",
    "forecast",
    "charging",
    "value",
)
# What counts as charging, for phones' battery state and charging sensors.
CHARGING_STATES = ("on", "charging", "full")
MAX_SKIP_DATES = 31
FORECAST_WHEN = ("coming", "not_coming")
WORKDAY_WHEN = ("workday", "day_off")
# The weather: what it's doing (rainy, sunny...), or the temperature.
WEATHER_TESTS = ("is", "is_not", "above", "below")
WEATHER_STATES = (
    "clear-night",
    "cloudy",
    "exceptional",
    "fog",
    "hail",
    "lightning",
    "lightning-rainy",
    "partlycloudy",
    "pouring",
    "rainy",
    "snowy",
    "snowy-rainy",
    "sunny",
    "windy",
    "windy-variant",
)
# A calendar condition: run only on days with a matching event, or skip those days.
CALENDAR_WHEN = ("event", "no_event")
# Kinds of day (School day, Holiday…) come from calendars in Settings; a day
# with none of them is a "Normal day".
NORMAL_DAY = "Normal day"
MAX_DAY_TYPE_NAME = 40
MAX_DAY_TYPES = 8
MAX_CALENDAR_MATCH = 100
PRESENCE_WHO = ("everyone", "anyone")
PRESENCE_WHERE = ("in", "away")
SENSOR_MODES = ("quiet", "active")


def _clock(value: Any, label: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        return normalize_time(value)
    except vol.Invalid as err:
        raise vol.Invalid(f"{label}: {err}") from err


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


MAX_HEADS_UP_MINUTES = 240
_MONTH_DAY = re.compile(r"^(\d{2})-(\d{2})$")


def _month_day(value: Any, label: str) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if len(text) == 10 and text[4] == "-":
        text = text[5:]  # a whole date: only the month and day count
    match = _MONTH_DAY.match(text)
    if not match:
        raise vol.Invalid(f"{label} must be a month and day, like 11-01.")
    try:
        date(2024, int(match[1]), int(match[2]))  # 2024: 29 February is allowed
    except ValueError as err:
        raise vol.Invalid(f"{label} isn't a real day.") from err
    return text


def _dates(start: Any, end: Any) -> dict[str, str | None]:
    first = _month_day(start, "From")
    last = _month_day(end, "Until")
    if (first is None) != (last is None):
        raise vol.Invalid("Choose both the first and the last day, or neither.")
    return {"from_date": first, "until_date": last}


def in_dates(day: date, first: str | None, last: str | None) -> bool:
    """Whether a day is within a yearly range ("MM-DD", either may wrap the new year)."""
    if not first or not last:
        return True
    today = f"{day.month:02d}-{day.day:02d}"
    if first <= last:
        return first <= today <= last
    return today >= first or today <= last


ON_STEP_ERROR = ("stop", "continue")
DEFAULT_RETRY_LATER_MINUTES = 5
MAX_RETRY_LATER_MINUTES = 120


def _on_step_error(value: Any) -> str:
    value = value or "stop"
    if value not in ON_STEP_ERROR:
        raise vol.Invalid("If a step fails must be stop or continue.")
    return str(value)


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


# Wait until a device shows a state: for up to this many minutes.
MAX_WAIT_UNTIL_MINUTES = 240
WAIT_TIMEOUT = ("continue", "stop")
# House modes a routine can run in, and a step can switch to.
MODES = ("home", "away", "night", "guests", "vacation")


def _normalize_action(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise vol.Invalid(f"Step {index} must be an object.")
    if "delay" in raw:
        return {"delay": parse_delay(raw["delay"])}
    action = _normalize_step(raw, index)
    only_if = raw.get("only_if")
    if only_if:
        try:
            action["only_if"] = _normalize_condition(only_if, index)
        except vol.Invalid as err:
            raise vol.Invalid(f"Step {index}, only if: {err}") from err
    return action


# Steps run at the same time: up to this many in one "together" step.
MAX_TOGETHER = 10
TOGETHER_KINDS = {
    "service",
    "todo",
    "run_routine",
    "set_mode",
    "volume_ramp",
    "briefing",
}
# A step on rooms can be limited to the rooms someone is in, or the empty ones.
ROOM_FILTERS = ("occupied", "empty")
MAX_RAMP_MINUTES = 60
# A step can wait for the cheapest electricity, up to 12 hours.
MAX_CHEAPER_MINUTES = 720


def _normalize_step(raw: dict[str, Any], index: int) -> dict[str, Any]:
    if "together" in raw:
        items = _as_list(raw.get("together"))
        if not 2 <= len(items) <= MAX_TOGETHER:
            raise vol.Invalid(
                f"Step {index}: run 2 to {MAX_TOGETHER} things at the same time."
            )
        together = []
        for item in items:
            if not isinstance(item, dict) or not set(item) & TOGETHER_KINDS:
                raise vol.Invalid(
                    f"Step {index}: only actions, to-dos, routines and house modes "
                    "can run at the same time."
                )
            together.append(_normalize_action(item, index))
        return {"together": together}
    if "todo" in raw:
        return {"todo": normalize_todo_step(raw["todo"], index)}
    if "run_routine" in raw:
        other = str(raw.get("run_routine") or "").strip()
        if not ROUTINE_ID_RE.fullmatch(other):
            raise vol.Invalid(f"Step {index}: pick the routine to run.")
        return {"run_routine": other}
    if "set_mode" in raw:
        mode = str(raw.get("set_mode") or "")
        if mode not in MODES:
            raise vol.Invalid(f"Step {index}: pick the house mode.")
        return {"set_mode": mode}
    if "briefing" in raw:
        brief = raw.get("briefing")
        if not isinstance(brief, dict):
            raise vol.Invalid(f"Step {index}: pick the voice and the speaker.")
        voice = str(brief.get("voice") or "").strip()
        speaker = str(brief.get("speaker") or "").strip()
        if not voice.startswith("tts.") or not ENTITY_ID_RE.fullmatch(voice):
            raise vol.Invalid(f"Step {index}: pick the voice.")
        if not speaker.startswith("media_player.") or not ENTITY_ID_RE.fullmatch(
            speaker
        ):
            raise vol.Invalid(f"Step {index}: pick the speaker.")
        weather = str(brief.get("weather") or "").strip() or None
        if weather and not weather.startswith("weather."):
            raise vol.Invalid(f"Step {index}: pick the weather.")
        calendars = _entity_list(brief.get("calendars"), f"Step {index}")
        if any(not c.startswith("calendar.") for c in calendars):
            raise vol.Invalid(f"Step {index}: pick calendars.")
        return {
            "briefing": {
                "voice": voice,
                "speaker": speaker,
                "weather": weather,
                "calendars": calendars,
                "day_type": bool(brief.get("day_type", True)),
            }
        }
    if "check_battery" in raw:
        check = raw.get("check_battery")
        if not isinstance(check, dict):
            check = {}
        try:
            level = int(check.get("below", DEFAULT_BELOW))
        except (TypeError, ValueError) as err:
            raise vol.Invalid(f"Step {index}: give the battery level.") from err
        if not 1 <= level <= 99:
            raise vol.Invalid(f"Step {index}: the battery level is 1 to 99%.")
        return {"check_battery": {"below": level}}
    if "tick_list" in raw:
        ticks = raw.get("tick_list")
        if not isinstance(ticks, dict):
            raise vol.Invalid(f"Step {index}: list the things to tick off.")
        items = [
            " ".join(str(i).split())[:60]
            for i in ticks.get("items") or []
            if str(i or "").strip()
        ]
        items = list(dict.fromkeys(items))
        if not 1 <= len(items) <= MAX_TICKS:
            raise vol.Invalid(f"Step {index}: 1 to {MAX_TICKS} things to tick off.")
        try:
            minutes = int(ticks.get("minutes", 30))
        except (TypeError, ValueError) as err:
            raise vol.Invalid(f"Step {index}: give the minutes to wait.") from err
        if not 1 <= minutes <= 240:
            raise vol.Invalid(f"Step {index}: wait 1 to 240 minutes.")
        timeout = ticks.get("on_timeout") or "continue"
        if timeout not in ("continue", "stop"):
            raise vol.Invalid(f"Step {index}: go on or stop when time's up.")
        return {
            "tick_list": {"items": items, "minutes": minutes, "on_timeout": timeout}
        }
    if "check_open" in raw:
        check = raw.get("check_open")
        if not isinstance(check, dict):
            raise vol.Invalid(f"Step {index}: pick the doors and windows to check.")
        entities = _entity_list(check.get("entity_id"), f"Step {index}")
        if not entities:
            raise vol.Invalid(f"Step {index}: pick the doors and windows to check.")
        if len(entities) > MAX_CHECKED:
            raise vol.Invalid(f"Step {index}: check up to {MAX_CHECKED} devices.")
        if any(e.split(".", 1)[0] not in CHECKABLE for e in entities):
            raise vol.Invalid(
                f"Step {index}: pick doors, windows, locks, covers or switches."
            )
        return {
            "check_open": {
                "entity_id": entities,
                # Stop the routine when something is open (default: carry on).
                "stop": bool(check.get("stop", False)),
            }
        }
    if "wait_cheaper" in raw:
        wait = raw.get("wait_cheaper")
        if not isinstance(wait, dict):
            raise vol.Invalid(f"Step {index}: pick the price sensor.")
        entity_id = str(wait.get("entity_id") or "").strip()
        if not ENTITY_ID_RE.fullmatch(entity_id):
            raise vol.Invalid(f"Step {index}: pick the price sensor.")
        return {
            "wait_cheaper": {
                "entity_id": entity_id,
                "minutes": _int(
                    wait.get("minutes") or 180,
                    15,
                    MAX_CHEAPER_MINUTES,
                    f"Step {index}: wait up to",
                ),
            }
        }
    if "volume_ramp" in raw:
        ramp = raw.get("volume_ramp")
        if not isinstance(ramp, dict):
            raise vol.Invalid(f"Step {index}: set the speakers and the volume.")
        players = _entity_list(ramp.get("entity_id"), f"Step {index}")
        if not players or any(not e.startswith("media_player.") for e in players):
            raise vol.Invalid(f"Step {index}: pick the speakers to turn up.")
        return {
            "volume_ramp": {
                "entity_id": players,
                "from": _int(ramp.get("from") or 0, 0, 100, f"Step {index}: from"),
                "to": _int(ramp.get("to") or 30, 0, 100, f"Step {index}: to"),
                "minutes": _int(
                    ramp.get("minutes") or 10,
                    1,
                    MAX_RAMP_MINUTES,
                    f"Step {index}: over",
                ),
            }
        }
    if "wait_until" in raw:
        wait = raw.get("wait_until")
        if not isinstance(wait, dict):
            raise vol.Invalid(f"Step {index}: say what to wait for.")
        entity_id = str(wait.get("entity_id") or "").strip()
        if not ENTITY_ID_RE.fullmatch(entity_id):
            raise vol.Invalid(f"Step {index}: pick the device to wait for.")
        states = _state_list(wait.get("states", wait.get("state")))
        # Or a level: until a number (power, temperature) goes below or
        # above it, such as the dishwasher's power under 5 W.
        below = wait.get("below")
        above = wait.get("above")
        below = None if below in (None, "") else _number(below, f"Step {index}: below")
        above = None if above in (None, "") else _number(above, f"Step {index}: above")
        if not states and below is None and above is None:
            raise vol.Invalid(f"Step {index}: set the state to wait for, or a level.")
        # And stays so for this long (the dishwasher really finished).
        hold = _int(wait.get("for_minutes") or 0, 0, 240, f"Step {index}: for")
        on_timeout = str(wait.get("on_timeout") or "continue")
        if on_timeout not in WAIT_TIMEOUT:
            raise vol.Invalid(f"Step {index}: if it doesn't happen, go on or stop.")
        return {
            "wait_until": {
                "entity_id": entity_id,
                "states": states,
                "minutes": _int(
                    wait.get("minutes") or 10,
                    1,
                    MAX_WAIT_UNTIL_MINUTES,
                    f"Step {index}: wait up to",
                ),
                "on_timeout": on_timeout,
                **({"below": below} if below is not None else {}),
                **({"above": above} if above is not None else {}),
                **({"for_minutes": hold} if hold else {}),
            }
        }

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
    rooms = raw.get("rooms")
    if rooms:
        if rooms not in ROOM_FILTERS:
            raise vol.Invalid(f"Step {index}: rooms someone is in, or empty ones.")
        if not target.get("area_id"):
            raise vol.Invalid(
                f"Step {index}: pick rooms to only do it in some of them."
            )
        action["rooms"] = rooms
    if service in COVER_STEPS:
        for key, low, high, label in (
            ("closed_at", 0, 99, "counts as closed at"),
            ("open_at", 1, 100, "counts as open at"),
        ):
            value = raw.get(key)
            if value is not None and value != "":
                action[key] = _int(value, low, high, f"Step {index}: {label}")
        if action.get("closed_at", COVER_CLOSED_POSITION) >= action.get("open_at", 101):
            raise vol.Invalid(
                f"Step {index}: counts as open at must be more than counts as closed at."
            )
    return action


# Steps that change something (waiting alone does nothing).
DOING_STEPS = {
    "service",
    "todo",
    "run_routine",
    "set_mode",
    "together",
    "volume_ramp",
    "briefing",
    "check_open",
    "check_battery",
}


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
                f"Condition {index} (Sensors) has no sensors picked: pick the "
                "motion or occupancy sensors, or remove it."
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
    if kind == "calendar":
        entity_id = str(raw.get("entity_id") or "").strip()
        if not ENTITY_ID_RE.fullmatch(entity_id) or not entity_id.startswith(
            "calendar."
        ):
            raise vol.Invalid("Pick the calendar for this condition.")
        when = str(raw.get("when") or "no_event")
        if when not in CALENDAR_WHEN:
            raise vol.Invalid(
                "A calendar condition is on days with an event, or without."
            )
        match = " ".join(str(raw.get("match") or "").split())
        if len(match) > MAX_CALENDAR_MATCH:
            raise vol.Invalid("The calendar event text is too long.")
        return {
            "kind": "calendar",
            "entity_id": entity_id,
            "when": when,
            "match": match,
        }
    if kind == "mode":
        modes = _modes(raw.get("modes"))
        if not modes:
            raise vol.Invalid("Pick the house modes for this condition.")
        return {"kind": "mode", "modes": modes}
    if kind == "workday":
        entity_id = str(raw.get("entity_id") or "").strip()
        if not ENTITY_ID_RE.fullmatch(entity_id) or not entity_id.startswith(
            "binary_sensor."
        ):
            raise vol.Invalid("Pick the workday sensor for this condition.")
        when = str(raw.get("when") or "workday")
        if when not in WORKDAY_WHEN:
            raise vol.Invalid("A workday condition is on workdays, or days off.")
        return {"kind": "workday", "entity_id": entity_id, "when": when}
    if kind == "charging":
        entities = [str(e).strip() for e in _as_list(raw.get("entities"))]
        entities = list(dict.fromkeys(e for e in entities if e))
        if not entities or not all(
            ENTITY_ID_RE.fullmatch(e)
            and e.split(".", 1)[0] in ("sensor", "binary_sensor")
            for e in entities
        ):
            raise vol.Invalid("Pick the phones' charging sensors for this condition.")
        return {"kind": "charging", "entities": entities}
    if kind == "value":
        entity_id = str(raw.get("entity_id") or "").strip()
        if not ENTITY_ID_RE.fullmatch(entity_id):
            raise vol.Invalid("Pick the sensor for this condition.")
        below = raw.get("below")
        above = raw.get("above")
        below = None if below in (None, "") else _number(below, "Below")
        above = None if above in (None, "") else _number(above, "Above")
        if below is None and above is None:
            raise vol.Invalid("Give a level the sensor has to be below or above.")
        return {"kind": "value", "entity_id": entity_id, "below": below, "above": above}
    if kind == "weather":
        entity_id = str(raw.get("entity_id") or "").strip()
        if not ENTITY_ID_RE.fullmatch(entity_id) or not entity_id.startswith(
            "weather."
        ):
            raise vol.Invalid("Pick the weather for this condition.")
        test = str(raw.get("test") or "is")
        if test not in WEATHER_TESTS:
            raise vol.Invalid(
                "A weather condition checks what it's doing, or the temperature."
            )
        cond: dict[str, Any] = {"kind": "weather", "entity_id": entity_id, "test": test}
        if test in ("is", "is_not"):
            states = [str(w) for w in _as_list(raw.get("conditions"))]
            if not states or any(w not in WEATHER_STATES for w in states):
                raise vol.Invalid("Pick the kinds of weather for this condition.")
            cond["conditions"] = [w for w in WEATHER_STATES if w in states]
        else:
            if raw.get("value") in (None, ""):
                raise vol.Invalid("Set the temperature for this condition.")
            cond["value"] = _number(raw.get("value"), "The temperature")
        return cond
    if kind == "day_type":
        types = [
            " ".join(str(t).split())[:MAX_DAY_TYPE_NAME]
            for t in _as_list(raw.get("types"))
        ]
        types = list(dict.fromkeys(t for t in types if t))
        if not types:
            raise vol.Invalid("Pick the kinds of day for this condition.")
        cond_day: dict[str, Any] = {"kind": "day_type", "types": types}
        person = str(raw.get("person") or "").strip()
        if person:
            if not person.startswith("person."):
                raise vol.Invalid("Pick whose kind of day it is.")
            cond_day["person"] = person
        return cond_day
    if kind == "forecast":
        entity_id = str(raw.get("entity_id") or "").strip()
        if not ENTITY_ID_RE.fullmatch(entity_id) or not entity_id.startswith(
            "weather."
        ):
            raise vol.Invalid("Pick the weather for this condition.")
        kinds = [str(w) for w in _as_list(raw.get("conditions"))]
        if any(w not in WEATHER_STATES for w in kinds):
            raise vol.Invalid("Pick the kinds of weather to look for.")
        below = raw.get("below")
        below = None if below in (None, "") else _number(below, "The temperature")
        if not kinds and below is None:
            raise vol.Invalid("Pick the kinds of weather, or a temperature.")
        when = str(raw.get("when") or "not_coming")
        if when not in FORECAST_WHEN:
            raise vol.Invalid("A forecast condition is when it's coming, or not.")
        return {
            "kind": "forecast",
            "entity_id": entity_id,
            "conditions": [w for w in WEATHER_STATES if w in kinds],
            "below": below,
            "hours": _int(raw.get("hours") or 6, 1, 48, "Hours ahead"),
            "when": when,
        }
    raise vol.Invalid(f"Condition {index}: pick what to check.")


def _modes(value: Any) -> list[str]:
    picked = [str(m) for m in _as_list(value)]
    if any(m not in MODES for m in picked):
        raise vol.Invalid("House modes are home, away, night, guests or vacation.")
    return [m for m in MODES if m in picked]


def calendar_matches(cond: dict[str, Any], summaries: list[str]) -> bool:
    """Whether the day's events satisfy a calendar condition.

    With no text, any event counts. The text matches anywhere in an event's
    title, ignoring case.
    """
    needle = cond["match"].casefold()
    found = (
        any(needle in s.casefold() for s in summaries) if needle else bool(summaries)
    )
    return found if cond["when"] == "event" else not found


_DOMAIN = re.compile(r"^[a-z0-9_]+$")


def _integration_order(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(
        isinstance(d, str) and _DOMAIN.match(d) for d in value
    ):
        raise vol.Invalid("The integration order must be a list of integrations.")
    return list(dict.fromkeys(value))


DEFAULT_WAIT_MINUTES = 5
MAX_COOLDOWN_MINUTES = 1440
MAX_WAIT_MINUTES = 240


def _wait_mode(value: Any) -> str:
    mode = str(value or "day")
    if mode not in WAIT_MODES:
        raise vol.Invalid(
            "Waiting for conditions is until the end of the day, until a time, "
            "for some minutes, or not at all."
        )
    return mode


def normalize_date(value: Any, label: str = "The date") -> str | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10]).isoformat()
    except ValueError as err:
        raise vol.Invalid(f"{label} must be a date (YYYY-MM-DD).") from err


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


MAX_TICKS = 12


def _skip_dates(raw: Any) -> list[str]:
    """Days a routine doesn't run on its own: ISO dates, sorted, today on."""
    # Home Assistant's day, not the computer's: Home Assistant OS runs on
    # UTC, so the computer's clock is tomorrow during a US evening.
    today = dt_util.now().date().isoformat()
    days = []
    for value in _as_list(raw):
        try:
            day = date.fromisoformat(str(value)).isoformat()
        except ValueError as err:
            raise vol.Invalid("Skip days are dates like 2026-12-24.") from err
        if day >= today:
            days.append(day)
    days = sorted(set(days))
    if len(days) > MAX_SKIP_DATES:
        raise vol.Invalid(f"Skip at most {MAX_SKIP_DATES} days.")
    return days


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
    if trigger not in (*VALUE_TRIGGERS, TRIGGER_CHEAPEST):
        value_entity = None
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

    forecast_hours = 0
    if trigger == TRIGGER_ALARM:
        entity = str(raw.get("watch_entity") or "").strip()
        if not ENTITY_ID_RE.fullmatch(entity) or not entity.startswith(
            ("sensor.", "input_datetime.")
        ):
            raise vol.Invalid("Pick the alarm (a phone's Next alarm sensor).")
    if trigger == TRIGGER_CALENDAR_EVENT:
        entity = str(raw.get("watch_entity") or "").strip()
        if not entity.startswith("calendar.") or not ENTITY_ID_RE.fullmatch(entity):
            raise vol.Invalid("Pick the calendar.")
    if trigger == TRIGGER_FORECAST:
        entity = str(raw.get("watch_entity") or "").strip()
        if not entity.startswith("weather.") or not ENTITY_ID_RE.fullmatch(entity):
            raise vol.Invalid("Pick the weather forecast.")
        kinds = [str(w) for w in _as_list(raw.get("watch_states"))]
        if any(w not in WEATHER_STATES for w in kinds):
            raise vol.Invalid("Pick kinds of weather from the list.")
        if not kinds and raw.get("value_threshold") in (None, ""):
            raise vol.Invalid(
                "Pick the kinds of weather, or a temperature it drops below."
            )
        forecast_hours = _int(raw.get("forecast_hours") or 3, 1, 48, "Within")
    if trigger == TRIGGER_TAG and not str(raw.get("watch_item") or "").strip():
        raise vol.Invalid("Pick the tag.")
    if trigger == TRIGGER_BUTTON:
        entity = str(raw.get("watch_entity") or "").strip()
        if not entity.startswith("event.") or not ENTITY_ID_RE.fullmatch(entity):
            raise vol.Invalid("Pick the button (its event entity).")
    if trigger == TRIGGER_CHEAPEST:
        entity = str(raw.get("value_entity") or "").strip()
        if not entity.startswith("sensor.") or not ENTITY_ID_RE.fullmatch(entity):
            raise vol.Invalid("Pick the electricity price sensor.")
        if not raw.get("active_from") or not raw.get("active_to"):
            raise vol.Invalid(
                "Set the hours to choose the cheapest one from, under Only run between."
            )

    modes: list[str] = []
    if trigger == TRIGGER_MODE:
        modes = _modes(raw.get("modes"))
        if not modes:
            raise vol.Invalid("Pick the house modes that start it.")

    if trigger == TRIGGER_FORECAST and raw.get("value_threshold") not in (None, ""):
        value_threshold = _number(raw.get("value_threshold"), "The temperature")

    watch_entity: str | None = None
    watch_states: list[str] = []
    watch_item = ""
    hold_minutes = 0
    if trigger in (TRIGGER_ALARM, TRIGGER_CALENDAR_EVENT, TRIGGER_FORECAST):
        watch_entity = str(raw.get("watch_entity") or "").strip()
    if trigger == TRIGGER_CALENDAR_EVENT:
        watch_item = _item_name(raw.get("watch_item"))
    if trigger == TRIGGER_TAG:
        watch_item = str(raw.get("watch_item") or "").strip()[:200]
    if trigger == TRIGGER_BUTTON:
        watch_entity = str(raw.get("watch_entity") or "").strip()
        # Which presses count (the event types, such as "single" or
        # "double"); none: every press.
        watch_states = _state_list(raw.get("watch_states"))
    if trigger == TRIGGER_FORECAST:
        watch_states = [
            w for w in WEATHER_STATES if w in _as_list(raw.get("watch_states"))
        ]
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
    if not any(set(action) & DOING_STEPS for action in actions):
        raise vol.Invalid(
            "Add at least one step that does something (waits alone do nothing)."
        )
    if any(
        step.get("run_routine") == rid
        for action in actions
        for step in [action, *action.get("together", [])]
    ):
        raise vol.Invalid("A routine can't run itself.")

    kind = "todo" if raw.get("kind") == "todo" else "routine"
    more_triggers = _more_triggers(raw, kind, trigger)
    if kind == "todo" and (
        trigger not in STATE_TRIGGERS or len(actions) != 1 or "todo" not in actions[0]
    ):
        raise vol.Invalid("A to-do rule watches one device and does one to-do step.")

    return {
        "id": rid,
        "name": name,
        "enabled": bool(raw.get("enabled", True)),
        "room": room,
        # Runs even while a calendar hold (Settings) is on.
        "ignore_holds": bool(raw.get("ignore_holds", False)),
        # Speaker steps still run during quiet hours (Settings), for alarms.
        "loud_ok": bool(raw.get("loud_ok", False)),
        # Scheduled runs don't happen on these days (ISO dates).
        "skip_dates": _skip_dates(raw.get("skip_dates")),
        # On hot days (by the forecast), its time moves by some minutes.
        "hot_shift": clean_hot_shift(
            raw.get("hot_shift"),
            trigger,
            (TRIGGER_TIME, TRIGGER_SUNRISE, TRIGGER_SUNSET),
        ),
        # Scheduled runs don't happen while the house is in these modes.
        "skip_modes": _modes(raw.get("skip_modes")),
        # Sunrise and sunset, but not before or after these times ("HH:MM:SS").
        "not_before": _clock(raw.get("not_before"), "Not before")
        if trigger in (TRIGGER_SUNRISE, TRIGGER_SUNSET)
        else None,
        "not_after": _clock(raw.get("not_after"), "Not after")
        if trigger in (TRIGGER_SUNRISE, TRIGGER_SUNSET)
        else None,
        # Routines with the same group show together and pause together.
        "group": " ".join(str(raw.get("group") or "").split())[:MAX_GROUP_NAME],
        "device_id": device_id,
        "trigger": trigger,
        "value_entity": value_entity,
        "value_threshold": value_threshold,
        "off_threshold": off_threshold,
        "kind": kind,
        "watch_entity": watch_entity,
        "watch_states": watch_states,
        "hold_minutes": hold_minutes,
        "modes": modes,
        "forecast_hours": forecast_hours,
        "watch_item": watch_item,
        "active_from": active_from,
        "active_to": active_to,
        "companion_of": companion_of,
        "time": normalize_time(raw.get("time") or "07:00:00"),
        "offset_minutes": _int(raw.get("offset_minutes"), -720, 720, "Offset"),
        # Up to this many minutes early or late each day (time and sun triggers).
        "random_minutes": _int(
            raw.get("random_minutes"), 0, MAX_RANDOM_MINUTES, "Vary the time by"
        ),
        # Scheduled runs are skipped through this day (vacation).
        "paused_until": normalize_date(raw.get("paused_until"), "Paused until"),
        # A notification this many minutes before a scheduled run, with Skip (0: no).
        "heads_up_minutes": _int(
            raw.get("heads_up_minutes"), 0, MAX_HEADS_UP_MINUTES, "Heads-up"
        ),
        # Only between these days each year ("MM-DD"), both or neither.
        **_dates(raw.get("from_date"), raw.get("until_date")),
        # The next scheduled run is skipped once.
        "skip_next": bool(raw.get("skip_next", False)),
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
        # When a step can't change a device: go on with the rest, or stop there.
        "on_step_error": _on_step_error(raw.get("on_step_error")),
        # Try the devices a step couldn't change again this many minutes later (0: no).
        "retry_later_minutes": _int(
            raw.get("retry_later_minutes", DEFAULT_RETRY_LATER_MINUTES),
            0,
            MAX_RETRY_LATER_MINUTES,
            "Try again after",
        ),
        "wait": _wait_mode(raw.get("wait")),
        "wait_until": normalize_time(raw.get("wait_until") or "12:00:00"),
        # Which integration to try first for a device in several (None: Settings).
        "integration_order": _integration_order(raw.get("integration_order")),
        # Not again within this many minutes of the last run (0: no limit).
        "cooldown_minutes": _int(
            raw.get("cooldown_minutes"), 0, MAX_COOLDOWN_MINUTES, "Not again within"
        ),
        # wait "minutes": how long after the trigger the run may still go ahead.
        "wait_minutes": _int(
            raw.get("wait_minutes", DEFAULT_WAIT_MINUTES),
            1,
            MAX_WAIT_MINUTES,
            "Wait for",
        ),
        "skip_noop": bool(raw.get("skip_noop", True)),
        "notify_on_run": bool(raw.get("notify_on_run", False)),
        "notify_on_skip": bool(raw.get("notify_on_skip", False)),
        "notify_on_error": bool(raw.get("notify_on_error", True)),
        "notify_on_health": bool(raw.get("notify_on_health", False)),
        "wait_when_unavailable": bool(raw.get("wait_when_unavailable", False)),
        "more_triggers": more_triggers,
        # Ask on the phone (and the card) before a scheduled run: Run or Skip,
        # and what happens when nobody answers in time.
        "ask_first": bool(raw.get("ask_first", False)),
        "ask_minutes": _int(
            raw.get("ask_minutes") or DEFAULT_ASK_MINUTES, 1, 60, "Wait for an answer"
        ),
        "ask_default": _ask_default(raw.get("ask_default")),
        "actions": actions,
    }


DEFAULT_ASK_MINUTES = 10
ASK_DEFAULTS = ("run", "skip")


def _ask_default(value: Any) -> str:
    value = value or "run"
    if value not in ASK_DEFAULTS:
        raise vol.Invalid("With no answer, it runs or skips.")
    return str(value)


def _more_triggers(
    raw: dict[str, Any], kind: str, trigger: str
) -> list[dict[str, Any]]:
    """The routine's other triggers, each checked like a routine's own trigger."""
    items = _as_list(raw.get("more_triggers"))
    if not items:
        return []
    if kind == "todo" or trigger == "manual":
        raise vol.Invalid("Only a routine with a trigger can have more triggers.")
    if len(items) > MAX_MORE_TRIGGERS:
        raise vol.Invalid(
            f"A routine can have at most {MAX_MORE_TRIGGERS} more triggers."
        )
    result: list[dict[str, Any]] = []
    for number, item in enumerate(items, start=2):
        if not isinstance(item, dict):
            raise vol.Invalid(f"Trigger {number} must be an object.")
        if item.get("trigger") not in MORE_TRIGGER_KINDS:
            raise vol.Invalid(f"Trigger {number}: pick what starts it.")
        # Checked the same way as a routine's own trigger.
        probe = {
            **{k: v for k, v in raw.items() if k not in TRIGGER_FIELDS},
            **{k: item.get(k) for k in TRIGGER_FIELDS},
            "more_triggers": [],
            "off_threshold": None,
            "kind": "routine",
        }
        try:
            checked = normalize_routine(probe)
        except vol.Invalid as err:
            raise vol.Invalid(f"Trigger {number}: {err}") from err
        result.append({k: checked[k] for k in TRIGGER_FIELDS})
    return result
