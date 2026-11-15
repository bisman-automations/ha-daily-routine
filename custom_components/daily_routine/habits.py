"""Ideas from what people do by hand.

Turning a light, switch, fan or cover on or off from the app, a dashboard,
a voice assistant or the wall is noted (not what automations, scripts or
Daily Routine itself do). When the same thing happens around the same time
on enough days, it's offered as an idea for a routine: "You turn on the
Porch light around 6:40 pm most days".
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, EventStateChangedData, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, WEEKDAYS

if TYPE_CHECKING:
    from .coordinator import HomeRoutinesCoordinator

HABIT_DOMAINS = ("light", "switch", "fan", "cover")
# What counts as "on" for each domain.
ON_STATES = {"on", "open"}
OFF_STATES = {"off", "closed"}
KEEP_DAYS = 21
LOOK_BACK_DAYS = 14
MAX_EVENTS = 5000
MIN_DAYS = 5
WINDOW_MINUTES = 40
MAX_IDEAS = 5
STORAGE_VERSION = 1


def _minute(at: datetime) -> int:
    return at.hour * 60 + at.minute


def suggest(
    events: list[dict[str, Any]],
    now: datetime,
    *,
    covered: set[tuple[str, bool]] | None = None,
    dismissed: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Habits worth a routine: the same change around the same time on
    MIN_DAYS or more of the last LOOK_BACK_DAYS days.

    Each event is {"e": entity_id, "on": bool, "at": iso local time}.
    ``covered`` holds (entity, on) pairs a routine already does; ``dismissed``
    holds ids of ideas someone said no to.
    """
    covered = covered or set()
    dismissed = dismissed or set()
    since = now - timedelta(days=LOOK_BACK_DAYS)
    # (entity, on) -> {date: first minute that day}
    seen: dict[tuple[str, bool], dict[str, tuple[int, int]]] = defaultdict(dict)
    for event in events:
        at = dt_util.parse_datetime(str(event.get("at") or ""))
        if at is None or at < since or at > now:
            continue
        at = dt_util.as_local(at)
        key = (str(event.get("e")), bool(event.get("on")))
        day = at.date().isoformat()
        minute = _minute(at)
        if day not in seen[key] or minute < seen[key][day][0]:
            seen[key][day] = (minute, at.weekday())
    ideas: list[dict[str, Any]] = []
    for (entity_id, on), days in seen.items():
        if (entity_id, on) in covered or len(days) < MIN_DAYS:
            continue
        points = sorted(days.values())
        best: list[tuple[int, int]] = []
        for i, (start, _wd) in enumerate(points):
            window = [p for p in points[i:] if p[0] - start <= WINDOW_MINUTES]
            if len(window) > len(best):
                best = window
        if len(best) < MIN_DAYS:
            continue
        minutes = sorted(p[0] for p in best)
        middle = minutes[len(minutes) // 2]
        middle = min(round(middle / 5) * 5, 23 * 60 + 55)
        time = f"{middle // 60:02d}:{middle % 60:02d}:00"
        weekdays = {p[1] for p in best}
        days_list = (
            list(WEEKDAYS[:5])
            if weekdays <= set(range(5)) and len(weekdays) >= 3
            else list(WEEKDAYS)
        )
        idea_id = f"{entity_id}:{'on' if on else 'off'}:{time[:5]}"
        if idea_id in dismissed:
            continue
        ideas.append(
            {
                "id": idea_id,
                "entity_id": entity_id,
                "on": on,
                "time": time,
                "days": days_list,
                "count": len(best),
            }
        )
    ideas.sort(key=lambda i: (-int(i["count"]), str(i["time"])))
    return ideas[:MAX_IDEAS]


def service_for(entity_id: str, on: bool) -> str:
    domain = entity_id.split(".", 1)[0]
    if domain == "cover":
        return "cover.open_cover" if on else "cover.close_cover"
    return f"{domain}.turn_{'on' if on else 'off'}"


class Habits:
    """Notes changes made by hand, and turns repeated ones into ideas."""

    def __init__(self, coordinator: HomeRoutinesCoordinator) -> None:
        self._coordinator = coordinator
        self.hass = coordinator.hass
        self._store: Store[dict[str, Any]] = Store(
            self.hass, STORAGE_VERSION, f"{DOMAIN}.habits"
        )
        self.events: list[dict[str, Any]] = []
        self.dismissed: set[str] = set()
        self._unsub: Any = None
        self._dirty = False

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.events = [e for e in data.get("events") or [] if isinstance(e, dict)]
        self.dismissed = {str(d) for d in data.get("dismissed") or []}

    @callback
    def start(self) -> None:
        if self._unsub is None:
            self._unsub = self.hass.bus.async_listen(
                EVENT_STATE_CHANGED, self._on_change
            )

    @callback
    def stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._dirty:
            self._store.async_delay_save(self._data, 0)

    def _data(self) -> dict[str, Any]:
        return {"events": self.events, "dismissed": sorted(self.dismissed)}

    def _by_hand(self, event: Event[EventStateChangedData]) -> bool:
        """A person did it: from a login, or a device's own button (not an
        automation, a script, or one of our routines while it runs)."""
        context = event.context
        if context.user_id is not None:
            return True
        if context.parent_id is not None:
            return False
        return not self._coordinator.busy()

    @callback
    def _on_change(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        if entity_id.split(".", 1)[0] not in HABIT_DOMAINS:
            return
        old, new = event.data["old_state"], event.data["new_state"]
        if old is None or new is None or old.state == new.state:
            return
        if new.state in ON_STATES and old.state in OFF_STATES:
            on = True
        elif new.state in OFF_STATES and old.state in ON_STATES:
            on = False
        else:
            return
        if not self._by_hand(event):
            return
        now = dt_util.now()
        self.events.append({"e": entity_id, "on": on, "at": now.isoformat()})
        cutoff = now - timedelta(days=KEEP_DAYS)
        if len(self.events) > MAX_EVENTS or len(self.events) % 50 == 0:
            self.events = [
                e
                for e in self.events[-MAX_EVENTS:]
                if (at := dt_util.parse_datetime(e["at"])) and at >= cutoff
            ]
        self._dirty = True
        self._store.async_delay_save(self._data, 60)

    def covered(self) -> set[tuple[str, bool]]:
        """What routines already do, so a habit they cover isn't offered."""
        done: set[tuple[str, bool]] = set()
        for routine in self._coordinator.routines:
            if not routine["enabled"]:
                continue
            for action in routine["actions"]:
                for step in action.get("together", [action]):
                    service = step.get("service") or ""
                    domain, _, name = service.partition(".")
                    on = name in ("turn_on", "open_cover")
                    if not (on or name in ("turn_off", "close_cover")):
                        continue
                    for entity_id in self._coordinator.resolve_targets(
                        step.get("target"), domain
                    ):
                        done.add((entity_id, on))
        return done

    def ideas(self, now: datetime) -> list[dict[str, Any]]:
        found = suggest(
            self.events, now, covered=self.covered(), dismissed=self.dismissed
        )
        for idea in found:
            state = self.hass.states.get(idea["entity_id"])
            idea["name"] = state.name if state else idea["entity_id"]
            idea["service"] = service_for(idea["entity_id"], idea["on"])
        return [i for i in found if self.hass.states.get(i["entity_id"])]

    async def async_dismiss(self, idea_id: str) -> None:
        self.dismissed.add(idea_id)
        await self._store.async_save(self._data())
