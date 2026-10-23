"""Plain-text forms of steps and extra conditions, for editing in Home Assistant's Settings.

Steps, one per line:
    light.turn_off | light.kitchen, light.hall
    cover.open_cover | area:living_room
    light.turn_on | light.hall | {"brightness": 128}
    light.turn_off                      (no target)
    wait 30                             (seconds, or 00:00:30)

Extra conditions, one per line:
    lock.front_door = unlocked

Lines that are blank or start with # are ignored. Validation of the values themselves
happens in routines.normalize_routine, so these functions only handle the layout.
"""

from __future__ import annotations

import json
from typing import Any

from .schema import vol

AREA_PREFIX = "area:"
WAIT_WORD = "wait"


def _number_text(seconds: float) -> str:
    return str(int(seconds)) if float(seconds).is_integer() else str(seconds)


def steps_to_text(actions: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for action in actions:
        if "delay" in action:
            lines.append(f"{WAIT_WORD} {_number_text(action['delay'])}")
            continue
        target = action.get("target") or {}
        names = [
            *target.get("entity_id", []),
            *(f"{AREA_PREFIX}{a}" for a in target.get("area_id", [])),
        ]
        data = action.get("data") or {}
        parts = [action["service"]]
        if names or data:
            parts.append(", ".join(names))
        if data:
            parts.append(json.dumps(data, separators=(", ", ": ")))
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _meaningful_lines(text: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for number, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if line and not line.startswith("#"):
            result.append((number, line))
    return result


def parse_steps_text(text: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for number, line in _meaningful_lines(text):
        if (
            line.split(None, 1)[0].lower() == WAIT_WORD
            and len(line.split(None, 1)) == 2
            and "|" not in line
        ):
            actions.append({"delay": line.split(None, 1)[1].strip()})
            continue
        parts = [part.strip() for part in line.split("|", 2)]
        service = parts[0]
        if not service:
            raise vol.Invalid(f"Line {number}: name the action, for example light.turn_off.")
        action: dict[str, Any] = {"service": service}
        target: dict[str, list[str]] = {}
        if len(parts) > 1 and parts[1]:
            for token in (t.strip() for t in parts[1].split(",")):
                if not token:
                    continue
                if token.startswith(AREA_PREFIX):
                    target.setdefault("area_id", []).append(token[len(AREA_PREFIX) :])
                else:
                    target.setdefault("entity_id", []).append(token)
        if target:
            action["target"] = target
        if len(parts) > 2 and parts[2]:
            try:
                data = json.loads(parts[2])
            except ValueError as err:
                raise vol.Invalid(
                    f'Line {number}: extra settings must be JSON, for example {{"brightness": 128}}.'
                ) from err
            if not isinstance(data, dict):
                raise vol.Invalid(f"Line {number}: extra settings must be a JSON object.")
            action["data"] = data
        actions.append(action)
    return actions


def conditions_to_text(conditions: list[dict[str, str]]) -> str:
    return "\n".join(f"{c['entity_id']} = {c['state']}" for c in conditions)


def parse_conditions_text(text: str) -> list[dict[str, str]]:
    conditions: list[dict[str, str]] = []
    for number, line in _meaningful_lines(text):
        if "=" not in line:
            raise vol.Invalid(
                f"Line {number}: write the condition as entity = state, for example lock.front_door = unlocked."
            )
        entity_id, _, state = line.partition("=")
        conditions.append({"entity_id": entity_id.strip(), "state": state.strip()})
    return conditions
