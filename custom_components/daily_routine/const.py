"""Constants for Daily Routine."""

from __future__ import annotations

DOMAIN = "daily_routine"

# Legacy (v1.0.0) config-entry keys, read only during migration.
LEGACY_CONF_ROUTINE_ID = "routine_id"
LEGACY_CONF_ROUTINES_JSON = "routines_json"

STORAGE_KEY = f"{DOMAIN}.routines"
STORAGE_VERSION = 2

MAX_ROUTINES = 200
MAX_ACTIONS = 50
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

TRIGGER_TIME = "time"
TRIGGER_SUNRISE = "sunrise"
TRIGGER_SUNSET = "sunset"
TRIGGER_MANUAL = "manual"
TRIGGER_EVERYONE_LEAVES = "everyone_leaves"
TRIGGER_SOMEONE_ARRIVES = "someone_arrives"
TRIGGER_ROOM_CLEAR = "room_clear"
TRIGGER_VALUE_ABOVE = "value_above"
TRIGGER_VALUE_BELOW = "value_below"

TRIGGERS = (
    TRIGGER_TIME,
    TRIGGER_SUNRISE,
    TRIGGER_SUNSET,
    TRIGGER_MANUAL,
    TRIGGER_EVERYONE_LEAVES,
    TRIGGER_SOMEONE_ARRIVES,
    TRIGGER_ROOM_CLEAR,
    TRIGGER_VALUE_ABOVE,
    TRIGGER_VALUE_BELOW,
)

ROUTINE_SUBENTRY = "routine"

EVENT_ROUTINE_STARTED = f"{DOMAIN}_routine_started"
EVENT_ROUTINE_FINISHED = f"{DOMAIN}_routine_finished"
EVENT_ROUTINE_SKIPPED = f"{DOMAIN}_routine_skipped"

SIGNAL_ROUTINES_CHANGED = f"{DOMAIN}_routines_changed"

DEFAULT_TIME = "07:00:00"
RETRY_DELAY_SECONDS = 2
