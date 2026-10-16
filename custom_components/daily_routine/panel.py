"""Sidebar panel and websocket API. Rooms come from Home Assistant areas; nothing to set up."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import HomeRoutinesCoordinator, list_areas
from .routines import normalize_routine

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


def _coordinator(hass: HomeAssistant) -> HomeRoutinesCoordinator | None:
    return (hass.data.get(DOMAIN) or {}).get("coordinator")


def _devices(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Devices a routine can be linked to. Our own routine and hub devices are left out."""
    registry = dr.async_get(hass)
    areas = ar.async_get(hass)
    devices = []
    for device in registry.devices.values():
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


def _overview(hass: HomeAssistant, coordinator: HomeRoutinesCoordinator) -> dict[str, Any]:
    people = [
        {
            "entity_id": s.entity_id,
            "name": s.attributes.get("friendly_name") or s.entity_id,
            "state": s.state,
        }
        for s in sorted(hass.states.async_all("person"), key=lambda s: s.entity_id)
    ]
    sensors = [
        {
            "entity_id": s.entity_id,
            "name": s.attributes.get("friendly_name") or s.entity_id,
            "device_class": s.attributes.get("device_class"),
            "state": s.state,
        }
        for s in sorted(hass.states.async_all("binary_sensor"), key=lambda s: s.entity_id)
    ]
    statuses = {r["id"]: coordinator.get_status(r["id"]) or {} for r in coordinator.routines}
    return {
        "areas": list_areas(hass),
        "devices": _devices(hass),
        "people": people,
        "binary_sensors": sensors,
        "routines": coordinator.routines,
        "statuses": statuses,
        "paused": coordinator.paused,
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
    ):
        websocket_api.async_register_command(hass, command)
    hass.data[_API_REGISTERED] = True


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/overview"})
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
    connection.send_result(msg["id"], coordinator.preview(routine))


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


async def async_register_panel(hass: HomeAssistant) -> None:
    async_setup_panel_api(hass)

    if not hass.data.get(_STATIC_REGISTERED):
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), cache_headers=False),
                StaticPathConfig(f"{STATIC_URL}/brand", str(BRAND_DIR), cache_headers=True),
            ]
        )
        hass.data[_STATIC_REGISTERED] = True

    if hass.data.get(_PANEL_REGISTERED):
        return
    try:
        await panel_custom.async_register_panel(
            hass,
            webcomponent_name=PANEL_COMPONENT,
            frontend_url_path=PANEL_URL_PATH,
            sidebar_title="Daily Routine",
            sidebar_icon="mdi:calendar-clock",
            module_url=f"{STATIC_URL}/daily-routine-panel.js",
            embed_iframe=False,
            require_admin=False,
        )
        hass.data[_PANEL_REGISTERED] = True
    except ValueError:
        hass.data[_PANEL_REGISTERED] = True


async def async_unregister_panel(hass: HomeAssistant) -> None:
    if not hass.config_entries.async_entries(DOMAIN):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
        hass.data[_PANEL_REGISTERED] = False
