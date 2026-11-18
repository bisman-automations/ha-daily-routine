"""1.34.0: a failed step says which device, which integration and why."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.daily_routine.coordinator import (
    HomeRoutinesCoordinator,
    result_words,
)
from custom_components.daily_routine.linked import linked_groups
from custom_components.daily_routine.routines import normalize_routine
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.util import dt as dt_util

from .conftest import make_routine
from .test_linked_devices import (
    BLE_PAIR,
    LEFT_MAC,
    ORDER,
    PAIR,
    PAIR_NAMES,
    RIGHT_MAC,
    _closes,
    _coord,
    _device,
    _pair_also,
    _pair_curtains,
    _stop,  # noqa: F401 - the fixture that keeps runs quick
)


def _shape(hass: HomeAssistant, keep: list[str]) -> None:
    """The Matter pair plus only some of the four SwitchBot covers."""
    ble = MockConfigEntry(domain="switchbot")
    cloud = MockConfigEntry(domain="switchbot_cloud")
    _device(
        hass,
        "matter",
        [PAIR["matter"]],
        connections={
            (dr.CONNECTION_NETWORK_MAC, LEFT_MAC),
            (dr.CONNECTION_NETWORK_MAC, RIGHT_MAC),
        },
        name=PAIR_NAMES[PAIR["matter"]],
        unique="matter-pair",
    )
    ways = {
        "left_ble": ("switchbot", LEFT_MAC, ble),
        "right_ble": ("switchbot", RIGHT_MAC, ble),
        "left_cloud": ("switchbot_cloud", LEFT_MAC, cloud),
        "right_cloud": ("switchbot_cloud", RIGHT_MAC, cloud),
    }
    for key in keep:
        domain, mac, entry = ways[key]
        _device(
            hass, domain, [PAIR[key]], mac=mac, entry=entry, name=PAIR_NAMES[PAIR[key]]
        )
    for key in ["matter", *keep]:
        hass.states.async_set(
            PAIR[key], "open", {"friendly_name": PAIR_NAMES[PAIR[key]]}
        )


# ----- rich reasons -----


async def test_linked_failure_names_every_way_tried(hass: HomeAssistant):
    """Matter answered but didn't move it; the right curtain's Bluetooth and
    Cloud couldn't be reached. The reason says all of that, not 'didn't work'."""
    _pair_curtains(hass)
    _closes(
        hass,
        {
            PAIR["matter"]: "ignore",
            PAIR["right_ble"]: "raise",
            PAIR["right_cloud"]: "raise",
        },
        names=PAIR_NAMES,
        also=_pair_also(),
    )
    coord = await _coord(hass, entities=list(PAIR.values()))
    assert await coord.async_run("routine_one") is False
    entry = coord.get_history("routine_one")[0]
    detail = entry["detail"]
    assert detail.startswith(
        "Boy's Bedroom Right Curtain (Cloud) (Switchbot Cloud) couldn't be reached: "
        "Failed to connect"
    )
    assert "(tried Matter and Switchbot first)" in detail
    assert "close_cover" not in detail
    why = entry["steps"][0]["why"]
    assert why[0] == (
        "Tried first: Boy's Bedroom Curtains (Matter) stayed open after 1 s "
        "(wanted closed)"
    )
    assert any("Right Curtain (BLE) (Switchbot) couldn't be reached" in w for w in why)
    status = coord.get_status("routine_one")
    assert status["last_result"] == "error:cover.close_cover"
    assert status["last_reason"] == detail


async def test_unavailable_device_says_so(hass: HomeAssistant):
    hass.states.async_set("light.hall", "unavailable", {"friendly_name": "Hall light"})

    async def turn_off(call: ServiceCall) -> None:
        raise HomeAssistantError("Entity is not available")

    hass.services.async_register("light", "turn_off", turn_off)
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [make_routine(retry_later_minutes=0)]
    assert await coord.async_run("routine_one") is False
    assert coord.get_history("routine_one")[0]["detail"] == (
        "Hall light is unavailable (Home Assistant said: Entity is not available)"
    )


async def test_other_errors_keep_home_assistants_words(hass: HomeAssistant):
    hass.states.async_set("light.hall", "on", {"friendly_name": "Hall light"})

    async def turn_off(call: ServiceCall) -> None:
        raise HomeAssistantError("Brightness out of range")

    hass.services.async_register("light", "turn_off", turn_off)
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [make_routine(retry_later_minutes=0)]
    assert await coord.async_run("routine_one") is False
    assert coord.get_history("routine_one")[0]["detail"] == (
        "Hall light failed: Brightness out of range"
    )


async def test_a_step_with_no_devices_says_so(hass: HomeAssistant):
    """The panel could save "Specific devices" with nothing ticked: no target.
    HA refuses that call; the run now says why instead of 'cover.open_cover
    didn't work'."""
    hass.services.async_register(
        "cover",
        "open_cover",
        lambda call: None,
        schema=cv.make_entity_service_schema({}),
    )
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [
        make_routine(
            actions=[{"service": "cover.open_cover", "data": {}}],
            retry_later_minutes=0,
        )
    ]
    notes = coord.explain("routine_one")["notes"]
    assert notes == [
        "Step 1 (“Open”) has no devices or room picked, so it can't do anything."
    ]
    assert await coord.async_run("routine_one") is False
    detail = coord.get_history("routine_one")[0]["detail"]
    assert detail == (
        "“Open” has no devices to send it to: pick the devices or a room in the step"
    )
    assert "didn't work" not in detail
    assert "cover.open_cover" not in detail
    assert coord.explain("routine_one")["last_failure"]["reason"] == detail


async def test_result_words_never_show_the_raw_service():
    assert result_words("error:cover.open_cover") == "A step failed (open cover)"
    assert result_words("success") == ""
    assert "cover.open_cover" not in result_words("error:cover.open_cover")


# ----- the linked-device fix -----


async def test_lone_cloud_curtain_is_not_a_pair(hass: HomeAssistant):
    """Both SwitchBot Bluetooth curtains, but only the right one in the Cloud.
    The right Cloud curtain was taken for a pair entity covering both sides."""
    _shape(hass, ["left_ble", "right_ble", "right_cloud"])
    (group,) = linked_groups(hass, BLE_PAIR, ORDER)
    assert [(lvl.domain, lvl.covering) for lvl in group.levels] == [
        ("matter", True),
        ("switchbot", False),
        ("switchbot_cloud", False),
    ]
    assert (PAIR["right_ble"], PAIR["right_cloud"]) in group.members


async def test_lone_cloud_curtain_is_still_the_fallback(hass: HomeAssistant):
    """Matter fails, the right Bluetooth curtain fails: the right Cloud curtain
    is tried (as a pair it was skipped, because the left had already moved)."""
    _shape(hass, ["left_ble", "right_ble", "right_cloud"])
    calls = _closes(
        hass,
        {PAIR["matter"]: "raise", PAIR["right_ble"]: "raise"},
        names=PAIR_NAMES,
        also=_pair_also(),
    )
    coord = await _coord(hass, entities=BLE_PAIR)
    assert await coord.async_run("routine_one") is True
    assert calls[0] == PAIR["matter"]
    assert calls[-1] == PAIR["right_cloud"]


async def test_lone_bluetooth_curtain_does_not_finish_both_sides(hass: HomeAssistant):
    """Only the right curtain over Bluetooth, both in the Cloud. When Matter
    fails, closing the right curtain over Bluetooth used to count as closing
    both (it was taken for the pair), so the left one stayed open."""
    _shape(hass, ["right_ble", "left_cloud", "right_cloud"])
    (group,) = linked_groups(
        hass, [PAIR["right_ble"], PAIR["left_cloud"], PAIR["right_cloud"]], ORDER
    )
    assert [lvl.covering for lvl in group.levels] == [True, False, False]
    _closes(hass, {PAIR["matter"]: "raise"}, names=PAIR_NAMES, also=_pair_also())
    coord = await _coord(
        hass, entities=[PAIR["right_ble"], PAIR["left_cloud"], PAIR["right_cloud"]]
    )
    assert await coord.async_run("routine_one") is True
    assert hass.states.get(PAIR["left_cloud"]).state == "closed"
    assert hass.states.get(PAIR["right_ble"]).state == "closed"


async def test_why_not_shows_the_order_devices_are_tried(hass: HomeAssistant):
    _pair_curtains(hass)
    coord = await _coord(hass, entities=list(PAIR.values()))
    plan = coord.explain("routine_one")["plan"]
    assert plan[0]["step"] == 1
    assert plan[0]["ways"] == [
        "Boy's Bedroom Curtains (Matter) → then "
        "Boy's Bedroom Left Curtain (BLE) and Boy's Bedroom Right Curtain (BLE) "
        "(Switchbot) → then "
        "Boy's Bedroom Left Curtain (Cloud) and Boy's Bedroom Right Curtain (Cloud) "
        "(Switchbot Cloud)"
    ]


# ----- an empty sensor condition -----


async def test_empty_sensor_condition_cannot_be_saved():
    with pytest.raises(vol.Invalid, match="Condition 1 \\(Sensors\\) has no sensors"):
        normalize_routine(
            {
                "id": "x",
                "name": "x",
                "trigger": "time",
                "time": "07:00:00",
                "conditions": [{"kind": "sensors", "sensors": [], "mode": "quiet"}],
                "actions": [{"service": "light.turn_off", "data": {}}],
            }
        )


async def test_empty_sensor_condition_is_ignored_and_noted(hass: HomeAssistant):
    """One that gets in anyway (not through the panel) is ignored, not a crash
    (max() of no sensors), and Why not? says so."""
    coord = HomeRoutinesCoordinator(hass)
    routine = make_routine()
    empty = {"kind": "sensors", "sensors": [], "mode": "quiet", "minutes": 5}
    routine["conditions"] = [empty]
    coord.routines = [routine]
    now = dt_util.now() + timedelta(seconds=1)
    assert coord._sensors_ok(empty, now) is True  # noqa: SLF001
    assert coord._sensors_ok({**empty, "mode": "active"}, now) is True  # noqa: SLF001
    assert coord.explain("routine_one")["notes"] == [
        "Only if, condition 1: no sensors are picked, so it is ignored. "
        "Pick the sensors, or remove it."
    ]
