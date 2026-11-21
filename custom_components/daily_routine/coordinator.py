"""Runtime: schedules, safety checks, execution and persistence."""

from __future__ import annotations

import asyncio
from asyncio import CancelledError, current_task, wait as wait_for_tasks
from collections import deque
from collections.abc import Callable, Coroutine
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
import hashlib
import logging
import re
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    SUN_EVENT_SUNRISE,
    SUN_EVENT_SUNSET,
)
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    storage,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_time,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.sun import get_astral_event_next
from homeassistant.util import dt as dt_util, slugify

from .access import async_get_access
from .away import AwayHelper, clean_summary, summary_or_default, vacation_or_none
from .battery import battery_text, is_battery, low_batteries
from .briefing import briefing_text, events_text, weather_text
from .checklist import checklist_text, left_open
from .const import (
    DOMAIN,
    EVENT_ROUTINE_FINISHED,
    EVENT_ROUTINE_SKIPPED,
    EVENT_ROUTINE_STARTED,
    MAX_ROUTINES,
    RETRY_DELAY_SECONDS,
    ROUTINE_SUBENTRY,
    SIGNAL_ROUTINES_CHANGED,
    STORAGE_KEY,
    STORAGE_VERSION,
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
    TRIGGER_SUNRISE,
    TRIGGER_SUNSET,
    TRIGGER_TIME,
    TRIGGER_VALUE_ABOVE,
    TRIGGER_VALUE_BELOW,
    WEEKDAYS,
)
from .day_types import (
    calendars as day_type_calendars,
    clean_day_types,
    day_type as pick_day_type,
    for_person,
)
from .energy import (
    KEEP_RUNS as POWER_RUNS,
    month_energy,
    power_sensors,
    read_watts,
    summary as power_summary,
)
from .guests import clean_guest_wifi, guest_wifi_or_none
from .habits import Habits
from .holds import active_hold, clean_holds
from .linked import (
    IntegrationLevel,
    LinkedGroup,
    area_fallbacks,
    integration_name,
    lead_entities,
    lead_first,
    linked_groups,
)
from .lived_in import LivedIn, clean_settings as clean_lived_in
from .planned import (
    alarm_time,
    cheapest_start,
    event_starts,
    forecast_matches,
    price_slots,
)
from .progress import step_label
from .quiet_hours import clean_quiet, in_quiet, is_loud, quiet_or_default
from .registry_cache import async_track_registries
from .reliability import DeviceStats
from .routines import (
    CHARGING_STATES,
    DESIRED_STATE,
    MODES,
    PER_PERSON_TRIGGERS,
    PLANNED_TRIGGERS,
    PRESENCE_TRIGGERS,
    PRESS_TRIGGERS,
    STATE_TRIGGERS,
    TODO_DONE_TRIGGERS,
    VALUE_TRIGGERS,
    WATCH_TRIGGERS,
    calendar_matches,
    in_dates,
    normalize_date,
    normalize_routine,
    uses_conditions,
)
from .run_log import RunLog, away_text, missed_today, needs_attention
from .schema import vol
from .todo import (
    FAILED_OUTCOMES,
    TODO_OUTCOME_TEXT,
    apply_todo_step,
    item_key,
    list_items,
)
from .undo import UNDO_FOR, restore, snapshot
from .voice import Voice
from .weather_shift import shift_minutes
from .weekly import clean_weekly, weekly_or_default

_LOGGER = logging.getLogger(__name__)

# Steps in these domains can rightly take long (a script runs to its end), so the
# step time limit doesn't apply to them.
UNLIMITED_DOMAINS = frozenset(
    {"script", "automation", "python_script", "pyscript", "shell_command"}
)
# A step that couldn't change a device is tried again later this many times.
RETRY_LATER_TIMES = 3
VERIFY_POLL_SECONDS = 0.25
# A cover still opening or closing is given this long to finish before it counts
# as not having changed.
MOVING_STATES = ("opening", "closing")
MOVING_SECONDS = 120
DOMAIN_RE = re.compile(r"[a-z0-9_]+")
# The phone notification's Try again button: this prefix plus the routine id.
TRY_AGAIN_ACTION = "DAILY_ROUTINE_TRY_AGAIN_"
# The heads-up notification's Skip button: this prefix plus the routine id.
SKIP_ACTION = "DAILY_ROUTINE_SKIP_"
# Devices remember_states keeps (and put_back_states turns back on).
REMEMBER_DOMAINS = ("light", "switch", "fan")
# Light settings put back with the light.
LIGHT_KEYS = ("brightness", "color_temp_kelvin", "hs_color")


@dataclass
class _Failure:
    """A step that didn't do everything: which devices (target), and why, in words."""

    index: int
    service: str
    target: dict[str, Any] | None
    text: str
    # Every device and every way tried, one line each (the run's "see each step").
    details: list[str] = field(default_factory=list)


def _trace_mark(rt: dict[str, Any], result: str, note: str) -> None:
    """Say how the open step of the run's replay went (it was skipped)."""
    trace = rt.get("trace")
    if trace:
        trace[-1].update(result=result, note=note)


def _public(step: dict[str, Any]) -> dict[str, Any]:
    """A replay line without its working parts."""
    return {k: v for k, v in step.items() if not k.startswith("_")}


@dataclass
class _Run:
    result: str = "success"
    details: list[str] = field(default_factory=list)
    failures: list[_Failure] = field(default_factory=list)
    # The step the run stopped at, when it stopped at a failed step.
    stopped_at: int | None = None


@dataclass
class _Retry:
    failures: list[_Failure]
    stopped_at: int | None
    tries: int


def _short_error(error: BaseException) -> str:
    text = str(error).strip() or type(error).__name__
    return text if len(text) <= 160 else text[:157] + "..."


def _join(names: list[str]) -> str:
    if len(names) < 3:
        return " and ".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


# Services of these domains act on devices: a step for them needs a device or room.
DEVICE_DOMAINS = frozenset(
    {
        "cover",
        "light",
        "switch",
        "fan",
        "lock",
        "climate",
        "media_player",
        "valve",
        "vacuum",
        "humidifier",
        "water_heater",
        "siren",
        "input_boolean",
    }
)
# Words in an integration's error that mean the device couldn't be talked to.
UNREACHABLE_WORDS = (
    "connect",
    "unreachable",
    "not reachable",
    "timed out",
    "timeout",
    "no response",
    "offline",
    "bluetooth",
    "ble",
)
# HA's own words when a service call has no entity, device or area to act on.
NO_TARGET_ERROR = "at least one of"
# How many devices a failure summary names before "and N more".
SUMMARY_NAMES = 2


def _summary(lines: list[str]) -> str:
    """A step's failure in one line: the first devices, then how many more."""
    if len(lines) <= SUMMARY_NAMES:
        return "; ".join(lines)
    more = len(lines) - SUMMARY_NAMES
    return "; ".join(lines[:SUMMARY_NAMES]) + f"; and {more} more (see each step)"


def result_words(result: str | None) -> str:
    """A run result code in words, for when no step said more (never a raw service)."""
    if not result or result == "success":
        return ""
    if result.startswith("error:todo_"):
        return "A to-do step failed"
    if result.startswith("error:"):
        what = result.split(":", 1)[1]
        domain, _, service = what.partition(".")
        verb = service.replace("_", " ") if service else domain.replace("_", " ")
        return f"A step failed ({verb})"
    if result.startswith("missing_service:"):
        return f"An action isn't in Home Assistant ({result.split(':', 1)[1]})"
    return result.replace("_", " ")


UNKNOWN_STATES = ("unknown", "unavailable")
# Recent runs kept per routine, newest last. Kept in memory, so they start again after a restart.
HISTORY_LENGTH = 20

H_TIME = "time"
H_PRESENCE = "presence"
H_WATCH = "watch"
H_CLEAR = "clear"
H_COND = "conditions_timer"
_CONDITION_REASON = {
    "presence": "presence_not_met",
    "sensors": "sensors_not_met",
    "state": "extra_condition_not_met",
    "calendar": "calendar_not_met",
    "mode": "wrong_mode",
    "workday": "workday_not_met",
    "weather": "weather_not_met",
    "day_type": "day_type_not_met",
    "forecast": "forecast_not_met",
    "charging": "charging_not_met",
    "value": "value_not_met",
}
# Power is read again this long after a run (energy.SETTLE_SECONDS; tests shorten it).
POWER_SETTLE_SECONDS = 60
# A volume ramp changes the volume in this many steps.
RAMP_STEPS = 20
# Binary sensors that say someone is in a room.
OCCUPANCY_CLASSES = ("occupancy", "motion", "presence")
# How often calendars used by conditions are read again (and after midnight).
CALENDAR_REFRESH = timedelta(minutes=30)
CALENDAR_TIMEOUT_SECONDS = 20


# The routines a "run another routine" step came through, so a loop is refused.
_CHAIN: ContextVar[tuple[str, ...]] = ContextVar("daily_routine_chain", default=())
# Minutes in a "wait until" step (tests make it shorter).
SECONDS_PER_MINUTE = 60
# "Ask me first": the phone notification's buttons, plus the routine id.
ASK_RUN_ACTION = "DAILY_ROUTINE_ASK_RUN_"
ASK_SKIP_ACTION = "DAILY_ROUTINE_ASK_SKIP_"
RUN_MISSED_ACTION = "DAILY_ROUTINE_RUN_MISSED"
# A step's "only if" in words, for the run's details.
ONLY_IF_SKIPPED = "skipped, its condition wasn't met"


def random_shift(routine_id: str, day: str, minutes: int) -> timedelta:
    """The same shift, from -minutes to +minutes, for a routine on a given day.

    Worked out from the routine and the date, so a restart doesn't pick a new one.
    """
    if minutes <= 0:
        return timedelta(0)
    digest = hashlib.sha256(f"{routine_id}:{day}".encode()).hexdigest()
    return timedelta(minutes=int(digest, 16) % (2 * minutes + 1) - minutes)


def _watched_entities(routine: dict[str, Any]) -> list[str]:
    """Every entity whose change can make this routine's conditions pass or fail."""
    entities: list[str] = []
    if routine["trigger"] == TRIGGER_EVERYONE_LEAVES:
        entities.extend(routine["people"])
    for more in routine.get("more_triggers") or []:
        if more["trigger"] == TRIGGER_EVERYONE_LEAVES:
            entities.extend(more["people"])
    if routine["trigger"] == TRIGGER_ROOM_CLEAR:
        entities.extend(routine["sensors"])
    for cond in routine["conditions"]:
        if cond["kind"] == "presence":
            entities.extend(cond["people"])
        elif cond["kind"] == "sensors":
            entities.extend(cond["sensors"])
        elif cond["kind"] == "charging":
            entities.extend(cond["entities"])
        elif "entity_id" in cond:
            entities.append(cond["entity_id"])
    return entities


# Conditions on these read more than the state: a weather's temperature, a
# calendar's events. Everything else a condition watches is judged by its state.
ATTRIBUTE_DOMAINS = ("calendar.", "weather.")


def _attributes_only(event: Event[EventStateChangedData]) -> bool:
    """Whether a state change kept the state and changed only attributes."""
    old, new = event.data.get("old_state"), event.data.get("new_state")
    return old is not None and new is not None and old.state == new.state


def _health_bad(state: State | None) -> bool:
    """Whether a device a routine needs is missing, unavailable or unknown."""
    return state is None or state.state in UNKNOWN_STATES


def _event_time(value: Any) -> datetime:
    """A calendar event's start or end: a time, or a date (local midnight).

    Raises ValueError when it is neither.
    """
    text = str(value or "")
    if len(text) == 10:
        return dt_util.start_of_local_day(date.fromisoformat(text))
    when = datetime.fromisoformat(text)
    return when if when.tzinfo else when.replace(tzinfo=dt_util.get_default_time_zone())


def _titles_between(
    events: list[dict[str, Any]] | None, start: datetime, end: datetime
) -> list[str] | None:
    """Titles of the events (read for a longer span) that are on between two times.

    What reading the calendar for just that span gives: events that overlap
    it, in the order they came. None when the calendar couldn't be read.
    Raises ValueError for an event without a readable start and end, so the
    caller reads that calendar for the span instead.
    """
    if events is None:
        return None
    titles = []
    for event in events:
        begins = _event_time(event.get("start"))
        ends = _event_time(event.get("end"))
        if ends > begins:
            on = begins < end and ends > start
        else:  # no length: on if it's at a moment inside the span
            on = start <= begins < end
        if on:
            titles.append(str(event.get("summary") or ""))
    return titles


def _calendars(routine: dict[str, Any]) -> list[str]:
    conditions = [
        *routine["conditions"],
        *(a["only_if"] for a in routine["actions"] if a.get("only_if")),
    ]
    return [c["entity_id"] for c in conditions if c["kind"] == "calendar"]


def _uses_day_type(routine: dict[str, Any]) -> bool:
    return any(
        c["kind"] == "day_type"
        for c in [
            *routine["conditions"],
            *(a["only_if"] for a in routine["actions"] if a.get("only_if")),
        ]
    )


def _with_who(action: dict[str, Any], who: str) -> dict[str, Any]:
    """A step with "{who}" in its text filled in: "{who} is home"."""
    data = action.get("data") or {}
    if not any(isinstance(v, str) and "{who}" in v for v in data.values()):
        return action
    return {
        **action,
        "data": {
            k: v.replace("{who}", who) if isinstance(v, str) else v
            for k, v in data.items()
        },
    }


def _clamp(when: datetime, routine: dict[str, Any]) -> datetime:
    """A sun time kept between the routine's not before and not after."""
    for key, later in (("not_before", True), ("not_after", False)):
        if not routine.get(key):
            continue
        limit = datetime.combine(
            when.date(), _parse_time(routine[key]), tzinfo=when.tzinfo
        )
        if (later and when < limit) or (not later and when > limit):
            when = limit
    return when


def _holds_or_none(raw: Any) -> list[dict[str, Any]]:
    try:
        return clean_holds(raw)
    except vol.Invalid:
        return []


def _day_types_or_none(raw: Any) -> list[dict[str, Any]]:
    try:
        return clean_day_types(raw)
    except vol.Invalid:
        return []


def _all_people(routine: dict[str, Any]) -> list[str]:
    people = list(routine["people"])
    for cond in routine["conditions"]:
        if cond["kind"] == "presence":
            people.extend(cond["people"])
    return list(dict.fromkeys(people))


H_VALUE = "value"
H_HOLD = "state_hold"
H_STATE = "state_trigger"
H_DONE = "todo_done"
H_PLANNED = "planned_source"
H_FORECAST = "forecast"
H_PRESS = "press"
# Reasons runs fail or are skipped, in words (for the daily summary).
REASON_WORDS = {
    "conditions_not_met": "its conditions weren't met",
    "paused": "routines are paused",
    "paused_until": "paused",
    "cooldown": "it ran a moment ago",
    "skip_mode": "the house was in a mode it doesn't run in",
    "skip_date": "it was a day it's set to skip",
    "calendar_hold": "a calendar hold was on",
    "skipped_once": "skipped once",
    "trigger_ended": "what started it was over",
    "asked_skip": "you said not to",
    "no_answer_skip": "nobody answered",
    "conditions_failed_mid_run": "its conditions stopped holding",
    "missing_service": "an action is missing",
    "unavailable_too_long": "a list or device was unavailable",
    "error": "a device didn't change",
}
# Devices listed for each step of a run's replay.
TRACE_DEVICES = 8
# How far ahead calendars are read for "before an event" triggers.
EVENTS_AHEAD = timedelta(days=3)
# How often the weather forecast is read for forecast triggers.
FORECAST_EVERY = timedelta(minutes=30)
H_HEALTH = "health"
H_HEALTH_TIMER = "health_timer"
H_EXPIRE = "expire"
H_CATCH_UP = "catch_up"

# Wait this long after Home Assistant starts before catching up missed routines,
# so lights, locks and people have loaded their states.
STARTUP_SETTLE_SECONDS = 30

# Runtime keys kept in storage. Clear timers and waiting state are not persisted.
# pending_date is kept so a routine waiting for its conditions still runs after a restart.
# What a run that was stopped part way by unloading Daily Routine (a reload,
# turning the integration off, an update) shows on its card and in Activity.
STOPPED_BY_UNLOAD = "Stopped part way: Daily Routine was reloaded or turned off"
# How long unloading waits for stopped runs to finish their bookkeeping.
STOP_RUNS_TIMEOUT_SECONDS = 5

PERSISTED_KEYS = (
    "last_run_date",
    "last_run",
    "last_result",
    "last_reason",
    "seen_state",
    "seen_done",
    "pending_date",
    "pending_since",
)
# How long to wait before saving a state we just saw. Noisy sensors then do not write on every change.
SEEN_SAVE_DELAY_SECONDS = 10
# Saves made while routines run are gathered up for this long (a sunset with
# several routines writes the file once, not once per step of each).
RUN_SAVE_DELAY_SECONDS = 2
# How often to check whether a routine needs attention (a device or list is missing or unavailable).
# Timing options on the Settings tab: default, lowest and highest allowed value, and the name shown in errors.
TIMING_SETTINGS = {
    "list_wait_seconds": (30, 0, 300, "The to-do list wait"),
    "health_check_minutes": (15, 1, 60, "The health check interval"),
    "health_alert_seconds": (60, 0, 600, "The health alert delay"),
    "startup_quiet_minutes": (5, 0, 60, "The startup quiet period"),
    "step_timeout_seconds": (30, 0, 600, "The step time limit"),
    "verify_seconds": (10, 0, 120, "The check that a step worked"),
}
# A device or list that is unavailable only briefly (for example while Home Assistant starts) is not announced.
# With "wait when unavailable": how long a run is held for a list or device to come back, and how
# long it may stay unavailable before the routine is announced as needing attention after all.
WAIT_UNAVAILABLE_HOURS = 24
# How many completed item names to remember, so a long list does not grow forever.
SEEN_DONE_LIMIT = 200


def area_entity_ids(hass: HomeAssistant, area_id: str) -> list[str]:
    """Entities in an area: entities assigned to it, plus device entities inheriting it."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    found = {
        entry.entity_id
        for entry in er.async_entries_for_area(ent_reg, area_id)
        if entry.disabled_by is None
    }
    for device in dr.async_entries_for_area(dev_reg, area_id):
        for entry in er.async_entries_for_device(ent_reg, device.id):
            if entry.area_id is None and entry.disabled_by is None:
                found.add(entry.entity_id)
    return sorted(found)


NOTIFY_SERVICE_RE = re.compile(r"notify\.[a-z0-9_]+")


def person_is_tracked(state: State) -> bool:
    """A person can be checked only when Home Assistant has a location for them (a device tracker)."""
    return bool(
        state.attributes.get("source") or state.attributes.get("device_trackers")
    )


def list_people(
    hass: HomeAssistant, hidden: set[str] | frozenset[str] = frozenset()
) -> list[dict[str, Any]]:
    """All person profiles. Flags whether a routine can check their location, and whether they are hidden."""
    return [
        {
            "entity_id": s.entity_id,
            "name": s.attributes.get("friendly_name") or s.entity_id,
            "state": s.state,
            "tracked": person_is_tracked(s),
            "hidden": s.entity_id in hidden,
        }
        for s in sorted(hass.states.async_all("person"), key=lambda s: s.entity_id)
    ]


GENERIC_NOTIFY_SERVICES = ("notify", "send_message")


def notify_targets(hass: HomeAssistant) -> list[dict[str, str]]:
    """Places a routine can send notifications, named the way Home Assistant names them."""
    registry = dr.async_get(hass)
    phone_names: dict[str, str] = {}
    for mobile_entry in hass.config_entries.async_entries("mobile_app"):
        for device in dr.async_entries_for_config_entry(
            registry, mobile_entry.entry_id
        ):
            name = device.name_by_user or device.name or ""
            if name:
                phone_names[slugify(name)] = name

    targets = []
    for service in sorted(hass.services.async_services_for_domain("notify")):
        if service in GENERIC_NOTIFY_SERVICES:
            continue
        if service.startswith("mobile_app_"):
            key = service[len("mobile_app_") :]
            name = phone_names.get(key) or key.replace("_", " ").title()
        else:
            name = service.replace("_", " ").title()
        targets.append({"entity": f"notify.{service}", "name": name})
    return sorted(targets, key=lambda t: t["name"].lower())


def _entities_by_area(hass: HomeAssistant) -> dict[str, list[str]]:
    """Same rule as area_entity_ids, for every area at once: one pass over the registries."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    device_area = {
        device.id: device.area_id for device in dev_reg.devices if device.area_id
    }
    index: dict[str, set[str]] = {}
    for entry in ent_reg.entities.values():
        if entry.disabled_by is not None:
            continue
        if entry.area_id:
            index.setdefault(entry.area_id, set()).add(entry.entity_id)
        elif entry.device_id in device_area:
            index.setdefault(device_area[entry.device_id], set()).add(entry.entity_id)
    return {area_id: sorted(ids) for area_id, ids in index.items()}


def list_areas(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Every area in Home Assistant, with the entities in it. Nothing is set up by hand."""
    areas = []
    by_area = _entities_by_area(hass)
    for area in sorted(
        ar.async_get(hass).async_list_areas(), key=lambda a: a.name.lower()
    ):
        entities = []
        for entity_id in by_area.get(area.id, []):
            state = hass.states.get(entity_id)
            if state is None:
                continue
            entities.append(
                {
                    "entity_id": entity_id,
                    "name": state.attributes.get("friendly_name") or entity_id,
                    "domain": entity_id.split(".", 1)[0],
                    "device_class": state.attributes.get("device_class"),
                    "state": state.state,
                }
            )
        areas.append(
            {"id": area.id, "name": area.name, "icon": area.icon, "entities": entities}
        )
    return areas


def _blank_runtime() -> dict[str, Any]:
    return {
        "state": "idle",
        "last_run": None,
        "last_result": None,
        "last_reason": None,
        "last_run_date": None,
        "next_run": None,
        "clear_since": None,  # in memory: when all sensors last became clear
        "armed": False,  # room_clear: a real off-transition happened since start
        "pending_date": None,  # local date while waiting for conditions
        "pending_since": None,  # when that wait began (for a wait of some minutes)
        "last_value": None,  # in memory: last number seen, for value triggers
        "places": {},  # in memory: each person's last known place, per-person triggers
        "all_away": None,  # last known "everyone away" for presence triggers
        "seen_done": None,  # in memory: names of completed items already seen
        "health_alerted": False,  # in memory: a problem was already announced
        # In memory: its health card may be showing. True to start with, since
        # one from before a restart or reload may still be there (1.34.3).
        "health_card": True,
        "unavailable_since": None,  # in memory: when a needed list or device went unavailable
        "held_run": None,  # in memory: a run waiting for a list or device to come back
        "progress": None,  # in memory: the step a running routine is on, for its card
        "started_by": None,  # in memory: the other trigger that started this run
    }


class HomeRoutinesCoordinator:
    """Owns routines, runtime state, scheduling and execution."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.routines: list[dict[str, Any]] = []
        # Bumped whenever routines change, so the panel knows when to reload them.
        self.revision = 0
        self._history: dict[str, deque[dict[str, str]]] = {}
        self._done_lock = asyncio.Lock()
        # When each sensor was last seen on (in memory). Needed for 'active within N minutes'.
        self._last_on: dict[str, datetime] = {}
        self.paused = False
        self.migrated = False
        # The house mode (home, away, night, guests or vacation).
        self.mode = "home"
        self.settings: dict[str, Any] = {
            "hidden_people": [],
            "notify_services": [],
            "integration_order": [],
            "lived_in": clean_lived_in(None),
            # Which routine editor opens: simple (a sentence) or full.
            "editor": "simple",
            "day_types": [],
            "weekly_report": weekly_or_default(None),
            "holds": [],
            "quiet_hours": quiet_or_default(None),
            "guest_wifi": None,
            **{key: default for key, (default, *_rest) in TIMING_SETTINGS.items()},
        }
        self.lived_in = LivedIn(self)
        self.away = AwayHelper(self)
        self.habits = Habits(self)
        # The forecast of each weather a forecast condition uses (read with
        # the forecast triggers, every FORECAST_EVERY).
        self._forecast_cache: dict[str, list[dict[str, Any]]] = {}
        # Who just arrived (or left), by routine id: "{who}" in a step's text.
        self._who: dict[str, str] = {}
        # Routines the restart report named, for Run them now.
        self._missed: list[str] = []
        # Power before and after each run, per routine (energy.py).
        self.power: dict[str, list[dict[str, Any]]] = {}
        self.voice = Voice(self)
        self._runtime: dict[str, dict[str, Any]] = {}
        # A routine's other triggers, each watched like a routine of its own
        # ("<id>~2"); when one fires, the routine itself runs.
        self._aliases: dict[str, dict[str, Any]] = {}
        # Coming events of the calendars "before an event" triggers use.
        self._upcoming: dict[str, list[dict[str, Any]]] = {}
        self._forecast_unsub: Callable[[], None] | None = None
        # How things were just before each routine's last run, for Undo (in memory).
        self._undo: dict[str, dict[str, Any]] = {}
        self._listeners: list[Callable[[], None]] = []
        # _find's index of routine positions, and which list it was built for.
        self._index: dict[str, int] = {}
        self._index_of: list[dict[str, Any]] | None = None
        self._index_len = -1
        # Told straight away (the pause switch, House mode): an automation that
        # changes one and reads it back sees the new state.
        self._now_listeners: list[Callable[[], None]] = []
        # One update for everything changed in the same moment (_notify_listeners).
        self._notify_pending = False
        # Counts updates, so the panel can share one payload per update.
        self.notify_count = 0
        self._handles: dict[tuple[str, str], Callable[[], None]] = {}
        # What a run couldn't do, for Try again and the tries planned later.
        self._retry: dict[str, _Retry] = {}
        # Remembered states by name (remember_states / put_back_states).
        self.memories: dict[str, dict[str, Any]] = {}
        self._startup_unsubs: list[Callable[[], None]] = []
        # Keeps linked devices and the panel's device lists until the
        # registries change (registry_cache.py), from setup to unload.
        self._registry_unsub: Callable[[], None] | None = None
        # Set when unloading: work that finishes later (a forecast or calendar
        # read started at startup) doesn't plan anything any more.
        self._unloaded = False
        # The steps of each run in progress, each in its own task (_run_steps),
        # so unloading can stop them. Without this a run waiting in a delay,
        # a "wait until" or a checklist went on after a reload, changing
        # devices from an unloaded coordinator and saving its old copy of the
        # routines over the new one's (1.34.1).
        self._step_tasks: set[asyncio.Task[_Run | None]] = set()
        self._health_unsub: Callable[[], None] | None = None
        # Today's event titles for each calendar a condition uses: entity -> (date, titles).
        self._calendar_days: dict[str, tuple[str, list[str]]] = {}
        self._calendar_unsub: Callable[[], None] | None = None
        self._calendar_refreshing = False
        self._calendar_tried: str | None = None  # the day calendars were last read
        self._started_at: datetime | None = None
        self._entry_id: str | None = None
        # Serialises edits so the dashboard and the integration's entries stay in step.
        self._lock = asyncio.Lock()
        self._store: storage.Store[dict[str, Any]] = storage.Store(
            hass, STORAGE_VERSION, STORAGE_KEY
        )
        self.run_log = RunLog(hass)
        self.device_stats = DeviceStats(hass)
        # Runs waiting for an answer to "Ask me first": routine id -> answer.
        self._asks: dict[str, asyncio.Future[str]] = {}

    # ----- helpers -----

    def _rt(self, routine_id: str) -> dict[str, Any]:
        return self._runtime.setdefault(routine_id, _blank_runtime())

    def _find(self, routine_id: str) -> dict[str, Any] | None:
        """The routine with this id. Asked very often (every sensor, every
        update), so through an index of positions, built again whenever the
        list is another one or another length, or a position no longer holds
        that routine."""
        routines = self.routines
        if self._index_of is not routines or self._index_len != len(routines):
            self._build_index()
        position = self._index.get(routine_id)
        if position is not None and position < len(routines):
            routine = routines[position]
            if routine["id"] == routine_id:
                return routine
            self._build_index()
            position = self._index.get(routine_id)
            return routines[position] if position is not None else None
        # Not in the index: look through the list itself (a routine put in
        # place of another keeps the list's length).
        return next((r for r in routines if r["id"] == routine_id), None)

    def _build_index(self) -> None:
        self._index = {r["id"]: i for i, r in enumerate(self.routines)}
        self._index_of = self.routines
        self._index_len = len(self.routines)

    def _set_handle(
        self, routine_id: str, kind: str, unsub: Callable[[], None]
    ) -> None:
        self._drop_handle(routine_id, kind)
        self._handles[(routine_id, kind)] = unsub

    def _drop_handle(self, routine_id: str, kind: str) -> None:
        unsub = self._handles.pop((routine_id, kind), None)
        if unsub is not None:
            unsub()

    def _drop_all_handles(self) -> None:
        for unsub in self._handles.values():
            unsub()
        self._handles.clear()

    def _state_is(self, entity_id: str, expected: str) -> bool:
        state = self.hass.states.get(entity_id)
        return state is not None and state.state == expected

    def _zone_key(self, zone: str) -> str | None:
        """The state a person reports while inside this zone (home is reported as 'home')."""
        if zone == "zone.home":
            return "home"
        state = self.hass.states.get(zone)
        return state.name if state is not None else None

    def _place(self, person: str) -> str | None:
        """A person's place, or None while unknown or unavailable."""
        state = self.hass.states.get(person)
        if state is None or state.state in UNKNOWN_STATES:
            return None
        return state.state

    def _everyone_away(self, people: list[str], zone: str) -> bool | None:
        """True or False, or None while anyone's place is unknown (no decision yet)."""
        key = self._zone_key(zone)
        if key is None:
            return None
        places = [self._place(p) for p in people]
        if any(place == key for place in places):
            return False  # someone is there: known whatever the others are
        if any(place is None for place in places):
            return None
        return True

    def _all_away(self, people: list[str], zone: str = "zone.home") -> bool:
        """True when every person has left the zone. Unknown or unavailable people block it."""
        key = self._zone_key(zone)
        if key is None:
            return False
        for pid in people:
            state = self.hass.states.get(pid)
            if state is None or state.state in (key, *UNKNOWN_STATES):
                return False
        return True

    def _allowed_day(self, routine: dict[str, Any], when: datetime) -> bool:
        return WEEKDAYS[when.weekday()] in routine["days"] and in_dates(
            when.date(), routine.get("from_date"), routine.get("until_date")
        )

    def _fire_event(
        self, event_type: str, routine: dict[str, Any], result: str | None = None
    ) -> None:
        data: dict[str, Any] = {"routine_id": routine["id"], "name": routine["name"]}
        if result is not None:
            data["result"] = result
        self.hass.bus.async_fire(event_type, data)

    def _send_notification(
        self,
        message: str,
        notification_id: str | None = None,
        *,
        retry_routine: str | None = None,
        actions: list[dict[str, str]] | None = None,
    ) -> None:
        if retry_routine:
            actions = [
                {"action": f"{TRY_AGAIN_ACTION}{retry_routine}", "title": "Try again"}
            ]
        self._spawn(self._notify(message, notification_id, actions))

    @staticmethod
    def _health_notification_id(routine_id: str) -> str:
        """One fixed id per routine, so its health card can be replaced or dismissed later."""
        return f"{DOMAIN}_health_{routine_id}"

    async def _dismiss_health_notification(self, routine_id: str) -> None:
        """Remove a routine's health card. Dismissing a card that is not there does nothing."""
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "dismiss",
                {"notification_id": self._health_notification_id(routine_id)},
                blocking=False,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not dismiss health notification for %s", routine_id)

    async def _notify(
        self,
        message: str,
        notification_id: str | None = None,
        actions: list[dict[str, str]] | None = None,
    ) -> None:
        for service in self.settings.get("notify_services") or []:
            if service == "notify.persistent_notification":
                continue  # the Home Assistant notification below is always created
            name = service.split(".", 1)[1]
            payload: dict[str, Any] = {"title": "Daily Routine", "message": message}
            if actions and name.startswith("mobile_app_"):
                # Only the Companion app shows buttons.
                payload["data"] = {"actions": actions}
                if notification_id:
                    payload["data"]["tag"] = notification_id
            try:
                await self.hass.services.async_call(
                    "notify", name, payload, blocking=False
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Could not call notify service %s", service)
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "message": message,
                    "title": "Daily Routine",
                    "notification_id": notification_id
                    or f"{DOMAIN}_{dt_util.now().timestamp()}",
                },
                blocking=False,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not create persistent notification")

    def _notify_listeners(self) -> None:
        """Tell the sensors and the panel, once for everything changed in the
        same moment: a burst of state changes or a run's several steps would
        otherwise rebuild every routine's status each time. Listeners added
        with ``now=True`` are told at once, every time."""
        for listener in list(self._now_listeners):
            listener()
        if self._notify_pending:
            return
        self._notify_pending = True
        self.hass.loop.call_soon(self._send_notify)

    @callback
    def _send_notify(self) -> None:
        self._notify_pending = False
        self.notify_count += 1
        for listener in list(self._listeners):
            listener()

    def async_add_listener(
        self, listener: Callable[[], None], *, now: bool = False
    ) -> Callable[[], None]:
        listeners = self._now_listeners if now else self._listeners
        listeners.append(listener)

        def remove() -> None:
            if listener in listeners:
                listeners.remove(listener)

        return remove

    # ----- status for sensors and the panel -----

    def _record(self, routine_id: str, outcome: str, detail: str = "") -> None:
        runs = self._history.setdefault(routine_id, deque(maxlen=HISTORY_LENGTH))
        # A run that went through its steps: each step, for replaying it.
        steps = (
            self._runtime.get(routine_id, {}).pop("trace", None)
            if outcome in ("ran", "failed")
            else None
        )
        entry: dict[str, Any] = {
            "at": dt_util.now().isoformat(),
            "outcome": outcome,
            "detail": detail,
        }
        if steps:
            entry["steps"] = steps
        runs.append(entry)
        if outcome == "held":
            return  # shown on the card only; the run is logged when it runs or is dropped
        routine = self._find(routine_id)
        name = routine["name"] if routine else routine_id
        if outcome == "skipped":
            self.run_log.record(routine_id, name, "skipped", reason=detail)
        elif outcome == "ran":
            self.run_log.record(routine_id, name, "ran", detail=detail, steps=steps)
        else:
            self.run_log.record(
                routine_id, name, "failed", reason=detail, detail=detail, steps=steps
            )

    def get_history(self, routine_id: str) -> list[dict[str, str]]:
        """Recent runs for a routine, newest first."""
        return list(reversed(self._history.get(routine_id, ())))

    def _routine_names(self) -> dict[str, str]:
        return {r["id"]: r["name"] for r in self.routines}

    def _next_run_of(self, routine_id: str) -> str | None:
        """The soonest planned run, over the routine's triggers."""
        times = [self._rt(routine_id)["next_run"]] + [
            self._rt(a["id"])["next_run"] for a in self._aliases_of(routine_id)
        ]
        known = [dt_util.parse_datetime(t) for t in times if t]
        found = [t for t in known if t is not None]
        return min(found).isoformat() if found else None

    def sensor_status(self, routine_id: str) -> dict[str, Any] | None:
        """The part of ``get_status`` a routine's status sensor shows. Every
        sensor asks after every update, so the run history, device
        reliability, power, holds and undo (the dashboard's) are left out."""
        routine = self._find(routine_id)
        if routine is None:
            return None
        rt = self._rt(routine_id)
        ok, reason = self._check(routine, dt_util.now())
        return {
            "name": routine["name"],
            "enabled": routine["enabled"],
            "room": routine["room"],
            "paused": self.paused,
            "state": rt["state"],
            "last_run": rt["last_run"],
            "last_result": rt["last_result"],
            "last_reason": rt.get("last_reason"),
            "next_run": self._next_run_of(routine_id),
            "clear_since": rt["clear_since"].isoformat() if rt["clear_since"] else None,
            "conditions_ok": ok,
            "block_reason": reason,
            "waiting": rt["pending_date"] is not None,
            "trigger": routine["trigger"],
            "days": routine["days"],
            "time": routine["time"],
            "repeat": routine["repeat"],
        }

    def get_status(self, routine_id: str) -> dict[str, Any] | None:
        routine = self._find(routine_id)
        if routine is None:
            return None
        rt = self._rt(routine_id)
        ok, reason = self._check(routine, dt_util.now())
        return {
            "name": routine["name"],
            "enabled": routine["enabled"],
            "room": routine["room"],
            "paused": self.paused,
            "state": rt["state"],
            "last_run": rt["last_run"],
            "last_result": rt["last_result"],
            "last_reason": rt.get("last_reason"),
            "history": self.get_history(routine_id),
            "next_run": self._next_run_of(routine_id),
            "clear_since": rt["clear_since"].isoformat() if rt["clear_since"] else None,
            "conditions_ok": ok,
            "block_reason": reason,
            "waiting": rt["pending_date"] is not None,
            "wait_ends": (
                ends.isoformat()
                if rt["pending_date"] is not None
                and (ends := self._wait_ends(routine, dt_util.now()))
                else None
            ),
            "held": rt["held_run"] is not None,
            "progress": rt["progress"] if rt["state"] == "running" else None,
            "can_try_again": routine_id in self._retry,
            "can_undo": self.can_undo(routine_id),
            "ask_until": rt.get("ask_until"),
            "waiting_for": self._unavailable_names(routine),
            "trigger": routine["trigger"],
            "days": routine["days"],
            "time": routine["time"],
            "repeat": routine["repeat"],
            "power": self.power_of(routine_id),
            "reliability": self.reliability(routine),
            "hold": None if routine.get("ignore_holds") else self.hold(dt_util.now()),
            "group": routine.get("group", ""),
            "paused_until": self.paused_until(routine),
            "ticklist": rt.get("ticklist") if rt["state"] == "running" else None,
        }

    # ----- lifecycle -----

    async def async_setup(self) -> None:
        """Load stored routines and settings. Scheduling starts in async_start."""
        if self._registry_unsub is None:
            self._registry_unsub = async_track_registries(self.hass)
        await self.run_log.async_load()
        await self.device_stats.async_load()
        data = await self._store.async_load() or {}
        self.paused = bool(data.get("paused", False))
        self.migrated = bool(data.get("migrated", False))
        saved_mode = data.get("mode")
        self.mode = (
            saved_mode
            if isinstance(saved_mode, str) and saved_mode in MODES
            else "home"
        )
        saved = data.get("settings") or {}
        services = saved.get("notify_services")
        if services is None and saved.get("notify_service"):
            services = [saved["notify_service"]]  # one target before 1.4.0
        self.settings = {
            "hidden_people": [
                p for p in saved.get("hidden_people", []) if isinstance(p, str)
            ],
            "notify_services": [s for s in (services or []) if isinstance(s, str)],
            "integration_order": [
                d for d in saved.get("integration_order") or [] if isinstance(d, str)
            ],
            "lived_in": _lived_in_or_default(saved.get("lived_in")),
            "editor": "full" if saved.get("editor") == "full" else "simple",
            "daily_summary": summary_or_default(saved.get("daily_summary")),
            "vacation": vacation_or_none(saved.get("vacation")),
            "day_types": _day_types_or_none(saved.get("day_types")),
            "weekly_report": weekly_or_default(saved.get("weekly_report")),
            "holds": _holds_or_none(saved.get("holds")),
            "quiet_hours": quiet_or_default(saved.get("quiet_hours")),
            "guest_wifi": guest_wifi_or_none(saved.get("guest_wifi")),
            "missed_report": bool(saved.get("missed_report")),
            "away_summary": bool(saved.get("away_summary")),
            "away_since": saved.get("away_since"),
            **{key: _timing_value(saved.get(key), key) for key in TIMING_SETTINGS},
        }
        for raw in data.get("routines", []):
            try:
                self.routines.append(normalize_routine(raw))
            except vol.Invalid as err:
                _LOGGER.error("Skipping stored routine %s: %s", raw.get("id"), err)
        for rid, saved in (data.get("runtime") or {}).items():
            self._rt(rid).update({k: saved.get(k) for k in PERSISTED_KEYS})
        memories = data.get("memories")
        self.memories = memories if isinstance(memories, dict) else {}
        power = data.get("power")
        self.power = (
            {k: v for k, v in power.items() if isinstance(v, list)}
            if isinstance(power, dict)
            else {}
        )
        await self.habits.async_load()

    async def async_start(self) -> None:
        # Calendars a condition uses first, so a run right after starting can
        # check them. They are read at the same time, and a calendar that a
        # "before an event" trigger or a hold also uses is read once, for the
        # longer span, and shared with the read below (1.33.0).
        now = dt_util.now()
        first, titles = await self._read_calendars_at_once(now, upcoming=False)
        await self._refresh_calendars(read=first, titles=titles, now=now)
        self._start_all(catch_up=False)
        self._started_at = dt_util.utcnow()
        self._schedule_health_interval()
        self.lived_in.start()
        self.away.start()
        self.habits.start()
        self._forecast_unsub = async_track_time_interval(
            self.hass, self._check_forecasts, FORECAST_EVERY
        )
        # The coming days' events and the forecasts are read in the
        # background, at the same time, so a slow calendar or weather service
        # doesn't hold up setting up (1.33.0). Routines they plan are planned
        # when they come in.
        self._spawn(self._after_start(first, now))
        await self.voice.async_start()
        self._calendar_unsub = async_track_time_interval(
            self.hass, self._on_calendar_interval, CALENDAR_REFRESH
        )
        self._startup_unsubs.append(
            self.hass.bus.async_listen(
                "mobile_app_notification_action", self._on_notification_action
            )
        )
        self._notify_listeners()
        if self.hass.is_running:
            self._schedule_startup_catch_up()
        else:
            self._startup_unsubs.append(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED,
                    lambda _event: self._schedule_startup_catch_up(),
                )
            )

    async def _after_start(
        self, read: dict[str, list[dict[str, Any]] | None], now: datetime
    ) -> None:
        """The slower reads after starting: coming events and forecasts, together."""
        await asyncio.gather(
            self._refresh_upcoming(read=read, now=now), self._check_forecasts()
        )

    async def _run_missed_from(self, event: Event) -> None:
        access = await async_get_access(self.hass)
        if await access.async_allows_context(event.context):
            await self.async_run_missed()

    @callback
    def _on_notification_action(self, event: Event) -> None:
        action = str(event.data.get("action") or "")
        if action == RUN_MISSED_ACTION:
            self._spawn(self._run_missed_from(event))
            return
        if action.startswith(TRY_AGAIN_ACTION):
            routine_id, what = action[len(TRY_AGAIN_ACTION) :], "Try again"
        elif action.startswith(SKIP_ACTION):
            routine_id, what = action[len(SKIP_ACTION) :], "Skip"
        elif action.startswith(ASK_RUN_ACTION):
            routine_id, what = action[len(ASK_RUN_ACTION) :], "Run"
        elif action.startswith(ASK_SKIP_ACTION):
            routine_id, what = action[len(ASK_SKIP_ACTION) :], "Don't run"
        else:
            return
        if self._find(routine_id) is None:
            return

        async def _try() -> None:
            if not await (await async_get_access(self.hass)).async_allows_context(
                event.context
            ):
                _LOGGER.warning(
                    "%s for routine %s was refused: that user can't use Daily Routine.",
                    what,
                    routine_id,
                )
                return
            if what == "Skip":
                await self.async_skip_next(routine_id, True)
            elif what in ("Run", "Don't run"):
                self.async_answer(routine_id, what == "Run")
            else:
                await self.async_try_again(routine_id)

        self._spawn(_try())

    def _report_missed(self, now: datetime) -> None:
        """After a restart, one notification naming routines whose time today
        passed while Home Assistant was off, with a Run them now button."""
        if not self.settings.get("missed_report"):
            return
        self._missed = missed_today(
            self.routines,
            self.run_log.view(),
            now,
            lambda r: self._allowed_day(r, now),
        )
        if not self._missed:
            return
        names = [r["name"] for r in self.routines if r["id"] in self._missed]
        self._spawn(
            self._notify(
                f"Missed while Home Assistant was off: {', '.join(names)}.",
                f"{DOMAIN}_missed",
                [{"action": RUN_MISSED_ACTION, "title": "Run them now"}],
            )
        )

    async def async_run_missed(self) -> list[str]:
        """Run the routines the restart report named (Run them now)."""
        ran = []
        for routine_id in self._missed:
            if self._find(routine_id) is not None and await self.async_run(routine_id):
                ran.append(routine_id)
        self._missed = []
        return ran

    def _schedule_startup_catch_up(self) -> None:
        @callback
        def _later(_now: datetime) -> None:
            now = dt_util.now()
            for routine in self.routines:
                self._maybe_catch_up(routine, now)
            self._report_missed(now)
            self._check_states_changed_while_stopped()
            self._check_done_after_startup()

        self._startup_unsubs.append(
            async_call_later(self.hass, STARTUP_SETTLE_SECONDS, _later)
        )

    async def async_unload(self, *, save: bool = True) -> None:
        self._unloaded = True
        # Runs in progress stop first, so nothing they do lands after the save
        # below (or after the next coordinator has loaded).
        await self._async_stop_runs()
        if self._registry_unsub is not None:
            self._registry_unsub()
            self._registry_unsub = None
        for unsub in self._startup_unsubs:
            unsub()
        self._startup_unsubs.clear()
        if self._health_unsub is not None:
            self._health_unsub()
            self._health_unsub = None
        if self._calendar_unsub is not None:
            self._calendar_unsub()
            self._calendar_unsub = None
        self._drop_all_handles()
        self.lived_in.stop()
        self.away.stop()
        self.habits.stop()
        self.voice.stop()
        if self._forecast_unsub is not None:
            self._forecast_unsub()
            self._forecast_unsub = None
        if save:
            await self._async_save()
            # The Activity log, device reliability and habits save a little
            # later than they change. Written now, or a reload loses what
            # happened in the last moments before it (1.34.1): the next
            # coordinator reads the files, not this one's memory.
            await self.run_log.async_flush()
            await self.device_stats.async_flush()
            await self.habits.async_flush()

    async def _async_stop_runs(self) -> None:
        """Stop every run in progress (unloading).

        Each run's steps are cancelled; ``_owned_steps`` records the run as
        stopped part way before the task ends, and the code that started the
        run (a schedule, the panel, the run_routine action) carries on without
        a result. Waits at most ``STOP_RUNS_TIMEOUT_SECONDS``.
        """
        tasks = [task for task in self._step_tasks if not task.done()]
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        _done, pending = await wait_for_tasks(tasks, timeout=STOP_RUNS_TIMEOUT_SECONDS)
        if pending:
            _LOGGER.warning(
                "%s routine run(s) didn't stop within %s s of unloading",
                len(pending),
                STOP_RUNS_TIMEOUT_SECONDS,
            )

    def _payload(self) -> dict[str, Any]:
        runtime = {
            rid: {k: rt.get(k) for k in PERSISTED_KEYS}
            for rid, rt in self._runtime.items()
        }
        return {
            "routines": self.routines,
            "paused": self.paused,
            "mode": self.mode,
            "migrated": self.migrated,
            "settings": self.settings,
            "runtime": runtime,
            "memories": self.memories,
            "power": self.power,
        }

    async def _async_save(self) -> None:
        await self._store.async_save(self._payload())

    @callback
    def _save_soon(self) -> None:
        """Save shortly, together with anything else that changes meanwhile
        (Home Assistant writes it on shutdown if it's still waiting)."""
        self._store.async_delay_save(self._payload, RUN_SAVE_DELAY_SECONDS)

    async def async_reload(self, only: set[str] | None = None) -> None:
        """Restart schedules and watchers from the current routine list.

        ``only``: just these routines (and their other triggers) were saved
        or deleted, so only theirs are restarted and every other routine's
        schedules, watchers and timers (a planned retry, a heads-up) are left
        as they are (1.33.0). Without it, everything is restarted (the
        reload action, a restore, an entry deleted in Home Assistant).
        """
        if only is None:
            self._drop_all_handles()
            # Routines waiting for their conditions keep waiting (_start_all
            # re-arms them): saving one routine mustn't cancel another's run today.
            self._start_all(catch_up=False)
        else:
            self._start_some(only)
        self.revision += 1
        await self._async_save()
        self._notify_listeners()
        async_dispatcher_send(self.hass, SIGNAL_ROUTINES_CHANGED)
        if any(
            c not in self._calendar_days for r in self.routines for c in _calendars(r)
        ):
            self._spawn(self._refresh_calendars())
        if any(
            r["trigger"] == TRIGGER_CALENDAR_EVENT
            and r["watch_entity"] not in self._upcoming
            for r in [*self.routines, *self._aliases.values()]
        ):
            self._spawn(self._refresh_upcoming())
        if any(
            r["trigger"] == TRIGGER_FORECAST
            and self._rt(r["id"]).get("forecast_hit") is None
            for r in [*self.routines, *self._aliases.values()]
        ):
            self._spawn(self._check_forecasts())

    # ----- editing (used by the panel, services and migration) -----

    async def async_save_routine(self, raw: Any) -> dict[str, Any]:
        async with self._lock:
            routine = await self._save_routine_locked(raw)
            self._sync_subentries_locked()
        return routine

    async def _save_routine_locked(self, raw: Any) -> dict[str, Any]:
        routine = normalize_routine(raw)
        untracked = []
        for person_id in _all_people(routine):
            state = self.hass.states.get(person_id)
            if state is None:
                untracked.append(person_id)
            elif not person_is_tracked(state):
                untracked.append(
                    str(state.attributes.get("friendly_name") or person_id)
                )
        if untracked:
            names = ", ".join(untracked)
            raise vol.Invalid(
                f"{names} has no location tracking, so a routine can't check where they are. "
                "Give them a device tracker or leave them out."
            )
        companion_raw = _companion_for(routine)
        if companion_raw is not None:
            clash = self._find(companion_raw["id"])
            if clash is not None and clash.get("companion_of") != routine["id"]:
                raise vol.Invalid(
                    f"A routine named '{companion_raw['id']}' already exists, so the turn-off routine can't be created."
                )
        companion = normalize_routine(companion_raw) if companion_raw else None

        old_companions = [
            r["id"] for r in self.routines if r.get("companion_of") == routine["id"]
        ]
        self._drop_companions(routine["id"])
        index = next(
            (i for i, r in enumerate(self.routines) if r["id"] == routine["id"]), None
        )
        if index is None:
            if len(self.routines) >= MAX_ROUTINES:
                raise vol.Invalid(f"You can have at most {MAX_ROUTINES} routines.")
            self.routines.append(routine)
        else:
            self.routines[index] = routine
        if companion is not None:
            self.routines.append(companion)
        # Only this routine and its turn-off routines (old and new) start again.
        await self.async_reload(
            only={routine["id"], *old_companions}
            | ({companion["id"]} if companion is not None else set())
        )
        return routine

    def _drop_companions(self, routine_id: str) -> None:
        for r in [r for r in self.routines if r.get("companion_of") == routine_id]:
            self._runtime.pop(r["id"], None)
        self.routines = [
            r for r in self.routines if r.get("companion_of") != routine_id
        ]

    async def async_delete_routine(self, routine_id: str) -> None:
        async with self._lock:
            companions = [
                r["id"] for r in self.routines if r.get("companion_of") == routine_id
            ]
            self._drop_companions(routine_id)
            self._spawn(self._dismiss_health_notification(routine_id))
            self.routines = [r for r in self.routines if r["id"] != routine_id]
            self._runtime.pop(routine_id, None)
            self._history.pop(routine_id, None)
            self.run_log.forget_routine(routine_id)
            await self.async_reload(only={routine_id, *companions})
            self._sync_subentries_locked()

    # ----- backup and restore -----

    def export_routines(self) -> list[dict[str, Any]]:
        """Every routine a person made. Turn-off routines are rebuilt from their parent, so they are left out."""
        return [dict(r) for r in self.routines if not r.get("companion_of")]

    async def async_import_routines(self, items: list[Any]) -> dict[str, Any]:
        """Save each routine in the file. Routines that fail are reported and the rest still import."""
        registry = dr.async_get(self.hass)
        imported = 0
        problems: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                problems.append(
                    {"id": "?", "reason": "Each routine must be an object."}
                )
                continue
            rid = str(item.get("id") or "?")
            if item.get("companion_of"):
                problems.append(
                    {
                        "id": rid,
                        "reason": "Turn-off routines are rebuilt automatically; export the main routine instead.",
                    }
                )
                continue
            device_id = item.get("device_id")
            if device_id and registry.async_get(device_id) is None:
                # The device belongs to another Home Assistant install; link to the hub instead.
                item = {**item, "device_id": None}
            try:
                await self.async_save_routine(item)
            except vol.Invalid as err:
                problems.append({"id": rid, "reason": str(err)})
            else:
                imported += 1
        return {"imported": imported, "problems": problems}

    # ----- integration entries (one per routine, editable in Home Assistant) -----

    def get_routine(self, routine_id: str) -> dict[str, Any] | None:
        return self._find(routine_id)

    def attach_entry(self, entry_id: str) -> None:
        self._entry_id = entry_id

    @callback
    def spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        self._spawn(coro)

    def notify_listeners(self) -> None:
        self._notify_listeners()

    async def call_service(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
        target: dict[str, Any] | None,
    ) -> BaseException | None:
        return await self._call_service(domain, service, data, target)

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Run work the entry owns: unloading waits for it to finish."""
        if (entry := self._entry()) is not None:
            entry.async_create_task(self.hass, coro)
        else:
            self.hass.async_create_task(coro)

    def _entry(self) -> ConfigEntry | None:
        return (
            self.hass.config_entries.async_get_entry(self._entry_id)
            if self._entry_id
            else None
        )

    def _routine_subentries(self, entry: ConfigEntry) -> dict[str, ConfigSubentry]:
        return {
            sub.unique_id: sub
            for sub in entry.subentries.values()
            if sub.subentry_type == ROUTINE_SUBENTRY and sub.unique_id
        }

    async def async_sync_subentries(self) -> None:
        async with self._lock:
            self._sync_subentries_locked()

    def _sync_subentries_locked(self) -> None:
        """Make the integration's entries match the routines saved from the dashboard."""
        entry = self._entry()
        if entry is None:
            return
        managed = {r["id"]: r for r in self.routines if not r.get("companion_of")}
        existing = self._routine_subentries(entry)
        changed = False
        for rid, routine in managed.items():
            sub = existing.get(rid)
            if sub is None:
                changed = True
                self.hass.config_entries.async_add_subentry(
                    entry,
                    ConfigSubentry(
                        data=MappingProxyType({"routine_id": rid}),
                        subentry_type=ROUTINE_SUBENTRY,
                        title=routine["name"],
                        unique_id=rid,
                    ),
                )
            elif sub.title != routine["name"]:
                self.hass.config_entries.async_update_subentry(
                    entry, sub, title=routine["name"]
                )
        for rid, sub in existing.items():
            if rid not in managed:
                changed = True
                self.hass.config_entries.async_remove_subentry(entry, sub.subentry_id)
        if changed:
            # Devices are linked to their routine entries, so tell them the entries now exist.
            async_dispatcher_send(self.hass, SIGNAL_ROUTINES_CHANGED)

    async def async_reconcile_subentries(self) -> None:
        """A routine whose entry was deleted in Home Assistant is deleted too."""
        async with self._lock:
            entry = self._entry()
            if entry is None:
                return
            present = set(self._routine_subentries(entry))
            gone = [
                r["id"]
                for r in self.routines
                if not r.get("companion_of") and r["id"] not in present
            ]
            if not gone:
                return
            for rid in gone:
                self._drop_companions(rid)
                self._runtime.pop(rid, None)
            self.routines = [r for r in self.routines if r["id"] not in gone]
            await self.async_reload()

    async def async_set_settings(self, raw: Any) -> dict[str, Any]:
        """Save the settings tab: which people routines can use, and an optional notify service."""
        if not isinstance(raw, dict):
            raise vol.Invalid("Settings must be an object.")
        hidden = raw.get("hidden_people", [])
        if not isinstance(hidden, list) or not all(
            isinstance(p, str) and p.startswith("person.") for p in hidden
        ):
            raise vol.Invalid("Hidden people must be person entities.")
        services = raw.get("notify_services", [])
        if not isinstance(services, list):
            raise vol.Invalid("Notification targets must be a list.")
        for notify in services:
            if not isinstance(notify, str) or not NOTIFY_SERVICE_RE.fullmatch(notify):
                raise vol.Invalid("Choose notification targets from the list.")
            if not self.hass.services.has_service("notify", notify.split(".", 1)[1]):
                raise vol.Invalid(
                    "One of those notification targets is no longer available."
                )
        timing = {}
        for key, (_default, low, high, label) in TIMING_SETTINGS.items():
            value = raw.get(key, self.settings[key])
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not low <= value <= high
            ):
                raise vol.Invalid(
                    f"{label} must be a whole number from {low} to {high}."
                )
            timing[key] = value
        order = raw.get("integration_order", self.settings["integration_order"])
        if not isinstance(order, list) or not all(
            isinstance(d, str) and DOMAIN_RE.fullmatch(d) for d in order
        ):
            raise vol.Invalid("The integration order must be a list of integrations.")
        lived_in = clean_lived_in(raw.get("lived_in", self.settings["lived_in"]))
        editor = raw.get("editor", self.settings.get("editor", "simple"))
        if editor not in ("simple", "full"):
            raise vol.Invalid("The editor is simple or full.")
        summary = clean_summary(
            raw.get("daily_summary", self.settings.get("daily_summary"))
        )
        day_types = clean_day_types(raw.get("day_types", self.settings["day_types"]))
        weekly = clean_weekly(raw.get("weekly_report", self.settings["weekly_report"]))
        holds = clean_holds(raw.get("holds", self.settings.get("holds")))
        quiet = clean_quiet(raw.get("quiet_hours", self.settings.get("quiet_hours")))
        guest_wifi = clean_guest_wifi(
            raw.get("guest_wifi", self.settings.get("guest_wifi"))
        )
        self.settings = {
            "hidden_people": sorted(set(hidden)),
            "notify_services": sorted(set(services)),
            "integration_order": list(dict.fromkeys(order)),
            "lived_in": lived_in,
            "editor": editor,
            "daily_summary": summary,
            "day_types": day_types,
            "weekly_report": weekly,
            "holds": holds,
            "quiet_hours": quiet,
            "missed_report": bool(
                raw.get("missed_report", self.settings.get("missed_report"))
            ),
            "away_summary": bool(
                raw.get("away_summary", self.settings.get("away_summary"))
            ),
            "guest_wifi": guest_wifi,
            # Set by going on vacation, not by the Settings tab.
            "vacation": self.settings.get("vacation"),
            "away_since": self.settings.get("away_since"),
            **timing,
        }
        self.away.start()
        self.spawn(self.lived_in.async_step(dt_util.now()))
        self._schedule_health_interval()
        await self._async_save()
        self._notify_listeners()
        self._spawn(self._refresh_calendars())
        self._spawn(self._refresh_upcoming())
        self._spawn(self._check_forecasts())
        return self.settings

    async def async_settings_changed(self) -> None:
        """Settings changed by vacation mode: apply, save and tell the panel."""
        self.spawn(self.lived_in.async_step(dt_util.now()))
        await self._async_save()
        self._notify_listeners()

    def send_summary(self, now: datetime) -> None:
        """Send the daily summary now."""
        self._send_notification(self.away.summary(now), f"{DOMAIN}_daily_summary")

    def send_weekly(self, now: datetime) -> None:
        """Send the weekly report now."""
        self._send_notification(self.away.weekly(now), f"{DOMAIN}_weekly_report")

    def reason_text(self, reason: str) -> str:
        """A run's reason in plain words, for the daily summary."""
        return REASON_WORDS.get(reason.split(":", 1)[0], reason.replace("_", " "))

    async def async_set_mode(self, mode: str) -> None:
        """Switch the house mode, and run what starts when it changes to that mode."""
        if mode not in MODES:
            raise vol.Invalid(f"Unknown house mode '{mode}'.")
        if mode == self.mode:
            return
        before = self.mode
        self.mode = mode
        now = dt_util.now()
        self._away_summary(before, mode, now)
        await self._async_save()
        for routine in [*self.routines, *self._aliases.values()]:
            if (
                routine["trigger"] == TRIGGER_MODE
                and routine["enabled"]
                and mode in routine["modes"]
                and self._allowed_day(routine, now)
            ):
                self._spawn(self._execute_routine(routine, now, scheduled=True))
        for routine in self.routines:
            self._evaluate_pending(routine)
        self.spawn(self.lived_in.async_step(now))
        self._notify_listeners()

    def _away_summary(self, before: str, mode: str, now: datetime) -> None:
        """Back from Away or Vacation: say what ran while you were gone."""
        away = ("away", "vacation")
        if mode in away and before not in away:
            self.settings["away_since"] = now.isoformat()
            return
        if before not in away or mode in away:
            return
        since = self.settings.pop("away_since", None)
        if not since or not self.settings.get("away_summary"):
            return
        start = datetime.fromisoformat(since)
        text = away_text(self.run_log.since(start), start, self.routines)
        self._spawn(self._notify(text, f"{DOMAIN}_away_summary"))

    async def async_set_paused(self, paused: bool) -> None:
        self.paused = paused
        await self._async_save()
        self._notify_listeners()

    def import_routine(self, raw: Any) -> bool:
        """Add a migrated routine unless one with the same id exists."""
        routine = normalize_routine(raw)
        if self._find(routine["id"]) is not None:
            return False
        self.routines.append(routine)
        return True

    async def async_save_all(self) -> None:
        await self._async_save()

    # ----- conditions (pure: no side effects) -----

    def _check(self, routine: dict[str, Any], now: datetime) -> tuple[bool, str | None]:
        """Return (passes, reason if blocked). Unknown or unavailable fails closed."""
        if routine["active_from"] and routine["active_to"]:
            if not _within_hours(now, routine["active_from"], routine["active_to"]):
                return False, "outside_hours"
        if routine["trigger"] == TRIGGER_EVERYONE_LEAVES and not self._all_away(
            routine["people"], routine["zone"]
        ):
            return False, "someone_home"
        if routine["trigger"] == TRIGGER_ROOM_CLEAR:
            if not all(self._state_is(eid, "off") for eid in routine["sensors"]):
                return False, "sensors_active"
            minutes = routine["clear_minutes"]
            if minutes > 0:
                since = self._rt(routine["id"])["clear_since"]
                if since is None:
                    return False, "clear_timer_not_started"
                if (now - since).total_seconds() < minutes * 60:
                    return False, "clear_timer_running"

        if routine["conditions"]:
            results: list[tuple[bool, str]] = []
            for cond in routine["conditions"]:
                ok = self._condition_ok(cond, now)
                results.append((ok, _CONDITION_REASON[cond["kind"]]))
            if routine["conditions_match"] == "any":
                if not any(ok for ok, _ in results):
                    return False, "extra_condition_not_met"
            else:
                for ok, reason in results:
                    if not ok:
                        return False, reason
        return True, None

    def _condition_ok(self, cond: dict[str, Any], now: datetime) -> bool:
        if cond["kind"] == "presence":
            return self._presence_ok(cond)
        if cond["kind"] == "sensors":
            return self._sensors_ok(cond, now)
        if cond["kind"] == "calendar":
            return self._calendar_ok(cond, now)
        if cond["kind"] == "mode":
            return self.mode in cond["modes"]
        if cond["kind"] == "workday":
            state = self.hass.states.get(cond["entity_id"])
            if state is None or state.state not in ("on", "off"):
                return False  # unknown: fail closed
            return (state.state == "on") == (cond["when"] == "workday")
        if cond["kind"] == "weather":
            return _weather_ok(self.hass.states.get(cond["entity_id"]), cond)
        if cond["kind"] == "charging":
            # Every phone on its charger; one unknown fails closed.
            for entity_id in cond["entities"]:
                state = self.hass.states.get(entity_id)
                if state is None or state.state not in CHARGING_STATES:
                    return False
            return True
        if cond["kind"] == "value":
            level = _as_float(self.hass.states.get(cond["entity_id"]))
            if level is None:
                return False  # unknown: fail closed
            if cond["below"] is not None and not level < cond["below"]:
                return False
            return cond["above"] is None or level > cond["above"]
        if cond["kind"] == "day_type":
            today = self.day_type(now, cond.get("person"))
            return today is not None and today in cond["types"]
        if cond["kind"] == "forecast":
            forecast = self._forecast_cache.get(cond["entity_id"])
            if forecast is None:
                return False  # not read yet: fail closed
            coming = forecast_matches(
                forecast, now, cond["hours"], cond["conditions"], cond["below"]
            )
            return coming == (cond["when"] == "coming")
        return self._state_is(cond["entity_id"], cond["state"])

    def _calendar_ok(self, cond: dict[str, Any], now: datetime) -> bool:
        """Today's events in the calendar. Not read yet, or unreadable, fails closed."""
        state = self.hass.states.get(cond["entity_id"])
        if state is None or state.state in UNKNOWN_STATES:
            return False
        today = now.date().isoformat()
        day = self._calendar_days.get(cond["entity_id"])
        if day is None or day[0] != today:
            if self._calendar_tried != today:
                # A new day: read the calendars again (once; the timer retries).
                self._spawn(self._refresh_calendars())
            return False
        return calendar_matches(cond, day[1])

    def day_type(self, now: datetime, person: str | None = None) -> str | None:
        """Today's kind of day (School day, Normal day…), for everyone or one
        person (their own kinds first). None: not read yet."""
        kinds = for_person(self.settings.get("day_types") or [], person)
        today = now.date().isoformat()
        titles: dict[str, list[str] | None] = {}
        for entity_id in day_type_calendars(kinds):
            day = self._calendar_days.get(entity_id)
            titles[entity_id] = day[1] if day and day[0] == today else None
        if kinds and any(t is None for t in titles.values()):
            if self._calendar_tried != today:
                self._spawn(self._refresh_calendars())
        return pick_day_type(kinds, titles)

    # ----- calendars -----

    async def _on_calendar_interval(self, _now: datetime) -> None:
        """Every CALENDAR_REFRESH: today's events and the coming days' events.

        A calendar both need is read once, for the longer span (yesterday to
        EVENTS_AHEAD), and today's titles are taken from that (1.33.0). All
        calendars are read at the same time.
        """
        now = dt_util.now()
        read, titles = await self._read_calendars_at_once(now, upcoming=True)
        await self._refresh_calendars(read=read, titles=titles, now=now)
        await self._refresh_upcoming(read=read, now=now)

    async def _read_calendars_at_once(
        self, now: datetime, *, upcoming: bool
    ) -> tuple[dict[str, list[dict[str, Any]] | None], dict[str, list[str] | None]]:
        """Read calendars in one go, each once.

        A calendar a condition uses and a "before an event" trigger (or a
        hold) uses too is read for the longer span, and today's titles are
        taken from that. A calendar only a condition uses is read for today.
        ``upcoming``: every "before an event" calendar is read now as well
        (else only the shared ones, and _refresh_upcoming reads the rest).
        Returns (events for the longer span, today's titles).
        """
        conditions = [] if self._calendar_refreshing else self._condition_calendars()
        coming = set(self._upcoming_calendars())
        longer = sorted(coming if upcoming else coming & set(conditions))
        today_only = [c for c in conditions if c not in coming]
        start = dt_util.start_of_local_day(now)
        end = start + timedelta(days=1)
        read, found = await asyncio.gather(
            self._read_events_many(longer, *self._upcoming_span(now)),
            asyncio.gather(*(self._read_calendar(c, start, end) for c in today_only)),
        )
        return read, dict(zip(today_only, found, strict=True))

    def _condition_calendars(self) -> list[str]:
        """Calendars a condition, a step's only if or a day type uses."""
        return sorted(
            {c for r in self.routines for c in _calendars(r)}
            | set(day_type_calendars(self.settings.get("day_types") or []))
        )

    def _upcoming_calendars(self) -> list[str]:
        """Calendars "before an event" triggers and calendar holds use."""
        return sorted(
            {
                r["watch_entity"]
                for r in [*self.routines, *self._aliases.values()]
                if r["trigger"] == TRIGGER_CALENDAR_EVENT
            }
            | {h["calendar"] for h in self.settings.get("holds") or []}
        )

    @staticmethod
    def _upcoming_span(now: datetime) -> tuple[datetime, datetime]:
        """What _refresh_upcoming reads: from a day ago to EVENTS_AHEAD."""
        return now - timedelta(days=1), now + EVENTS_AHEAD

    async def _read_events_many(
        self, entity_ids: list[str], start: datetime, end: datetime
    ) -> dict[str, list[dict[str, Any]] | None]:
        """_read_events for several calendars at the same time."""
        if not entity_ids:
            return {}
        found = await asyncio.gather(
            *(self._read_events(entity_id, start, end) for entity_id in entity_ids)
        )
        return dict(zip(entity_ids, found, strict=True))

    async def _refresh_calendars(
        self,
        read: dict[str, list[dict[str, Any]] | None] | None = None,
        now: datetime | None = None,
        titles: dict[str, list[str] | None] | None = None,
    ) -> None:
        """Read today's events from every calendar a condition uses.

        ``read``: calendars already read for a span that covers today (the
        shared read, _read_calendars_at_once); today's titles are taken from
        those. ``titles``: calendars already read for today. The others are
        read now, at the same time.
        """
        if self._calendar_refreshing:
            return
        wanted = self._condition_calendars()
        if not wanted:
            self._calendar_days.clear()
            return
        read = read or {}
        titles = titles or {}
        self._calendar_refreshing = True
        try:
            now = now or dt_util.now()
            self._calendar_tried = now.date().isoformat()
            start = dt_util.start_of_local_day(now)
            end = start + timedelta(days=1)
            to_read = [e for e in wanted if e not in read and e not in titles]
            found = await asyncio.gather(
                *(self._read_calendar(e, start, end) for e in to_read)
            )
            titles_of: dict[str, list[str] | None] = {
                e: titles[e] for e in wanted if e in titles and e not in read
            }
            titles_of.update(zip(to_read, found, strict=True))
            again: list[str] = []
            for entity_id in wanted:
                if entity_id in read:
                    try:
                        titles_of[entity_id] = _titles_between(
                            read[entity_id], start, end
                        )
                    except ValueError:
                        again.append(entity_id)
            # An event without a time we can read: read today's span after all.
            for entity_id in again:
                titles_of[entity_id] = await self._read_calendar(entity_id, start, end)
            for entity_id in wanted:
                day_titles = titles_of[entity_id]
                if day_titles is None:
                    self._calendar_days.pop(entity_id, None)
                else:
                    self._calendar_days[entity_id] = (
                        now.date().isoformat(),
                        day_titles,
                    )
            for gone in set(self._calendar_days) - set(wanted):
                del self._calendar_days[gone]
        finally:
            self._calendar_refreshing = False
        for routine in self.routines:
            if _calendars(routine) or _uses_day_type(routine):
                self._evaluate_pending(routine)
        self._notify_listeners()

    async def _read_calendar(
        self, entity_id: str, start: datetime, end: datetime
    ) -> list[str] | None:
        """The titles of a calendar's events between two times.

        None when it can't be read (an unreadable calendar fails closed).
        """
        events = await self._read_events(entity_id, start, end)
        if events is None:
            return None
        return [str(e.get("summary") or "") for e in events]

    def _presence_ok(self, cond: dict[str, Any]) -> bool:
        """Who is in or away from the place. Unknown or unavailable people fail closed."""
        key = self._zone_key(cond["zone"])
        if key is None:
            return False
        inside: list[bool] = []
        for pid in cond["people"]:
            state = self.hass.states.get(pid)
            if state is None or state.state in UNKNOWN_STATES:
                return False
            inside.append(state.state == key)
        if cond["who"] == "everyone":
            return all(inside) if cond["where"] == "in" else not any(inside)
        return any(inside) if cond["where"] == "in" else not all(inside)

    def _sensors_ok(self, cond: dict[str, Any], now: datetime) -> bool:
        """Quiet: all off for N minutes. Active: any on, or one went off within N minutes.

        A sensor condition with no sensors picked can't be saved; one that
        arrives anyway (an old import) is treated as not set up and ignored,
        and Why not? says so.
        """
        if not cond.get("sensors"):
            return True
        states: list[State] = []
        for eid in cond["sensors"]:
            state = self.hass.states.get(eid)
            if state is None or state.state in UNKNOWN_STATES:
                return False
            states.append(state)
        any_on = any(state.state == "on" for state in states)
        window = cond["minutes"] * 60
        if cond["mode"] == "quiet":
            since_change = (
                now - max(state.last_changed for state in states)
            ).total_seconds()
            return not any_on and since_change >= window
        seen = [self._last_on[eid] for eid in cond["sensors"] if eid in self._last_on]
        recent = bool(seen) and (now - max(seen)).total_seconds() < window
        return any_on or recent

    # ----- watching sensors and people -----

    def _watch_conditions(self, routine: dict[str, Any]) -> None:
        rid = routine["id"]
        self._drop_handle(rid, H_WATCH)
        self._drop_handle(rid, H_CLEAR)
        self._drop_handle(rid, H_COND)
        if not uses_conditions(routine):
            return

        rt = self._rt(rid)
        if routine["trigger"] == TRIGGER_ROOM_CLEAR:
            all_off = all(self._state_is(eid, "off") for eid in routine["sensors"])
            if not all_off:
                rt["clear_since"] = None
            elif rt["clear_since"] is None:
                # Started from what HA can see now. For room_clear, don't act on this
                # until a real off-transition happens (no lights-off just because of a restart).
                rt["clear_since"] = dt_util.now()
                rt["armed"] = False

        entities: set[str] = set(_watched_entities(routine))

        now = dt_util.now()
        for cond in routine["conditions"]:
            if cond["kind"] == "sensors":
                for eid in cond["sensors"]:
                    state = self.hass.states.get(eid)
                    if state is not None and state.state == "on":
                        self._last_on[eid] = now

        @callback
        def _changed(event: Event[EventStateChangedData]) -> None:
            new = event.data.get("new_state")
            entity_id = event.data.get("entity_id")
            if str(entity_id).startswith("calendar."):
                # An event started or ended, or the calendar came back: read it again.
                self._spawn(self._refresh_calendars())
            elif new is not None and new.state == "on":
                self._last_on[new.entity_id] = dt_util.now()
            if _attributes_only(event) and not str(entity_id).startswith(
                ATTRIBUTE_DOMAINS
            ):
                # Only attributes changed (a phone's GPS fix on a person, a
                # sensor's extra details): these conditions read the state, so
                # nothing they decide can change (1.34.2).
                return
            self._on_condition_entity_changed(routine, entity_id)

        unsub = async_track_state_change_event(self.hass, sorted(entities), _changed)
        self._set_handle(rid, H_WATCH, unsub)
        self._arm_clear_timer(routine)
        self._arm_condition_timers(routine)

    def _on_condition_entity_changed(
        self, routine: dict[str, Any], entity_id: str | None
    ) -> None:
        rid = routine["id"]
        if routine["trigger"] == TRIGGER_ROOM_CLEAR and entity_id in routine["sensors"]:
            rt = self._rt(rid)
            if all(self._state_is(eid, "off") for eid in routine["sensors"]):
                if rt["clear_since"] is None:
                    rt["clear_since"] = dt_util.now()
                    rt["armed"] = True
            else:
                # Any motion restarts the quiet period from zero.
                rt["clear_since"] = None
                self._drop_handle(rid, H_CLEAR)
            self._arm_clear_timer(routine)
        self._arm_condition_timers(routine)
        self._evaluate_pending(routine)
        self._notify_listeners()

    def _arm_clear_timer(self, routine: dict[str, Any]) -> None:
        """Wake up when a room's quiet period finishes."""
        rid = routine["id"]
        rt = self._rt(rid)
        self._drop_handle(rid, H_CLEAR)
        if routine["trigger"] != TRIGGER_ROOM_CLEAR or routine["clear_minutes"] <= 0:
            return
        if rt["clear_since"] is None or not routine["enabled"] or not rt["armed"]:
            return
        end = rt["clear_since"] + timedelta(minutes=routine["clear_minutes"])

        @callback
        def _fire(_fired: datetime) -> None:
            self._drop_handle(rid, H_CLEAR)
            self._on_clear_elapsed(routine)
            self._notify_listeners()

        self._set_handle(rid, H_CLEAR, async_track_point_in_time(self.hass, _fire, end))

    def _arm_condition_timers(self, routine: dict[str, Any]) -> None:
        """Wake a waiting routine at the moment a quiet or active period from its sensor conditions changes."""
        rid = routine["id"]
        self._drop_handle(rid, H_COND)
        if not routine["enabled"]:
            return
        now = dt_util.now()
        due: list[datetime] = []
        for cond in routine["conditions"]:
            if cond["kind"] != "sensors":
                continue
            states = [self.hass.states.get(eid) for eid in cond["sensors"]]
            if any(state is None or state.state in UNKNOWN_STATES for state in states):
                continue
            typed = [state for state in states if state is not None]
            if any(state.state == "on" for state in typed):
                continue  # still on: the period starts when it goes off
            if cond["mode"] == "quiet":
                start: datetime | None = max(state.last_changed for state in typed)
            else:
                seen = [
                    self._last_on[eid]
                    for eid in cond["sensors"]
                    if eid in self._last_on
                ]
                start = max(seen) if seen else None
            if start is None:
                continue
            flip = start + timedelta(minutes=cond["minutes"])
            if flip > now:
                due.append(flip)
        if not due:
            return

        @callback
        def _fire(_fired: datetime) -> None:
            self._drop_handle(rid, H_COND)
            self._evaluate_pending(routine)
            self._notify_listeners()

        self._set_handle(
            rid, H_COND, async_track_point_in_time(self.hass, _fire, min(due))
        )

    def _on_clear_elapsed(self, routine: dict[str, Any]) -> None:
        if routine["trigger"] == TRIGGER_ROOM_CLEAR:
            rt = self._rt(routine["id"])
            if rt["armed"]:
                rt["armed"] = False  # once per quiet period
                self._spawn(
                    self._execute_routine(routine, dt_util.now(), scheduled=True)
                )
            return
        self._evaluate_pending(routine)

    # ----- waiting (scheduled or presence runs blocked by conditions) -----

    def _wait_ends(self, routine: dict[str, Any], now: datetime) -> datetime | None:
        """When waiting for conditions stops today, or None when the routine doesn't wait."""
        if not uses_conditions(routine) or routine.get("wait", "day") == "no":
            return None
        midnight = dt_util.start_of_local_day(now) + timedelta(days=1)
        if routine.get("wait") == "minutes":
            since = dt_util.parse_datetime(
                self._rt(routine["id"])["pending_since"] or ""
            )
            ends = (since or now) + timedelta(minutes=routine["wait_minutes"])
            ends = min(ends, midnight)
            return ends if ends > now else None
        if routine.get("wait") == "until":
            until = datetime.combine(
                now.date(), _parse_time(routine["wait_until"]), tzinfo=now.tzinfo
            )
            return until if until > now else None
        return midnight

    def _arm_pending(self, routine: dict[str, Any], now: datetime) -> bool:
        """Wait for the conditions; False when this routine doesn't wait now."""
        rid = routine["id"]
        rt = self._rt(rid)
        if rt["pending_date"] != now.date().isoformat():
            rt["pending_since"] = None  # a new wait
        ends = self._wait_ends(routine, now)
        if ends is None:
            return False
        newly = rt["pending_date"] != now.date().isoformat()
        rt["pending_date"] = now.date().isoformat()
        rt["pending_since"] = rt["pending_since"] or now.isoformat()
        rt["state"] = "waiting"

        @callback
        def _expire(_fired: datetime) -> None:
            self._expire_pending(routine)

        self._set_handle(
            rid, H_EXPIRE, async_track_point_in_time(self.hass, _expire, ends)
        )
        if newly:
            self._save_soon()
        return True

    def _resume_waiting(self, routine: dict[str, Any], now: datetime) -> None:
        """After a restart or a reload: carry on waiting, or let a stale wait go."""
        rt = self._rt(routine["id"])
        if rt["pending_date"] is None:
            return
        if (
            rt["pending_date"] == now.date().isoformat()
            and routine["enabled"]
            and self._arm_pending(routine, now)
        ):
            self._evaluate_pending(routine)
            return
        self._expire_pending(routine)

    def _clear_pending(self, routine: dict[str, Any]) -> None:
        rid = routine["id"]
        rt = self._rt(rid)
        if rt["pending_date"] is not None:
            self._save_soon()
        rt["pending_date"] = None
        rt["pending_since"] = None
        self._drop_handle(rid, H_EXPIRE)
        rt["started_by"] = None
        if rt["state"] == "waiting":
            rt["state"] = "idle"

    def _expire_pending(self, routine: dict[str, Any]) -> None:
        rid = routine["id"]
        if self._rt(rid)["pending_date"] is None:
            return
        self._clear_pending(routine)
        rt = self._rt(rid)
        rt["last_result"] = "conditions_not_met"
        rt["state"] = "skipped"
        self._record(rid, "skipped", "conditions_not_met")
        _LOGGER.info(
            "Routine '%s' skipped: conditions not met in time.",
            routine["name"],
        )
        self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "conditions_not_met")
        if routine["notify_on_skip"]:
            self._send_notification(
                f"Routine '{routine['name']}' skipped – conditions were not met in time today."
            )
        self._notify_listeners()

    def _evaluate_pending(self, routine: dict[str, Any]) -> None:
        rt = self._rt(routine["id"])
        if rt["pending_date"] is None:
            return
        now = dt_util.now()
        if rt["pending_date"] != now.date().isoformat():
            self._expire_pending(routine)
            return
        if not routine["enabled"] or self.paused:
            self._clear_pending(routine)
            return
        source = self._source(routine)
        if not self._trigger_holds(source):
            # What started the run is over (everyone left again, the door closed):
            # running now would be for something that no longer happened.
            self._clear_pending(routine)
            rt["last_result"] = "trigger_ended"
            rt["state"] = "skipped"
            self._record(routine["id"], "skipped", "trigger_ended")
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "trigger_ended")
            self._notify_listeners()
            return
        ok, _reason = self._check(source, now)
        if ok:
            self._clear_pending(routine)
            self._spawn(self._execute_routine(routine, now, scheduled=True))

    def _cooling_down(self, routine: dict[str, Any], now: datetime) -> bool:
        """Whether the routine last ran less than its cooldown ago."""
        minutes = routine.get("cooldown_minutes") or 0
        last = dt_util.parse_datetime(self._rt(routine["id"])["last_run"] or "")
        return bool(minutes and last and now - last < timedelta(minutes=minutes))

    def _trigger_holds(self, routine: dict[str, Any]) -> bool:
        """Whether what the trigger reacted to is still so (for a run that waited)."""
        trigger = routine["trigger"]
        people = routine["people"]
        zone = routine["zone"]
        if trigger == TRIGGER_SOMEONE_ARRIVES:
            return self._everyone_away(people, zone) is False
        if trigger == TRIGGER_EVERYONE_LEAVES:
            return self._everyone_away(people, zone) is True
        if trigger in PER_PERSON_TRIGGERS:
            key = self._zone_key(zone)
            places = [self._place(p) for p in people]
            if trigger == TRIGGER_PERSON_ARRIVES:
                return any(place == key for place in places)
            return any(place is not None and place != key for place in places)
        if trigger == TRIGGER_MODE:
            return self.mode in routine["modes"]
        if trigger in STATE_TRIGGERS:
            state = self.hass.states.get(routine["watch_entity"])
            return state is not None and state.state in routine["watch_states"]
        if trigger in VALUE_TRIGGERS:
            value = _as_float(self.hass.states.get(routine["value_entity"]))
            if value is None:
                return False
            threshold = float(routine["value_threshold"])
            return (
                value > threshold
                if trigger == TRIGGER_VALUE_ABOVE
                else value < threshold
            )
        return True

    # ----- time and sun triggers -----

    def _next_run_datetime(
        self, routine: dict[str, Any], after: datetime
    ) -> datetime | None:
        if not routine["enabled"]:
            return None
        trigger = routine["trigger"]
        if trigger in PLANNED_TRIGGERS:
            return self._next_planned(routine, after)
        if trigger not in (TRIGGER_TIME, TRIGGER_SUNRISE, TRIGGER_SUNSET):
            return None
        offset = timedelta(minutes=routine["offset_minutes"])
        vary = routine.get("random_minutes", 0)

        def shift(day: datetime) -> timedelta:
            return random_shift(routine["id"], day.date().isoformat(), vary)

        # With dates, the next allowed day can be most of a year away.
        seasonal = bool(routine.get("from_date"))
        if trigger == TRIGGER_TIME:
            target = _parse_time(routine["time"])
            for day_offset in range(-1, 368 if seasonal else 8):
                candidate_date = (after + timedelta(days=day_offset)).date()
                base = datetime.combine(candidate_date, target, tzinfo=after.tzinfo)
                candidate = base + shift(base) + self._hot(routine, base)
                if candidate > after and self._allowed_day(routine, base):
                    return candidate
            return None

        event = SUN_EVENT_SUNRISE if trigger == TRIGGER_SUNRISE else SUN_EVENT_SUNSET
        cursor = after
        for _ in range(370 if seasonal else 10):
            try:
                event_dt = get_astral_event_next(self.hass, event, cursor)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Sun event lookup failed: %s", err)
                return None
            if event_dt is None:
                return None
            base = dt_util.as_local(event_dt) + offset
            candidate = _clamp(base + shift(base), routine) + self._hot(routine, base)
            if candidate > after and self._allowed_day(routine, base):
                return candidate
            cursor = event_dt + timedelta(minutes=1)
        return None

    def _hot(self, routine: dict[str, Any], base: datetime) -> timedelta:
        """How far a hot day (by the forecast) moves this day's run."""
        rule = routine.get("hot_shift")
        if not rule:
            return timedelta()
        forecast = self._forecast_cache.get(rule["entity_id"])
        return timedelta(minutes=shift_minutes(rule, forecast, base.date()))

    def _next_planned(
        self, routine: dict[str, Any], after: datetime
    ) -> datetime | None:
        """The next run of a trigger planned from another entity."""
        trigger = routine["trigger"]
        offset = timedelta(minutes=routine["offset_minutes"])
        if trigger == TRIGGER_ALARM:
            alarm = alarm_time(self.hass.states.get(routine["watch_entity"]))
            if alarm is None:
                return None
            when = dt_util.as_local(alarm + offset)
            return when if when > after and self._allowed_day(routine, when) else None
        if trigger == TRIGGER_CALENDAR_EVENT:
            events = self._upcoming.get(routine["watch_entity"]) or []
            for start in event_starts(events, routine["watch_item"]):
                when = dt_util.as_local(start + offset)
                if when > after and self._allowed_day(routine, when):
                    return when
            return None
        # The cheapest slot inside the routine's hours, today or tomorrow. Today's
        # choice is kept once made, so new prices can't move it into the past.
        rt = self._rt(routine["id"])
        state = self.hass.states.get(routine["value_entity"])
        slots = price_slots(dict(state.attributes), after) if state else []
        for day_offset in (0, 1):
            day = dt_util.as_local(after) + timedelta(days=day_offset)
            begin = datetime.combine(
                day.date(), _parse_time(routine["active_from"]), tzinfo=day.tzinfo
            )
            end = datetime.combine(
                day.date(), _parse_time(routine["active_to"]), tzinfo=day.tzinfo
            )
            if end <= begin:
                end += timedelta(days=1)
            key = begin.date().isoformat()
            kept = (rt.get("cheapest") or {}).get(key)
            chosen = dt_util.parse_datetime(kept) if kept else None
            if chosen is None:
                chosen = cheapest_start(slots, begin, end)
                if chosen is None:
                    # Prices not known (yet): the start of the hours.
                    chosen = begin
                else:
                    rt["cheapest"] = {key: chosen.isoformat()}
            when = dt_util.as_local(chosen)
            if when > after and self._allowed_day(routine, when):
                return when
        return None

    def _watch_planned(self, routine: dict[str, Any]) -> None:
        """Plan again when the alarm or the prices change."""
        rid = routine["id"]
        self._drop_handle(rid, H_PLANNED)
        trigger = routine["trigger"]
        entity = (
            routine["watch_entity"]
            if trigger == TRIGGER_ALARM
            else routine["value_entity"]
            if trigger == TRIGGER_CHEAPEST
            else None
        )
        if not entity:
            return

        @callback
        def _changed(_event: Event[EventStateChangedData]) -> None:
            self._schedule_one(routine, dt_util.now())
            self._notify_listeners()

        self._set_handle(
            rid,
            H_PLANNED,
            async_track_state_change_event(self.hass, [entity], _changed),
        )

    async def _refresh_upcoming(
        self,
        read: dict[str, list[dict[str, Any]] | None] | None = None,
        now: datetime | None = None,
    ) -> None:
        """Read the coming days' events for "before an event" triggers, and plan them.

        ``read``: calendars already read for the same span (shared with
        today's read); the rest are read at the same time.
        """
        wanted = self._upcoming_calendars()
        now = now or dt_util.now()
        read = dict(read or {})
        read.update(
            await self._read_events_many(
                [e for e in wanted if e not in read], *self._upcoming_span(now)
            )
        )
        if self._unloaded:
            return
        for entity_id in wanted:
            events = read.get(entity_id)
            if events is not None:
                self._upcoming[entity_id] = events
        for gone in set(self._upcoming) - set(wanted):
            del self._upcoming[gone]
        for routine in [*self.routines, *self._aliases.values()]:
            if routine["trigger"] == TRIGGER_CALENDAR_EVENT:
                self._schedule_one(routine, dt_util.now())
        self._notify_listeners()

    async def _read_events(
        self, entity_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]] | None:
        if self.hass.states.get(
            entity_id
        ) is None or not self.hass.services.has_service("calendar", "get_events"):
            return None
        try:
            async with asyncio.timeout(CALENDAR_TIMEOUT_SECONDS):
                response = await self.hass.services.async_call(
                    "calendar",
                    "get_events",
                    {
                        "entity_id": entity_id,
                        "start_date_time": start.isoformat(),
                        "end_date_time": end.isoformat(),
                    },
                    blocking=True,
                    return_response=True,
                )
        except Exception as err:  # noqa: BLE001 - the routine just isn't planned
            _LOGGER.warning("Couldn't read %s: %s", entity_id, err)
            return None
        calendar = (response or {}).get(entity_id)
        events = calendar.get("events") if isinstance(calendar, dict) else None
        return (
            [e for e in events if isinstance(e, dict)]
            if isinstance(events, list)
            else []
        )

    async def _check_forecasts(self, _now: datetime | None = None) -> None:
        """Run forecast routines when the forecast newly shows what they watch for."""
        routines = [
            r
            for r in [*self.routines, *self._aliases.values()]
            if r["trigger"] == TRIGGER_FORECAST and r["enabled"]
        ]
        now = dt_util.now()
        # Every weather is read at the same time (1.33.0), the ones the
        # conditions use and the ones forecast triggers watch.
        wanted = sorted(
            self._forecast_entities() | {r["watch_entity"] for r in routines}
        )
        found = await asyncio.gather(*(self._read_forecast(e) for e in wanted))
        if self._unloaded:
            return
        read = dict(zip(wanted, found, strict=True))
        forecasts: dict[str, list[dict[str, Any]] | None] = {}
        for entity in self._forecast_entities():
            forecasts[entity] = read[entity]
            if forecasts[entity] is not None:
                self._forecast_cache[entity] = forecasts[entity] or []
        for gone in set(self._forecast_cache) - set(self._forecast_entities()):
            del self._forecast_cache[gone]
        # A new forecast can move a hot-day routine's time.
        for routine in self.routines:
            if not routine.get("hot_shift") or not routine["enabled"]:
                continue
            moved = shift_minutes(
                routine["hot_shift"],
                self._forecast_cache.get(routine["hot_shift"]["entity_id"]),
                now.date(),
            )
            rt = self._rt(routine["id"])
            if rt.get("hot_moved") != moved:
                rt["hot_moved"] = moved
                self._schedule_one(routine, now)
        for routine in routines:
            entity = routine["watch_entity"]
            if entity not in forecasts:
                forecasts[entity] = read[entity]
            forecast = forecasts[entity]
            if forecast is None:
                continue  # unknown: no change either way
            rt = self._rt(routine["id"])
            hit = forecast_matches(
                forecast,
                now,
                routine["forecast_hours"],
                routine["watch_states"],
                routine["value_threshold"],
            )
            was = rt.get("forecast_hit")
            rt["forecast_hit"] = hit
            if hit and was is False and self._allowed_day(routine, now):
                self._spawn(self._execute_routine(routine, now, scheduled=True))

    def _forecast_entities(self) -> set[str]:
        """Weathers a forecast condition (or a step's only if) looks at."""
        found = set()
        for routine in self.routines:
            for cond in [
                *routine["conditions"],
                *(a["only_if"] for a in routine["actions"] if a.get("only_if")),
            ]:
                if cond["kind"] == "forecast":
                    found.add(cond["entity_id"])
            if routine.get("hot_shift"):
                found.add(routine["hot_shift"]["entity_id"])
        return found

    async def _read_forecast(self, entity_id: str) -> list[dict[str, Any]] | None:
        if self.hass.states.get(
            entity_id
        ) is None or not self.hass.services.has_service("weather", "get_forecasts"):
            return None
        for kind in ("hourly", "twice_daily", "daily"):
            try:
                async with asyncio.timeout(CALENDAR_TIMEOUT_SECONDS):
                    response = await self.hass.services.async_call(
                        "weather",
                        "get_forecasts",
                        {"entity_id": entity_id, "type": kind},
                        blocking=True,
                        return_response=True,
                    )
            except Exception:  # noqa: BLE001 - not every forecast type is offered
                continue
            found = (response or {}).get(entity_id)
            entries = found.get("forecast") if isinstance(found, dict) else None
            if isinstance(entries, list) and entries:
                return [e for e in entries if isinstance(e, dict)]
        return None

    def _schedule_one(self, routine: dict[str, Any], after: datetime) -> None:
        rid = routine["id"]
        rt = self._rt(rid)
        self._drop_handle(rid, H_TIME)
        next_dt = self._next_run_datetime(routine, after)
        rt["next_run"] = next_dt.isoformat() if next_dt else None
        if next_dt is None:
            return

        @callback
        def _fire(fired: datetime) -> None:
            self._schedule_one(routine, fired + timedelta(seconds=2))
            self._spawn(self._execute_routine(routine, fired, scheduled=True))

        self._set_handle(
            rid, H_TIME, async_track_point_in_time(self.hass, _fire, next_dt)
        )
        self._schedule_heads_up(routine, next_dt, after)

    def _schedule_heads_up(
        self, routine: dict[str, Any], next_dt: datetime, after: datetime
    ) -> None:
        """A notification shortly before a scheduled run, with a Skip button."""
        rid = routine["id"]
        self._drop_handle(rid, "heads_up")
        minutes = routine.get("heads_up_minutes") or 0
        notice_at = next_dt - timedelta(minutes=minutes)
        if not minutes or notice_at <= after:
            return

        @callback
        def _notice(_now: datetime) -> None:
            self._drop_handle(rid, "heads_up")
            if self.paused or routine.get("skip_next"):
                return
            if (
                until := self.paused_until(routine)
            ) and dt_util.now().date().isoformat() <= until:
                return
            at = dt_util.as_local(next_dt).strftime("%H:%M")
            self._send_notification(
                f"Routine '{routine['name']}' runs at {at}.",
                f"daily_routine_heads_up_{rid}",
                actions=[{"action": f"{SKIP_ACTION}{rid}", "title": "Skip it"}],
            )

        self._set_handle(
            rid, "heads_up", async_track_point_in_time(self.hass, _notice, notice_at)
        )

    def _maybe_catch_up(self, routine: dict[str, Any], now: datetime) -> None:
        """After a restart, run a time routine that was missed by a few minutes."""
        if routine["trigger"] != TRIGGER_TIME or routine["catch_up_minutes"] <= 0:
            return
        if not routine["enabled"] or not self._allowed_day(routine, now):
            return
        target = datetime.combine(
            now.date(), _parse_time(routine["time"]), tzinfo=now.tzinfo
        )
        window_end = target + timedelta(minutes=routine["catch_up_minutes"])
        if (
            target <= now < window_end
            and self._rt(routine["id"])["last_run_date"] != now.date().isoformat()
        ):
            _LOGGER.info("Catching up missed routine '%s'", routine["name"])
            self._spawn(self._run_catch_up(routine, window_end))

    async def _run_catch_up(
        self, routine: dict[str, Any], window_end: datetime
    ) -> None:
        """Try a missed run, and keep trying each minute while a condition blocks it, until the window closes."""
        rid = routine["id"]
        now = dt_util.now()
        if (
            not routine["enabled"]
            or self._rt(rid)["last_run_date"] == now.date().isoformat()
        ):
            return
        ran = await self._execute_routine(routine, now, scheduled=True)
        if ran or not uses_conditions(routine):
            return
        retry_at = now + timedelta(minutes=1)
        if retry_at >= window_end:
            _LOGGER.warning(
                "Routine '%s' was still blocked when its catch-up window closed.",
                routine["name"],
            )
            return

        @callback
        def _again(_fired: datetime) -> None:
            self._spawn(self._run_catch_up(routine, window_end))

        self._set_handle(
            rid, H_CATCH_UP, async_track_point_in_time(self.hass, _again, retry_at)
        )

    # ----- presence triggers -----

    def _watch_presence(self, routine: dict[str, Any]) -> None:
        rid = routine["id"]
        trigger = routine["trigger"]
        self._drop_handle(rid, H_PRESENCE)
        if trigger not in PRESENCE_TRIGGERS:
            return
        people = list(routine["people"])
        if trigger in PER_PERSON_TRIGGERS:
            self._watch_each_person(routine, people)
            return
        rt = self._rt(rid)
        zone = routine["zone"]
        # Last known answer; None until everyone's place is known (after a restart,
        # trackers loading must not look like leaving or arriving).
        rt["all_away"] = self._everyone_away(people, zone)

        @callback
        def _on_change(_event: Event[EventStateChangedData]) -> None:
            if not routine["enabled"]:
                return
            now = dt_util.now()
            is_away = self._everyone_away(people, zone)
            if is_away is None:
                return  # a tracker is unknown or unavailable: not a move
            was_away = rt["all_away"]
            rt["all_away"] = is_away
            if was_away is None or not self._allowed_day(routine, now):
                return
            leaves = trigger == TRIGGER_EVERYONE_LEAVES and is_away and not was_away
            arrives = trigger == TRIGGER_SOMEONE_ARRIVES and was_away and not is_away
            if leaves or arrives:
                new = _event.data.get("new_state")
                self._note_who(rid, new.name if new else "Someone")
                self._spawn(self._execute_routine(routine, now, scheduled=True))

        unsub = async_track_state_change_event(self.hass, people, _on_change)
        self._set_handle(rid, H_PRESENCE, unsub)

    def _watch_each_person(self, routine: dict[str, Any], people: list[str]) -> None:
        """Leave or arrive for any one chosen person, judged on each person's own change."""
        rid = routine["id"]
        trigger = routine["trigger"]
        zone = routine["zone"]
        # Each person's last known place, so a move through unavailable
        # (home, unavailable, away) still counts once, and a blip doesn't.
        places: dict[str, str] = self._rt(rid)["places"]
        places.clear()
        for person in people:
            if (place := self._place(person)) is not None:
                places[person] = place

        @callback
        def _changed(event: Event[EventStateChangedData]) -> None:
            if not routine["enabled"]:
                return
            new = event.data.get("new_state")
            key = self._zone_key(zone)
            if new is None or key is None or new.state in UNKNOWN_STATES:
                return
            person = event.data["entity_id"]
            before = places.get(person)
            places[person] = new.state
            if before is None:
                return  # first known place: nothing to compare with
            was_in = before == key
            is_in = new.state == key
            leaves = trigger == TRIGGER_PERSON_LEAVES and was_in and not is_in
            arrives = trigger == TRIGGER_PERSON_ARRIVES and is_in and not was_in
            now = dt_util.now()
            if (leaves or arrives) and self._allowed_day(routine, now):
                self._note_who(rid, new.name)
                self._spawn(self._execute_routine(routine, now, scheduled=True))

        unsub = async_track_state_change_event(self.hass, people, _changed)
        self._set_handle(rid, H_PRESENCE, unsub)

    def _watch_value(self, routine: dict[str, Any]) -> None:
        rid = routine["id"]
        self._drop_handle(rid, H_VALUE)
        trigger = routine["trigger"]
        if trigger not in VALUE_TRIGGERS or not routine["enabled"]:
            return
        threshold = float(routine["value_threshold"])
        rt = self._rt(rid)
        # The last number seen: a sensor coming back from unavailable only counts
        # as crossing if it's now on the other side of where it last was.
        rt["last_value"] = _as_float(self.hass.states.get(routine["value_entity"]))

        @callback
        def _changed(event: Event[EventStateChangedData]) -> None:
            new = _as_float(event.data.get("new_state"))
            if new is None:
                return
            old = rt["last_value"]
            rt["last_value"] = new
            if old is None:
                return  # first number: nothing to compare with
            if trigger == TRIGGER_VALUE_ABOVE:
                crossed = new > threshold and old <= threshold
            else:
                crossed = new < threshold and old >= threshold
            now = dt_util.now()
            if crossed and self._allowed_day(routine, now):
                self._spawn(self._execute_routine(routine, now, scheduled=True))

        unsub = async_track_state_change_event(
            self.hass, [routine["value_entity"]], _changed
        )
        self._set_handle(rid, H_VALUE, unsub)

    def _watch_state(self, routine: dict[str, Any]) -> None:
        """Run when the device changes into one of the chosen states (after the hold time, if any)."""
        rid = routine["id"]
        self._drop_handle(rid, H_STATE)
        self._drop_handle(rid, H_HOLD)
        if routine["trigger"] not in STATE_TRIGGERS:
            return
        rt = self._rt(rid)
        entity = routine["watch_entity"]
        wanted = routine["watch_states"]
        current = self.hass.states.get(entity)
        if rt.get("seen_state") is None and current is not None:
            rt["seen_state"] = current.state  # first time: remember where the device is
        if not routine["enabled"]:
            return

        @callback
        def _changed(event: Event[EventStateChangedData]) -> None:
            new = event.data.get("new_state")
            if new is None:
                return
            if new.state in UNKNOWN_STATES and new.state not in wanted:
                return  # gone quiet for a moment: compare with where it was before
            before = rt.get("seen_state")
            if before is None:
                old = event.data.get("old_state")
                before = old.state if old is not None else None
            if rt.get("seen_state") != new.state:
                # Saved only when it moves: a device reporting new attributes
                # (a player's position, a sensor's details) leaves it as it was.
                rt["seen_state"] = new.state
                self._store.async_delay_save(self._payload, SEEN_SAVE_DELAY_SECONDS)
            if new.state not in wanted:
                self._drop_handle(rid, H_HOLD)
                return
            if before in wanted:
                return  # moving between chosen states, or back after a blip: already counted
            self._start_state_run(routine)

        unsub = async_track_state_change_event(self.hass, [entity], _changed)
        self._set_handle(rid, H_STATE, unsub)

    def _watch_press(self, routine: dict[str, Any]) -> None:
        """Run when its NFC tag is scanned, or its button pressed."""
        rid = routine["id"]
        self._drop_handle(rid, H_PRESS)
        trigger = routine["trigger"]
        if trigger not in PRESS_TRIGGERS or not routine["enabled"]:
            return
        if trigger == TRIGGER_BUTTON:
            wanted = routine["watch_states"]

            @callback
            def _pressed(event: Event[EventStateChangedData]) -> None:
                new, old = event.data.get("new_state"), event.data.get("old_state")
                # An event entity's state is the time of its last press; one
                # coming back after a restart (or from unavailable) isn't a press.
                if new is None or old is None or new.state in UNKNOWN_STATES:
                    return
                if old.state in UNKNOWN_STATES or new.state == old.state:
                    return
                if wanted and new.attributes.get("event_type") not in wanted:
                    return
                self._run_state_routine(routine)

            unsub = async_track_state_change_event(
                self.hass, [routine["watch_entity"]], _pressed
            )
        else:
            tag = routine["watch_item"]

            @callback
            def _scanned(event: Event) -> None:
                if event.data.get("tag_id") == tag:
                    self._run_state_routine(routine)

            unsub = self.hass.bus.async_listen("tag_scanned", _scanned)
        self._set_handle(rid, H_PRESS, unsub)

    def _start_state_run(self, routine: dict[str, Any]) -> None:
        rid = routine["id"]
        if routine["hold_minutes"] <= 0:
            self._run_state_routine(routine)
            return

        @callback
        def _held(_now: datetime) -> None:
            self._handles.pop((rid, H_HOLD), None)
            state = self.hass.states.get(routine["watch_entity"])
            if state is not None and state.state in routine["watch_states"]:
                self._run_state_routine(routine)

        unsub = async_call_later(self.hass, routine["hold_minutes"] * 60, _held)
        self._set_handle(rid, H_HOLD, unsub)

    def _run_state_routine(self, routine: dict[str, Any]) -> None:
        now = dt_util.now()
        if self._allowed_day(routine, now):
            self._spawn(self._execute_routine(routine, now, scheduled=True))

    def _check_states_changed_while_stopped(self) -> None:
        """A device that changed into a chosen state while Home Assistant was stopped runs its routine once."""
        changed = False
        for routine in self.routines:
            if routine["trigger"] not in STATE_TRIGGERS or not routine["enabled"]:
                continue
            state = self.hass.states.get(routine["watch_entity"])
            if state is None or (
                state.state in UNKNOWN_STATES
                and state.state not in routine["watch_states"]
            ):
                continue
            rt = self._rt(routine["id"])
            seen = rt.get("seen_state")
            if seen != state.state:
                rt["seen_state"] = state.state
                changed = True
            if (
                seen is not None
                and seen != state.state
                and state.state in routine["watch_states"]
            ):
                self._start_state_run(routine)
        if changed:
            self._save_soon()

    # ----- starting everything -----

    def _watch_done(self, routine: dict[str, Any]) -> None:
        """Run when an item is checked off the chosen to-do list (the watched item, or any item)."""
        rid = routine["id"]
        self._drop_handle(rid, H_DONE)
        if routine["trigger"] not in TODO_DONE_TRIGGERS or not routine["enabled"]:
            return
        if self._rt(rid).get("seen_done") is None:
            self._spawn(self._check_done(routine))

        @callback
        def _changed(event: Event[EventStateChangedData]) -> None:
            if event.data.get("new_state") is not None:
                self._spawn(self._check_done(routine))

        unsub = async_track_state_change_event(
            self.hass, [routine["watch_entity"]], _changed
        )
        self._set_handle(rid, H_DONE, unsub)

    async def _check_done(self, routine: dict[str, Any]) -> None:
        """Compare the completed items with the last look; run once for each newly completed one."""
        async with self._done_lock:
            rt = self._rt(routine["id"])
            items = await list_items(self.hass, routine["watch_entity"], ["completed"])
            if items is None:
                return
            done = list(
                dict.fromkeys(
                    key for key in (item_key(i.get("summary")) for i in items) if key
                )
            )
            previous = rt.get("seen_done")
            seen = done[-SEEN_DONE_LIMIT:]
            if seen != previous:
                rt["seen_done"] = seen
                self._store.async_delay_save(self._payload, SEEN_SAVE_DELAY_SECONDS)
            if previous is None:
                return  # first look: what is already done does not run the routine
            wanted = item_key(routine["watch_item"])
            new = [key for key in done if key not in previous]
            if any(not wanted or key == wanted for key in new):
                self._run_state_routine(routine)

    @callback
    def _check_health(self, _now: datetime) -> None:
        """Regular check of every routine. The watchers below catch most changes sooner."""
        for routine in self.routines:
            self._check_routine_health(routine)

    def _in_startup_quiet(self) -> bool:
        """True while Home Assistant is still starting up and devices may not have loaded yet."""
        if self._started_at is None:
            return False
        return dt_util.utcnow() < self._started_at + timedelta(
            minutes=self.settings["startup_quiet_minutes"]
        )

    def _schedule_health_interval(self) -> None:
        """(Re)start the regular health check at the interval chosen in settings."""
        if self._health_unsub is not None:
            self._health_unsub()
        self._health_unsub = async_track_time_interval(
            self.hass,
            self._check_health,
            timedelta(minutes=self.settings["health_check_minutes"]),
        )

    def _check_routine_health(self, routine: dict[str, Any]) -> None:
        """Announce, once, a routine whose device or list has gone missing or unavailable."""
        if not routine["enabled"] or self._in_startup_quiet():
            return
        rt = self._rt(routine["id"])
        problems = self.health(routine)
        if not problems:
            rt["health_alerted"] = False
            rt["unavailable_since"] = None
            # Dismissed only when a card may be showing, not for every healthy
            # routine at every check (a service call each) (1.34.3).
            if rt.get("health_card", True):
                rt["health_card"] = False
                self._spawn(self._dismiss_health_notification(routine["id"]))
            self._release_held(routine)
            return
        if self._expire_held(routine):
            self._notify_listeners()
        if routine["wait_when_unavailable"] and not self._missing_entities(routine):
            # Only unavailable, not gone: wait quietly for a day before asking for attention.
            now = dt_util.utcnow()
            since = rt["unavailable_since"] or now
            rt["unavailable_since"] = since
            if now - since < timedelta(hours=WAIT_UNAVAILABLE_HOURS):
                return
        if not rt.get("health_alerted"):
            rt["health_alerted"] = True
            if routine["notify_on_health"]:
                rt["health_card"] = True
                self._send_notification(
                    f"Routine '{routine['name']}' needs attention: {' '.join(problems)}",
                    self._health_notification_id(routine["id"]),
                )

    def _watch_health(self, routine: dict[str, Any]) -> None:
        """When a device or list the routine needs changes, check again after a short grace period."""
        rid = routine["id"]
        self._drop_handle(rid, H_HEALTH)
        self._drop_handle(rid, H_HEALTH_TIMER)
        entities = self._health_entities(routine)
        watched = routine["notify_on_health"] or routine["wait_when_unavailable"]
        if not routine["enabled"] or not watched or not entities:
            return

        @callback
        def _changed(event: Event[EventStateChangedData]) -> None:
            # Health only looks at whether a device is there and available, so
            # a light going on or off (most changes) needs no new check
            # (1.33.0). Only going missing, unavailable or unknown, or coming
            # back, does.
            if _health_bad(event.data.get("old_state")) == _health_bad(
                event.data.get("new_state")
            ):
                return
            current = self._find(rid)
            if (
                current is not None
                and self._rt(rid)["held_run"] is not None
                and not self.health(current)
            ):
                self._release_held(current)
            self._schedule_health_check(rid)

        unsub = async_track_state_change_event(self.hass, entities, _changed)
        self._set_handle(rid, H_HEALTH, unsub)

    def _schedule_health_check(self, routine_id: str) -> None:
        self._drop_handle(routine_id, H_HEALTH_TIMER)

        @callback
        def _due(_now: datetime) -> None:
            self._handles.pop((routine_id, H_HEALTH_TIMER), None)
            current = self._find(routine_id)
            if current is not None:
                self._check_routine_health(current)

        unsub = async_call_later(self.hass, self.settings["health_alert_seconds"], _due)
        self._set_handle(routine_id, H_HEALTH_TIMER, unsub)

    def _check_done_after_startup(self) -> None:
        """Items checked off while Home Assistant was stopped run their routine once, after startup."""
        for routine in self.routines:
            if routine["trigger"] in TODO_DONE_TRIGGERS and routine["enabled"]:
                self._spawn(self._check_done(routine))

    def _build_aliases(self, only: set[str] | None = None) -> None:
        """Each routine's other triggers as routines of their own.

        ``only``: build just these routines' again and keep the others' as
        they are (the same dicts, which their watchers hold), in the same
        order as building them all.
        """
        old = self._aliases
        self._aliases = {}
        for routine in self.routines:
            if only is not None and routine["id"] not in only:
                for alias_id, alias in old.items():
                    if alias["alias_of"] == routine["id"]:
                        self._aliases[alias_id] = alias
                continue
            for number, more in enumerate(routine.get("more_triggers") or [], start=2):
                alias_id = f"{routine['id']}~{number}"
                self._aliases[alias_id] = {
                    **routine,
                    **more,
                    "id": alias_id,
                    "alias_of": routine["id"],
                    "more_triggers": [],
                    "off_threshold": None,
                    "heads_up_minutes": 0,
                    "catch_up_minutes": 0,
                }

    def _aliases_of(self, routine_id: str) -> list[dict[str, Any]]:
        return [a for a in self._aliases.values() if a["alias_of"] == routine_id]

    def alias_routines(self, routine_id: str) -> list[dict[str, Any]]:
        """A routine's other triggers, each as a routine of its own."""
        return self._aliases_of(routine_id)

    def next_run_after(
        self, routine: dict[str, Any], after: datetime
    ) -> datetime | None:
        """When a routine (or one of its triggers) would run after a time, from
        its schedule alone (the dry run of tomorrow)."""
        return self._next_run_datetime(routine, after)

    def planned_run(self, trigger_id: str) -> datetime | None:
        """When a routine (or one of its other triggers) is next planned to run."""
        return dt_util.parse_datetime(self._rt(trigger_id)["next_run"] or "")

    def _source(self, routine: dict[str, Any]) -> dict[str, Any]:
        """The trigger a run came from: the routine, or one of its other triggers."""
        started_by = self._rt(routine["id"]).get("started_by")
        return self._aliases.get(started_by or "", routine)

    def _start_all(self, *, catch_up: bool) -> None:
        now = dt_util.now()
        self._build_aliases()
        ids = {r["id"] for r in self.routines} | set(self._aliases)
        for rid in list(self._runtime):
            if rid not in ids:
                del self._runtime[rid]
        self._start_routines(
            list(self._aliases.values()), self.routines, now, catch_up=catch_up
        )

    def _start_some(self, routine_ids: set[str]) -> None:
        """Restart only these routines (saved or deleted) and their other triggers.

        Their handles are dropped first (every kind: schedules, watchers, a
        planned retry, a heads-up, as a full reload would); every other
        routine's are left running.
        """
        now = dt_util.now()
        old_aliases = {
            alias_id
            for alias_id, alias in self._aliases.items()
            if alias["alias_of"] in routine_ids
        }
        mine = routine_ids | old_aliases
        for key in [key for key in self._handles if key[0] in mine]:
            self._drop_handle(*key)
        self._build_aliases(only=routine_ids)
        ids = {r["id"] for r in self.routines} | set(self._aliases)
        for rid in mine - ids:
            self._runtime.pop(rid, None)
        self._start_routines(
            [a for a in self._aliases.values() if a["alias_of"] in routine_ids],
            [r for r in self.routines if r["id"] in routine_ids],
            now,
            catch_up=False,
        )

    def _start_routines(
        self,
        aliases: list[dict[str, Any]],
        routines: list[dict[str, Any]],
        now: datetime,
        *,
        catch_up: bool,
    ) -> None:
        """Plan and watch these routines and other triggers (their handles are gone)."""
        for alias in aliases:
            self._rt(alias["id"])
            self._watch_planned(alias)
            self._schedule_one(alias, now)
            self._watch_presence(alias)
            self._watch_value(alias)
            self._watch_state(alias)
            self._watch_press(alias)
        for routine in routines:
            self._rt(routine["id"])
            self._watch_planned(routine)
            self._schedule_one(routine, now)
            self._watch_presence(routine)
            self._watch_value(routine)
            self._watch_state(routine)
            self._watch_press(routine)
            self._watch_done(routine)
            self._watch_health(routine)
            self._watch_conditions(routine)
            self._resume_waiting(routine, now)
            if catch_up:
                self._maybe_catch_up(routine, now)

    # ----- planning and execution -----

    def _area_entities(self, area_id: str) -> list[str]:
        return area_entity_ids(self.hass, area_id)

    def _resolve_targets(self, target: dict[str, Any] | None, domain: str) -> list[str]:
        """Entities a target reaches, limited to the service's domain."""
        target = target or {}
        ids: set[str] = set(target.get("entity_id", []))
        for area_id in target.get("area_id", []):
            ids.update(self._area_entities(area_id))
        return sorted(e for e in ids if e.split(".", 1)[0] == domain)

    def _plan_action(
        self, routine: dict[str, Any], action: dict[str, Any]
    ) -> tuple[str, dict[str, Any] | None, list[str]]:
        """Return (outcome, target override or None, entities the step reaches)."""
        service = action["service"]
        domain = service.split(".", 1)[0]
        target = action.get("target") or {}
        entities = self._resolve_targets(target, domain)
        if not (routine["skip_noop"] and service in DESIRED_STATE and entities):
            return "would_run", None, entities
        wanted = DESIRED_STATE[service]
        remaining: list[str] = []
        for group in linked_groups(
            self.hass, entities, self._integration_order(routine)
        ):
            # A device in several integrations: believe the first way that's
            # reporting (the others can lag behind or be stale). A pair is
            # already done only when the pair entity, or every member, shows
            # the wanted state. Members already there are left out so a
            # half-done pair does not fire the pair entity again.
            for entity_id in self._linked_step_remaining(group, entities, wanted):
                if entity_id not in remaining:
                    remaining.append(entity_id)
        remaining.sort(key=entities.index)
        if not remaining:
            return "already_in_state", None, entities
        if len(remaining) != len(entities):
            return "partly_in_state", {"entity_id": remaining}, remaining
        return "would_run", None, entities

    async def _call_service(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
        target: dict[str, Any] | None,
    ) -> BaseException | None:
        """One call, within the step time limit (except scripts and the like, which
        can rightly take longer). Returns the error, or None."""
        limit = self.settings["step_timeout_seconds"]
        try:
            async with asyncio.timeout(
                limit if limit and domain not in UNLIMITED_DOMAINS else None
            ):
                await self.hass.services.async_call(
                    domain, service, data, blocking=True, target=target or None
                )
        except TimeoutError:
            return TimeoutError(f"no answer within {limit} seconds")
        except Exception as err:  # noqa: BLE001
            return err
        return None

    async def _call_with_retries(
        self,
        routine: dict[str, Any],
        domain: str,
        service: str,
        data: dict[str, Any],
        target: dict[str, Any] | None,
    ) -> BaseException | None:
        attempts = 1 + routine["retries"]
        for attempt in range(1, attempts + 1):
            error = await self._call_service(domain, service, data, target)
            if error is None:
                return None
            if attempt == attempts:
                return error
            _LOGGER.debug(
                "Routine '%s': %s.%s failed (attempt %d of %d), retrying: %s",
                routine["name"],
                domain,
                service,
                attempt,
                attempts,
                _short_error(error),
            )
            await asyncio.sleep(RETRY_DELAY_SECONDS)
        return None

    async def _execute_routine(
        self, routine: dict[str, Any], now: datetime, *, scheduled: bool
    ) -> bool:
        if main_id := routine.get("alias_of"):
            # Another of the routine's triggers: run the routine itself.
            main = self._find(main_id)
            if main is None:
                return False
            self._rt(main_id)["started_by"] = routine["id"]
            return await self._execute_routine(main, now, scheduled=scheduled)
        rid = routine["id"]
        rt = self._rt(rid)
        today = now.date().isoformat()

        if not routine["enabled"]:
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "disabled")
            return False
        if scheduled and self.paused:
            self._record(rid, "skipped", "paused")
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "paused")
            return False
        if scheduled and not routine["repeat"] and rt["last_run_date"] == today:
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "already_ran_today")
            return False
        if scheduled and self._hold_if_unavailable(routine, now):
            return False
        if scheduled and (until := self.paused_until(routine)) and today <= until:
            self._record(rid, "skipped", "paused_until")
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "paused_until")
            return False
        if scheduled and not routine.get("ignore_holds") and (hold := self.hold(now)):
            # A calendar hold is on (Babysitter, Vacation…).
            self._record(rid, "skipped", f"calendar_hold:{hold}")
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "calendar_hold")
            self._notify_listeners()
            return False
        if scheduled and today in routine.get("skip_dates", []):
            # A day it's set to skip (a holiday, a day off).
            self._record(rid, "skipped", "skip_date")
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "skip_date")
            self._notify_listeners()
            return False
        if scheduled and self.mode in routine.get("skip_modes", []):
            # Not in this house mode (a bedtime lockup while there are guests).
            self._record(rid, "skipped", "skip_mode")
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "skip_mode")
            self._notify_listeners()
            return False
        if scheduled and self._cooling_down(routine, now):
            # Ran a moment ago (a door opened twice, a sensor around its level).
            self._record(rid, "skipped", "cooldown")
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "cooldown")
            self._notify_listeners()
            return False

        ok, reason = self._check(self._source(routine), now)
        if not ok:
            if scheduled and rt["pending_date"] is not None:
                self._notify_listeners()  # already waiting
                return False
            if scheduled and self._arm_pending(routine, now):
                self._fire_event(EVENT_ROUTINE_SKIPPED, routine, f"waiting:{reason}")
                self._notify_listeners()
                return False
            rt["last_result"] = reason
            rt["state"] = "skipped"
            self._record(rid, "skipped", reason or "")
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, reason)
            if scheduled and routine["notify_on_skip"]:
                self._send_notification(
                    f"Routine '{routine['name']}' skipped – conditions not met."
                )
            self._notify_listeners()
            return False

        ran_before = rt["last_run_date"]
        if scheduled:
            rt["last_run_date"] = today
            self._clear_pending(routine)
            if routine.get("skip_next"):
                # Skipped once, as asked: it runs as usual next time.
                routine["skip_next"] = False
                rt["state"] = "idle"
                rt["last_result"] = "skipped_once"
                self._record(rid, "skipped", "skipped_once")
                self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "skipped_once")
                self.revision += 1
                # Written now: a restart mustn't skip it a second time.
                await self._async_save()
                self._notify_listeners()
                return False

        if (
            scheduled
            and routine.get("ask_first")
            and not await self._ask_first(routine)
        ):
            return False

        rt["state"] = "running"
        rt["last_run"] = now.isoformat()
        self._keep_for_undo(routine, now)
        self._note_power(routine)
        self._notify_listeners()
        self._fire_event(EVENT_ROUTINE_STARTED, routine)

        run = await self._run_steps(
            routine,
            [(i, action, None) for i, action in enumerate(routine["actions"])],
            now,
            scheduled=scheduled,
        )
        if run is None:
            return False  # held for an unavailable list
        result = run.result

        if (
            scheduled
            and result.startswith("conditions_failed_mid_run")
            and self._wait_ends(routine, dt_util.now()) is not None
        ):
            # Stopped part way because a condition stopped passing: wait for it
            # again and run the rest then (steps already done are skipped).
            rt["last_run_date"] = ran_before
            rt["last_result"] = result
            why = result.split(":", 1)[1].replace("_", " ") or "a condition"
            self._record(
                rid,
                "failed",
                f"Its conditions stopped holding partway ({why}) – waiting to finish",
            )
            self._arm_pending(routine, dt_util.now())
            self._fire_event(EVENT_ROUTINE_FINISHED, routine, result)
            self._save_soon()
            self._notify_listeners()
            return False

        await self._finish_run(routine, run, tries=0)
        return result == "success"

    async def _finish_run(
        self, routine: dict[str, Any], run: _Run, *, tries: int
    ) -> None:
        """Record a run (or a later try), notify, and plan another try when one is due."""
        rid = routine["id"]
        rt = self._rt(rid)
        rt["started_by"] = None
        self._drop_handle(rid, "retry_later")
        self._retry.pop(rid, None)
        result = run.result
        rt["last_result"] = result
        summary = "; ".join(run.details)
        problems = "; ".join(
            f"Step {f.index + 1}: {f.text}" if len(run.failures) > 1 else f.text
            for f in run.failures
            if f.text
        )
        # The card's "last:" line: why, in words, not the result code.
        rt["last_reason"] = (
            None if result == "success" else (problems or result_words(result))
        )
        later = (
            bool(run.failures)
            and routine["retry_later_minutes"] > 0
            and tries < RETRY_LATER_TIMES
            and not result.startswith("conditions_failed")
        )
        if run.failures:
            # Kept for Try again, and for the try that's planned.
            self._retry[rid] = _Retry(run.failures, run.stopped_at, tries)
        if result == "success":
            rt["state"] = "idle"
            if tries:
                self._record(rid, "ran", f"Tried again: done. {summary}".strip())
                _LOGGER.info("Routine '%s' finished on a later try.", routine["name"])
            else:
                self._record(rid, "ran", summary)
                _LOGGER.info("Routine '%s' completed.", routine["name"])
            self._fire_event(EVENT_ROUTINE_FINISHED, routine, "success")
            if tries and routine["notify_on_error"]:
                self._send_notification(
                    f"Routine '{routine['name']}' finished when it tried again."
                )
            elif routine["notify_on_run"]:
                note = f" ({summary})" if summary else ""
                self._send_notification(f"Routine '{routine['name']}' completed{note}.")
        else:
            rt["state"] = "error"
            # The code stays when no step said more: Activity groups by it and
            # shows it in words.
            detail = problems or result
            if later:
                detail += f" – trying again in {routine['retry_later_minutes']} min"
            self._record(rid, "failed", detail)
            self._fire_event(EVENT_ROUTINE_FINISHED, routine, result)
            if routine["notify_on_error"]:
                message = (
                    f"Routine '{routine['name']}' failed: "
                    f"{problems or result_words(result)}."
                )
                if later:
                    minutes = routine["retry_later_minutes"]
                    message += f" It tries again in {minutes} minutes."
                self._send_notification(
                    message, retry_routine=rid if run.failures else None
                )
        if later:
            self._plan_retry(routine, tries + 1)
        if (before := rt.pop("power_before", None)) is not None:
            self._spawn(self._read_power_after(rid, *before))
        self._save_soon()
        self._notify_listeners()

    def _plan_retry(self, routine: dict[str, Any], tries: int) -> None:
        rid = routine["id"]

        @callback
        def _due(_now: datetime) -> None:
            self._drop_handle(rid, "retry_later")
            self._spawn(self.async_try_again(rid, tries=tries))

        self._set_handle(
            rid,
            "retry_later",
            async_call_later(self.hass, routine["retry_later_minutes"] * 60, _due),
        )

    async def async_try_again(self, routine_id: str, *, tries: int = 0) -> bool:
        """Try what failed again: the devices a step couldn't change, then the steps
        after it that didn't run. With nothing kept (after a restart), run it again."""
        routine = self._find(routine_id)
        if routine is None:
            raise vol.Invalid(f"No routine with id '{routine_id}'.")
        retry = self._retry.get(routine_id)
        if retry is None:
            return await self.async_run(routine_id)
        if not routine["enabled"]:
            self._retry.pop(routine_id, None)
            return False
        now = dt_util.now()
        ok, reason = self._check(routine, now)
        if not ok:
            self._drop_handle(routine_id, "retry_later")
            self._retry.pop(routine_id, None)
            self._record(routine_id, "skipped", f"Didn't try again: {reason}")
            self._notify_listeners()
            return False
        steps: list[tuple[int, dict[str, Any], dict[str, Any] | None]] = [
            (f.index, routine["actions"][f.index], f.target)
            for f in retry.failures
            if f.index < len(routine["actions"])
        ]
        if retry.stopped_at is not None:
            steps += [
                (i, routine["actions"][i], None)
                for i in range(retry.stopped_at + 1, len(routine["actions"]))
            ]
        rt = self._rt(routine_id)
        rt["state"] = "running"
        self._notify_listeners()
        run = await self._run_steps(routine, steps, now, scheduled=False)
        if run is None:
            return False
        await self._finish_run(routine, run, tries=tries or (retry.tries + 1))
        return run.result == "success"

    async def _run_steps(
        self,
        routine: dict[str, Any],
        steps: list[tuple[int, dict[str, Any], dict[str, Any] | None]],
        now: datetime,
        *,
        scheduled: bool,
    ) -> _Run | None:
        """Run steps in order, showing which one is running on the routine's card.

        The steps run in their own task, kept in ``_step_tasks`` while they
        run, so unloading can stop them (``_async_stop_runs``). None: held for
        an unavailable list, or stopped because Daily Routine was unloaded;
        either way the caller records nothing more.
        """
        if self._unloaded:
            # Started after unloading began (an "Ask me first" answered late, a
            # timer that fired on the way out): this coordinator is done.
            return None
        rt = self._rt(routine["id"])
        rt["trace"] = []
        task = self.hass.loop.create_task(
            self._owned_steps(routine, steps, now, scheduled=scheduled),
            name=f"{DOMAIN} run {routine['id']}",
        )
        self._step_tasks.add(task)
        try:
            return await task
        except CancelledError:
            if (me := current_task()) is not None and me.cancelling():
                # The caller itself is being cancelled (Home Assistant stopping,
                # a service call given up): let that through.
                raise
            # Only the steps were stopped, by unloading: nothing more to record.
            return None
        finally:
            self._step_tasks.discard(task)
            rt["progress"] = None
            rt["trace"] = [_public(step) for step in rt.get("trace") or []]

    async def _owned_steps(
        self,
        routine: dict[str, Any],
        steps: list[tuple[int, dict[str, Any], dict[str, Any] | None]],
        now: datetime,
        *,
        scheduled: bool,
    ) -> _Run | None:
        """The steps of one run, as a task unloading can cancel.

        When unloading cancels it, the run is recorded as stopped part way
        (card, Activity, the finished event) here, inside the task, so it's
        written down before unloading saves.
        """
        try:
            return await self._run_step_list(routine, steps, now, scheduled=scheduled)
        except CancelledError:
            if self._unloaded:
                self._note_stopped_by_unload(routine)
            raise

    def _note_stopped_by_unload(self, routine: dict[str, Any]) -> None:
        """Record a run that unloading stopped part way.

        It shows as failed with ``STOPPED_BY_UNLOAD``, so the card doesn't
        stay on Running and Try again (or the next scheduled time) runs it in
        full. No notification is sent: the person who reloaded knows.
        """
        rid = routine["id"]
        rt = self._rt(rid)
        rt["state"] = "error"
        rt["started_by"] = None
        rt["last_result"] = "stopped"
        rt["last_reason"] = STOPPED_BY_UNLOAD
        rt.pop("power_before", None)
        self._record(rid, "failed", STOPPED_BY_UNLOAD)
        self._fire_event(EVENT_ROUTINE_FINISHED, routine, "stopped")
        _LOGGER.info(
            "Routine '%s' stopped part way: Daily Routine was unloaded.",
            routine["name"],
        )

    async def _run_step_list(
        self,
        routine: dict[str, Any],
        steps: list[tuple[int, dict[str, Any], dict[str, Any] | None]],
        now: datetime,
        *,
        scheduled: bool,
        track: bool = True,
    ) -> _Run | None:
        """Run steps in order. None: held for an unavailable list (nothing done yet).

        track=False runs steps of a "together" step: the card shows that step.
        """
        run = _Run()
        acted = False
        rt = self._rt(routine["id"])
        started = dt_util.now().isoformat()
        for position, (index, action, only) in enumerate(steps):
            if track:
                self._trace_open(rt, run, index, action)
                rt["progress"] = {
                    "step": position + 1,
                    "total": len(steps),
                    "label": step_label(self.hass, action, self._routine_names()),
                    "started": started,
                    "step_started": dt_util.now().isoformat(),
                    "until": (
                        (dt_util.now() + timedelta(seconds=action["delay"])).isoformat()
                        if "delay" in action
                        else None
                    ),
                }
                self._notify_listeners()
            ok, reason = self._check(self._source(routine), dt_util.now())
            if not ok:
                _LOGGER.warning(
                    "Routine '%s' stopped: conditions no longer pass (%s).",
                    routine["name"],
                    reason,
                )
                run.result = f"conditions_failed_mid_run:{reason}"
                break

            if (cond := action.get("only_if")) and not self._condition_ok(
                cond, dt_util.now()
            ):
                run.details.append(f"Step {index + 1} {ONLY_IF_SKIPPED}")
                if track:
                    _trace_mark(rt, "skipped", "Its Only if didn't hold")
                continue

            if "delay" in action:
                await asyncio.sleep(action["delay"])
                continue

            if (
                not routine.get("loud_ok")
                and is_loud(action)
                and in_quiet(self.settings.get("quiet_hours"), dt_util.now())
            ):
                run.details.append(f"Step {index + 1} skipped: quiet hours")
                if track:
                    _trace_mark(rt, "skipped", "Quiet hours")
                continue

            if "check_battery" in action:
                acted = True
                low = self._low_batteries(action["check_battery"]["below"])
                if not low:
                    run.details.append("Every battery was fine")
                    continue
                text = battery_text(low)
                run.details.append(text)
                await self._notify(text, f"{DOMAIN}_battery_{routine['id']}")
                continue

            if "tick_list" in action:
                ticked_all = await self._wait_ticks(routine, action["tick_list"])
                if ticked_all:
                    run.details.append("Everything was ticked off")
                    continue
                run.details.append("Not everything was ticked off in time")
                if action["tick_list"]["on_timeout"] == "stop":
                    run.result = "stopped:tick_list"
                    run.stopped_at = index
                    if track:
                        _trace_mark(rt, "stopped", "Not everything was ticked off")
                    break
                continue

            if "service" in action and self._who.get(routine["id"]):
                action = _with_who(action, self._who[routine["id"]])

            if "check_open" in action:
                acted = True
                found = self._left_open(action["check_open"]["entity_id"])
                if not found:
                    run.details.append("Everything was shut")
                    continue
                text = checklist_text(found)
                run.details.append(text)
                await self._notify(text, f"{DOMAIN}_checklist_{routine['id']}")
                if action["check_open"]["stop"]:
                    run.failures.append(_Failure(index, "check_open", None, text))
                    run.result = "error:check_open"
                    run.stopped_at = index
                    break
                continue

            if "together" in action:
                acted = True
                results = await asyncio.gather(
                    *(
                        self._run_step_list(
                            routine,
                            [(index, sub, None)],
                            now,
                            scheduled=False,
                            track=False,
                        )
                        for sub in action["together"]
                    )
                )
                done = [r for r in results if r is not None]
                for sub_run in done:
                    run.details.extend(sub_run.details)
                    run.failures.extend(sub_run.failures)
                bad = [r.result for r in done if r.result != "success"]
                if bad and routine["on_step_error"] != "continue":
                    run.result = bad[0]
                    run.stopped_at = index
                    break
                continue

            if "wait_until" in action:
                wait = action["wait_until"]
                seconds = wait["minutes"] * SECONDS_PER_MINUTE
                rt["progress"]["until"] = (
                    dt_util.now() + timedelta(seconds=seconds)
                ).isoformat()
                self._notify_listeners()
                if await self._wait_until_state(
                    wait["entity_id"],
                    wait["states"],
                    seconds,
                    below=wait.get("below"),
                    above=wait.get("above"),
                    hold=wait.get("for_minutes", 0) * SECONDS_PER_MINUTE,
                ):
                    continue
                text = (
                    f"{self._entity_name(wait['entity_id'])} didn't change "
                    f"within {wait['minutes']} min"
                )
                if wait["on_timeout"] == "continue":
                    run.details.append(text)
                    continue
                run.failures.append(_Failure(index, "wait_until", None, text))
                run.result = "error:wait_until"
                run.stopped_at = index
                break

            if "run_routine" in action:
                acted = True
                text, failed = await self._run_other(routine, action["run_routine"])
                if not failed:
                    if text:
                        run.details.append(text)
                    continue
                run.failures.append(_Failure(index, "run_routine", None, text))
                if routine["on_step_error"] == "continue":
                    continue
                run.result = "error:run_routine"
                run.stopped_at = index
                break

            if "set_mode" in action:
                acted = True
                await self.async_set_mode(action["set_mode"])
                continue

            if "briefing" in action:
                acted = True
                error = await self._speak_briefing(action["briefing"])
                if error is None:
                    continue
                run.failures.append(_Failure(index, "briefing", None, error))
                if routine["on_step_error"] == "continue":
                    continue
                run.result = "error:briefing"
                run.stopped_at = index
                break

            if "wait_cheaper" in action:
                wait = action["wait_cheaper"]
                seconds, note = self._until_cheapest(wait)
                if note:
                    run.details.append(note)
                if seconds > 0:
                    if track and rt.get("progress"):
                        rt["progress"]["until"] = (
                            dt_util.now() + timedelta(seconds=seconds)
                        ).isoformat()
                        self._notify_listeners()
                    await asyncio.sleep(seconds * SECONDS_PER_MINUTE / 60)
                continue

            if "volume_ramp" in action:
                acted = True
                ramp = action["volume_ramp"]
                seconds = ramp["minutes"] * SECONDS_PER_MINUTE
                if track and rt.get("progress"):
                    rt["progress"]["until"] = (
                        dt_util.now() + timedelta(seconds=seconds)
                    ).isoformat()
                    self._notify_listeners()
                error = await self._ramp_volume(ramp, seconds)
                if error is None:
                    continue
                run.failures.append(_Failure(index, "volume_ramp", None, error))
                if routine["on_step_error"] == "continue":
                    continue
                run.result = "error:volume_ramp"
                run.stopped_at = index
                break

            if "rooms" in action and only is None:
                action = self._only_rooms(action)
                if not action.get("target"):
                    word = "someone is in" if action["rooms"] == "occupied" else "empty"
                    run.details.append(f"Step {index + 1}: no room was {word}")
                    if track:
                        _trace_mark(rt, "skipped", f"No room was {word}")
                    continue

            if "todo" in action:
                step = action["todo"]
                outcome = await apply_todo_step(
                    self.hass,
                    step,
                    dry_run=False,
                    wait_seconds=self.settings["list_wait_seconds"],
                )
                if (
                    outcome == "list_unavailable"
                    and scheduled
                    and not acted
                    and routine["wait_when_unavailable"]
                    and self._hold_if_unavailable(routine, now, force=True)
                ):
                    return None
                acted = True
                if outcome in FAILED_OUTCOMES:
                    _LOGGER.warning(
                        "Routine '%s': to-do %s on %s failed (%s).",
                        routine["name"],
                        step["op"],
                        step["list"],
                        outcome,
                    )
                    run.result = f"error:todo_{outcome}"
                    break
                run.details.append(f"{step['item']}: {TODO_OUTCOME_TEXT[outcome]}")
                continue

            override: dict[str, Any] | None
            if only is not None:
                outcome, override = "would_run", only
            else:
                outcome, override, _entities = self._plan_action(routine, action)
            if outcome == "already_in_state":
                _LOGGER.debug(
                    "Routine '%s': %s already done, skipped",
                    routine["name"],
                    action["service"],
                )
                if track:
                    _trace_mark(rt, "skipped", "Already done")
                continue

            domain, service = action["service"].split(".", 1)
            if not self.hass.services.has_service(domain, service):
                _LOGGER.error(
                    "Routine '%s': service %s does not exist.",
                    routine["name"],
                    action["service"],
                )
                run.result = f"missing_service:{action['service']}"
                break
            acted = True
            failure = await self._run_service_step(routine, index, action, override)
            if failure is None:
                continue
            run.failures.append(failure)
            if routine["on_step_error"] == "continue":
                continue
            run.result = f"error:{action['service']}"
            run.stopped_at = index
            break

        if run.failures and run.result == "success":
            run.result = f"error:{run.failures[0].service}"
        if track:
            self._trace_close(rt, run)
        return run

    def _trace_open(
        self, rt: dict[str, Any], run: _Run, index: int, action: dict[str, Any]
    ) -> None:
        """Start a step's line in the run's replay (closing the one before)."""
        self._trace_close(rt, run)
        rt.setdefault("trace", []).append(
            {
                "step": index + 1,
                "label": step_label(self.hass, action, self._routine_names()),
                "at": dt_util.now().isoformat(),
                "_started": dt_util.now(),
                "_failures": len(run.failures),
                "_action": action,
            }
        )

    def _trace_close(self, rt: dict[str, Any], run: _Run) -> None:
        """Finish the open step: how long it took, how it went, and how the
        devices it reached ended up."""
        trace = rt.get("trace")
        if not trace or "_started" not in trace[-1]:
            return
        step = trace[-1]
        action = step.pop("_action")
        failures = run.failures[step.pop("_failures") :]
        step["seconds"] = round(
            (dt_util.now() - step.pop("_started")).total_seconds(), 1
        )
        if failures:
            step["result"] = "failed"
            step["note"] = "; ".join(f.text for f in failures if f.text)[:300]
            lines = [line for f in failures for line in (f.details or [f.text]) if line]
            if lines:
                step["why"] = lines[:12]
        elif run.stopped_at == step["step"] - 1 or (
            run.result.startswith("conditions_failed") and "result" not in step
        ):
            step["result"] = "stopped"
            step["note"] = "Its conditions stopped holding"
        step.setdefault("result", "ok")
        devices: dict[str, str] = {}
        for each in action.get("together") or [action]:
            if not each.get("service"):
                continue
            domain = each["service"].split(".", 1)[0]
            targets = self._resolve_targets(each.get("target") or {}, domain)
            targets += list((each.get("data") or {}).get("entities") or {})
            for entity_id in targets[:TRACE_DEVICES]:
                state = self.hass.states.get(entity_id)
                devices[entity_id] = state.state if state else "unavailable"
        if devices:
            step["devices"] = [
                {"entity_id": e, "name": self._entity_name(e), "state": st}
                for e, st in list(devices.items())[:TRACE_DEVICES]
            ]

    async def _run_service_step(
        self,
        routine: dict[str, Any],
        index: int,
        action: dict[str, Any],
        override: dict[str, Any] | None,
    ) -> _Failure | None:
        """Call one step's action. With several devices, a failed call is tried device
        by device, so the ones that answer still change. Afterwards, devices that should
        now be on, off, open, closed or locked are checked."""
        domain, service = action["service"].split(".", 1)
        data = dict(action.get("data") or {})
        target = override if override is not None else action.get("target")
        entities = self._resolve_targets(target or {}, domain)
        if not entities:
            error = await self._call_with_retries(
                routine, domain, service, data, target
            )
            if error is None:
                return None
            self._log_step_error(routine, action, error)
            text = self._no_device_failure(action, target, error)
            return _Failure(index, action["service"], None, text, [text])

        # A device in more than one integration is reached one integration
        # at a time, falling back only when that level failed or didn't change.
        # The routine's linked device (usually the Matter one) goes first when
        # the step reaches it or its linked siblings (_lead_plan).
        groups, led_at, area_levels = self._lead_plan(routine, domain, entities)
        linked = [g for g in groups if g.has_fallback]
        retry_target: dict[str, list[str]] = {}
        tried_before: dict[str, list[tuple[str, str]]] = {}
        led_keys: set[str] = set()
        if not linked and not area_levels:
            failed = await self._run_on_devices(routine, action, data, target, entities)
        else:
            in_area_levels = {e for level in area_levels for e in level}
            singles: list[str] = []
            for group in groups:
                if not group.has_fallback:
                    # The linked device's room siblings wait for it, not run
                    # at the same time as it.
                    singles.extend(
                        e for e in group.entity_ids if e not in in_area_levels
                    )
            failed = (
                await self._run_on_devices(
                    routine, action, data, {"entity_id": singles}, singles
                )
                if singles
                else {}
            )
            trails: list[list[tuple[str, str]]] = [[] for _ in linked]
            area_trail: list[tuple[str, str]] = []
            outcomes = await asyncio.gather(
                *(
                    self._run_linked(routine, action, data, g, trail)
                    for g, trail in zip(linked, trails, strict=True)
                ),
                self._run_levels(routine, action, data, area_levels, area_trail),
            )
            led_group = groups[led_at] if led_at is not None else None
            runs: list[
                tuple[list[str], dict[str, str] | None, list[tuple[str, str]], bool]
            ] = [
                (g.entity_ids, out, trail, g is led_group)
                for g, out, trail in zip(linked, outcomes[:-1], trails, strict=True)
            ]
            runs.append(
                (
                    [e for level in area_levels for e in level],
                    outcomes[-1],
                    area_trail,
                    True,
                )
            )
            for ids, last_failed, trail, led in runs:
                if last_failed:
                    failed.update(last_failed)
                    key = next(iter(last_failed))
                    # Retry later starts again at the first integration.
                    retry_target[key] = ids
                    if trail:
                        tried_before[key] = trail
                    if led:
                        led_keys.add(key)

        if not failed:
            return None
        wanted = DESIRED_STATE.get(action["service"])
        verify = self._verify_seconds(data)
        lines: list[str] = []
        details: list[str] = []
        for entity_id, why in failed.items():
            line = self._device_failure(entity_id, why, wanted, verify)
            _LOGGER.warning(
                "Routine '%s': %s (%s).", routine["name"], line, action["service"]
            )
            lines.append(line)
            details.append(line)
        # A linked device: say which ways were tried before the last one.
        summary_lines = list(lines)
        for key, trail in tried_before.items():
            if key not in failed:
                continue
            at = list(failed).index(key)
            ways = list(dict.fromkeys(way for way, _why in trail if way))
            if ways:
                first = "the linked device, " if key in led_keys else ""
                summary_lines[at] += f" (tried {first}{_join(ways)} first)"
            words = [f"Tried first: {why}" for _way, why in trail]
            if key in led_keys and words:
                words[0] = words[0].replace(
                    "Tried first:", "Tried first (linked device):", 1
                )
            details[at:at] = words
        again = [e for key in failed for e in retry_target.get(key, [key])]
        return _Failure(
            index,
            action["service"],
            {"entity_id": again},
            _summary(summary_lines),
            details,
        )

    def _lead_plan(
        self, routine: dict[str, Any], domain: str, entities: list[str]
    ) -> tuple[list[LinkedGroup], int | None, list[tuple[str, ...]]]:
        """The step's devices as groups, with the routine's linked device first.

        With a "Linked device" on the routine (usually the Matter one):

        - If it is in one of the step's linked groups (Device Linker), its
          level moves to the front of that group, ahead of the integration
          order; the other levels follow in that order. Returns that group's
          index.
        - If no group has it but the step reaches it anyway (a step for the
          room it is in), the step's other devices of the same kind in that
          room become its fallbacks, one integration at a time in the
          integration order (``area_levels``, the linked device first).
        - Otherwise (the linked device isn't part of the step) nothing changes.
        """
        order = self._integration_order(routine)
        groups = linked_groups(self.hass, entities, order)
        lead = lead_entities(self.hass, routine.get("device_id"), domain)
        if not lead:
            return groups, None, []
        groups, led_at = lead_first(groups, lead)
        if led_at is not None:
            return groups, led_at, []
        area_id = self._lead_area(routine.get("device_id"))
        if area_id is None:
            return groups, None, []
        fallbacks = area_fallbacks(
            self.hass, lead, entities, self._area_entities(area_id), order
        )
        own = tuple(e for e in lead if e in entities)
        if not own or not fallbacks:
            return groups, None, []
        return groups, None, [own, *fallbacks]

    def _lead_area(self, device_id: str | None) -> str | None:
        device = dr.async_get(self.hass).async_get(device_id) if device_id else None
        return device.area_id if device is not None else None

    async def _run_levels(
        self,
        routine: dict[str, Any],
        action: dict[str, Any],
        data: dict[str, Any],
        levels: list[tuple[str, ...]],
        trail: list[tuple[str, str]],
    ) -> dict[str, str] | None:
        """The linked device, then its room fallbacks, one way at a time.

        Each way is sent on its own and checked (the call, then the wanted
        state within the check time). The next way is only used when this one
        failed, so the linked device and its fallbacks are never sent the
        command together. Returns why the last way didn't do it, or None.
        """
        if not levels:
            return None
        domain, service = action["service"].split(".", 1)
        wanted = DESIRED_STATE.get(action["service"])
        verify = self._verify_seconds(data)
        ent_reg = er.async_get(self.hass)
        last_failed: dict[str, str] = {}
        level_before: IntegrationLevel | None = None
        for ids in levels:
            entry = ent_reg.async_get(ids[0])
            level = IntegrationLevel(
                domain=entry.platform if entry else domain, entity_ids=ids
            )
            call_failed: dict[str, str] = {}
            error = await self._call_service(
                domain, service, data, {"entity_id": list(ids)}
            )
            if error is not None:
                if len(ids) == 1:
                    call_failed[ids[0]] = _short_error(error)
                else:
                    for entity_id in ids:
                        one = await self._call_with_retries(
                            routine, domain, service, data, {"entity_id": [entity_id]}
                        )
                        if one is not None:
                            call_failed[entity_id] = _short_error(one)
            if wanted and verify:
                check = [e for e in ids if e not in call_failed]
                for entity_id in await self._wait_for_state(check, wanted, verify):
                    call_failed.setdefault(entity_id, "didn't change")
            if not call_failed:
                return None
            if last_failed:
                trail.append(
                    self._level_failure(level_before, last_failed, wanted, verify)
                )
            last_failed = call_failed
            level_before = level
            _LOGGER.debug(
                "Routine '%s': %s through %s didn't do it, trying the next way.",
                routine["name"],
                action["service"],
                ", ".join(ids),
            )
        return last_failed or None

    async def _run_on_devices(
        self,
        routine: dict[str, Any],
        action: dict[str, Any],
        data: dict[str, Any],
        target: dict[str, Any] | None,
        entities: list[str],
    ) -> dict[str, str]:
        """Devices reached one way: the call, device by device if it fails, then the
        check. Returns why each device that didn't change didn't."""
        domain, service = action["service"].split(".", 1)
        failed: dict[str, str] = {}
        if len(entities) > 1:
            error = await self._call_service(domain, service, data, target)
            if error is not None:
                _LOGGER.debug(
                    "Routine '%s': %s failed for the group (%s), trying each device.",
                    routine["name"],
                    action["service"],
                    _short_error(error),
                )
                for entity_id in entities:
                    error = await self._call_with_retries(
                        routine, domain, service, data, {"entity_id": [entity_id]}
                    )
                    if error is not None:
                        failed[entity_id] = _short_error(error)
        else:
            error = await self._call_with_retries(
                routine, domain, service, data, target
            )
            if error is not None:
                failed[entities[0]] = _short_error(error)

        wanted = DESIRED_STATE.get(action["service"])
        verify = self._verify_seconds(data)
        if wanted and verify:
            check = [e for e in entities if e not in failed]
            stuck = await self._wait_for_state(check, wanted, verify)
            for entity_id in stuck:
                # Sent, but it didn't change: send it once more.
                await self._call_service(
                    domain, service, data, {"entity_id": [entity_id]}
                )
            for entity_id in await self._wait_for_state(stuck, wanted, verify):
                failed[entity_id] = "didn't change"
            for entity_id in stuck:
                if entity_id not in failed:
                    self.device_stats.record(
                        entity_id, "retried", "needed the command again"
                    )
        for entity_id in entities:
            if entity_id in failed:
                self.device_stats.record(entity_id, "failed", failed[entity_id])
            elif entity_id not in (stuck if wanted and verify else []):
                self.device_stats.record(entity_id, "ok")
        return failed

    async def _run_linked(
        self,
        routine: dict[str, Any],
        action: dict[str, Any],
        data: dict[str, Any],
        group: LinkedGroup,
        trail: list[tuple[str, str]] | None = None,
    ) -> dict[str, str] | None:
        """One device or pair, several integrations: try each level in order.

        A covering pair is one call. A multi-device level (left and right
        over Bluetooth) must all succeed. The next level is used only for
        members that are not yet in the wanted state, and only if this level
        failed or didn't change them. A covering pair is not sent when some
        members already changed (it would move the side that is already
        done). The returned map names the last level that was tried; each
        level that failed before it is told in ``trail`` (in words).
        """
        domain, service = action["service"].split(".", 1)
        wanted = DESIRED_STATE.get(action["service"])
        verify = self._verify_seconds(data)
        remaining: set[int] = set(range(len(group.members))) or {0}
        last_failed: dict[str, str] = {}
        level_before: IntegrationLevel | None = None

        skipped: list[tuple[str, str]] = []
        for level in group.levels:
            if level.covering and remaining != set(range(len(group.members))):
                skipped.append(
                    (
                        "",
                        f"{self._level_label(level)} wasn't sent: it moves both "
                        "sides and one side had already changed",
                    )
                )
                continue
            entities = group.level_entities_for(level, remaining)
            if not entities:
                continue

            call_failed: dict[str, str] = {}
            if len(entities) == 1:
                error = await self._call_service(
                    domain, service, data, {"entity_id": entities}
                )
                if error is not None:
                    call_failed[entities[0]] = _short_error(error)
                    _LOGGER.debug(
                        "Routine '%s': %s through %s failed (%s), trying the next way.",
                        routine["name"],
                        action["service"],
                        entities[0],
                        _short_error(error),
                    )
            else:
                error = await self._call_service(
                    domain, service, data, {"entity_id": entities}
                )
                if error is not None:
                    _LOGGER.debug(
                        "Routine '%s': %s failed for %s (%s), trying each device.",
                        routine["name"],
                        action["service"],
                        ", ".join(entities),
                        _short_error(error),
                    )
                    for entity_id in entities:
                        error = await self._call_with_retries(
                            routine, domain, service, data, {"entity_id": [entity_id]}
                        )
                        if error is not None:
                            call_failed[entity_id] = _short_error(error)

            if not (wanted and verify):
                if not call_failed:
                    return None
                if last_failed and trail is not None:
                    trail.append(
                        self._level_failure(level_before, last_failed, wanted, verify)
                    )
                last_failed = call_failed
                level_before = level
                remaining = {
                    index
                    for entity_id, index in (
                        (e, group.member_index(e)) for e in call_failed
                    )
                    if index is not None
                } or remaining
                continue

            reached = await self._linked_level_reached(
                group, level, entities, call_failed, wanted, verify
            )
            if reached is None and not level.covering:
                # This way finished its own members. A member it doesn't reach
                # (a left curtain that is only in the Cloud) still gets the next way.
                pending = self._members_not_in(group, wanted)
                if pending:
                    reached = {
                        group.members[index][0]: "didn't change"
                        for index in sorted(pending)
                    }
            if reached is None:
                return None
            if last_failed and trail is not None:
                trail.append(
                    self._level_failure(level_before, last_failed, wanted, verify)
                )
            last_failed = reached
            level_before = level
            remaining = set()
            for entity_id in group.entity_ids:
                index = group.member_index(entity_id)
                if index is None:
                    continue
                member = group.members[index]
                if not any(self._state_is(e, wanted) for e in member):
                    remaining.add(index)
            if not remaining:
                return None
            _LOGGER.debug(
                "Routine '%s': %s through %s didn't finish it, trying the next way.",
                routine["name"],
                action["service"],
                ", ".join(entities),
            )

        if last_failed and trail is not None:
            trail.extend(skipped)
        return last_failed or None

    async def _linked_level_reached(
        self,
        group: LinkedGroup,
        level: IntegrationLevel,
        entities: list[str],
        call_failed: dict[str, str],
        wanted: str,
        verify: float,
    ) -> dict[str, str] | None:
        """Wait for this level to show the change. None means the group is done.

        A covering pair counts as done when the pair entity changes, or when
        every member shows the change (some report it later). A single curtain
        counts as done when any of that curtain's ways shows it. A
        multi-device level needs every remaining member to change.
        """
        loop = asyncio.get_running_loop()
        start = loop.time()
        watch = list(level.entity_ids)
        for member in group.members:
            watch.extend(entity_id for entity_id in member if entity_id not in watch)
        while True:
            if self._linked_level_done(group, level, wanted):
                return None
            moving = any(self._state_is(e, s) for e in watch for s in MOVING_STATES)
            limit = MOVING_SECONDS if moving else verify
            if loop.time() - start >= max(verify, limit):
                break
            await asyncio.sleep(VERIFY_POLL_SECONDS)
        return self._linked_level_failure(group, level, entities, call_failed, wanted)

    def _members_not_in(self, group: LinkedGroup, wanted: str) -> set[int]:
        """The members none of whose ways shows the wanted state yet."""
        return {
            index
            for index, member in enumerate(group.members)
            if not any(self._state_is(entity_id, wanted) for entity_id in member)
        }

    def _linked_level_done(
        self, group: LinkedGroup, level: IntegrationLevel, wanted: str
    ) -> bool:
        """True when this level has produced the wanted state for its members."""
        if level.covering:
            if any(self._state_is(entity_id, wanted) for entity_id in level.entity_ids):
                return True
            return bool(group.members) and all(
                any(self._state_is(entity_id, wanted) for entity_id in member)
                for member in group.members
            )
        indexes: set[int] = set()
        for entity_id in level.entity_ids:
            index = group.member_index(entity_id)
            if index is not None:
                indexes.add(index)
        if not indexes:
            return all(
                self._state_is(entity_id, wanted) for entity_id in level.entity_ids
            )
        return all(
            any(self._state_is(entity_id, wanted) for entity_id in group.members[index])
            for index in indexes
        )

    def _linked_level_failure(
        self,
        group: LinkedGroup,
        level: IntegrationLevel,
        entities: list[str],
        call_failed: dict[str, str],
        wanted: str,
    ) -> dict[str, str]:
        """Why the last level didn't finish, named by that level's entities."""
        failed = dict(call_failed)
        if level.covering:
            if not any(
                self._state_is(entity_id, wanted) for entity_id in level.entity_ids
            ):
                for entity_id in level.entity_ids:
                    failed.setdefault(entity_id, "didn't change")
            return failed
        for entity_id in entities:
            index = group.member_index(entity_id)
            ways = group.members[index] if index is not None else (entity_id,)
            if any(self._state_is(way, wanted) for way in ways):
                continue
            failed.setdefault(entity_id, "didn't change")
        return failed

    def _linked_pending_members(self, group: LinkedGroup, wanted: str) -> set[int]:
        """Members that are not yet in the wanted state, believing the first reporter."""
        for level in group.levels:
            if not level.covering:
                continue
            entity_id = level.entity_ids[0]
            state = self.hass.states.get(entity_id)
            if state is None or state.state in UNKNOWN_STATES:
                break
            if self._state_is(entity_id, wanted):
                return set()
            return set(range(len(group.members)))
        pending: set[int] = set()
        members = group.members or (tuple(group.entity_ids),)
        for index, member in enumerate(members):
            # Believe the first reporting way in integration order, not the
            # step's entity-id order (a stale cloud or Bluetooth reading can
            # come first alphabetically).
            ordered: list[str] = []
            for level in group.levels:
                for entity_id in level.entity_ids:
                    if entity_id in member and entity_id not in ordered:
                        ordered.append(entity_id)
            for entity_id in member:
                if entity_id not in ordered:
                    ordered.append(entity_id)
            reporting = next(
                (
                    entity_id
                    for entity_id in ordered
                    if (st := self.hass.states.get(entity_id)) is not None
                    and st.state not in UNKNOWN_STATES
                ),
                None,
            )
            if reporting is None or not self._state_is(reporting, wanted):
                pending.add(index)
        return pending

    def _linked_step_remaining(
        self, group: LinkedGroup, entities: list[str], wanted: str
    ) -> list[str]:
        """The step's entities in this group that still need the command."""
        pending = self._linked_pending_members(group, wanted)
        if not pending:
            return []
        remaining: list[str] = []
        all_members = set(range(len(group.members))) if group.members else {0}
        for entity_id in entities:
            if entity_id not in group.entity_ids:
                continue
            index = group.member_index(entity_id)
            if index in pending:
                remaining.append(entity_id)
            elif index is None and pending == all_members:
                remaining.append(entity_id)
        return remaining

    def _integration_order(self, routine: dict[str, Any]) -> list[str]:
        own = routine.get("integration_order")
        return (
            list(own) if own is not None else list(self.settings["integration_order"])
        )

    def _verify_seconds(self, data: dict[str, Any]) -> float:
        verify: float = self.settings["verify_seconds"]
        if verify and isinstance(data.get("transition"), (int, float)):
            verify += data["transition"]  # a fade changes the state at its end
        return verify

    def _integration_of(self, entity_id: str) -> str:
        """The integration an entity comes from, as Home Assistant names it."""
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry is None:
            return ""  # not from an integration's registry: nothing to add
        platform = entry.platform
        name = integration_name(self.hass, platform)
        return name.replace("_", " ").title() if name == platform else name

    def _device_label(self, entity_id: str) -> str:
        """'Master Bedroom Right Curtain (SwitchBot Bluetooth)'."""
        name = self._entity_name(entity_id)
        integration = self._integration_of(entity_id)
        if not integration or name.endswith(f"({integration})"):
            return name
        return f"{name} ({integration})"

    def _level_label(self, level: IntegrationLevel | None) -> str:
        if level is None:
            return "an earlier way"
        names = _join([self._entity_name(e) for e in level.entity_ids])
        integration = self._integration_of(level.entity_ids[0])
        return f"{names} ({integration})" if integration else names

    def _device_failure(
        self, entity_id: str, why: str, wanted: str | None, verify: float
    ) -> str:
        """One device that didn't do it, and the concrete reason, in words."""
        label = self._device_label(entity_id)
        state = self.hass.states.get(entity_id)
        now = state.state if state is not None else None
        if why == "didn't change":
            if state is None:
                return f"{label} isn't in Home Assistant"
            if now == "unavailable":
                return f"{label} is unavailable"
            if now == "unknown":
                return f"{label} didn't change (its state is unknown)"
            if now in MOVING_STATES:
                return f"{label} was still {now} when the check ended"
            wait = f" after {verify:g} s" if verify else ""
            target = f" (wanted {wanted})" if wanted and wanted != now else ""
            return f"{label} stayed {now}{wait}{target}"
        if why.startswith("no answer within"):
            return f"{label} timed out: {why}"
        if now == "unavailable":
            return f"{label} is unavailable (Home Assistant said: {why})"
        if any(word in why.lower() for word in UNREACHABLE_WORDS):
            return f"{label} couldn't be reached: {why}"
        return f"{label} failed: {why}"

    def _level_failure(
        self,
        level: IntegrationLevel | None,
        failed: dict[str, str],
        wanted: str | None,
        verify: float,
    ) -> tuple[str, str]:
        """How one way of a linked device went, before the next way was tried:
        (the integration, what happened in words)."""
        way = self._integration_of(level.entity_ids[0]) if level else ""
        if not failed:
            return way, f"{self._level_label(level)} didn't do it"
        return way, "; ".join(
            self._device_failure(entity_id, why, wanted, verify)
            for entity_id, why in failed.items()
        )

    def _no_device_failure(
        self,
        action: dict[str, Any],
        target: dict[str, Any] | None,
        error: BaseException,
    ) -> str:
        """A step that reached no device of its kind, and what Home Assistant said."""
        what = step_label(self.hass, action, self._routine_names())
        text = _short_error(error)
        domain = action["service"].split(".", 1)[0]
        if not target or NO_TARGET_ERROR in text:
            return (
                f"“{what}” has no devices to send it to: pick the devices or a room "
                "in the step"
            )
        areas = [
            self._area_name(area_id) for area_id in (target or {}).get("area_id", [])
        ]
        where = f" in {_join(areas)}" if areas else ""
        return f"“{what}” found no {domain} devices{where}. Home Assistant said: {text}"

    def _area_name(self, area_id: str) -> str:
        area = ar.async_get(self.hass).async_get_area(area_id)
        return area.name if area else area_id

    def _log_step_error(
        self, routine: dict[str, Any], action: dict[str, Any], error: BaseException
    ) -> None:
        _LOGGER.warning(
            "Routine '%s': %s failed (%s).",
            routine["name"],
            action["service"],
            _short_error(error),
            exc_info=error if _LOGGER.isEnabledFor(logging.DEBUG) else None,
        )

    async def async_remember(self, name: str, entity_ids: list[str]) -> list[str]:
        """Remember which of these lights, switches and fans are on, and how the
        lights look, under a name. Returns the ones that are on."""
        kept: dict[str, Any] = {}
        for entity_id in sorted(set(entity_ids)):
            if entity_id.split(".", 1)[0] not in REMEMBER_DOMAINS:
                continue
            state = self.hass.states.get(entity_id)
            if state is None or state.state != "on":
                continue
            attrs: dict[str, Any] = {}
            if entity_id.startswith("light."):
                mode = state.attributes.get("color_mode")
                for key in LIGHT_KEYS:
                    value = state.attributes.get(key)
                    if value is None:
                        continue
                    if key == "color_temp_kelvin" and mode != "color_temp":
                        continue
                    if key == "hs_color" and mode in (
                        "color_temp",
                        "brightness",
                        "onoff",
                    ):
                        continue
                    attrs[key] = list(value) if isinstance(value, tuple) else value
            kept[entity_id] = attrs
        self.memories[name] = {"at": dt_util.now().isoformat(), "on": kept}
        await self._async_save()
        return list(kept)

    async def async_put_back(self, name: str, *, forget: bool = True) -> list[str]:
        """Turn back on what was remembered under that name, lights as they were."""
        memory = self.memories.get(name)
        if memory is None:
            raise vol.Invalid(f"Nothing is remembered as '{name}'.")
        failed: list[str] = []
        for entity_id, attrs in (memory.get("on") or {}).items():
            domain = entity_id.split(".", 1)[0]
            if self.hass.states.get(entity_id) is None:
                continue  # removed since
            data = dict(attrs) if domain == "light" else {}
            error = await self._call_service(
                domain, "turn_on", data, {"entity_id": [entity_id]}
            )
            if error is not None:
                failed.append(entity_id)
        if forget and not failed:
            self.memories.pop(name, None)
            await self._async_save()
        if failed:
            raise HomeAssistantError(
                f"{_join([self._entity_name(e) for e in failed])} couldn't be turned back on"
            )
        return list(memory.get("on") or {})

    def _entity_name(self, entity_id: str) -> str:
        state = self.hass.states.get(entity_id)
        return state.name if state is not None else entity_id

    async def _wait_for_state(
        self,
        entities: list[str],
        wanted: str,
        seconds: float,
        *,
        any_of: bool = False,
    ) -> list[str]:
        """Wait up to that long for the devices to reach the state; return the ones
        that didn't (with any_of, all of them unless one did). Curtains and blinds
        still opening or closing get longer, up to MOVING_SECONDS."""
        loop = asyncio.get_running_loop()
        start = loop.time()
        while True:
            left = [e for e in entities if not self._state_is(e, wanted)]
            if any_of and len(left) < len(entities):
                return []
            if not left:
                return []
            moving = any(self._state_is(e, s) for e in left for s in MOVING_STATES)
            limit = MOVING_SECONDS if moving else seconds
            if loop.time() - start >= max(seconds, limit):
                return left
            await asyncio.sleep(VERIFY_POLL_SECONDS)

    async def _wait_until_state(
        self,
        entity_id: str,
        states: list[str],
        seconds: float,
        *,
        below: float | None = None,
        above: float | None = None,
        hold: float = 0,
    ) -> bool:
        """Wait up to that long for a device to show one of the states, or a
        level below or above a number, held for ``hold`` seconds."""
        reached = asyncio.Event()
        held: Callable[[], None] | None = None

        def _now_in() -> bool:
            state = self.hass.states.get(entity_id)
            if state is None:
                return False
            if below is not None or above is not None:
                value = _as_float(state)
                if value is None:
                    return False
                return (below is None or value < below) and (
                    above is None or value > above
                )
            return state.state in states

        @callback
        def _settled(_now: datetime) -> None:
            nonlocal held
            held = None
            if _now_in():
                reached.set()

        @callback
        def _check() -> None:
            nonlocal held
            if not _now_in():
                if held is not None:
                    held()  # it changed back: the hold starts again next time
                    held = None
                return
            if hold <= 0:
                reached.set()
            elif held is None:
                held = async_call_later(self.hass, hold, _settled)

        @callback
        def _changed(_event: Event[EventStateChangedData]) -> None:
            _check()

        unsub = async_track_state_change_event(self.hass, [entity_id], _changed)
        _check()
        try:
            async with asyncio.timeout(seconds):
                await reached.wait()
        except TimeoutError:
            return False
        finally:
            unsub()
            if held is not None:
                held()
        return True

    async def _run_other(
        self, routine: dict[str, Any], other_id: str
    ) -> tuple[str, bool]:
        """Run another routine as a step. Returns (what happened, whether it failed)."""
        other = self._find(other_id)
        if other is None:
            return "The routine to run isn't there any more", True
        chain = (*_CHAIN.get(), routine["id"])
        if other_id in chain:
            return f"{other['name']} would run itself in a loop", True
        if self._rt(other_id)["state"] == "running":
            return f"{other['name']} was already running", False
        token = _CHAIN.set(chain)
        try:
            ran = await self._execute_routine(other, dt_util.now(), scheduled=False)
        finally:
            _CHAIN.reset(token)
        if ran:
            return f"Ran {other['name']}", False
        if self._rt(other_id)["state"] == "error":
            return f"{other['name']} failed", True
        reason = self._rt(other_id)["last_result"] or "its conditions weren't met"
        return f"{other['name']} didn't run ({reason})", False

    def busy(self) -> bool:
        """A routine or the lived-in lights are changing things right now."""
        return any(rt.get("state") == "running" for rt in self._runtime.values()) or (
            self.lived_in.active(dt_util.now())
        )

    def resolve_targets(self, target: dict[str, Any] | None, domain: str) -> list[str]:
        return self._resolve_targets(target, domain)

    def room_occupied(self, area_id: str) -> bool | None:
        """Someone is in the room, by its motion, occupancy or presence sensors.
        None: it has none, or none can be read."""
        seen = None
        for entity_id in self._area_entities(area_id):
            if not entity_id.startswith("binary_sensor."):
                continue
            state = self.hass.states.get(entity_id)
            if state is None or state.attributes.get("device_class") not in (
                OCCUPANCY_CLASSES
            ):
                continue
            if state.state == "on":
                return True
            if state.state == "off":
                seen = False
        return seen

    def _only_rooms(self, action: dict[str, Any]) -> dict[str, Any]:
        """The step for only the rooms someone is in (or only the empty ones).
        A room whose sensors can't tell is left alone."""
        want = action["rooms"] == "occupied"
        target = dict(action.get("target") or {})
        target["area_id"] = [
            a for a in target.get("area_id", []) if self.room_occupied(a) is want
        ]
        if not target["area_id"]:
            del target["area_id"]
        result = {k: v for k, v in action.items() if k != "target"}
        if target:
            result["target"] = target
        return result

    def _until_cheapest(self, wait: dict[str, Any]) -> tuple[float, str]:
        """Seconds until the cheapest price slot in the next minutes, and a note."""
        state = self.hass.states.get(wait["entity_id"])
        now = dt_util.now()
        slots = price_slots(dict(state.attributes), now) if state else []
        current = [s for s in slots if s[0] <= now]
        begin = current[-1][0] if current else now
        start = cheapest_start(slots, begin, now + timedelta(minutes=wait["minutes"]))
        if start is None:
            return 0, "No prices to wait for: went on straight away"
        if start <= now:
            return 0, ""
        return (start - now).total_seconds(), (
            f"Waited for the cheapest price, from {dt_util.as_local(start):%H:%M}"
        )

    def hold(self, now: datetime) -> str | None:
        """The calendar event holding routines back now, if any."""
        holds = self.settings.get("holds") or []
        return active_hold(holds, self._upcoming, now) if holds else None

    async def async_undo_recent(self, minutes: int = 60) -> list[str]:
        """Undo every routine that ran in the last minutes, newest first.
        Returns the names of the routines put back."""
        now = dt_util.now()
        recent = []
        for rid, kept in self._undo.items():
            at = dt_util.parse_datetime(kept["at"])
            if at and now - at <= timedelta(minutes=minutes) and self.can_undo(rid):
                recent.append((at, rid))
        done = []
        for _at, rid in sorted(recent, reverse=True):
            routine = self._find(rid)
            try:
                await self.async_undo(rid)
            except (HomeAssistantError, vol.Invalid) as err:
                _LOGGER.warning("Couldn't undo %s: %s", rid, err)
                continue
            if routine:
                done.append(routine["name"])
        return done

    def reliability(self, routine: dict[str, Any]) -> dict[str, Any] | None:
        """How reliably the routine's devices answered over 30 days."""
        entities: list[str] = []
        for action in routine["actions"]:
            for step in action.get("together", [action]):
                if "service" in step:
                    domain = step["service"].split(".", 1)[0]
                    entities.extend(self._resolve_targets(step.get("target"), domain))
        return self.device_stats.score(entities)

    async def _speak_briefing(self, brief: dict[str, Any]) -> str | None:
        """Say the day ahead. Returns what went wrong, or None."""
        now = dt_util.now()
        weather = ""
        if brief["weather"]:
            forecast = self._forecast_cache.get(brief["weather"])
            if forecast is None:
                forecast = await self._read_forecast(brief["weather"]) or []
            highs = [
                float(f["temperature"])
                for f in forecast
                if isinstance(f.get("temperature"), (int, float))
                and (when := dt_util.parse_datetime(str(f.get("datetime") or "")))
                and dt_util.as_local(when).date() == now.date()
            ]
            weather = weather_text(
                self.hass.states.get(brief["weather"]), max(highs) if highs else None
            )
        events = None
        if brief["calendars"]:
            found: list[dict[str, Any]] = []
            start = dt_util.start_of_local_day(now)
            for calendar in brief["calendars"]:
                found.extend(
                    await self._read_events(calendar, start, start + timedelta(days=1))
                    or []
                )
            events = events_text(found, now)
        day = (
            self.day_type(now)
            if brief["day_type"] and self.settings.get("day_types")
            else None
        )
        message = briefing_text(now, day_type=day, weather=weather, events=events)
        error = await self._call_service(
            "tts",
            "speak",
            {"media_player_entity_id": brief["speaker"], "message": message},
            {"entity_id": [brief["voice"]]},
        )
        return None if error is None else f"Couldn't say the briefing: {error}"

    def _note_who(self, routine_id: str, name: str) -> None:
        """Remember who set off a presence trigger, for "{who}" in its steps."""
        self._who[routine_id.split("~", 1)[0]] = name

    def _low_batteries(self, below: int) -> list[tuple[str, str]]:
        readings = [
            (state.name, state.domain, state.state)
            for state in self.hass.states.async_all(("sensor", "binary_sensor"))
            if is_battery(state.domain, dict(state.attributes))
        ]
        return low_batteries(readings, below)

    async def _wait_ticks(self, routine: dict[str, Any], ticks: dict[str, Any]) -> bool:
        """Show a checklist on the dashboard and wait until it's all ticked off."""
        rt = self._rt(routine["id"])
        done = asyncio.Event()
        rt["ticklist"] = {"items": list(ticks["items"]), "ticked": []}
        rt["tick_done"] = done
        self._notify_listeners()
        try:
            await asyncio.wait_for(done.wait(), ticks["minutes"] * SECONDS_PER_MINUTE)
        except TimeoutError:
            return False
        finally:
            rt.pop("ticklist", None)
            rt.pop("tick_done", None)
            self._notify_listeners()
        return True

    def async_tick_item(self, routine_id: str, item: str, done: bool) -> list[str]:
        """Tick (or untick) a thing on a running routine's checklist."""
        rt = self._rt(routine_id)
        ticklist = rt.get("ticklist")
        if not ticklist or item not in ticklist["items"]:
            raise HomeAssistantError("That checklist isn't showing any more")
        ticked = [i for i in ticklist["ticked"] if i != item]
        if done:
            ticked.append(item)
        ticklist["ticked"] = [i for i in ticklist["items"] if i in ticked]
        if len(ticklist["ticked"]) == len(ticklist["items"]) and rt.get("tick_done"):
            rt["tick_done"].set()
        self._notify_listeners()
        return ticklist["ticked"]

    def attention(self, now: datetime) -> list[dict[str, Any]]:
        """Routines that often fail or are skipped (last 14 days, 3+ tries)."""
        log = self.run_log
        return needs_attention(
            log.view(), now, self.routines, times=log.times(), ordered=log.ordered
        )

    def energy_month(self, now: datetime) -> dict[str, Any]:
        names = {r["id"]: r["name"] for r in self.routines}
        return month_energy(self.power, names, now)

    def _left_open(self, entity_ids: list[str]) -> list[tuple[str, str]]:
        """Which of these devices are open, unlocked or on (checklist.py)."""
        states: dict[str, tuple[str, str] | None] = {}
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            states[entity_id] = (state.name, state.state) if state else None
        return left_open(states)

    def explain(self, routine_id: str) -> dict[str, Any]:
        """Why a routine won't run (or didn't), in reason codes for the panel."""
        routine = self._find(routine_id)
        if routine is None:
            raise vol.Invalid(f"No routine with id '{routine_id}'.")
        now = dt_util.now()
        rt = self._rt(routine_id)
        reasons: list[str] = []
        if not routine["enabled"]:
            reasons.append("disabled")
        if self.paused:
            reasons.append("paused")
        if (until := self.paused_until(routine)) and now.date().isoformat() <= until:
            reasons.append("paused_until")
        if routine.get("skip_next"):
            reasons.append("skip_next")
        if not routine.get("ignore_holds") and self.hold(now):
            reasons.append("calendar_hold")
        if self.mode in routine.get("skip_modes", []):
            reasons.append("skip_mode")
        if now.date().isoformat() in routine.get("skip_dates", []):
            reasons.append("skip_date")
        if not self._allowed_day(routine, now):
            reasons.append("not_today")
        if self._cooling_down(routine, now):
            reasons.append("cooldown")
        if not routine["repeat"] and rt["last_run_date"] == now.date().isoformat():
            reasons.append("already_ran_today")
        ok, reason = self._check(self._source(routine), now)
        if not ok and reason:
            reasons.append(reason)
        last_skip = next(
            (
                e
                for e in reversed(self.run_log.view())
                if e.get("routine_id") == routine_id and e["outcome"] == "skipped"
            ),
            None,
        )
        last_failure = next(
            (
                e
                for e in reversed(self.run_log.view())
                if e.get("routine_id") == routine_id and e["outcome"] == "failed"
            ),
            None,
        )
        return {
            "reasons": list(dict.fromkeys(reasons)),
            "next_run": self._next_run_of(routine_id),
            "last_skip": last_skip,
            "last_failure": last_failure,
            "unavailable": self._unavailable_names(routine),
            "notes": self._setup_notes(routine),
            "plan": self._device_plan(routine),
        }

    def _setup_notes(self, routine: dict[str, Any]) -> list[str]:
        """Things in a routine's setup that make it do less than it looks like."""
        notes: list[str] = []
        for number, cond in enumerate(routine.get("conditions") or [], 1):
            if cond.get("kind") == "sensors" and not cond.get("sensors"):
                notes.append(
                    f"Only if, condition {number}: no sensors are picked, so it is "
                    "ignored. Pick the sensors, or remove it."
                )
        for number, action in enumerate(routine["actions"], 1):
            cond = action.get("only_if") or {}
            if cond.get("kind") == "sensors" and not cond.get("sensors"):
                notes.append(
                    f"Step {number}: its Only if has no sensors picked, so it is ignored."
                )
            for each in action.get("together") or [action]:
                service = each.get("service")
                if not service or service.split(".", 1)[0] not in DEVICE_DOMAINS:
                    continue
                label = step_label(self.hass, each, self._routine_names())
                target = each.get("target") or {}
                if not target:
                    notes.append(
                        f"Step {number} (“{label}”) has no devices or room picked, "
                        "so it can't do anything."
                    )
                elif not self._resolve_targets(target, service.split(".", 1)[0]):
                    notes.append(
                        f"Step {number} (“{label}”) finds no "
                        f"{service.split('.', 1)[0]} devices to send it to."
                    )
        return notes

    def _device_plan(self, routine: dict[str, Any]) -> list[dict[str, Any]]:
        """For each device step: the ways a linked device is tried, in order."""
        plan: list[dict[str, Any]] = []
        for number, action in enumerate(routine["actions"], 1):
            for each in action.get("together") or [action]:
                service = each.get("service")
                if not service:
                    continue
                entities = self._resolve_targets(
                    each.get("target"), service.split(".", 1)[0]
                )
                if not entities:
                    continue
                ways: list[str] = []
                groups, led_at, area_levels = self._lead_plan(
                    routine, service.split(".", 1)[0], entities
                )
                in_area_levels = {e for level in area_levels for e in level}
                if area_levels:
                    ways.append(
                        "Linked device first: "
                        + " → then ".join(
                            self._level_label(
                                IntegrationLevel(
                                    domain=service.split(".", 1)[0], entity_ids=ids
                                )
                            )
                            for ids in area_levels
                        )
                    )
                for index, group in enumerate(groups):
                    if group.has_fallback:
                        first = "Linked device first: " if index == led_at else ""
                        ways.append(
                            first
                            + " → then ".join(
                                self._level_label(level) for level in group.levels
                            )
                        )
                    else:
                        ways.extend(
                            self._device_label(e)
                            for e in group.entity_ids
                            if e not in in_area_levels
                        )
                plan.append(
                    {
                        "step": number,
                        "label": step_label(self.hass, each, self._routine_names()),
                        "ways": ways,
                    }
                )
        return plan

    async def _ramp_volume(self, ramp: dict[str, Any], seconds: float) -> str | None:
        """Turn speakers up (or down) bit by bit. Returns what went wrong, or None."""
        players = [e for e in ramp["entity_id"] if self.hass.states.get(e)]
        if not players:
            return "The speakers aren't available"
        start, end = ramp["from"] / 100, ramp["to"] / 100
        for step in range(RAMP_STEPS + 1):
            level = round(start + (end - start) * step / RAMP_STEPS, 3)
            error = await self._call_service(
                "media_player",
                "volume_set",
                {"volume_level": level},
                {"entity_id": players},
            )
            if error is not None:
                return f"Couldn't set the volume: {error}"
            if step < RAMP_STEPS:
                await asyncio.sleep(seconds / RAMP_STEPS)
        return None

    def _note_power(self, routine: dict[str, Any]) -> None:
        """Read the power of the routine's devices before it runs."""
        entities: list[str] = []
        for action in routine["actions"]:
            for step in action.get("together", [action]):
                if "service" in step:
                    domain = step["service"].split(".", 1)[0]
                    entities.extend(self._resolve_targets(step.get("target"), domain))
                if "volume_ramp" in step:
                    entities.extend(step["volume_ramp"]["entity_id"])
        sensors = power_sensors(self.hass, entities)
        watts = read_watts(self.hass, sensors) if sensors else None
        rt = self._rt(routine["id"])
        if watts is None:
            rt.pop("power_before", None)
        else:
            rt["power_before"] = (sensors, watts)

    async def _read_power_after(
        self, routine_id: str, sensors: list[str], before: float
    ) -> None:
        await asyncio.sleep(POWER_SETTLE_SECONDS)
        after = read_watts(self.hass, sensors)
        if after is None or self._find(routine_id) is None:
            return
        readings = self.power.setdefault(routine_id, [])
        readings.append(
            {"at": dt_util.now().isoformat(), "before": before, "after": after}
        )
        del readings[:-POWER_RUNS]
        self._save_soon()
        self._notify_listeners()

    def power_of(self, routine_id: str) -> dict[str, Any] | None:
        return power_summary(self.power.get(routine_id) or [])

    async def async_pause_group(self, group: str, until: str | None) -> list[str]:
        """Pause (or resume, with None) every routine in a group."""
        ids = [r["id"] for r in self.routines if group and r.get("group") == group]
        if not ids:
            raise vol.Invalid(f"No routines in the group '{group}'.")
        await self.async_pause_until(ids, until)
        return ids

    def _keep_for_undo(self, routine: dict[str, Any], now: datetime) -> None:
        """Note how the devices this routine changes are now, for Undo."""
        entities: list[str] = []
        steps = [
            step
            for action in routine["actions"]
            for step in action.get("together", [action])
        ]
        for action in steps:
            if "service" in action:
                domain = action["service"].split(".", 1)[0]
                entities.extend(self._resolve_targets(action.get("target"), domain))
                # "Make it look like this": the devices in its picture.
                entities.extend((action.get("data") or {}).get("entities") or {})
        saved = snapshot(self.hass, entities)
        if saved:
            self._undo[routine["id"]] = {"at": now.isoformat(), "states": saved}
        else:
            self._undo.pop(routine["id"], None)

    def can_undo(self, routine_id: str) -> bool:
        kept = self._undo.get(routine_id)
        if kept is None or self._rt(routine_id)["state"] == "running":
            return False
        at = dt_util.parse_datetime(kept["at"])
        return at is not None and dt_util.now() - at < UNDO_FOR

    async def async_undo(self, routine_id: str) -> list[str]:
        """Put back what the routine's last run changed. Returns what was put back."""
        routine = self._find(routine_id)
        if routine is None:
            raise vol.Invalid(f"No routine with id '{routine_id}'.")
        if not self.can_undo(routine_id):
            raise vol.Invalid(f"There's nothing to undo for '{routine['name']}'.")
        kept = self._undo.pop(routine_id)
        failed = await restore(self.hass, kept["states"], self._call_service)
        put_back = [e for e in kept["states"] if e not in failed]
        if failed:
            names = _join([self._entity_name(e) for e in failed])
            self._record(routine_id, "failed", f"Undo: {names} couldn't be put back")
        else:
            self._record(routine_id, "ran", "Undone: put back as it was before")
        self._notify_listeners()
        if failed:
            raise HomeAssistantError(f"{names} couldn't be put back")
        return put_back

    def paused_until(self, routine: dict[str, Any]) -> str | None:
        """The day a routine is paused through; a turn-off routine follows its routine."""
        owner = (
            self._find(routine["companion_of"]) if routine.get("companion_of") else None
        )
        return (owner or routine).get("paused_until")

    async def async_skip_next(self, routine_id: str, skip: bool = True) -> None:
        """Skip (or stop skipping) a routine's next scheduled run."""
        routine = self._find(routine_id)
        if routine is None:
            raise vol.Invalid(f"No routine with id '{routine_id}'.")
        async with self._lock:
            routine["skip_next"] = bool(skip)
            self.revision += 1
            await self._async_save()
        self._notify_listeners()

    async def async_pause_until(
        self, routine_ids: list[str], until: str | None
    ) -> None:
        """Pause routines' scheduled runs through a day (None resumes them)."""
        day = normalize_date(until, "Paused until")
        routines = [self._find(rid) for rid in routine_ids]
        missing = [
            rid for rid, r in zip(routine_ids, routines, strict=True) if r is None
        ]
        if missing:
            raise vol.Invalid(f"No routine with id '{missing[0]}'.")
        async with self._lock:
            for routine in routines:
                assert routine is not None
                routine["paused_until"] = day
            self.revision += 1
            await self._async_save()
        self._notify_listeners()

    async def _ask_first(self, routine: dict[str, Any]) -> bool:
        """Ask on the phone and the card whether to run now. True: run it."""
        rid = routine["id"]
        rt = self._rt(rid)
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._asks[rid] = future
        minutes = routine["ask_minutes"]
        rt["state"] = "asking"
        rt["ask_until"] = (
            dt_util.now() + timedelta(seconds=minutes * SECONDS_PER_MINUTE)
        ).isoformat()
        self._notify_listeners()
        default = "runs" if routine["ask_default"] == "run" else "is skipped"
        self._send_notification(
            f"Run '{routine['name']}' now? With no answer in {minutes} min, it {default}.",
            f"daily_routine_ask_{rid}",
            actions=[
                {"action": f"{ASK_RUN_ACTION}{rid}", "title": "Run"},
                {"action": f"{ASK_SKIP_ACTION}{rid}", "title": "Skip"},
            ],
        )
        answer = routine["ask_default"]
        answered = False
        try:
            async with asyncio.timeout(minutes * SECONDS_PER_MINUTE):
                answer = await future
                answered = True
        except TimeoutError:
            pass
        finally:
            self._asks.pop(rid, None)
            rt["ask_until"] = None
        if answer == "run":
            return True
        reason = "asked_skip" if answered else "no_answer_skip"
        rt["state"] = "skipped"
        rt["last_result"] = reason
        self._record(rid, "skipped", reason)
        self._fire_event(EVENT_ROUTINE_SKIPPED, routine, reason)
        self._notify_listeners()
        return False

    def async_answer(self, routine_id: str, run: bool) -> bool:
        """Answer "Ask me first": run or skip. False when nothing is asking."""
        future = self._asks.get(routine_id)
        if future is None or future.done():
            return False
        future.set_result("run" if run else "skip")
        return True

    async def async_copy_to_rooms(
        self, routine_id: str, rooms: list[str]
    ) -> list[dict[str, Any]]:
        """Copy a routine to other rooms: steps aimed at its room aim at each new one."""
        routine = self._find(routine_id)
        if routine is None:
            raise vol.Invalid(f"No routine with id '{routine_id}'.")
        names = {a["id"]: a["name"] for a in list_areas(self.hass) if a["id"]}
        copies = []
        for room in rooms:
            if room not in names:
                raise vol.Invalid(f"No room '{room}'.")
            if room == routine["room"]:
                continue
            new_id = self._free_id(f"{routine_id}_{slugify(room)}"[:64])
            raw = {
                **routine,
                "id": new_id,
                "name": f"{routine['name']} ({names[room]})",
                "room": room,
                "device_id": None,
                "skip_next": False,
                "paused_until": None,
                "companion_of": None,
                "actions": [
                    _moved_action(a, routine["room"], room) for a in routine["actions"]
                ],
            }
            copies.append(await self.async_save_routine(raw))
        return copies

    def _free_id(self, wanted: str) -> str:
        taken = {r["id"] for r in self.routines}
        candidate, number = wanted, 2
        while candidate in taken:
            suffix = f"_{number}"
            candidate = wanted[: 64 - len(suffix)] + suffix
            number += 1
        return candidate

    async def async_start_run(self, routine_id: str) -> dict[str, Any]:
        """Run now from the dashboard: start it and answer at once.

        A routine that can't run (turned off, conditions not met) is recorded
        straight away and answered with the reason. One already running isn't
        started a second time.
        """
        routine = self._find(routine_id)
        if routine is None:
            raise vol.Invalid(f"No routine with id '{routine_id}'.")
        if self._rt(routine_id)["state"] == "running":
            return {"started": False, "ran": False, "reason": "already_running"}
        now = dt_util.now()
        ok, reason = self._check(routine, now)
        if not routine["enabled"] or not ok:
            await self._execute_routine(routine, now, scheduled=False)
            return {
                "started": False,
                "ran": False,
                "reason": "disabled" if not routine["enabled"] else reason,
            }
        # Shown as running before the reply, so the card changes at once.
        self._rt(routine_id)["started_by"] = None
        self._rt(routine_id)["state"] = "running"
        self._notify_listeners()
        self._spawn(self._execute_routine(routine, now, scheduled=False))
        return {"started": True, "ran": None, "reason": None}

    async def async_run(self, routine_id: str) -> bool:
        """Manual run: conditions still apply, the once-a-day rule does not."""
        routine = self._find(routine_id)
        if routine is None:
            raise vol.Invalid(f"No routine with id '{routine_id}'.")
        return await self._execute_routine(routine, dt_util.now(), scheduled=False)

    async def preview(self, routine: dict[str, Any]) -> dict[str, Any]:
        """What a run would do right now. Reads lists for to-do steps, and calls no service."""
        now = dt_util.now()
        ok, reason = self._check(routine, now)
        steps: list[dict[str, Any]] = []
        for action in routine["actions"]:
            if (cond := action.get("only_if")) and not self._condition_ok(cond, now):
                steps.append(
                    {
                        "type": "only_if",
                        "label": step_label(self.hass, action, self._routine_names()),
                    }
                )
                continue
            if "delay" in action:
                steps.append({"type": "delay", "seconds": action["delay"]})
                continue
            if not ({"service", "todo"} & set(action)):
                # Run a routine, wait until, house mode: described, not tried.
                steps.append(
                    {
                        "type": "other",
                        "label": step_label(self.hass, action, self._routine_names()),
                    }
                )
                continue
            if "todo" in action:
                step = action["todo"]
                outcome = await apply_todo_step(self.hass, step, dry_run=True)
                steps.append(
                    {
                        "type": "todo",
                        "op": step["op"],
                        "list": step["list"],
                        "item": step["item"],
                        "outcome": outcome,
                    }
                )
                continue
            outcome, _override, entities = self._plan_action(routine, action)
            steps.append(
                {
                    "type": "service",
                    "service": action["service"],
                    "outcome": outcome,
                    "entities": entities,
                    "target": action.get("target") or {},
                }
            )
        return {
            "conditions_ok": ok,
            "block_reason": reason,
            "paused": self.paused,
            "steps": steps,
        }

    def _health_entities(self, routine: dict[str, Any]) -> list[str]:
        """The devices and lists a routine needs to be present and available."""
        entities: list[str] = []
        if routine["trigger"] in WATCH_TRIGGERS:
            entities.append(routine["watch_entity"])
        for action in routine["actions"]:
            if "todo" in action:
                entities.append(action["todo"]["list"])
        return list(dict.fromkeys(entities))

    def _missing_entities(self, routine: dict[str, Any]) -> list[str]:
        """Lists and devices the routine needs that are not in Home Assistant at all."""
        return [
            entity_id
            for entity_id in self._health_entities(routine)
            if self.hass.states.get(entity_id) is None
        ]

    def _unavailable_names(self, routine: dict[str, Any]) -> list[str]:
        """Names of the lists and devices the routine needs that are there but unavailable."""
        names: list[str] = []
        for entity_id in self._health_entities(routine):
            state = self.hass.states.get(entity_id)
            if state is not None and state.state in UNKNOWN_STATES:
                names.append(state.attributes.get("friendly_name") or entity_id)
        return names

    def _hold_if_unavailable(
        self, routine: dict[str, Any], now: datetime, *, force: bool = False
    ) -> bool:
        """Hold this run until its lists and devices are back. True when it was held."""
        if not routine["wait_when_unavailable"] or self._missing_entities(routine):
            return False
        names = self._unavailable_names(routine)
        if not names and not force:
            return False
        rt = self._rt(routine["id"])
        if rt["held_run"] is None:
            rt["held_run"] = {"at": dt_util.utcnow()}
            rt["state"] = "held"
            waiting = ", ".join(names) or "a to-do list"
            _LOGGER.info(
                "Routine '%s' is waiting for %s to come back.", routine["name"], waiting
            )
            self._record(routine["id"], "held", f"waiting for {waiting}")
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "waiting:unavailable")
        self._notify_listeners()
        if force:
            # The list went away after the check above; look again when it changes.
            self._schedule_health_check(routine["id"])
        return True

    def _release_held(self, routine: dict[str, Any]) -> None:
        """Everything is back: run the held run now."""
        rt = self._rt(routine["id"])
        if rt["held_run"] is None or self._expire_held(routine):
            return
        rt["held_run"] = None
        rt["state"] = "idle"
        _LOGGER.info(
            "Routine '%s' is running now that its list is back.", routine["name"]
        )
        self._spawn(self._execute_routine(routine, dt_util.now(), scheduled=True))

    def _expire_held(self, routine: dict[str, Any]) -> bool:
        """Drop a held run that waited too long. True when it was dropped."""
        rt = self._rt(routine["id"])
        held = rt["held_run"]
        if held is None or dt_util.utcnow() - held["at"] < timedelta(
            hours=WAIT_UNAVAILABLE_HOURS
        ):
            return False
        rt["held_run"] = None
        rt["state"] = "skipped"
        rt["last_result"] = "unavailable_too_long"
        self._record(routine["id"], "skipped", "unavailable_too_long")
        self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "unavailable_too_long")
        if routine["notify_on_skip"]:
            self._send_notification(
                f"Routine '{routine['name']}' skipped – its list or device did not come back within {WAIT_UNAVAILABLE_HOURS} hours."
            )
        return True

    def health(self, routine: dict[str, Any]) -> list[str]:
        """Things that will stop this routine from working, shown on its card."""
        problems: list[str] = []
        for entity_id in self._health_entities(routine):
            state = self.hass.states.get(entity_id)
            if state is None:
                problems.append(f"{entity_id} is not in Home Assistant.")
            elif state.state in UNKNOWN_STATES:
                name = state.attributes.get("friendly_name") or entity_id
                if routine.get("wait_when_unavailable"):
                    problems.append(
                        f"{name} is unavailable right now. Waiting for it to come back."
                    )
                else:
                    problems.append(f"{name} is unavailable right now.")
        return problems

    # ----- service handlers -----


# Turning a step off again: the opposite service for each service a routine can use.
OPPOSITE_SERVICES = {
    "light.turn_on": "light.turn_off",
    "light.turn_off": "light.turn_on",
    "switch.turn_on": "switch.turn_off",
    "switch.turn_off": "switch.turn_on",
    "fan.turn_on": "fan.turn_off",
    "fan.turn_off": "fan.turn_on",
    "cover.open_cover": "cover.close_cover",
    "cover.close_cover": "cover.open_cover",
    "lock.lock": "lock.unlock",
    "lock.unlock": "lock.lock",
}


def _timing_value(value: Any, key: str) -> int:
    """A saved timing setting, or its default when the saved value is missing or out of range."""
    default, low, high, _label = TIMING_SETTINGS[key]
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not low <= value <= high
    ):
        return default
    return value


def _companion_for(routine: dict[str, Any]) -> dict[str, Any] | None:
    """A 'turn back off' routine for a value-above routine with a turn-off level."""
    if routine["trigger"] != TRIGGER_VALUE_ABOVE or routine["off_threshold"] is None:
        return None
    actions: list[dict[str, Any]] = []
    for action in routine["actions"]:
        if "service" not in action:
            if "delay" in action:
                actions.append(action)
            # To-do, run-a-routine, mode and wait steps aren't reversed.
            continue
        opposite = OPPOSITE_SERVICES.get(action["service"])
        if opposite is None:
            raise vol.Invalid(
                f"Can't work out how to turn {action['service']} back off. "
                "Remove the turn-off level, or make the off routine yourself."
            )
        actions.append({**action, "service": opposite})
    return {
        "id": f"{routine['id']}_off",
        "name": f"{routine['name']} (off)",
        "enabled": routine["enabled"],
        "room": routine["room"],
        "device_id": routine["device_id"],
        "trigger": TRIGGER_VALUE_BELOW,
        "value_entity": routine["value_entity"],
        "value_threshold": routine["off_threshold"],
        "days": routine["days"],
        "from_date": routine["from_date"],
        "until_date": routine["until_date"],
        "cooldown_minutes": routine["cooldown_minutes"],
        "integration_order": routine["integration_order"],
        "actions": actions,
        "retries": routine["retries"],
        "on_step_error": routine["on_step_error"],
        "retry_later_minutes": routine["retry_later_minutes"],
        "notify_on_error": routine["notify_on_error"],
        "companion_of": routine["id"],
    }


def _lived_in_or_default(raw: Any) -> dict[str, Any]:
    try:
        return clean_lived_in(raw)
    except vol.Invalid:
        return clean_lived_in(None)


def _moved_action(action: dict[str, Any], old: str | None, new: str) -> dict[str, Any]:
    """A step copied to another room: aimed at the new room where it was aimed at the old."""
    if "together" in action:
        return {
            **action,
            "together": [_moved_action(a, old, new) for a in action["together"]],
        }
    target = action.get("target")
    if not target or not old or old not in target.get("area_id", []):
        return action
    areas = [new if a == old else a for a in target["area_id"]]
    return {**action, "target": {**target, "area_id": areas}}


def _weather_ok(state: State | None, cond: dict[str, Any]) -> bool:
    """What the weather is doing, or its temperature. Unknown fails closed."""
    if state is None or state.state in UNKNOWN_STATES:
        return False
    if cond["test"] == "is":
        return state.state in cond["conditions"]
    if cond["test"] == "is_not":
        return state.state not in cond["conditions"]
    try:
        temperature = float(state.attributes["temperature"])
    except KeyError, TypeError, ValueError:
        return False
    if cond["test"] == "above":
        return temperature > cond["value"]
    return temperature < cond["value"]


def _as_float(state: State | None) -> float | None:
    if state is None:
        return None
    try:
        return float(state.state)
    except TypeError, ValueError:
        return None


def _within_hours(now: datetime, start: str, end: str) -> bool:
    """True inside [start, end). A window that crosses midnight (for example 22:00 to 06:00) is allowed."""
    local = dt_util.as_local(now).time()
    begin, finish = _parse_time(start), _parse_time(end)
    if begin <= finish:
        return begin <= local < finish
    return local >= begin or local < finish


def _parse_time(value: str) -> dt_time:
    hour, minute, second = (int(p) for p in value.split(":"))
    return dt_time(hour, minute, second)
