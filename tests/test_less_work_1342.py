"""1.34.2: less work for the same answers. Attribute-only updates of what a
condition watches are let go, a state trigger or a to-do list saves only when
what it remembers moved, and the pause switch, House mode and Kind of day
write only when what they show changed."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from custom_components.daily_routine import coordinator as coordinator_module
from homeassistant.core import HomeAssistant

from .test_live_panel_api import _setup

LIGHT_OFF = {"service": "light.turn_off", "target": {"entity_id": ["light.hall"]}}


def _count_condition_checks(coord: Any) -> list[str | None]:
    seen: list[str | None] = []
    real = coord._on_condition_entity_changed  # noqa: SLF001

    def counted(routine: dict[str, Any], entity_id: str | None) -> None:
        seen.append(entity_id)
        real(routine, entity_id)

    coord._on_condition_entity_changed = counted  # noqa: SLF001
    return seen


async def test_attribute_updates_dont_recheck_conditions(
    hass: HomeAssistant, enable_custom_integrations
):
    phone = {"source": "device_tracker.sam_phone"}
    hass.states.async_set("person.sam", "home", {**phone, "latitude": 1.0})
    hass.states.async_set("weather.home", "sunny", {"temperature": 18})
    coord = await _setup(hass)
    await coord.async_save_routine(
        {
            "id": "r",
            "name": "R",
            "trigger": "manual",
            "conditions": [
                {
                    "kind": "presence",
                    "who": "everyone",
                    "where": "away",
                    "people": ["person.sam"],
                    "zone": "zone.home",
                },
                {
                    "kind": "weather",
                    "entity_id": "weather.home",
                    "test": "above",
                    "value": 20,
                },
            ],
            "actions": [LIGHT_OFF],
        }
    )
    await hass.async_block_till_done()
    seen = _count_condition_checks(coord)

    # A phone's GPS fixes move the person's attributes, not where they are.
    for n in range(10):
        hass.states.async_set(
            "person.sam", "home", {**phone, "latitude": 1.0 + n / 100}
        )
    await hass.async_block_till_done()
    assert seen == []

    # Leaving is a change of state: checked.
    hass.states.async_set("person.sam", "not_home", {**phone, "latitude": 2.0})
    await hass.async_block_till_done()
    assert seen == ["person.sam"]

    # A weather's temperature is an attribute, and a condition reads it.
    hass.states.async_set("weather.home", "sunny", {"temperature": 22})
    await hass.async_block_till_done()
    assert seen == ["person.sam", "weather.home"]
    assert coord.get_status("r")["conditions_ok"] is True


async def test_state_trigger_saves_only_when_the_state_moves(
    hass: HomeAssistant, enable_custom_integrations
):
    hass.states.async_set("media_player.tv", "playing", {"media_position": 0})
    coord = await _setup(hass)
    await coord.async_save_routine(
        {
            "id": "tv_off",
            "name": "TV off",
            "trigger": "state_is",
            "watch_entity": "media_player.tv",
            "watch_state": "off",
            "actions": [LIGHT_OFF],
        }
    )
    await hass.async_block_till_done()
    with patch.object(coord._store, "async_delay_save") as save:  # noqa: SLF001
        for n in range(1, 20):
            hass.states.async_set("media_player.tv", "playing", {"media_position": n})
        await hass.async_block_till_done()
        assert save.call_count == 0
        hass.states.async_set("media_player.tv", "paused")
        await hass.async_block_till_done()
        assert save.call_count == 1
    assert coord._rt("tv_off")["seen_state"] == "paused"  # noqa: SLF001


async def test_todo_list_saves_only_when_done_items_change(
    hass: HomeAssistant, enable_custom_integrations
):
    coord = await _setup(hass)
    items = [{"summary": "Milk", "status": "completed"}]

    async def fake_items(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return list(items)

    routine = {"id": "t", "watch_entity": "todo.shop", "watch_item": ""}
    coord._rt("t")["seen_done"] = ["milk"]  # noqa: SLF001
    with (
        patch.object(coordinator_module, "list_items", fake_items),
        patch.object(coord._store, "async_delay_save") as save,  # noqa: SLF001
        patch.object(coord, "_run_state_routine") as run,
    ):
        await coord._check_done(routine)  # noqa: SLF001
        assert save.call_count == 0 and run.call_count == 0
        items.append({"summary": "Eggs", "status": "completed"})
        await coord._check_done(routine)  # noqa: SLF001
        assert save.call_count == 1 and run.call_count == 1


async def test_entities_write_only_when_they_change(
    hass: HomeAssistant, enable_custom_integrations
):
    coord = await _setup(hass)
    await hass.async_block_till_done()
    coord._notify_listeners()  # noqa: SLF001 (settles each entity's first write)
    await hass.async_block_till_done()
    ours = ("switch.pause_all_routines", "select.house_mode", "sensor.kind_of_day")
    before = {e: hass.states.get(e).last_reported for e in ours}
    with patch("homeassistant.core.StateMachine.async_set_internal") as written:
        for _ in range(10):
            coord._notify_listeners()  # noqa: SLF001
        await hass.async_block_till_done()
    assert [c for c in written.call_args_list if c.args[0] in ours] == []
    assert {e: hass.states.get(e).last_reported for e in ours} == before

    # A real change is still written at once.
    await coord.async_set_paused(True)
    await hass.async_block_till_done()
    paused = [
        s for s in hass.states.async_all("switch") if s.entity_id.endswith("routines")
    ]
    assert paused and paused[0].state == "on"
