"""Constants for Daily Routine."""

from __future__ import annotations

DOMAIN = "daily_routine"

# Per-routine config entry fields
CONF_ROUTINE_ID = "routine_id"
CONF_NAME = "name"
CONF_ENABLED = "enabled"
CONF_TRIGGER = "trigger"
CONF_TIME = "time"
CONF_OFFSET_MINUTES = "offset_minutes"
CONF_DAYS = "days"
CONF_REQUIRE_EVERYONE_AWAY = "require_everyone_away"
CONF_REQUIRE_SENSORS_CLEAR = "require_sensors_clear"
CONF_CLEAR_MINUTES = "clear_minutes"
CONF_PEOPLE = "people"
CONF_SENSORS = "sensors"
CONF_NOTIFY_ON_RUN = "notify_on_run"
CONF_NOTIFY_ON_SKIP = "notify_on_skip"
CONF_ACTION_1_SERVICE = "action_1_service"
CONF_ACTION_1_ENTITIES = "action_1_entities"
CONF_ACTION_1_DELAY = "action_1_delay"
CONF_ACTION_2_SERVICE = "action_2_service"
CONF_ACTION_2_ENTITIES = "action_2_entities"
CONF_ACTION_2_DELAY = "action_2_delay"
CONF_ACTION_3_SERVICE = "action_3_service"
CONF_ACTION_3_ENTITIES = "action_3_entities"

CONF_ROUTINES_JSON = "routines_json"

STORAGE_KEY = f"{DOMAIN}.runtime"
STORAGE_VERSION = 1
MAX_ROUTINES = 100
MAX_ACTIONS = 50
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

TRIGGER_TIME = "time"
TRIGGER_SUNRISE = "sunrise"
TRIGGER_SUNSET = "sunset"
TRIGGER_MANUAL = "manual"
TRIGGER_EVERYONE_LEAVES = "everyone_leaves"
TRIGGER_SOMEONE_ARRIVES = "someone_arrives"

TRIGGERS = (
    TRIGGER_TIME,
    TRIGGER_SUNRISE,
    TRIGGER_SUNSET,
    TRIGGER_MANUAL,
    TRIGGER_EVERYONE_LEAVES,
    TRIGGER_SOMEONE_ARRIVES,
)

EVENT_ROUTINE_STARTED = f"{DOMAIN}_routine_started"
EVENT_ROUTINE_FINISHED = f"{DOMAIN}_routine_finished"
EVENT_ROUTINE_SKIPPED = f"{DOMAIN}_routine_skipped"

DEFAULT_TIME = "07:00:00"
DEFAULT_DAYS = list(WEEKDAYS)
DEFAULT_TRIGGER = TRIGGER_TIME
