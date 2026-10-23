"""v1.0.0 config entries convert into storage routines."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.daily_routine.const import (
    DOMAIN,
    LEGACY_CONF_ROUTINE_ID,
    LEGACY_CONF_ROUTINES_JSON,
)
from custom_components.daily_routine.legacy import legacy_routines
from custom_components.daily_routine.routines import normalize_routine


def test_per_routine_entry_converts() -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Away lights off",
        data={LEGACY_CONF_ROUTINE_ID: "away_lights_off"},
        options={
            "name": "Away lights off",
            "trigger": "everyone_leaves",
            "time": "22:00:00",
            "people": ["person.a"],
            "require_sensors_clear": True,
            "sensors": ["binary_sensor.hall"],
            "clear_minutes": 15,
            "action_1_service": "light.turn_off",
            "action_1_entities": ["light.hall"],
            "action_2_delay": 5,
            "action_2_service": "notify.persistent_notification",
        },
    )
    raws = legacy_routines([entry])
    assert len(raws) == 1
    routine = normalize_routine(raws[0])
    assert routine["id"] == "away_lights_off"
    assert routine["trigger"] == "everyone_leaves"
    # Leaving triggers already check that everyone is away, so no extra condition is added.
    assert routine["conditions"] == [
        {"kind": "sensors", "sensors": ["binary_sensor.hall"], "mode": "quiet", "minutes": 15}
    ]
    assert routine["actions"][0] == {
        "service": "light.turn_off",
        "data": {},
        "target": {"entity_id": ["light.hall"]},
    }
    # v1.0.0 put a step's delay after that step (action_2 here).
    assert routine["actions"][1]["service"] == "notify.persistent_notification"
    assert routine["actions"][2] == {"delay": 5.0}


def test_json_option_converts() -> None:
    raw_json = json.dumps(
        [
            {
                "id": "morning_covers",
                "name": "Morning curtains",
                "enabled": True,
                "time": "07:00:00",
                "days": ["mon", "tue"],
                "require_everyone_away": False,
                "require_sensors_clear": False,
                "clear_minutes": 0,
                "people": [],
                "sensors": [],
                "actions": [
                    {
                        "service": "cover.open_cover",
                        "target": {"entity_id": ["cover.a"]},
                        "data": {},
                    }
                ],
            }
        ]
    )
    entry = MockConfigEntry(
        domain=DOMAIN, title="Daily Routine", data={}, options={LEGACY_CONF_ROUTINES_JSON: raw_json}
    )
    raws = legacy_routines([entry])
    assert [normalize_routine(r)["id"] for r in raws] == ["morning_covers"]


async def test_setup_migrates_and_removes_extra_entries(
    hass: HomeAssistant, enable_custom_integrations
) -> None:
    first = MockConfigEntry(
        domain=DOMAIN,
        title="Morning covers",
        data={LEGACY_CONF_ROUTINE_ID: "morning_covers"},
        options={
            "name": "Morning covers",
            "time": "07:00:00",
            "action_1_service": "cover.open_cover",
            "action_1_entities": ["cover.a"],
        },
        unique_id="morning_covers",
    )
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Bedroom covers",
        data={LEGACY_CONF_ROUTINE_ID: "bedroom_covers"},
        options={
            "name": "Bedroom covers",
            "time": "08:00:00",
            "action_1_service": "cover.open_cover",
            "action_1_entities": ["cover.b"],
        },
        unique_id="bedroom_covers",
    )
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN]["coordinator"]
    assert {r["id"] for r in coordinator.routines} == {"morning_covers", "bedroom_covers"}
    assert coordinator.migrated is True
    assert hass.config_entries.async_get_entry(first.entry_id).data == {}
    leftover = hass.config_entries.async_get_entry(second.entry_id)
    assert leftover is not None and leftover.data == {} and leftover.options == {}
    sensor_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{DOMAIN}_morning_covers_status"
    )
    assert sensor_id is not None and hass.states.get(sensor_id) is not None
    sensor_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{DOMAIN}_bedroom_covers_status"
    )
    assert sensor_id is not None and hass.states.get(sensor_id) is not None
