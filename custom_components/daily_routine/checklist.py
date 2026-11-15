"""Leaving-home checklist: what's still open, unlocked or on.

A ``check_open`` step looks at the devices it lists and, when any of them is
open, unlocked or on, sends one notification naming them ("Still open: Back
door, Garage door (unlocked)"). Nothing is sent when everything is shut.
Pure, so it can be tested without Home Assistant.
"""

from __future__ import annotations

# Devices that can be checked, and the states that count as "not shut".
OPEN_STATES: dict[str, dict[str, str]] = {
    "binary_sensor": {"on": "open"},
    "cover": {"open": "open", "opening": "opening", "closing": "closing"},
    "lock": {
        "unlocked": "unlocked",
        "unlocking": "unlocking",
        "open": "open",
        "opening": "opening",
        "jammed": "jammed",
    },
    "valve": {"open": "open", "opening": "opening"},
    "light": {"on": "on"},
    "switch": {"on": "on"},
    "fan": {"on": "on"},
    "input_boolean": {"on": "on"},
    "media_player": {"on": "on", "playing": "playing"},
    "climate": {},
}
CHECKABLE = tuple(OPEN_STATES)
MAX_CHECKED = 40


def left_open(states: dict[str, tuple[str, str] | None]) -> list[tuple[str, str]]:
    """(name, word) for each device still open, unlocked or on.

    ``states`` maps an entity id to (name, state), or None when it's missing.
    A missing or unavailable device is reported as "not reporting", so a
    sensor with a flat battery doesn't pass the check unnoticed.
    """
    found: list[tuple[str, str]] = []
    for entity_id, value in states.items():
        domain = entity_id.split(".", 1)[0]
        if value is None:
            found.append((entity_id, "not reporting"))
            continue
        name, state = value
        if state in ("unavailable", "unknown"):
            found.append((name, "not reporting"))
            continue
        if domain == "climate":
            if state not in ("off", ""):
                found.append((name, "on"))
            continue
        word = OPEN_STATES.get(domain, {"on": "on"}).get(state)
        if word:
            found.append((name, word))
    return found


def checklist_text(found: list[tuple[str, str]]) -> str:
    """One line for the notification: "Still open: Back door, Garage (unlocked)"."""
    parts = [name if word == "open" else f"{name} ({word})" for name, word in found]
    return f"Still open: {', '.join(parts)}."
