"""Config flow: adds the single Daily Routine entry, and lets each routine be edited in Home Assistant."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector

from .const import DOMAIN, ROUTINE_SUBENTRY, TRIGGERS, WEEKDAYS
from .schema import vol
from .text_format import conditions_to_text, parse_conditions_text, parse_steps_text, steps_to_text

TRIGGER_NAMES = {
    "time": "At a set time",
    "sunrise": "At sunrise",
    "sunset": "At sunset",
    "everyone_leaves": "When everyone leaves",
    "someone_arrives": "When someone arrives",
    "person_leaves": "When a person leaves a place",
    "person_arrives": "When a person arrives at a place",
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
    value = str(value).strip()
    return value if value.count(":") == 2 else f"{value}:00"


CONDITION_MATCH_OPTIONS = [
    selector.SelectOptionDict(value="all", label="All of these are true"),
    selector.SelectOptionDict(value="any", label="Any one of these is true"),
]


def _form_from_routine(routine: dict[str, Any]) -> dict[str, Any]:
    """The routine as form values: the same keys the form uses, so an edit starts from what is saved."""
    return {
        "name": routine["name"],
        "enabled": bool(routine["enabled"]),
        "trigger": routine["trigger"],
        "time": routine["time"][:5],
        "offset_minutes": routine["offset_minutes"],
        "days": list(routine["days"]),
        "zone": routine["zone"],
        "room": routine["room"],
        "device_id": routine["device_id"],
        "people": list(routine["people"]),
        "require_everyone_away": routine["require_everyone_away"],
        "sensors": list(routine["sensors"]),
        "require_sensors_clear": routine["require_sensors_clear"],
        "clear_minutes": routine["clear_minutes"],
        "active_from": routine["active_from"][:5] if routine["active_from"] else None,
        "active_to": routine["active_to"][:5] if routine["active_to"] else None,
        "value_entity": routine["value_entity"],
        "value_threshold": routine["value_threshold"],
        "off_threshold": routine["off_threshold"],
        "conditions_match": routine["conditions_match"],
        "require_states": conditions_to_text(routine["require_states"]),
        "catch_up_minutes": routine["catch_up_minutes"],
        "retries": routine["retries"],
        "repeat": routine["repeat"],
        "skip_noop": routine["skip_noop"],
        "notify_on_run": routine["notify_on_run"],
        "notify_on_skip": routine["notify_on_skip"],
        "notify_on_error": routine["notify_on_error"],
        "actions": steps_to_text(routine["actions"]),
    }


def _routine_from_form(
    routine: dict[str, Any],
    values: dict[str, Any],
    actions: list[dict[str, Any]],
    conditions: list[dict[str, str]],
) -> dict[str, Any]:
    """Merge the submitted form into the routine. Validation happens when it is saved."""
    return {
        **routine,
        "name": str(values.get("name") or "").strip() or routine["name"],
        "enabled": bool(values.get("enabled")),
        "trigger": values["trigger"],
        "time": _hhmmss(values["time"]),
        "offset_minutes": int(values.get("offset_minutes") or 0),
        "days": list(values.get("days") or []),
        "zone": values.get("zone") or "zone.home",
        "room": values.get("room") or None,
        "device_id": values.get("device_id") or None,
        "people": list(values.get("people") or []),
        "require_everyone_away": bool(values.get("require_everyone_away")),
        "sensors": list(values.get("sensors") or []),
        "require_sensors_clear": bool(values.get("require_sensors_clear")),
        "clear_minutes": int(values.get("clear_minutes") or 0),
        "active_from": _hhmmss(values["active_from"]) if values.get("active_from") else None,
        "active_to": _hhmmss(values["active_to"]) if values.get("active_to") else None,
        "value_entity": values.get("value_entity") or None,
        "value_threshold": values.get("value_threshold"),
        "off_threshold": values.get("off_threshold"),
        "conditions_match": values.get("conditions_match") or "all",
        "require_states": conditions,
        "catch_up_minutes": int(values.get("catch_up_minutes") or 0),
        "retries": int(values.get("retries") or 0),
        "repeat": bool(values.get("repeat")),
        "skip_noop": bool(values.get("skip_noop")),
        "notify_on_run": bool(values.get("notify_on_run")),
        "notify_on_skip": bool(values.get("notify_on_skip")),
        "notify_on_error": bool(values.get("notify_on_error")),
        "actions": actions,
    }


def _device_options(hass: Any) -> list[selector.SelectOptionDict]:
    """Physical devices a routine can be linked to. Daily Routine's own devices are left out."""
    options = []
    for device in dr.async_get(hass).devices:
        if any(domain == DOMAIN for domain, _ in device.identifiers):
            continue
        options.append(
            selector.SelectOptionDict(
                value=device.id, label=device.name_by_user or device.name or device.id
            )
        )
    return sorted(options, key=lambda option: option["label"].lower())


def _form_schema(hass: Any, values: dict[str, Any]) -> vol.Schema:
    """The editor. Optional fields use suggested values so clearing them really clears them."""

    def optional(key: str) -> vol.Optional:
        return vol.Optional(key, description={"suggested_value": values.get(key)})

    def entity(domain: str, multiple: bool = False) -> selector.EntitySelector:
        return selector.EntitySelector(
            selector.EntitySelectorConfig(domain=domain, multiple=multiple)
        )

    def number(low: float, high: float, step: Any = 1) -> selector.NumberSelector:
        return selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=low, max=high, step=step, mode=selector.NumberSelectorMode.BOX
            )
        )

    devices = _device_options(hass)
    return vol.Schema(
        {
            vol.Required("name", default=values.get("name", "")): str,
            vol.Required("enabled", default=bool(values.get("enabled", True))): bool,
            vol.Required("trigger", default=values.get("trigger", "time")): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=t, label=TRIGGER_NAMES.get(t, t))
                        for t in TRIGGERS
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required("time", default=values.get("time", "07:00")): str,
            vol.Required("offset_minutes", default=values.get("offset_minutes") or 0): number(
                -720, 720
            ),
            vol.Required(
                "days", default=values.get("days") or list(WEEKDAYS)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=d, label=d.capitalize()) for d in WEEKDAYS
                    ],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Required("zone", default=values.get("zone") or "zone.home"): entity("zone"),
            optional("room"): selector.AreaSelector(selector.AreaSelectorConfig()),
            optional("device_id"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=devices, mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
            optional("people"): entity("person", multiple=True),
            vol.Required(
                "require_everyone_away", default=bool(values.get("require_everyone_away"))
            ): bool,
            optional("sensors"): entity("binary_sensor", multiple=True),
            vol.Required(
                "require_sensors_clear", default=bool(values.get("require_sensors_clear"))
            ): bool,
            vol.Required("clear_minutes", default=values.get("clear_minutes") or 0): number(
                0, 1440
            ),
            optional("active_from"): str,
            optional("active_to"): str,
            optional("value_entity"): entity("sensor"),
            optional("value_threshold"): number(-1_000_000, 1_000_000, "any"),
            optional("off_threshold"): number(-1_000_000, 1_000_000, "any"),
            vol.Required(
                "conditions_match", default=values.get("conditions_match") or "all"
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=CONDITION_MATCH_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
            vol.Required(
                "require_states", default=values.get("require_states", "")
            ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            vol.Required("catch_up_minutes", default=values.get("catch_up_minutes") or 0): number(
                0, 180
            ),
            vol.Required("retries", default=values.get("retries") or 0): number(0, 3),
            vol.Required("repeat", default=bool(values.get("repeat"))): bool,
            vol.Required("skip_noop", default=bool(values.get("skip_noop", True))): bool,
            vol.Required("notify_on_run", default=bool(values.get("notify_on_run"))): bool,
            vol.Required("notify_on_skip", default=bool(values.get("notify_on_skip"))): bool,
            vol.Required(
                "notify_on_error", default=bool(values.get("notify_on_error", True))
            ): bool,
            vol.Required("actions", default=values.get("actions", "")): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
        }
    )


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
            actions: list[dict[str, Any]] = []
            conditions: list[dict[str, str]] = []
            try:
                actions = parse_steps_text(user_input.get("actions") or "")
            except vol.Invalid as err:
                errors["actions"] = "invalid_steps"
                placeholders["reason"] = str(err)
            try:
                conditions = parse_conditions_text(user_input.get("require_states") or "")
            except vol.Invalid as err:
                errors["require_states"] = "invalid_conditions"
                placeholders["reason"] = str(err)
            if not errors:
                raw = _routine_from_form(routine, user_input, actions, conditions)
                try:
                    await coordinator.async_save_routine(raw)
                except vol.Invalid as err:
                    errors["base"] = "invalid_routine"
                    placeholders["reason"] = str(err)
                else:
                    return self.async_abort(reason="updated")

        values = user_input if user_input is not None else _form_from_routine(routine)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_form_schema(self.hass, values),
            errors=errors,
            description_placeholders={"name": routine["name"], **placeholders},
        )
