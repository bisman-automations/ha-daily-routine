"""To-do steps: add, complete or remove an item on a Home Assistant to-do list.

A to-do step is a routine step like "light.turn_off":
    {"todo": {"op": "add", "list": "todo.groceries", "item": "Empty dishwasher"}}

Each step checks the list first, so it never adds an item that is already open, and it
completes or removes only an open item. A dry run reads the list and changes nothing.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .schema import vol

TODO_OPS = ("add", "complete", "remove")
TODO_LIST_RE = re.compile(r"^todo\.[a-z0-9_]+$")
MAX_ITEM_LENGTH = 255
MAX_DUE_DAYS = 365


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def normalize_todo_step(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise vol.Invalid(f"Step {index}: the to-do step must be an object.")
    op = str(raw.get("op") or "").strip().lower()
    if op not in TODO_OPS:
        raise vol.Invalid(f"Step {index}: the to-do action must be add, complete or remove.")
    todo_list = str(raw.get("list") or "").strip()
    if not TODO_LIST_RE.fullmatch(todo_list):
        raise vol.Invalid(f"Step {index}: pick the to-do list.")
    item = _clean(raw.get("item"))
    if not item:
        raise vol.Invalid(f"Step {index}: name the to-do item.")
    if len(item) > MAX_ITEM_LENGTH:
        raise vol.Invalid(f"Step {index}: the to-do item name is too long.")
    step: dict[str, Any] = {"op": op, "list": todo_list, "item": item}
    due = _normalize_due(raw.get("due"), op, index)
    if due is not None:
        step["due"] = due
    return step


def _normalize_due(raw_due: Any, op: str, index: int) -> dict[str, Any] | None:
    """The due-date check for completing or removing an item. None means no check."""
    if raw_due is None:
        return None
    if op == "add":
        raise vol.Invalid(
            f"Step {index}: a due date can only be checked when completing or removing an item."
        )
    if not isinstance(raw_due, dict):
        raise vol.Invalid(f"Step {index}: the due date check must be an object.")
    within = raw_due.get("within")
    if within is not None and (
        isinstance(within, bool) or not isinstance(within, int) or not 0 <= within <= MAX_DUE_DAYS
    ):
        raise vol.Invalid(
            f"Step {index}: due within must be 0 to {MAX_DUE_DAYS} days, or any date."
        )
    return {
        "within": within,
        "overdue": bool(raw_due.get("overdue", True)),
        "undated": bool(raw_due.get("undated", False)),
    }


def due_date_of(item: dict[str, Any]) -> date | None:
    """The day an item is due, in local time. None when it has no due date."""
    value = item.get("due")
    if not value:
        return None
    text = str(value)
    parsed = dt_util.parse_datetime(text)
    if parsed is not None:
        if parsed.tzinfo is not None:
            return dt_util.as_local(parsed).date()
        return parsed.date()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def due_allows(item: dict[str, Any], due: dict[str, Any] | None, today: date) -> bool:
    """Whether a due-date check lets this item be completed or removed today."""
    if not due:
        return True
    when = due_date_of(item)
    if when is None:
        return bool(due["undated"])
    if when < today:
        return bool(due["overdue"])
    if due["within"] is None:
        return True
    return when <= today + timedelta(days=int(due["within"]))


def find_open_item(items: list[dict[str, Any]], wanted: str) -> dict[str, Any] | None:
    """The open (not done) item with this name, ignoring case and extra spaces."""
    key = _clean(wanted).casefold()
    for item in items:
        if item.get("status", "needs_action") != "needs_action":
            continue
        if _clean(item.get("summary")).casefold() == key:
            return item
    return None


def item_key(text: Any) -> str:
    """How item names are compared: ignoring capitals and extra spaces."""
    return _clean(text).casefold()


async def open_items(hass: HomeAssistant, entity_id: str) -> list[dict[str, Any]] | None:
    """Items on the list that are not done yet, or None when the list cannot be read."""
    return await list_items(hass, entity_id, ["needs_action"])


async def list_items(
    hass: HomeAssistant, entity_id: str, status: list[str]
) -> list[dict[str, Any]] | None:
    """Items with one of these statuses, or None when the list cannot be read."""
    try:
        response = await hass.services.async_call(
            "todo",
            "get_items",
            {"entity_id": entity_id, "status": status},
            blocking=True,
            return_response=True,
        )
    except HomeAssistantError:
        return None
    entity_result = response.get(entity_id) if isinstance(response, dict) else None
    raw_items = entity_result.get("items", []) if isinstance(entity_result, dict) else []
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


# Plain words for each outcome, used in run history and notifications.
TODO_OUTCOME_TEXT = {
    "added": "added",
    "already_on_list": "already on the list",
    "completed": "completed",
    "no_open_item": "nothing open to complete",
    "removed": "removed",
    "not_on_list": "not on the list",
    "list_unavailable": "the list is not available",
    "failed": "could not change the list",
    "would_add": "would be added",
    "would_complete": "would be completed",
    "would_remove": "would be removed",
    "not_due": "not due in the chosen window",
}

# Outcomes that do not change the list: the step is skipped and the routine carries on.
NOOP_OUTCOMES = ("already_on_list", "no_open_item", "not_on_list", "not_due")
# Outcomes that stop the routine: the list could not be read or changed.
FAILED_OUTCOMES = ("list_unavailable", "failed")


async def apply_todo_step(
    hass: HomeAssistant,
    step: dict[str, Any],
    *,
    dry_run: bool,
    wait_seconds: float = 0,
    poll_seconds: float = 2,
) -> str:
    """Make the change, or with dry_run only say what would happen.

    A list that has not loaded yet (for example a cloud calendar after a restart) is
    retried for up to wait_seconds. A dry run never waits.
    """
    entity_id = step["list"]
    items = await open_items(hass, entity_id)
    if items is None and not dry_run and wait_seconds > 0:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_seconds
        while items is None and loop.time() < deadline:
            await asyncio.sleep(min(poll_seconds, max(deadline - loop.time(), 0)))
            items = await open_items(hass, entity_id)
    if items is None:
        return "list_unavailable"
    match = find_open_item(items, step["item"])
    op = step["op"]

    if op == "add":
        if match is not None:
            return "already_on_list"
        if dry_run:
            return "would_add"
    elif op == "complete":
        if match is None:
            return "no_open_item"
        if not due_allows(match, step.get("due"), dt_util.now().date()):
            return "not_due"
        if dry_run:
            return "would_complete"
    else:
        if match is None:
            return "not_on_list"
        if not due_allows(match, step.get("due"), dt_util.now().date()):
            return "not_due"
        if dry_run:
            return "would_remove"

    ref = (match.get("uid") or match.get("summary")) if match is not None else step["item"]
    try:
        if op == "add":
            await hass.services.async_call(
                "todo", "add_item", {"entity_id": entity_id, "item": step["item"]}, blocking=True
            )
            return "added"
        if op == "complete":
            await hass.services.async_call(
                "todo",
                "update_item",
                {"entity_id": entity_id, "item": ref, "status": "completed"},
                blocking=True,
            )
            return "completed"
        await hass.services.async_call(
            "todo", "remove_item", {"entity_id": entity_id, "item": ref}, blocking=True
        )
        return "removed"
    except HomeAssistantError:
        return "failed"
