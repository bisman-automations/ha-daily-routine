"""1.31.1: the same answers with less work: one update per moment, the pause
switch told at once, run saves gathered up, old habits dropped from the
front, ideas kept until something changes, lived-in only ticking while on,
and one live payload for every open screen."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.daily_routine import habits as habits_module, panel
from custom_components.daily_routine.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .test_live_panel_api import _setup


async def test_one_update_per_moment(hass: HomeAssistant, enable_custom_integrations):
    coord = await _setup(hass)
    later: list[int] = []
    now: list[int] = []
    coord.async_add_listener(lambda: later.append(1))
    coord.async_add_listener(lambda: now.append(1), now=True)
    for _ in range(5):
        coord._notify_listeners()  # noqa: SLF001
    assert now == [1] * 5  # the pause switch and House mode: every time
    assert later == []  # status sensors and the panel: after the moment
    await hass.async_block_till_done()
    assert later == [1]
    count = coord.notify_count
    coord._notify_listeners()  # noqa: SLF001
    await hass.async_block_till_done()
    assert later == [1, 1] and coord.notify_count == count + 1


async def test_run_saves_are_gathered(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, freezer
):
    coord = await _setup(hass)
    hass.services.async_register("light", "turn_off", lambda call: None)
    await coord.async_save_routine(
        {
            "id": "r",
            "name": "R",
            "trigger": "manual",
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": ["light.x"]}}
            ],
        }
    )
    key = f"{DOMAIN}.routines"
    before = hass_storage[key]["data"]["runtime"].get("r", {}).get("last_result")
    await coord.async_run("r")
    await hass.async_block_till_done()
    # Not written yet: gathered up with whatever else changes meanwhile...
    assert hass_storage[key]["data"]["runtime"].get("r", {}).get("last_result") == (
        before
    )
    freezer.tick(timedelta(seconds=3))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    # ...and written shortly after.
    assert hass_storage[key]["data"]["runtime"]["r"]["last_result"] == "success"


def test_drop_old_reads_only_the_front() -> None:
    now = dt_util.now()
    events: list[dict[str, Any]] = [
        {"e": "light.a", "at": (now - timedelta(days=30 - i)).isoformat()}
        for i in range(30)
    ]
    cutoff = now - timedelta(days=21)
    habits_module.drop_old(events, cutoff)
    assert all(dt_util.parse_datetime(e["at"]) >= cutoff for e in events)
    assert len(events) == 21
    # Too many: the oldest go.
    many = [{"e": "light.a", "at": now.isoformat()}] * (habits_module.MAX_EVENTS + 7)
    habits_module.drop_old(many, cutoff)
    assert len(many) == habits_module.MAX_EVENTS


async def test_habits_skip_other_domains_and_keep_ideas(
    hass: HomeAssistant, enable_custom_integrations
):
    coord = await _setup(hass)
    assert not habits_module._habit_domain({"entity_id": "sensor.power"})  # noqa: SLF001
    assert habits_module._habit_domain({"entity_id": "light.hall"})  # noqa: SLF001
    found: list[int] = []
    real = habits_module.suggest

    def counted(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        found.append(1)
        return real(*args, **kwargs)

    habits_module.suggest = counted
    try:
        now = dt_util.now()
        coord.habits.ideas(now)
        coord.habits.ideas(now)
        assert found == [1]  # nothing changed: the same ideas
        coord.habits.events.append(
            {"e": "light.hall", "on": True, "at": now.isoformat()}
        )
        coord.habits.ideas(now)
        assert found == [1, 1]  # a new habit event: looked at again
        coord.habits.ideas(now + timedelta(days=1))
        assert found == [1, 1, 1]  # a new day too
    finally:
        habits_module.suggest = real


async def test_lived_in_ticks_only_while_on(
    hass: HomeAssistant, enable_custom_integrations
):
    coord = await _setup(hass)
    assert coord.lived_in._unsub is None  # off by default: no timer  # noqa: SLF001
    hass.states.async_set("light.hall", "off")
    await coord.async_set_settings(
        {"lived_in": {"enabled": True, "lights": ["light.hall"], "modes": ["away"]}}
    )
    await hass.async_block_till_done()
    assert coord.lived_in._unsub is not None  # noqa: SLF001
    await coord.async_set_settings(
        {"lived_in": {"enabled": False, "lights": ["light.hall"], "modes": ["away"]}}
    )
    await hass.async_block_till_done()
    assert coord.lived_in._unsub is None  # noqa: SLF001


async def test_live_push_is_shared_and_skips_repeats(
    hass: HomeAssistant, enable_custom_integrations, hass_ws_client
):
    coord = await _setup(hass)
    built: list[int] = []
    real = panel._status  # noqa: SLF001

    def counted(current: Any) -> dict[str, Any]:
        built.append(1)
        return real(current)

    panel._status = counted  # noqa: SLF001
    panel._shared.clear()  # noqa: SLF001
    try:
        first = await hass_ws_client(hass)
        second = await hass_ws_client(hass)
        for client in (first, second):
            await client.send_json({"id": 1, "type": f"{DOMAIN}/subscribe"})
            assert (await client.receive_json())["success"]
            assert (await client.receive_json())["type"] == "event"
        # Two screens, one payload built.
        assert built == [1]
        coord._notify_listeners()  # noqa: SLF001
        await hass.async_block_till_done()
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
        await hass.async_block_till_done()
        # Built once more for the update, and nothing sent: nothing changed.
        assert built == [1, 1]
    finally:
        panel._status = real  # noqa: SLF001
