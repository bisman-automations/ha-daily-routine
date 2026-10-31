"""Who may see Daily Routine: admins, plus the people an admin lets in."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import CLIENT_ID, MockConfigEntry

from custom_components.daily_routine.const import DOMAIN
from custom_components.daily_routine.panel import _devices
from homeassistant.auth.models import User
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import async_get_integration


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, unique_id=DOMAIN
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
async def kid(hass: HomeAssistant, hass_admin_user: User) -> User:
    """A Home Assistant login that isn't an admin (the first login is the owner)."""
    return await hass.auth.async_create_user("Kid", group_ids=["system-users"])


def _panel_admin_only(hass: HomeAssistant) -> bool:
    return hass.data["frontend_panels"]["daily-routine"].require_admin


async def _ws(client, **message):
    await client.send_json_auto_id(message)
    return await client.receive_json()


async def test_only_admins_see_routines_until_an_admin_lets_someone_in(
    hass: HomeAssistant,
    enable_custom_integrations,
    hass_ws_client,
    hass_admin_user,
    kid,
) -> None:
    await _setup(hass)
    assert _panel_admin_only(hass)

    # A login that isn't an admin is turned away, and told why.
    token = hass.auth.async_create_access_token(
        await hass.auth.async_create_refresh_token(kid, CLIENT_ID)
    )
    reader = await hass_ws_client(hass, token)
    me = await _ws(reader, type=f"{DOMAIN}/access/me")
    assert me["result"] == {"allowed": False, "is_admin": False}
    for command in ("overview", "status", "activity"):
        res = await _ws(reader, type=f"{DOMAIN}/{command}")
        assert res["error"]["code"] == "unauthorized", command
    res = await _ws(reader, type=f"{DOMAIN}/access/get")
    assert res["error"]["code"] == "unauthorized"

    # An admin lets them in.
    admin = await hass_ws_client(hass)
    people = await _ws(admin, type=f"{DOMAIN}/access/get")
    assert people["result"]["user_ids"] == []
    assert kid.id in {p["id"] for p in people["result"]["people"]}
    res = await _ws(
        admin,
        type=f"{DOMAIN}/access/set",
        user_ids=[kid.id, "not-a-user"],
    )
    assert res["result"]["user_ids"] == [kid.id]
    assert not _panel_admin_only(hass)  # the sidebar entry now shows to everyone

    me = await _ws(reader, type=f"{DOMAIN}/access/me")
    assert me["result"]["allowed"] is True
    res = await _ws(reader, type=f"{DOMAIN}/overview")
    assert res["success"]
    # Changing routines is still admin-only.
    res = await _ws(reader, type=f"{DOMAIN}/pause", paused=True)
    assert res["error"]["code"] == "unauthorized"

    await _ws(admin, type=f"{DOMAIN}/access/set", user_ids=[])
    assert _panel_admin_only(hass)


async def test_access_is_kept_across_restarts(
    hass: HomeAssistant, enable_custom_integrations, hass_storage, kid
) -> None:
    hass_storage[f"{DOMAIN}.access"] = {
        "version": 1,
        "key": f"{DOMAIN}.access",
        "data": {"user_ids": [kid.id]},
    }
    await _setup(hass)
    assert not _panel_admin_only(hass)


async def test_pause_switch_and_actions_need_access(
    hass: HomeAssistant,
    enable_custom_integrations,
    hass_admin_user,
    kid,
) -> None:
    await _setup(hass)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": "switch.pause_all_routines"},
            blocking=True,
            context=Context(user_id=kid.id),
        )
    assert err.value.translation_key == "not_allowed"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "reload_routines",
            {},
            blocking=True,
            context=Context(user_id=kid.id),
        )

    # Admins, and automations (no user), may.
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.pause_all_routines"},
        blocking=True,
        context=Context(user_id=hass_admin_user.id),
    )
    assert hass.states.get("switch.pause_all_routines").state == "on"
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.pause_all_routines"}, blocking=True
    )
    assert hass.states.get("switch.pause_all_routines").state == "off"


async def test_devices_name_the_integration_they_come_from(hass: HomeAssistant) -> None:
    """Linked device choices say which integration each device comes from."""
    from custom_components.daily_routine.panel import _devices
    from homeassistant.helpers import device_registry as dr
    from homeassistant.loader import async_get_integration

    await async_get_integration(hass, "hue")
    registry = dr.async_get(hass)
    for domain in ("hue", "made_up_cloud"):
        source = MockConfigEntry(domain=domain)
        source.add_to_hass(hass)
        registry.async_get_or_create(
            config_entry_id=source.entry_id,
            identifiers={(domain, "curtains")},
            name="Office Curtains",
        )
    assert sorted(d["integration"] for d in _devices(hass)) == [
        "Philips Hue",
        "made_up_cloud",
    ]
    assert not any(d["service"] for d in _devices(hass))


async def test_services_are_marked_so_only_devices_are_offered(
    hass: HomeAssistant,
) -> None:
    """A service device, or any device of a service integration, is a service."""
    await async_get_integration(hass, "hue")
    registry = dr.async_get(hass)
    source = MockConfigEntry(domain="made_up_hub")
    source.add_to_hass(hass)
    registry.async_get_or_create(
        config_entry_id=source.entry_id,
        identifiers={("made_up_hub", "routine")},
        name="A routine",
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    registry.async_get_or_create(
        config_entry_id=source.entry_id,
        identifiers={("made_up_hub", "lamp")},
        name="A lamp",
    )
    service = {d["name"]: d["service"] for d in _devices(hass)}
    assert service == {"A lamp": False, "A routine": True}
    with patch(
        "custom_components.daily_routine.panel.async_get_loaded_integration"
    ) as loaded:
        loaded.return_value.name = "Music Assistant"
        loaded.return_value.integration_type = "service"
        assert all(d["service"] for d in _devices(hass))
