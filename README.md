# Daily Routine

<p align="center">
  <img src="custom_components/daily_routine/brand/icon.png" alt="Daily Routine logo" width="128">
</p>

**Room-by-room, safety-aware daily routines for Home Assistant.** Every room you already have in Home Assistant appears in the sidebar. Open a room, add a routine, done. Nothing to set up twice.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/bisman-automations/ha-daily-routine)](https://github.com/bisman-automations/ha-daily-routine/releases)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bisman-automations&repository=ha-daily-routine&category=integration)

---

## What it does

- **Rooms from Home Assistant.** The sidebar shows every area (room) automatically, plus a **Whole house** card for routines that span the house. Create or rename rooms in *Settings → Areas & zones*, and they appear here.
- **Routines per room.** Curtains open at 07:00 in most rooms and at 08:00 in the bedroom: two routines. Lights in a room turn off once its motion sensor has been quiet for 10 minutes.
- **Whole-house routines.** Lights off when everyone has left and the house has been quiet for 15 minutes.
- **Triggers:** a set time, sunrise or sunset (with offset), everyone leaves, someone arrives, a room has been quiet, or manual only.
- **Safety first.** Unknown or unavailable people and sensors block a run. Conditions are checked again before every step.
- **Waits instead of giving up.** If a run is blocked (someone is still home, or motion is still happening), it waits until the conditions are met and runs once, up to midnight.
- **Runs as often as needed.** Presence and room triggers run every time they happen (for example, every time everyone leaves), not just once a day.
- **Dry run.** *Test* shows exactly what a routine would do right now, without touching any device.
- **Pause switch.** *Pause all routines* stops every automatic run. Manual runs still work.
- **Skips no-op steps.** A "lights off" step does nothing if the lights are already off.
- **Retries and catch-up.** Retry a failed step, and run a missed time-based routine if Home Assistant restarted a few minutes late.
- **Extra conditions.** Run only when an entity is in a given state, for example guest mode is off.
- **Notifications** on run, skip, or failure (failures are on by default).
- **Status sensors** per routine (`idle`, `waiting`, `running`, `skipped`, `error`) for dashboards and automations.

---

## Install

### HACS

1. Click the badge above, or in HACS add `https://github.com/bisman-automations/ha-daily-routine` as a custom repository of type **Integration**.
2. Install **Daily Routine**, then restart Home Assistant.
3. *Settings → Devices & services → Add integration* → **Daily Routine**.
4. Open **Daily Routine** in the sidebar.

Requires Home Assistant 2026.9.0 or newer.

### Manual

Copy `custom_components/daily_routine` into your `config/custom_components/` folder, restart, and add the integration.

### Upgrading from 1.0.0

Your routines are moved into the new storage automatically on the first start. Old per-routine entries become empty entries titled *Daily Routine (migrated – safe to delete)*. Delete them when you are happy with the new setup.

---

## Using the sidebar

1. **Whole house** or a **room card** → **+ New routine**.
2. Pick a template (for example *Open curtains* or *Lights off when vacant*), or start blank.
3. Set **When**, **Do**, and **Options**. Use **Test (dry run)** to check it, then **Save**.

Room routines target the room itself, so lights or covers you add to that room later are included without editing the routine.

Each routine card has **Run now**, **Edit**, **Enable/Disable**, and **Delete**. In the editor, **Linked device** picks the device the routine belongs to.

## Routines as devices

Each routine is its own service under the Daily Routine integration. It is linked to the device you picked (shown as *Connected via* on the routine's page). Routines without a device link to the **Daily Routine** device. Deleting a routine removes its service.

---

## Routine reference

| Field | Meaning |
|-------|---------|
| Trigger | `time`, `sunrise`, `sunset`, `everyone_leaves`, `someone_arrives`, `room_clear`, or `manual` |
| Time / offset | For time and sun triggers (offset in minutes, negative is before) |
| Days | Which days it may run |
| Who | People for presence triggers (or "only when everyone is away") |
| Sensors + quiet for | Motion/occupancy sensors that must stay off for N minutes. Motion restarts the count |
| Extra conditions | Entity + state that must match, for example `input_boolean.guest_mode` = `off` |
| Run every time | Run on every trigger, not once a day (default on for presence and room triggers) |
| Skip steps already done | Skip a step whose devices are already in the target state (default on) |
| Catch up | After a restart, still run a missed time routine within N minutes (up to 180) |
| Retry | Retry a failed step up to 3 times |
| Steps | Service calls (with a room, specific devices, or no target) and waits |
| Notify | On run, on skip, on failure |

### Trigger notes

- **Room is quiet** runs once each time the room's sensors have been quiet for the chosen minutes. It does not fire just because Home Assistant restarted in a quiet room.
- **Everyone leaves / someone arrives** fire on the change itself. With sensors selected, they wait until the room is quiet.

---

## Services

| Service | Description |
|---------|-------------|
| `daily_routine.run_routine` | Run a routine by id. Conditions still apply; the once-a-day rule does not. |
| `daily_routine.reload_routines` | Restart all schedules and watchers. |

```yaml
service: daily_routine.run_routine
data:
  routine_id: kitchen_lights_off
```

Entities:

- `switch.pause_all_routines`: on = all automatic routines paused.
- `sensor.<routine>_status`: one per routine. State is `idle`, `waiting`, `running`, `skipped`, or `error`. Attributes include `last_run`, `last_result`, `next_run`, `block_reason`, `waiting`, and `repeat`.

## Events

- `daily_routine_routine_started`
- `daily_routine_routine_finished` (`result`: `success` or a failure reason)
- `daily_routine_routine_skipped` (`result`: `disabled`, `paused`, `already_ran_today`, `conditions_not_met`, `waiting:<reason>`, …)

Every event includes `routine_id` and `name`.

---

## Safety behaviour

- Missing, `unknown`, or `unavailable` people or sensors → the condition fails and the routine does not run.
- Everyone away means every selected person is exactly `not_home`.
- Sensors are quiet only when every selected sensor is exactly `off`. Any motion restarts the quiet timer.
- The quiet timer starts when Home Assistant sees the sensors go quiet. It is not stored, so a restart starts it again from the live state.
- Conditions are checked again before every step. If they stop holding, the rest of the routine stops.
- Waiting routines expire at midnight and are reported as skipped.
- There is **no force or bypass** option, including for manual runs.
- Pausing and resuming takes effect immediately.
- This is **not a security system**. Test with harmless steps before using locks or alarms.

---

## Development

- Routines live in Home Assistant storage (`.storage/daily_routine.routines`). The sidebar talks to them through websocket commands in `panel.py`.
- Pure routine validation is in `routines.py`. Scheduling and execution are in `coordinator.py`. Room discovery uses the area, device, and entity registries.
- Tests:
  - `pip install -r requirements_test.txt`
  - `pytest` (Home Assistant test harness, covering scheduling, safety, migration, and setup)
  - `node --test tests/test_panel_frontend.mjs` (sidebar panel smoke tests)
- Version lives in `custom_components/daily_routine/manifest.json`. See `CHANGELOG.md`.

---

## Licence and feedback

Open source. Issues and pull requests welcome: https://github.com/bisman-automations/ha-daily-routine/issues
