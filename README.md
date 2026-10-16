# Daily Routine

<p align="center">
  <img src="custom_components/daily_routine/brand/icon.png" alt="Daily Routine logo" width="128">
</p>

**Reusable, safety-aware daily routines for Home Assistant** — configure everything from the UI, no YAML required.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/bisman-automations/ha-daily-routine)](https://github.com/bisman-automations/ha-daily-routine/releases)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bisman-automations&repository=ha-daily-routine&category=integration)

---

## Why Daily Routine?

Home Assistant automations are powerful, but repeating the same “run these actions every day at X with safety checks” pattern gets messy. Daily Routine gives you:

- **UI-only configuration** (JSON editor with live validation)
- **Precise one-shot scheduling** (no 15-second polling drift)
- **Fail-closed safety** for away / occupancy scenarios
- **Per-routine status sensors** you can put on dashboards
- **Delay steps**, optional notifications, events, and manual run service
- **Persistence** of last-run state across restarts (clear timers are rebuilt from live sensor state)

Perfect for morning curtains, evening lights-off when everyone is away, weekend-only scenes, etc.

---

## Features

| Feature | Details |
|---------|---------|
| **Time + weekday scheduling** | Exact local time, any subset of days |
| **Away safety** | All selected `person` entities must be exactly `not_home` |
| **Sensor clear timer** | All selected binary sensors must stay `off` for N continuous minutes; the routine runs the moment that period completes |
| **Waiting, not skipping** | If a scheduled or presence run is blocked by people or sensors, it waits (until midnight) and runs as soon as conditions are met |
| **Fail closed** | Unknown / unavailable entities block the routine |
| **Mid-run re-check** | Conditions are verified before every action |
| **Daily run limit** | Scheduled runs fire at most once per calendar day |
| **Delay actions** | `{"delay": 10}` or `{"delay": "00:00:30"}` between steps |
| **Status sensors** | One sensor per routine with last run, next run, conditions, result |
| **Events** | `daily_routine_routine_started`, `_finished`, `_skipped` |
| **Optional notifications** | Persistent notification on success or skip |
| **Manual run** | Service that still respects all safety conditions |
| **HACS ready** | One-click install button above |

---

## Installation

### HACS (recommended)

1. Click the button above **or**  
   HACS → Integrations → ⋮ → **Custom repositories** → add  
   `https://github.com/bisman-automations/ha-daily-routine` as **Integration**.
2. Search for **Daily Routine** and install.
3. Restart Home Assistant.
4. Settings → Devices & services → **Add integration** → search **Daily Routine**.
5. Open the integration → **Configure** and paste/edit your routines JSON.

### Manual

Copy the `custom_components/daily_routine` folder into your Home Assistant `config/custom_components/` directory, restart, then add the integration from the UI.

---

## Configuration

All configuration lives in **Settings → Devices & services → Daily Routine → Configure**.

Paste a JSON array of routines. Validation runs immediately on save; any error is shown in the form.

### Example

```json
[
  {
    "id": "morning_covers",
    "name": "Morning curtains",
    "enabled": true,
    "time": "07:00:00",
    "days": ["mon", "tue", "wed", "thu", "fri"],
    "require_everyone_away": false,
    "require_sensors_clear": false,
    "clear_minutes": 0,
    "people": [],
    "sensors": [],
    "notify_on_run": false,
    "notify_on_skip": false,
    "actions": [
      {
        "service": "cover.open_cover",
        "target": {
          "entity_id": ["cover.living_room_curtains", "cover.kitchen_curtains"]
        },
        "data": {}
      }
    ]
  },
  {
    "id": "away_lights_off",
    "name": "Away lights off",
    "enabled": true,
    "trigger": "everyone_leaves",
    "time": "00:00:00",
    "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "require_everyone_away": true,
    "require_sensors_clear": true,
    "clear_minutes": 15,
    "people": ["person.adult_one", "person.adult_two"],
    "sensors": [
      "binary_sensor.hall_motion",
      "binary_sensor.living_room_occupancy",
      "binary_sensor.kitchen_motion"
    ],
    "notify_on_run": true,
    "notify_on_skip": false,
    "actions": [
      {
        "service": "light.turn_off",
        "target": {
          "entity_id": ["light.living_room", "light.kitchen", "light.hall"]
        },
        "data": {}
      },
      {
        "delay": 5
      },
      {
        "service": "notify.persistent_notification",
        "data": {
          "message": "Away routine completed – lights turned off.",
          "title": "Daily Routine"
        }
      }
    ]
  }
]
```

### Field reference

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Stable unique ID (`a-zA-Z0-9_-`, max 64 chars) |
| `name` | string | Friendly name shown in sensors & logs |
| `enabled` | bool | Master on/off switch |
| `trigger` | string | `time` (default), `sunrise`, `sunset`, `everyone_leaves`, `someone_arrives`, or `manual` |
| `time` | string | Local time `HH:MM` or `HH:MM:SS` (used by `time` triggers) |
| `days` | list | Any of `mon tue wed thu fri sat sun` |
| `require_everyone_away` | bool | All `people` must be exactly `not_home` |
| `require_sensors_clear` | bool | All `sensors` must be `off` for `clear_minutes` |
| `clear_minutes` | int | 0–1440 continuous clear **minutes** required |
| `people` | list | `person.*` entity IDs (required for presence triggers) |
| `sensors` | list | Binary sensor entity IDs (on = occupied/motion) |
| `notify_on_run` | bool | Persistent notification on successful run |
| `notify_on_skip` | bool | Persistent notification when skipped |
| `actions` | list | Ordered steps (see below) |

### Action types

**Service call**

```json
{
  "service": "light.turn_off",
  "target": { "entity_id": ["light.kitchen"] },
  "data": { "transition": 2 }
}
```

**Delay**

```json
{ "delay": 10 }
```
or
```json
{ "delay": "00:00:30" }
```

---

## Safety behaviour

- Missing / `unknown` / `unavailable` entities → conditions fail → routine does **not** run.
- Every selected person must be exactly `not_home`.
- Every selected sensor must be exactly `off`; any non-off state resets the clear timer.
- The clear timer starts from the moment Home Assistant sees the sensors go clear. Any motion restarts it from zero. It is not stored, so after a restart it begins again from the live sensor state.
- **Waiting:** if a scheduled or presence run is blocked by people or sensors, it does not give up. It waits until the conditions hold (for example, the clear timer finishes) and then runs once. If they never hold, it is skipped at midnight and fires a `daily_routine_routine_skipped` event with `conditions_not_met`.
- **Waiting is not kept across restarts.** A routine that was waiting when Home Assistant restarted starts fresh on the next trigger.
- Conditions are re-checked **before every action**. If they stop holding, remaining actions are aborted.
- There is **no force / bypass** parameter on the run service.
- A scheduled routine is marked “ran today” *before* actions execute, preventing duplicate runs if a service call hangs.
- This is **not** a security system. Always test with harmless actions first.

---

## Services

| Service | Description |
|---------|-------------|
| `daily_routine.run_routine` | Run a routine by `routine_id`. Safety conditions still apply; daily limit is ignored. |
| `daily_routine.reload_routines` | Re-validate and reload definitions from the options UI without restarting HA. |

Example:

```yaml
service: daily_routine.run_routine
data:
  routine_id: away_lights_off
```

---

## Common setups

- **Bedroom curtains separately:** make one routine for the other curtains at the usual time, and a second routine for the bedroom curtains with its own time (for example 08:00).
- **Lights off when the house is empty:** use `"trigger": "everyone_leaves"`, set `require_everyone_away` and `require_sensors_clear` to true, list all people and all motion/occupancy sensors, and set `clear_minutes` to how long the house must stay quiet (for example 15). Lights turn off once everyone has left and the sensors have been clear for that long.

## Status sensors

Each routine creates a sensor:

- **State**: `idle` · `waiting` · `running` · `skipped` · `error` · `unknown`
- **Attributes**:
  - `routine_id`, `name`, `enabled`
  - `last_run`, `last_result`, `next_run`
  - `clear_since`, `conditions_ok`, `block_reason`, `waiting`
  - `days`, `time`

Use them on a dashboard, in automations, or with the Logbook.

---

## Events

Listen for:

- `daily_routine_routine_started`
- `daily_routine_routine_finished` (includes `result`)
- `daily_routine_routine_skipped` (includes `result`; `waiting:<reason>` when a run starts waiting)

Payload always contains `routine_id` and `name`.

---

## Tips & best practices

1. Start with a simple time-only routine (no away/sensors) and verify the status sensor updates.
2. For away routines always enable **both** `require_everyone_away` **and** `require_sensors_clear`.
3. Use short `clear_minutes` (5–15) while testing; increase later.
4. Prefer `scene.turn_on` or `script.turn_on` for complex action sequences.
5. Put the status sensors on a “Routines” dashboard card so you can see next-run times at a glance.
6. After editing JSON, the integration reloads automatically; you can also call `daily_routine.reload_routines`.

---

## Development

- Version lives in `custom_components/daily_routine/manifest.json`.
- Last-run state is stored via Home Assistant’s `Store` helper and survives restarts. Clear timers and waiting state are kept in memory only.
- Tests: `pip install -r requirements_test.txt`, then `pytest` and `node --test tests/`.
- Scheduling uses `async_track_point_in_time` for exact firing.
- Before releasing, run against your HA version and the usual HACS / hassfest checks.

---

## License & credits

Open source. Built for real households that want reliable, readable routines without YAML sprawl.

Issues & PRs welcome: https://github.com/bisman-automations/ha-daily-routine
