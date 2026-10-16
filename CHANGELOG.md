# Changelog

All notable changes to the **ha-daily-routine** Home Assistant integration will be documented in this file.

## [1.0.0] - 2026-10-16

### Features
* **Daily Routine integration** (`daily_routine`): one config entry per routine, configured from the UI. Each routine has a status sensor, optional notifications and events.
* **Triggers:** exact local time, sunrise/sunset (with offset), everyone leaves, someone arrives, or manual.
* **Safety conditions:** everyone away (`not_home`) and sensors clear for N minutes. Unknown or unavailable entities fail closed. Conditions are re-checked before every action.
* **Waiting:** a scheduled or presence run blocked by people or sensors waits until the conditions hold (until midnight at the latest) instead of giving up for the day.
* **Delay steps**, persistent notifications, and `daily_routine_routine_started` / `_finished` / `_skipped` events.
* **Services:** `daily_routine.run_routine` (conditions still enforced) and `daily_routine.reload_routines`.
* **Sidebar panel** listing routines with live status and a Run button.
* **Clear timers** follow live sensor changes and fire when the clear period completes. The timer is held in memory only, so it cannot carry a stale time across restarts.

### Project
* Tests (`pytest`, Home Assistant test harness) and a JavaScript panel smoke test (`node --test tests/`).
* `ruff`, `mypy`, and hassfest/HACS validation in CI.
* Minimum Home Assistant version: 2026.9.0.

## [0.0.1]

* Initial placeholder commit.
