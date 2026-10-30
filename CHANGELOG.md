# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.14.1] - 2026-10-30

### Changed

- **Install from the Marketplace.** The README now explains how to install from the Marketplace that replaces HACS in Home Assistant 2026.11, with its own button. The HACS steps stay for Home Assistant 2026.10 and older, and an install you already have moves over by itself.

## [1.14.0] - 2026-10-30

### Added

- **Who can see Daily Routine,** under **Settings** in the sidebar. Admins always can; an admin checks anyone else who should. Someone who isn't allowed gets a page saying so.

### Changed

- **Only admins see routines until an admin lets someone else in.** Routines show who's home and when, and before this release anyone with a Home Assistant login could read them in the sidebar. If people who aren't admins used the sidebar, check their names under **Settings → Who can see Daily Routine**.
- **The pause switch and the actions need access too.** Admins, the people allowed in, and automations can use them.
- **The Run routine and Reload routines actions say what went wrong:** when Daily Routine isn't running, or the routine id is unknown, the action fails with a message instead of only writing to the log. They stay available while Daily Routine is turned off.
- The integration icon has a dark-mode version.
- The README follows the same layout as the other bisman-automations integrations: a button that opens it in HACS, a button that starts setup, and troubleshooting steps for debug logs and diagnostics.
- Each release now includes `daily_routine.zip` for installing without HACS.
- The changelog no longer keeps an Unreleased section, and the agent notes follow the updated shared release rules.

### Fixed

- Setup and options screens show their labels and descriptions instead of raw keys: the English text is now shipped where Home Assistant reads it.
- Linking a routine to a child device on newer Home Assistant no longer reads that device's connections, which Home Assistant deprecates.
- **A failed start no longer leaves routines half running.** If setup stopped part way, schedules and watchers it had started kept going and the next attempt couldn't start. Now everything is stopped, and the retry starts clean.
- **Editing a routine's entry while Daily Routine is turned off** shows a message instead of an error.
- Work a routine starts in the background (notifications, catch-up runs, saving) now finishes or stops when Daily Routine is unloaded, instead of running on afterwards.

## [1.13.0] - 2026-10-25

### Added

- **Activity tab:** statistics, activity and analytics in one place, for today, the last 7 days or the last 30 days. It shows triggered, succeeded, failed and skipped runs, the success rate, runs per day, why runs failed, why runs were skipped (with the conditions that were not met), each routine's last run and last failure, and recent events filterable by outcome.
- **Needs attention:** routines whose devices or lists are missing right now, routines that failed several times in a row, and routines with no runs in the period.
- **Run history is kept:** every run, skip and failure is saved with Home Assistant for 30 days, so the Activity tab survives restarts. A deleted routine's history is removed with it.

### Fixed

- **Diagnostics:** person and notify entity ids inside routines are replaced in the download, not just in the settings.

## [1.12.1] - 2026-10-24

### Fixed

- **Routines now appear under Linked devices:** a routine was linked to its device, shown as *Connected via*, but did not appear under that device's *Linked devices* the way other integrations' services do. A routine's service now shares the linked device's identity, so it is listed there. On Home Assistant versions that don't allow this, it stays *Connected via* the device. When several routines use one device, only one of them can be listed under *Linked devices*.

## [1.12.0] - 2026-10-23

### Added

- **Timing settings:** a new **Timing** card on the Settings tab sets four values for the whole integration:
  - **Wait for a to-do list** (default 30 seconds, 0 to 300): a to-do step waits this long for a list that has not loaded yet, such as an iCloud list after a restart. A dry run never waits.
  - **Health check every** (default 15 minutes, 1 to 60).
  - **Health alert delay** (default 60 seconds, 0 to 600): how long a problem must last before it is announced.
  - **Startup quiet period** (default 5 minutes, 0 to 60): no health alerts for this long after Home Assistant starts, while devices are still loading.

### Changed

- **Health cards clear themselves:** a health notification in Home Assistant's notifications is removed when the next check finds the problem gone, and when its routine is deleted. Only the notification in Home Assistant is removed; phone notifications stay where they were sent.

## [1.11.0] - 2026-10-23

### Added

- **Due date check:** a complete or remove step can act only on items due today, within 1, 7 or 21 days, or on any date. Choose whether overdue items, and items with no due date, are included too. Set it in the editor, or add `due=1 overdue=yes undated=no` to the one-line step. Steps without a check behave as before.

## [1.10.0] - 2026-10-23

### Added

- **Device-state trigger:** "When a device changes to a value" runs a routine when a device moves into one of the states you name, such as `on`, or `on, off`. It runs once per change, not again while the device stays in that state.
- **Hold time:** optionally, the device must still be in one of those states after a number of minutes. A change to another state in the meantime cancels the run.
- **Restart policy:** if the device changed while Home Assistant was stopped and is still in one of its states, the routine runs once after startup.
- **To-do steps:** a routine can add, complete or remove an item on a to-do list, written `todo add | todo.groceries | Empty dishwasher`. Adding never creates a second open item with the same name, and completing or removing acts only on an open item.
- **Checked-off trigger:** "When an item is checked off a to-do list" runs a routine when a named item (or any item) is checked off. Items already done when the routine is saved do not count. Items checked off while Home Assistant was stopped run it once after startup.
- **Health notifications:** "Notify me when it needs attention" sends one message when a device or to-do list a routine needs stays missing or unavailable for a minute, and another only after it has recovered and failed again. Checks run when the device or list changes, and every 15 minutes.
- **Dry run** shows what each step would do, including to-do steps, without changing anything.
- **Health warnings:** a routine card warns when a device or to-do list it needs is missing or unavailable.
- **Recent runs** show what a to-do step did, for example "already on the list".
- **To-do rules:** a new **To-dos** tab lists rules that add, complete or remove a to-do item when a device or sensor changes to a state you choose. Each rule is a routine, so it has pause, conditions, run history, retries, notifications, dry run and backup.
- Suggested states come from the device's history for the last 30 days, and any text is accepted.

### Changed

- Export and import include to-do rules.

## [1.9.1] - 2026-10-23

### Fixed

- Routines now appear under their own entries in Settings → Devices & services → Daily Routine, not under "Devices that don't belong to a sub-entry". A routine's device and status sensor belong to its entry.

## [1.9.0] - 2026-10-23

### Added

- **Recent runs:** each routine card lists its last five runs: when it ran, was skipped (and why), or failed. The last 20 are kept until Home Assistant restarts.

### Changed

- **Conditions list:** the "only when everyone is away" and "only when the sensors have been quiet" checkboxes are now entries in one Extra conditions list. Add who is home or away (everyone or anyone, at or away from a place, in either direction), sensors quiet or active for N minutes, or a device's state. Every condition must hold, or any one, as you choose.
- Routines saved before this change convert on load. Routines that set "any one" together with the old checkboxes now count those converted conditions in that choice, so check them.

## [1.8.0] - 2026-10-23

### Added

- **Per-person triggers:** "When a person leaves a place" and "When a person arrives at a place" fire for the people you pick, one at a time, instead of waiting for everyone.
- **Full editor in Settings:** every routine setting, including people, sensors, quiet time, active hours, extra conditions, linked device, catch-up, retries and notifications, can be changed under Devices & services. Steps and extra conditions are one plain line each.
- **Duplicate:** copy a routine from its card and change the copy.
- **Templates:** lights off when someone leaves, close all curtains at bedtime, lights off at sunrise.
- **Export and import:** save every routine to a file from Settings, and load it on this or another Home Assistant. Routines that fail the checks are listed and the rest still load.

### Changed

- **Catch-up** is a choice of windows (15 minutes to 3 hours) instead of a number of minutes.
- The background refresh asks only for each routine's run state, and reloads the routines when they change.

### Fixed

- The extra-condition picker no longer sends its internal choice flag to Home Assistant.

## [1.7.0] - 2026-10-23

### Added

- **Extra conditions: all or any.** With more than one extra condition, choose whether all of them must be true or just one.
- **Extra conditions: state names.** Locks, covers, people, trackers, media players, climate and on/off devices offer their states by name (Locked, Unlocked, Open, Home, Away and so on). Choose Other… to type a state yourself.

## [1.6.1] - 2026-10-23

### Fixed

- The dashboard loads and saves quickly again. Phone names were read through a deprecated device attribute, which made each dashboard load slow and logged a deprecation report on every call. The overview now builds in under a millisecond on a home with 800 entities and 200 devices, and a routine save takes a few milliseconds.

## [1.6.0] - 2026-10-23

### Added

- **Integration entries:** each routine appears under Daily Routine in Settings → Devices & services. Edit its name, on/off, trigger, time, place and steps there. Deleting the entry deletes the routine. (#5)
- **Diagnostics:** Download diagnostics from the integration page. Phone names and hidden profiles are redacted. (#4)
- **Loading:** a skeleton placeholder on first load and a progress bar while the dashboard refreshes or saves. (#1)
- Added the MIT license, which HACS needs before the integration can be listed in its catalog.

### Changed

- **Catch-up after a restart:** waits for Home Assistant to finish starting, then retries each minute while a condition blocks the run, until the catch-up window closes. (#2)
- **Faster dashboard:** the overview is built in one pass over the registries, the panel caches its lookups, and a refresh that brings no change no longer redraws the page. (#3)

## [1.5.0] - 2026-10-18

### Added

- **Places:** leave and arrive triggers, and "only when everyone is away," now work with any Home Assistant zone, such as Work. Pick a place in the editor. Home stays the default, so existing routines behave the same.

### Changed

- Someone at another place now counts as away from Home for leave-home routines.

## [1.4.0] - 2026-10-17

### Added

- **Sensor triggers:** run when a numeric sensor (for example humidity) rises above or falls below a level. Staying above the level does not re-run the routine.
- **Turn-off level:** for "above" triggers, an optional level creates a companion "(off)" routine that turns the same devices back off. Lights, fans, covers and locks are supported.
- **Daytime windows:** any routine can be limited to a start and end time, and windows can cross midnight.
- **New templates:** bathroom fan on humidity, fan off when the room is empty, night lights at sunset, open all curtains, lock up at bedtime, lock up when everyone leaves, and welcome home lights.
- **Notifications:** choose any number of phones or devices, shown by name rather than by entity id.

### Changed

- Clearer wording for the people and notification settings.
- Actions are picked by name (for example "Turn off lights") instead of typing service ids. "Other (advanced)" still allows any service.
- Previews and labels show the same plain names.

## [1.3.0] - 2026-10-17

### Added

- Tabs: **Rooms**, **Routines** (search), and **Settings** (administrators only).
- **People in routines:** choose which person profiles appear when building a routine. Turned-off profiles (for example a shared display) are hidden from the picker. Routines that already use them keep them.
- **Notifications:** optionally send routine runs, skips and failures to a notify service, such as a phone, in addition to Home Assistant notifications.

## [1.2.0] - 2026-10-17

### Added

- **All routines** view with search. Matches name, room, linked device, trigger, days, and status, and each word narrows the list.

### Changed

- Only people with location tracking (a device tracker) can be checked by a routine. Other people are listed but disabled.
- Saving a routine that includes a person without tracking is refused with a clear message.
- "Select everyone with tracking" shortcut in the editor. The "everyone is away" template uses only tracked people.
- Works with Home Assistant 2026.10, which validates with probatio. Earlier versions still use voluptuous.
- Routine sensors on Home Assistant 2026.10 or later include the device name in their entity id. Upgraded routines keep their existing id.

### Fixed

- Fixes for hassfest (config schema), mypy, and the test harness on current Home Assistant (frontend dependency, timer cleanup, registry lookups).

## [1.1.1] - 2026-10-16

### Added

- Each routine is its own service device under Daily Routine, linked to the device it belongs to ("Connected via").
- New **Linked device** picker in the routine editor. Routines without a device link to the Daily Routine device.
- The routine's status sensor and the pause switch attach to their devices.
- **Delete** button on each routine card and in the editor.
- Each routine card shows which device it is linked to.

## [1.1.0] - 2026-10-16

### Added

- **Rooms come from Home Assistant.** Every area is listed automatically, with a **Whole house** card. Nothing to set up in the integration.
- **Routines per room.** Open a room and add a routine. Room routines target the room, so new devices in that room are included automatically.
- **Templates:** open/close curtains, lights off when a room is vacant, lights off at a time, lights off when everyone is away, or blank.
- **Dry run (Test)** shows what a routine would do right now, without calling any service.
- **Pause all routines** from the sidebar or the `switch.pause_all_routines` entity.
- **Room is quiet** trigger: runs once per quiet period when a room's sensors have been quiet for N minutes. It does not fire just because Home Assistant restarted.
- **Extra conditions:** a routine can require any entity to be in a given state.
- **Skips no-op steps:** a "turn off" step does nothing if its devices are already off (and similarly for open/close, lock/unlock).
- **Retries** a failed step up to 3 times.
- **Catch-up** after a restart for missed time-based routines (up to 180 minutes).

### Changed

- **Repeats.** Routines triggered by people or rooms can run every time (for example, each time everyone leaves). Previously these ran at most once a day. Off by default for time-based routines.
- **Failure notifications** are on by default.
- **Waiting** routines expire at midnight and report `conditions_not_met`.
- Routines are stored in Home Assistant storage, not in config entries or JSON options.
- v1.0.0 routines migrate automatically. Old per-routine entries become empty entries titled *Daily Routine (migrated – safe to delete)*.
- Python tests use the Home Assistant test harness and cover scheduling, safety, room discovery, migration, pause, retries, and setup.
- Sidebar panel smoke tests run under `node --test`.
- Minimum Home Assistant version: 2026.9.0.

### Fixed

- Clear timers follow live sensor changes and fire when the quiet period completes. They are held in memory and are not restored from storage after a restart.

## [1.0.0] - 2026-10-16

### Added

- Initial release: one config entry per routine, JSON options, status sensors, presence and time triggers, safety conditions, delay steps, and a sidebar panel.

[1.14.1]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.14.0...v1.14.1
[1.14.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.13.0...v1.14.0
[1.13.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.12.1...v1.13.0
[1.12.1]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.12.0...v1.12.1
[1.12.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.11.0...v1.12.0
[1.11.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.10.0...v1.11.0
[1.10.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.9.1...v1.10.0
[1.9.1]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.9.0...v1.9.1
[1.9.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.8.0...v1.9.0
[1.8.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.7.0...v1.8.0
[1.7.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.6.1...v1.7.0
[1.6.1]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.6.0...v1.6.1
[1.6.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/bisman-automations/ha-daily-routine/releases/tag/v1.0.0
