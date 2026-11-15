"""The guest Wi-Fi card: the network name and password for visitors.

``settings.guest_wifi`` is {ssid, password}. While the house mode is
Guests, the Today tab and the dashboard card show it, so a guest can read
it off the wall tablet. Nothing is shown in other modes.
"""

from __future__ import annotations

from typing import Any

from .schema import vol

MAX_SSID = 32
MAX_PASSWORD = 63


def clean_guest_wifi(raw: Any) -> dict[str, str] | None:
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise vol.Invalid("Guest Wi-Fi needs a network name.")
    ssid = str(raw.get("ssid") or "").strip()
    password = str(raw.get("password") or "")
    if not ssid and not password:
        return None
    if not ssid or len(ssid) > MAX_SSID:
        raise vol.Invalid(f"The Wi-Fi name is 1 to {MAX_SSID} characters.")
    if len(password) > MAX_PASSWORD:
        raise vol.Invalid(f"The Wi-Fi password is at most {MAX_PASSWORD} characters.")
    return {"ssid": ssid, "password": password}


def guest_wifi_or_none(raw: Any) -> dict[str, str] | None:
    try:
        return clean_guest_wifi(raw)
    except vol.Invalid:
        return None


def guest_card(settings: dict[str, Any], mode: str) -> dict[str, str] | None:
    """What the card shows: the guest network while the house is in Guests."""
    wifi = settings.get("guest_wifi")
    return wifi if wifi and mode == "guests" else None
