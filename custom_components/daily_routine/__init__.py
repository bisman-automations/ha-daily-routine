"""Daily Routine – reusable, safety-aware daily routines for Home Assistant."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from datetime import time as dt_time
from typing import Any, Callable

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET, Platform
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    ServiceCall,
    callback,
)
from homeassistant.helpers import storage
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
)
from homeassistant.helpers.sun import get_astral_event_next
from homeassistant.util import dt as dt_util

from .config_flow import options_to_routine
from .const import (
    CONF_ROUTINES_JSON,
    DOMAIN,
    EVENT_ROUTINE_FINISHED,
    EVENT_ROUTINE_SKIPPED,
    EVENT_ROUTINE_STARTED,
    MAX_ACTIONS,
    MAX_ROUTINES,
    STORAGE_KEY,
    STORAGE_VERSION,
    TRIGGER_EVERYONE_LEAVES,
    TRIGGER_SOMEONE_ARRIVES,
    TRIGGER_SUNRISE,
    TRIGGER_SUNSET,
    TRIGGER_TIME,
    WEEKDAYS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

ROUTINE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
SERVICE_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_routines(raw: str) -> list[dict[str, Any]]:
    """Parse and validate routine JSON; reject malformed definitions."""
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as err:
        raise vol.Invalid(f"Routines must be valid JSON: {err}") from err

    if not isinstance(parsed, list) or len(parsed) > MAX_ROUTINES:
        raise vol.Invalid(f"Routines must be a list with no more than {MAX_ROUTINES} items.")

    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    required = {
        "id",
        "name",
        "enabled",
        "time",
        "days",
        "require_everyone_away",
        "require_sensors_clear",
        "clear_minutes",
        "people",
        "sensors",
        "actions",
    }

    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise vol.Invalid(f"Routine {index + 1} must be an object.")

        missing = required - item.keys()
        if missing:
            raise vol.Invalid(f"Routine {index + 1} is missing: {', '.join(sorted(missing))}.")

        rid = item["id"]
        if not isinstance(rid, str) or not ROUTINE_ID_RE.fullmatch(rid):
            raise vol.Invalid(
                f"Routine {index + 1} has an invalid id "
                f"(use letters, digits, _ or - only, max 64 chars)."
            )
        if rid in seen:
            raise vol.Invalid(f"Duplicate routine id: {rid}")
        seen.add(rid)

        if not isinstance(item["name"], str) or not item["name"].strip():
            raise vol.Invalid(f"Routine {rid}: name cannot be empty.")

        if not isinstance(item["enabled"], bool):
            raise vol.Invalid(f"Routine {rid}: enabled must be true or false.")

        if not isinstance(item["time"], str):
            raise vol.Invalid(f"Routine {rid}: time must be a string.")
        try:
            if len(item["time"]) == 8:
                datetime.strptime(item["time"], "%H:%M:%S")
            else:
                datetime.strptime(item["time"], "%H:%M")
        except ValueError as err:
            raise vol.Invalid(f"Routine {rid}: time must be HH:MM or HH:MM:SS.") from err

        if not isinstance(item["days"], list) or any(day not in WEEKDAYS for day in item["days"]):
            raise vol.Invalid(
                f"Routine {rid}: days must contain weekday abbreviations ({', '.join(WEEKDAYS)})."
            )

        for key in ("require_everyone_away", "require_sensors_clear"):
            if not isinstance(item[key], bool):
                raise vol.Invalid(f"Routine {rid}: {key} must be true or false.")

        if not isinstance(item["clear_minutes"], int) or not (0 <= item["clear_minutes"] <= 1440):
            raise vol.Invalid(f"Routine {rid}: clear_minutes must be an integer from 0 to 1440.")

        for key in ("people", "sensors"):
            if not isinstance(item[key], list) or any(
                not isinstance(x, str) or "." not in x for x in item[key]
            ):
                raise vol.Invalid(f"Routine {rid}: {key} must be a list of entity IDs.")

        if item["require_everyone_away"] and not item["people"]:
            raise vol.Invalid(
                f"Routine {rid}: add at least one person when requiring everyone away."
            )
        if item["require_sensors_clear"] and not item["sensors"]:
            raise vol.Invalid(
                f"Routine {rid}: add at least one sensor when requiring sensors clear."
            )

        # Optional notify flags (default false)
        for key in ("notify_on_run", "notify_on_skip"):
            if key in item and not isinstance(item[key], bool):
                raise vol.Invalid(f"Routine {rid}: {key} must be true or false.")
            item.setdefault(key, False)

        if not isinstance(item["actions"], list) or len(item["actions"]) > MAX_ACTIONS:
            raise vol.Invalid(
                f"Routine {rid}: actions must be a list with at most {MAX_ACTIONS} items."
            )

        for ai, action in enumerate(item["actions"]):
            if not isinstance(action, dict):
                raise vol.Invalid(f"Routine {rid}, action {ai + 1}: must be an object.")

            # Delay action: {"delay": seconds} or {"delay": "HH:MM:SS"}
            if "delay" in action:
                delay = action["delay"]
                if isinstance(delay, (int, float)) and delay >= 0:
                    continue
                if isinstance(delay, str):
                    try:
                        parts = [int(p) for p in delay.split(":")]
                        if len(parts) in (2, 3):
                            continue
                    except ValueError:
                        pass
                raise vol.Invalid(
                    f"Routine {rid}, action {ai + 1}: delay must be seconds "
                    f"(number) or HH:MM / HH:MM:SS string."
                )

            # Normal service action
            if not isinstance(action.get("service"), str) or not SERVICE_RE.fullmatch(
                action["service"]
            ):
                raise vol.Invalid(
                    f"Routine {rid}, action {ai + 1}: service must look like domain.service."
                )
            if "target" in action and not isinstance(action["target"], dict):
                raise vol.Invalid(f"Routine {rid}, action {ai + 1}: target must be an object.")
            if "data" in action and not isinstance(action["data"], dict):
                raise vol.Invalid(f"Routine {rid}, action {ai + 1}: data must be an object.")

        validated.append(item)

    return validated


def _parse_time(value: str) -> dt_time:
    """Parse HH:MM or HH:MM:SS into a time object."""
    if len(value) == 8:
        return datetime.strptime(value, "%H:%M:%S").time()
    return datetime.strptime(value, "%H:%M").time()


def _weekday(now: datetime) -> str:
    return WEEKDAYS[now.weekday()]


def _parse_delay(delay: Any) -> float:
    """Return delay in seconds."""
    if isinstance(delay, (int, float)):
        return float(delay)
    parts = [int(p) for p in str(delay).split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

# Handle kinds (one unsubscribe callable per routine and kind).
_H_TIME = "time"
_H_PRESENCE = "presence"
_H_WATCH = "watch"
_H_CLEAR = "clear"
_H_EXPIRE = "expire"

# Runtime keys written to storage. Clear timers and waiting state are
# deliberately NOT persisted: after a restart they are rebuilt from live state.
_PERSISTED_KEYS = ("last_run_date", "last_run", "last_result")

_SCHEDULED_TRIGGERS = (TRIGGER_TIME, TRIGGER_SUNRISE, TRIGGER_SUNSET)
_PRESENCE_TRIGGERS = (TRIGGER_EVERYONE_LEAVES, TRIGGER_SOMEONE_ARRIVES)


def _blank_runtime() -> dict[str, Any]:
    return {
        "state": "idle",
        "last_run": None,
        "last_result": None,
        "last_run_date": None,
        "next_run": None,
        "clear_since": None,  # in memory: when all sensors last became clear
        "pending_date": None,  # local date while waiting for conditions
        "all_away": None,  # last known "everyone away" for presence triggers
    }


def _routine_uses_conditions(routine: dict[str, Any]) -> bool:
    return bool(routine["require_everyone_away"] or routine["require_sensors_clear"])


class HomeRoutinesCoordinator:
    """Owns runtime state, scheduling and execution of all routines."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.routines: list[dict[str, Any]] = []
        self._runtime: dict[str, dict[str, Any]] = {}
        self._listeners: list[Callable[[], None]] = []
        self._handles: dict[tuple[str, str], Callable[[], None]] = {}
        self._store: storage.Store[dict[str, Any]] = storage.Store(
            hass, STORAGE_VERSION, STORAGE_KEY
        )

    # ----- small helpers -----

    def _rt(self, routine_id: str) -> dict[str, Any]:
        return self._runtime.setdefault(routine_id, _blank_runtime())

    def _find(self, routine_id: str) -> dict[str, Any] | None:
        return next((r for r in self.routines if r["id"] == routine_id), None)

    def _set_handle(self, routine_id: str, kind: str, unsub: Callable[[], None]) -> None:
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

    def _all_away(self, people: list[str]) -> bool:
        return all(self._state_is(pid, "not_home") for pid in people)

    def _allowed_day(self, routine: dict[str, Any], when: datetime) -> bool:
        days = routine.get("days") or list(WEEKDAYS)
        return WEEKDAYS[when.weekday()] in days

    def _fire_event(
        self, event_type: str, routine: dict[str, Any], result: str | None = None
    ) -> None:
        data: dict[str, Any] = {"routine_id": routine["id"], "name": routine["name"]}
        if result is not None:
            data["result"] = result
        self.hass.bus.async_fire(event_type, data)

    def _send_notification(self, message: str) -> None:
        self.hass.async_create_task(self._notify(message))

    async def _notify(self, message: str) -> None:
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "message": message,
                    "title": "Daily Routine",
                    "notification_id": f"{DOMAIN}_{dt_util.now().timestamp()}",
                },
                blocking=False,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not create persistent notification")

    # ----- public helpers used by sensors and the panel -----

    def get_status(self, routine_id: str) -> dict[str, Any] | None:
        routine = self._find(routine_id)
        if routine is None:
            return None
        rt = self._rt(routine_id)
        ok, reason = self._check(routine, dt_util.now())
        return {
            "name": routine["name"],
            "enabled": routine["enabled"],
            "state": rt["state"],
            "last_run": rt["last_run"],
            "last_result": rt["last_result"],
            "next_run": rt["next_run"],
            "clear_since": rt["clear_since"].isoformat() if rt["clear_since"] else None,
            "conditions_ok": ok,
            "block_reason": reason,
            "waiting": rt["pending_date"] is not None,
            "days": routine["days"],
            "time": routine["time"],
            "trigger": routine.get("trigger", TRIGGER_TIME),
            "offset_minutes": routine.get("offset_minutes", 0),
        }

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _notify_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    # ----- lifecycle -----

    async def async_setup(self) -> None:
        """Load persisted state, validate routines, start schedulers."""
        data = await self._store.async_load() or {}
        for rid, saved in (data.get("runtime") or {}).items():
            self._rt(rid).update({k: saved.get(k) for k in _PERSISTED_KEYS})
        self._reload_routines()
        self._start_all()
        _LOGGER.info("Daily Routine ready – %d routine(s) loaded.", len(self.routines))

    async def async_unload(self) -> None:
        self._drop_all_handles()
        await self._async_save()

    def _reload_routines(self) -> None:
        """Load every config entry as one routine (plus legacy JSON if present)."""
        routines: list[dict[str, Any]] = []
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if CONF_ROUTINES_JSON in entry.options and not entry.data.get("routine_id"):
                try:
                    for item in _validate_routines(str(entry.options[CONF_ROUTINES_JSON])):
                        item.setdefault("entry_id", entry.entry_id)
                        routines.append(item)
                except vol.Invalid as err:
                    _LOGGER.error("Legacy routines JSON invalid: %s", err)
                continue
            routines.append(
                options_to_routine(
                    entry.entry_id, entry.title, dict(entry.options), dict(entry.data)
                )
            )

        self.routines = routines
        keep = {r["id"] for r in routines}
        for rid in list(self._runtime):
            if rid not in keep:
                del self._runtime[rid]
        for rid in keep:
            self._rt(rid)
        self._notify_listeners()

    def _start_all(self) -> None:
        now = dt_util.now()
        for routine in self.routines:
            self._schedule_one(routine, now)
            self._watch_presence(routine)
            self._watch_conditions(routine)

    async def async_reload(self) -> None:
        """Called when options change or the reload service is used."""
        self._drop_all_handles()
        for rt in self._runtime.values():
            rt["pending_date"] = None
            if rt["state"] == "waiting":
                rt["state"] = "idle"
        self._reload_routines()
        self._start_all()
        await self._async_save()
        _LOGGER.info("Daily Routine reloaded – %d routine(s).", len(self.routines))

    async def _async_save(self) -> None:
        to_store = {
            rid: {k: rt.get(k) for k in _PERSISTED_KEYS} for rid, rt in self._runtime.items()
        }
        await self._store.async_save({"runtime": to_store})

    # ----- condition check (pure: no side effects) -----

    def _check(self, routine: dict[str, Any], now: datetime) -> tuple[bool, str | None]:
        """Return (passes, reason-if-blocked). Unknown/unavailable fails closed."""
        if routine["require_everyone_away"] and not self._all_away(routine["people"]):
            return False, "someone_home"

        if routine["require_sensors_clear"]:
            if not all(self._state_is(eid, "off") for eid in routine["sensors"]):
                return False, "sensors_active"
            minutes = routine["clear_minutes"]
            if minutes > 0:
                since = self._rt(routine["id"])["clear_since"]
                if since is None:
                    return False, "clear_timer_not_started"
                if (now - since).total_seconds() < minutes * 60:
                    return False, "clear_timer_running"
        return True, None

    # ----- watching sensors and people -----

    def _watch_conditions(self, routine: dict[str, Any]) -> None:
        """Track sensor/people state so waiting routines and clear timers stay live."""
        rid = routine["id"]
        self._drop_handle(rid, _H_WATCH)
        self._drop_handle(rid, _H_CLEAR)
        if not _routine_uses_conditions(routine):
            return

        rt = self._rt(rid)
        if routine["require_sensors_clear"]:
            # Start the clear timer from the moment HA first observes the sensors clear.
            all_off = all(self._state_is(eid, "off") for eid in routine["sensors"])
            if not all_off:
                rt["clear_since"] = None
            elif rt["clear_since"] is None:
                rt["clear_since"] = dt_util.now()

        entities: set[str] = set()
        if routine["require_everyone_away"]:
            entities.update(routine["people"])
        if routine["require_sensors_clear"]:
            entities.update(routine["sensors"])

        @callback
        def _changed(event: Event[EventStateChangedData]) -> None:
            self._on_condition_entity_changed(routine, event.data.get("entity_id"))

        unsub = async_track_state_change_event(self.hass, sorted(entities), _changed)
        self._set_handle(rid, _H_WATCH, unsub)
        self._arm_clear_timer(routine)

    def _on_condition_entity_changed(self, routine: dict[str, Any], entity_id: str | None) -> None:
        rid = routine["id"]
        if routine["require_sensors_clear"] and entity_id in routine["sensors"]:
            rt = self._rt(rid)
            if all(self._state_is(eid, "off") for eid in routine["sensors"]):
                if rt["clear_since"] is None:
                    rt["clear_since"] = dt_util.now()
            else:
                # Any motion restarts the clear period from zero.
                rt["clear_since"] = None
                self._drop_handle(rid, _H_CLEAR)
            self._arm_clear_timer(routine)
        self._evaluate_pending(routine)
        self._notify_listeners()

    def _arm_clear_timer(self, routine: dict[str, Any]) -> None:
        """Wake up when the clear period finishes so a waiting routine can run."""
        rid = routine["id"]
        rt = self._rt(rid)
        self._drop_handle(rid, _H_CLEAR)
        if not routine["require_sensors_clear"] or routine["clear_minutes"] <= 0:
            return
        if rt["clear_since"] is None or not routine["enabled"]:
            return
        end = rt["clear_since"] + timedelta(minutes=routine["clear_minutes"])
        if end <= dt_util.now():
            return

        @callback
        def _fire(_fired: datetime) -> None:
            self._drop_handle(rid, _H_CLEAR)
            self._evaluate_pending(routine)
            self._notify_listeners()

        self._set_handle(rid, _H_CLEAR, async_track_point_in_time(self.hass, _fire, end))

    # ----- waiting (blocked scheduled / presence runs) -----

    def _arm_pending(self, routine: dict[str, Any], now: datetime) -> None:
        rid = routine["id"]
        rt = self._rt(rid)
        rt["pending_date"] = now.date().isoformat()
        rt["state"] = "waiting"
        midnight = dt_util.start_of_local_day(now) + timedelta(days=1)

        @callback
        def _expire(_fired: datetime) -> None:
            self._expire_pending(routine)

        self._set_handle(rid, _H_EXPIRE, async_track_point_in_time(self.hass, _expire, midnight))
        _LOGGER.debug("Routine '%s' is waiting for conditions until %s", routine["name"], midnight)

    def _clear_pending(self, routine: dict[str, Any]) -> None:
        rid = routine["id"]
        rt = self._rt(rid)
        rt["pending_date"] = None
        self._drop_handle(rid, _H_EXPIRE)
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
        _LOGGER.info("Routine '%s' skipped: conditions not met before end of day.", routine["name"])
        self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "conditions_not_met")
        if routine.get("notify_on_skip"):
            self._send_notification(
                f"Routine '{routine['name']}' skipped – conditions were not met today."
            )
        self._notify_listeners()

    def _evaluate_pending(self, routine: dict[str, Any]) -> None:
        rid = routine["id"]
        rt = self._rt(rid)
        if rt["pending_date"] is None:
            return
        now = dt_util.now()
        if rt["pending_date"] != now.date().isoformat():
            self._expire_pending(routine)
            return
        if not routine["enabled"]:
            self._clear_pending(routine)
            return
        ok, _reason = self._check(routine, now)
        if ok:
            self._clear_pending(routine)
            self.hass.async_create_task(self._execute_routine(routine, now, scheduled=True))

    # ----- time / sun triggers -----

    def _next_run_datetime(self, routine: dict[str, Any], after: datetime) -> datetime | None:
        """Next fire time for time / sunrise / sunset triggers."""
        if not routine.get("enabled"):
            return None
        trigger = routine.get("trigger") or TRIGGER_TIME
        if trigger not in _SCHEDULED_TRIGGERS:
            return None
        offset = timedelta(minutes=int(routine.get("offset_minutes") or 0))

        if trigger == TRIGGER_TIME:
            if not routine.get("days"):
                return None
            target = _parse_time(routine["time"])
            for day_offset in range(0, 8):
                candidate_date = (after + timedelta(days=day_offset)).date()
                candidate = datetime.combine(candidate_date, target, tzinfo=after.tzinfo)
                if candidate <= after:
                    continue
                if self._allowed_day(routine, candidate):
                    return candidate
            return None

        event = SUN_EVENT_SUNRISE if trigger == TRIGGER_SUNRISE else SUN_EVENT_SUNSET
        cursor = after
        for _ in range(10):
            try:
                event_dt = get_astral_event_next(self.hass, event, cursor)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Sun event lookup failed: %s", err)
                return None
            if event_dt is None:
                return None
            candidate = dt_util.as_local(event_dt) + offset
            if candidate > after and self._allowed_day(routine, candidate):
                return candidate
            cursor = event_dt + timedelta(minutes=1)
        return None

    def _schedule_one(self, routine: dict[str, Any], after: datetime) -> None:
        rid = routine["id"]
        rt = self._rt(rid)
        self._drop_handle(rid, _H_TIME)
        next_dt = self._next_run_datetime(routine, after)
        rt["next_run"] = next_dt.isoformat() if next_dt else None
        if next_dt is None:
            return

        @callback
        def _fire(fired: datetime) -> None:
            # Schedule the next occurrence first, then run this one.
            self._schedule_one(routine, fired + timedelta(seconds=2))
            self.hass.async_create_task(self._execute_routine(routine, fired, scheduled=True))

        self._set_handle(rid, _H_TIME, async_track_point_in_time(self.hass, _fire, next_dt))
        _LOGGER.debug("Scheduled routine '%s' for %s", routine["name"], next_dt.isoformat())

    # ----- presence triggers -----

    def _watch_presence(self, routine: dict[str, Any]) -> None:
        rid = routine["id"]
        trigger = routine.get("trigger") or TRIGGER_TIME
        self._drop_handle(rid, _H_PRESENCE)
        if trigger not in _PRESENCE_TRIGGERS:
            return
        people = list(routine.get("people") or [])
        if not people:
            _LOGGER.warning(
                "Routine '%s' uses a presence trigger but has no people selected",
                routine["name"],
            )
            return
        rt = self._rt(rid)
        rt["all_away"] = self._all_away(people)

        @callback
        def _on_change(_event: Event[EventStateChangedData]) -> None:
            if not routine.get("enabled"):
                return
            now = dt_util.now()
            was_away = bool(rt["all_away"])
            is_away = self._all_away(people)
            rt["all_away"] = is_away
            if not self._allowed_day(routine, now):
                return
            if trigger == TRIGGER_EVERYONE_LEAVES and is_away and not was_away:
                fire = True
            elif trigger == TRIGGER_SOMEONE_ARRIVES and was_away and not is_away:
                fire = True
            else:
                fire = False
            if fire:
                self.hass.async_create_task(self._execute_routine(routine, now, scheduled=True))

        unsub = async_track_state_change_event(self.hass, people, _on_change)
        self._set_handle(rid, _H_PRESENCE, unsub)
        _LOGGER.debug(
            "Presence trigger for '%s' watching %s (%s)", routine["name"], people, trigger
        )

    # ----- execution -----

    async def _execute_routine(
        self,
        routine: dict[str, Any],
        now: datetime,
        *,
        scheduled: bool,
    ) -> bool:
        """Run actions only when all configured conditions pass."""
        rid = routine["id"]
        rt = self._rt(rid)

        if not routine["enabled"]:
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "disabled")
            return False

        if scheduled and rt["last_run_date"] == now.date().isoformat():
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "already_ran_today")
            return False

        ok, reason = self._check(routine, now)
        if not ok:
            if scheduled and _routine_uses_conditions(routine):
                # Wait for the conditions instead of giving up for the day.
                if rt["pending_date"] is None:
                    self._arm_pending(routine, now)
                    self._fire_event(EVENT_ROUTINE_SKIPPED, routine, f"waiting:{reason}")
                self._notify_listeners()
                return False
            rt["last_result"] = reason
            rt["state"] = "skipped"
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, reason)
            if scheduled and routine.get("notify_on_skip"):
                self._send_notification(
                    f"Routine '{routine['name']}' skipped – conditions not met."
                )
            self._notify_listeners()
            return False

        if scheduled:
            rt["last_run_date"] = now.date().isoformat()
            self._clear_pending(routine)

        rt["state"] = "running"
        rt["last_run"] = now.isoformat()
        self._notify_listeners()
        self._fire_event(EVENT_ROUTINE_STARTED, routine)

        success = True
        for action in routine["actions"]:
            ok, reason = self._check(routine, dt_util.now())
            if not ok:
                _LOGGER.warning(
                    "Routine '%s' stopped because its conditions no longer pass (%s).",
                    routine["name"],
                    reason,
                )
                rt["last_result"] = f"conditions_failed_mid_run:{reason}"
                success = False
                break

            if "delay" in action:
                seconds = _parse_delay(action["delay"])
                await asyncio.sleep(seconds)
                continue

            domain, service = action["service"].split(".", 1)
            if not self.hass.services.has_service(domain, service):
                _LOGGER.error(
                    "Routine '%s': service %s does not exist; stopping.",
                    routine["name"],
                    action["service"],
                )
                rt["last_result"] = f"missing_service:{action['service']}"
                success = False
                break

            service_data = dict(action.get("data") or {})
            try:
                await self.hass.services.async_call(
                    domain,
                    service,
                    service_data,
                    target=action.get("target"),
                    blocking=True,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Routine '%s' action %s failed; stopping remaining actions.",
                    routine["name"],
                    action["service"],
                )
                rt["last_result"] = f"error:{action['service']}"
                success = False
                break

        if success:
            rt["last_result"] = "success"
            rt["state"] = "idle"
            _LOGGER.info("Routine '%s' completed successfully.", routine["name"])
            self._fire_event(EVENT_ROUTINE_FINISHED, routine, "success")
            if routine.get("notify_on_run"):
                self._send_notification(f"Routine '{routine['name']}' completed.")
        else:
            rt["state"] = "error"
            self._fire_event(EVENT_ROUTINE_FINISHED, routine, rt["last_result"])

        await self._async_save()
        self._notify_listeners()
        return success

    async def async_run_routine(self, call: ServiceCall) -> None:
        routine = self._find(call.data["routine_id"])
        if routine is None:
            _LOGGER.error("No configured routine with id '%s'.", call.data["routine_id"])
            return
        # Manual runs never bypass conditions and ignore the once-per-day guard.
        await self._execute_routine(routine, dt_util.now(), scheduled=False)


# ---------------------------------------------------------------------------
# Setup / unload
# ---------------------------------------------------------------------------


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up from YAML is not used; config entries only."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one routine config entry (shared coordinator)."""
    domain_data = hass.data.setdefault(DOMAIN, {})

    if "coordinator" not in domain_data:
        coordinator = HomeRoutinesCoordinator(hass)
        domain_data["coordinator"] = coordinator
        await coordinator.async_setup()

        async def run_routine_service(call: ServiceCall) -> None:
            await coordinator.async_run_routine(call)

        async def reload_service(call: ServiceCall) -> None:
            await coordinator.async_reload()

        hass.services.async_register(
            DOMAIN,
            "run_routine",
            run_routine_service,
            schema=vol.Schema({vol.Required("routine_id"): str}),
        )
        hass.services.async_register(DOMAIN, "reload_routines", reload_service)

        from .panel import async_register_panel

        await async_register_panel(hass)
    else:
        coordinator = domain_data["coordinator"]
        await coordinator.async_reload()

    domain_data[entry.entry_id] = {"coordinator": coordinator}
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def options_updated(hass: HomeAssistant, updated_entry: ConfigEntry) -> None:
        await hass.config_entries.async_reload(updated_entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one routine entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    domain_data = hass.data.get(DOMAIN, {})
    domain_data.pop(entry.entry_id, None)

    remaining = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id
    ]
    coordinator = domain_data.get("coordinator")
    if coordinator and remaining:
        await coordinator.async_reload()
    elif coordinator and not remaining:
        await coordinator.async_unload()
        domain_data.pop("coordinator", None)
        for service in ("run_routine", "reload_routines"):
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)
        from .panel import async_unregister_panel

        await async_unregister_panel(hass)

    return unload_ok
