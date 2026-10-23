"""Config flow: adds the single Daily Routine entry, and lets each routine be edited in Home Assistant."""

from __future__ import annotations

import json
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN, ROUTINE_SUBENTRY, TRIGGERS
from .schema import vol

TRIGGER_NAMES = {
    "time": "At a set time",
    "sunrise": "At sunrise",
    "sunset": "At sunset",
    "everyone_leaves": "When everyone leaves",
    "someone_arrives": "When someone arrives",
    "room_clear": "When the room has been quiet",
    "value_above": "When a sensor goes above a level",
    "value_below": "When a sensor drops below a level",
    "manual": "Only when run manually",
}


class DailyRoutineConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up Daily Routine (single instance)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance")
        if user_input is not None:
            return self.async_create_entry(title="Daily Routine", data={})
        return self.async_show_form(step_id="user")

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {ROUTINE_SUBENTRY: RoutineSubentryFlow}


def _hhmmss(value: str) -> str:
    value = value.strip()
    return value if value.count(":") == 2 else f"{value}:00"


class RoutineSubentryFlow(ConfigSubentryFlow):
    """Edit one routine from Home Assistant. New routines are made in the sidebar."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_abort(reason="use_sidebar")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry: ConfigSubentry = self._get_reconfigure_subentry()
        coordinator = self.hass.data[DOMAIN]["coordinator"]
        routine = coordinator.get_routine(subentry.unique_id or "")
        if routine is None:
            return self.async_abort(reason="routine_missing")

        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            try:
                actions = json.loads(user_input["actions"])
            except ValueError:
                errors["actions"] = "invalid_json"
            else:
                raw = {
                    **routine,
                    "name": user_input["name"].strip() or routine["name"],
                    "enabled": bool(user_input["enabled"]),
                    "trigger": user_input["trigger"],
                    "time": _hhmmss(user_input["time"]),
                    "zone": user_input["zone"],
                    "actions": actions,
                }
                try:
                    await coordinator.async_save_routine(raw)
                except vol.Invalid as err:
                    errors["base"] = "invalid_routine"
                    placeholders["reason"] = str(err)
                else:
                    return self.async_abort(reason="updated")

        values = user_input or {}
        schema = vol.Schema(
            {
                vol.Required("name", default=values.get("name", routine["name"])): str,
                vol.Required(
                    "enabled", default=values.get("enabled", bool(routine["enabled"]))
                ): bool,
                vol.Required(
                    "trigger", default=values.get("trigger", routine["trigger"])
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=t, label=TRIGGER_NAMES.get(t, t))
                            for t in TRIGGERS
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required("time", default=values.get("time", routine["time"][:5])): str,
                vol.Required("zone", default=values.get("zone", routine["zone"])): str,
                vol.Required(
                    "actions",
                    default=values.get("actions", json.dumps(routine["actions"], indent=2)),
                ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
            description_placeholders={"name": routine["name"], **placeholders},
        )
