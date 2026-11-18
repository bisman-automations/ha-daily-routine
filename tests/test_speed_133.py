"""1.33.0: the same answers with less work.

Linked devices and the panel's device lists kept until the registries
change, the Activity summary in one pass over the log, saving one routine
restarting only that routine, live updates sending only what changed, a
calendar read once when two things need it, slow reads after starting, and
the health check only when a device goes missing or comes back.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta
import random
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daily_routine import coordinator as coordinator_module, panel
from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.coordinator import (
    H_HEALTH_TIMER,
    H_TIME,
    H_VALUE,
    HomeRoutinesCoordinator,
    _titles_between,
)
from custom_components.daily_routine.linked import (
    _linked_groups,
    _linked_integrations,
    linked_groups,
    linked_integrations,
)
from custom_components.daily_routine.registry_cache import (
    async_track_registries,
    get_registry_cache,
)
from custom_components.daily_routine.run_log import (
    RECENT_LIMIT,
    REPEAT_THRESHOLD,
    WINDOWS,
    RunLog,
    _ranked,
    _when,
    daily_counts,
    needs_attention,
    summarize,
    window_start,
)
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from .conftest import make_routine
from .test_live_panel_api import _setup

MAC = "aa:bb:cc:dd:ee:01"
OTHER_MAC = "aa:bb:cc:dd:ee:02"


def _device(
    hass: HomeAssistant, domain: str, entity_id: str, mac: str
) -> dr.DeviceEntry:
    entry = MockConfigEntry(domain=domain)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(domain, f"{domain}-{entity_id}")},
        connections={(dr.CONNECTION_NETWORK_MAC, mac)},
        name="Curtain",
    )
    platform, object_id = entity_id.split(".")
    er.async_get(hass).async_get_or_create(
        platform,
        domain,
        f"{domain}-{object_id}",
        suggested_object_id=object_id,
        device_id=device.id,
        config_entry=entry,
    )
    return device


def _levels(groups) -> list[list[list[str]]]:
    return [[list(level.entity_ids) for level in g.levels] for g in groups]


# ----- linked devices, kept until the registries change -----


async def test_linked_groups_kept_until_a_registry_changes(hass: HomeAssistant):
    matter = _device(hass, "matter", "cover.curtain_matter", MAC)
    _device(hass, "switchbot", "cover.curtain_ble", MAC)
    other = _device(hass, "switchbot_cloud", "cover.curtain_cloud", OTHER_MAC)
    step = ["cover.curtain_ble"]
    order = ["matter", "switchbot", "switchbot_cloud"]

    stop = async_track_registries(hass)
    try:
        cache = get_registry_cache(hass)
        assert cache is not None
        first = linked_groups(hass, step, order)
        built = cache.builds
        # The same step again: nothing worked out again, the same groups.
        assert linked_groups(hass, step, order) == first
        assert cache.builds == built
        assert _levels(first) == [[["cover.curtain_matter"], ["cover.curtain_ble"]]]
        assert linked_integrations(hass) == _linked_integrations(hass)

        # A device registry update (the cloud curtain now shares the MAC):
        # dropped, and the new answer is what working it out afresh gives.
        version = cache.version
        dr.async_get(hass).async_update_device(
            other.id,
            new_connections=other.connections | {(dr.CONNECTION_NETWORK_MAC, MAC)},
        )
        assert cache.version != version
        again = linked_groups(hass, step, order)
        assert again == _linked_groups(hass, step, order)
        assert _levels(again) == [
            [
                ["cover.curtain_matter"],
                ["cover.curtain_ble"],
                ["cover.curtain_cloud"],
            ]
        ]
        assert "switchbot_cloud" in linked_integrations(hass)

        # An entity registry update (the Matter entity turned off) too.
        er.async_get(hass).async_update_entity(
            "cover.curtain_matter", disabled_by=er.RegistryEntryDisabler.USER
        )
        third = linked_groups(hass, step, order)
        assert third == _linked_groups(hass, step, order)
        assert _levels(third) == [[["cover.curtain_ble"], ["cover.curtain_cloud"]]]

        # And a device removed.
        dr.async_get(hass).async_remove_device(matter.id)
        assert linked_groups(hass, step, order) == _linked_groups(hass, step, order)
    finally:
        stop()
    # Off again: nothing kept, every call works it out.
    assert get_registry_cache(hass) is None
    assert linked_groups(hass, step, order) == _linked_groups(hass, step, order)


async def test_registry_cache_is_counted_and_versions_differ(hass: HomeAssistant):
    first = async_track_registries(hass)
    second = async_track_registries(hass)
    cache = get_registry_cache(hass)
    assert cache is not None
    first()
    first()  # twice is the same as once
    assert get_registry_cache(hass) is cache  # the other one still tracks
    old = cache.version
    second()
    assert get_registry_cache(hass) is None
    third = async_track_registries(hass)
    try:
        # A new cache never reuses a version a page might still hold.
        assert get_registry_cache(hass).version != old
    finally:
        third()


async def test_coordinator_tracks_registries_while_loaded(
    hass: HomeAssistant, enable_custom_integrations
):
    coord = await _setup(hass)
    assert get_registry_cache(hass) is not None
    await coord.async_unload(save=False)
    assert get_registry_cache(hass) is None


async def test_saving_a_routine_keeps_the_cache(
    hass: HomeAssistant, enable_custom_integrations
):
    """Saving updates the routine's own device, which nothing kept includes."""
    coord = await _setup(hass)
    raw = {
        "id": "r",
        "name": "R",
        "trigger": "time",
        "time": "23:59:00",
        "actions": [
            {"service": "light.turn_off", "target": {"entity_id": ["light.l"]}}
        ],
    }
    await coord.async_save_routine(raw)
    await hass.async_block_till_done()
    cache = get_registry_cache(hass)
    version = cache.version
    await coord.async_save_routine({**raw, "time": "23:58:00", "name": "Renamed"})
    await hass.async_block_till_done()
    assert cache.version == version
    # Someone else's device still counts.
    device = _device(hass, "hue", "light.lamp", OTHER_MAC)
    assert cache.version != version
    version = cache.version
    dr.async_get(hass).async_update_device(device.id, name_by_user="Lamp")
    assert cache.version != version
    # A firmware version or an entity's name: nothing kept reads those.
    version = cache.version
    dr.async_get(hass).async_update_device(device.id, sw_version="2.0")
    er.async_get(hass).async_update_entity("light.lamp", name="Desk lamp")
    assert cache.version == version
    # A new entity id does change each entity's integration.
    er.async_get(hass).async_update_entity("light.lamp", new_entity_id="light.desk")
    assert cache.version != version


# ----- the Activity summary in one pass -----


def _old_summarize(events, window, now, routines):
    """summarize as it was before 1.33.0, to compare against."""
    if window not in WINDOWS:
        window = "7d"
    since = window_start(window, now)
    in_window = [e for e in events if _when(e) >= since]
    ran = sum(1 for e in in_window if e["outcome"] == "ran")
    failed = sum(1 for e in in_window if e["outcome"] == "failed")
    skipped = sum(1 for e in in_window if e["outcome"] == "skipped")
    attempted = ran + failed
    by_routine = []
    for routine in routines:
        mine = [e for e in in_window if e.get("routine_id") == routine["id"]]
        everything = [e for e in events if e.get("routine_id") == routine["id"]]
        last_failure = next(
            (e for e in reversed(mine) if e["outcome"] == "failed"), None
        )
        last = mine[-1] if mine else None
        streak = 0
        for event in reversed(everything):
            if event["outcome"] != "failed":
                break
            streak += 1
        by_routine.append(
            {
                "id": routine["id"],
                "name": routine["name"],
                "enabled": routine["enabled"],
                "ran": sum(1 for e in mine if e["outcome"] == "ran"),
                "failed": sum(1 for e in mine if e["outcome"] == "failed"),
                "skipped": sum(1 for e in mine if e["outcome"] == "skipped"),
                "last_at": last["at"] if last else None,
                "last_outcome": last["outcome"] if last else None,
                "last_failure": (
                    {"at": last_failure["at"], "reason": last_failure["reason"]}
                    if last_failure
                    else None
                ),
                "failing_in_a_row": streak,
            }
        )
    failure_reasons = Counter(
        e["reason"] for e in in_window if e["outcome"] == "failed"
    )
    skip_reasons = Counter(e["reason"] for e in in_window if e["outcome"] == "skipped")
    days: dict[str, dict[str, int]] = {}
    for event in in_window:
        key = dt_util.as_local(_when(event)).date().isoformat()
        bucket = days.setdefault(key, {"ran": 0, "failed": 0, "skipped": 0})
        bucket[event["outcome"]] += 1
    recent = [
        {
            k: e.get(k, "")
            for k in ("at", "routine_id", "name", "outcome", "reason", "detail")
        }
        for e in reversed(in_window[-RECENT_LIMIT:])
    ]
    return {
        "window": window,
        "totals": {
            "triggered": len(in_window),
            "ran": ran,
            "failed": failed,
            "skipped": skipped,
            "success_rate": round(100 * ran / attempted) if attempted else None,
        },
        "by_routine": by_routine,
        "failure_reasons": _ranked(failure_reasons),
        "skip_reasons": _ranked(skip_reasons),
        "by_day": [{"day": day, **counts} for day, counts in sorted(days.items())],
        "recent": recent,
        "quiet_routines": [
            r["name"]
            for r in by_routine
            if r["enabled"] and not (r["ran"] + r["failed"] + r["skipped"])
        ],
        "repeated_failures": [
            {"name": r["name"], "count": r["failing_in_a_row"]}
            for r in by_routine
            if r["failing_in_a_row"] >= REPEAT_THRESHOLD
        ],
    }


def _random_events(now: datetime, count: int, seed: int, ordered: bool):
    rng = random.Random(seed)
    times = sorted(
        now - timedelta(minutes=rng.randint(0, 30 * 24 * 60)) for _ in range(count)
    )
    if not ordered:
        # The clock went back once or twice: a few events out of order.
        for _ in range(5):
            i, j = rng.randrange(count), rng.randrange(count)
            times[i], times[j] = times[j], times[i]
    events = []
    for at in times:
        outcome = rng.choice(["ran", "ran", "failed", "skipped"])
        events.append(
            {
                "at": at.isoformat(),
                "routine_id": f"r{rng.randrange(8)}",
                "name": "x",
                "outcome": outcome,
                "reason": rng.choice(["", "not_today", "unavailable", "paused"]),
                "detail": "",
            }
        )
    # Three failures in a row at the end for one routine.
    for minutes in (3, 2, 1):
        events.append(
            {
                "at": (now - timedelta(minutes=minutes)).isoformat(),
                "routine_id": "r1",
                "name": "x",
                "outcome": "failed",
                "reason": "unavailable",
                "detail": "",
            }
        )
    return events


@pytest.mark.parametrize("ordered", [True, False])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_summarize_matches_the_old_way(ordered: bool, seed: int) -> None:
    now = dt_util.now()
    events = _random_events(now, 600, seed, ordered)
    routines = [{"id": f"r{i}", "name": f"R{i}", "enabled": i != 3} for i in range(10)]
    times = [_when(e) for e in events]
    in_order = all(a <= b for a, b in zip(times, times[1:], strict=False))
    assert in_order is ordered
    for window in (*WINDOWS, "nonsense"):
        old = _old_summarize(events, window, now, routines)
        assert summarize(events, window, now, routines) == old
        assert (
            summarize(events, window, now, routines, times=times, ordered=in_order)
            == old
        )
    # The three failures at the end (or more, when the one before failed too).
    assert any(
        r["name"] == "R1" and r["count"] >= REPEAT_THRESHOLD
        for r in old["repeated_failures"]
    )


@pytest.mark.parametrize("ordered", [True, False])
def test_attention_and_daily_counts_match_with_times(ordered: bool) -> None:
    now = dt_util.now()
    events = _random_events(now, 400, 7, ordered)
    routines = [{"id": f"r{i}", "name": f"R{i}", "enabled": True} for i in range(8)]
    times = [_when(e) for e in events]
    assert needs_attention(events, now, routines) == needs_attention(
        events, now, routines, times=times, ordered=ordered
    )
    for rid in (None, "r2"):
        assert daily_counts(events, now, 30, rid) == daily_counts(
            events, now, 30, rid, times=times
        )


async def test_run_log_times_since_and_order(hass: HomeAssistant, freezer) -> None:
    log = RunLog(hass)
    start = dt_util.now()
    for minutes in range(5):
        freezer.move_to(start + timedelta(minutes=minutes))
        log.record(f"r{minutes % 2}", "R", "ran")
    assert log.ordered
    assert list(log.times()) == [_when(e) for e in log.view()]
    cut = start + timedelta(minutes=2)
    assert log.since(cut) == [e for e in log.events() if _when(e) >= cut]
    assert log.view() is log.view()  # the log itself, not a copy
    # The clock goes back: still right, without bisection.
    freezer.move_to(start - timedelta(minutes=1))
    log.record("r0", "R", "failed")
    assert not log.ordered
    assert log.since(cut) == [e for e in log.events() if _when(e) >= cut]
    # Forgetting a routine keeps the times next to their events.
    log.forget_routine("r1")
    assert [e["routine_id"] for e in log.view()] == ["r0"] * 4
    assert list(log.times()) == [_when(e) for e in log.view()]
    assert not log.ordered
    log.forget_routine("r0")
    assert log.ordered and not log.view()


async def test_run_log_load_skips_events_without_a_time(
    hass: HomeAssistant, hass_storage
) -> None:
    now = dt_util.now()
    hass_storage[f"{DOMAIN}.runlog"] = {
        "version": 1,
        "key": f"{DOMAIN}.runlog",
        "data": {
            "events": [
                {"at": now.isoformat(), "routine_id": "a", "outcome": "ran"},
                {"at": "not a time", "routine_id": "b", "outcome": "ran"},
                {"routine_id": "c", "outcome": "ran"},
            ]
        },
    }
    log = RunLog(hass)
    await log.async_load()
    assert [e["routine_id"] for e in log.view()] == ["a"]


# ----- saving one routine restarts only that routine -----


async def test_saving_a_routine_leaves_the_others_running(
    hass: HomeAssistant, enable_custom_integrations
):
    coord = await _setup(hass)
    hass.services.async_register("light", "turn_off", lambda call: None)
    await coord.async_save_routine(
        {
            "id": "other",
            "name": "Other",
            "trigger": "time",
            "time": "06:00:00",
            "more_triggers": [{"trigger": "time", "time": "07:00:00"}],
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": ["light.x"]}}
            ],
        }
    )
    other_time = coord._handles[("other", H_TIME)]  # noqa: SLF001
    other_alias = coord._aliases["other~2"]  # noqa: SLF001
    other_alias_time = coord._handles[("other~2", H_TIME)]  # noqa: SLF001
    # A retry planned for the other routine (a full reload used to drop it).
    dropped: list[str] = []
    coord._set_handle("other", "retry_later", lambda: dropped.append("other"))  # noqa: SLF001

    await coord.async_save_routine(
        {
            "id": "mine",
            "name": "Mine",
            "trigger": "time",
            "time": "08:00:00",
            "more_triggers": [{"trigger": "time", "time": "09:00:00"}],
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": ["light.x"]}}
            ],
        }
    )
    assert dropped == []
    assert coord._handles[("other", H_TIME)] is other_time  # noqa: SLF001
    assert coord._handles[("other~2", H_TIME)] is other_alias_time  # noqa: SLF001
    assert coord._aliases["other~2"] is other_alias  # noqa: SLF001
    assert ("mine", H_TIME) in coord._handles  # noqa: SLF001
    assert ("mine~2", H_TIME) in coord._handles  # noqa: SLF001
    assert list(coord._aliases) == ["other~2", "mine~2"]  # noqa: SLF001

    # Saving it again without its other trigger: that trigger's watchers and
    # status go, and nothing of the other routine changes.
    mine_time = coord._handles[("mine", H_TIME)]  # noqa: SLF001
    await coord.async_save_routine(
        {
            "id": "mine",
            "name": "Mine",
            "trigger": "time",
            "time": "08:30:00",
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": ["light.x"]}}
            ],
        }
    )
    assert coord._handles[("mine", H_TIME)] is not mine_time  # noqa: SLF001
    assert ("mine~2", H_TIME) not in coord._handles  # noqa: SLF001
    assert "mine~2" not in coord._runtime  # noqa: SLF001
    assert coord._handles[("other", H_TIME)] is other_time  # noqa: SLF001
    assert dropped == []

    # The same as restarting everything.
    planned = {rid: coord._rt(rid)["next_run"] for rid in ("mine", "other", "other~2")}  # noqa: SLF001
    await coord.async_reload()
    assert {
        rid: coord._rt(rid)["next_run"]
        for rid in ("mine", "other", "other~2")  # noqa: SLF001
    } == planned
    assert dropped == ["other"]  # a full reload still restarts everything

    # Deleting one: only its handles go.
    other_time = coord._handles[("other", H_TIME)]  # noqa: SLF001
    await coord.async_delete_routine("mine")
    assert not [key for key in coord._handles if key[0] == "mine"]  # noqa: SLF001
    assert coord._handles[("other", H_TIME)] is other_time  # noqa: SLF001


async def test_saving_restarts_a_turn_off_routine_too(
    hass: HomeAssistant, enable_custom_integrations
):
    coord = await _setup(hass)
    hass.states.async_set("sensor.hall_humidity", "40")
    raw = make_routine(
        id="fan",
        name="Fan",
        trigger="value_above",
        value_entity="sensor.hall_humidity",
        value_threshold=60,
        off_threshold=50,
        actions=[
            {
                "service": "fan.turn_on",
                "target": {"entity_id": ["fan.hall"]},
                "data": {},
            }
        ],
    )
    await coord.async_save_routine(raw)
    await coord.async_save_routine({**raw, "id": "other", "name": "Other"})
    other = coord._handles[("other_off", H_VALUE)]  # noqa: SLF001
    old = coord._handles[("fan_off", H_VALUE)]  # noqa: SLF001
    await coord.async_save_routine({**raw, "off_threshold": 45})
    # The turn-off routine was made again and is watched again...
    assert coord._find("fan_off")["value_threshold"] == 45  # noqa: SLF001
    assert coord._handles[("fan_off", H_VALUE)] is not old  # noqa: SLF001
    # ...and the other routine's turn-off routine was left alone.
    assert coord._handles[("other_off", H_VALUE)] is other  # noqa: SLF001
    # Without a turn-off level, its turn-off routine and watchers go.
    await coord.async_save_routine({**raw, "off_threshold": None})
    assert coord._find("fan_off") is None  # noqa: SLF001
    assert not [key for key in coord._handles if key[0] == "fan_off"]  # noqa: SLF001


# ----- live updates send only what changed -----


def test_status_delta() -> None:
    before = {
        "revision": 1,
        "paused": False,
        "statuses": {"a": {"state": "idle"}, "b": {"state": "idle"}},
    }
    assert panel.status_delta(before, before) is None
    after = {
        "revision": 1,
        "paused": True,
        "statuses": {"a": {"state": "running"}, "c": {"state": "idle"}},
    }
    assert panel.status_delta(before, after) == {
        "revision": 1,
        "paused": True,
        "delta": True,
        "statuses": {"a": {"state": "running"}, "c": {"state": "idle"}},
        "removed": ["b"],
    }


async def test_subscribe_with_delta_sends_changes_only(
    hass: HomeAssistant, enable_custom_integrations, hass_ws_client
):
    coord = await _setup(hass)
    hass.services.async_register("light", "turn_off", lambda call: None)
    for rid in ("a", "b"):
        await coord.async_save_routine(
            {
                "id": rid,
                "name": rid.upper(),
                "trigger": "manual",
                "actions": [
                    {"service": "light.turn_off", "target": {"entity_id": ["light.x"]}}
                ],
            }
        )
    panel._shared.clear()  # noqa: SLF001
    delta = await hass_ws_client(hass)
    full = await hass_ws_client(hass)
    await delta.send_json({"id": 1, "type": f"{DOMAIN}/subscribe", "delta": True})
    await full.send_json({"id": 1, "type": f"{DOMAIN}/subscribe"})
    for client in (delta, full):
        assert (await client.receive_json())["success"]
        first = (await client.receive_json())["event"]
        assert "delta" not in first and set(first["statuses"]) == {"a", "b"}

    # One routine's status changes.
    coord._rt("a")["state"] = "waiting"  # noqa: SLF001
    coord._notify_listeners()  # noqa: SLF001
    await hass.async_block_till_done()
    await asyncio.sleep(panel.PUSH_SECONDS + 0.05)
    await hass.async_block_till_done()
    changed = (await delta.receive_json())["event"]
    assert changed["delta"] is True
    assert set(changed["statuses"]) == {"a"}
    assert changed["statuses"]["a"]["state"] == "waiting"
    assert changed["removed"] == []
    assert changed["revision"] == coord.revision
    # Without delta: the whole status, as before.
    whole = (await full.receive_json())["event"]
    assert set(whole["statuses"]) == {"a", "b"} and "delta" not in whole


# ----- the overview leaves out a device list the page already has -----


async def test_overview_keeps_the_catalog_until_the_registries_change(
    hass: HomeAssistant, enable_custom_integrations, hass_ws_client
):
    await _setup(hass)
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/overview"})
    first = (await client.receive_json())["result"]
    assert "devices" in first and "integrations" in first
    version = first["catalog"]
    assert isinstance(version, str)

    await client.send_json({"id": 2, "type": f"{DOMAIN}/overview", "catalog": version})
    kept = (await client.receive_json())["result"]
    assert kept["catalog_kept"] is True and kept["catalog"] == version
    assert "devices" not in kept and "integrations" not in kept
    # Everything else is still there.
    assert set(first) - set(kept) == {"devices", "integrations"}

    # A new device: the version moves on and the lists come again.
    _device(hass, "matter", "cover.new_curtain", MAC)
    await client.send_json({"id": 3, "type": f"{DOMAIN}/overview", "catalog": version})
    fresh = (await client.receive_json())["result"]
    assert fresh["catalog"] != version and "catalog_kept" not in fresh
    assert "cover.new_curtain" in fresh["integrations"]


async def test_scripts_carry_the_version_and_may_be_kept(
    hass: HomeAssistant, enable_custom_integrations, monkeypatch
):
    paths: list[Any] = []
    real_config = panel.StaticPathConfig

    def record(*args, **kwargs):
        config = real_config(*args, **kwargs)
        paths.append(config)
        return config

    monkeypatch.setattr(panel, "StaticPathConfig", record)
    urls: list[str] = []
    monkeypatch.setattr(
        panel.frontend, "add_extra_js_url", lambda _hass, url: urls.append(url)
    )
    modules: list[str] = []
    real_register = panel.panel_custom.async_register_panel

    async def register(*args, **kwargs):
        modules.append(kwargs["module_url"])
        return await real_register(*args, **kwargs)

    monkeypatch.setattr(panel.panel_custom, "async_register_panel", register)
    await _setup(hass)
    from homeassistant.loader import async_get_integration  # noqa: PLC0415

    version = (await async_get_integration(hass, DOMAIN)).version
    assert urls == [f"/{DOMAIN}_static/daily-routine-card.js?v={version}"]
    assert modules == [f"/{DOMAIN}_static/daily-routine-panel.js?v={version}"]
    assert paths and all(config.cache_headers for config in paths)


# ----- calendars: read once, at the same time; slow reads after starting -----


def test_titles_between() -> None:
    day = dt_util.start_of_local_day(dt_util.now())
    end = day + timedelta(days=1)
    events = [
        {
            "summary": "Yesterday",
            "start": (day - timedelta(hours=3)).isoformat(),
            "end": (day - timedelta(hours=1)).isoformat(),
        },
        {
            "summary": "Overnight",
            "start": (day - timedelta(hours=1)).isoformat(),
            "end": (day + timedelta(hours=1)).isoformat(),
        },
        {
            "summary": "School",
            "start": (day + timedelta(hours=8)).isoformat(),
            "end": (day + timedelta(hours=15)).isoformat(),
        },
        {
            "summary": "All day",
            "start": day.date().isoformat(),
            "end": end.date().isoformat(),
        },
        {
            "summary": "Tomorrow all day",
            "start": end.date().isoformat(),
            "end": (end + timedelta(days=1)).date().isoformat(),
        },
        {
            "summary": "Reminder",
            "start": (day + timedelta(hours=9)).isoformat(),
            "end": (day + timedelta(hours=9)).isoformat(),
        },
        {
            "summary": "Ends at midnight",
            "start": (day - timedelta(hours=2)).isoformat(),
            "end": day.isoformat(),
        },
        {
            "summary": "Tomorrow",
            "start": (end + timedelta(hours=8)).isoformat(),
            "end": (end + timedelta(hours=9)).isoformat(),
        },
    ]
    assert _titles_between(events, day, end) == [
        "Overnight",
        "School",
        "All day",
        "Reminder",
    ]
    assert _titles_between(None, day, end) is None
    with pytest.raises(ValueError):
        _titles_between([{"summary": "No times"}], day, end)


def _calendar_service(hass: HomeAssistant, events: list[dict[str, Any]]):
    calls: list[tuple[str, str, str]] = []

    async def get_events(call: ServiceCall):
        entity = call.data["entity_id"]
        entity = entity[0] if isinstance(entity, list) else entity
        calls.append((entity, call.data["start_date_time"], call.data["end_date_time"]))
        return {entity: {"events": events}}

    hass.services.async_register(
        "calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY
    )
    return calls


async def test_a_calendar_two_things_use_is_read_once(hass: HomeAssistant):
    day = dt_util.start_of_local_day(dt_util.now())
    calls = _calendar_service(
        hass,
        [
            {
                "summary": "School",
                "start": (day + timedelta(hours=8)).isoformat(),
                "end": (day + timedelta(hours=15)).isoformat(),
            },
            {
                "summary": "Trip",
                "start": (day + timedelta(days=1, hours=8)).isoformat(),
                "end": (day + timedelta(days=1, hours=9)).isoformat(),
            },
        ],
    )
    for entity in ("calendar.family", "calendar.school"):
        hass.states.async_set(entity, "off")
    conditional = make_routine(
        id="cond",
        conditions=[
            {"kind": "calendar", "entity_id": "calendar.family", "match": "school"}
        ],
    )
    before_event = make_routine(
        id="before",
        trigger="calendar_event",
        watch_entity="calendar.family",
        watch_item="trip",
        offset_minutes=-30,
    )
    only_condition = make_routine(
        id="only",
        conditions=[
            {"kind": "calendar", "entity_id": "calendar.school", "match": "school"}
        ],
    )
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [conditional, before_event, only_condition]
    coord._start_all(catch_up=False)  # noqa: SLF001
    try:
        await coord._on_calendar_interval(dt_util.now())  # noqa: SLF001
        entities = [entity for entity, _start, _end in calls]
        # calendar.family once (for both), calendar.school once (today only).
        assert sorted(entities) == ["calendar.family", "calendar.school"]
        assert coord._calendar_days["calendar.family"][1] == ["School"]  # noqa: SLF001
        assert coord._calendar_days["calendar.school"][1] == ["School", "Trip"]  # noqa: SLF001
        assert [e["summary"] for e in coord._upcoming["calendar.family"]] == [  # noqa: SLF001
            "School",
            "Trip",
        ]
    finally:
        await coord.async_unload(save=False)


async def test_starting_does_not_wait_for_slow_reads(
    hass: HomeAssistant, enable_custom_integrations, monkeypatch
):
    release = asyncio.Event()
    started: list[str] = []

    async def slow_upcoming(self, read=None, now=None):
        started.append("upcoming")
        await release.wait()

    async def slow_forecasts(self, _now=None):
        started.append("forecasts")
        await release.wait()

    monkeypatch.setattr(HomeRoutinesCoordinator, "_refresh_upcoming", slow_upcoming)
    monkeypatch.setattr(HomeRoutinesCoordinator, "_check_forecasts", slow_forecasts)
    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", unique_id=DOMAIN)
    entry.add_to_hass(hass)
    # Set up while both reads are still going (they only end when released,
    # so waiting for them would never finish).
    assert await asyncio.wait_for(
        hass.config_entries.async_setup(entry.entry_id), timeout=30
    )
    for _ in range(5):
        await asyncio.sleep(0)
    # Both started, at the same time.
    assert sorted(started) == ["forecasts", "upcoming"]
    release.set()
    await hass.async_block_till_done()


async def test_reads_finishing_after_unload_plan_nothing(hass: HomeAssistant):
    hass.states.async_set("weather.home", "sunny")
    release = asyncio.Event()

    async def get_forecasts(call: ServiceCall):
        await release.wait()
        return {"weather.home": {"forecast": [{"condition": "rainy"}]}}

    hass.services.async_register(
        "weather",
        "get_forecasts",
        get_forecasts,
        supports_response=SupportsResponse.ONLY,
    )
    routine = make_routine(
        trigger="forecast", watch_entity="weather.home", watch_states=["rainy"]
    )
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [routine]
    coord._start_all(catch_up=False)  # noqa: SLF001
    task = hass.async_create_task(coord._check_forecasts())  # noqa: SLF001
    await asyncio.sleep(0)
    await coord.async_unload(save=False)
    release.set()
    await task
    assert coord._rt(routine["id"]).get("forecast_hit") is None  # noqa: SLF001
    assert not coord._handles  # noqa: SLF001


# ----- the health check only when a device goes missing or comes back -----


async def test_health_watch_ignores_on_and_off(hass: HomeAssistant):
    hass.states.async_set("binary_sensor.door", "off")
    routine = make_routine(
        trigger="state_is",
        watch_entity="binary_sensor.door",
        watch_states=["on"],
        notify_on_health=True,
    )
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [routine]
    coord._start_all(catch_up=False)  # noqa: SLF001
    try:
        rid = routine["id"]
        hass.states.async_set("binary_sensor.door", "on")
        await hass.async_block_till_done()
        assert (rid, H_HEALTH_TIMER) not in coord._handles  # noqa: SLF001
        hass.states.async_set("binary_sensor.door", "unavailable")
        await hass.async_block_till_done()
        assert (rid, H_HEALTH_TIMER) in coord._handles  # noqa: SLF001
        coord._drop_handle(rid, H_HEALTH_TIMER)  # noqa: SLF001
        hass.states.async_set("binary_sensor.door", "unknown")  # still not there
        await hass.async_block_till_done()
        assert (rid, H_HEALTH_TIMER) not in coord._handles  # noqa: SLF001
        hass.states.async_set("binary_sensor.door", "off")  # back
        await hass.async_block_till_done()
        assert (rid, H_HEALTH_TIMER) in coord._handles  # noqa: SLF001
        coord._drop_handle(rid, H_HEALTH_TIMER)  # noqa: SLF001
        hass.states.async_remove("binary_sensor.door")  # gone
        await hass.async_block_till_done()
        assert (rid, H_HEALTH_TIMER) in coord._handles  # noqa: SLF001
    finally:
        await coord.async_unload(save=False)


def test_module_constants_unchanged() -> None:
    # The span the shared read covers is the one "before an event" reads.
    now = dt_util.now()
    start, end = HomeRoutinesCoordinator._upcoming_span(now)  # noqa: SLF001
    assert start == now - timedelta(days=1)
    assert end == now + coordinator_module.EVENTS_AHEAD
