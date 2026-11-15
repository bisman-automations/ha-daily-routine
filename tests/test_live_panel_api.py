"""1.22.0 through the dashboard's API and Assist: Undo, house mode, Today,
lived-in settings, and the Assist sentences."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.runtime import get_coordinator
from custom_components.daily_routine.undo import restore, snapshot
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


async def _setup(hass: HomeAssistant) -> Any:
    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return get_coordinator(hass)


async def test_dashboard_commands(
    hass: HomeAssistant, enable_custom_integrations, hass_ws_client
):
    coord = await _setup(hass)
    hass.states.async_set("light.hall", "on")
    hass.states.async_set("weather.home", "sunny", {"friendly_name": "Home"})

    async def turn_off(call):
        hass.states.async_set("light.hall", "off")

    hass.services.async_register("light", "turn_off", turn_off)
    hass.services.async_register(
        "light", "turn_on", lambda call: hass.states.async_set("light.hall", "on")
    )
    await coord.async_save_routine(
        {
            "id": "r",
            "name": "R",
            "trigger": "manual",
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": ["light.hall"]}}
            ],
        }
    )
    client = await hass_ws_client(hass)

    await client.send_json({"id": 1, "type": f"{DOMAIN}/overview"})
    overview = (await client.receive_json())["result"]
    assert overview["mode"] == "home"
    assert [w["entity_id"] for w in overview["weathers"]] == ["weather.home"]
    assert overview["lived_in"] == {"active": False, "on": []}
    assert overview["workday_sensors"] == []

    await client.send_json(
        {"id": 2, "type": f"{DOMAIN}/routine/undo", "routine_id": "r"}
    )
    assert (await client.receive_json())["error"]["code"] == "not_found"

    assert await coord.async_run("r")
    await hass.async_block_till_done()
    assert hass.states.get("light.hall").state == "off"
    await client.send_json(
        {"id": 3, "type": f"{DOMAIN}/routine/undo", "routine_id": "r"}
    )
    assert (await client.receive_json())["result"] == {"put_back": ["light.hall"]}
    await hass.async_block_till_done()
    assert hass.states.get("light.hall").state == "on"

    await client.send_json({"id": 4, "type": f"{DOMAIN}/mode/set", "mode": "night"})
    assert (await client.receive_json())["result"] == {"mode": "night"}
    await client.send_json({"id": 5, "type": f"{DOMAIN}/mode/set", "mode": "party"})
    assert not (await client.receive_json())["success"]

    await client.send_json({"id": 6, "type": f"{DOMAIN}/today"})
    today = (await client.receive_json())["result"]
    assert today["mode"] == "night"
    assert {e["routine_id"] for e in today["done"]} == {"r"}

    await client.send_json(
        {
            "id": 7,
            "type": f"{DOMAIN}/settings/save",
            "settings": {
                **coord.settings,
                "lived_in": {
                    "enabled": True,
                    "lights": ["light.hall"],
                    "start": "sunset",
                    "end": "23:00",
                    "modes": ["vacation"],
                },
            },
        }
    )
    saved = (await client.receive_json())["result"]["settings"]
    assert saved["lived_in"]["modes"] == ["vacation"]
    await client.send_json(
        {
            "id": 8,
            "type": f"{DOMAIN}/settings/save",
            "settings": {**coord.settings, "lived_in": {"lights": ["lock.a"]}},
        }
    )
    assert (await client.receive_json())["error"]["code"] == "invalid_settings"


async def test_undo_service_and_failures(
    hass: HomeAssistant, enable_custom_integrations
):
    coord = await _setup(hass)
    hass.states.async_set("switch.fan", "off")
    hass.services.async_register(
        "switch", "turn_on", lambda call: hass.states.async_set("switch.fan", "on")
    )
    await coord.async_save_routine(
        {
            "id": "s",
            "name": "S",
            "trigger": "manual",
            "actions": [
                {"service": "switch.turn_on", "target": {"entity_id": ["switch.fan"]}}
            ],
        }
    )
    assert await coord.async_run("s")
    await hass.async_block_till_done()

    async def broken(call):
        raise RuntimeError("no answer")

    hass.services.async_register("switch", "turn_off", broken)
    # Putting it back fails, and says which device.
    with pytest.raises(Exception, match="put back"):
        await hass.services.async_call(
            DOMAIN, "undo_routine", {"routine_id": "s"}, blocking=True
        )
    assert "Undo" in coord.get_history("s")[0]["detail"]
    with pytest.raises(Exception):  # noqa: B017 - nothing left to undo
        await hass.services.async_call(
            DOMAIN, "undo_routine", {"routine_id": "s"}, blocking=True
        )


async def test_snapshot_and_restore_each_kind(hass: HomeAssistant):
    hass.states.async_set("cover.a", "open", {"current_position": 30})
    hass.states.async_set("cover.b", "closed")
    hass.states.async_set("lock.a", "unlocked")
    hass.states.async_set(
        "climate.a", "heat_cool", {"target_temp_low": 18, "target_temp_high": 24}
    )
    hass.states.async_set(
        "light.c", "on", {"color_mode": "hs", "hs_color": (30, 50), "brightness": 9}
    )
    hass.states.async_set("light.gone", "unavailable")
    hass.states.async_set("media_player.tv", "on")
    saved = snapshot(
        hass,
        [
            "cover.a",
            "cover.b",
            "lock.a",
            "climate.a",
            "light.c",
            "light.gone",
            "media_player.tv",
            "light.none",
        ],
    )
    assert set(saved) == {"cover.a", "cover.b", "lock.a", "climate.a", "light.c"}
    assert saved["light.c"]["attrs"] == {"brightness": 9, "hs_color": [30, 50]}
    calls: list[tuple[str, str, dict]] = []

    async def call(domain, service, data, target):
        calls.append((domain, service, data))
        if service == "set_cover_position":
            return RuntimeError("no position")
        return None

    hass.states.async_remove("lock.a")
    failed = await restore(hass, saved, call)
    assert failed == []
    assert ("cover", "open_cover", {}) in calls
    assert ("cover", "close_cover", {}) in calls
    assert not any(domain == "lock" for domain, *_ in calls)
    assert (
        "climate",
        "set_temperature",
        {"target_temp_low": 18, "target_temp_high": 24},
    ) in calls


async def test_assist_sentences_are_added_with_conversation(
    hass: HomeAssistant, enable_custom_integrations
):
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.hall", "on")
    coord = await _setup(hass)
    assert coord.voice._remove is not None  # noqa: SLF001
    await coord.async_save_routine(
        {
            "id": "bed",
            "name": "Bedtime",
            "trigger": "manual",
            "skip_noop": False,
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": ["light.hall"]}}
            ],
        }
    )
    result = await hass.services.async_call(
        "conversation",
        "process",
        {"text": "run bedtime"},
        blocking=True,
        return_response=True,
    )
    speech = result["response"]["speech"]["plain"]["speech"]
    assert speech == "Starting Bedtime."
    await hass.async_block_till_done()
    assert len(calls) == 1
    result = await hass.services.async_call(
        "conversation",
        "process",
        {"text": "set the house to night"},
        blocking=True,
        return_response=True,
    )
    assert coord.mode == "night"
    result = await hass.services.async_call(
        "conversation",
        "process",
        {"text": "what's the next routine"},
        blocking=True,
        return_response=True,
    )
    assert "Nothing else" in result["response"]["speech"]["plain"]["speech"]
    coord.voice.stop()
    assert coord.voice._remove is None  # noqa: SLF001
