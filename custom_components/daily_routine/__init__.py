"""Daily Routine – reusable, safety-aware daily routines for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import HomeRoutinesCoordinator
from .devices import async_ensure_hub
from .legacy import is_legacy, legacy_routines
from .schema import vol

_LOGGER = logging.getLogger(__name__)

# Set up from config entries only (the sidebar and devices are created from the entry).
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [Platform.SENSOR, Platform.SWITCH]
SERVICES = ("run_routine", "reload_routines")


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Config entries only."""
    return True


async def _async_migrate(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: HomeRoutinesCoordinator
) -> None:
    """Fold v1.0.0 per-routine entries into the single storage-backed entry."""
    if coordinator.migrated:
        return
    entries = hass.config_entries.async_entries(DOMAIN)
    for raw in legacy_routines(entries):
        try:
            coordinator.import_routine(raw)
        except vol.Invalid as err:
            _LOGGER.error("Could not migrate routine %s: %s", raw.get("id"), err)
    coordinator.migrated = True
    await coordinator.async_save_all()
    for other in entries:
        if other.entry_id != entry.entry_id and is_legacy(other):
            # Its routines now live in storage. Leave the empty entry for the user to delete.
            hass.config_entries.async_update_entry(
                other,
                title="Daily Routine (migrated – safe to delete)",
                data={},
                options={},
            )
    if is_legacy(entry):
        hass.config_entries.async_update_entry(
            entry, title="Daily Routine", data={}, options={}
        )


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Runs when an integration entry is edited or deleted in Home Assistant."""
    domain_data = hass.data.get(DOMAIN, {})
    coordinator = domain_data.get("coordinator")
    if coordinator is not None and domain_data.get("primary") == entry.entry_id:
        await coordinator.async_reconcile_subentries()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the single Daily Routine entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})

    if "coordinator" in domain_data and domain_data.get("primary") != entry.entry_id:
        # Leftover entry from v1.0.0. Its routines live in the primary entry; nothing to set up.
        return True

    if "coordinator" not in domain_data:
        domain_data["primary"] = entry.entry_id
        coordinator = HomeRoutinesCoordinator(hass)
        domain_data["coordinator"] = coordinator
        coordinator.attach_entry(entry.entry_id)
        await coordinator.async_setup()
        await _async_migrate(hass, entry, coordinator)
        await coordinator.async_start()
        await coordinator.async_sync_subentries()
        entry.async_on_unload(entry.add_update_listener(_async_entry_updated))

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

        from .panel import async_register_panel  # noqa: PLC0415

        await async_register_panel(hass)

    async_ensure_hub(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    domain_data = hass.data.get(DOMAIN, {})
    if domain_data.get("primary") != entry.entry_id:
        return True

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator: HomeRoutinesCoordinator | None = domain_data.pop("coordinator", None)
    domain_data.pop("primary", None)
    if coordinator is not None:
        await coordinator.async_unload()
    for service in SERVICES:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)

    from .panel import async_unregister_panel  # noqa: PLC0415

    await async_unregister_panel(hass)
    return unload_ok
