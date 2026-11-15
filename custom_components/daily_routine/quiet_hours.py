"""Quiet hours: routines leave the speakers alone at night.

``settings.quiet_hours`` is {enabled, from, until} ("HH:MM", may cross
midnight). While it's on, steps that make sound (text to speech, playing on
a media player, turning a speaker up, a briefing) are skipped, unless the
routine's ``loud_ok`` says it may use the speakers anyway (an alarm, say).
Turning something down or off is never skipped. Pure apart from the
settings check.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from .schema import vol

# Media player services that make no sound, or less of it.
QUIET_SERVICES = {
    "turn_off",
    "media_pause",
    "media_stop",
    "volume_mute",
    "volume_down",
    "clear_playlist",
    "unjoin",
}
DEFAULT_QUIET = {"enabled": False, "from": "22:00", "until": "07:00"}


def _hhmm(value: Any, label: str) -> str:
    text = str(value or "").strip()[:5]
    try:
        parsed = time.fromisoformat(text)
    except ValueError as err:
        raise vol.Invalid(f"{label}: give a time like 22:00.") from err
    return parsed.strftime("%H:%M")


def clean_quiet(raw: Any) -> dict[str, Any]:
    if not raw:
        return dict(DEFAULT_QUIET)
    if not isinstance(raw, dict):
        raise vol.Invalid("Quiet hours need a start and an end.")
    start = _hhmm(raw.get("from") or DEFAULT_QUIET["from"], "Quiet from")
    end = _hhmm(raw.get("until") or DEFAULT_QUIET["until"], "Quiet until")
    if start == end:
        raise vol.Invalid("Quiet hours must start and end at different times.")
    return {"enabled": bool(raw.get("enabled")), "from": start, "until": end}


def quiet_or_default(raw: Any) -> dict[str, Any]:
    try:
        return clean_quiet(raw)
    except vol.Invalid:
        return dict(DEFAULT_QUIET)


def in_quiet(quiet: dict[str, Any] | None, now: datetime) -> bool:
    if not quiet or not quiet.get("enabled"):
        return False
    clock = now.strftime("%H:%M")
    start, end = quiet["from"], quiet["until"]
    if start < end:
        return start <= clock < end
    return clock >= start or clock < end


def is_loud(action: dict[str, Any]) -> bool:
    """Whether a step makes sound: text to speech, a speaker, a briefing."""
    if "volume_ramp" in action or "briefing" in action:
        return True
    service = str(action.get("service") or "")
    domain, _, name = service.partition(".")
    if domain == "tts":
        return True
    if domain == "media_player":
        if name == "volume_set":
            level = (action.get("data") or {}).get("volume_level")
            return not (isinstance(level, int | float) and level == 0)
        return name not in QUIET_SERVICES
    return False
