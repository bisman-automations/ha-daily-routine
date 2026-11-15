"""Low battery sweep: one notification naming devices running low.

A ``check_battery`` step looks at every battery sensor (a percentage with
device class battery) and every low-battery binary sensor in the house,
and sends one notification listing those under its level ("Low batteries:
Back door 12%, Smoke alarm (low)"). Nothing is sent when they're all fine.
Pure apart from reading the states.
"""

from __future__ import annotations

from typing import Any

DEFAULT_BELOW = 20
MAX_LISTED = 15


def low_batteries(
    readings: list[tuple[str, str, str]], below: int
) -> list[tuple[str, str]]:
    """(name, level) for each low battery, lowest first.

    ``readings`` are (name, domain, state): a sensor's percentage, or a
    binary sensor that's "on" when its battery is low.
    """
    found: list[tuple[float, str, str]] = []
    for name, domain, state in readings:
        if domain == "binary_sensor":
            if state == "on":
                found.append((-1.0, name, "low"))
            continue
        try:
            level = float(state)
        except TypeError, ValueError:
            continue  # unknown or unavailable: not counted as low
        if level < below:
            found.append((level, name, f"{round(level)}%"))
    found.sort()
    return [(name, level) for _value, name, level in found]


def battery_text(found: list[tuple[str, str]]) -> str:
    shown = [
        f"{name} {level}" if level != "low" else f"{name} (low)"
        for name, level in found[:MAX_LISTED]
    ]
    more = len(found) - MAX_LISTED
    return (
        "Low batteries: "
        + ", ".join(shown)
        + (f" and {more} more." if more > 0 else ".")
    )


def is_battery(domain: str, attributes: dict[str, Any]) -> bool:
    return attributes.get("device_class") == "battery" and domain in (
        "sensor",
        "binary_sensor",
    )
