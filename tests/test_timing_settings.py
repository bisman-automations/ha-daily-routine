"""Global timing settings on the Settings tab: defaults, limits and how a save takes effect."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.coordinator import TIMING_SETTINGS
from custom_components.daily_routine.schema import vol
from homeassistant.core import HomeAssistant


async def _setup(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN]["coordinator"]


async def test_timing_settings_start_at_their_defaults(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    coord = await _setup(hass)
    for key, (default, _low, _high, _label) in TIMING_SETTINGS.items():
        assert coord.settings[key] == default


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("list_wait_seconds", -1),
        ("list_wait_seconds", 301),
        ("health_check_minutes", 0),
        ("health_check_minutes", 61),
        ("health_alert_seconds", 601),
        ("startup_quiet_minutes", 61),
        ("startup_quiet_minutes", "5"),
        ("startup_quiet_minutes", True),
        ("startup_quiet_minutes", 2.5),
    ],
)
async def test_timing_settings_reject_values_outside_their_limits(
    hass: HomeAssistant,
    enable_custom_integrations,
    hass_storage,
    key: str,
    value: object,
) -> None:
    coord = await _setup(hass)
    before = dict(coord.settings)
    with pytest.raises(vol.Invalid):
        await coord.async_set_settings({**coord.settings, key: value})
    assert coord.settings == before, "a rejected save changes nothing"


async def test_saved_timing_settings_are_kept(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    coord = await _setup(hass)
    saved = await coord.async_set_settings(
        {
            **coord.settings,
            "list_wait_seconds": 45,
            "health_check_minutes": 30,
            "health_alert_seconds": 0,
            "startup_quiet_minutes": 0,
        }
    )
    assert saved["list_wait_seconds"] == 45
    assert saved["health_check_minutes"] == 30
    assert saved["health_alert_seconds"] == 0
    assert saved["startup_quiet_minutes"] == 0


async def test_saving_timing_settings_restarts_the_health_check(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    coord = await _setup(hass)
    first = coord._health_unsub
    await coord.async_set_settings({**coord.settings, "health_check_minutes": 20})
    assert coord._health_unsub is not None
    assert coord._health_unsub is not first, (
        "the interval is scheduled again at the new value"
    )
