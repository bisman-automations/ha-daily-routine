"""1.21.0: a device in more than one integration is reached one way at a time."""

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


def _device(hass, domain, entity_ids, *, mac=MAC, entry=None):
    entry = entry or MockConfigEntry(domain=domain)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(domain, f"{domain}-{mac}")},
        connections={(dr.CONNECTION_NETWORK_MAC, mac)},
        name="Bedroom curtain",
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


def _covers(hass, behaviour):
    """cover.close_cover: 'raise', 'ignore', or (default) close it, per entity."""
    calls: list[str] = []

    async def close(call: ServiceCall) -> None:
        for entity_id in call.data["entity_id"]:
            calls.append(entity_id)
            how = behaviour.get(entity_id)
            if how == "raise":
                raise HomeAssistantError("Failed to connect")
            if how != "ignore":
                for other in WAYS.values():  # the curtain closes, every way sees it
                    hass.states.async_set(
                        other, "closed", {"friendly_name": "Bedroom curtain"}
                    )

    hass.services.async_register("cover", "close_cover", close)
    return calls


async def _coord(hass, **routine) -> HomeRoutinesCoordinator:
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [
        make_routine(
            actions=[
                {
                    "service": "cover.close_cover",
                    "target": {"entity_id": list(WAYS.values())},
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


async def test_linked_entities_are_one_device_in_order(hass: HomeAssistant):
    _curtain(hass)
    entities = sorted(WAYS.values())
    assert linked_groups(hass, entities, ORDER) == [list(WAYS.values())]
    assert (
        linked_groups(hass, entities, ["switchbot_cloud"])[0][0]
        == "cover.curtain_cloud"
    )


async def test_the_first_way_that_works_is_the_only_one_used(hass: HomeAssistant):
    _curtain(hass)
    calls = _covers(hass, {})
    coord = await _coord(hass)
    assert await coord.async_run("routine_one") is True
    assert calls == ["cover.curtain_matter"]


async def test_falls_back_when_a_way_fails(hass: HomeAssistant):
    _curtain(hass)
    calls = _covers(hass, {"cover.curtain_matter": "raise"})
    coord = await _coord(hass)
    assert await coord.async_run("routine_one") is True
    assert calls == ["cover.curtain_matter", "cover.curtain_ble"]


async def test_falls_back_when_nothing_changed(hass: HomeAssistant):
    _curtain(hass)
    calls = _covers(hass, {"cover.curtain_matter": "ignore"})
    coord = await _coord(hass)
    assert await coord.async_run("routine_one") is True
    assert calls == ["cover.curtain_matter", "cover.curtain_ble"]


async def test_fails_only_when_every_way_failed(hass: HomeAssistant):
    _curtain(hass)
    calls = _covers(hass, dict.fromkeys(WAYS.values(), "raise"))
    coord = await _coord(hass)
    assert await coord.async_run("routine_one") is False
    assert calls == list(WAYS.values())
    assert (
        coord.get_history("routine_one")[0]["detail"]
        == "Bedroom curtain couldn't be reached"
    )


async def test_a_routine_can_have_its_own_order(hass: HomeAssistant):
    _curtain(hass)
    calls = _covers(hass, {})
    coord = await _coord(hass, integration_order=["switchbot", "matter"])
    assert await coord.async_run("routine_one") is True
    assert calls == ["cover.curtain_ble"]


async def test_already_done_believes_the_first_way(hass: HomeAssistant):
    _curtain(hass)
    hass.states.async_set("cover.curtain_matter", "closed")  # the cloud still says open
    calls = _covers(hass, {})
    coord = await _coord(hass)
    await coord.async_run("routine_one")
    assert calls == []


async def test_a_device_with_two_channels_is_not_grouped(hass: HomeAssistant):
    _device(hass, "shelly", ["switch.left", "switch.right"], mac="aa:bb:cc:dd:ee:02")
    _device(hass, "matter", ["switch.both"], mac="aa:bb:cc:dd:ee:02")
    groups = linked_groups(hass, ["switch.both", "switch.left", "switch.right"], [])
    assert groups == [["switch.both"], ["switch.left"], ["switch.right"]]


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
    coord = await _coord(hass)
    assert await coord.async_run("routine_one") is True
    assert calls == ["cover.curtain_matter"]
