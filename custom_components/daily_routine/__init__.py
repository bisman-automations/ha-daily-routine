"""Daily Routine – reusable, safety-aware daily routines for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .access import async_ensure_allowed
from .const import DOMAIN
from .coordinator import HomeRoutinesCoordinator
from .devices import async_ensure_hub
from .legacy import is_legacy, legacy_routines
from .runtime import DATA_PRIMARY, DailyRoutineConfigEntry, get_coordinator
from .schema import vol

_LOGGER = logging.getLogger(__name__)

# Set up from config entries only (the sidebar and devices are created from the entry).
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [Platform.SENSOR, Platform.SWITCH]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the actions once; they work while the entry is loaded."""

    def _coordinator() -> HomeRoutinesCoordinator:
        if (coordinator := get_coordinator(hass)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="not_loaded"
            )
        return coordinator

    async def run_routine_service(call: ServiceCall) -> None:
        coordinator = _coordinator()
        await async_ensure_allowed(hass, call.context)
        try:
            await coordinator.async_run(call.data["routine_id"])
        except vol.Invalid as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="run_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    async def reload_service(call: ServiceCall) -> None:
        coordinator = _coordinator()
        await async_ensure_allowed(hass, call.context)
        await coordinator.async_reload()

    hass.services.async_register(
        DOMAIN,
        "run_routine",
        run_routine_service,
        schema=vol.Schema({vol.Required("routine_id"): str}),
    )
    hass.services.async_register(DOMAIN, "reload_routines", reload_service)
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


async def _async_entry_updated(
    hass: HomeAssistant, entry: DailyRoutineConfigEntry
) -> None:
    """Runs when an integration entry is edited or deleted in Home Assistant."""
    if (coordinator := entry.runtime_data) is not None:
        await coordinator.async_reconcile_subentries()


async def async_setup_entry(
    hass: HomeAssistant, entry: DailyRoutineConfigEntry
) -> bool:
    """Set up the single Daily Routine entry."""
    primary = hass.data.setdefault(DATA_PRIMARY, entry.entry_id)
    if primary != entry.entry_id:
        # Leftover entry from v1.0.0. Its routines live in the primary entry; nothing to set up.
        entry.runtime_data = None
        return True

    coordinator = HomeRoutinesCoordinator(hass)
    coordinator.attach_entry(entry.entry_id)
    loaded = False
    try:
        await coordinator.async_setup()
        loaded = True
        await _async_migrate(hass, entry, coordinator)
        await coordinator.async_start()
        await coordinator.async_sync_subentries()
        entry.runtime_data = coordinator

        from .panel import async_register_panel  # noqa: PLC0415

        await async_register_panel(hass)
        async_ensure_hub(hass, entry)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        # Leave nothing running, so the next attempt starts clean. Stored
        # routines are only written back if they were read in full.
        await coordinator.async_unload(save=loaded)
        hass.data.pop(DATA_PRIMARY, None)
        from .panel import async_unregister_panel  # noqa: PLC0415

        await async_unregister_panel(hass)
        raise
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: DailyRoutineConfigEntry
) -> bool:
    coordinator = entry.runtime_data
    if coordinator is None:
        return True

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    await coordinator.async_unload()
    hass.data.pop(DATA_PRIMARY, None)

    from .panel import async_unregister_panel  # noqa: PLC0415

    await async_unregister_panel(hass)
    return True
