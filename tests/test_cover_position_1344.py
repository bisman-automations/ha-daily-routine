"""1.34.4: a curtain that stops at a few percent when closed counts as closed."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.daily_routine.coordinator import HomeRoutinesCoordinator
from custom_components.daily_routine.routines import COVER_CLOSED_POSITION, cover_is
from homeassistant.core import HomeAssistant

from .conftest import make_routine

CURTAIN = "cover.bedroom_curtain"


def _cover_routine(service: str) -> dict:
    return make_routine(
        trigger="manual",
        actions=[{"service": service, "target": {"entity_id": [CURTAIN]}, "data": {}}],
    )


async def _started(hass: HomeAssistant, routine: dict) -> HomeRoutinesCoordinator:
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [routine]
    coord._start_all(catch_up=False)  # noqa: SLF001
    await hass.async_block_till_done()
    return coord


@pytest.mark.parametrize(
    ("state", "position", "expected", "result"),
    [
        ("open", 2, "closed", True),
        ("open", 2, "open", False),
        ("open", COVER_CLOSED_POSITION, "closed", True),
        ("open", COVER_CLOSED_POSITION + 1, "open", True),
        ("open", 50, "closed", False),
        ("closed", 0, "closed", True),
        ("open", None, "open", None),  # no position: the state decides
        ("closing", 2, "closed", None),  # still moving: the state decides
        ("open", 2, "opening", None),
        ("open", "2", "closed", None),
    ],
)
def test_cover_is(state, position, expected, result) -> None:
    assert cover_is(state, position, expected) is result


async def test_open_runs_for_a_curtain_closed_at_two_percent(
    hass: HomeAssistant,
) -> None:
    """Home Assistant calls a curtain at 2 % open; Open used to be skipped."""
    hass.states.async_set(CURTAIN, "open", {"current_position": 2})
    calls = async_mock_service(hass, "cover", "open_cover")
    coord = await _started(hass, _cover_routine("cover.open_cover"))

    assert await coord.async_run("routine_one")
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == [CURTAIN]


async def test_close_is_skipped_for_a_curtain_closed_at_two_percent(
    hass: HomeAssistant,
) -> None:
    hass.states.async_set(CURTAIN, "open", {"current_position": 2})
    coord = await _started(hass, _cover_routine("cover.close_cover"))
    routine = coord.routines[0]

    outcome, _, _ = coord._plan_action(routine, routine["actions"][0])  # noqa: SLF001
    assert outcome == "already_in_state"


async def test_open_still_skipped_when_really_open(hass: HomeAssistant) -> None:
    hass.states.async_set(CURTAIN, "open", {"current_position": 80})
    coord = await _started(hass, _cover_routine("cover.open_cover"))
    routine = coord.routines[0]

    outcome, _, _ = coord._plan_action(routine, routine["actions"][0])  # noqa: SLF001
    assert outcome == "already_in_state"


async def test_curtain_at_two_percent_is_not_left_open(hass: HomeAssistant) -> None:
    hass.states.async_set(CURTAIN, "open", {"current_position": 2})
    hass.states.async_set("cover.garage", "open", {"current_position": 60})
    coord = await _started(hass, _cover_routine("cover.close_cover"))

    found = coord._left_open([CURTAIN, "cover.garage"])  # noqa: SLF001
    assert len(found) == 1
    assert "garage" in found[0][0].lower()
