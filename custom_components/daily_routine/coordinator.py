"""Runtime: schedules, safety checks, execution and persistence."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from datetime import time as dt_time
from typing import Any

from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HomeAssistant,
    ServiceCall,
    State,
    callback,
)
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import storage
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_time, async_track_state_change_event
from homeassistant.helpers.sun import get_astral_event_next
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    EVENT_ROUTINE_FINISHED,
    EVENT_ROUTINE_SKIPPED,
    EVENT_ROUTINE_STARTED,
    MAX_ROUTINES,
    RETRY_DELAY_SECONDS,
    SIGNAL_ROUTINES_CHANGED,
    STORAGE_KEY,
    STORAGE_VERSION,
    TRIGGER_EVERYONE_LEAVES,
    TRIGGER_ROOM_CLEAR,
    TRIGGER_SOMEONE_ARRIVES,
    TRIGGER_SUNRISE,
    TRIGGER_SUNSET,
    TRIGGER_TIME,
    WEEKDAYS,
)
from .routines import (
    DESIRED_STATE,
    PRESENCE_TRIGGERS,
    normalize_routine,
    uses_conditions,
)
from .schema import vol

_LOGGER = logging.getLogger(__name__)

H_TIME = "time"
H_PRESENCE = "presence"
H_WATCH = "watch"
H_CLEAR = "clear"
H_EXPIRE = "expire"

# Runtime keys kept in storage. Clear timers and waiting state are not persisted.
PERSISTED_KEYS = ("last_run_date", "last_run", "last_result")


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


def person_is_tracked(state: State) -> bool:
    """A person can be checked only when Home Assistant has a location for them (a device tracker)."""
    return bool(state.attributes.get("source") or state.attributes.get("device_trackers"))


def list_people(hass: HomeAssistant) -> list[dict[str, Any]]:
    """All person profiles, flagged by whether a routine can check their location."""
    return [
        {
            "entity_id": s.entity_id,
            "name": s.attributes.get("friendly_name") or s.entity_id,
            "state": s.state,
            "tracked": person_is_tracked(s),
        }
        for s in sorted(hass.states.async_all("person"), key=lambda s: s.entity_id)
    ]


def list_areas(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Every area in Home Assistant, with the entities in it. Nothing is set up by hand."""
    areas = []
    for area in sorted(ar.async_get(hass).async_list_areas(), key=lambda a: a.name.lower()):
        entities = []
        for entity_id in area_entity_ids(hass, area.id):
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
        areas.append({"id": area.id, "name": area.name, "icon": area.icon, "entities": entities})
    return areas


def _blank_runtime() -> dict[str, Any]:
    return {
        "state": "idle",
        "last_run": None,
        "last_result": None,
        "last_run_date": None,
        "next_run": None,
        "clear_since": None,  # in memory: when all sensors last became clear
        "armed": False,  # room_clear: a real off-transition happened since start
        "pending_date": None,  # local date while waiting for conditions
        "all_away": None,  # last known "everyone away" for presence triggers
    }


class HomeRoutinesCoordinator:
    """Owns routines, runtime state, scheduling and execution."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.routines: list[dict[str, Any]] = []
        self.paused = False
        self.migrated = False
        self._runtime: dict[str, dict[str, Any]] = {}
        self._listeners: list[Callable[[], None]] = []
        self._handles: dict[tuple[str, str], Callable[[], None]] = {}
        self._store: storage.Store[dict[str, Any]] = storage.Store(
            hass, STORAGE_VERSION, STORAGE_KEY
        )

    # ----- helpers -----

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
        return WEEKDAYS[when.weekday()] in routine["days"]

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

    def _notify_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    # ----- status for sensors and the panel -----

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
            "next_run": rt["next_run"],
            "clear_since": rt["clear_since"].isoformat() if rt["clear_since"] else None,
            "conditions_ok": ok,
            "block_reason": reason,
            "waiting": rt["pending_date"] is not None,
            "trigger": routine["trigger"],
            "days": routine["days"],
            "time": routine["time"],
            "repeat": routine["repeat"],
        }

    # ----- lifecycle -----

    async def async_setup(self) -> None:
        """Load stored routines and settings. Scheduling starts in async_start."""
        data = await self._store.async_load() or {}
        self.paused = bool(data.get("paused", False))
        self.migrated = bool(data.get("migrated", False))
        for raw in data.get("routines", []):
            try:
                self.routines.append(normalize_routine(raw))
            except vol.Invalid as err:
                _LOGGER.error("Skipping stored routine %s: %s", raw.get("id"), err)
        for rid, saved in (data.get("runtime") or {}).items():
            self._rt(rid).update({k: saved.get(k) for k in PERSISTED_KEYS})

    async def async_start(self) -> None:
        self._start_all(catch_up=True)
        self._notify_listeners()

    async def async_unload(self) -> None:
        self._drop_all_handles()
        await self._async_save()

    async def _async_save(self) -> None:
        runtime = {rid: {k: rt.get(k) for k in PERSISTED_KEYS} for rid, rt in self._runtime.items()}
        await self._store.async_save(
            {
                "routines": self.routines,
                "paused": self.paused,
                "migrated": self.migrated,
                "runtime": runtime,
            }
        )

    async def async_reload(self) -> None:
        """Restart every schedule and watcher from the current routine list."""
        self._drop_all_handles()
        for rt in self._runtime.values():
            rt["pending_date"] = None
            if rt["state"] == "waiting":
                rt["state"] = "idle"
        self._start_all(catch_up=False)
        await self._async_save()
        self._notify_listeners()
        async_dispatcher_send(self.hass, SIGNAL_ROUTINES_CHANGED)

    # ----- editing (used by the panel, services and migration) -----

    async def async_save_routine(self, raw: Any) -> dict[str, Any]:
        routine = normalize_routine(raw)
        untracked = []
        for person_id in routine["people"]:
            state = self.hass.states.get(person_id)
            if state is None:
                untracked.append(person_id)
            elif not person_is_tracked(state):
                untracked.append(str(state.attributes.get("friendly_name") or person_id))
        if untracked:
            names = ", ".join(untracked)
            raise vol.Invalid(
                f"{names} has no location tracking, so a routine can't check where they are. "
                "Give them a device tracker or leave them out."
            )
        index = next((i for i, r in enumerate(self.routines) if r["id"] == routine["id"]), None)
        if index is None:
            if len(self.routines) >= MAX_ROUTINES:
                raise vol.Invalid(f"You can have at most {MAX_ROUTINES} routines.")
            self.routines.append(routine)
        else:
            self.routines[index] = routine
        await self.async_reload()
        return routine

    async def async_delete_routine(self, routine_id: str) -> None:
        self.routines = [r for r in self.routines if r["id"] != routine_id]
        self._runtime.pop(routine_id, None)
        await self.async_reload()

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
        if routine["require_everyone_away"] and not self._all_away(routine["people"]):
            return False, "someone_home"

        for item in routine["require_states"]:
            if not self._state_is(item["entity_id"], item["state"]):
                return False, "extra_condition_not_met"

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
        rid = routine["id"]
        self._drop_handle(rid, H_WATCH)
        self._drop_handle(rid, H_CLEAR)
        if not uses_conditions(routine):
            return

        rt = self._rt(rid)
        if routine["require_sensors_clear"]:
            all_off = all(self._state_is(eid, "off") for eid in routine["sensors"])
            if not all_off:
                rt["clear_since"] = None
            elif rt["clear_since"] is None:
                # Started from what HA can see now. For room_clear, don't act on this
                # until a real off-transition happens (no lights-off just because of a restart).
                rt["clear_since"] = dt_util.now()
                rt["armed"] = False

        entities: set[str] = set()
        if routine["require_everyone_away"]:
            entities.update(routine["people"])
        if routine["require_sensors_clear"]:
            entities.update(routine["sensors"])
        entities.update(item["entity_id"] for item in routine["require_states"])

        @callback
        def _changed(event: Event[EventStateChangedData]) -> None:
            self._on_condition_entity_changed(routine, event.data.get("entity_id"))

        unsub = async_track_state_change_event(self.hass, sorted(entities), _changed)
        self._set_handle(rid, H_WATCH, unsub)
        self._arm_clear_timer(routine)

    def _on_condition_entity_changed(self, routine: dict[str, Any], entity_id: str | None) -> None:
        rid = routine["id"]
        if routine["require_sensors_clear"] and entity_id in routine["sensors"]:
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
        self._evaluate_pending(routine)
        self._notify_listeners()

    def _arm_clear_timer(self, routine: dict[str, Any]) -> None:
        """Wake up when the quiet period finishes."""
        rid = routine["id"]
        rt = self._rt(rid)
        self._drop_handle(rid, H_CLEAR)
        if not routine["require_sensors_clear"] or routine["clear_minutes"] <= 0:
            return
        if rt["clear_since"] is None or not routine["enabled"]:
            return
        if routine["trigger"] == TRIGGER_ROOM_CLEAR and not rt["armed"]:
            return
        end = rt["clear_since"] + timedelta(minutes=routine["clear_minutes"])

        @callback
        def _fire(_fired: datetime) -> None:
            self._drop_handle(rid, H_CLEAR)
            self._on_clear_elapsed(routine)
            self._notify_listeners()

        self._set_handle(rid, H_CLEAR, async_track_point_in_time(self.hass, _fire, end))

    def _on_clear_elapsed(self, routine: dict[str, Any]) -> None:
        if routine["trigger"] == TRIGGER_ROOM_CLEAR:
            rt = self._rt(routine["id"])
            if rt["armed"]:
                rt["armed"] = False  # once per quiet period
                self.hass.async_create_task(
                    self._execute_routine(routine, dt_util.now(), scheduled=True)
                )
            return
        self._evaluate_pending(routine)

    # ----- waiting (scheduled or presence runs blocked by conditions) -----

    def _arm_pending(self, routine: dict[str, Any], now: datetime) -> None:
        rid = routine["id"]
        rt = self._rt(rid)
        rt["pending_date"] = now.date().isoformat()
        rt["state"] = "waiting"
        midnight = dt_util.start_of_local_day(now) + timedelta(days=1)

        @callback
        def _expire(_fired: datetime) -> None:
            self._expire_pending(routine)

        self._set_handle(rid, H_EXPIRE, async_track_point_in_time(self.hass, _expire, midnight))

    def _clear_pending(self, routine: dict[str, Any]) -> None:
        rid = routine["id"]
        rt = self._rt(rid)
        rt["pending_date"] = None
        self._drop_handle(rid, H_EXPIRE)
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
        if routine["notify_on_skip"]:
            self._send_notification(
                f"Routine '{routine['name']}' skipped – conditions were not met today."
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
        ok, _reason = self._check(routine, now)
        if ok:
            self._clear_pending(routine)
            self.hass.async_create_task(self._execute_routine(routine, now, scheduled=True))

    # ----- time and sun triggers -----

    def _next_run_datetime(self, routine: dict[str, Any], after: datetime) -> datetime | None:
        if not routine["enabled"]:
            return None
        trigger = routine["trigger"]
        if trigger not in (TRIGGER_TIME, TRIGGER_SUNRISE, TRIGGER_SUNSET):
            return None
        offset = timedelta(minutes=routine["offset_minutes"])

        if trigger == TRIGGER_TIME:
            target = _parse_time(routine["time"])
            for day_offset in range(8):
                candidate_date = (after + timedelta(days=day_offset)).date()
                candidate = datetime.combine(candidate_date, target, tzinfo=after.tzinfo)
                if candidate > after and self._allowed_day(routine, candidate):
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
        self._drop_handle(rid, H_TIME)
        next_dt = self._next_run_datetime(routine, after)
        rt["next_run"] = next_dt.isoformat() if next_dt else None
        if next_dt is None:
            return

        @callback
        def _fire(fired: datetime) -> None:
            self._schedule_one(routine, fired + timedelta(seconds=2))
            self.hass.async_create_task(self._execute_routine(routine, fired, scheduled=True))

        self._set_handle(rid, H_TIME, async_track_point_in_time(self.hass, _fire, next_dt))

    def _maybe_catch_up(self, routine: dict[str, Any], now: datetime) -> None:
        """After a restart, run a time routine that was missed by a few minutes."""
        if routine["trigger"] != TRIGGER_TIME or routine["catch_up_minutes"] <= 0:
            return
        if not routine["enabled"] or not self._allowed_day(routine, now):
            return
        target = datetime.combine(now.date(), _parse_time(routine["time"]), tzinfo=now.tzinfo)
        window_end = target + timedelta(minutes=routine["catch_up_minutes"])
        if (
            target <= now < window_end
            and self._rt(routine["id"])["last_run_date"] != now.date().isoformat()
        ):
            _LOGGER.info("Catching up missed routine '%s'", routine["name"])
            self.hass.async_create_task(self._execute_routine(routine, now, scheduled=True))

    # ----- presence triggers -----

    def _watch_presence(self, routine: dict[str, Any]) -> None:
        rid = routine["id"]
        trigger = routine["trigger"]
        self._drop_handle(rid, H_PRESENCE)
        if trigger not in PRESENCE_TRIGGERS:
            return
        people = list(routine["people"])
        rt = self._rt(rid)
        rt["all_away"] = self._all_away(people)

        @callback
        def _on_change(_event: Event[EventStateChangedData]) -> None:
            if not routine["enabled"]:
                return
            now = dt_util.now()
            was_away = bool(rt["all_away"])
            is_away = self._all_away(people)
            rt["all_away"] = is_away
            if not self._allowed_day(routine, now):
                return
            leaves = trigger == TRIGGER_EVERYONE_LEAVES and is_away and not was_away
            arrives = trigger == TRIGGER_SOMEONE_ARRIVES and was_away and not is_away
            if leaves or arrives:
                self.hass.async_create_task(self._execute_routine(routine, now, scheduled=True))

        unsub = async_track_state_change_event(self.hass, people, _on_change)
        self._set_handle(rid, H_PRESENCE, unsub)

    # ----- starting everything -----

    def _start_all(self, *, catch_up: bool) -> None:
        now = dt_util.now()
        ids = {r["id"] for r in self.routines}
        for rid in list(self._runtime):
            if rid not in ids:
                del self._runtime[rid]
        for routine in self.routines:
            self._rt(routine["id"])
            self._schedule_one(routine, now)
            self._watch_presence(routine)
            self._watch_conditions(routine)
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
        remaining = [e for e in entities if not self._state_is(e, wanted)]
        if not remaining:
            return "already_in_state", None, entities
        if len(remaining) != len(entities):
            return "partly_in_state", {"entity_id": remaining}, remaining
        return "would_run", None, entities

    async def _call_with_retries(
        self,
        routine: dict[str, Any],
        domain: str,
        service: str,
        data: dict[str, Any],
        target: dict[str, Any] | None,
    ) -> None:
        attempts = 1 + routine["retries"]
        for attempt in range(1, attempts + 1):
            try:
                await self.hass.services.async_call(
                    domain, service, data, blocking=True, target=target or None
                )
                return
            except Exception:  # noqa: BLE001
                if attempt == attempts:
                    raise
                _LOGGER.warning(
                    "Routine '%s': %s.%s failed (attempt %d of %d), retrying.",
                    routine["name"],
                    domain,
                    service,
                    attempt,
                    attempts,
                )
                await asyncio.sleep(RETRY_DELAY_SECONDS)

    async def _execute_routine(
        self, routine: dict[str, Any], now: datetime, *, scheduled: bool
    ) -> bool:
        rid = routine["id"]
        rt = self._rt(rid)
        today = now.date().isoformat()

        if not routine["enabled"]:
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "disabled")
            return False
        if scheduled and self.paused:
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "paused")
            return False
        if scheduled and not routine["repeat"] and rt["last_run_date"] == today:
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, "already_ran_today")
            return False

        ok, reason = self._check(routine, now)
        if not ok:
            if scheduled and uses_conditions(routine):
                if rt["pending_date"] is None:
                    self._arm_pending(routine, now)
                    self._fire_event(EVENT_ROUTINE_SKIPPED, routine, f"waiting:{reason}")
                self._notify_listeners()
                return False
            rt["last_result"] = reason
            rt["state"] = "skipped"
            self._fire_event(EVENT_ROUTINE_SKIPPED, routine, reason)
            if scheduled and routine["notify_on_skip"]:
                self._send_notification(
                    f"Routine '{routine['name']}' skipped – conditions not met."
                )
            self._notify_listeners()
            return False

        if scheduled:
            rt["last_run_date"] = today
            self._clear_pending(routine)

        rt["state"] = "running"
        rt["last_run"] = now.isoformat()
        self._notify_listeners()
        self._fire_event(EVENT_ROUTINE_STARTED, routine)

        result = "success"
        for action in routine["actions"]:
            ok, reason = self._check(routine, dt_util.now())
            if not ok:
                _LOGGER.warning(
                    "Routine '%s' stopped: conditions no longer pass (%s).", routine["name"], reason
                )
                result = f"conditions_failed_mid_run:{reason}"
                break

            if "delay" in action:
                await asyncio.sleep(action["delay"])
                continue

            outcome, override, _entities = self._plan_action(routine, action)
            if outcome == "already_in_state":
                _LOGGER.debug(
                    "Routine '%s': %s already done, skipped", routine["name"], action["service"]
                )
                continue

            domain, service = action["service"].split(".", 1)
            if not self.hass.services.has_service(domain, service):
                _LOGGER.error(
                    "Routine '%s': service %s does not exist.", routine["name"], action["service"]
                )
                result = f"missing_service:{action['service']}"
                break
            target = override if override is not None else action.get("target")
            try:
                await self._call_with_retries(
                    routine, domain, service, dict(action.get("data") or {}), target
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Routine '%s': %s failed.", routine["name"], action["service"])
                result = f"error:{action['service']}"
                break

        rt["last_result"] = result
        if result == "success":
            rt["state"] = "idle"
            _LOGGER.info("Routine '%s' completed.", routine["name"])
            self._fire_event(EVENT_ROUTINE_FINISHED, routine, "success")
            if routine["notify_on_run"]:
                self._send_notification(f"Routine '{routine['name']}' completed.")
        else:
            rt["state"] = "error"
            self._fire_event(EVENT_ROUTINE_FINISHED, routine, result)
            if routine["notify_on_error"]:
                self._send_notification(f"Routine '{routine['name']}' failed: {result}")

        await self._async_save()
        self._notify_listeners()
        return result == "success"

    async def async_run(self, routine_id: str) -> bool:
        """Manual run: conditions still apply, the once-a-day rule does not."""
        routine = self._find(routine_id)
        if routine is None:
            raise vol.Invalid(f"No routine with id '{routine_id}'.")
        return await self._execute_routine(routine, dt_util.now(), scheduled=False)

    def preview(self, routine: dict[str, Any]) -> dict[str, Any]:
        """What a run would do right now, without calling any service."""
        now = dt_util.now()
        ok, reason = self._check(routine, now)
        steps: list[dict[str, Any]] = []
        for action in routine["actions"]:
            if "delay" in action:
                steps.append({"type": "delay", "seconds": action["delay"]})
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

    # ----- service handlers -----

    async def async_run_routine(self, call: ServiceCall) -> None:
        try:
            await self.async_run(call.data["routine_id"])
        except vol.Invalid as err:
            _LOGGER.error("%s", err)


def _parse_time(value: str) -> dt_time:
    hour, minute, second = (int(p) for p in value.split(":"))
    return dt_time(hour, minute, second)
