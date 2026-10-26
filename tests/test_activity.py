"""The Activity tab: run log, its summary, and the websocket command that serves it."""

from __future__ import annotations

from datetime import datetime, timedelta

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.run_log import summarize
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

NOW = datetime(2026, 10, 25, 15, 0, tzinfo=dt_util.UTC)
ROUTINES = [
    {"id": "lights", "name": "Lights off", "enabled": True},
    {"id": "lock", "name": "Lock up", "enabled": True},
    {"id": "idle", "name": "Idle routine", "enabled": True},
]


def _event(routine_id, outcome, at, reason="", name=None):
    return {
        "at": at.isoformat(),
        "routine_id": routine_id,
        "name": name or routine_id,
        "outcome": outcome,
        "reason": reason,
        "detail": "",
    }


def test_summary_counts_runs_failures_and_skips_for_the_window() -> None:
    events = [
        _event("lights", "ran", NOW - timedelta(hours=2)),
        _event("lights", "failed", NOW - timedelta(hours=1), "error:light.turn_on"),
        _event("lock", "skipped", NOW - timedelta(minutes=30), "Front door is open"),
        _event("lock", "skipped", NOW - timedelta(minutes=20), "Front door is open"),
        _event("lights", "ran", NOW - timedelta(days=10)),  # outside the 7-day window
    ]
    result = summarize(events, "7d", NOW, ROUTINES)
    assert result["totals"] == {
        "triggered": 4,
        "ran": 1,
        "failed": 1,
        "skipped": 2,
        "success_rate": 50,
    }
    assert result["failure_reasons"] == [{"reason": "error:light.turn_on", "count": 1}]
    assert result["skip_reasons"] == [{"reason": "Front door is open", "count": 2}]


def test_today_starts_at_local_midnight() -> None:
    events = [
        _event("lights", "ran", NOW - timedelta(hours=3)),
        _event("lights", "ran", NOW - timedelta(days=1)),
    ]
    assert summarize(events, "today", NOW, ROUTINES)["totals"]["ran"] == 1


def test_success_rate_is_empty_when_nothing_was_attempted() -> None:
    result = summarize([_event("lock", "skipped", NOW, "Paused")], "7d", NOW, ROUTINES)
    assert result["totals"]["success_rate"] is None


def test_per_routine_rows_include_the_last_failure_and_failure_streak() -> None:
    events = [
        _event("lights", "ran", NOW - timedelta(days=3)),
        _event("lights", "failed", NOW - timedelta(days=2), "error:light.turn_on"),
        _event("lights", "failed", NOW - timedelta(days=1), "error:light.turn_on"),
        _event(
            "lights",
            "failed",
            NOW - timedelta(hours=1),
            "missing_service:light.turn_on",
        ),
    ]
    row = next(
        r
        for r in summarize(events, "30d", NOW, ROUTINES)["by_routine"]
        if r["id"] == "lights"
    )
    assert row["failed"] == 3 and row["ran"] == 1
    assert row["last_failure"]["reason"] == "missing_service:light.turn_on"
    assert row["failing_in_a_row"] == 3


def test_repeated_failures_and_quiet_routines_are_called_out() -> None:
    events = [
        _event("lights", "failed", NOW - timedelta(hours=i), "error:x")
        for i in range(3)
    ]
    result = summarize(events, "7d", NOW, ROUTINES)
    assert result["repeated_failures"] == [{"name": "Lights off", "count": 3}]
    assert result["quiet_routines"] == ["Lock up", "Idle routine"]


def test_by_day_groups_by_local_date_and_recent_is_newest_first() -> None:
    events = [
        _event("lights", "ran", NOW - timedelta(days=2)),
        _event(
            "lock", "skipped", NOW - timedelta(days=2) + timedelta(minutes=5), "Paused"
        ),
        _event("lights", "failed", NOW - timedelta(hours=1), "error:x"),
    ]
    result = summarize(events, "7d", NOW, ROUTINES)
    assert [d["day"] for d in result["by_day"]] == sorted(
        d["day"] for d in result["by_day"]
    )
    assert sum(d["ran"] + d["failed"] + d["skipped"] for d in result["by_day"]) == 3
    assert [r["outcome"] for r in result["recent"]] == ["failed", "skipped", "ran"]


async def test_runs_and_skips_are_recorded_and_served_to_the_tab(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, hass_ws_client
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN]["coordinator"]
    await coordinator.async_save_routine(
        {
            "id": "hall",
            "name": "Hall lights off",
            "trigger": "manual",
            "actions": [
                {"service": "light.turn_off", "target": {"entity_id": ["light.hall"]}}
            ],
        }
    )
    await hass.async_block_till_done()
    # No light.turn_off service is registered, so the run fails.
    await coordinator.async_run("hall")
    await hass.async_block_till_done()
    coordinator.run_log.record("hall", "Hall lights off", "skipped", reason="Paused")

    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/activity", "window": "today"})
    response = await client.receive_json()
    assert response["success"], response
    data = response["result"]
    assert data["window"] == "today"
    assert data["totals"]["failed"] == 1
    assert data["totals"]["skipped"] == 1
    assert data["failure_reasons"][0]["reason"].startswith("missing_service:")
    assert data["skip_reasons"] == [{"reason": "Paused", "count": 1}]
    assert data["by_routine"][0]["name"] == "Hall lights off"
    assert data["health"] == [] or isinstance(data["health"], list)


async def test_deleting_a_routine_removes_its_events(
    hass: HomeAssistant, enable_custom_integrations, hass_storage
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN]["coordinator"]
    coordinator.run_log.record("gone", "Gone routine", "ran")
    coordinator.run_log.record("kept", "Kept routine", "ran")
    coordinator.run_log.forget_routine("gone")
    assert [e["routine_id"] for e in coordinator.run_log.events()] == ["kept"]
