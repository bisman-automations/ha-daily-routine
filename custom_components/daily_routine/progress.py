"""What a running routine is doing, in words, for the progress bar on its card."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar

# A step's action in plain words. Anything else is its service name, tidied up.
VERBS = {
    "turn_on": "Turn on",
    "turn_off": "Turn off",
    "toggle": "Toggle",
    "open_cover": "Open",
    "close_cover": "Close",
    "stop_cover": "Stop",
    "set_cover_position": "Move",
    "lock": "Lock",
    "unlock": "Unlock",
    "speak": "Say something on",
    "media_pause": "Pause",
    "media_play": "Play",
    "media_stop": "Stop",
    "volume_set": "Set the volume of",
    "set_temperature": "Set the temperature of",
    "set_hvac_mode": "Set the mode of",
    "remember_states": "Remember what's on",
    "put_back_states": "Put back what was on",
}
MODE_NAMES = {
    "home": "Home",
    "away": "Away",
    "night": "Night",
    "guests": "Guests",
    "vacation": "Vacation",
}
TODO_VERBS = {"add": "Add", "complete": "Check off", "remove": "Remove"}
MAX_NAMES = 2


def _duration(seconds: float) -> str:
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds} second{'' if seconds == 1 else 's'}"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    hours = round(minutes / 60, 1)
    return f"{hours:g} hour{'' if hours == 1 else 's'}"


def _names(hass: HomeAssistant, target: dict[str, Any] | None) -> str:
    target = target or {}
    names: list[str] = []
    areas = ar.async_get(hass)
    for area_id in target.get("area_id") or []:
        area = areas.async_get_area(area_id)
        names.append(area.name if area else area_id)
    for entity_id in target.get("entity_id") or []:
        state = hass.states.get(entity_id)
        names.append(state.name if state else entity_id)
    if len(names) > MAX_NAMES:
        return f"{', '.join(names[:MAX_NAMES])} and {len(names) - MAX_NAMES} more"
    return " and ".join(names)


def step_label(
    hass: HomeAssistant,
    action: dict[str, Any],
    routine_names: dict[str, str] | None = None,
) -> str:
    """One step, in a few words: "Close Bedroom curtains", "Wait 5 minutes"."""
    if "delay" in action:
        return f"Wait {_duration(action['delay'])}"
    if "todo" in action:
        step = action["todo"]
        return f"{TODO_VERBS.get(step['op'], step['op'])} “{step['item']}”"
    if "together" in action:
        parts = [step_label(hass, sub, routine_names) for sub in action["together"]]
        return f"At the same time: {'; '.join(parts)}"
    if "run_routine" in action:
        other = (routine_names or {}).get(action["run_routine"], action["run_routine"])
        return f"Run {other}"
    if "wait_until" in action:
        wait = action["wait_until"]
        state = hass.states.get(wait["entity_id"])
        name = state.name if state else wait["entity_id"]
        if wait.get("below") is not None or wait.get("above") is not None:
            level = " and ".join(
                f"{word} {wait[key]:g}"
                for key, word in (("below", "under"), ("above", "over"))
                if wait.get(key) is not None
            )
            return f"Wait for {name} to be {level}"
        return f"Wait for {name} to be {' or '.join(wait['states'])}"
    if "wait_cheaper" in action:
        hours = action["wait_cheaper"]["minutes"] / 60
        return f"Wait for the cheapest electricity in the next {hours:g} hours"
    if "check_battery" in action:
        return f"Check batteries under {action['check_battery']['below']}%"
    if "tick_list" in action:
        count = len(action["tick_list"]["items"])
        return (
            f"Wait for {count} {'thing' if count == 1 else 'things'} to be ticked off"
        )
    if "check_open" in action:
        count = len(action["check_open"]["entity_id"])
        return (
            f"Check {count} {'door or window' if count == 1 else 'doors and windows'}"
        )
    if "volume_ramp" in action:
        ramp = action["volume_ramp"]
        names = _names(hass, {"entity_id": ramp["entity_id"]})
        return f"Turn {names} from {ramp['from']}% to {ramp['to']}%"
    if "set_mode" in action:
        return f"Set the house mode to {MODE_NAMES.get(action['set_mode'], action['set_mode'])}"
    if action.get("service") == "scene.apply":
        count = len((action.get("data") or {}).get("entities") or {})
        return f"Set {count} {'device' if count == 1 else 'devices'} as saved"
    domain, _, service = str(action.get("service", "")).partition(".")
    verb = VERBS.get(service) or service.replace("_", " ").capitalize() or domain
    names = _names(hass, action.get("target"))
    return f"{verb} {names}".strip()
