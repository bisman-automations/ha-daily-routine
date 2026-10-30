"""Extra conditions: all of them must hold, or any one of them."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.routines import normalize_routine
from custom_components.daily_routine.runtime import get_coordinator
from custom_components.daily_routine.schema import vol
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import make_routine

GUEST_OFF = {"entity_id": "input_boolean.guest_mode", "state": "off"}
GARAGE_UNLOCKED = {"entity_id": "lock.garage", "state": "unlocked"}


def test_conditions_default_to_all() -> None:
    assert (
        normalize_routine(make_routine(require_states=[GUEST_OFF]))["conditions_match"]
        == "all"
    )


def test_conditions_match_must_be_all_or_any() -> None:
    with pytest.raises(vol.Invalid):
        normalize_routine(
            make_routine(require_states=[GUEST_OFF], conditions_match="some")
        )


async def _setup(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, get_coordinator(hass)


async def test_all_mode_needs_every_condition(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass.states.async_set("input_boolean.guest_mode", "off")
    hass.states.async_set("lock.garage", "locked")
    entry, coordinator = await _setup(hass)
    routine = normalize_routine(
        make_routine(require_states=[GUEST_OFF, GARAGE_UNLOCKED])
    )
    now = dt_util.now()
    assert coordinator._check(routine, now) == (False, "extra_condition_not_met")  # noqa: SLF001

    hass.states.async_set("lock.garage", "unlocked")
    assert coordinator._check(routine, now) == (True, None)  # noqa: SLF001

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_any_mode_needs_just_one_condition(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass.states.async_set("input_boolean.guest_mode", "on")
    hass.states.async_set("lock.garage", "locked")
    entry, coordinator = await _setup(hass)
    routine = normalize_routine(
        make_routine(
            require_states=[GUEST_OFF, GARAGE_UNLOCKED], conditions_match="any"
        )
    )
    now = dt_util.now()
    assert coordinator._check(routine, now) == (False, "extra_condition_not_met")  # noqa: SLF001

    hass.states.async_set("lock.garage", "unlocked")
    assert coordinator._check(routine, now) == (True, None)  # noqa: SLF001

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
