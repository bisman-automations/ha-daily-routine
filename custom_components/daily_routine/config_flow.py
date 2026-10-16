"""Config and options flow — one entry per routine, with setup templates."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ACTION_1_DELAY,
    CONF_ACTION_1_ENTITIES,
    CONF_ACTION_1_SERVICE,
    CONF_ACTION_2_DELAY,
    CONF_ACTION_2_ENTITIES,
    CONF_ACTION_2_SERVICE,
    CONF_ACTION_3_ENTITIES,
    CONF_ACTION_3_SERVICE,
    CONF_CLEAR_MINUTES,
    CONF_DAYS,
    CONF_ENABLED,
    CONF_NAME,
    CONF_NOTIFY_ON_RUN,
    CONF_NOTIFY_ON_SKIP,
    CONF_OFFSET_MINUTES,
    CONF_PEOPLE,
    CONF_REQUIRE_EVERYONE_AWAY,
    CONF_REQUIRE_SENSORS_CLEAR,
    CONF_ROUTINE_ID,
    CONF_SENSORS,
    CONF_TIME,
    CONF_TRIGGER,
    DEFAULT_DAYS,
    DEFAULT_TIME,
    DEFAULT_TRIGGER,
    DOMAIN,
    TRIGGER_EVERYONE_LEAVES,
    TRIGGER_MANUAL,
    TRIGGER_SOMEONE_ARRIVES,
    TRIGGER_SUNRISE,
    TRIGGER_SUNSET,
    TRIGGER_TIME,
    TRIGGERS,
    WEEKDAYS,
)

ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
SERVICE_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")

# Template ids shown on the first Add step
TEMPLATES = (
    "morning_covers",
    "evening_covers",
    "morning_lights",
    "bedtime_lights",
    "away_lights_off",
    "arrive_lights_on",
    "lock_when_away",
    "unlock_on_arrive",
    "notify_at_time",
    "manual_scene",
    "custom",
)


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip().lower()).strip("_")
    return (slug or "routine")[:64]


def _entity_sel(*domains: str, multiple: bool = True):
    cfg: selector.EntitySelectorConfig = {"multiple": multiple}
    if len(domains) == 1:
        cfg["domain"] = domains[0]
    elif domains:
        cfg["domain"] = list(domains)
    return selector.EntitySelector(selector.EntitySelectorConfig(**cfg))


def _full_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Full custom / options schema."""
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")): selector.TextSelector(),
            vol.Required(
                CONF_ENABLED, default=d.get(CONF_ENABLED, True)
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_TRIGGER, default=d.get(CONF_TRIGGER, DEFAULT_TRIGGER)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(TRIGGERS),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="trigger",
                )
            ),
            vol.Optional(
                CONF_TIME, default=d.get(CONF_TIME, DEFAULT_TIME)
            ): selector.TimeSelector(),
            vol.Optional(
                CONF_OFFSET_MINUTES, default=d.get(CONF_OFFSET_MINUTES, 0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-180,
                    max=180,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="min",
                )
            ),
            vol.Optional(
                CONF_DAYS, default=d.get(CONF_DAYS, DEFAULT_DAYS)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(WEEKDAYS),
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Optional(
                CONF_REQUIRE_EVERYONE_AWAY,
                default=d.get(CONF_REQUIRE_EVERYONE_AWAY, False),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_REQUIRE_SENSORS_CLEAR,
                default=d.get(CONF_REQUIRE_SENSORS_CLEAR, False),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_CLEAR_MINUTES, default=d.get(CONF_CLEAR_MINUTES, 0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=1440, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_PEOPLE, default=d.get(CONF_PEOPLE, [])): _entity_sel("person"),
            vol.Optional(CONF_SENSORS, default=d.get(CONF_SENSORS, [])): _entity_sel(
                "binary_sensor"
            ),
            vol.Optional(
                CONF_NOTIFY_ON_RUN, default=d.get(CONF_NOTIFY_ON_RUN, False)
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_NOTIFY_ON_SKIP, default=d.get(CONF_NOTIFY_ON_SKIP, False)
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_ACTION_1_SERVICE, default=d.get(CONF_ACTION_1_SERVICE, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_ACTION_1_ENTITIES, default=d.get(CONF_ACTION_1_ENTITIES, [])
            ): selector.EntitySelector(selector.EntitySelectorConfig(multiple=True)),
            vol.Optional(
                CONF_ACTION_1_DELAY, default=d.get(CONF_ACTION_1_DELAY, 0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=3600,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
            vol.Optional(
                CONF_ACTION_2_SERVICE, default=d.get(CONF_ACTION_2_SERVICE, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_ACTION_2_ENTITIES, default=d.get(CONF_ACTION_2_ENTITIES, [])
            ): selector.EntitySelector(selector.EntitySelectorConfig(multiple=True)),
            vol.Optional(
                CONF_ACTION_2_DELAY, default=d.get(CONF_ACTION_2_DELAY, 0)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=3600,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
            vol.Optional(
                CONF_ACTION_3_SERVICE, default=d.get(CONF_ACTION_3_SERVICE, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_ACTION_3_ENTITIES, default=d.get(CONF_ACTION_3_ENTITIES, [])
            ): selector.EntitySelector(selector.EntitySelectorConfig(multiple=True)),
        }
    )


def _pack_options(user_input: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize form data into stored options."""
    src = {**(base or {}), **user_input}
    name = (src.get(CONF_NAME) or "").strip()
    service = (src.get(CONF_ACTION_1_SERVICE) or "").strip()
    return {
        CONF_NAME: name,
        CONF_ENABLED: src.get(CONF_ENABLED, True),
        CONF_TRIGGER: src.get(CONF_TRIGGER, DEFAULT_TRIGGER),
        CONF_TIME: src.get(CONF_TIME, DEFAULT_TIME),
        CONF_OFFSET_MINUTES: int(src.get(CONF_OFFSET_MINUTES) or 0),
        CONF_DAYS: src.get(CONF_DAYS) or list(DEFAULT_DAYS),
        CONF_REQUIRE_EVERYONE_AWAY: bool(src.get(CONF_REQUIRE_EVERYONE_AWAY, False)),
        CONF_REQUIRE_SENSORS_CLEAR: bool(src.get(CONF_REQUIRE_SENSORS_CLEAR, False)),
        CONF_CLEAR_MINUTES: int(src.get(CONF_CLEAR_MINUTES) or 0),
        CONF_PEOPLE: list(src.get(CONF_PEOPLE) or []),
        CONF_SENSORS: list(src.get(CONF_SENSORS) or []),
        CONF_NOTIFY_ON_RUN: bool(src.get(CONF_NOTIFY_ON_RUN, False)),
        CONF_NOTIFY_ON_SKIP: bool(src.get(CONF_NOTIFY_ON_SKIP, False)),
        CONF_ACTION_1_SERVICE: service,
        CONF_ACTION_1_ENTITIES: list(src.get(CONF_ACTION_1_ENTITIES) or []),
        CONF_ACTION_1_DELAY: int(src.get(CONF_ACTION_1_DELAY) or 0),
        CONF_ACTION_2_SERVICE: (src.get(CONF_ACTION_2_SERVICE) or "").strip(),
        CONF_ACTION_2_ENTITIES: list(src.get(CONF_ACTION_2_ENTITIES) or []),
        CONF_ACTION_2_DELAY: int(src.get(CONF_ACTION_2_DELAY) or 0),
        CONF_ACTION_3_SERVICE: (src.get(CONF_ACTION_3_SERVICE) or "").strip(),
        CONF_ACTION_3_ENTITIES: list(src.get(CONF_ACTION_3_ENTITIES) or []),
    }


def options_to_routine(
    entry_id: str, title: str, options: dict[str, Any], data: dict[str, Any]
) -> dict[str, Any]:
    """Build a runtime routine dict from a config entry."""
    rid = data.get(CONF_ROUTINE_ID) or entry_id
    name = options.get(CONF_NAME) or title or rid
    actions: list[dict[str, Any]] = []
    for n in (1, 2, 3):
        service = (options.get(f"action_{n}_service") or "").strip()
        entities = options.get(f"action_{n}_entities") or []
        delay = options.get(f"action_{n}_delay") or 0
        if service:
            action: dict[str, Any] = {"service": service, "data": {}}
            if entities:
                action["target"] = {"entity_id": list(entities)}
            actions.append(action)
        if n < 3 and delay:
            try:
                delay_val = float(delay)
            except (TypeError, ValueError):
                delay_val = 0
            if delay_val > 0:
                actions.append({"delay": delay_val})

    time_val = options.get(CONF_TIME) or DEFAULT_TIME
    if isinstance(time_val, str) and len(time_val) == 5:
        time_val = time_val + ":00"
    trigger = options.get(CONF_TRIGGER) or DEFAULT_TRIGGER
    if trigger not in TRIGGERS:
        trigger = DEFAULT_TRIGGER
    try:
        offset = int(options.get(CONF_OFFSET_MINUTES) or 0)
    except (TypeError, ValueError):
        offset = 0

    return {
        "id": rid,
        "name": name,
        "enabled": bool(options.get(CONF_ENABLED, True)),
        "trigger": trigger,
        "time": time_val,
        "offset_minutes": offset,
        "days": list(options.get(CONF_DAYS) or DEFAULT_DAYS),
        "require_everyone_away": bool(options.get(CONF_REQUIRE_EVERYONE_AWAY, False)),
        "require_sensors_clear": bool(options.get(CONF_REQUIRE_SENSORS_CLEAR, False)),
        "clear_minutes": int(options.get(CONF_CLEAR_MINUTES) or 0),
        "people": list(options.get(CONF_PEOPLE) or []),
        "sensors": list(options.get(CONF_SENSORS) or []),
        "notify_on_run": bool(options.get(CONF_NOTIFY_ON_RUN, False)),
        "notify_on_skip": bool(options.get(CONF_NOTIFY_ON_SKIP, False)),
        "actions": actions,
        "entry_id": entry_id,
    }


def _template_schema(template: str) -> vol.Schema:
    """Short form for a given template — only the fields the user must pick."""
    if template == "morning_covers":
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default="Morning covers"): selector.TextSelector(),
                vol.Required("covers"): _entity_sel("cover"),
                vol.Optional(CONF_TRIGGER, default=TRIGGER_TIME): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[TRIGGER_TIME, TRIGGER_SUNRISE],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        translation_key="trigger",
                    )
                ),
                vol.Optional(CONF_TIME, default="07:00:00"): selector.TimeSelector(),
                vol.Optional(CONF_OFFSET_MINUTES, default=0): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-180,
                        max=180,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Optional(CONF_DAYS, default=DEFAULT_DAYS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(WEEKDAYS),
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }
        )
    if template == "evening_covers":
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default="Evening covers"): selector.TextSelector(),
                vol.Required("covers"): _entity_sel("cover"),
                vol.Optional(CONF_TRIGGER, default=TRIGGER_SUNSET): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[TRIGGER_SUNSET, TRIGGER_TIME],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        translation_key="trigger",
                    )
                ),
                vol.Optional(CONF_TIME, default="20:00:00"): selector.TimeSelector(),
                vol.Optional(CONF_OFFSET_MINUTES, default=0): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-180,
                        max=180,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Optional(CONF_DAYS, default=DEFAULT_DAYS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(WEEKDAYS),
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }
        )
    if template == "morning_lights":
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default="Morning lights"): selector.TextSelector(),
                vol.Required("lights"): _entity_sel("light"),
                vol.Optional(CONF_TRIGGER, default=TRIGGER_TIME): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[TRIGGER_TIME, TRIGGER_SUNRISE],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        translation_key="trigger",
                    )
                ),
                vol.Optional(CONF_TIME, default="07:00:00"): selector.TimeSelector(),
                vol.Optional(CONF_OFFSET_MINUTES, default=0): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-180,
                        max=180,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Optional(CONF_DAYS, default=DEFAULT_DAYS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(WEEKDAYS),
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }
        )
    if template == "bedtime_lights":
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default="Bedtime lights off"): selector.TextSelector(),
                vol.Required("lights"): _entity_sel("light"),
                vol.Optional(CONF_TIME, default="22:30:00"): selector.TimeSelector(),
                vol.Optional(CONF_DAYS, default=DEFAULT_DAYS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(WEEKDAYS),
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }
        )
    if template == "away_lights_off":
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default="Away lights off"): selector.TextSelector(),
                vol.Required("lights"): _entity_sel("light"),
                vol.Required(CONF_PEOPLE): _entity_sel("person"),
                vol.Optional(CONF_SENSORS, default=[]): _entity_sel("binary_sensor"),
                vol.Optional(CONF_CLEAR_MINUTES, default=10): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=1440, mode=selector.NumberSelectorMode.BOX
                    )
                ),
            }
        )
    if template == "arrive_lights_on":
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default="Arrive lights on"): selector.TextSelector(),
                vol.Required("lights"): _entity_sel("light"),
                vol.Required(CONF_PEOPLE): _entity_sel("person"),
            }
        )
    if template == "lock_when_away":
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default="Lock when away"): selector.TextSelector(),
                vol.Required("locks"): _entity_sel("lock"),
                vol.Required(CONF_PEOPLE): _entity_sel("person"),
            }
        )
    if template == "unlock_on_arrive":
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default="Unlock on arrive"): selector.TextSelector(),
                vol.Required("locks"): _entity_sel("lock"),
                vol.Required(CONF_PEOPLE): _entity_sel("person"),
            }
        )
    if template == "notify_at_time":
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default="Timed reminder"): selector.TextSelector(),
                vol.Required(CONF_TIME, default="09:00:00"): selector.TimeSelector(),
                vol.Optional(CONF_DAYS, default=DEFAULT_DAYS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(WEEKDAYS),
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Optional("message", default="Routine reminder"): selector.TextSelector(),
            }
        )
    if template == "manual_scene":
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default="Manual scene"): selector.TextSelector(),
                vol.Required("scenes"): _entity_sel("scene"),
            }
        )
    # custom
    return _full_schema()


def _template_to_options(template: str, user_input: dict[str, Any]) -> dict[str, Any]:
    """Map short template answers into full options."""
    name = (user_input.get(CONF_NAME) or template).strip()
    base: dict[str, Any] = {
        CONF_NAME: name,
        CONF_ENABLED: True,
        CONF_TRIGGER: TRIGGER_TIME,
        CONF_TIME: DEFAULT_TIME,
        CONF_OFFSET_MINUTES: 0,
        CONF_DAYS: list(DEFAULT_DAYS),
        CONF_REQUIRE_EVERYONE_AWAY: False,
        CONF_REQUIRE_SENSORS_CLEAR: False,
        CONF_CLEAR_MINUTES: 0,
        CONF_PEOPLE: [],
        CONF_SENSORS: [],
        CONF_NOTIFY_ON_RUN: False,
        CONF_NOTIFY_ON_SKIP: False,
        CONF_ACTION_1_SERVICE: "",
        CONF_ACTION_1_ENTITIES: [],
        CONF_ACTION_1_DELAY: 0,
        CONF_ACTION_2_SERVICE: "",
        CONF_ACTION_2_ENTITIES: [],
        CONF_ACTION_2_DELAY: 0,
        CONF_ACTION_3_SERVICE: "",
        CONF_ACTION_3_ENTITIES: [],
    }

    if template == "custom":
        return _pack_options(user_input, base)

    if template == "morning_covers":
        base[CONF_TRIGGER] = user_input.get(CONF_TRIGGER, TRIGGER_TIME)
        base[CONF_TIME] = user_input.get(CONF_TIME, "07:00:00")
        base[CONF_OFFSET_MINUTES] = int(user_input.get(CONF_OFFSET_MINUTES) or 0)
        base[CONF_DAYS] = user_input.get(CONF_DAYS) or list(DEFAULT_DAYS)
        base[CONF_ACTION_1_SERVICE] = "cover.open_cover"
        base[CONF_ACTION_1_ENTITIES] = list(user_input.get("covers") or [])
    elif template == "evening_covers":
        base[CONF_TRIGGER] = user_input.get(CONF_TRIGGER, TRIGGER_SUNSET)
        base[CONF_TIME] = user_input.get(CONF_TIME, "20:00:00")
        base[CONF_OFFSET_MINUTES] = int(user_input.get(CONF_OFFSET_MINUTES) or 0)
        base[CONF_DAYS] = user_input.get(CONF_DAYS) or list(DEFAULT_DAYS)
        base[CONF_ACTION_1_SERVICE] = "cover.close_cover"
        base[CONF_ACTION_1_ENTITIES] = list(user_input.get("covers") or [])
    elif template == "morning_lights":
        base[CONF_TRIGGER] = user_input.get(CONF_TRIGGER, TRIGGER_TIME)
        base[CONF_TIME] = user_input.get(CONF_TIME, "07:00:00")
        base[CONF_OFFSET_MINUTES] = int(user_input.get(CONF_OFFSET_MINUTES) or 0)
        base[CONF_DAYS] = user_input.get(CONF_DAYS) or list(DEFAULT_DAYS)
        base[CONF_ACTION_1_SERVICE] = "light.turn_on"
        base[CONF_ACTION_1_ENTITIES] = list(user_input.get("lights") or [])
    elif template == "bedtime_lights":
        base[CONF_TRIGGER] = TRIGGER_TIME
        base[CONF_TIME] = user_input.get(CONF_TIME, "22:30:00")
        base[CONF_DAYS] = user_input.get(CONF_DAYS) or list(DEFAULT_DAYS)
        base[CONF_ACTION_1_SERVICE] = "light.turn_off"
        base[CONF_ACTION_1_ENTITIES] = list(user_input.get("lights") or [])
    elif template == "away_lights_off":
        base[CONF_TRIGGER] = TRIGGER_EVERYONE_LEAVES
        base[CONF_PEOPLE] = list(user_input.get(CONF_PEOPLE) or [])
        base[CONF_SENSORS] = list(user_input.get(CONF_SENSORS) or [])
        base[CONF_REQUIRE_SENSORS_CLEAR] = bool(base[CONF_SENSORS])
        base[CONF_CLEAR_MINUTES] = int(user_input.get(CONF_CLEAR_MINUTES) or 10)
        base[CONF_ACTION_1_SERVICE] = "light.turn_off"
        base[CONF_ACTION_1_ENTITIES] = list(user_input.get("lights") or [])
    elif template == "arrive_lights_on":
        base[CONF_TRIGGER] = TRIGGER_SOMEONE_ARRIVES
        base[CONF_PEOPLE] = list(user_input.get(CONF_PEOPLE) or [])
        base[CONF_ACTION_1_SERVICE] = "light.turn_on"
        base[CONF_ACTION_1_ENTITIES] = list(user_input.get("lights") or [])
    elif template == "lock_when_away":
        base[CONF_TRIGGER] = TRIGGER_EVERYONE_LEAVES
        base[CONF_PEOPLE] = list(user_input.get(CONF_PEOPLE) or [])
        base[CONF_ACTION_1_SERVICE] = "lock.lock"
        base[CONF_ACTION_1_ENTITIES] = list(user_input.get("locks") or [])
    elif template == "unlock_on_arrive":
        base[CONF_TRIGGER] = TRIGGER_SOMEONE_ARRIVES
        base[CONF_PEOPLE] = list(user_input.get(CONF_PEOPLE) or [])
        base[CONF_ACTION_1_SERVICE] = "lock.unlock"
        base[CONF_ACTION_1_ENTITIES] = list(user_input.get("locks") or [])
    elif template == "notify_at_time":
        base[CONF_TRIGGER] = TRIGGER_TIME
        base[CONF_TIME] = user_input.get(CONF_TIME, "09:00:00")
        base[CONF_DAYS] = user_input.get(CONF_DAYS) or list(DEFAULT_DAYS)
        base[CONF_ACTION_1_SERVICE] = "notify.persistent_notification"
        # message stored via action data not supported in simple options —
        # use notify_on_run with empty action entity list and a second approach:
        # still set action service; extra data not in options model.
        # For now fire persistent notification without custom message in action data.
        base[CONF_NOTIFY_ON_RUN] = True
        base[CONF_ACTION_1_SERVICE] = "notify.persistent_notification"
        base[CONF_ACTION_1_ENTITIES] = []
    elif template == "manual_scene":
        base[CONF_TRIGGER] = TRIGGER_MANUAL
        base[CONF_ACTION_1_SERVICE] = "scene.turn_on"
        base[CONF_ACTION_1_ENTITIES] = list(user_input.get("scenes") or [])

    return _pack_options(base)


class DailyRoutineConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a new routine, optionally from a template."""

    VERSION = 1

    def __init__(self) -> None:
        self._template: str = "custom"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._template = user_input.get("template") or "custom"
            return await self.async_step_details()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("template", default="morning_covers"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(TEMPLATES),
                            mode=selector.SelectSelectorMode.LIST,
                            translation_key="template",
                        )
                    )
                }
            ),
        )

    async def async_step_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        template = self._template or "custom"

        if user_input is not None:
            options = _template_to_options(template, user_input)
            name = options.get(CONF_NAME) or ""
            if not name:
                errors["base"] = "name_required"
            service = options.get(CONF_ACTION_1_SERVICE) or ""
            if service and not SERVICE_RE.match(service):
                errors["base"] = "invalid_service"
            if not errors:
                rid = _slug(name)
                await self.async_set_unique_id(rid)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=name,
                    data={CONF_ROUTINE_ID: rid},
                    options=options,
                )

        return self.async_show_form(
            step_id="details",
            data_schema=_template_schema(template),
            errors=errors,
            description_placeholders={"template": template},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DailyRoutineOptionsFlow:
        return DailyRoutineOptionsFlow()


class DailyRoutineOptionsFlow(OptionsFlowWithReload):
    """Full editor for an existing routine."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            options = _pack_options(user_input)
            name = options.get(CONF_NAME) or ""
            if not name:
                errors["base"] = "name_required"
            service = options.get(CONF_ACTION_1_SERVICE) or ""
            if service and not SERVICE_RE.match(service):
                errors["base"] = "invalid_service"
            if not errors:
                if name != self.config_entry.title:
                    self.hass.config_entries.async_update_entry(self.config_entry, title=name)
                return self.async_create_entry(title="", data=options)

        defaults = dict(self.config_entry.options)
        if not defaults.get(CONF_NAME):
            defaults[CONF_NAME] = self.config_entry.title
        return self.async_show_form(
            step_id="init",
            data_schema=_full_schema(defaults),
            errors=errors,
        )
