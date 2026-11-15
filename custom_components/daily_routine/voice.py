"""Daily Routine through Assist (a voice speaker, or typing to Assist).

Registered as conversation triggers while Daily Routine is loaded (only when
Home Assistant's conversation integration is):

- "Run bedtime", "Start the morning curtains routine"
- "Skip the morning curtains", "Don't skip bedtime"
- "Set the house to night", "Switch the house mode to away"
- "What routines are left today?", "What's the next routine?"

Routines are found by name (``find_routine``) and the replies are plain
functions, so they're tested without a voice pipeline. Only people allowed
to use Daily Routine (admins and the people an admin picked) can run, skip
or change anything.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import difflib
import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.core import Context
from homeassistant.util import dt as dt_util

from .access import async_get_access
from .progress import MODE_NAMES
from .routines import MODES
from .timeline import today

if TYPE_CHECKING:
    from .coordinator import HomeRoutinesCoordinator

_LOGGER = logging.getLogger(__name__)

SENTENCES = {
    "run": [
        "(run|start) [the] {routine} [routine]",
        "(run|start) [the] routine {routine}",
    ],
    "skip": ["skip [the] [next] {routine} [routine]"],
    "unskip": ["(don't|do not) skip [the] {routine} [routine]"],
    "mode": [
        "(set|switch|change|put) [the] house [mode] (to|in|into) {mode} [mode]",
        "house mode {mode}",
    ],
    "left": [
        "what routines are (left|planned|coming up) [today]",
        "what's left [to run] today",
        "(what's|what is) the next routine",
        "when is the next routine",
    ],
}
NOT_ALLOWED = "You don't have access to Daily Routine."


def _clean(text: str | None) -> str:
    text = re.sub(r"[^\w\s']", " ", (text or "").casefold())
    return re.sub(r"\s+", " ", text).strip()


def find_routine(
    routines: Iterable[dict[str, Any]], said: str | None
) -> dict[str, Any] | None:
    """The routine a spoken name means: same words, one inside the other, or close."""
    said = re.sub(r"^(the|my|our) ", "", _clean(said))
    said = re.sub(r" routine$", "", said)
    if not said:
        return None
    by_name = {
        _clean(r["name"]): r
        for r in routines
        if r.get("kind") != "todo" and not r.get("companion_of")
    }
    if said in by_name:
        return by_name[said]
    inside = [r for name, r in by_name.items() if said in name or name in said]
    if len(inside) == 1:
        return inside[0]
    close = difflib.get_close_matches(said, list(by_name), n=1, cutoff=0.6)
    return by_name[close[0]] if close else None


def find_mode(said: str | None) -> str | None:
    said = _clean(said)
    for mode in MODES:
        if said in (mode, MODE_NAMES[mode].casefold()) or said.startswith(mode):
            return mode
    return {"holiday": "vacation", "guest": "guests", "home": "home"}.get(said)


def _time(iso: str) -> str:
    at = dt_util.parse_datetime(iso)
    if at is None:
        return iso
    at = dt_util.as_local(at)
    hour = at.hour % 12 or 12
    return f"{hour}:{at.minute:02d} {'AM' if at.hour < 12 else 'PM'}"


class Voice:
    """Registers the sentences and answers them."""

    def __init__(self, coordinator: HomeRoutinesCoordinator) -> None:
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self._remove: Callable[[], None] | None = None

    async def async_start(self) -> None:
        if "conversation" not in self.hass.config.components or self._remove:
            return
        from homeassistant.helpers.script import ScriptRunResult  # noqa: PLC0415
        from homeassistant.helpers.trigger import (  # noqa: PLC0415
            async_initialize_triggers,
            async_validate_trigger_config,
        )

        removers = []
        for kind, sentences in SENTENCES.items():

            async def action(
                run_variables: dict[str, Any], context: Any = None, kind: str = kind
            ) -> ScriptRunResult:
                trigger = run_variables.get("trigger") or {}
                user_input = trigger.get("user_input") or {}
                user_id = (user_input.get("context") or {}).get("user_id")
                try:
                    text = await self.async_answer(
                        kind, trigger.get("slots") or {}, user_id
                    )
                except Exception as err:  # noqa: BLE001 - say it, don't crash the pipeline
                    text = str(err) or "Something went wrong."
                return ScriptRunResult(
                    conversation_response=text, service_response=None, variables={}
                )

            try:
                config = await async_validate_trigger_config(
                    self.hass, [{"platform": "conversation", "command": sentences}]
                )
                remove = await async_initialize_triggers(
                    self.hass,
                    config,
                    action,
                    "daily_routine",
                    f"Daily Routine voice ({kind})",
                    _LOGGER.log,
                )
            except Exception:  # noqa: BLE001 - voice is extra; the rest works without it
                _LOGGER.warning(
                    "Daily Routine: couldn't add the Assist sentences", exc_info=True
                )
                continue
            if remove:
                removers.append(remove)

        def _remove_all() -> None:
            for remove in removers:
                remove()

        self._remove = _remove_all

    def stop(self) -> None:
        if self._remove:
            self._remove()
            self._remove = None

    async def async_answer(
        self, kind: str, slots: dict[str, Any], user_id: str | None
    ) -> str:
        coordinator = self.coordinator
        access = await async_get_access(self.hass)
        if not await access.async_allows_context(Context(user_id=user_id)):
            return NOT_ALLOWED
        if kind == "left":
            return self._left()
        if kind == "mode":
            mode = find_mode(slots.get("mode"))
            if mode is None:
                names = ", ".join(MODE_NAMES[m] for m in MODES)
                return f"The house modes are {names}."
            await coordinator.async_set_mode(mode)
            return f"The house is in {MODE_NAMES[mode]} mode."
        routine = find_routine(coordinator.routines, slots.get("routine"))
        if routine is None:
            return f"I couldn't find a routine called {slots.get('routine')}."
        name = routine["name"]
        if kind == "skip":
            await coordinator.async_skip_next(routine["id"], True)
            return f"OK, the next {name} run will be skipped."
        if kind == "unskip":
            await coordinator.async_skip_next(routine["id"], False)
            return f"OK, {name} will run as usual."
        result = await coordinator.async_start_run(routine["id"])
        if result["started"]:
            return f"Starting {name}."
        if result["reason"] == "already_running":
            return f"{name} is already running."
        if result["reason"] == "disabled":
            return f"{name} is turned off."
        return f"{name} can't run right now: its conditions aren't met."

    def _left(self) -> str:
        planned = today(self.coordinator, dt_util.now())["planned"]
        if not planned:
            return "Nothing else is planned today."
        first = planned[0]
        text = f"Next is {first['name']} at {_time(first['at'])}"
        if len(planned) > 1:
            more = len(planned) - 1
            text += f", then {more} more today"
        return text + "."
