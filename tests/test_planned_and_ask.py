"""1.24.0: before an alarm or a calendar event, the forecast, the cheapest hour,
Ask me first, steps at the same time, copying to rooms, and device trouble."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
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
from custom_components.daily_routine.planned import (
    alarm_time,
    cheapest_start,
    event_starts,
    forecast_matches,
    price_slots,
)
from custom_components.daily_routine.reliability import DeviceStats
from custom_components.daily_routine.runtime import get_coordinator
from custom_components.daily_routine.schema import vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.helpers import area_registry as ar
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


def _cst(local: str) -> datetime:
    """A fixed time for the pure helpers, which don't use Home Assistant's zone."""
    return datetime.fromisoformat(f"{local}-06:00")


async def _started(hass: HomeAssistant, *routines: dict[str, Any]):
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = list(routines)
    coord._start_all(catch_up=False)  # noqa: SLF001
    await hass.async_block_till_done()
    return coord


def _light(service: str = "light.turn_off", entity: str = "light.hall"):
    return {"service": service, "target": {"entity_id": [entity]}, "data": {}}


# ----- pure helpers -----


def test_alarm_time_reads_timestamps_and_helpers(hass: HomeAssistant):
    hass.states.async_set("sensor.phone_next_alarm", "2026-11-16T12:30:00+00:00")
    assert alarm_time(
        hass.states.get("sensor.phone_next_alarm")
    ) == datetime.fromisoformat("2026-11-16T12:30:00+00:00")
    hass.states.async_set("sensor.none", "unavailable")
    assert alarm_time(hass.states.get("sensor.none")) is None
    assert alarm_time(None) is None
    hass.states.async_set("input_datetime.wake", "nope", {"timestamp": 0})
    assert alarm_time(hass.states.get("input_datetime.wake")).year == 1970
    hass.states.async_set("input_datetime.local", "2026-11-16 06:00:00")
    assert alarm_time(hass.states.get("input_datetime.local")).tzinfo is not None


def test_event_starts_match_titles_and_all_day():
    events = [
        {"summary": "School", "start": "2026-11-16T08:00:00-06:00"},
        {"summary": "Soccer", "start": "2026-11-16T17:00:00-06:00"},
        {"summary": "School holiday", "start": "2026-11-17"},
        {"summary": "Broken", "start": "later"},
        {"summary": "School", "start": "2026-11-18T08:00:00"},
    ]
    starts = event_starts(events, "school")
    assert len(starts) == 3
    assert len(event_starts(events, "")) == 4


def test_prices_from_several_integrations_and_the_cheapest():
    now = _cst("2026-11-14 12:00:00")
    nord = {
        "raw_today": [
            {"start": "2026-11-14T00:00:00-06:00", "end": "...", "value": 0.30},
            {"start": "2026-11-14T02:00:00-06:00", "value": 0.10},
            {"start": "2026-11-14T03:00:00-06:00", "value": "bad"},
        ]
    }
    slots = price_slots(nord, now)
    assert len(slots) == 2
    begin, end = _cst("2026-11-14 00:00:00"), _cst("2026-11-14 06:00:00")
    assert cheapest_start(slots, begin, end) == datetime.fromisoformat(
        "2026-11-14T02:00:00-06:00"
    )
    plain = {"today": [0.5] * 24, "tomorrow": [0.4] * 23 + [0.1]}
    slots = price_slots(plain, now)
    assert len(slots) == 48
    # Plain lists count from local midnight (UTC here: no Home Assistant zone).
    assert cheapest_start(
        slots, _at("2026-11-15 20:00:00"), _at("2026-11-16 00:00:00")
    ) == _at("2026-11-15 23:00:00")
    assert price_slots({"today": [1, 2, 3]}, now) == []
    assert cheapest_start([], begin, end) is None
    tibber = {"prices": [{"startsAt": "2026-11-14T05:00:00", "total": 0.2}, "x"]}
    assert len(price_slots(tibber, now)) == 1


def test_forecast_matches_kinds_and_cold():
    now = _cst("2026-11-14 12:00:00")
    forecast = [
        {
            "datetime": "2026-11-14T13:00:00-06:00",
            "condition": "cloudy",
            "temperature": 5,
        },
        {
            "datetime": "2026-11-14T15:00:00-06:00",
            "condition": "rainy",
            "temperature": 2,
        },
        {"datetime": "2026-11-14T20:00:00-06:00", "condition": "snowy", "templow": -3},
        {"datetime": "nope"},
        {"datetime": "2026-11-14T09:00:00", "condition": "rainy"},
    ]
    assert forecast_matches(forecast, now, 3, ["rainy"], None)
    assert not forecast_matches(forecast, now, 2, ["rainy"], None)
    assert forecast_matches(forecast, now, 12, [], 0)
    assert not forecast_matches(forecast, now, 4, [], 0)
    assert not forecast_matches(
        [{"datetime": "2026-11-14T13:00:00-06:00", "templow": "x"}], now, 3, [], 0
    )


# ----- validation -----


def test_new_triggers_are_checked():
    assert (
        make_routine(
            trigger="alarm", watch_entity="sensor.phone_next_alarm", offset_minutes=-15
        )["watch_entity"]
        == "sensor.phone_next_alarm"
    )
    with pytest.raises(vol.Invalid, match="alarm"):
        make_routine(trigger="alarm", watch_entity="light.x")
    with pytest.raises(vol.Invalid, match="calendar"):
        make_routine(trigger="calendar_event", watch_entity="sensor.x")
    cal = make_routine(
        trigger="calendar_event", watch_entity="calendar.family", watch_item="School"
    )
    assert cal["watch_item"] == "School"
    with pytest.raises(vol.Invalid, match="forecast"):
        make_routine(trigger="forecast", watch_entity="sensor.x")
    with pytest.raises(vol.Invalid, match="kinds of weather"):
        make_routine(trigger="forecast", watch_entity="weather.home")
    with pytest.raises(vol.Invalid, match="from the list"):
        make_routine(
            trigger="forecast", watch_entity="weather.home", watch_states=["lava"]
        )
    rain = make_routine(
        trigger="forecast",
        watch_entity="weather.home",
        watch_states=["rainy"],
        forecast_hours=6,
    )
    assert rain["forecast_hours"] == 6 and rain["repeat"] is not None
    cold = make_routine(
        trigger="forecast", watch_entity="weather.home", value_threshold="0"
    )
    assert cold["value_threshold"] == 0
    with pytest.raises(vol.Invalid, match="price"):
        make_routine(trigger="cheapest", value_entity="light.x")
    with pytest.raises(vol.Invalid, match="hours"):
        make_routine(trigger="cheapest", value_entity="sensor.price")
    assert (
        make_routine(
            trigger="cheapest",
            value_entity="sensor.price",
            active_from="00:00",
            active_to="06:00",
        )["value_entity"]
        == "sensor.price"
    )
    with pytest.raises(vol.Invalid, match="runs or skips"):
        make_routine(ask_first=True, ask_default="maybe")
    asked = make_routine(ask_first=True, ask_minutes=5, ask_default="skip")
    assert (asked["ask_first"], asked["ask_minutes"], asked["ask_default"]) == (
        True,
        5,
        "skip",
    )


def test_together_steps_are_checked():
    routine = make_routine(
        actions=[{"together": [_light(), _light("light.turn_on", "light.den")]}]
    )
    assert len(routine["actions"][0]["together"]) == 2
    with pytest.raises(vol.Invalid, match="2 to"):
        make_routine(actions=[{"together": [_light()]}])
    with pytest.raises(vol.Invalid, match="same time"):
        make_routine(actions=[{"together": [_light(), {"delay": 3}]}])
    with pytest.raises(vol.Invalid, match="itself"):
        make_routine(id="x", actions=[{"together": [_light(), {"run_routine": "x"}]}])


# ----- planning -----


async def test_alarm_trigger_plans_before_the_alarm(hass: HomeAssistant, freezer):
    freezer.move_to(_at("2026-11-14 20:00:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.hall", "on")
    hass.states.async_set(
        "sensor.phone_next_alarm", _at("2026-11-15 06:30:00").isoformat()
    )
    routine = make_routine(
        trigger="alarm",
        watch_entity="sensor.phone_next_alarm",
        offset_minutes=-15,
        skip_noop=False,
    )
    coord = await _started(hass, routine)
    assert (
        coord.get_status("routine_one")["next_run"]
        == _at("2026-11-15 06:15:00").isoformat()
    )
    # The alarm moves: so does the plan.
    hass.states.async_set(
        "sensor.phone_next_alarm", _at("2026-11-15 07:00:00").isoformat()
    )
    await hass.async_block_till_done()
    assert (
        coord.get_status("routine_one")["next_run"]
        == _at("2026-11-15 06:45:00").isoformat()
    )
    freezer.move_to(_at("2026-11-15 06:45:01"))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls) == 1
    hass.states.async_set("sensor.phone_next_alarm", "unknown")
    await hass.async_block_till_done()
    assert coord.get_status("routine_one")["next_run"] is None


async def test_calendar_event_trigger(hass: HomeAssistant, freezer):
    freezer.move_to(_at("2026-11-14 06:00:00"))

    async def get_events(call: ServiceCall) -> ServiceResponse:
        return {
            call.data["entity_id"][0]
            if isinstance(call.data["entity_id"], list)
            else call.data["entity_id"]: {
                "events": [
                    {
                        "summary": "Dentist",
                        "start": _at("2026-11-14 09:00:00").isoformat(),
                    },
                    {
                        "summary": "School",
                        "start": _at("2026-11-14 08:00:00").isoformat(),
                    },
                ]
            }
        }

    hass.services.async_register(
        "calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY
    )
    hass.states.async_set("calendar.family", "off")
    routine = make_routine(
        trigger="calendar_event",
        watch_entity="calendar.family",
        watch_item="school",
        offset_minutes=-30,
    )
    coord = await _started(hass, routine)
    await coord._refresh_upcoming()  # noqa: SLF001
    assert (
        coord.get_status("routine_one")["next_run"]
        == _at("2026-11-14 07:30:00").isoformat()
    )
    hass.states.async_remove("calendar.family")
    assert (
        await coord._read_events("calendar.family", dt_util.now(), dt_util.now())
        is None
    )  # noqa: SLF001


async def test_cheapest_hour(hass: HomeAssistant, freezer):
    freezer.move_to(_at("2026-11-14 12:00:00"))
    prices = [0.3] * 24
    prices[2] = 0.05
    hass.states.async_set(
        "sensor.price",
        "0.3",
        {"today": prices, "tomorrow": [0.3] * 3 + [0.01] + [0.3] * 20},
    )
    routine = make_routine(
        trigger="cheapest",
        value_entity="sensor.price",
        active_from="00:00",
        active_to="06:00",
    )
    coord = await _started(hass, routine)
    # Today's window is over: tomorrow's cheapest hour, 03:00.
    assert (
        coord.get_status("routine_one")["next_run"]
        == _at("2026-11-15 03:00:00").isoformat()
    )
    # Prices not known: the start of the hours.
    coord._rt("routine_one")["cheapest"] = None  # noqa: SLF001
    hass.states.async_set("sensor.price", "0.3", {})
    await hass.async_block_till_done()
    assert (
        coord.get_status("routine_one")["next_run"]
        == _at("2026-11-15 00:00:00").isoformat()
    )


async def test_forecast_trigger_runs_when_rain_shows_up(hass: HomeAssistant, freezer):
    freezer.move_to(_at("2026-11-14 12:00:00"))
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.hall", "on")
    hass.states.async_set("weather.home", "sunny")
    forecast: list[dict[str, Any]] = [
        {"datetime": _at("2026-11-14 13:00:00").isoformat(), "condition": "sunny"}
    ]

    async def get_forecasts(call: ServiceCall) -> ServiceResponse:
        if call.data["type"] != "hourly":
            raise ValueError("no")
        return {"weather.home": {"forecast": forecast}}

    hass.services.async_register(
        "weather",
        "get_forecasts",
        get_forecasts,
        supports_response=SupportsResponse.ONLY,
    )
    routine = make_routine(
        trigger="forecast",
        watch_entity="weather.home",
        watch_states=["rainy"],
        skip_noop=False,
    )
    coord = await _started(hass, routine)
    await coord._check_forecasts()  # noqa: SLF001 - sets what it was
    assert not calls
    forecast.append(
        {"datetime": _at("2026-11-14 14:00:00").isoformat(), "condition": "rainy"}
    )
    await coord._check_forecasts()  # noqa: SLF001
    await hass.async_block_till_done()
    assert len(calls) == 1
    await coord._check_forecasts()  # noqa: SLF001 - still raining: not again
    await hass.async_block_till_done()
    assert len(calls) == 1
    hass.states.async_remove("weather.home")
    assert await coord._read_forecast("weather.home") is None  # noqa: SLF001


# ----- ask me first -----


async def test_ask_first_runs_skips_or_waits(hass: HomeAssistant, monkeypatch):
    monkeypatch.setattr(coordinator_module, "SECONDS_PER_MINUTE", 0.05)
    calls = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.hall", "on")
    routine = make_routine(ask_first=True, ask_minutes=1, repeat=True, skip_noop=False)
    coord = await _started(hass, routine)

    task = hass.async_create_task(
        coord._execute_routine(routine, dt_util.now(), scheduled=True)
    )  # noqa: SLF001
    await asyncio.sleep(0.01)
    assert coord.get_status("routine_one")["state"] == "asking"
    assert coord.get_status("routine_one")["ask_until"]
    assert coord.async_answer("routine_one", run=False)
    assert not await task
    assert coord.get_status("routine_one")["last_result"] == "asked_skip"
    assert not coord.async_answer("routine_one", run=True)  # nothing asking now

    task = hass.async_create_task(
        coord._execute_routine(routine, dt_util.now(), scheduled=True)
    )  # noqa: SLF001
    await asyncio.sleep(0.01)
    coord.async_answer("routine_one", run=True)
    assert await task
    assert len(calls) == 1

    # Nobody answers: it runs (the default here).
    assert await coord._execute_routine(routine, dt_util.now(), scheduled=True)  # noqa: SLF001
    assert len(calls) == 2
    skipping = make_routine(
        id="s", ask_first=True, ask_minutes=1, ask_default="skip", repeat=True
    )
    coord2 = await _started(hass, skipping)
    assert not await coord2._execute_routine(skipping, dt_util.now(), scheduled=True)  # noqa: SLF001
    assert coord2.get_status("s")["last_result"] == "no_answer_skip"
    # Run now doesn't ask.
    assert await coord.async_run("routine_one")


async def test_ask_from_the_phone(hass: HomeAssistant, hass_admin_user, monkeypatch):
    monkeypatch.setattr(coordinator_module, "SECONDS_PER_MINUTE", 1)
    async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.hall", "on")
    routine = make_routine(ask_first=True, ask_minutes=1, repeat=True, skip_noop=False)
    coord = await _started(hass, routine)
    hass.bus.async_listen(
        "mobile_app_notification_action", coord._on_notification_action
    )  # noqa: SLF001
    task = hass.async_create_task(
        coord._execute_routine(routine, dt_util.now(), scheduled=True)
    )  # noqa: SLF001
    await asyncio.sleep(0.01)
    from homeassistant.core import Context  # noqa: PLC0415

    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": "DAILY_ROUTINE_ASK_SKIP_routine_one"},
        context=Context(user_id=hass_admin_user.id),
    )
    assert not await task


# ----- together, copy, devices -----


async def test_steps_at_the_same_time(hass: HomeAssistant):
    calls = async_mock_service(hass, "light", "turn_off")
    on = async_mock_service(hass, "light", "turn_on")
    hass.states.async_set("light.hall", "on")
    hass.states.async_set("light.den", "off")
    routine = make_routine(
        trigger="manual",
        skip_noop=False,
        actions=[{"together": [_light(), _light("light.turn_on", "light.den")]}],
    )
    coord = await _started(hass, routine)
    assert await coord.async_run("routine_one")
    assert len(calls) == 1 and len(on) == 1
    assert coord.can_undo("routine_one")
    preview = await coord.preview(routine)
    assert preview["steps"][0]["label"].startswith("At the same time")


async def test_a_failed_step_in_together_stops_the_routine(hass: HomeAssistant):
    async def broken(call):
        raise RuntimeError("no answer")

    hass.services.async_register("light", "turn_off", broken)
    on = async_mock_service(hass, "light", "turn_on")
    hass.states.async_set("light.hall", "on")
    hass.states.async_set("light.den", "off")
    routine = make_routine(
        trigger="manual",
        skip_noop=False,
        retries=0,
        retry_later_minutes=0,
        actions=[
            {"together": [_light(), _light("light.turn_on", "light.den")]},
            _light("light.turn_on", "light.den"),
        ],
    )
    coord = await _started(hass, routine)
    assert not await coord.async_run("routine_one")
    assert (
        len(on) == 1
    )  # the other one at the same time still ran; the next step didn't
    assert coord.device_stats.summary(30)[0]["entity_id"] == "light.hall"


async def test_copy_to_rooms(
    hass: HomeAssistant, enable_custom_integrations, hass_ws_client
):
    areas = ar.async_get(hass)
    kitchen = areas.async_create("Kitchen")
    den = areas.async_create("Den")
    entry = MockConfigEntry(domain=DOMAIN, title="Daily Routine", unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coord = get_coordinator(hass)
    await coord.async_save_routine(
        {
            "id": "lights",
            "name": "Lights off",
            "room": kitchen.id,
            "trigger": "time",
            "actions": [
                {"service": "light.turn_off", "target": {"area_id": [kitchen.id]}},
                {
                    "together": [
                        {
                            "service": "light.turn_off",
                            "target": {"area_id": [kitchen.id]},
                        },
                        {
                            "service": "switch.turn_off",
                            "target": {"entity_id": ["switch.a"]},
                        },
                    ]
                },
            ],
        }
    )
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": f"{DOMAIN}/routine/copy",
            "routine_id": "lights",
            "rooms": [den.id, kitchen.id],
        }
    )
    result = (await client.receive_json())["result"]
    assert len(result["routines"]) == 1
    copy = result["routines"][0]
    assert copy["room"] == den.id and copy["name"] == "Lights off (Den)"
    assert copy["actions"][0]["target"]["area_id"] == [den.id]
    assert copy["actions"][1]["together"][0]["target"]["area_id"] == [den.id]
    assert copy["actions"][1]["together"][1]["target"] == {"entity_id": ["switch.a"]}
    # Again: a free id.
    await client.send_json(
        {
            "id": 2,
            "type": f"{DOMAIN}/routine/copy",
            "routine_id": "lights",
            "rooms": [den.id],
        }
    )
    again = (await client.receive_json())["result"]["routines"][0]
    assert again["id"] != copy["id"]
    await client.send_json(
        {
            "id": 3,
            "type": f"{DOMAIN}/routine/copy",
            "routine_id": "lights",
            "rooms": ["nowhere"],
        }
    )
    assert (await client.receive_json())["error"]["code"] == "invalid_routine"
    with pytest.raises(vol.Invalid):
        await coord.async_copy_to_rooms("nope", [den.id])

    await client.send_json(
        {
            "id": 4,
            "type": f"{DOMAIN}/routine/answer",
            "routine_id": "lights",
            "run": True,
        }
    )
    assert (await client.receive_json())["result"] == {"answered": False}
    await client.send_json({"id": 5, "type": f"{DOMAIN}/activity", "window": "7d"})
    assert (await client.receive_json())["result"]["devices"] == []
    hass.states.async_set(
        "sensor.phone_next_alarm",
        "unknown",
        {"device_class": "timestamp", "friendly_name": "Phone Next alarm"},
    )
    hass.states.async_set(
        "input_datetime.wake", "unknown", {"has_date": True, "has_time": True}
    )
    hass.states.async_set(
        "input_datetime.date_only", "unknown", {"has_date": True, "has_time": False}
    )
    hass.states.async_set("sensor.temp", "3", {"device_class": "temperature"})
    await client.send_json({"id": 6, "type": f"{DOMAIN}/overview"})
    overview = (await client.receive_json())["result"]
    assert [a["entity_id"] for a in overview["alarm_sensors"]] == [
        "sensor.phone_next_alarm",
        "input_datetime.wake",
    ]
    assert overview["settings"]["editor"] == "simple"
    await client.send_json(
        {
            "id": 7,
            "type": f"{DOMAIN}/settings/save",
            "settings": {**coord.settings, "editor": "full"},
        }
    )
    assert (await client.receive_json())["result"]["settings"]["editor"] == "full"
    await client.send_json(
        {
            "id": 8,
            "type": f"{DOMAIN}/settings/save",
            "settings": {**coord.settings, "editor": "odd"},
        }
    )
    assert not (await client.receive_json())["success"]


async def test_device_stats_count_and_forget(hass: HomeAssistant, freezer):
    freezer.move_to(_at("2026-11-14 12:00:00"))
    stats = DeviceStats(hass)
    await stats.async_load()
    hass.states.async_set("light.a", "on", {"friendly_name": "Lamp"})
    stats.record("light.a", "ok")
    stats.record("light.a", "retried", "needed the command again")
    stats.record("light.a", "failed", "didn't change")
    stats.record("light.b", "ok")
    stats.record("light.b", "odd")
    rows = stats.summary(7)
    assert len(rows) == 1
    assert (
        rows[0]["name"] == "Lamp"
        and rows[0]["failed"] == 1
        and rows[0]["success_rate"] == 67
    )
    stats.record("light.a", "ok", now=dt_util.now() + timedelta(days=40))
    assert stats.summary(7, dt_util.now() + timedelta(days=40)) == []
    assert "light.b" not in stats.days


async def test_verified_devices_are_counted(hass: HomeAssistant):
    on = async_mock_service(hass, "light", "turn_on")
    hass.states.async_set("light.a", "off")
    routine = make_routine(
        trigger="manual", skip_noop=False, actions=[_light("light.turn_on", "light.a")]
    )
    coord = await _started(hass, routine)
    coord.settings["verify_seconds"] = 0.05
    assert not await coord.async_run("routine_one")  # it never turned on
    assert len(on) >= 2  # sent again
    row = coord.device_stats.summary(1)[0]
    assert row["entity_id"] == "light.a" and row["failed"] == 1
