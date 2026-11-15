"""Ideas from what people do by hand.

Turning a light, switch, fan or cover on or off from the app, a dashboard,
a voice assistant or the wall is noted (not what automations, scripts or
Daily Routine itself do). When the same thing happens around the same time
on enough days, it's offered as an idea for a routine: "You turn on the
Porch light around 6:40 pm most days".
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
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
# A habit "only when it's dark" or "only when someone's home" when that held
# for this share of the days in it.
CONDITION_SHARE = 0.8
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
    # (entity, on) -> {date: (first minute that day, weekday, dark, someone home)}
    seen: dict[
        tuple[str, bool], dict[str, tuple[int, int, bool | None, bool | None]]
    ] = defaultdict(dict)
    for event in events:
        at = dt_util.parse_datetime(str(event.get("at") or ""))
        if at is None or at < since or at > now:
            continue
        at = dt_util.as_local(at)
        key = (str(event.get("e")), bool(event.get("on")))
        day = at.date().isoformat()
        minute = _minute(at)
        if day not in seen[key] or minute < seen[key][day][0]:
            seen[key][day] = (minute, at.weekday(), event.get("d"), event.get("h"))
    ideas: list[dict[str, Any]] = []
    for (entity_id, on), days in seen.items():
        if (entity_id, on) in covered or len(days) < MIN_DAYS:
            continue
        points = sorted(days.values())
        best: list[tuple[int, int, bool | None, bool | None]] = []
        for i, (start, *_rest) in enumerate(points):
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
        dark = [p[2] for p in best if p[2] is not None]
        home = [p[3] for p in best if p[3] is not None]
        ideas.append(
            {
                "id": idea_id,
                # Seen almost always when it was dark, or someone was home:
                # the routine could check for that too.
                "when_dark": len(dark) == len(best)
                and sum(dark) >= CONDITION_SHARE * len(best),
                "someone_home": len(home) == len(best)
                and sum(home) >= CONDITION_SHARE * len(best),
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


@callback
def _habit_domain(event_data: Mapping[str, Any]) -> bool:
    """A state change worth looking at: a light, switch, fan and so on."""
    return str(event_data["entity_id"]).split(".", 1)[0] in HABIT_DOMAINS


def drop_old(events: list[dict[str, Any]], cutoff: datetime) -> None:
    """Drop events older than ``cutoff``, and the oldest past ``MAX_EVENTS``.

    Events are kept oldest first, so only the front is read: the first one
    recent enough ends the search (the whole list used to be parsed again
    every 50 changes).
    """
    if len(events) > MAX_EVENTS:
        del events[: len(events) - MAX_EVENTS]
    drop = 0
    for event in events:
        at = dt_util.parse_datetime(str(event.get("at")))
        if at is not None and at >= cutoff:
            break
        drop += 1
    if drop:
        del events[:drop]


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
        # The last ideas found, and what they were found from (ideas()).
        self._ideas_key: tuple[Any, ...] | None = None
        self._ideas: list[dict[str, Any]] = []

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.events = [e for e in data.get("events") or [] if isinstance(e, dict)]
        self.dismissed = {str(d) for d in data.get("dismissed") or []}

    @callback
    def start(self) -> None:
        if self._unsub is None:
            # Only changes to lights, switches and the like reach _on_change:
            # Home Assistant skips the rest before calling into it.
            self._unsub = self.hass.bus.async_listen(
                EVENT_STATE_CHANGED, self._on_change, event_filter=_habit_domain
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
        sun = self.hass.states.get("sun.sun")
        people = self.hass.states.async_all("person")
        noted: dict[str, Any] = {"e": entity_id, "on": on, "at": now.isoformat()}
        if sun is not None and sun.state in ("above_horizon", "below_horizon"):
            noted["d"] = sun.state == "below_horizon"
        if people:
            noted["h"] = any(p.state == "home" for p in people)
        self.events.append(noted)
        drop_old(self.events, now - timedelta(days=KEEP_DAYS))
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
        # Ideas change only with a new habit event, a dismissal, a routine
        # change (what's covered) or a new day: until then, the last ones.
        last = self.events[-1]["at"] if self.events else None
        key = (
            len(self.events),
            last,
            frozenset(self.dismissed),
            self._coordinator.revision,
            now.date(),
        )
        if key != self._ideas_key:
            self._ideas = suggest(
                self.events, now, covered=self.covered(), dismissed=self.dismissed
            )
            self._ideas_key = key
        found = [dict(idea) for idea in self._ideas]
        for idea in found:
            state = self.hass.states.get(idea["entity_id"])
            idea["name"] = state.name if state else idea["entity_id"]
            idea["service"] = service_for(idea["entity_id"], idea["on"])
        return [i for i in found if self.hass.states.get(i["entity_id"])]

    async def async_dismiss(self, idea_id: str) -> None:
        self.dismissed.add(idea_id)
        await self._store.async_save(self._data())
