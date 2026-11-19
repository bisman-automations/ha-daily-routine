"""Which devices give trouble: per device and day, how often a step reached it,
had to send the command again, or couldn't change it. Kept for 30 days."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import storage
from homeassistant.util import dt as dt_util

from .const import DOMAIN

STORE_KEY = f"{DOMAIN}.devices"
STORE_VERSION = 1
KEEP_DAYS = 30
SAVE_DELAY_SECONDS = 30
OUTCOMES = ("ok", "retried", "failed")


class DeviceStats:
    """Counts per device: {entity_id: {day: {ok, retried, failed}}}, plus the
    last problem's time and reason."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: storage.Store[dict[str, Any]] = storage.Store(
            hass, STORE_VERSION, STORE_KEY
        )
        self.days: dict[str, dict[str, dict[str, int]]] = {}
        self.last_problem: dict[str, dict[str, str]] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        days = data.get("days")
        self.days = days if isinstance(days, dict) else {}
        last = data.get("last_problem")
        self.last_problem = last if isinstance(last, dict) else {}
        self._prune(dt_util.now())

    def record(
        self,
        entity_id: str,
        outcome: str,
        reason: str = "",
        now: datetime | None = None,
    ) -> None:
        if outcome not in OUTCOMES:
            return
        now = now or dt_util.now()
        day = dt_util.as_local(now).date().isoformat()
        counts = self.days.setdefault(entity_id, {}).setdefault(
            day, dict.fromkeys(OUTCOMES, 0)
        )
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome != "ok":
            self.last_problem[entity_id] = {
                "at": now.isoformat(),
                "outcome": outcome,
                "reason": reason,
            }
        self._prune(now)
        self._store.async_delay_save(self._payload, SAVE_DELAY_SECONDS)

    def _prune(self, now: datetime) -> None:
        oldest = (dt_util.as_local(now) - timedelta(days=KEEP_DAYS)).date().isoformat()
        for entity_id in list(self.days):
            kept = {d: c for d, c in self.days[entity_id].items() if d >= oldest}
            if kept:
                self.days[entity_id] = kept
            else:
                del self.days[entity_id]
                self.last_problem.pop(entity_id, None)

    def _payload(self) -> dict[str, Any]:
        return {"days": self.days, "last_problem": self.last_problem}

    async def async_flush(self) -> None:
        """Write now what's waiting for a delayed save (unloading).

        The next coordinator (after a reload) reads the file, not this one's
        memory, so anything not yet written would be lost (1.34.1).
        """
        await self._store.async_save(self._payload())

    def summary(self, days: int, now: datetime | None = None) -> list[dict[str, Any]]:
        """Devices with a problem in the last days, the most trouble first."""
        now = now or dt_util.now()
        oldest = (dt_util.as_local(now) - timedelta(days=days)).date().isoformat()
        rows = []
        for entity_id, by_day in self.days.items():
            total = dict.fromkeys(OUTCOMES, 0)
            for day, counts in by_day.items():
                if day >= oldest:
                    for key in OUTCOMES:
                        total[key] += counts.get(key, 0)
            if not total["failed"] and not total["retried"]:
                continue
            state = self.hass.states.get(entity_id)
            reached = sum(total.values())
            rows.append(
                {
                    "entity_id": entity_id,
                    "name": state.name if state else entity_id,
                    **total,
                    "success_rate": round(100 * (reached - total["failed"]) / reached)
                    if reached
                    else None,
                    "last_problem": self.last_problem.get(entity_id),
                }
            )
        return sorted(rows, key=lambda r: (-r["failed"], -r["retried"], r["name"]))

    def score(
        self, entity_ids: list[str], days: int = 30, now: datetime | None = None
    ) -> dict[str, Any] | None:
        """How reliably these devices answered lately: a percent (ok first
        time, retries count half) and the ones with the most trouble."""
        now = now or dt_util.now()
        oldest = (dt_util.as_local(now) - timedelta(days=days)).date().isoformat()
        good = 0.0
        reached = 0
        trouble: list[dict[str, Any]] = []
        for entity_id in dict.fromkeys(entity_ids):
            total = dict.fromkeys(OUTCOMES, 0)
            for day, counts in self.days.get(entity_id, {}).items():
                if day >= oldest:
                    for key in OUTCOMES:
                        total[key] += counts.get(key, 0)
            count = sum(total.values())
            if not count:
                continue
            reached += count
            good += total.get("ok", 0) + total["retried"] / 2
            if total["failed"] or total["retried"]:
                state = self.hass.states.get(entity_id)
                trouble.append(
                    {
                        "name": state.name if state else entity_id,
                        "failed": total["failed"],
                        "retried": total["retried"],
                    }
                )
        if not reached:
            return None
        trouble.sort(
            key=lambda t: (-int(t["failed"]), -int(t["retried"]), str(t["name"]))
        )
        return {"score": round(100 * good / reached), "worst": trouble[:3]}
