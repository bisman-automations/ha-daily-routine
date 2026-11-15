"""Sidebar panel and websocket API. Rooms come from Home Assistant areas; nothing to set up."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import wraps
import logging
from pathlib import Path
from typing import Any

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.loader import LoaderError, async_get_loaded_integration
from homeassistant.util import dt as dt_util

from .access import async_get_access, get_access
from .const import DOMAIN
from .coordinator import (
    HomeRoutinesCoordinator,
    list_areas,
    list_people,
    notify_targets,
)
from .guests import guest_card
from .linked import integration_name, integration_rank, linked_integrations
from .routines import MODES, normalize_routine
from .run_log import WINDOWS, daily_counts, summarize
from .runtime import get_coordinator
from .schema import vol
from .timeline import today, tomorrow

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "daily-routine"
STATIC_URL = f"/{DOMAIN}_static"
PANEL_COMPONENT = "daily-routine-panel"
FRONTEND_DIR = Path(__file__).parent / "frontend"
BRAND_DIR = Path(__file__).parent / "brand"

_PANEL_REGISTERED = f"{DOMAIN}_panel_registered"
_STATIC_REGISTERED = f"{DOMAIN}_panel_static_registered"
_API_REGISTERED = f"{DOMAIN}_panel_api_registered"
_CARD_REGISTERED = f"{DOMAIN}_card_registered"

SENSOR_CLASSES = ("motion", "occupancy", "presence", "moving")


def require_access(func: Callable) -> Callable:
    """Only admins and the people an admin allowed may see routines."""

    @wraps(func)
    def with_access(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
    ) -> None:
        if not get_access(hass).allows(connection.user):
            raise Unauthorized
        func(hass, connection, msg)

    return with_access


def _coordinator(hass: HomeAssistant) -> HomeRoutinesCoordinator | None:
    return get_coordinator(hass)


def _devices(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Devices a routine can be linked to. Our own routine and hub devices are left out.

    Services (software such as a Music Assistant player or another
    integration's service) are marked ``service`` so the editor offers only
    real devices, while a routine already linked to one still shows it.
    """
    registry = dr.async_get(hass)
    areas = ar.async_get(hass)
    devices = []
    sources: dict[str, tuple[str, bool]] = {}
    for device in registry.devices:
        if device.disabled or any(domain == DOMAIN for domain, _ in device.identifiers):
            continue
        area = areas.async_get_area(device.area_id) if device.area_id else None
        integration, service_integration = _source(hass, device, sources)
        devices.append(
            {
                "id": device.id,
                "name": device.name_by_user or device.name or device.id,
                "area_id": device.area_id,
                "area_name": area.name if area else None,
                "model": device.model,
                "integration": integration,
                "service": device.entry_type is dr.DeviceEntryType.SERVICE
                or service_integration,
            }
        )
    return sorted(devices, key=lambda d: str(d["name"]).lower())


def _source(
    hass: HomeAssistant, device: dr.DeviceEntry, cache: dict[str, tuple[str, bool]]
) -> tuple[str | None, bool]:
    """The integration a device comes from (such as SwitchBot Cloud), and
    whether that integration is a service rather than hardware."""
    entry = hass.config_entries.async_get_entry(device.config_entry_id or "")
    if entry is None:
        return None, False
    if entry.domain not in cache:
        try:
            integration = async_get_loaded_integration(hass, entry.domain)
            cache[entry.domain] = (
                integration.name,
                integration.integration_type == "service",
            )
        except LoaderError:
            cache[entry.domain] = (entry.domain, False)
    return cache[entry.domain]


def _is_number(value: str) -> bool:
    try:
        float(value)
    except TypeError, ValueError:
        return False
    return True


def _zones(hass: HomeAssistant) -> list[dict[str, str]]:
    zones = [{"entity_id": "zone.home", "name": "Home"}]
    for state in sorted(hass.states.async_all("zone"), key=lambda s: s.entity_id):
        if state.entity_id != "zone.home":
            zones.append(
                {
                    "entity_id": state.entity_id,
                    "name": str(state.attributes.get("friendly_name") or state.name),
                }
            )
    return zones


def _overview(
    hass: HomeAssistant, coordinator: HomeRoutinesCoordinator
) -> dict[str, Any]:
    sensors = [
        {
            "entity_id": s.entity_id,
            "name": s.attributes.get("friendly_name") or s.entity_id,
            "device_class": s.attributes.get("device_class"),
            "state": s.state,
        }
        for s in sorted(
            hass.states.async_all("binary_sensor"), key=lambda s: s.entity_id
        )
    ]
    numeric = [
        {
            "entity_id": s.entity_id,
            "name": s.attributes.get("friendly_name") or s.entity_id,
            "device_class": s.attributes.get("device_class"),
            "unit": s.attributes.get("unit_of_measurement") or "",
        }
        for s in sorted(hass.states.async_all("sensor"), key=lambda s: s.entity_id)
        if s.attributes.get("unit_of_measurement") and _is_number(s.state)
    ]
    statuses = {
        r["id"]: coordinator.get_status(r["id"]) or {} for r in coordinator.routines
    }
    # Every entity, not only the ones in an area, so a to-do link can watch any device.
    all_entities = sorted(
        (
            {
                "entity_id": s.entity_id,
                "name": s.attributes.get("friendly_name") or s.entity_id,
                "state": s.state,
            }
            for s in hass.states.async_all()
        ),
        key=lambda e: e["name"].lower(),
    )
    todo_lists = [e for e in all_entities if e["entity_id"].startswith("todo.")]
    return {
        "areas": list_areas(hass),
        "devices": _devices(hass),
        "people": list_people(hass, frozenset(coordinator.settings["hidden_people"])),
        "settings": coordinator.settings,
        "notify_targets": notify_targets(hass),
        "zones": _zones(hass),
        "binary_sensors": sensors,
        "sensors": numeric,
        "routines": coordinator.routines,
        "health": {r["id"]: coordinator.health(r) for r in coordinator.routines},
        "todo_lists": todo_lists,
        "all_entities": all_entities,
        "statuses": statuses,
        "paused": coordinator.paused,
        "revision": coordinator.revision,
        "mode": coordinator.mode,
        "workday_sensors": _platform_entities(hass, "binary_sensor", "workday"),
        "weathers": [e for e in all_entities if e["entity_id"].startswith("weather.")],
        "alarm_sensors": _alarm_sensors(hass),
        "lived_in": coordinator.lived_in.status(dt_util.now()),
        # Which integration each entity comes from, shown next to its name in
        # every device list (one light can be in Hue, Matter and HomeKit).
        "integrations": entity_integrations(hass),
        "habits": coordinator.habits.ideas(dt_util.now()),
        "day_type": coordinator.day_type(dt_util.now()),
        "hold": coordinator.hold(dt_util.now()),
        "guest_wifi": guest_card(coordinator.settings, coordinator.mode),
        "attention": coordinator.attention(dt_util.now()),
        "energy": coordinator.energy_month(dt_util.now()),
        "can_undo_recent": any(
            coordinator.can_undo(r["id"]) for r in coordinator.routines
        ),
        "calendars": [
            e for e in all_entities if e["entity_id"].startswith("calendar.")
        ],
    }


def entity_integrations(hass: HomeAssistant) -> dict[str, str]:
    """Entity id -> the name of the integration it comes from."""
    names: dict[str, str] = {}
    found: dict[str, str] = {}
    for entry in er.async_get(hass).entities.values():
        if entry.disabled_by is not None or not entry.platform:
            continue
        if entry.platform not in names:
            names[entry.platform] = integration_name(hass, entry.platform)
        found[entry.entity_id] = names[entry.platform]
    return found


def _alarm_sensors(hass: HomeAssistant) -> list[dict[str, str]]:
    """Time sensors a routine can run before, such as a phone's Next alarm, and
    date-and-time helpers. Next alarm sensors first."""
    found = []
    for state in hass.states.async_all(("sensor", "input_datetime")):
        if (
            state.domain == "sensor"
            and state.attributes.get("device_class") != "timestamp"
        ):
            continue
        if state.domain == "input_datetime" and not (
            state.attributes.get("has_date") and state.attributes.get("has_time")
        ):
            continue
        found.append({"entity_id": state.entity_id, "name": state.name})
    return sorted(
        found,
        key=lambda e: ("alarm" not in e["entity_id"], e["name"].lower()),
    )


def _platform_entities(
    hass: HomeAssistant, domain: str, platform: str
) -> list[dict[str, str]]:
    """Entities a given integration made, such as Workday's sensors."""
    registry = er.async_get(hass)
    found = []
    for entry in registry.entities.values():
        if entry.domain != domain or entry.platform != platform or entry.disabled:
            continue
        state = hass.states.get(entry.entity_id)
        found.append(
            {
                "entity_id": entry.entity_id,
                "name": state.name if state else entry.entity_id,
            }
        )
    return sorted(found, key=lambda e: e["name"].lower())


def async_setup_panel_api(hass: HomeAssistant) -> None:
    if hass.data.get(_API_REGISTERED):
        return
    for command in (
        ws_overview,
        ws_save_routine,
        ws_delete_routine,
        ws_run_routine,
        ws_preview_routine,
        ws_set_paused,
        ws_skip_next,
        ws_try_again,
        ws_pause_until,
        ws_vacation_start,
        ws_vacation_end,
        ws_summary_send,
        ws_save_settings,
        ws_status,
        ws_subscribe,
        ws_answer,
        ws_copy,
        ws_today,
        ws_undo,
        ws_undo_recent,
        ws_set_mode,
        ws_integrations,
        ws_activity,
        ws_export,
        ws_import,
        ws_access_me,
        ws_access_get,
        ws_access_set,
        ws_dismiss_idea,
        ws_pause_group,
        ws_weekly_send,
        ws_why,
        ws_history,
        ws_tomorrow,
        ws_tick,
    ):
        websocket_api.async_register_command(hass, command)
    hass.data[_API_REGISTERED] = True


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/overview"})
@require_access
@websocket_api.async_response
async def ws_overview(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    connection.send_result(msg["id"], _overview(hass, coordinator))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/activity",
        vol.Optional("window", default="7d"): vol.In(WINDOWS),
    }
)
@require_access
@websocket_api.async_response
async def ws_activity(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Statistics, activity and health for the Activity tab, for one window."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    now = dt_util.now()
    summary = summarize(
        coordinator.run_log.events(), msg["window"], now, coordinator.routines
    )
    summary["health"] = [
        {"id": r["id"], "name": r["name"], "problems": problems}
        for r in coordinator.routines
        if r["enabled"] and (problems := coordinator.health(r))
    ]
    summary["devices"] = coordinator.device_stats.summary(
        {"today": 1, "7d": 7, "30d": 30}[msg["window"]], now
    )
    connection.send_result(msg["id"], summary)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/routine/answer",
        vol.Required("routine_id"): str,
        vol.Required("run"): bool,
    }
)
@require_access
@callback
def ws_answer(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Answer "Ask me first" from the card: run it now, or skip it."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    answered = coordinator.async_answer(msg["routine_id"], msg["run"])
    connection.send_result(msg["id"], {"answered": answered})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/routine/copy",
        vol.Required("routine_id"): str,
        vol.Required("rooms"): [str],
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_copy(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Copy a routine to other rooms."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    try:
        copies = await coordinator.async_copy_to_rooms(msg["routine_id"], msg["rooms"])
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid_routine", str(err))
        return
    connection.send_result(msg["id"], {"routines": copies})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/integrations"})
@require_access
@websocket_api.async_response
async def ws_integrations(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Integrations whose devices are also in another integration, best first."""
    connection.send_result(
        msg["id"],
        {
            "integrations": [
                {
                    "domain": domain,
                    "name": integration_name(hass, domain),
                    "local": integration_rank(hass, domain) <= 2,
                }
                for domain in linked_integrations(hass)
            ]
        },
    )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/status"})
@require_access
@websocket_api.async_response
async def ws_status(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The small, frequent refresh: run states only. A full reload happens when the revision changes."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    connection.send_result(msg["id"], _status(coordinator))


def _status(coordinator: HomeRoutinesCoordinator) -> dict[str, Any]:
    return {
        "revision": coordinator.revision,
        "paused": coordinator.paused,
        "log_count": coordinator.run_log.count,
        "mode": coordinator.mode,
        "guest_wifi": guest_card(coordinator.settings, coordinator.mode),
        "lived_in": coordinator.lived_in.status(dt_util.now()),
        "statuses": {
            r["id"]: coordinator.get_status(r["id"]) or {} for r in coordinator.routines
        },
    }


# Live updates are sent at most this often while a routine is running.
PUSH_SECONDS = 0.3


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@require_access
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Run states as they change, so a card shows Running and its progress at once."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    pending: list[asyncio.TimerHandle] = []

    @callback
    def _send() -> None:
        pending.clear()
        if (current := _coordinator(hass)) is not None:
            connection.send_message(
                websocket_api.event_message(msg["id"], _status(current))
            )

    @callback
    def _changed() -> None:
        if not pending:
            pending.append(hass.loop.call_later(PUSH_SECONDS, _send))

    remove = coordinator.async_add_listener(_changed)

    @callback
    def _unsubscribe() -> None:
        remove()
        for handle in pending:
            handle.cancel()

    connection.subscriptions[msg["id"]] = _unsubscribe
    connection.send_result(msg["id"])
    _send()


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/export"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_export(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    connection.send_result(
        msg["id"],
        {
            "format": DOMAIN,
            "version": 1,
            "routines": coordinator.export_routines(),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/import",
        vol.Required("routines"): list,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_import(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    connection.send_result(
        msg["id"], await coordinator.async_import_routines(msg["routines"])
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/routine/save", vol.Required("routine"): dict}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save_routine(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    try:
        routine = await coordinator.async_save_routine(msg["routine"])
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid_routine", str(err))
        return
    connection.send_result(msg["id"], {"routine": routine})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/routine/delete", vol.Required("routine_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_delete_routine(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    await coordinator.async_delete_routine(msg["routine_id"])
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/routine/try_again",
        vol.Required("routine_id"): str,
    }
)
@require_access
@websocket_api.async_response
async def ws_try_again(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    try:
        done = await coordinator.async_try_again(msg["routine_id"])
    except vol.Invalid as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    connection.send_result(msg["id"], {"done": done})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/routine/run", vol.Required("routine_id"): str}
)
@require_access
@websocket_api.async_response
async def ws_run_routine(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    try:
        result = await coordinator.async_start_run(msg["routine_id"])
    except vol.Invalid as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/routine/preview", vol.Required("routine"): dict}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_preview_routine(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    try:
        routine = normalize_routine(msg["routine"])
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid_routine", str(err))
        return
    connection.send_result(msg["id"], await coordinator.preview(routine))


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/pause", vol.Required("paused"): bool}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_paused(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    await coordinator.async_set_paused(msg["paused"])
    connection.send_result(msg["id"], {"paused": coordinator.paused})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/routine/skip_next",
        vol.Required("routine_id"): str,
        vol.Optional("skip", default=True): bool,
    }
)
@require_access
@websocket_api.async_response
async def ws_skip_next(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Skip (or stop skipping) a routine's next scheduled run."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    try:
        await coordinator.async_skip_next(msg["routine_id"], msg["skip"])
    except vol.Invalid as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    connection.send_result(msg["id"], {"skip_next": msg["skip"]})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/routines/pause_until",
        vol.Required("routine_ids"): [str],
        vol.Required("until"): vol.Any(None, str),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_pause_until(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Pause routines' scheduled runs through a day, or resume them (until None)."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    try:
        await coordinator.async_pause_until(msg["routine_ids"], msg["until"])
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid_routine", str(err))
        return
    connection.send_result(msg["id"], {"until": msg["until"]})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/group/pause",
        vol.Required("group"): str,
        vol.Required("until"): vol.Any(None, str),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_pause_group(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Pause every routine in a group through a day, or resume them (None)."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    try:
        ids = await coordinator.async_pause_group(msg["group"], msg["until"])
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid_routine", str(err))
        return
    connection.send_result(msg["id"], {"routine_ids": ids, "until": msg["until"]})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/idea/dismiss", vol.Required("idea_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_dismiss_idea(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Don't offer an idea from someone's habits again."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    await coordinator.habits.async_dismiss(msg["idea_id"][:200])
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/vacation/start",
        vol.Required("until"): str,
        vol.Optional("routine_ids", default=[]): [str],
        vol.Optional("lived_in", default=True): bool,
        vol.Optional("summary", default=True): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_vacation_start(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Going on vacation: Vacation mode, routines paused, lived-in lights, a daily summary."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    try:
        vacation = await coordinator.away.async_start(
            msg["until"],
            msg["routine_ids"],
            lived_in=msg["lived_in"],
            summary=msg["summary"],
        )
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid_routine", str(err))
        return
    connection.send_result(msg["id"], {"vacation": vacation})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/vacation/end"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_vacation_end(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Back home: everything as it was before the vacation."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    await coordinator.away.async_end()
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/summary/send"})
@websocket_api.require_admin
@callback
def ws_summary_send(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Send today's summary now (Settings' Send it now), and return its text."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    now = dt_util.now()
    coordinator.send_summary(now)
    connection.send_result(msg["id"], {"text": coordinator.away.summary(now)})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/weekly/send"})
@websocket_api.require_admin
@callback
def ws_weekly_send(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Send the weekly report now, and return its text."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    now = dt_util.now()
    coordinator.send_weekly(now)
    connection.send_result(msg["id"], {"text": coordinator.away.weekly(now)})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/routine/why", vol.Required("routine_id"): str}
)
@require_access
@callback
def ws_why(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Why a routine won't run on its own right now (or didn't last time)."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    try:
        connection.send_result(msg["id"], coordinator.explain(msg["routine_id"]))
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid_routine", str(err))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/routine/history",
        vol.Optional("routine_id"): str,
        vol.Optional("days", default=30): vol.All(int, vol.Range(min=7, max=30)),
    }
)
@require_access
@callback
def ws_history(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Ran, skipped and failed per day, for one routine or all of them."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    connection.send_result(
        msg["id"],
        {
            "days": daily_counts(
                coordinator.run_log.events(),
                dt_util.now(),
                msg["days"],
                msg.get("routine_id"),
            )
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/routine/tick",
        vol.Required("routine_id"): str,
        vol.Required("item"): str,
        vol.Required("done"): bool,
    }
)
@require_access
@callback
def ws_tick(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Tick off a thing on a running routine's checklist (the card or panel)."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    try:
        ticked = coordinator.async_tick_item(
            msg["routine_id"], msg["item"], msg["done"]
        )
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    connection.send_result(msg["id"], {"ticked": ticked})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/plan/tomorrow"})
@require_access
@callback
def ws_tomorrow(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """A dry run of tomorrow: what would run when, and its steps."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    connection.send_result(msg["id"], tomorrow(coordinator, dt_util.now()))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/today"})
@require_access
@callback
def ws_today(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """What ran today and what's still to come."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    connection.send_result(msg["id"], today(coordinator, dt_util.now()))


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/routine/undo", vol.Required("routine_id"): str}
)
@require_access
@websocket_api.async_response
async def ws_undo(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    try:
        put_back = await coordinator.async_undo(msg["routine_id"])
    except vol.Invalid as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "undo_failed", str(err))
        return
    connection.send_result(msg["id"], {"put_back": put_back})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/undo/recent",
        vol.Optional("minutes", default=60): vol.All(int, vol.Range(min=5, max=120)),
    }
)
@require_access
@websocket_api.async_response
async def ws_undo_recent(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Put back what every routine changed in the last hour."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    names = await coordinator.async_undo_recent(msg["minutes"])
    connection.send_result(msg["id"], {"undone": names})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/mode/set", vol.Required("mode"): vol.In(MODES)}
)
@require_access
@websocket_api.async_response
async def ws_set_mode(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    await coordinator.async_set_mode(msg["mode"])
    connection.send_result(msg["id"], {"mode": coordinator.mode})


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/settings/save", vol.Required("settings"): dict}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save_settings(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    try:
        settings = await coordinator.async_set_settings(msg["settings"])
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid_settings", str(err))
        return
    connection.send_result(msg["id"], {"settings": settings})


async def async_register_panel(hass: HomeAssistant, *, update: bool = False) -> None:
    """Add the sidebar entry.

    It's admin-only unless an admin let someone who isn't an admin in. Home
    Assistant can't show a sidebar entry to only some people, so then everyone
    sees it, and the page tells anyone not allowed that they don't have access.
    """
    access = await async_get_access(hass)
    async_setup_panel_api(hass)

    if not hass.data.get(_STATIC_REGISTERED):
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False),
                StaticPathConfig(
                    f"{STATIC_URL}/brand", str(BRAND_DIR), cache_headers=True
                ),
            ]
        )
        hass.data[_STATIC_REGISTERED] = True
    if not hass.data.get(_CARD_REGISTERED):
        # The dashboard card, on every dashboard.
        frontend.add_extra_js_url(hass, f"{STATIC_URL}/daily-routine-card.js")
        hass.data[_CARD_REGISTERED] = True

    require_admin = not await access.async_listed_non_admins()
    wanted = "admin" if require_admin else "all"
    registered = hass.data.get(_PANEL_REGISTERED)
    if registered == wanted or (registered and not update):
        return
    if registered:
        frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
    try:
        await panel_custom.async_register_panel(
            hass,
            webcomponent_name=PANEL_COMPONENT,
            frontend_url_path=PANEL_URL_PATH,
            sidebar_title="Daily Routine",
            sidebar_icon="mdi:calendar-clock",
            module_url=f"{STATIC_URL}/daily-routine-panel.js",
            embed_iframe=False,
            require_admin=require_admin,
        )
    except ValueError:
        pass  # Already registered by an earlier load of this entry.
    hass.data[_PANEL_REGISTERED] = wanted


async def async_unregister_panel(hass: HomeAssistant) -> None:
    if not hass.config_entries.async_entries(DOMAIN):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
        hass.data[_PANEL_REGISTERED] = False


# ---- who may see Daily Routine ------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/access/me"})
@callback
def ws_access_me(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Whether the person viewing may see routines, so the page can say so."""
    user = connection.user
    connection.send_result(
        msg["id"],
        {
            "allowed": get_access(hass).allows(user),
            "is_admin": bool(user and user.is_admin),
        },
    )


async def _people(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Home Assistant logins an admin can let in (admins always see everything)."""
    users = [
        u
        for u in await hass.auth.async_get_users()
        if not u.system_generated and u.is_active
    ]
    return [
        {"id": u.id, "name": u.name or u.id, "is_admin": u.is_admin}
        for u in sorted(users, key=lambda u: (u.name or "").casefold())
    ]


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/access/get"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_access_get(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    connection.send_result(
        msg["id"],
        {"user_ids": list(get_access(hass).user_ids), "people": await _people(hass)},
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/access/set", vol.Required("user_ids"): [str]}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_access_set(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    access = get_access(hass)
    await access.async_set(msg["user_ids"])
    await async_register_panel(hass, update=True)
    connection.send_result(
        msg["id"], {"user_ids": list(access.user_ids), "people": await _people(hass)}
    )
