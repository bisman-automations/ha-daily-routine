"""1.21.0 / 1.32.0: a device in more than one integration is reached by level."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daily_routine import coordinator as coordinator_module
from custom_components.daily_routine.coordinator import HomeRoutinesCoordinator
from custom_components.daily_routine.linked import linked_groups
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import make_routine

MAC = "aa:bb:cc:dd:ee:01"
WAYS = {
    "matter": "cover.curtain_matter",
    "switchbot": "cover.curtain_ble",
    "switchbot_cloud": "cover.curtain_cloud",
}
ORDER = ["matter", "switchbot", "switchbot_cloud"]

LEFT_MAC = "aa:bb:cc:dd:ee:10"
RIGHT_MAC = "aa:bb:cc:dd:ee:11"

# Boy's Bedroom / Office pair: one Matter cover and four SwitchBot covers.
PAIR = {
    "matter": "cover.boys_curtains",
    "left_ble": "cover.boys_left_ble",
    "right_ble": "cover.boys_right_ble",
    "left_cloud": "cover.boys_left_cloud",
    "right_cloud": "cover.boys_right_cloud",
}
PAIR_NAMES = {
    PAIR["matter"]: "Boy's Bedroom Curtains",
    PAIR["left_ble"]: "Boy's Bedroom Left Curtain (BLE)",
    PAIR["right_ble"]: "Boy's Bedroom Right Curtain (BLE)",
    PAIR["left_cloud"]: "Boy's Bedroom Left Curtain (Cloud)",
    PAIR["right_cloud"]: "Boy's Bedroom Right Curtain (Cloud)",
}
BLE_PAIR = [PAIR["left_ble"], PAIR["right_ble"]]
CLOUD_PAIR = [PAIR["left_cloud"], PAIR["right_cloud"]]
SWITCHBOT_PAIR = BLE_PAIR + CLOUD_PAIR


@pytest.fixture(autouse=True)
async def _stop(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(coordinator_module, "VERIFY_POLL_SECONDS", 0.01)
    monkeypatch.setattr(coordinator_module, "RETRY_DELAY_SECONDS", 0)
    created: list[HomeRoutinesCoordinator] = []
    original_init = HomeRoutinesCoordinator.__init__

    def recording_init(self, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(HomeRoutinesCoordinator, "__init__", recording_init)
    yield
    for coord in created:
        await coord.async_unload(save=False)


def _levels(group) -> list[list[str]]:
    return [list(level.entity_ids) for level in group.levels]


def _device(
    hass,
    domain,
    entity_ids,
    *,
    mac=MAC,
    connections=None,
    entry=None,
    name="Bedroom curtain",
    unique=None,
):
    entry = entry or MockConfigEntry(domain=domain)
    if entry.entry_id not in hass.config_entries.async_entry_ids():
        entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(domain, unique or f"{domain}-{mac}-{entity_ids[0]}")},
        connections=connections or {(dr.CONNECTION_NETWORK_MAC, mac)},
        name=name,
    )
    for entity_id in entity_ids:
        platform, object_id = entity_id.split(".")
        er.async_get(hass).async_get_or_create(
            platform,
            domain,
            f"{domain}-{object_id}",
            suggested_object_id=object_id,
            device_id=device.id,
            config_entry=entry,
        )
    return entry


def _curtain(hass):
    for domain, entity_id in WAYS.items():
        _device(hass, domain, [entity_id])
        hass.states.async_set(entity_id, "open", {"friendly_name": "Bedroom curtain"})


def _pair_curtains(hass):
    """One Matter pair device carrying both sides' MACs, plus left and right SwitchBot.

    This is the registry shape Device Linker leaves when a Matter pair is in
    two links (Office Left and Office Right), or one pair plus four SwitchBot
    devices that share those addresses.
    """
    ble_entry = MockConfigEntry(domain="switchbot")
    cloud_entry = MockConfigEntry(domain="switchbot_cloud")
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
    _device(
        hass,
        "switchbot",
        [PAIR["left_ble"]],
        mac=LEFT_MAC,
        entry=ble_entry,
        name=PAIR_NAMES[PAIR["left_ble"]],
    )
    _device(
        hass,
        "switchbot",
        [PAIR["right_ble"]],
        mac=RIGHT_MAC,
        entry=ble_entry,
        name=PAIR_NAMES[PAIR["right_ble"]],
    )
    _device(
        hass,
        "switchbot_cloud",
        [PAIR["left_cloud"]],
        mac=LEFT_MAC,
        entry=cloud_entry,
        name=PAIR_NAMES[PAIR["left_cloud"]],
    )
    _device(
        hass,
        "switchbot_cloud",
        [PAIR["right_cloud"]],
        mac=RIGHT_MAC,
        entry=cloud_entry,
        name=PAIR_NAMES[PAIR["right_cloud"]],
    )
    for entity_id, name in PAIR_NAMES.items():
        hass.states.async_set(entity_id, "open", {"friendly_name": name})


def _closes(hass, behaviour, *, names=None, also=None):
    """cover.close_cover: 'raise', 'ignore', or (default) close it, per entity.

    ``also`` maps a called entity to others that should close with it (a
    Matter pair closing every way of both curtains, for example).
    """
    names = names or {}
    also = also or {}
    calls: list[str] = []

    async def close(call: ServiceCall) -> None:
        for entity_id in call.data["entity_id"]:
            calls.append(entity_id)
            how = behaviour.get(entity_id)
            if how == "raise":
                raise HomeAssistantError("Failed to connect")
            if how == "ignore":
                continue
            to_close = [entity_id, *also.get(entity_id, ())]
            for other in to_close:
                friendly = names.get(other) or (
                    hass.states.get(other).name if hass.states.get(other) else other
                )
                hass.states.async_set(other, "closed", {"friendly_name": friendly})

    hass.services.async_register("cover", "close_cover", close)
    return calls


def _covers(hass, behaviour):
    """The 1.21.0 single-curtain helper: one close is visible on every way."""
    return _closes(
        hass,
        behaviour,
        names=dict.fromkeys(WAYS.values(), "Bedroom curtain"),
        also={entity_id: list(WAYS.values()) for entity_id in WAYS.values()},
    )


def _pair_also() -> dict[str, list[str]]:
    sides = {
        PAIR["matter"]: list(PAIR.values()),
        PAIR["left_ble"]: [PAIR["left_ble"], PAIR["left_cloud"]],
        PAIR["left_cloud"]: [PAIR["left_ble"], PAIR["left_cloud"]],
        PAIR["right_ble"]: [PAIR["right_ble"], PAIR["right_cloud"]],
        PAIR["right_cloud"]: [PAIR["right_ble"], PAIR["right_cloud"]],
    }
    return sides


async def _coord(hass, *, entities, **routine) -> HomeRoutinesCoordinator:
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [
        make_routine(
            actions=[
                {
                    "service": "cover.close_cover",
                    "target": {"entity_id": list(entities)},
                    "data": {},
                }
            ],
            retry_later_minutes=0,
            **routine,
        )
    ]
    await coord.async_set_settings(
        {**coord.settings, "integration_order": ORDER, "verify_seconds": 1}
    )
    coord._start_all(catch_up=False)  # noqa: SLF001
    await hass.async_block_till_done()
    return coord


# ----- 1.21.0: one curtain, three ways -----


async def test_linked_entities_are_one_device_in_order(hass: HomeAssistant):
    _curtain(hass)
    entities = sorted(WAYS.values())
    groups = linked_groups(hass, entities, ORDER)
    assert len(groups) == 1
    assert _levels(groups[0]) == [
        [WAYS["matter"]],
        [WAYS["switchbot"]],
        [WAYS["switchbot_cloud"]],
    ]
    assert _levels(linked_groups(hass, entities, ["switchbot_cloud"])[0])[0] == [
        WAYS["switchbot_cloud"]
    ]


async def test_the_first_way_that_works_is_the_only_one_used(hass: HomeAssistant):
    _curtain(hass)
    calls = _covers(hass, {})
    coord = await _coord(hass, entities=list(WAYS.values()))
    assert await coord.async_run("routine_one") is True
    assert calls == ["cover.curtain_matter"]


async def test_falls_back_when_a_way_fails(hass: HomeAssistant):
    _curtain(hass)
    calls = _covers(hass, {"cover.curtain_matter": "raise"})
    coord = await _coord(hass, entities=list(WAYS.values()))
    assert await coord.async_run("routine_one") is True
    assert calls == ["cover.curtain_matter", "cover.curtain_ble"]


async def test_falls_back_when_nothing_changed(hass: HomeAssistant):
    _curtain(hass)
    calls = _covers(hass, {"cover.curtain_matter": "ignore"})
    coord = await _coord(hass, entities=list(WAYS.values()))
    assert await coord.async_run("routine_one") is True
    assert calls == ["cover.curtain_matter", "cover.curtain_ble"]


async def test_fails_only_when_every_way_failed(hass: HomeAssistant):
    _curtain(hass)
    calls = _covers(hass, dict.fromkeys(WAYS.values(), "raise"))
    coord = await _coord(hass, entities=list(WAYS.values()))
    assert await coord.async_run("routine_one") is False
    assert calls == list(WAYS.values())
    assert (
        coord.get_history("routine_one")[0]["detail"]
        == "Bedroom curtain couldn't be reached"
    )


async def test_a_routine_can_have_its_own_order(hass: HomeAssistant):
    _curtain(hass)
    calls = _covers(hass, {})
    coord = await _coord(
        hass, entities=list(WAYS.values()), integration_order=["switchbot", "matter"]
    )
    assert await coord.async_run("routine_one") is True
    assert calls == ["cover.curtain_ble"]


async def test_already_done_believes_the_first_way(hass: HomeAssistant):
    _curtain(hass)
    hass.states.async_set("cover.curtain_matter", "closed")  # the cloud still says open
    calls = _covers(hass, {})
    coord = await _coord(hass, entities=list(WAYS.values()))
    await coord.async_run("routine_one")
    assert calls == []


async def test_a_device_with_two_channels_is_not_grouped(hass: HomeAssistant):
    _device(hass, "shelly", ["switch.left", "switch.right"], mac="aa:bb:cc:dd:ee:02")
    _device(hass, "matter", ["switch.both"], mac="aa:bb:cc:dd:ee:02")
    groups = linked_groups(hass, ["switch.both", "switch.left", "switch.right"], [])
    assert [group.entity_ids for group in groups] == [
        ["switch.both"],
        ["switch.left"],
        ["switch.right"],
    ]
    assert all(not group.has_fallback for group in groups)


async def test_a_curtain_still_closing_is_given_time(hass: HomeAssistant):
    from homeassistant.helpers.event import async_call_later

    _curtain(hass)
    calls: list[str] = []

    async def close(call: ServiceCall) -> None:
        calls.extend(call.data["entity_id"])
        hass.states.async_set("cover.curtain_matter", "closing")
        async_call_later(
            hass,
            1.5,
            lambda _now: hass.states.async_set("cover.curtain_matter", "closed"),
        )

    hass.services.async_register("cover", "close_cover", close)
    coord = await _coord(hass, entities=list(WAYS.values()))
    assert await coord.async_run("routine_one") is True
    assert calls == ["cover.curtain_matter"]


# ----- 1.32.0: a Matter pair covering left and right SwitchBot curtains -----


async def test_pair_is_grouped_in_integration_levels(hass: HomeAssistant):
    _pair_curtains(hass)
    groups = linked_groups(hass, list(PAIR.values()), ORDER)
    assert len(groups) == 1
    assert _levels(groups[0]) == [[PAIR["matter"]], BLE_PAIR, CLOUD_PAIR]
    assert groups[0].levels[0].covering is True
    assert groups[0].has_fallback is True


async def test_pair_matter_is_included_when_the_step_only_lists_switchbot(
    hass: HomeAssistant,
):
    """A higher-priority covering entity is brought in even when not targeted."""
    _pair_curtains(hass)
    groups = linked_groups(hass, SWITCHBOT_PAIR, ORDER)
    assert _levels(groups[0]) == [[PAIR["matter"]], BLE_PAIR, CLOUD_PAIR]


async def test_boys_bedroom_uses_only_matter_when_it_works(hass: HomeAssistant):
    _pair_curtains(hass)
    calls = _closes(hass, {}, names=PAIR_NAMES, also=_pair_also())
    coord = await _coord(hass, entities=list(PAIR.values()))
    assert await coord.async_run("routine_one") is True
    assert calls == [PAIR["matter"]]


async def test_boys_bedroom_falls_back_to_both_ble_when_matter_fails(
    hass: HomeAssistant,
):
    _pair_curtains(hass)
    calls = _closes(
        hass, {PAIR["matter"]: "raise"}, names=PAIR_NAMES, also=_pair_also()
    )
    coord = await _coord(hass, entities=list(PAIR.values()))
    assert await coord.async_run("routine_one") is True
    assert calls == [PAIR["matter"], *BLE_PAIR]


async def test_boys_bedroom_falls_back_to_cloud_after_ble_failure(
    hass: HomeAssistant,
):
    _pair_curtains(hass)
    calls = _closes(
        hass,
        {PAIR["matter"]: "raise", **dict.fromkeys(BLE_PAIR, "raise")},
        names=PAIR_NAMES,
        also=_pair_also(),
    )
    coord = await _coord(hass, entities=list(PAIR.values()))
    assert await coord.async_run("routine_one") is True
    assert calls[0] == PAIR["matter"]
    assert set(c for c in calls if c in BLE_PAIR) == set(BLE_PAIR)
    assert set(c for c in calls if c in CLOUD_PAIR) == set(CLOUD_PAIR)
    ble_at = [i for i, c in enumerate(calls) if c in BLE_PAIR]
    cloud_at = [i for i, c in enumerate(calls) if c in CLOUD_PAIR]
    assert ble_at and cloud_at and min(cloud_at) > max(ble_at)


async def test_office_multi_link_matter_is_one_cluster(hass: HomeAssistant):
    """A Matter pair in two 3-device links (each side's MAC) is one cluster."""
    _pair_curtains(hass)
    # Targeting only the Office-style 3+3 entities is the same registry.
    groups = linked_groups(hass, list(PAIR.values()), ORDER)
    assert len(groups) == 1
    assert groups[0].levels[0].entity_ids == (PAIR["matter"],)
    assert set(groups[0].levels[1].entity_ids) == set(BLE_PAIR)
    coord = await _coord(hass, entities=SWITCHBOT_PAIR)
    calls = _closes(hass, {}, names=PAIR_NAMES, also=_pair_also())
    # Register after coord so the close handler is the one used; _coord does
    # not call the service. Re-register is fine.
    assert await coord.async_run("routine_one") is True
    assert calls == [PAIR["matter"]]


async def test_left_only_step_does_not_fire_the_pair_entity(hass: HomeAssistant):
    _pair_curtains(hass)
    left = [PAIR["left_ble"], PAIR["left_cloud"]]
    groups = linked_groups(hass, left, ORDER)
    assert len(groups) == 1
    assert PAIR["matter"] not in groups[0].entity_ids
    assert _levels(groups[0]) == [[PAIR["left_ble"]], [PAIR["left_cloud"]]]
    calls = _closes(hass, {}, names=PAIR_NAMES, also=_pair_also())
    coord = await _coord(hass, entities=left)
    assert await coord.async_run("routine_one") is True
    assert calls == [PAIR["left_ble"]]
    assert PAIR["matter"] not in calls
    assert PAIR["right_ble"] not in calls


async def test_pair_failure_message_names_the_last_level(hass: HomeAssistant):
    _pair_curtains(hass)
    calls = _closes(
        hass,
        dict.fromkeys(PAIR.values(), "raise"),
        names=PAIR_NAMES,
        also=_pair_also(),
    )
    coord = await _coord(hass, entities=list(PAIR.values()))
    assert await coord.async_run("routine_one") is False
    assert calls[0] == PAIR["matter"]
    assert set(c for c in calls if c in BLE_PAIR) == set(BLE_PAIR)
    assert set(c for c in calls if c in CLOUD_PAIR) == set(CLOUD_PAIR)
    ble_at = [i for i, c in enumerate(calls) if c in BLE_PAIR]
    cloud_at = [i for i, c in enumerate(calls) if c in CLOUD_PAIR]
    assert ble_at and cloud_at and min(cloud_at) > max(ble_at)
    detail = coord.get_history("routine_one")[0]["detail"]
    assert "Boy's Bedroom Left Curtain (Cloud)" in detail
    assert "Boy's Bedroom Right Curtain (Cloud)" in detail
    assert "couldn't be reached" in detail
    assert "Boy's Bedroom Curtains" not in detail
