"""Sidebar panel + websocket API for Daily Routine."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "daily-routine"
STATIC_URL = f"/{DOMAIN}_static"
PANEL_COMPONENT = "daily-routine-panel"
FRONTEND_DIR = Path(__file__).parent / "frontend"
BRAND_DIR = Path(__file__).parent / "brand"

_PANEL_REGISTERED = f"{DOMAIN}_panel_registered"
_STATIC_REGISTERED = f"{DOMAIN}_panel_static_registered"
_API_REGISTERED = f"{DOMAIN}_panel_api_registered"


def async_setup_panel_api(hass: HomeAssistant) -> None:
    """Register websocket commands once."""
    if hass.data.get(_API_REGISTERED):
        return
    websocket_api.async_register_command(hass, ws_get_routines)
    websocket_api.async_register_command(hass, ws_save_routines)
    websocket_api.async_register_command(hass, ws_run_routine)
    hass.data[_API_REGISTERED] = True


def _entry(hass: HomeAssistant):
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/panel/routines"})
@websocket_api.async_response
async def ws_get_routines(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return current routines + live status."""
    coordinator = (hass.data.get(DOMAIN) or {}).get("coordinator")
    if coordinator is None:
        connection.send_result(msg["id"], {"routines": [], "statuses": {}})
        return

    routines = list(coordinator.routines)
    statuses: dict[str, Any] = {r["id"]: coordinator.get_status(r["id"]) or {} for r in routines}
    connection.send_result(
        msg["id"],
        {
            "routines": routines,
            "statuses": statuses,
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/save",
        vol.Required("routines"): list,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save_routines(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Dashboard bulk save is not used — each routine is a config entry."""
    connection.send_error(
        msg["id"],
        "use_configure",
        "Edit routines with Configure on each entry under Settings → Devices & services → Daily Routine, or use Add service to create a new routine.",
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel/run",
        vol.Required("routine_id"): str,
    }
)
@websocket_api.async_response
async def ws_run_routine(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Run a routine from the dashboard."""
    coordinator = (hass.data.get(DOMAIN) or {}).get("coordinator")
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Daily Routine is not loaded")
        return

    class _Call:
        data = {"routine_id": msg["routine_id"]}

    await coordinator.async_run_routine(_Call())  # type: ignore[arg-type]
    connection.send_result(msg["id"], {"ok": True})


async def async_register_panel(hass: HomeAssistant) -> None:
    """Register static assets, websocket API, and sidebar panel."""
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
        _LOGGER.debug("Daily Routine sidebar panel registered")
    except ValueError:
        hass.data[_PANEL_REGISTERED] = True


async def async_unregister_panel(hass: HomeAssistant) -> None:
    """Remove the panel when the last instance is unloaded."""
    if not hass.config_entries.async_entries(DOMAIN):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
        hass.data[_PANEL_REGISTERED] = False
        _LOGGER.debug("Daily Routine sidebar panel removed")
