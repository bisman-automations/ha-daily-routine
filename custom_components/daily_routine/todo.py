"""To-do steps: add, complete or remove an item on a Home Assistant to-do list.

A to-do step is a routine step like "light.turn_off":
    {"todo": {"op": "add", "list": "todo.groceries", "item": "Empty dishwasher"}}

Each step checks the list first, so it never adds an item that is already open, and it
completes or removes only an open item. A dry run reads the list and changes nothing.
"""

from __future__ import annotations

import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .schema import vol

TODO_OPS = ("add", "complete", "remove")
TODO_LIST_RE = re.compile(r"^todo\.[a-z0-9_]+$")
MAX_ITEM_LENGTH = 255


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
    return {"op": op, "list": todo_list, "item": item}


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
}

# Outcomes that do not change the list: the step is skipped and the routine carries on.
NOOP_OUTCOMES = ("already_on_list", "no_open_item", "not_on_list")
# Outcomes that stop the routine: the list could not be read or changed.
FAILED_OUTCOMES = ("list_unavailable", "failed")


async def apply_todo_step(hass: HomeAssistant, step: dict[str, Any], *, dry_run: bool) -> str:
    """Make the change, or with dry_run only say what would happen."""
    entity_id = step["list"]
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
        if dry_run:
            return "would_complete"
    else:
        if match is None:
            return "not_on_list"
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
