"""Going on vacation in one tap, and the daily summary of what ran.

Vacation (``settings["vacation"]``): the house goes to Vacation mode, the
picked routines are paused through the last day, lived-in lights turn on,
and (if asked) the daily summary is sent while away. When it ends (the day
after the last day, or **Back home**) everything is put back as it was:
the mode (if it's still Vacation), lived-in lights, the summary, and the
routines that are still paused through that day.

Daily summary (``settings["daily_summary"]``: enabled, time, only_away): at
that time each day, a notification of today's runs: how many ran, failed
and were skipped, naming the ones that failed or were skipped and why.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
import re
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .schema import vol

if TYPE_CHECKING:
    from .coordinator import HomeRoutinesCoordinator

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SUMMARY_DEFAULT = {"enabled": False, "time": "21:00", "only_away": False}
AWAY_MODES = ("away", "vacation")
MAX_LISTED = 8


def clean_summary(raw: Any) -> dict[str, Any]:
    """The daily summary's settings."""
    if raw is None:
        return dict(SUMMARY_DEFAULT)
    if not isinstance(raw, dict):
        raise vol.Invalid("The daily summary settings must be an object.")
    time = str(raw.get("time") or "21:00")[:5]
    if not _TIME.match(time):
        raise vol.Invalid("Send the daily summary at a time like 21:00.")
    return {
        "enabled": bool(raw.get("enabled")),
        "time": time,
        "only_away": bool(raw.get("only_away")),
    }


def summary_or_default(raw: Any) -> dict[str, Any]:
    try:
        return clean_summary(raw)
    except vol.Invalid:
        return dict(SUMMARY_DEFAULT)


def vacation_or_none(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw.get("until"):
        return None
    return raw


def summary_text(
    events: list[dict[str, Any]], day: date, reason_text: Callable[[str], str]
) -> str:
    """Today's runs in a few lines."""
    today = [
        e
        for e in events
        if dt_util.as_local(datetime.fromisoformat(e["at"])).date() == day
    ]
    ran = [e for e in today if e["outcome"] == "ran"]
    failed = [e for e in today if e["outcome"] == "failed"]
    skipped = [e for e in today if e["outcome"] == "skipped"]
    if not today:
        return "Nothing ran today."
    lines = [
        f"Today: {len(ran)} ran, {len(failed)} failed, {len(skipped)} skipped."
        if failed or skipped
        else f"Today: all {len(ran)} ran. 👍"
    ]
    for label, rows in (("Failed", failed), ("Skipped", skipped)):
        if rows:
            listed = [
                f"{e['name']}"
                + (
                    f" ({reason_text(e.get('reason') or '')})"
                    if e.get("reason")
                    else ""
                )
                for e in rows[:MAX_LISTED]
            ]
            more = (
                f" and {len(rows) - MAX_LISTED} more" if len(rows) > MAX_LISTED else ""
            )
            lines.append(f"{label}: " + "; ".join(listed) + more)
    return "\n".join(lines)


class AwayHelper:
    """Vacation mode and the daily summary for the coordinator."""

    def __init__(self, coordinator: HomeRoutinesCoordinator) -> None:
        self.coord = coordinator
        self._summary_unsub: Callable[[], None] | None = None
        self._check_unsub: Callable[[], None] | None = None

    # ----- the daily summary -----

    @callback
    def start(self) -> None:
        self.stop()
        settings = summary_or_default(self.coord.settings.get("daily_summary"))
        if settings["enabled"]:
            hour, minute = (int(x) for x in settings["time"].split(":"))
            self._summary_unsub = async_track_time_change(
                self.coord.hass, self._send_summary, hour=hour, minute=minute, second=0
            )
        # Once a day, just after midnight: has the vacation ended?
        self._check_unsub = async_track_time_change(
            self.coord.hass, self._check_vacation, hour=0, minute=0, second=30
        )

    @callback
    def stop(self) -> None:
        for unsub in (self._summary_unsub, self._check_unsub):
            if unsub is not None:
                unsub()
        self._summary_unsub = self._check_unsub = None

    @callback
    def _send_summary(self, now: datetime) -> None:
        settings = summary_or_default(self.coord.settings.get("daily_summary"))
        if settings["only_away"] and self.coord.mode not in AWAY_MODES:
            return
        self.coord.send_summary(now)

    def summary(self, now: datetime) -> str:
        return summary_text(
            self.coord.run_log.events(),
            dt_util.as_local(now).date(),
            self.coord.reason_text,
        )

    # ----- vacation -----

    @callback
    def _check_vacation(self, now: datetime) -> None:
        vacation = self.coord.settings.get("vacation")
        if vacation and dt_util.as_local(now).date().isoformat() > vacation["until"]:
            self.coord.spawn(self.async_end())

    async def async_start(
        self,
        until: str,
        routine_ids: list[str],
        *,
        lived_in: bool,
        summary: bool,
    ) -> dict[str, Any]:
        coord = self.coord
        try:
            last = date.fromisoformat(str(until))
        except ValueError as err:
            raise vol.Invalid("Pick the last day you're away.") from err
        if last < dt_util.now().date():
            raise vol.Invalid("Pick a day that hasn't passed.")
        current = coord.settings.get("vacation")
        vacation = {
            "until": last.isoformat(),
            "routines": list(dict.fromkeys(routine_ids)),
            # What to put back afterwards (kept from the first start when
            # the dates are changed while away).
            "mode_was": current["mode_was"] if current else coord.mode,
            "lived_in_was": (
                current["lived_in_was"]
                if current
                else coord.settings["lived_in"]["enabled"]
            ),
            "summary_was": (
                current["summary_was"]
                if current
                else summary_or_default(coord.settings.get("daily_summary"))
            ),
        }
        if routine_ids:
            await coord.async_pause_until(routine_ids, last.isoformat())
        coord.settings["vacation"] = vacation
        if lived_in:
            coord.settings["lived_in"] = {**coord.settings["lived_in"], "enabled": True}
        if summary:
            coord.settings["daily_summary"] = {
                **summary_or_default(coord.settings.get("daily_summary")),
                "enabled": True,
                "only_away": True,
            }
        await coord.async_set_mode("vacation")
        self.start()
        await coord.async_settings_changed()
        return vacation

    async def async_end(self) -> None:
        coord = self.coord
        vacation = coord.settings.get("vacation")
        if not vacation:
            return
        coord.settings["vacation"] = None
        coord.settings["lived_in"] = {
            **coord.settings["lived_in"],
            "enabled": bool(vacation.get("lived_in_was")),
        }
        coord.settings["daily_summary"] = summary_or_default(
            vacation.get("summary_was")
        )
        still = [
            r["id"]
            for r in coord.routines
            if r["id"] in vacation.get("routines", [])
            and r.get("paused_until") == vacation["until"]
        ]
        if still:
            await coord.async_pause_until(still, None)
        if coord.mode == "vacation":
            await coord.async_set_mode(vacation.get("mode_was") or "home")
        self.start()
        await coord.async_settings_changed()
