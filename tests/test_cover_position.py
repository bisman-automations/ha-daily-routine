"""1.34.4 and 1.35.0: curtains judged by position, with limits per step."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service
import voluptuous as vol

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


# ----- 1.35.0: each open or close step's own limits -----


def _step(service: str, **limits) -> dict:
    return {
        "service": service,
        "target": {"entity_id": [CURTAIN]},
        "data": {},
        **limits,
    }


def test_limits_are_kept_on_cover_steps_only() -> None:
    routine = make_routine(
        actions=[
            _step("cover.close_cover", closed_at=10, open_at="90"),
            _step("cover.open_cover", closed_at=""),
            {**_step("light.turn_on"), "closed_at": 10},
        ]
    )
    close, open_, light = routine["actions"]
    assert (close["closed_at"], close["open_at"]) == (10, 90)
    assert "closed_at" not in open_ and "open_at" not in open_
    assert "closed_at" not in light


@pytest.mark.parametrize(
    "limits",
    [
        {"closed_at": 50, "open_at": 40},
        {"open_at": 3},
        {"closed_at": 100},
        {"open_at": 0},
    ],
)
def test_impossible_limits_are_refused(limits) -> None:
    with pytest.raises(vol.Invalid):
        make_routine(actions=[_step("cover.open_cover", **limits)])


def test_cover_is_with_limits() -> None:
    assert cover_is("open", 10, "closed", 10, 90) is True
    assert cover_is("open", 50, "closed", 10, 90) is False
    assert cover_is("open", 50, "open", 10, 90) is False
    assert cover_is("open", 90, "open", 10, 90) is True


async def test_open_step_runs_until_its_open_limit(hass: HomeAssistant) -> None:
    """Open with 'counts as open at 95' runs for a curtain stopped half open."""
    hass.states.async_set(CURTAIN, "open", {"current_position": 50})
    calls = async_mock_service(hass, "cover", "open_cover")
    routine = make_routine(
        trigger="manual", actions=[_step("cover.open_cover", open_at=95)]
    )
    coord = await _started(hass, routine)

    assert await coord.async_run("routine_one")
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_close_step_uses_its_closed_limit(hass: HomeAssistant) -> None:
    hass.states.async_set(CURTAIN, "open", {"current_position": 8})
    coord = await _started(
        hass,
        make_routine(
            trigger="manual", actions=[_step("cover.close_cover", closed_at=10)]
        ),
    )
    routine = coord.routines[0]
    outcome, _, _ = coord._plan_action(routine, routine["actions"][0])  # noqa: SLF001
    assert outcome == "already_in_state"
    # Other checks (no step) keep the default: 8 % is not closed there.
    assert not coord._state_is(CURTAIN, "closed")  # noqa: SLF001


async def test_step_check_accepts_a_curtain_within_its_closed_limit(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check after the step uses the step's limit: stopping at 8 % with
    'counts as closed at 10' is a success, not a failed step sent again."""
    from custom_components.daily_routine import coordinator as module

    monkeypatch.setattr(module, "VERIFY_POLL_SECONDS", 0.01)
    hass.states.async_set(CURTAIN, "open", {"current_position": 60})
    calls = []

    async def close(call) -> None:
        calls.append(call)
        hass.states.async_set(CURTAIN, "open", {"current_position": 8})

    hass.services.async_register("cover", "close_cover", close)
    coord = HomeRoutinesCoordinator(hass)
    coord.routines = [
        make_routine(
            trigger="manual",
            retry_later_minutes=0,
            actions=[_step("cover.close_cover", closed_at=10)],
        )
    ]
    await coord.async_set_settings({**coord.settings, "verify_seconds": 1})
    coord._start_all(catch_up=False)  # noqa: SLF001
    await hass.async_block_till_done()

    assert await coord.async_run("routine_one") is True
    assert len(calls) == 1
    await coord.async_unload(save=False)
