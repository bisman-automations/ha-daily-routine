"""Lived-in lights: while the house is in Away or Vacation mode, chosen lights
turn on and off at random in the evening, so the house looks lived in."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time as dt_time, timedelta
import logging
import random
import re
from typing import TYPE_CHECKING, Any

from homeassistant.const import SUN_EVENT_SUNSET
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from .routines import MODES
from .schema import vol

if TYPE_CHECKING:
    from .coordinator import HomeRoutinesCoordinator

_LOGGER = logging.getLogger(__name__)

TICK = timedelta(minutes=1)
# A light stays as it is for a random time in this range (minutes).
MIN_GAP = 8
MAX_GAP = 25
MAX_LIGHTS = 30
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

DEFAULT = {
    "enabled": False,
    "lights": [],
    "start": "sunset",
    "end": "23:00",
    "modes": ["away", "vacation"],
}


def clean_settings(raw: Any) -> dict[str, Any]:
    """Validate the lived-in settings from the Settings tab."""
    if raw is None:
        return dict(DEFAULT)
    if not isinstance(raw, dict):
        raise vol.Invalid("Lived-in lights settings must be an object.")
    lights = raw.get("lights") or []
    if not isinstance(lights, list) or not all(
        isinstance(e, str) and e.split(".", 1)[0] in ("light", "switch") for e in lights
    ):
        raise vol.Invalid("Pick lights or switches for lived-in lights.")
    if len(lights) > MAX_LIGHTS:
        raise vol.Invalid(f"Pick at most {MAX_LIGHTS} lights for lived-in lights.")
    start = str(raw.get("start") or "sunset")
    end = str(raw.get("end") or "23:00")
    if start != "sunset" and not _TIME.match(start):
        raise vol.Invalid("Lived-in lights start at sunset or at a time like 18:30.")
    if not _TIME.match(end):
        raise vol.Invalid("Lived-in lights end at a time like 23:00.")
    modes = [m for m in MODES if m in (raw.get("modes") or [])]
    if not modes:
        raise vol.Invalid("Pick the house modes for lived-in lights.")
    return {
        "enabled": bool(raw.get("enabled")),
        "lights": list(dict.fromkeys(lights)),
        "start": start,
        "end": end,
        "modes": modes,
    }


def _at(day: datetime, text: str) -> datetime:
    hour, minute = (int(p) for p in text.split(":"))
    return datetime.combine(day.date(), dt_time(hour, minute), tzinfo=day.tzinfo)


class LivedIn:
    """Turns the chosen lights on and off at random while it's active."""

    def __init__(self, coordinator: HomeRoutinesCoordinator) -> None:
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self._unsub: Callable[[], None] | None = None
        self._next: datetime | None = None
        # Lights this turned on, so only those are turned off again.
        self.ours: set[str] = set()
        self.random = random.Random()

    @property
    def settings(self) -> dict[str, Any]:
        return self.coordinator.settings.get("lived_in") or DEFAULT

    def start(self) -> None:
        self.stop()
        self._unsub = async_track_time_interval(self.hass, self._tick, TICK)

    def stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def window(self, now: datetime) -> tuple[datetime, datetime] | None:
        """Today's evening window (or last night's, just after midnight)."""
        settings = self.settings
        for day in (now, now - timedelta(days=1)):
            if settings["start"] == "sunset":
                sunset = get_astral_event_date(self.hass, SUN_EVENT_SUNSET, day.date())
                if sunset is None:
                    continue
                begin = dt_util.as_local(sunset)
            else:
                begin = _at(day, settings["start"])
            end = _at(day, settings["end"])
            if end <= begin:
                end += timedelta(days=1)
            if begin <= now < end:
                return begin, end
        return None

    def active(self, now: datetime) -> bool:
        settings = self.settings
        return bool(
            settings["enabled"]
            and settings["lights"]
            and self.coordinator.mode in settings["modes"]
            and self.window(now) is not None
        )

    @callback
    def _tick(self, now: datetime) -> None:
        self.coordinator.spawn(self.async_step(dt_util.as_local(now)))

    async def async_step(self, now: datetime) -> None:
        """Change one light when it's time, or turn them all off when it's over."""
        if not self.active(now):
            self._next = None
            if self.ours:
                await self._switch(sorted(self.ours), on=False)
            return
        if self._next is not None and now < self._next:
            return
        lights = [e for e in self.settings["lights"] if self.hass.states.get(e)]
        if not lights:
            return
        off = [e for e in lights if e not in self.ours]
        # Keep about half of them on, changing one at a time.
        turn_on = bool(off) and (
            not self.ours or len(self.ours) < max(1, len(lights) // 2)
        )
        if turn_on and self.ours and self.random.random() < 0.3:
            turn_on = False
        if turn_on:
            await self._switch([self.random.choice(off)], on=True)
        elif self.ours:
            await self._switch([self.random.choice(sorted(self.ours))], on=False)
        self._next = now + timedelta(minutes=self.random.randint(MIN_GAP, MAX_GAP))

    async def _switch(self, entities: list[str], *, on: bool) -> None:
        for entity_id in entities:
            domain = entity_id.split(".", 1)[0]
            error = await self.coordinator.call_service(
                domain, "turn_on" if on else "turn_off", {}, {"entity_id": [entity_id]}
            )
            if error is not None:
                _LOGGER.debug("Lived-in lights: %s didn't change: %s", entity_id, error)
                continue
            if on:
                self.ours.add(entity_id)
            else:
                self.ours.discard(entity_id)
        self.coordinator.notify_listeners()

    def status(self, now: datetime) -> dict[str, Any]:
        return {"active": self.active(now), "on": sorted(self.ours)}
