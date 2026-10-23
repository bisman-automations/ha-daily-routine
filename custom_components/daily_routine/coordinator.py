"""Runtime: schedules, safety checks, execution and persistence."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from datetime import time as dt_time
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
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
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_time,
    async_track_state_change_event,
)
from homeassistant.helpers.sun import get_astral_event_next
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

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
    TRIGGER_EVERYONE_LEAVES,
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
from .routines import (
    DESIRED_STATE,
    PER_PERSON_TRIGGERS,
    PRESENCE_TRIGGERS,
    VALUE_TRIGGERS,
    normalize_routine,
    uses_conditions,
)
from .schema import vol

_LOGGER = logging.getLogger(__name__)

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
}


def _watched_entities(routine: dict[str, Any]) -> list[str]:
    """Every entity whose change can make this routine's conditions pass or fail."""
    entities: list[str] = []
    if routine["trigger"] == TRIGGER_EVERYONE_LEAVES:
        entities.extend(routine["people"])
    if routine["trigger"] == TRIGGER_ROOM_CLEAR:
        entities.extend(routine["sensors"])
    for cond in routine["conditions"]:
        if cond["kind"] == "presence":
            entities.extend(cond["people"])
        elif cond["kind"] == "sensors":
            entities.extend(cond["sensors"])
        else:
            entities.append(cond["entity_id"])
    return entities


def _all_people(routine: dict[str, Any]) -> list[str]:
    people = list(routine["people"])
    for cond in routine["conditions"]:
        if cond["kind"] == "presence":
            people.extend(cond["people"])
    return list(dict.fromkeys(people))


H_VALUE = "value"
H_EXPIRE = "expire"
H_CATCH_UP = "catch_up"

# Wait this long after Home Assistant starts before catching up missed routines,
# so lights, locks and people have loaded their states.
STARTUP_SETTLE_SECONDS = 30

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


NOTIFY_SERVICE_RE = re.compile(r"notify\.[a-z0-9_]+")


def person_is_tracked(state: State) -> bool:
    """A person can be checked only when Home Assistant has a location for them (a device tracker)."""
    return bool(state.attributes.get("source") or state.attributes.get("device_trackers"))


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
        for device in dr.async_entries_for_config_entry(registry, mobile_entry.entry_id):
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
    device_area = {device.id: device.area_id for device in dev_reg.devices if device.area_id}
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
    for area in sorted(ar.async_get(hass).async_list_areas(), key=lambda a: a.name.lower()):
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
        # Bumped whenever routines change, so the panel knows when to reload them.
        self.revision = 0
        self._history: dict[str, deque[dict[str, str]]] = {}
        # When each sensor was last seen on (in memory). Needed for 'active within N minutes'.
        self._last_on: dict[str, datetime] = {}
        self.paused = False
        self.migrated = False
        self.settings: dict[str, Any] = {"hidden_people": [], "notify_services": []}
        self._runtime: dict[str, dict[str, Any]] = {}
        self._listeners: list[Callable[[], None]] = []
        self._handles: dict[tuple[str, str], Callable[[], None]] = {}
        self._startup_unsubs: list[Callable[[], None]] = []
        self._entry_id: str | None = None
        # Serialises edits so the dashboard and the integration's entries stay in step.
        self._lock = asyncio.Lock()
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

    def _zone_key(self, zone: str) -> str | None:
        """The state a person reports while inside this zone (home is reported as 'home')."""
        if zone == "zone.home":
            return "home"
        state = self.hass.states.get(zone)
        return state.name if state is not None else None

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
        for service in self.settings.get("notify_services") or []:
            try:
                await self.hass.services.async_call(
                    "notify",
                    service.split(".", 1)[1],
                    {"title": "Daily Routine", "message": message},
                    blocking=False,
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

    def _record(self, routine_id: str, outcome: str, detail: str = "") -> None:
        runs = self._history.setdefault(routine_id, deque(maxlen=HISTORY_LENGTH))
        runs.append({"at": dt_util.now().isoformat(), "outcome": outcome, "detail": detail})

    def get_history(self, routine_id: str) -> list[dict[str, str]]:
        """Recent runs for a routine, newest first."""
        return list(reversed(self._history.get(routine_id, ())))

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
            "history": self.get_history(routine_id),
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
        saved = data.get("settings") or {}
        services = saved.get("notify_services")
        if services is None and saved.get("notify_service"):
            services = [saved["notify_service"]]  # one target before 1.4.0
        self.settings = {
            "hidden_people": [p for p in saved.get("hidden_people", []) if isinstance(p, str)],
            "notify_services": [s for s in (services or []) if isinstance(s, str)],
        }
        for raw in data.get("routines", []):
            try:
                self.routines.append(normalize_routine(raw))
            except vol.Invalid as err:
                _LOGGER.error("Skipping stored routine %s: %s", raw.get("id"), err)
        for rid, saved in (data.get("runtime") or {}).items():
            self._rt(rid).update({k: saved.get(k) for k in PERSISTED_KEYS})

    async def async_start(self) -> None:
        self._start_all(catch_up=False)
        self._notify_listeners()
        if self.hass.is_running:
            self._schedule_startup_catch_up()
        else:
            self._startup_unsubs.append(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, lambda _event: self._schedule_startup_catch_up()
                )
            )

    def _schedule_startup_catch_up(self) -> None:
        def _later(_now: datetime) -> None:
            now = dt_util.now()
            for routine in self.routines:
                self._maybe_catch_up(routine, now)

        self._startup_unsubs.append(async_call_later(self.hass, STARTUP_SETTLE_SECONDS, _later))

    async def async_unload(self) -> None:
        for unsub in self._startup_unsubs:
            unsub()
        self._startup_unsubs.clear()
        self._drop_all_handles()
        await self._async_save()

    async def _async_save(self) -> None:
        runtime = {rid: {k: rt.get(k) for k in PERSISTED_KEYS} for rid, rt in self._runtime.items()}
        await self._store.async_save(
            {
                "routines": self.routines,
                "paused": self.paused,
                "migrated": self.migrated,
                "settings": self.settings,
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
        self.revision += 1
        await self._async_save()
        self._notify_listeners()
        async_dispatcher_send(self.hass, SIGNAL_ROUTINES_CHANGED)

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
                untracked.append(str(state.attributes.get("friendly_name") or person_id))
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

        self._drop_companions(routine["id"])
        index = next((i for i, r in enumerate(self.routines) if r["id"] == routine["id"]), None)
        if index is None:
            if len(self.routines) >= MAX_ROUTINES:
                raise vol.Invalid(f"You can have at most {MAX_ROUTINES} routines.")
            self.routines.append(routine)
        else:
            self.routines[index] = routine
        if companion is not None:
            self.routines.append(companion)
        await self.async_reload()
        return routine

    def _drop_companions(self, routine_id: str) -> None:
        for r in [r for r in self.routines if r.get("companion_of") == routine_id]:
            self._runtime.pop(r["id"], None)
        self.routines = [r for r in self.routines if r.get("companion_of") != routine_id]

    async def async_delete_routine(self, routine_id: str) -> None:
        async with self._lock:
            self._drop_companions(routine_id)
            self.routines = [r for r in self.routines if r["id"] != routine_id]
            self._runtime.pop(routine_id, None)
            self._history.pop(routine_id, None)
            await self.async_reload()
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
                problems.append({"id": "?", "reason": "Each routine must be an object."})
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

    def _entry(self) -> ConfigEntry | None:
        return self.hass.config_entries.async_get_entry(self._entry_id) if self._entry_id else None

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
                self.hass.config_entries.async_update_subentry(entry, sub, title=routine["name"])
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
                raise vol.Invalid("One of those notification targets is no longer available.")
        self.settings = {
            "hidden_people": sorted(set(hidden)),
            "notify_services": sorted(set(services)),
        }
        await self._async_save()
        self._notify_listeners()
        return self.settings

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
        return self._state_is(cond["entity_id"], cond["state"])

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
        """Quiet: all off for N minutes. Active: any on, or one went off within N minutes."""
        states: list[State] = []
        for eid in cond["sensors"]:
            state = self.hass.states.get(eid)
            if state is None or state.state in UNKNOWN_STATES:
                return False
            states.append(state)
        any_on = any(state.state == "on" for state in states)
        window = cond["minutes"] * 60
        if cond["mode"] == "quiet":
            since_change = (now - max(state.last_changed for state in states)).total_seconds()
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
            if new is not None and new.state == "on":
                self._last_on[new.entity_id] = dt_util.now()
            self._on_condition_entity_changed(routine, event.data.get("entity_id"))

        unsub = async_track_state_change_event(self.hass, sorted(entities), _changed)
        self._set_handle(rid, H_WATCH, unsub)
        self._arm_clear_timer(routine)
        self._arm_condition_timers(routine)

    def _on_condition_entity_changed(self, routine: dict[str, Any], entity_id: str | None) -> None:
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
                seen = [self._last_on[eid] for eid in cond["sensors"] if eid in self._last_on]
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

        self._set_handle(rid, H_COND, async_track_point_in_time(self.hass, _fire, min(due)))

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
        self._record(rid, "skipped", "conditions_not_met")
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
            self.hass.async_create_task(self._run_catch_up(routine, window_end))

    async def _run_catch_up(self, routine: dict[str, Any], window_end: datetime) -> None:
        """Try a missed run, and keep trying each minute while a condition blocks it, until the window closes."""
        rid = routine["id"]
        now = dt_util.now()
        if not routine["enabled"] or self._rt(rid)["last_run_date"] == now.date().isoformat():
            return
        ran = await self._execute_routine(routine, now, scheduled=True)
        if ran or not uses_conditions(routine):
            return
        retry_at = now + timedelta(minutes=1)
        if retry_at >= window_end:
            _LOGGER.warning(
                "Routine '%s' was still blocked when its catch-up window closed.", routine["name"]
            )
            return

        @callback
        def _again(_fired: datetime) -> None:
            self.hass.async_create_task(self._run_catch_up(routine, window_end))

        self._set_handle(rid, H_CATCH_UP, async_track_point_in_time(self.hass, _again, retry_at))

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
        rt["all_away"] = self._all_away(people, zone)

        @callback
        def _on_change(_event: Event[EventStateChangedData]) -> None:
            if not routine["enabled"]:
                return
            now = dt_util.now()
            was_away = bool(rt["all_away"])
            is_away = self._all_away(people, zone)
            rt["all_away"] = is_away
            if not self._allowed_day(routine, now):
                return
            leaves = trigger == TRIGGER_EVERYONE_LEAVES and is_away and not was_away
            arrives = trigger == TRIGGER_SOMEONE_ARRIVES and was_away and not is_away
            if leaves or arrives:
                self.hass.async_create_task(self._execute_routine(routine, now, scheduled=True))

        unsub = async_track_state_change_event(self.hass, people, _on_change)
        self._set_handle(rid, H_PRESENCE, unsub)

    def _watch_each_person(self, routine: dict[str, Any], people: list[str]) -> None:
        """Leave or arrive for any one chosen person, judged on each person's own change."""
        rid = routine["id"]
        trigger = routine["trigger"]
        zone = routine["zone"]

        @callback
        def _changed(event: Event[EventStateChangedData]) -> None:
            if not routine["enabled"]:
                return
            old = event.data.get("old_state")
            new = event.data.get("new_state")
            key = self._zone_key(zone)
            if old is None or new is None or key is None:
                return
            if old.state in UNKNOWN_STATES or new.state in UNKNOWN_STATES:
                return
            was_in = old.state == key
            is_in = new.state == key
            leaves = trigger == TRIGGER_PERSON_LEAVES and was_in and not is_in
            arrives = trigger == TRIGGER_PERSON_ARRIVES and is_in and not was_in
            now = dt_util.now()
            if (leaves or arrives) and self._allowed_day(routine, now):
                self.hass.async_create_task(self._execute_routine(routine, now, scheduled=True))

        unsub = async_track_state_change_event(self.hass, people, _changed)
        self._set_handle(rid, H_PRESENCE, unsub)

    def _watch_value(self, routine: dict[str, Any]) -> None:
        rid = routine["id"]
        self._drop_handle(rid, H_VALUE)
        trigger = routine["trigger"]
        if trigger not in VALUE_TRIGGERS or not routine["enabled"]:
            return
        threshold = float(routine["value_threshold"])

        @callback
        def _changed(event: Event[EventStateChangedData]) -> None:
            new = _as_float(event.data.get("new_state"))
            if new is None:
                return
            old = _as_float(event.data.get("old_state"))
            if trigger == TRIGGER_VALUE_ABOVE:
                crossed = new > threshold and (old is None or old <= threshold)
            else:
                crossed = new < threshold and (old is None or old >= threshold)
            now = dt_util.now()
            if crossed and self._allowed_day(routine, now):
                self.hass.async_create_task(self._execute_routine(routine, now, scheduled=True))

        unsub = async_track_state_change_event(self.hass, [routine["value_entity"]], _changed)
        self._set_handle(rid, H_VALUE, unsub)

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
            self._watch_value(routine)
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
            self._record(rid, "skipped", "paused")
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
            self._record(rid, "skipped", reason or "")
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
        self._record(
            rid, "ran" if result == "success" else "failed", "" if result == "success" else result
        )
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


def _companion_for(routine: dict[str, Any]) -> dict[str, Any] | None:
    """A 'turn back off' routine for a value-above routine with a turn-off level."""
    if routine["trigger"] != TRIGGER_VALUE_ABOVE or routine["off_threshold"] is None:
        return None
    actions: list[dict[str, Any]] = []
    for action in routine["actions"]:
        if "service" not in action:
            actions.append(action)
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
        "actions": actions,
        "retries": routine["retries"],
        "notify_on_error": routine["notify_on_error"],
        "companion_of": routine["id"],
    }


def _as_float(state: State | None) -> float | None:
    if state is None:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
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
