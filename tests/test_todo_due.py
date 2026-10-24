"""To-do complete and remove steps can act only on items due in a chosen window."""

from __future__ import annotations

from datetime import date

import pytest
from homeassistant.core import HomeAssistant

from custom_components.daily_routine.schema import vol
from custom_components.daily_routine.text_format import parse_steps_text, steps_to_text
from custom_components.daily_routine.todo import (
    apply_todo_step,
    due_allows,
    normalize_todo_step,
)

from .test_todo_rules import GROCERIES, FakeTodo

TODAY = date(2026, 10, 23)
TODAY_ISO = "2026-10-23"


def _item(due: str | None) -> dict:
    item = {"summary": "Milk", "status": "needs_action"}
    if due is not None:
        item["due"] = due
    return item


def _window(within, overdue=True, undated=False) -> dict:
    return {"within": within, "overdue": overdue, "undated": undated}


@pytest.mark.parametrize(
    ("due", "within", "overdue", "undated", "expected"),
    [
        ("2026-10-23", 0, True, False, True),  # today, today only
        ("2026-10-24", 0, True, False, False),  # tomorrow is outside "today only"
        ("2026-10-24", 1, True, False, True),  # tomorrow, within 1 day
        ("2026-11-13", 21, True, False, True),  # exactly 3 weeks out
        ("2026-11-14", 21, True, False, False),  # one day past 3 weeks
        ("2026-10-20", 0, True, False, True),  # overdue, included
        ("2026-10-20", 0, False, False, False),  # overdue, left out
        (None, 0, True, False, False),  # no due date, left out
        (None, 0, True, True, True),  # no due date, included
        ("2026-12-31", None, True, False, True),  # any date: every dated item
    ],
)
def test_due_window_choices(due, within, overdue, undated, expected) -> None:
    assert due_allows(_item(due), _window(within, overdue, undated), TODAY) is expected


def test_no_due_check_allows_everything() -> None:
    assert due_allows(_item(None), None, TODAY) is True


def test_due_time_with_no_timezone_counts_its_day() -> None:
    assert due_allows(_item("2026-10-23T09:00:00"), _window(0), TODAY) is True


def test_due_check_is_kept_for_complete_and_remove_only() -> None:
    step = normalize_todo_step(
        {"op": "complete", "list": GROCERIES, "item": "Milk", "due": _window(1)}, 1
    )
    assert step["due"] == {"within": 1, "overdue": True, "undated": False}
    with pytest.raises(vol.Invalid):
        normalize_todo_step({"op": "add", "list": GROCERIES, "item": "Milk", "due": _window(1)}, 1)


def test_due_window_must_be_a_sensible_number_of_days() -> None:
    with pytest.raises(vol.Invalid):
        normalize_todo_step(
            {"op": "complete", "list": GROCERIES, "item": "Milk", "due": _window(400)}, 1
        )
    with pytest.raises(vol.Invalid):
        normalize_todo_step(
            {"op": "complete", "list": GROCERIES, "item": "Milk", "due": _window(True)}, 1
        )


def test_one_line_syntax_carries_the_due_options_both_ways() -> None:
    line = "todo complete | todo.groceries | Buy milk | due=1 overdue=no undated=yes"
    action = parse_steps_text(line)[0]["todo"]
    assert action["due"] == {"within": 1, "overdue": False, "undated": True}
    assert steps_to_text([{"todo": action}]) == line


def test_one_line_syntax_without_due_options_is_unchanged() -> None:
    action = parse_steps_text("todo complete | todo.groceries | Buy milk")[0]["todo"]
    assert "due" not in action
    assert steps_to_text([{"todo": action}]) == "todo complete | todo.groceries | Buy milk"


def test_one_line_due_option_errors_name_the_line() -> None:
    with pytest.raises(vol.Invalid, match="Line 1"):
        parse_steps_text("todo complete | todo.groceries | Buy milk | soon")


async def test_item_outside_the_window_is_left_on_the_list(
    hass: HomeAssistant, freezer, enable_custom_integrations, hass_storage
) -> None:
    freezer.move_to("2026-10-23 12:00:00")
    fake = FakeTodo(
        hass, [{"uid": "1", "summary": "Milk", "status": "needs_action", "due": "2026-10-24"}]
    )
    step = {"op": "complete", "list": GROCERIES, "item": "Milk", "due": _window(0)}

    assert await apply_todo_step(hass, step, dry_run=True) == "not_due"
    assert await apply_todo_step(hass, step, dry_run=False) == "not_due"
    assert fake.updates == [], "nothing is changed on the list"


async def test_item_inside_the_window_is_completed(
    hass: HomeAssistant, freezer, enable_custom_integrations, hass_storage
) -> None:
    freezer.move_to("2026-10-23 12:00:00")
    fake = FakeTodo(
        hass, [{"uid": "1", "summary": "Milk", "status": "needs_action", "due": TODAY_ISO}]
    )
    step = {"op": "complete", "list": GROCERIES, "item": "Milk", "due": _window(0)}

    assert await apply_todo_step(hass, step, dry_run=False) == "completed"
    assert len(fake.updates) == 1


async def test_remove_respects_the_window_too(
    hass: HomeAssistant, freezer, enable_custom_integrations, hass_storage
) -> None:
    freezer.move_to("2026-10-23 12:00:00")
    fake = FakeTodo(hass, [{"uid": "1", "summary": "Milk", "status": "needs_action"}])
    step = {"op": "remove", "list": GROCERIES, "item": "Milk", "due": _window(0, undated=False)}

    assert await apply_todo_step(hass, step, dry_run=False) == "not_due"
    assert fake.removes == []
