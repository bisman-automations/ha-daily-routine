"""Integration entries per routine, catch-up after restart, diagnostics, and the area index."""

from __future__ import annotations

from datetime import timedelta

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.daily_routine.const import DOMAIN, ROUTINE_SUBENTRY
from custom_components.daily_routine.coordinator import (
    _entities_by_area,
    area_entity_ids,
)
from custom_components.daily_routine.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.util import dt as dt_util

from .conftest import make_routine


async def _setup(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, hass.data[DOMAIN]["coordinator"]


def _subentries(entry):
    return {
        s.unique_id: s
        for s in entry.subentries.values()
        if s.subentry_type == ROUTINE_SUBENTRY
    }


def _at_utc(local: str):
    """A local (harness time zone) moment, expressed in UTC for the freezer."""
    from datetime import datetime

    return (
        datetime.fromisoformat(local)
        .replace(tzinfo=dt_util.get_default_time_zone())
        .astimezone(dt_util.UTC)
    )


# ----- integration entries -----


async def test_each_dashboard_routine_gets_an_entry_and_companions_do_not(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(id="kitchen", name="Kitchen lights")
    )
    await coordinator.async_save_routine(
        make_routine(
            id="bath_fan",
            name="Bath fan",
            trigger="value_above",
            value_entity="sensor.bathroom_humidity",
            value_threshold=60,
            off_threshold=50,
            actions=[
                {
                    "service": "fan.turn_on",
                    "target": {"entity_id": ["fan.bath"]},
                    "data": {},
                }
            ],
        )
    )
    await hass.async_block_till_done()
    subs = _subentries(entry)
    assert set(subs) == {"kitchen", "bath_fan"}
    assert subs["kitchen"].title == "Kitchen lights"
    assert subs["kitchen"].data["routine_id"] == "kitchen"
    assert "bath_fan_off" not in subs
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_deleting_a_routine_removes_its_entry(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(id="kitchen", name="Kitchen lights")
    )
    await coordinator.async_delete_routine("kitchen")
    await hass.async_block_till_done()
    assert _subentries(entry) == {}
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_removing_an_entry_in_home_assistant_deletes_the_routine(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(id="kitchen", name="Kitchen lights")
    )
    await hass.async_block_till_done()
    sub = _subentries(entry)["kitchen"]

    assert hass.config_entries.async_remove_subentry(entry, sub.subentry_id)
    await hass.async_block_till_done()
    assert coordinator.get_routine("kitchen") is None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_editing_an_entry_in_home_assistant_updates_the_routine(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(id="kitchen", name="Kitchen lights")
    )
    await hass.async_block_till_done()
    sub = _subentries(entry)["kitchen"]

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ROUTINE_SUBENTRY),
        context={"source": "reconfigure", "subentry_id": sub.subentry_id},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Renamed kitchen",
            "enabled": True,
            "trigger": "time",
            "time": "06:30",
            "zone": "zone.home",
            "actions": "light.turn_off | light.hall",
        },
    )
    assert result["type"] == "abort"
    assert result["reason"] == "updated"
    await hass.async_block_till_done()

    routine = coordinator.get_routine("kitchen")
    assert routine is not None
    assert routine["name"] == "Renamed kitchen"
    assert routine["time"] == "06:30:00"
    assert _subentries(entry)["kitchen"].title == "Renamed kitchen"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_bad_steps_in_the_entry_form_show_an_error(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(id="kitchen", name="Kitchen lights")
    )
    await hass.async_block_till_done()
    sub = _subentries(entry)["kitchen"]

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, ROUTINE_SUBENTRY),
        context={"source": "reconfigure", "subentry_id": sub.subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Kitchen lights",
            "enabled": True,
            "trigger": "time",
            "time": "06:30",
            "zone": "zone.home",
            "actions": "light.turn_off | light.hall | {not json",
        },
    )
    assert result["type"] == "form"
    assert result["errors"] == {"actions": "invalid_steps"}
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# ----- catch-up after a restart -----


async def test_missed_routine_catches_up_inside_its_window(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, freezer
) -> None:
    calls = async_mock_service(hass, "light", "turn_off")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(id="kitchen", name="Kitchen", catch_up_minutes=60)
    )
    await hass.async_block_till_done()

    freezer.move_to(_at_utc("2026-01-05T07:20:00"))
    coordinator._maybe_catch_up(coordinator.get_routine("kitchen"), dt_util.now())  # noqa: SLF001
    await hass.async_block_till_done()
    assert len(calls) == 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_missed_routine_does_not_catch_up_after_its_window(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, freezer
) -> None:
    calls = async_mock_service(hass, "light", "turn_off")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(id="kitchen", name="Kitchen", catch_up_minutes=60)
    )
    await hass.async_block_till_done()

    freezer.move_to(_at_utc("2026-01-05T08:05:00") + timedelta(seconds=0))
    coordinator._maybe_catch_up(coordinator.get_routine("kitchen"), dt_util.now())  # noqa: SLF001
    await hass.async_block_till_done()
    assert calls == []

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# ----- diagnostics -----


async def test_diagnostics_include_routines_and_redact_phones(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    async_mock_service(hass, "notify", "mobile_app_alex_phone")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        make_routine(id="kitchen", name="Kitchen lights")
    )
    await coordinator.async_set_settings(
        {"hidden_people": [], "notify_services": ["notify.mobile_app_alex_phone"]}
    )
    await hass.async_block_till_done()

    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["loaded"] is True
    assert [r["id"] for r in data["routines"]] == ["kitchen"]
    assert data["settings"]["notify_services"] == "**REDACTED**"
    assert "kitchen" in data["statuses"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# ----- area index -----


async def test_area_index_matches_the_per_area_lookup(hass: HomeAssistant) -> None:
    areas = ar.async_get(hass)
    kitchen = areas.async_create("Kitchen")
    bedroom = areas.async_create("Bedroom")
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    owner = MockConfigEntry(domain="test")
    owner.add_to_hass(hass)

    device = dev_reg.async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("test", "lamp")}
    )
    dev_reg.async_update_device(device.id, area_id=kitchen.id)
    lamp = ent_reg.async_get_or_create(
        "light", "test", "lamp", device_id=device.id, original_name="Lamp"
    )
    moved = ent_reg.async_get_or_create(
        "light", "test", "moved", device_id=device.id, original_name="Moved"
    )
    ent_reg.async_update_entity(moved.entity_id, area_id=bedroom.id)
    ent_reg.async_get_or_create("switch", "test", "loose", original_name="Loose")

    index = _entities_by_area(hass)
    for area in (kitchen, bedroom):
        assert index.get(area.id, []) == area_entity_ids(hass, area.id)
    assert lamp.entity_id in index[kitchen.id]
    assert moved.entity_id in index[bedroom.id]
    assert moved.entity_id not in index[kitchen.id]
