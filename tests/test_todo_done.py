"""A to-do item checked off runs a routine; a health problem is announced once."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.routines import normalize_routine
from custom_components.daily_routine.schema import vol

GROCERIES = "todo.groceries"
LIGHT_STEP = {"service": "light.turn_on", "target": {"entity_id": ["light.hall"]}, "data": {}}


def _checked(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": "milk_checked",
        "name": "Milk checked",
        "trigger": "todo_done",
        "watch_entity": GROCERIES,
        "actions": [LIGHT_STEP],
    }
    raw.update(overrides)
    return raw


class FakeList:
    """Stands in for a to-do list: its items can be checked off, and it reports them."""

    def __init__(self, hass: HomeAssistant, items: list[dict[str, str]]) -> None:
        self.items = [dict(item) for item in items]
        hass.services.async_register(
            "todo", "get_items", self._get, supports_response=SupportsResponse.ONLY
        )

    async def _get(self, call: ServiceCall) -> dict[str, Any]:
        wanted = call.data.get("status") or []
        items = [i for i in self.items if not wanted or i["status"] in wanted]
        return {call.data["entity_id"]: {"items": items}}

    def check_off(self, hass: HomeAssistant, summary: str, count: str) -> None:
        for item in self.items:
            if item["summary"] == summary:
                item["status"] = "completed"
        hass.states.async_set(GROCERIES, count)


async def _setup(hass: HomeAssistant, *, quiet_minutes: int = 0):
    """Start the integration. Tests end the startup quiet period unless told otherwise."""
    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN]["coordinator"]
    await coordinator.async_set_settings(
        {**coordinator.settings, "startup_quiet_minutes": quiet_minutes}
    )
    return coordinator


async def test_health_alerts_wait_for_the_startup_quiet_period(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    coord = await _setup(hass, quiet_minutes=5)
    await coord.async_save_routine(
        _checked(
            id="lamp_check",
            name="Lamp check",
            watch_entity="input_boolean.lamp_ok",
            trigger="state_is",
            watch_states=["on"],
            notify_on_health=True,
        )
    )
    sent: list[str] = []
    coord._send_notification = sent.append  # type: ignore[method-assign]

    coord._check_health(None)  # type: ignore[arg-type]
    assert sent == [], "a device may still be loading during startup, so no alert yet"

    await coord.async_set_settings({**coord.settings, "startup_quiet_minutes": 0})
    coord._check_health(None)  # type: ignore[arg-type]
    assert len(sent) == 1 and "Lamp check" in sent[0]


def test_checked_off_trigger_needs_a_to_do_list() -> None:
    with pytest.raises(vol.Invalid):
        normalize_routine(_checked(watch_entity="binary_sensor.dishwasher_clean"))
    kept = normalize_routine(_checked(watch_item="  Milk   carton "))
    assert kept["watch_item"] == "Milk carton"
    assert kept["watch_states"] == []
    assert kept["hold_minutes"] == 0
    assert normalize_routine(_checked())["watch_item"] == ""


def test_checked_off_item_name_has_a_limit() -> None:
    with pytest.raises(vol.Invalid):
        normalize_routine(_checked(watch_item="x" * 300))


def test_health_notification_is_off_unless_chosen() -> None:
    assert normalize_routine(_checked())["notify_on_health"] is False


async def test_checking_off_the_named_item_runs_the_routine_once(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    calls = async_mock_service(hass, "light", "turn_on")
    items = FakeList(
        hass,
        [
            {"uid": "1", "summary": "Milk", "status": "needs_action"},
            {"uid": "2", "summary": "Eggs", "status": "needs_action"},
        ],
    )
    hass.states.async_set(GROCERIES, "2")
    coord = await _setup(hass)
    await coord.async_save_routine(_checked(watch_item="milk"))
    await hass.async_block_till_done()

    items.check_off(hass, "Eggs", "1")
    await hass.async_block_till_done()
    assert calls == [], "another item does not run it"

    items.check_off(hass, "Milk", "0")
    await hass.async_block_till_done()
    assert len(calls) == 1

    hass.states.async_set(GROCERIES, "0", {"refreshed": True})
    await hass.async_block_till_done()
    assert len(calls) == 1, "nothing new was checked off, so it does not run again"


async def test_items_already_done_when_saved_do_not_run_it(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    calls = async_mock_service(hass, "light", "turn_on")
    items = FakeList(
        hass,
        [
            {"uid": "1", "summary": "Milk", "status": "completed"},
            {"uid": "2", "summary": "Eggs", "status": "needs_action"},
        ],
    )
    hass.states.async_set(GROCERIES, "1")
    coord = await _setup(hass)
    await coord.async_save_routine(_checked())
    await hass.async_block_till_done()
    assert calls == []

    items.check_off(hass, "Eggs", "0")
    await hass.async_block_till_done()
    assert len(calls) == 1, "with no name, any newly checked item runs it"


async def test_health_problem_is_announced_once_and_again_after_it_clears(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    coord = await _setup(hass)
    await coord.async_save_routine(
        _checked(
            id="lamp_check",
            name="Lamp check",
            watch_entity="input_boolean.lamp_ok",
            trigger="state_is",
            watch_states=["on"],
            notify_on_health=True,
        )
    )
    await coord.async_save_routine(
        _checked(
            id="quiet_check",
            name="Quiet check",
            watch_entity="input_boolean.lamp_ok",
            trigger="state_is",
            watch_states=["on"],
            notify_on_health=False,
        )
    )
    sent: list[str] = []
    coord._send_notification = sent.append  # type: ignore[method-assign]

    coord._check_health(None)  # type: ignore[arg-type]
    coord._check_health(None)  # type: ignore[arg-type]
    assert len(sent) == 1
    assert "Lamp check" in sent[0] and "is not in Home Assistant" in sent[0]

    hass.states.async_set("input_boolean.lamp_ok", "unavailable")
    coord._check_health(None)  # type: ignore[arg-type]
    assert len(sent) == 1, "still a problem, so no second message"

    hass.states.async_set("input_boolean.lamp_ok", "off")
    coord._check_health(None)  # type: ignore[arg-type]
    hass.states.async_set("input_boolean.lamp_ok", "unavailable")
    coord._check_health(None)  # type: ignore[arg-type]
    assert len(sent) == 2 and "Lamp check" in sent[1]
    assert all("Quiet check" not in message for message in sent)


async def test_item_checked_off_while_stopped_runs_once_after_startup(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    calls = async_mock_service(hass, "light", "turn_on")
    items = FakeList(
        hass,
        [
            {"uid": "1", "summary": "Milk", "status": "needs_action"},
            {"uid": "2", "summary": "Eggs", "status": "needs_action"},
        ],
    )
    hass.states.async_set(GROCERIES, "2")
    coord = await _setup(hass)
    await coord.async_save_routine(_checked())
    await hass.async_block_till_done()
    assert calls == []

    # Checked off with no state change seen, as if Home Assistant were stopped.
    items.items[0]["status"] = "completed"
    items.items[1]["status"] = "completed"
    coord._check_done_after_startup()
    await hass.async_block_till_done()
    assert len(calls) == 1, "two items done while stopped run the routine once"

    coord._check_done_after_startup()
    await hass.async_block_till_done()
    assert len(calls) == 1, "the next startup does not run it again for the same items"


async def test_completed_items_are_saved_for_the_next_start(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    async_mock_service(hass, "light", "turn_on")
    FakeList(hass, [{"uid": "1", "summary": "Milk", "status": "needs_action"}])
    hass.states.async_set(GROCERIES, "1")
    coord = await _setup(hass)
    await coord.async_save_routine(_checked())
    await hass.async_block_till_done()
    assert "seen_done" in str(coord._payload())


async def test_brief_unavailable_does_not_announce_but_a_lasting_one_does(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    async_mock_service(hass, "light", "turn_on")
    hass.states.async_set("input_boolean.lamp_ok", "on")
    coord = await _setup(hass)
    await coord.async_save_routine(
        _checked(
            id="lamp_watch",
            name="Lamp watch",
            watch_entity="input_boolean.lamp_ok",
            trigger="state_is",
            watch_states=["on"],
            notify_on_health=True,
        )
    )
    await hass.async_block_till_done()
    sent: list[str] = []
    coord._send_notification = sent.append  # type: ignore[method-assign]

    now = dt_util.utcnow()
    hass.states.async_set("input_boolean.lamp_ok", "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set("input_boolean.lamp_ok", "on")
    await hass.async_block_till_done()
    async_fire_time_changed(hass, now + timedelta(seconds=120))
    await hass.async_block_till_done()
    health = [m for m in sent if "needs attention" in m]
    assert health == [], "back within the grace period, so nothing is announced"

    now = dt_util.utcnow()
    hass.states.async_set("input_boolean.lamp_ok", "unavailable")
    await hass.async_block_till_done()
    async_fire_time_changed(hass, now + timedelta(seconds=120))
    await hass.async_block_till_done()
    health = [m for m in sent if "needs attention" in m]
    assert len(health) == 1 and "Lamp watch" in health[0]
