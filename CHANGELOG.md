# Changelog

All notable changes to the **ha-daily-routine** Home Assistant integration will be documented in this file.

## [1.1.1] - 2026-10-16

### Routines as devices
* Each routine is its own service device under Daily Routine, linked to the device it belongs to ("Connected via").
* New **Linked device** picker in the routine editor. Routines without a device link to the Daily Routine device.
* The routine's status sensor and the pause switch attach to their devices.

### Sidebar
* **Delete** button on each routine card and in the editor.
* Each routine card shows which device it is linked to.

## [1.1.0] - 2026-10-16

### Sidebar
* **Rooms come from Home Assistant.** Every area is listed automatically, with a **Whole house** card. Nothing to set up in the integration.
* **Routines per room.** Open a room and add a routine. Room routines target the room, so new devices in that room are included automatically.
* **Templates:** open/close curtains, lights off when a room is vacant, lights off at a time, lights off when everyone is away, or blank.
* **Dry run (Test)** shows what a routine would do right now, without calling any service.
* **Pause all routines** from the sidebar or the `switch.pause_all_routines` entity.

### Behaviour
* **Repeats.** Routines triggered by people or rooms can run every time (for example, each time everyone leaves). Previously these ran at most once a day. Off by default for time-based routines.
* **Room is quiet** trigger: runs once per quiet period when a room's sensors have been quiet for N minutes. It does not fire just because Home Assistant restarted.
* **Extra conditions:** a routine can require any entity to be in a given state.
* **Skips no-op steps:** a "turn off" step does nothing if its devices are already off (and similarly for open/close, lock/unlock).
* **Retries** a failed step up to 3 times.
* **Catch-up** after a restart for missed time-based routines (up to 180 minutes).
* **Failure notifications** are on by default.
* **Waiting** routines expire at midnight and report `conditions_not_met`.

### Storage and migration
* Routines are stored in Home Assistant storage, not in config entries or JSON options.
* v1.0.0 routines migrate automatically. Old per-routine entries become empty entries titled *Daily Routine (migrated – safe to delete)*.

### Fixes
* Clear timers follow live sensor changes and fire when the quiet period completes. They are held in memory and are not restored from storage after a restart.

### Project
* Python tests use the Home Assistant test harness and cover scheduling, safety, room discovery, migration, pause, retries, and setup.
* Sidebar panel smoke tests run under `node --test`.
* Minimum Home Assistant version: 2026.9.0.

## [1.0.0] - 2026-10-16

* Initial release: one config entry per routine, JSON options, status sensors, presence and time triggers, safety conditions, delay steps, and a sidebar panel.

## [0.0.1]

* Initial placeholder commit.
