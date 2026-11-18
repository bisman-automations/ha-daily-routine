"""1.34.0: the routine's Linked device is tried first in its room.

Donavan's Master Bedroom: the routine is linked to "Master Bedroom Curtains
(Matter)", and its step opens the curtains of the room. The Matter pair is
tried first, on its own; only if it doesn't do it are the Bluetooth curtains
(left and right) tried, then the Cloud ones.
"""

from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daily_routine.coordinator import HomeRoutinesCoordinator
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

from .conftest import make_routine
from .test_linked_devices import (
    ORDER,
    PAIR,
    PAIR_NAMES,
    _closes,
    _pair_also,
    _pair_curtains,
    _stop,  # noqa: F401 - the fixture that keeps runs quick
)

BLE = [PAIR["left_ble"], PAIR["right_ble"]]
CLOUD = [PAIR["left_cloud"], PAIR["right_cloud"]]
PLATFORM = {
    PAIR["matter"]: "matter",
    PAIR["left_ble"]: "switchbot",
    PAIR["right_ble"]: "switchbot",
    PAIR["left_cloud"]: "switchbot_cloud",
    PAIR["right_cloud"]: "switchbot_cloud",
}


def _room(hass: HomeAssistant, *, blind: bool = False) -> tuple[str, str]:
    """Five curtains in one room, not linked by Device Linker (each its own
    device, no shared address). Returns (area id, the Matter device id)."""
    area = ar.async_get(hass).async_create("Master Bedroom")
    matter_device = ""
    entries: dict[str, MockConfigEntry] = {}
    every = dict(PLATFORM)
    if blind:
        every["cover.bedroom_blind"] = "somfy"
    for index, (entity_id, platform) in enumerate(every.items()):
        entry = entries.get(platform)
        if entry is None:
            entry = entries[platform] = MockConfigEntry(domain=platform)
            entry.add_to_hass(hass)
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(platform, f"dev-{index}")},
            name=PAIR_NAMES.get(entity_id, "Bedroom blind"),
        )
        dr.async_get(hass).async_update_device(device.id, area_id=area.id)
        er.async_get(hass).async_get_or_create(
            "cover",
            platform,
            f"u-{index}",
            suggested_object_id=entity_id.split(".", 1)[1],
            device_id=device.id,
            config_entry=entry,
        )
        hass.states.async_set(
            entity_id,
            "open",
            {
                "friendly_name": PAIR_NAMES.get(entity_id, "Bedroom blind"),
                "device_class": "blind" if "blind" in entity_id else "curtain",
            },
        )
        if platform == "matter":
            matter_device = device.id
    return area.id, matter_device


async def _routine(
    hass: HomeAssistant, target: dict[str, Any], device_id: str | None, order=ORDER
) -> HomeRoutinesCoordinator:
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [
        make_routine(
            actions=[{"service": "cover.close_cover", "target": target, "data": {}}],
            retry_later_minutes=0,
            device_id=device_id,
        )
    ]
    await coord.async_set_settings(
        {**coord.settings, "integration_order": order, "verify_seconds": 1}
    )
    return coord


def _calls_by_call(hass: HomeAssistant, behaviour: dict[str, str]) -> list[list[str]]:
    """Like _closes, but keeps each call's entities together (to see what was
    sent at the same time)."""
    flat = _closes(hass, behaviour, names=PAIR_NAMES, also=_pair_also())
    handler = hass.services._services["cover"]["close_cover"].job.target  # noqa: SLF001
    grouped: list[list[str]] = []

    async def close(call):
        grouped.append(list(call.data["entity_id"]))
        await handler(call)

    hass.services.async_register("cover", "close_cover", close)
    del flat
    return grouped


async def test_room_step_with_linked_matter_uses_only_matter(hass: HomeAssistant):
    area, matter = _room(hass)
    calls = _calls_by_call(hass, {})
    coord = await _routine(hass, {"area_id": [area]}, matter)
    assert await coord.async_run("routine_one") is True
    assert calls == [[PAIR["matter"]]]


async def test_matter_fails_then_bluetooth_pair_then_cloud(hass: HomeAssistant):
    area, matter = _room(hass)
    calls = _calls_by_call(hass, {PAIR["matter"]: "raise"})
    coord = await _routine(hass, {"area_id": [area]}, matter)
    assert await coord.async_run("routine_one") is True
    # Matter alone, then left and right over Bluetooth together. Cloud is
    # never needed.
    assert calls == [[PAIR["matter"]], BLE]


async def test_bluetooth_fails_too_then_cloud(hass: HomeAssistant):
    area, matter = _room(hass)
    behaviour = {PAIR["matter"]: "raise", **dict.fromkeys(BLE, "raise")}
    calls = _calls_by_call(hass, behaviour)
    coord = await _routine(hass, {"area_id": [area]}, matter)
    assert await coord.async_run("routine_one") is True
    assert calls[0] == [PAIR["matter"]]
    assert calls[-1] == CLOUD
    first_cloud = calls.index(CLOUD)
    assert all(set(c) <= set(BLE) for c in calls[1:first_cloud])
    assert not any(PAIR["matter"] in c and len(c) > 1 for c in calls)


async def test_every_way_failing_says_linked_device_first(hass: HomeAssistant):
    area, matter = _room(hass)
    _calls_by_call(hass, dict.fromkeys(PLATFORM, "raise"))
    coord = await _routine(hass, {"area_id": [area]}, matter)
    assert await coord.async_run("routine_one") is False
    run = coord.get_history("routine_one")[0]
    assert "(tried the linked device, Matter and Switchbot first)" in run["detail"]
    why = run["steps"][0]["why"]
    assert why[0].startswith(
        "Tried first (linked device): Boy's Bedroom Curtains (Matter)"
    )


async def test_linked_device_outside_the_step_changes_nothing(hass: HomeAssistant):
    """A step for only the Bluetooth curtains (not the room): the Matter
    device isn't part of it, so they are sent as before, together."""
    _area, matter = _room(hass)
    calls = _calls_by_call(hass, {})
    coord = await _routine(hass, {"entity_id": BLE}, matter)
    assert await coord.async_run("routine_one") is True
    assert calls == [BLE]


async def test_a_room_step_without_a_linked_device_is_unchanged(hass: HomeAssistant):
    area, _matter = _room(hass)
    calls = _calls_by_call(hass, {})
    coord = await _routine(hass, {"area_id": [area]}, None)
    assert await coord.async_run("routine_one") is True
    # Every curtain gets it as before (the test service tries a room target
    # device by device); none waits for another.
    assert sorted(e for c in calls for e in c) == sorted(PLATFORM)


async def test_other_kinds_in_the_room_are_not_fallbacks(hass: HomeAssistant):
    """The room's blind isn't a fallback for the curtains: it gets the
    command as usual, and the curtains still go Matter first."""
    area, matter = _room(hass, blind=True)
    calls = _calls_by_call(hass, {})
    coord = await _routine(hass, {"area_id": [area]}, matter)
    assert await coord.async_run("routine_one") is True
    assert sorted(calls) == sorted([["cover.bedroom_blind"], [PAIR["matter"]]])


async def test_linked_device_goes_ahead_of_the_integration_order(hass: HomeAssistant):
    """With Device Linker groups and an order that puts Bluetooth first, the
    routine's linked Matter pair is still tried first."""
    _pair_curtains(hass)
    matter_device = er.async_get(hass).async_get(PAIR["matter"]).device_id
    calls = _calls_by_call(hass, {})
    coord = await _routine(
        hass,
        {"entity_id": list(PAIR.values())},
        matter_device,
        order=["switchbot", "switchbot_cloud", "matter"],
    )
    assert await coord.async_run("routine_one") is True
    assert calls == [[PAIR["matter"]]]
    plan = coord.explain("routine_one")["plan"][0]["ways"]
    assert plan[0].startswith("Linked device first: Boy's Bedroom Curtains (Matter)")


async def test_why_not_shows_linked_device_first_for_the_room(hass: HomeAssistant):
    area, matter = _room(hass)
    coord = await _routine(hass, {"area_id": [area]}, matter)
    ways = coord.explain("routine_one")["plan"][0]["ways"]
    assert ways == [
        "Linked device first: Boy's Bedroom Curtains (Matter) → then "
        "Boy's Bedroom Left Curtain (BLE) and Boy's Bedroom Right Curtain (BLE) "
        "(Switchbot) → then "
        "Boy's Bedroom Left Curtain (Cloud) and Boy's Bedroom Right Curtain (Cloud) "
        "(Switchbot Cloud)"
    ]


async def test_matter_answers_but_nothing_moves_then_bluetooth(hass: HomeAssistant):
    """The Matter call is accepted but the curtains don't close within the
    check time: Bluetooth is tried next."""
    area, matter = _room(hass)
    calls = _calls_by_call(hass, {PAIR["matter"]: "ignore"})
    coord = await _routine(hass, {"area_id": [area]}, matter)
    assert await coord.async_run("routine_one") is True
    assert calls == [[PAIR["matter"]], BLE]
