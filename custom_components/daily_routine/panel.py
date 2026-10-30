"""Sidebar panel and websocket API. Rooms come from Home Assistant areas; nothing to set up."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
import logging
from pathlib import Path
from typing import Any

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.util import dt as dt_util

from .access import async_get_access, get_access
from .const import DOMAIN
from .coordinator import (
    HomeRoutinesCoordinator,
    list_areas,
    list_people,
    notify_targets,
)
from .routines import normalize_routine
from .run_log import WINDOWS, summarize
from .runtime import get_coordinator
from .schema import vol

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "daily-routine"
STATIC_URL = f"/{DOMAIN}_static"
PANEL_COMPONENT = "daily-routine-panel"
FRONTEND_DIR = Path(__file__).parent / "frontend"
BRAND_DIR = Path(__file__).parent / "brand"

_PANEL_REGISTERED = f"{DOMAIN}_panel_registered"
_STATIC_REGISTERED = f"{DOMAIN}_panel_static_registered"
_API_REGISTERED = f"{DOMAIN}_panel_api_registered"

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
    """Devices a routine can be linked to. Our own routine and hub devices are left out."""
    registry = dr.async_get(hass)
    areas = ar.async_get(hass)
    devices = []
    for device in registry.devices:
        if device.disabled or any(domain == DOMAIN for domain, _ in device.identifiers):
            continue
        area = areas.async_get_area(device.area_id) if device.area_id else None
        devices.append(
            {
                "id": device.id,
                "name": device.name_by_user or device.name or device.id,
                "area_id": device.area_id,
                "area_name": area.name if area else None,
                "model": device.model,
            }
        )
    return sorted(devices, key=lambda d: str(d["name"]).lower())


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
    }


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
        ws_save_settings,
        ws_status,
        ws_activity,
        ws_export,
        ws_import,
        ws_access_me,
        ws_access_get,
        ws_access_set,
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
    connection.send_result(msg["id"], summary)


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
    connection.send_result(
        msg["id"],
        {
            "revision": coordinator.revision,
            "paused": coordinator.paused,
            "statuses": {
                r["id"]: coordinator.get_status(r["id"]) or {}
                for r in coordinator.routines
            },
        },
    )


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
    {vol.Required("type"): f"{DOMAIN}/routine/run", vol.Required("routine_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_run_routine(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return
    try:
        ran = await coordinator.async_run(msg["routine_id"])
    except vol.Invalid as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    connection.send_result(msg["id"], {"ran": ran})


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
