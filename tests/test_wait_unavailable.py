"""Wait for an unavailable to-do list: hold the run, run it when the list is back."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.daily_routine.config_flow import _routine_from_form
from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.coordinator import WAIT_UNAVAILABLE_HOURS
from custom_components.daily_routine.routines import normalize_routine
from custom_components.daily_routine.runtime import get_coordinator
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from tests.test_todo_rules import GROCERIES, FakeTodo

WASHER = "sensor.washer_state"


def _wash(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": "wash_towels",
        "name": "Wash towels",
        "trigger": "state_is",
        "watch_entity": WASHER,
        "watch_states": ["Run"],
        "repeat": True,
        "notify_on_health": True,
        "notify_on_skip": True,
        "wait_when_unavailable": True,
        "actions": [
            {"todo": {"op": "complete", "list": GROCERIES, "item": "Wash towels"}}
        ],
    }
    raw.update(overrides)
    return raw


async def _setup(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = get_coordinator(hass)
    await coordinator.async_set_settings(
        {
            **coordinator.settings,
            "startup_quiet_minutes": 0,
            "list_wait_seconds": 0,
            "health_alert_seconds": 60,
        }
    )
    sent: list[str] = []
    coordinator._send_notification = lambda message, *_: sent.append(message)  # type: ignore[method-assign]
    return coordinator, sent


def test_wait_when_unavailable_defaults_off() -> None:
    routine = normalize_routine(_wash(wait_when_unavailable=None))
    assert routine["wait_when_unavailable"] is False
    assert normalize_routine(_wash())["wait_when_unavailable"] is True


def test_the_routine_form_keeps_the_option() -> None:
    routine = normalize_routine(_wash())
    merged = _routine_from_form(
        routine,
        {
            "trigger": "state_is",
            "time": "07:00",
            "watch_entity": WASHER,
            "wait_when_unavailable": False,
        },
        routine["actions"],
        [],
    )
    assert merged["wait_when_unavailable"] is False


async def test_a_run_waits_for_the_list_and_runs_when_it_is_back(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    todo = FakeTodo(
        hass, [{"uid": "u1", "summary": "Wash towels", "status": "needs_action"}]
    )
    hass.states.async_set(WASHER, "Idle")
    hass.states.async_set(GROCERIES, "unavailable")
    coord, sent = await _setup(hass)
    await coord.async_save_routine(_wash())
    await hass.async_block_till_done()

    hass.states.async_set(WASHER, "Run")
    await hass.async_block_till_done()
    status = coord.get_status("wash_towels")
    assert status["held"] is True
    assert status["state"] == "held"
    assert status["waiting_for"] == [GROCERIES]
    assert todo.updates == []

    # The regular check does not ask for attention while the list is only unavailable.
    coord._check_health(None)  # type: ignore[arg-type]
    await hass.async_block_till_done()
    assert not [m for m in sent if "needs attention" in m]
    assert "Waiting for it to come back" in coord.health(coord._find("wash_towels"))[0]

    hass.states.async_set(GROCERIES, "1")
    await hass.async_block_till_done()
    assert [u["item"] for u in todo.updates] == ["u1"]
    status = coord.get_status("wash_towels")
    assert status["held"] is False
    assert status["last_result"] == "success"
    assert sent == []


async def test_without_the_option_the_run_goes_ahead_as_before(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    FakeTodo(hass, [])
    hass.states.async_set(WASHER, "Idle")
    hass.states.async_set(GROCERIES, "unavailable")
    coord, sent = await _setup(hass)
    await coord.async_save_routine(_wash(wait_when_unavailable=False))
    await hass.async_block_till_done()

    hass.states.async_set(WASHER, "Run")
    await hass.async_block_till_done()
    status = coord.get_status("wash_towels")
    assert status["held"] is False
    assert status["last_run"] is not None


async def test_a_list_that_stays_away_is_announced_after_a_day(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, freezer
) -> None:
    FakeTodo(hass, [])
    hass.states.async_set(WASHER, "Idle")
    hass.states.async_set(GROCERIES, "1")
    coord, sent = await _setup(hass)
    await coord.async_save_routine(_wash())
    await hass.async_block_till_done()

    hass.states.async_set(GROCERIES, "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set(WASHER, "Run")
    await hass.async_block_till_done()
    assert coord.get_status("wash_towels")["held"] is True

    freezer.tick(timedelta(hours=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    coord._check_health(None)  # type: ignore[arg-type]
    await hass.async_block_till_done()
    assert not [m for m in sent if "needs attention" in m]

    freezer.tick(timedelta(hours=WAIT_UNAVAILABLE_HOURS))
    coord._check_health(None)  # type: ignore[arg-type]
    await hass.async_block_till_done()
    assert len([m for m in sent if "needs attention" in m]) == 1
    assert len([m for m in sent if "did not come back" in m]) == 1
    status = coord.get_status("wash_towels")
    assert status["held"] is False
    assert status["last_result"] == "unavailable_too_long"


async def test_a_missing_list_is_still_announced(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    FakeTodo(hass, [])
    hass.states.async_set(WASHER, "Idle")
    coord, sent = await _setup(hass)
    await coord.async_save_routine(_wash())
    await hass.async_block_till_done()
    coord._check_health(None)  # type: ignore[arg-type]
    await hass.async_block_till_done()
    assert len([m for m in sent if "needs attention" in m]) == 1

    hass.states.async_set(WASHER, "Run")
    await hass.async_block_till_done()
    assert coord.get_status("wash_towels")["held"] is False


async def test_the_home_assistant_notify_target_does_not_make_a_second_card(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    created = async_mock_service(hass, "persistent_notification", "create")
    notified = async_mock_service(hass, "notify", "persistent_notification")
    phone = async_mock_service(hass, "notify", "mobile_app_phone")
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = get_coordinator(hass)
    await coord.async_set_settings(
        {
            **coord.settings,
            "notify_services": [
                "notify.persistent_notification",
                "notify.mobile_app_phone",
            ],
        }
    )
    await coord._notify("Hello", "daily_routine_health_x")
    await hass.async_block_till_done()
    assert len(created) == 1
    assert notified == []
    assert len(phone) == 1
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
