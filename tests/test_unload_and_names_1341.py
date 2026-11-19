"""1.34.1: unloading stops what it should, entities are named the Home Assistant
way, and the diagnostics download leaves out every secret.

- A run in progress stops when Daily Routine unloads (a reload, turning it off),
  and is recorded as stopped part way, instead of carrying on from the
  unloaded coordinator and saving its old routines over the new ones.
- The Activity log, device reliability and habits are written on unload, so a
  reload doesn't lose what happened just before it.
- The sidebar entry goes away while Daily Routine isn't running.
- Status sensors take their name from the routine's device plus "Status"
  (no more "Morning Morning status" on Home Assistant 2026.9+), and a new
  routine's sensor ID carries the routine's name.
- Diagnostics redact the guest Wi-Fi card, step codes and tokens, and phone or
  tracker ids in the settings too.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.daily_routine import coordinator as coordinator_module
from custom_components.daily_routine.const import DOMAIN, EVENT_ROUTINE_FINISHED
from custom_components.daily_routine.coordinator import STOPPED_BY_UNLOAD
from custom_components.daily_routine.diagnostics import (
    REDACTED,
    async_get_config_entry_diagnostics,
)
from custom_components.daily_routine.runtime import get_coordinator
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

COMPONENT = Path(__file__).parent.parent / "custom_components" / "daily_routine"
PANELS = "frontend_panels"

SLOW = {
    "id": "slow",
    "name": "Slow lights",
    "trigger": "time",
    "time": "07:00:00",
    "actions": [
        {"delay": 600},
        {"service": "light.turn_off", "target": {"entity_id": ["light.hall"]}},
    ],
}


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
def held_delays(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    """Delay steps of a minute or more wait for this event instead of the clock."""
    gate = asyncio.Event()
    real_sleep = asyncio.sleep

    async def sleep(seconds: float, *args: Any) -> None:
        if seconds >= 60:
            await gate.wait()
        else:
            await real_sleep(0)

    fake = SimpleNamespace(
        **{name: getattr(asyncio, name) for name in dir(asyncio) if name[0] != "_"}
    )
    fake.sleep = sleep
    monkeypatch.setattr(coordinator_module, "asyncio", fake)
    return gate


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


# ---- runs in progress stop on unload -----------------------------------------


async def test_a_run_in_progress_stops_on_reload_and_says_so(
    hass: HomeAssistant, enable_custom_integrations, held_delays: asyncio.Event
) -> None:
    calls = async_mock_service(hass, "light", "turn_off")
    finished: list[Event] = []

    @callback
    def _finished(event: Event) -> None:
        finished.append(event)

    hass.bus.async_listen(EVENT_ROUTINE_FINISHED, _finished)
    entry = await _setup(hass)
    coordinator = get_coordinator(hass)
    await coordinator.async_save_routine(SLOW)
    await hass.async_block_till_done()

    run = hass.async_create_task(coordinator.async_run("slow"))
    await _settle()
    assert coordinator.get_status("slow")["state"] == "running"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    # The caller (here the run_routine action's path) ends without a result.
    assert await run is False

    # The old run doesn't come back to life when its delay would have ended.
    held_delays.set()
    await hass.async_block_till_done()
    assert not calls

    assert [e.data["result"] for e in finished] == ["stopped"]
    new = get_coordinator(hass)
    assert new is not coordinator
    status = new.get_status("slow")
    # Written before unloading saved, so the new coordinator shows it.
    assert status["last_result"] == "stopped"
    assert status["last_reason"] == STOPPED_BY_UNLOAD
    stopped = [e for e in new.run_log.view() if e.get("routine_id") == "slow"]
    assert stopped
    assert STOPPED_BY_UNLOAD in json.dumps(stopped[-1])
    assert not coordinator._step_tasks  # noqa: SLF001


async def test_the_run_routine_action_returns_when_the_entry_unloads(
    hass: HomeAssistant, enable_custom_integrations, held_delays: asyncio.Event
) -> None:
    calls = async_mock_service(hass, "light", "turn_off")
    entry = await _setup(hass)
    await get_coordinator(hass).async_save_routine(SLOW)
    await hass.async_block_till_done()

    action = hass.async_create_task(
        hass.services.async_call(
            DOMAIN, "run_routine", {"routine_id": "slow"}, blocking=True
        )
    )
    await _settle()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await asyncio.wait_for(action, 5)  # No error, no hang.
    held_delays.set()
    await hass.async_block_till_done()
    assert not calls


async def test_steps_dont_start_once_unloading_began(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """A run that reaches its steps after unloading (an "Ask me first" answered
    late) does nothing."""
    calls = async_mock_service(hass, "light", "turn_off")
    entry = await _setup(hass)
    coordinator = get_coordinator(hass)
    await coordinator.async_save_routine({**SLOW, "actions": SLOW["actions"][1:]})
    await hass.async_block_till_done()
    routine = coordinator.get_routine("slow")
    assert await hass.config_entries.async_unload(entry.entry_id)

    run = await coordinator._run_steps(  # noqa: SLF001
        routine,
        [(0, routine["actions"][0], None)],
        coordinator_module.dt_util.now(),
        scheduled=True,
    )
    await hass.async_block_till_done()
    assert run is None
    assert not calls


async def test_a_cancelled_caller_is_not_swallowed(
    hass: HomeAssistant, enable_custom_integrations, held_delays: asyncio.Event
) -> None:
    """Cancelling whoever started the run (Home Assistant stopping) still
    cancels it; only a stop by unloading turns into "no result"."""
    await _setup(hass)
    coordinator = get_coordinator(hass)
    await coordinator.async_save_routine(SLOW)
    await hass.async_block_till_done()
    run = hass.async_create_task(coordinator.async_run("slow"))
    await _settle()
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    await _settle()
    assert not coordinator._step_tasks  # noqa: SLF001


# ---- recent history survives a reload ------------------------------------------


async def test_activity_and_reliability_survive_a_reload(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    async_mock_service(hass, "light", "turn_off")
    entry = await _setup(hass)
    coordinator = get_coordinator(hass)
    await coordinator.async_save_routine({**SLOW, "actions": SLOW["actions"][1:]})
    assert await coordinator.async_run("slow")
    coordinator.device_stats.record("light.hall", "failed", "offline")
    await hass.async_block_till_done()
    ran = len(coordinator.run_log.view())
    assert ran

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    new = get_coordinator(hass)
    assert len(new.run_log.view()) == ran
    assert new.device_stats.last_problem["light.hall"]["reason"] == "offline"


async def test_habits_are_written_on_unload_only_when_changed(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    entry = await _setup(hass)
    habits = get_coordinator(hass).habits
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert f"{DOMAIN}.habits" not in hass_storage

    assert await hass.config_entries.async_setup(entry.entry_id)
    habits = get_coordinator(hass).habits
    habits.events.append({"e": "light.hall", "s": "on", "t": 1})
    habits._dirty = True  # noqa: SLF001
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert hass_storage[f"{DOMAIN}.habits"]["data"]["events"] == [
        {"e": "light.hall", "s": "on", "t": 1}
    ]


# ---- the sidebar entry --------------------------------------------------------


async def test_sidebar_entry_goes_away_while_not_running(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    entry = await _setup(hass)
    assert "daily-routine" in hass.data[PANELS]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert "daily-routine" not in hass.data[PANELS]

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert "daily-routine" in hass.data[PANELS]

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert "daily-routine" in hass.data[PANELS]


# ---- entity names ---------------------------------------------------------------


async def test_status_sensor_is_named_after_its_routine_device(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    await _setup(hass)
    coordinator = get_coordinator(hass)
    await coordinator.async_save_routine({**SLOW, "name": "Morning covers"})
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_slow_status")
    # Made after the routine's device, so the routine's name is in the ID.
    assert entity_id == "sensor.morning_covers_status"
    entity = registry.async_get(entity_id)
    assert entity.has_entity_name
    assert entity.translation_key == "status"
    assert entity.device_id is not None
    # Not "Morning covers Morning covers status" any more.
    assert hass.states.get(entity_id).attributes["friendly_name"] == (
        "Morning covers Status"
    )

    # Renaming the routine renames its device, and with it the sensor's name.
    await coordinator.async_save_routine({**SLOW, "name": "Evening covers"})
    await hass.async_block_till_done()
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_slow_status") == (
        entity_id
    )
    assert hass.states.get(entity_id).attributes["friendly_name"] == (
        "Evening covers Status"
    )


async def test_existing_entity_ids_are_kept(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    """Upgrading keeps every ID already in the registry, the old prefixed ones too."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_slow_status",
        suggested_object_id="daily_routine_slow_lights_status",
        config_entry=entry,
    )
    registry.async_get_or_create(
        "switch",
        DOMAIN,
        f"{DOMAIN}_paused",
        suggested_object_id="pause_all_routines",
        config_entry=entry,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await get_coordinator(hass).async_save_routine(SLOW)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.daily_routine_slow_lights_status")
    assert state is not None
    assert state.attributes["friendly_name"] == "Slow lights Status"
    assert hass.states.get("switch.pause_all_routines") is not None


async def test_house_entities_keep_their_ids_and_names(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    await _setup(hass)
    registry = er.async_get(hass)
    for entity_id, name, key in (
        ("switch.pause_all_routines", "Pause all routines", "pause_all"),
        ("select.house_mode", "House mode", "house_mode"),
        ("sensor.kind_of_day", "Kind of day", "kind_of_day"),
    ):
        entity = registry.async_get(entity_id)
        assert entity is not None, entity_id
        assert entity.has_entity_name
        assert entity.translation_key == key
        assert hass.states.get(entity_id).attributes["friendly_name"] == name


def _json(name: str) -> dict[str, Any]:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))


def test_translations_and_icons_cover_every_entity_and_state() -> None:
    strings = _json("strings.json")
    assert strings == _json("translations/en.json")
    icons = _json("icons.json")
    entity = strings["entity"]
    for platform, keys in entity.items():
        assert set(keys) == set(icons["entity"][platform]), platform
    # Every state a routine can be in has words on the status sensor.
    source = (COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    states = set(re.findall(r'rt\["state"\] = "([a-z_]+)"', source))
    assert states
    assert states <= set(entity["sensor"]["status"]["state"])
    # Every House mode has words.
    assert set(coordinator_module.MODES) == set(entity["select"]["house_mode"]["state"])
    # Every action has an icon.
    services = re.findall(
        r"^([a-z_]+):", (COMPONENT / "services.yaml").read_text(), re.MULTILINE
    )
    assert set(services) == set(icons["services"])
    for platform in ("sensor.py", "switch.py", "select.py"):
        text = (COMPONENT / platform).read_text(encoding="utf-8")
        assert "_attr_icon" not in text, platform
        assert "_attr_name" not in text, platform
        assert "has_entity_name = False" not in text, platform


# ---- diagnostics ----------------------------------------------------------------


async def test_diagnostics_leave_out_secrets_everywhere(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    async_mock_service(hass, "alarm_control_panel", "alarm_arm_away")
    entry = await _setup(hass)
    coordinator = get_coordinator(hass)
    coordinator.settings["guest_wifi"] = {"ssid": "Visitors", "password": "hunter22"}
    coordinator.settings["daily_summary"] = {
        **coordinator.settings["daily_summary"],
        "extra": [{"to": "notify.mobile_app_alex"}, "device_tracker.alex_phone"],
    }
    await coordinator.async_save_routine(
        {
            "id": "arm",
            "name": "Arm when leaving",
            "trigger": "time",
            "time": "08:00:00",
            "actions": [
                {
                    "service": "alarm_control_panel.alarm_arm_away",
                    "target": {"entity_id": ["alarm_control_panel.home"]},
                    "data": {"code": "4321"},
                }
            ],
        }
    )
    await hass.async_block_till_done()

    data = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(data)
    for secret in (
        "hunter22",
        "Visitors",
        "4321",
        "notify.mobile_app_alex",
        "device_tracker.alex_phone",
    ):
        assert secret not in text, secret
    assert data["settings"]["guest_wifi"] == REDACTED
    assert data["routines"][0]["actions"][0]["data"]["code"] == REDACTED
    # What support needs is still there.
    assert "alarm_control_panel.alarm_arm_away" in text
    assert "arm" in data["statuses"]
    assert await hass.config_entries.async_unload(entry.entry_id)
