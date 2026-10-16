"""Config flow: adds the single Daily Routine entry. Routines are managed in the sidebar."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult

from .const import DOMAIN


class DailyRoutineConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up Daily Routine (single instance)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance")
        if user_input is not None:
            return self.async_create_entry(title="Daily Routine", data={})
        return self.async_show_form(step_id="user")
