"""1.22.0: live progress, quick Run now, more triggers, new steps, Undo, house
modes, workday and weather, lived-in lights, Today and Assist."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.daily_routine import coordinator as coordinator_module
from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.coordinator import HomeRoutinesCoordinator
from custom_components.daily_routine.lived_in import clean_settings
from custom_components.daily_routine.routines import normalize_routine
from custom_components.daily_routine.runtime import get_coordinator
from custom_components.daily_routine.schema import vol
from custom_components.daily_routine.text_format import (
    conditions_to_text,
    parse_conditions_text,
    parse_steps_text,
    steps_to_text,
)
from custom_components.daily_routine.timeline import today
from custom_components.daily_routine.voice import Voice, find_mode, find_routine
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt as dt_util

from .conftest import make_routine


@pytest.fixture(autouse=True)
async def _stop(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(coordinator_module, "VERIFY_POLL_SECONDS", 0.01)
    created: list[HomeRoutinesCoordinator] = []
    original_init = HomeRoutinesCoordinator.__init__

    def recording_init(self, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(HomeRoutinesCoordinator, "__init__", recording_init)
    yield
    for coord in created:
        await coord.async_unload(save=False)


def _at(local: str) -> datetime:
    return datetime.fromisoformat(local).replace(tzinfo=dt_util.get_default_time_zone())


async def _started(hass: HomeAssistant, *routines: dict[str, Any]):
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = list(routines)
    coord._start_all(catch_up=False)  # noqa: SLF001
    await hass.async_block_till_done()
    return coord


def _light_step(service: str = "light.turn_off", entity: str = "light.hall"):
    return {"service": service, "target": {"entity_id": [entity]}, "data": {}}


# ----- live progress and Run now -----


async def test_progress_shows_the_step_and_clears_after(hass: HomeAssistant):
    async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.hall", "on", {"friendly_name": "Hall light"})
    routine = make_routine(
        trigger="manual", actions=[{"delay": 1}, _light_step()], skip_noop=False
    )
    coord = await _started(hass, routine)
    seen: list[Any] = []
    coord.async_add_listener(
        lambda: seen.append(coord.get_status("routine_one")["progress"])
    )
    result = await coord.async_start_run("routine_one")
    assert result == {"started": True, "ran": None, "reason": None}
    assert coord.get_status("routine_one")["state"] == "running"
    await asyncio.sleep(0)
    again = await coord.async_start_run("routine_one")
    assert again["reason"] == "already_running"
    await hass.async_block_till_done()
    steps = [p for p in seen if p]
    assert steps[0]["step"] == 1 and steps[0]["total"] == 2
    assert steps[0]["label"] == "Wait 1 second" and steps[0]["until"]
    assert steps[-1]["label"] == "Turn off Hall light"
    status = coord.get_status("routine_one")
    assert status["state"] == "idle" and status["progress"] is None


async def test_run_now_answers_at_once_when_it_cant_run(hass: HomeAssistant):
    hass.states.async_set("lock.front", "locked")
    routine = make_routine(
        trigger="manual",
        conditions=[{"kind": "state", "entity_id": "lock.front", "state": "unlocked"}],
    )
    coord = await _started(hass, routine)
    result = await coord.async_start_run("routine_one")
    assert result["started"] is False
    assert result["reason"] == "extra_condition_not_met"
    assert coord.run_log.count == 1
    off = await _started(hass, make_routine(id="off", trigger="manual", enabled=False))
    assert (await off.async_start_run("off"))["reason"] == "disabled"
    with pytest.raises(vol.Invalid):
        await coord.async_start_run("nope")


async def test_subscribe_pushes_run_states(
    hass: HomeAssistant, enable_custom_integrations, hass_ws_client
):
    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = get_coordinator(hass)
    await coord.async_save_routine(
        {
            "id": "r",
            "name": "R",
            "trigger": "manual",
            "actions": [{"delay": 1}, _light_step()],
        }
    )
    async_mock_service(hass, "light", "turn_off")
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/subscribe"})
    assert (await client.receive_json())["success"]
    first = await client.receive_json()
    assert first["event"]["statuses"]["r"]["state"] == "idle"
    assert first["event"]["mode"] == "home"
    await client.send_json(
        {"id": 2, "type": f"{DOMAIN}/routine/run", "routine_id": "r"}
    )
    states = []
    for _ in range(10):
        msg = await client.receive_json(timeout=5)
        if msg.get("type") == "event":
            states.append(msg["event"]["statuses"]["r"]["state"])
            if states[-1] == "idle" and "running" in states:
                break
        else:
            assert msg["result"]["started"] is True
    assert "running" in states and states[-1] == "idle"
    await client.send_json({"id": 3, "type": f"{DOMAIN}/today"})
    result = (await client.receive_json())["result"]
    assert [e["routine_id"] for e in result["done"]] == ["r"]


# ----- more triggers -----


def test_more_triggers_are_checked_like_the_routines_own():
    routine = make_routine(
        more_triggers=[
            {"trigger": "time", "time": "22:00"},
            {
                "trigger": "state_is",
                "watch_entity": "lock.front",
                "watch_states": "unlocked",
            },
        ]
    )
    assert [t["trigger"] for t in routine["more_triggers"]] == ["time", "state_is"]
    assert routine["more_triggers"][0]["time"] == "22:00:00"
    with pytest.raises(vol.Invalid, match="Trigger 2"):
        make_routine(more_triggers=[{"trigger": "state_is"}])
    with pytest.raises(vol.Invalid, match="pick what starts it"):
        make_routine(more_triggers=[{"trigger": "room_clear"}])
    with pytest.raises(vol.Invalid):
        make_routine(trigger="manual", more_triggers=[{"trigger": "time"}])
    with pytest.raises(vol.Invalid, match="at most"):
        make_routine(more_triggers=[{"trigger": "time"}] * 5)


async def test_another_trigger_runs_the_routine(hass: HomeAssistant, freezer):
    freezer.move_to(_at("2026-11-14 12:00:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.hall", "on")
    hass.states.async_set("lock.front", "locked")
    routine = make_routine(
        time="07:00:00",
        repeat=True,
        skip_noop=False,
        more_triggers=[
            {"trigger": "time", "time": "21:00"},
            {
                "trigger": "state_is",
                "watch_entity": "lock.front",
                "watch_states": ["unlocked"],
            },
        ],
    )
    coord = await _started(hass, routine)
    assert coord.get_status("routine_one")["next_run"].startswith("2026-11-14T21:00")
    hass.states.async_set("lock.front", "unlocked")
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert coord.get_status("routine_one")["last_result"] == "success"
    freezer.move_to(_at("2026-11-14 21:00:01"))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls) == 2
    events = [e["routine_id"] for e in coord.run_log.events()]
    assert events == ["routine_one", "routine_one"]


async def test_everyone_leaves_as_another_trigger_waits_for_the_house(
    hass: HomeAssistant,
):
    async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("person.a", "home")
    routine = make_routine(
        more_triggers=[{"trigger": "everyone_leaves", "people": ["person.a"]}],
        conditions=[{"kind": "state", "entity_id": "input_boolean.ok", "state": "on"}],
    )
    assert "person.a" in coordinator_module._watched_entities(routine)  # noqa: SLF001
    coord = await _started(hass, routine)
    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()
    rt = coord._rt("routine_one")  # noqa: SLF001
    assert rt["state"] == "waiting" and rt["started_by"] == "routine_one~2"
    # Back home before the condition holds: what started it is over.
    hass.states.async_set("person.a", "home")
    hass.states.async_set("input_boolean.ok", "on")
    await hass.async_block_till_done()
    assert rt["last_result"] == "trigger_ended"
    assert rt["started_by"] is None


# ----- new steps -----


async def test_run_another_routine_and_refuse_a_loop(hass: HomeAssistant):
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.hall", "on")
    lights = make_routine(id="lights", name="Lights", trigger="manual", skip_noop=False)
    bedtime = make_routine(
        id="bedtime",
        name="Bedtime",
        trigger="manual",
        actions=[{"run_routine": "lights"}],
    )
    coord = await _started(hass, lights, bedtime)
    assert await coord.async_run("bedtime")
    assert len(calls) == 1
    assert "Ran Lights" in coord.get_history("bedtime")[0]["detail"]
    with pytest.raises(vol.Invalid, match="itself"):
        make_routine(id="x", actions=[{"run_routine": "x"}])
    a = make_routine(
        id="a", trigger="manual", retry_later_minutes=0, actions=[{"run_routine": "b"}]
    )
    b = make_routine(
        id="b", trigger="manual", retry_later_minutes=0, actions=[{"run_routine": "a"}]
    )
    loop = await _started(hass, a, b)
    assert not await loop.async_run("a")
    assert "loop" in loop.get_history("b")[0]["detail"]
    assert "failed" in loop.get_history("a")[0]["detail"]
    gone = await _started(
        hass,
        make_routine(
            id="c",
            trigger="manual",
            retry_later_minutes=0,
            actions=[{"run_routine": "zz"}],
        ),
    )
    assert not await gone.async_run("c")


async def test_wait_until_goes_on_or_stops(hass: HomeAssistant):
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.hall", "on")
    hass.states.async_set("cover.garage", "open")
    routine = make_routine(
        trigger="manual",
        skip_noop=False,
        actions=[
            {
                "wait_until": {
                    "entity_id": "cover.garage",
                    "states": "closed",
                    "minutes": 5,
                }
            },
            _light_step(),
        ],
    )
    coord = await _started(hass, routine)
    task = hass.async_create_task(coord.async_run("routine_one"))
    await asyncio.sleep(0.05)
    progress = coord.get_status("routine_one")["progress"]
    assert progress["label"].startswith("Wait for") and progress["until"]
    assert not calls
    hass.states.async_set("cover.garage", "closed")
    assert await task
    assert len(calls) == 1

    hass.states.async_set("cover.garage", "open")
    stopping = make_routine(
        id="stop",
        trigger="manual",
        skip_noop=False,
        retry_later_minutes=0,
        actions=[
            {
                "wait_until": {
                    "entity_id": "cover.garage",
                    "states": ["closed"],
                    "minutes": 1,
                    "on_timeout": "stop",
                }
            },
            _light_step(),
        ],
    )
    coord2 = await _started(hass, stopping)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(coordinator_module, "SECONDS_PER_MINUTE", 0.05)
        assert not await coord2.async_run("stop")
    assert len(calls) == 1
    assert "didn't change" in coord2.get_history("stop")[0]["detail"]


async def test_only_if_skips_just_that_step(hass: HomeAssistant):
    calls = async_mock_service(hass, "light", "turn_off")
    on = async_mock_service(hass, "light", "turn_on")
    hass.states.async_set("light.hall", "on")
    hass.states.async_set("light.porch", "off")
    hass.states.async_set("binary_sensor.window", "on")
    routine = make_routine(
        trigger="manual",
        skip_noop=False,
        actions=[
            {
                **_light_step(),
                "only_if": {
                    "kind": "state",
                    "entity_id": "binary_sensor.window",
                    "state": "off",
                },
            },
            _light_step("light.turn_on", "light.porch"),
        ],
    )
    coord = await _started(hass, routine)
    assert await coord.async_run("routine_one")
    assert not calls and len(on) == 1
    assert "Step 1 skipped" in coord.get_history("routine_one")[0]["detail"]
    preview = await coord.preview(routine)
    assert preview["steps"][0]["type"] == "only_if"


# ----- undo -----


async def test_undo_puts_things_back(hass: HomeAssistant):
    calls: list[ServiceCall] = []

    async def record(call: ServiceCall) -> None:
        calls.append(call)
        entity = call.data["entity_id"][0]
        state = "on" if call.service == "turn_on" else "off"
        hass.states.async_set(entity, state)

    for service in ("turn_on", "turn_off"):
        hass.services.async_register("light", service, record)
    hass.states.async_set(
        "light.hall", "on", {"brightness": 120, "color_mode": "brightness"}
    )
    hass.states.async_set("light.den", "off")
    routine = make_routine(
        trigger="manual",
        actions=[_light_step(), _light_step("light.turn_on", "light.den")],
    )
    coord = await _started(hass, routine)
    assert not coord.can_undo("routine_one")
    assert await coord.async_run("routine_one")
    assert coord.get_status("routine_one")["can_undo"]
    calls.clear()
    put_back = await coord.async_undo("routine_one")
    assert sorted(put_back) == ["light.den", "light.hall"]
    by_entity = {c.data["entity_id"][0]: c for c in calls}
    assert by_entity["light.hall"].service == "turn_on"
    assert by_entity["light.hall"].data["brightness"] == 120
    assert by_entity["light.den"].service == "turn_off"
    assert not coord.can_undo("routine_one")
    with pytest.raises(vol.Invalid):
        await coord.async_undo("routine_one")


async def test_undo_covers_locks_and_climate():
    from custom_components.daily_routine.undo import _call_for  # noqa: PLC0415

    assert _call_for("cover.a", {"state": "open", "attrs": {"position": 40}}) == [
        ("set_cover_position", {"position": 40})
    ]
    assert _call_for("cover.a", {"state": "closed", "attrs": {}}) == [
        ("close_cover", {})
    ]
    assert _call_for("lock.a", {"state": "locked", "attrs": {}}) == [("lock", {})]
    assert _call_for("climate.a", {"state": "heat", "attrs": {"temperature": 20}}) == [
        ("set_hvac_mode", {"hvac_mode": "heat"}),
        ("set_temperature", {"temperature": 20}),
    ]


# ----- house modes -----


async def test_modes_gate_runs_and_start_routines(hass: HomeAssistant):
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.hall", "on")
    only_night = make_routine(
        id="night_only",
        trigger="manual",
        skip_noop=False,
        conditions=[{"kind": "mode", "modes": ["night"]}],
    )
    on_away = make_routine(
        id="on_away", trigger="mode_changes", modes=["away"], skip_noop=False
    )
    coord = await _started(hass, only_night, on_away)
    assert (await coord.async_start_run("night_only"))["reason"] == "wrong_mode"
    await coord.async_set_mode("night")
    assert (await coord.async_start_run("night_only"))["started"]
    await hass.async_block_till_done()
    assert len(calls) == 1
    await coord.async_set_mode("away")
    await hass.async_block_till_done()
    assert len(calls) == 2
    await coord.async_set_mode("away")  # no change: nothing runs
    await hass.async_block_till_done()
    assert len(calls) == 2
    with pytest.raises(vol.Invalid):
        await coord.async_set_mode("party")
    with pytest.raises(vol.Invalid):
        make_routine(trigger="mode_changes")


async def test_set_mode_step_and_select_entity(
    hass: HomeAssistant, enable_custom_integrations
):
    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = get_coordinator(hass)
    assert hass.states.get("select.house_mode").state == "home"
    await coord.async_save_routine(
        {
            "id": "bed",
            "name": "Bed",
            "trigger": "manual",
            "actions": [{"set_mode": "night"}],
        }
    )
    assert await coord.async_run("bed")
    await hass.async_block_till_done()
    assert hass.states.get("select.house_mode").state == "night"
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.house_mode", "option": "guests"},
        blocking=True,
    )
    assert coord.mode == "guests"
    await hass.services.async_call(
        DOMAIN, "set_mode", {"mode": "vacation"}, blocking=True
    )
    assert hass.states.get("select.house_mode").state == "vacation"


# ----- workday and weather -----


async def test_workday_and_weather_conditions(hass: HomeAssistant):
    coord = await _started(hass)
    workday = {
        "kind": "workday",
        "entity_id": "binary_sensor.workday_sensor",
        "when": "workday",
    }
    day_off = {**workday, "when": "day_off"}
    now = dt_util.now()
    assert not coord._condition_ok(workday, now)  # noqa: SLF001 - unknown fails closed
    hass.states.async_set("binary_sensor.workday_sensor", "on")
    assert coord._condition_ok(workday, now)  # noqa: SLF001
    assert not coord._condition_ok(day_off, now)  # noqa: SLF001
    hass.states.async_set("weather.home", "rainy", {"temperature": 12})
    rain = normalize_routine(
        {
            "id": "w",
            "name": "W",
            "conditions": [
                {
                    "kind": "weather",
                    "entity_id": "weather.home",
                    "test": "is",
                    "conditions": ["rainy"],
                },
                {
                    "kind": "weather",
                    "entity_id": "weather.home",
                    "test": "above",
                    "value": "20",
                },
            ],
            "actions": [_light_step()],
        }
    )["conditions"]
    assert coord._condition_ok(rain[0], now)  # noqa: SLF001
    assert not coord._condition_ok(rain[1], now)  # noqa: SLF001
    assert coord._condition_ok({**rain[1], "test": "below"}, now)  # noqa: SLF001
    assert not coord._condition_ok({**rain[0], "test": "is_not"}, now)  # noqa: SLF001
    with pytest.raises(vol.Invalid):
        make_routine(
            conditions=[{"kind": "weather", "entity_id": "weather.home", "test": "is"}]
        )
    with pytest.raises(vol.Invalid):
        make_routine(conditions=[{"kind": "workday", "entity_id": "sensor.x"}])


# ----- lived-in lights -----


def test_lived_in_settings_are_checked():
    assert clean_settings(None)["enabled"] is False
    good = clean_settings(
        {
            "enabled": True,
            "lights": ["light.a"],
            "start": "18:30",
            "end": "23:00",
            "modes": ["away"],
        }
    )
    assert good["modes"] == ["away"]
    with pytest.raises(vol.Invalid):
        clean_settings({"lights": ["lock.a"], "modes": ["away"]})
    with pytest.raises(vol.Invalid):
        clean_settings({"lights": [], "start": "dusk", "modes": ["away"]})
    with pytest.raises(vol.Invalid):
        clean_settings({"lights": [], "modes": []})


async def test_lived_in_lights_turn_on_while_away_and_off_after(
    hass: HomeAssistant, freezer
):
    freezer.move_to(_at("2026-11-14 19:00:00"))
    on = async_mock_service(hass, "light", "turn_on")
    off = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.a", "off")
    hass.states.async_set("light.b", "off")
    coord = await _started(hass)
    await coord.async_set_settings(
        {
            **coord.settings,
            "lived_in": {
                "enabled": True,
                "lights": ["light.a", "light.b"],
                "start": "18:00",
                "end": "23:00",
                "modes": ["away"],
            },
        }
    )
    await hass.async_block_till_done()
    assert not on  # still home
    await coord.async_set_mode("away")
    await hass.async_block_till_done()
    assert len(on) == 1
    assert coord.lived_in.status(dt_util.now())["active"]
    await coord.lived_in.async_step(dt_util.now())  # too soon for another change
    assert len(on) == 1
    await coord.async_set_mode("home")
    await hass.async_block_till_done()
    assert len(off) == 1 and not coord.lived_in.ours
    await coord.async_set_mode("away")
    await hass.async_block_till_done()
    late = _at("2026-11-14 23:30:00")
    await coord.lived_in.async_step(late)
    assert not coord.lived_in.ours


# ----- today -----


async def test_today_lists_runs_planned_and_watching(hass: HomeAssistant, freezer):
    freezer.move_to(_at("2026-11-14 12:00:00"))
    routines = [
        make_routine(id="evening", name="Evening", time="20:00:00", skip_next=True),
        make_routine(id="morning", name="Morning", time="07:00:00"),
        make_routine(
            id="door",
            name="Door",
            trigger="state_is",
            watch_entity="lock.a",
            watch_states="unlocked",
        ),
        make_routine(id="off", name="Off", enabled=False, time="21:00:00"),
    ]
    coord = await _started(hass, *routines)
    coord.run_log.record("morning", "Morning", "ran")
    result = today(coord, dt_util.now())
    assert [p["routine_id"] for p in result["planned"]] == ["evening"]
    assert result["planned"][0]["skip_next"] is True
    assert [w["routine_id"] for w in result["watching"]] == ["door"]
    assert [d["routine_id"] for d in result["done"]] == ["morning"]


# ----- Assist -----


def test_find_routine_and_mode():
    routines = [
        {"id": "a", "name": "Morning curtains"},
        {"id": "b", "name": "Bedtime"},
        {"id": "c", "name": "Bedtime (off)", "companion_of": "b"},
    ]
    assert find_routine(routines, "the morning curtains routine")["id"] == "a"
    assert find_routine(routines, "bed time")["id"] == "b"
    assert find_routine(routines, "") is None
    assert find_mode("Night") == "night"
    assert find_mode("holiday") == "vacation"
    assert find_mode("disco") is None


async def test_assist_answers(
    hass: HomeAssistant, hass_admin_user, hass_read_only_user
):
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.hall", "on")
    coord = await _started(
        hass, make_routine(id="bed", name="Bedtime", trigger="manual", skip_noop=False)
    )
    voice = Voice(coord)
    assert (
        await voice.async_answer("run", {"routine": "bedtime"}, None)
        == "Starting Bedtime."
    )
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert "couldn't find" in await voice.async_answer(
        "run", {"routine": "disco"}, None
    )
    assert "skipped" in await voice.async_answer(
        "skip", {"routine": "bedtime"}, hass_admin_user.id
    )
    assert coord.get_routine("bed")["skip_next"]
    assert "as usual" in await voice.async_answer(
        "unskip", {"routine": "bedtime"}, None
    )
    assert (
        await voice.async_answer("mode", {"mode": "night"}, None)
        == "The house is in Night mode."
    )
    assert coord.mode == "night"
    assert "modes are" in await voice.async_answer("mode", {"mode": "party"}, None)
    assert (
        await voice.async_answer("left", {}, None) == "Nothing else is planned today."
    )
    assert "access" in await voice.async_answer(
        "run", {"routine": "bedtime"}, hass_read_only_user.id
    )


# ----- the text form -----


def test_newer_steps_and_conditions_survive_the_text_form():
    actions = [
        {"run_routine": "lights"},
        {"set_mode": "night"},
        {**_light_step(), "only_if": {"kind": "mode", "modes": ["night"]}},
        {"service": "light.turn_off", "target": {"entity_id": ["light.hall"]}},
    ]
    text = steps_to_text(actions)
    assert parse_steps_text(text) == actions
    conditions = [
        {"kind": "mode", "modes": ["away"]},
        {"kind": "state", "entity_id": "lock.a", "state": "on"},
    ]
    assert parse_conditions_text(conditions_to_text(conditions)) == conditions
    with pytest.raises(vol.Invalid):
        parse_steps_text("{not json")


def test_companion_leaves_newer_steps_out():
    routine = make_routine(
        trigger="value_above",
        value_entity="sensor.h",
        value_threshold=60,
        off_threshold=50,
        actions=[{"set_mode": "away"}, {"delay": 2}, _light_step("light.turn_on")],
    )
    companion = coordinator_module._companion_for(routine)  # noqa: SLF001
    assert companion["actions"] == [{"delay": 2.0}, {**_light_step("light.turn_off")}]


@pytest.mark.parametrize(
    ("action", "message"),
    [
        ({"run_routine": "has spaces"}, "routine to run"),
        ({"set_mode": "party"}, "house mode"),
        ({"wait_until": "soon"}, "what to wait for"),
        ({"wait_until": {"entity_id": "bad"}}, "device to wait for"),
        ({"wait_until": {"entity_id": "cover.a"}}, "state to wait for"),
        (
            {
                "wait_until": {
                    "entity_id": "cover.a",
                    "states": "closed",
                    "on_timeout": "x",
                }
            },
            "go on or stop",
        ),
        (
            {"service": "light.turn_on", "only_if": {"kind": "nope"}},
            "only if",
        ),
    ],
)
def test_new_steps_are_checked(action, message):
    with pytest.raises(vol.Invalid, match=message):
        make_routine(actions=[action, _light_step()])


@pytest.mark.parametrize(
    ("condition", "message"),
    [
        ({"kind": "mode", "modes": []}, "house modes"),
        ({"kind": "mode", "modes": ["party"]}, "home, away"),
        ({"kind": "workday", "entity_id": "binary_sensor.w", "when": "x"}, "workdays"),
        ({"kind": "weather", "entity_id": "sensor.x"}, "weather"),
        ({"kind": "weather", "entity_id": "weather.a", "test": "x"}, "temperature"),
        (
            {"kind": "weather", "entity_id": "weather.a", "test": "above"},
            "Set the temperature",
        ),
    ],
)
def test_new_conditions_are_checked(condition, message):
    with pytest.raises(vol.Invalid, match=message):
        make_routine(conditions=[condition])


async def test_assist_explains_why_it_didnt_run(hass: HomeAssistant):
    hass.states.async_set("lock.a", "locked")
    blocked = make_routine(
        id="blocked",
        name="Blocked",
        trigger="manual",
        conditions=[{"kind": "state", "entity_id": "lock.a", "state": "unlocked"}],
    )
    off = make_routine(id="off", name="Sleepy", trigger="manual", enabled=False)
    slow = make_routine(
        id="slow", name="Slow", trigger="manual", actions=[{"delay": 5}, _light_step()]
    )
    coord = await _started(hass, blocked, off, slow)
    voice = Voice(coord)
    assert "conditions" in await voice.async_answer("run", {"routine": "blocked"}, None)
    assert "turned off" in await voice.async_answer("run", {"routine": "sleepy"}, None)
    coord._rt("slow")["state"] = "running"  # noqa: SLF001
    assert "already running" in await voice.async_answer(
        "run", {"routine": "slow"}, None
    )


async def test_assist_says_whats_next(hass: HomeAssistant, freezer):
    freezer.move_to(_at("2026-11-14 12:00:00"))
    coord = await _started(
        hass,
        make_routine(id="a", name="Evening", time="20:00:00"),
        make_routine(id="b", name="Late", time="23:15:00"),
    )
    answer = await Voice(coord).async_answer("left", {}, None)
    assert answer == "Next is Evening at 8:00 PM, then 1 more today."
