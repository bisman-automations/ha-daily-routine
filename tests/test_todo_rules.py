"""To-do rules: a routine whose step adds, completes or removes an item on a to-do list."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.daily_routine.const import DOMAIN, STORAGE_KEY
from custom_components.daily_routine.routines import normalize_routine
from custom_components.daily_routine.schema import vol
from custom_components.daily_routine.text_format import parse_steps_text, steps_to_text
from custom_components.daily_routine.todo import normalize_todo_step

GROCERIES = "todo.groceries"
CLEAN = "binary_sensor.dishwasher_clean"


class FakeTodo:
    """Stands in for the to-do integration: keeps items and records what was asked of it."""

    def __init__(self, hass: HomeAssistant, items: list[dict] | None = None) -> None:
        self.items = {GROCERIES: [dict(item) for item in items or []]}
        self.adds: list[dict] = []
        self.updates: list[dict] = []
        self.removes: list[dict] = []
        hass.services.async_register(
            "todo", "get_items", self._get, supports_response=SupportsResponse.ONLY
        )
        hass.services.async_register("todo", "add_item", self._add)
        hass.services.async_register("todo", "update_item", self._update)
        hass.services.async_register("todo", "remove_item", self._remove)

    async def _get(self, call: ServiceCall) -> dict:
        entity = call.data["entity_id"]
        wanted = call.data.get("status") or []
        items = [i for i in self.items[entity] if not wanted or i["status"] in wanted]
        return {entity: {"items": items}}

    async def _add(self, call: ServiceCall) -> None:
        self.adds.append(dict(call.data))
        entity = call.data["entity_id"]
        uid = f"uid{len(self.items[entity]) + 1}"
        self.items[entity].append(
            {"uid": uid, "summary": call.data["item"], "status": "needs_action"}
        )

    async def _update(self, call: ServiceCall) -> None:
        self.updates.append(dict(call.data))
        for item in self.items[call.data["entity_id"]]:
            if item["uid"] == call.data["item"]:
                item["status"] = call.data["status"]

    async def _remove(self, call: ServiceCall) -> None:
        self.removes.append(dict(call.data))
        entity = call.data["entity_id"]
        self.items[entity] = [i for i in self.items[entity] if i["uid"] != call.data["item"]]

    def open_summaries(self) -> list[str]:
        return [i["summary"] for i in self.items[GROCERIES] if i["status"] == "needs_action"]


def _rule(**overrides):
    raw = {
        "id": "empty_dishwasher",
        "name": "Empty dishwasher",
        "kind": "todo",
        "trigger": "state_is",
        "watch_entity": CLEAN,
        "watch_states": ["on"],
        "actions": [{"todo": {"op": "add", "list": GROCERIES, "item": "Empty dishwasher"}}],
    }
    raw.update(overrides)
    return raw


async def _setup(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, hass.data[DOMAIN]["coordinator"]


async def _teardown(hass: HomeAssistant, entry) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# ----- validation and the text form -----


def test_a_to_do_step_needs_an_action_a_list_and_an_item() -> None:
    with pytest.raises(vol.Invalid):
        normalize_todo_step({"op": "tidy", "list": GROCERIES, "item": "x"}, 1)
    with pytest.raises(vol.Invalid):
        normalize_todo_step({"op": "add", "list": "light.kitchen", "item": "x"}, 1)
    with pytest.raises(vol.Invalid):
        normalize_todo_step({"op": "add", "list": GROCERIES, "item": "   "}, 1)
    with pytest.raises(vol.Invalid):
        normalize_todo_step({"op": "add", "list": GROCERIES, "item": "x" * 300}, 1)
    assert normalize_todo_step({"op": "ADD", "list": GROCERIES, "item": "  Buy   milk "}, 1) == {
        "op": "add",
        "list": GROCERIES,
        "item": "Buy milk",
    }


def test_a_to_do_step_round_trips_through_the_text_form() -> None:
    routine = normalize_routine(
        _rule(actions=[{"todo": {"op": "complete", "list": GROCERIES, "item": "Buy milk"}}])
    )
    text = steps_to_text(routine["actions"])
    assert text == "todo complete | todo.groceries | Buy milk"
    assert parse_steps_text(text) == [
        {"todo": {"op": "complete", "list": GROCERIES, "item": "Buy milk"}}
    ]
    with pytest.raises(vol.Invalid):
        parse_steps_text("todo add | todo.groceries")


def test_a_to_do_rule_is_one_state_trigger_with_one_to_do_step() -> None:
    routine = normalize_routine(_rule(watch_states="on, running, on"))
    assert routine["watch_states"] == ["on", "running"]
    assert routine["kind"] == "todo"
    with pytest.raises(vol.Invalid):
        normalize_routine(_rule(trigger="time"))
    with pytest.raises(vol.Invalid):
        normalize_routine(
            _rule(
                actions=[
                    {"todo": {"op": "add", "list": GROCERIES, "item": "a"}},
                    {"delay": 1},
                    {"todo": {"op": "add", "list": GROCERIES, "item": "b"}},
                ]
            )
        )
    with pytest.raises(vol.Invalid):
        normalize_routine(_rule(watch_states=""))
    with pytest.raises(vol.Invalid):
        normalize_routine(_rule(hold_minutes=5000))


# ----- running -----


async def test_a_to_do_rule_adds_once_and_completes_when_the_state_changes(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass.states.async_set(CLEAN, "off")
    todo = FakeTodo(hass)
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(_rule())
    await coordinator.async_save_routine(
        _rule(
            id="finish_dishwasher",
            name="Finish dishwasher",
            watch_states=["off"],
            actions=[{"todo": {"op": "complete", "list": GROCERIES, "item": "Empty dishwasher"}}],
        )
    )
    await hass.async_block_till_done()

    hass.states.async_set(CLEAN, "on")
    await hass.async_block_till_done()
    assert todo.open_summaries() == ["Empty dishwasher"]
    assert todo.adds == [{"entity_id": GROCERIES, "item": "Empty dishwasher"}]

    hass.states.async_set(CLEAN, "off")
    await hass.async_block_till_done()
    assert todo.open_summaries() == []
    await _teardown(hass, entry)


async def test_several_states_count_once_each_move_between_them_does_not_rerun(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass.states.async_set("sensor.dishwasher_state", "empty")
    todo = FakeTodo(hass)
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        _rule(watch_entity="sensor.dishwasher_state", watch_states=["running", "rinse"])
    )
    await hass.async_block_till_done()

    hass.states.async_set("sensor.dishwasher_state", "running")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.dishwasher_state", "rinse")
    await hass.async_block_till_done()
    assert len(todo.adds) == 1, "running to rinse is still the same run"

    hass.states.async_set("sensor.dishwasher_state", "empty")
    hass.states.async_set("sensor.dishwasher_state", "rinse")
    await hass.async_block_till_done()
    # The item from the first run is still open, so the second run finds it and adds nothing.
    details = [run["detail"] for run in coordinator.get_history("empty_dishwasher")]
    assert details == ["Empty dishwasher: already on the list", "Empty dishwasher: added"]
    assert len(todo.adds) == 1
    await _teardown(hass, entry)


async def test_a_hold_time_runs_only_if_the_state_is_still_there(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass.states.async_set(CLEAN, "off")
    todo = FakeTodo(hass)
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(_rule(hold_minutes=2))
    await hass.async_block_till_done()

    hass.states.async_set(CLEAN, "on")
    await hass.async_block_till_done()
    assert todo.adds == [], "nothing happens while the hold time runs"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=3))
    await hass.async_block_till_done()
    assert len(todo.adds) == 1

    hass.states.async_set(CLEAN, "off")
    await hass.async_block_till_done()
    hass.states.async_set(CLEAN, "on")
    await hass.async_block_till_done()
    hass.states.async_set(CLEAN, "off")
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=10))
    await hass.async_block_till_done()
    assert len(todo.adds) == 1, "a flicker that goes back before the hold time adds nothing"
    await _teardown(hass, entry)


async def test_a_device_that_changed_while_stopped_runs_once_after_start(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass_storage[STORAGE_KEY] = {
        "version": 2,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {
            "routines": [normalize_routine(_rule())],
            "runtime": {"empty_dishwasher": {"seen_state": "off"}},
        },
    }
    hass.states.async_set(CLEAN, "on")
    todo = FakeTodo(hass)
    entry, coordinator = await _setup(hass)
    await hass.async_block_till_done()
    assert todo.adds == [], "nothing runs before the start-up wait ends"

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=60))
    await hass.async_block_till_done()
    assert len(todo.adds) == 1
    await _teardown(hass, entry)


async def test_a_paused_to_do_rule_does_nothing(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass.states.async_set(CLEAN, "off")
    todo = FakeTodo(hass)
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(_rule())
    await hass.async_block_till_done()
    coordinator.paused = True

    hass.states.async_set(CLEAN, "on")
    await hass.async_block_till_done()
    assert todo.adds == []
    history = coordinator.get_history("empty_dishwasher")
    assert history[0]["outcome"] == "skipped"
    assert history[0]["detail"] == "paused"
    await _teardown(hass, entry)


async def test_run_history_says_what_the_list_already_had(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass.states.async_set(CLEAN, "off")
    todo = FakeTodo(hass, [{"uid": "x", "summary": "Empty dishwasher", "status": "needs_action"}])
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(_rule())
    await hass.async_block_till_done()

    hass.states.async_set(CLEAN, "on")
    await hass.async_block_till_done()
    assert todo.adds == []
    history = coordinator.get_history("empty_dishwasher")
    assert history[0]["outcome"] == "ran"
    assert history[0]["detail"] == "Empty dishwasher: already on the list"
    await _teardown(hass, entry)


async def test_a_list_that_cannot_be_read_fails_the_run(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass.states.async_set(CLEAN, "off")
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        _rule(actions=[{"todo": {"op": "add", "list": "todo.missing", "item": "x"}}])
    )
    await hass.async_block_till_done()

    hass.states.async_set(CLEAN, "on")
    await hass.async_block_till_done()
    assert (
        coordinator.get_status("empty_dishwasher")["last_result"] == "error:todo_list_unavailable"
    )
    assert coordinator.get_history("empty_dishwasher")[0]["outcome"] == "failed"
    await _teardown(hass, entry)


async def test_remove_takes_an_open_item_off_the_list(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    hass.states.async_set(CLEAN, "off")
    todo = FakeTodo(hass, [{"uid": "w", "summary": "Water plants", "status": "needs_action"}])
    entry, coordinator = await _setup(hass)
    await coordinator.async_save_routine(
        _rule(
            id="rain",
            watch_entity=CLEAN,
            actions=[{"todo": {"op": "remove", "list": GROCERIES, "item": "water plants"}}],
        )
    )
    await hass.async_block_till_done()

    hass.states.async_set(CLEAN, "on")
    await hass.async_block_till_done()
    assert todo.removes == [{"entity_id": GROCERIES, "item": "w"}]
    assert todo.open_summaries() == []
    await _teardown(hass, entry)


# ----- dry run and health -----


async def test_preview_reads_the_list_and_changes_nothing(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    todo = FakeTodo(hass)
    entry, coordinator = await _setup(hass)
    routine = normalize_routine(_rule())
    plan = await coordinator.preview(routine)
    assert plan["steps"][0] == {
        "type": "todo",
        "op": "add",
        "list": GROCERIES,
        "item": "Empty dishwasher",
        "outcome": "would_add",
    }
    assert todo.adds == []
    await _teardown(hass, entry)


async def test_health_names_what_will_stop_the_rule(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    entry, coordinator = await _setup(hass)
    routine = normalize_routine(
        _rule(actions=[{"todo": {"op": "add", "list": "todo.gone", "item": "x"}}])
    )
    assert "CLEAN" not in " ".join(coordinator.health(routine))
    assert any("todo.gone is not in Home Assistant" in p for p in coordinator.health(routine))
    hass.states.async_set(GROCERIES, "0", {"friendly_name": "Groceries"})
    hass.states.async_set(CLEAN, "unavailable", {"friendly_name": "Dishwasher clean"})
    assert coordinator.health(normalize_routine(_rule())) == [
        "Dishwasher clean is unavailable right now."
    ]
    await _teardown(hass, entry)
