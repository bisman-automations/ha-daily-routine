"""Value triggers (sensor above/below a level), turn-off companions, daytime windows and notify names."""

from __future__ import annotations

from datetime import datetime

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.coordinator import (
    HomeRoutinesCoordinator,
    _companion_for,
    _within_hours,
    notify_targets,
)
from custom_components.daily_routine.routines import normalize_routine
from custom_components.daily_routine.runtime import get_coordinator
from custom_components.daily_routine.schema import vol
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import make_routine


def _at(local: str) -> datetime:
    return datetime.fromisoformat(local).replace(tzinfo=dt_util.get_default_time_zone())


def _humidity(**overrides):
    raw = {
        "id": "bath_fan",
        "name": "Bath fan",
        "trigger": "value_above",
        "value_entity": "sensor.bathroom_humidity",
        "value_threshold": 60,
        "off_threshold": 50,
        "actions": [
            {
                "service": "fan.turn_on",
                "target": {"entity_id": ["fan.bathroom_fan"]},
                "data": {},
            }
        ],
    }
    raw.update(overrides)
    return raw


def test_value_trigger_needs_a_sensor_and_level() -> None:
    raw = _humidity()
    del raw["value_entity"]
    with pytest.raises(vol.Invalid):
        normalize_routine(raw)
    with pytest.raises(vol.Invalid):
        normalize_routine(_humidity(value_threshold=None))


def test_turn_off_level_must_sit_below_turn_on_level() -> None:
    with pytest.raises(vol.Invalid):
        normalize_routine(_humidity(off_threshold=70))


def test_active_window_needs_both_ends() -> None:
    with pytest.raises(vol.Invalid):
        normalize_routine(_humidity(active_from="07:00"))
    routine = normalize_routine(_humidity(active_from="07:00", active_to="22:00"))
    assert routine["active_from"] == "07:00:00"
    assert routine["active_to"] == "22:00:00"


def test_companion_turns_the_fan_back_off_below_the_off_level() -> None:
    routine = normalize_routine(_humidity())
    companion = _companion_for(routine)
    assert companion is not None
    assert companion["id"] == "bath_fan_off"
    assert companion["trigger"] == "value_below"
    assert companion["value_threshold"] == 50
    assert companion["actions"][0]["service"] == "fan.turn_off"
    assert companion["companion_of"] == "bath_fan"


def test_no_companion_without_a_turn_off_level() -> None:
    assert _companion_for(normalize_routine(_humidity(off_threshold=None))) is None


def test_companion_refuses_services_it_cannot_reverse() -> None:
    raw = _humidity(
        actions=[{"service": "script.run_mode", "target": {}, "data": {}}],
    )
    with pytest.raises(vol.Invalid):
        _companion_for(normalize_routine(raw))


def test_daytime_window_includes_its_start_and_excludes_its_end() -> None:
    assert _within_hours(_at("2026-01-05T07:00"), "07:00:00", "22:00:00")
    assert not _within_hours(_at("2026-01-05T22:00"), "07:00:00", "22:00:00")
    assert not _within_hours(_at("2026-01-05T03:00"), "07:00:00", "22:00:00")


def test_window_can_cross_midnight() -> None:
    assert _within_hours(_at("2026-01-05T23:30"), "22:00:00", "06:00:00")
    assert _within_hours(_at("2026-01-05T05:00"), "22:00:00", "06:00:00")
    assert not _within_hours(_at("2026-01-05T12:00"), "22:00:00", "06:00:00")


def test_check_reports_outside_hours() -> None:
    coordinator = HomeRoutinesCoordinator.__new__(HomeRoutinesCoordinator)
    routine = make_routine(active_from="07:00:00", active_to="08:00:00")
    ok, reason = coordinator._check(routine, _at("2026-01-05T12:00"))  # noqa: SLF001
    assert ok is False
    assert reason == "outside_hours"


async def _setup(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, get_coordinator(hass)


async def test_companion_is_created_saved_and_removed(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(_humidity())
    ids = {r["id"] for r in coordinator.routines}
    assert {"bath_fan", "bath_fan_off"} <= ids

    await coordinator.async_delete_routine("bath_fan")
    ids = {r["id"] for r in coordinator.routines}
    assert "bath_fan" not in ids
    assert "bath_fan_off" not in ids

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_companion_name_clash_is_refused(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(id="bath_fan_off", name="Something else")
    )
    with pytest.raises(vol.Invalid):
        await coordinator.async_save_routine(_humidity())

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_crossing_the_level_runs_the_fan_service(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass.states.async_set(
        "sensor.bathroom_humidity", "48", {"unit_of_measurement": "%"}
    )
    calls = async_mock_service(hass, "fan", "turn_on")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(_humidity(off_threshold=None))
    await hass.async_block_till_done()

    hass.states.async_set(
        "sensor.bathroom_humidity", "62", {"unit_of_measurement": "%"}
    )
    await hass.async_block_till_done()
    assert len(calls) == 1

    hass.states.async_set(
        "sensor.bathroom_humidity", "63", {"unit_of_measurement": "%"}
    )
    await hass.async_block_till_done()
    assert len(calls) == 1, "staying above the level must not run it again"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def test_notify_targets_are_named_and_generic_services_hidden(
    hass: HomeAssistant,
) -> None:
    async_mock_service(hass, "notify", "mobile_app_alex_phone")
    async_mock_service(hass, "notify", "send_message")
    async_mock_service(hass, "notify", "notify")
    targets = notify_targets(hass)
    assert {"entity": "notify.mobile_app_alex_phone", "name": "Alex Phone"} in targets
    assert all(
        t["entity"] not in {"notify.send_message", "notify.notify"} for t in targets
    )


def test_phone_names_come_from_the_mobile_app_device(hass: HomeAssistant) -> None:
    """A phone is named by its device, read without the deprecated per-device config entry access."""
    from homeassistant.helpers import device_registry as dr

    phone_app = MockConfigEntry(domain="mobile_app")
    phone_app.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=phone_app.entry_id,
        identifiers={("mobile_app", "alex_phone")},
        name="Alex's iPhone",
    )
    async_mock_service(hass, "notify", "mobile_app_alex_s_iphone")
    assert {
        "entity": "notify.mobile_app_alex_s_iphone",
        "name": "Alex's iPhone",
    } in notify_targets(hass)
