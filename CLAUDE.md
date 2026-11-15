# Daily Routine: notes for Claude and other coding agents

Home Assistant custom integration (`daily_routine`) for scheduled household routines. Installed through HACS. Read this before changing anything.

## Layout

- `custom_components/daily_routine/`
  - `coordinator.py`: scheduling, safety checks, execution, notifications, storage, people and notify helpers.
  - `routines.py`: validation and normalisation of routine data (`normalize_routine`). Every stored routine goes through here.
  - `const.py`: trigger names, storage key, limits.
  - `schema.py`: the only place that imports the schema library. Always `from .schema import vol`, never `voluptuous` or `probatio` directly.
  - `panel.py`: sidebar websocket API (`daily_routine/...` commands) and the overview payload. Reads use `@require_access`, changes `require_admin`.
  - `access.py`: who may see Daily Routine (admins plus the people an admin picks), the same model as TV Mgmt. The pause switch and the actions call `async_ensure_allowed`.
  - `runtime.py`: the coordinator is the primary entry's `runtime_data`; use `get_coordinator(hass)` outside the entry. Actions are registered once in `async_setup`. Background work goes through the coordinator's `_spawn` (entry-owned tasks).
  - `devices.py`: hub device and one service device per routine.
  - `sensor.py`, `switch.py`, `select.py`: per-routine status sensors, the pause switch, and the House mode.
  - `progress.py` (step labels), `undo.py`, `lived_in.py`, `timeline.py` (Today), `voice.py` (Assist): 1.22.0, see Conventions.
  - `frontend/daily-routine-panel.js`: the whole sidebar UI, one file, no build step.
- `tests/`: pytest (Python backend) and `test_panel_frontend.mjs` (Node tests of the panel with a stubbed `HTMLElement`).
- `README.md` is user documentation. `CHANGELOG.md` is the release history.

## Checks

Run the shared checks at the end of this file, plus:

```
mypy custom_components/daily_routine
node --test tests/test_panel_frontend.mjs
```

CI runs both (mypy on the newest Home Assistant). The integration depends on
`frontend`, so the tests need `home-assistant-frontend`; CI installs it with
the `core-requirements: frontend` input, and locally `pip install
home-assistant-frontend==<the version in
homeassistant/components/frontend/manifest.json>`.

## Conventions

- Routine data is validated in `routines.py`. Add new fields there first, then the panel draft mapping (`_draftFromRoutine` / `_draftToRaw`), then tests.
- Safety rules are fail-closed. Unknown or unavailable people and sensors block a run, and conditions are re-checked before each step. Don't weaken these without asking.
- Calendar conditions (`kind: calendar`) are checked from `_calendar_days`, today's event titles read with `calendar.get_events` at start, every 30 minutes, when the calendar entity changes, and once on a new day. Unread or unreadable calendars fail closed. `_calendar_tried` keeps a status read from asking again in a loop.
- `skip_next` and `paused_until` are routine fields but change through `async_skip_next` / `async_pause_until` (no reload). `skip_next` is consumed only when a scheduled run would really go ahead; `paused_until` is checked before conditions, and a turn-off companion follows its routine's. `random_minutes` shifts time and sun runs by `random_shift` (from the routine id and date, so restarts keep the day's time).
- Actions in the panel's `ACTIONS` list can ask for `fields` (merged into `data`) and a `voice` (a `tts` entity as the target, for `tts.speak`).
- Waiting for conditions: a scheduled run blocked by its conditions sets `pending_date` (persisted, so a restart resumes it through `_resume_waiting`; `async_reload` keeps it) and runs as soon as `_evaluate_pending` finds the conditions met. The routine's `wait` (`day`, `until` with `wait_until`, `no`) decides when `_wait_ends`; `no` skips at once. A scheduled run stopped by `conditions_failed_mid_run` restores `last_run_date` and waits again, so it runs from the start later that day.
- Failed steps (1.18.0): `_run_steps` runs a list of `(index, action, only)`; `_run_service_step` calls a step with several devices once as a group and, if that fails, device by device (`_call_with_retries`), so the ones that answer still change. Every call is under `step_timeout_seconds` except `UNLIMITED_DOMAINS` (scripts). Services in `DESIRED_STATE` are then checked for `verify_seconds` and sent once more to devices that didn't change. A `_Failure` names the devices; the routine's `on_step_error` (`stop`, the default, or `continue`) decides whether the rest runs. `_finish_run` keeps the failures in `_retry` (memory only) for `async_try_again` (the card's Try again, `daily_routine/routine/try_again`, and the Companion app button `DAILY_ROUTINE_TRY_AGAIN_<id>`, checked with `async_allows_context`) and plans up to `RETRY_LATER_TIMES` tries `retry_later_minutes` apart; each re-checks the conditions. Tests turn the check off by default (`tests/conftest.py`).
- 1.19.0: `heads_up_minutes` schedules a `heads_up` handle in `_schedule_heads_up` (from `_schedule_one`) that sends a notification with a `SKIP_ACTION` button (`_on_notification_action` calls `async_skip_next`). `from_date` / `until_date` ("MM-DD", both or neither, may wrap New Year) are part of `_allowed_day` via `routines.in_dates`; `_next_run_datetime` searches up to a year ahead when they're set. `remember_states` / `put_back_states` are real actions (registered in `async_setup`, described in `services.yaml`) over `coordinator.memories`, saved in the Store; the panel's ACTIONS offers them (`domains`, `untargeted`). Light steps' optional `transition` (minutes in the panel, seconds in data) lengthens the step check by the fade.
- 1.20.0: triggers are moments. A waiting run (`pending_date`) is dropped with `trigger_ended` when `_trigger_holds` says what started it is over; `wait: minutes` waits `wait_minutes` from `pending_since` (persisted). The panel's `EVENT_TRIGGERS` default new routines to `wait: no`. Presence edges use `_everyone_away` (None while a tracker is unknown: no decision, no change), per-person triggers keep each person's last known place (`rt["places"]`), value triggers compare with the last number seen (`rt["last_value"]`), and state triggers ignore unknown/unavailable unless chosen, comparing with `seen_state`. `cooldown_minutes` (`_cooling_down`, from `last_run`) skips scheduled runs with reason `cooldown`; manual runs aren't held back.
- 1.21.0: `linked.py` groups a step's entities into devices (`linked_groups`): devices sharing an identifier or connection, from different config entries, each with exactly one of the step's entities. `_run_service_step` runs unlinked entities as before (`_run_on_devices`) and each linked group through `_run_linked`: one entity at a time, in `_integration_order` (the routine's `integration_order`, else `settings.integration_order`, then local before cloud by the integration's `iot_class`), until the device shows the change (any of its entities, `_wait_for_state(any_of=True)`; covers still opening or closing get `MOVING_SECONDS`). `_plan_action` trusts a group's first reporting entity. `daily_routine/integrations` lists integrations with linked devices for the panel.
- 1.22.0: runs show progress. `_run_steps` wraps `_run_step_list` and sets `rt["progress"]` ({step, total, label from `progress.step_label`, started, step_started, until}) before each step, cleared in `finally`; `get_status` sends it while running. `daily_routine/subscribe` pushes `panel._status` (statuses, mode, lived_in, `run_log.count`) at most every `PUSH_SECONDS` on each `_notify_listeners`; the panel uses it instead of polling (`_live`). `routine/run` calls `async_start_run`: checks first, answers `{started, ran, reason}` at once and spawns the run (`already_running` refused). `more_triggers` (routines `_more_triggers`, `TRIGGER_FIELDS`, `MORE_TRIGGER_KINDS`, max 4) become alias routines `<id>~<n>` in `_build_aliases`, watched like routines; `_execute_routine(alias)` runs the main routine with `rt["started_by"]`, and `_source()` picks the alias for `_check` / `_trigger_holds`. New steps: `run_routine` (`_run_other`, `_CHAIN` contextvar refuses loops), `wait_until` (`_wait_until_state`, `SECONDS_PER_MINUTE`), `set_mode`; any non-delay step may have `only_if` (a condition). The text form writes these as JSON lines. Conditions `mode`, `workday`, `weather` (`_weather_ok`); trigger `mode_changes` (`modes`). `coordinator.mode` is stored, `async_set_mode` runs `mode_changes` routines and re-checks waiting ones; `select.py` is the House mode entity; `set_mode` / `undo_routine` actions. Undo: `_keep_for_undo` snapshots (`undo.py`) the targets of service steps when a run starts; `async_undo` within `UNDO_FOR`. `lived_in.py` (settings `lived_in`, one-minute tick) switches the picked lights while the mode is in its modes and in its window, and turns off only `ours`. `timeline.today` feeds `daily_routine/today` (Today tab). `voice.py` registers Assist sentences (conversation triggers, `after_dependencies: conversation`; tests need `core-requirements: conversation`).
- 1.24.0: planned triggers (`routines.PLANNED_TRIGGERS`: `alarm`, `calendar_event`, `cheapest`) are planned by `_next_planned` from the pure `planned.py` (`alarm_time`, `event_starts`, `price_slots`, `cheapest_start`); `_watch_planned` re-plans when the alarm or price sensor changes, `_refresh_upcoming` keeps three days of events in `_upcoming`, and a day's cheapest slot is kept in `rt["cheapest"]`. `forecast` is repeating and edge-triggered (`_check_forecasts` every `FORECAST_EVERY`, `rt["forecast_hit"]`, `planned.forecast_matches`; `forecast_hours`, `watch_states` kinds, `value_threshold` a temperature). Ask first (`ask_first`, `ask_minutes`, `ask_default`): `_ask_first` runs only for scheduled runs, sets state `asking` and `ask_until`, sends Run/Don't run (`ASK_RUN_ACTION` / `ASK_SKIP_ACTION`) and waits on a future that `async_answer` (`routine/answer`) resolves; skips record `asked_skip` / `no_answer_skip`. A `together` step (`TOGETHER_KINDS`, 2 to `MAX_TOGETHER`) runs its steps with `asyncio.gather` through `_run_step_list(track=False)`; the panel edits it as steps with `withPrev` (`flatSteps` / `groupSteps`). `async_copy_to_rooms` (`routine/copy`, admin) remaps area targets with `_moved_action`. `reliability.DeviceStats` (Store `daily_routine.devices`, 30 days) records ok/retried/failed per entity from `_run_on_devices`; Activity sends `devices`. `settings.editor` (`simple`/`full`) is the shared default; the panel's `_editorMode()` prefers the session switch, then `localStorage` `daily-routine-editor`. The simple editor (`_renderSimple`, `SIMPLE_SECTIONS`) and the full one share the section builders (`_whenFields`, `_stepsBody`, …). Node tests open the full editor (`_editorOverride`) and steps (`openSteps`). `frontend/daily-routine-card.js` is the `daily-routine-card` dashboard card, added once with `frontend.add_extra_js_url`; run, skip, try again, undo, set mode and answer are `require_access` so the card works for allowed users.
- 1.25.0: run replays: `_run_steps` starts `rt["trace"]`; `_run_step_list` (track=True) opens a line per step (`_trace_open`), `_trace_mark` notes skips, `_trace_close` adds seconds, result (ok, skipped, failed, stopped), a note and the end state of up to `TRACE_DEVICES` devices; `_record` moves the trace onto the history entry and the run log event (`steps`, at most `run_log.MAX_STEPS`); the panel's `replayHtml`. `PRESS_TRIGGERS` (`tag`: `watch_item` is the tag id, `tag_scanned` events; `button`: an `event.` entity, `watch_states` the event types, none: all) are watched by `_watch_press`; a press after unavailable or a restore isn't one. `away.py` (`coordinator.away`): vacation (`settings["vacation"]`, set by `vacation/start`, ended by `vacation/end` or just after midnight past `until`; puts back the mode if still Vacation, lived-in lights, the summary and routines still paused through that day) and the daily summary (`settings["daily_summary"]`, `summary_text`, `async_track_time_change`, `summary/send`); `async_set_settings` keeps `vacation` and reads `daily_summary`. "Make it look like this" is a `scene.apply` step (`data.entities`; the panel's kind `scene`, `captureLook` from `hass.states`; `_keep_for_undo` includes those entities). `wait_until` takes `below` / `above` (instead of states) and `for_minutes` (`_wait_until_state(hold=)`). Sharing is panel-only: `shareCode` (`DR1:` + base64 JSON with the rooms' names) and `_importRoutine` (rooms matched by id, then name).
- 1.26.0: `habits.py` (`coordinator.habits`, Store `daily_routine.habits`): state changes of `HABIT_DOMAINS` made by hand (`_by_hand`: a user context, or no parent while `coordinator.busy()` is false) are kept `KEEP_DAYS`; the pure `suggest` turns the same change in a `WINDOW_MINUTES` window on `MIN_DAYS` of `LOOK_BACK_DAYS` into an idea, minus `covered()` (what enabled routines do) and `dismissed` (`daily_routine/idea/dismiss`, admin). The overview sends `habits`; the panel's `_habitIdeas` / `_habitCards`. `energy.py`: `_note_power` reads the power sensors on the devices a run changes (`power_sensors`, `read_watts`) into `rt["power_before"]`; `_finish_run` spawns `_read_power_after` (`POWER_SETTLE_SECONDS`), kept in `coordinator.power` (stored, `KEEP_RUNS`), `get_status` sends `power` (`summary`). A service step's `rooms` (`occupied` / `empty`, only with `area_id`) goes through `_only_rooms` (`room_occupied` by `OCCUPANCY_CLASSES` binary sensors; None is left alone; nothing left skips the step). Step `volume_ramp` ({entity_id, from, to, minutes}) runs `_ramp_volume` in `RAMP_STEPS`. `day_types.py`: `settings.day_types` [{name, calendar, words}] (`clean_day_types`), today's kind from `_calendar_days` (`coordinator.day_type`, None while unread), condition `day_type` {types}, `NORMAL_DAY`; their calendars are read with the conditions'. A routine's `group` (≤ 40); `async_pause_group` / `daily_routine/group/pause` (admin). The overview's `integrations` (`panel.entity_integrations`: entity id → integration name) labels every `select option` and `.checks label` in `_labelIntegrations` (before `_addSearches`). The panel's `LIBRARY` (by `topic`) shows under **+ New routine**.
- 1.27.0: `coordinator.explain` (`daily_routine/routine/why`, access) lists reason codes (the skip checks of `_execute_routine`, then `_check`), `next_run`, `last_skip` (run log) and `unavailable`; the panel's `_whyHtml` words them with `REASON_LABELS`. A routine's `skip_modes` skips scheduled runs (`skip_mode`). Sun triggers' `not_before` / `not_after` ("HH:MM:SS", None for other triggers) clamp each day's time in `_clamp`. `weekly.py` (`settings.weekly_report` {enabled, weekday Monday 0, time}, `weekly_text`) is sent by `AwayHelper._send_weekly` (`coordinator.send_weekly`, `daily_routine/weekly/send` admin). Habit events note `d` (sun below the horizon) and `h` (someone home); `suggest` sets `when_dark` / `someone_home` at `CONDITION_SHARE`, and the panel adds those conditions to the idea's draft (`spec.conditions` in `_applyTemplate`). Step `wait_cheaper` ({entity_id, minutes 15–720}) waits in `_until_cheapest` (`planned.price_slots` / `cheapest_start` from the current slot). `sensor.py` adds `KindOfDaySensor` (unique id `daily_routine_kind_of_day`). `get_status` and `timeline` send `group`; the card's `group` option filters by it and pauses it for admins.
- 1.28.0: step `briefing` ({voice, speaker, weather, calendars, day_type}) speaks `briefing.briefing_text` with `tts.speak` (`_speak_briefing`: the weather's state, today's high from the forecast, today's events from `_read_events`). Condition `forecast` ({entity_id, conditions, below, hours, when coming/not_coming}) reads `_forecast_cache`, filled by `_check_forecasts` for `_forecast_entities`; unread fails closed. Kinds of day may have `person` (`day_types.for_person`); `coordinator.day_type(now, person)`, and the `day_type` condition's optional `person`. `async_undo_recent` (`daily_routine/undo/recent`, access) undoes every undoable run of the last minutes, newest first; the overview's `can_undo_recent`. `holds.py`: `settings.holds` [{calendar, words}], `active_hold` over `_upcoming` (their calendars are read by `_refresh_upcoming`); scheduled runs skip with `calendar_hold:<title>` unless the routine's `ignore_holds`; overview and status send `hold`. `DeviceStats.score` (30 days, a retry counts half) is the status's `reliability`. Groups share as `DR2:` (`groupShareCode`, `_importGroup`, each saved as a new routine).
- 1.29.0: step `check_open` ({entity_id, stop}; `checklist.CHECKABLE` domains, `left_open` / `checklist_text`, sent with `_notify`; nothing sent when all shut; `stop` ends the run with `error:check_open`). `quiet_hours.py`: `settings.quiet_hours` {enabled, from, until}; `_run_step_list` skips `is_loud` steps (tts, media_player except `QUIET_SERVICES`, `volume_ramp`, `briefing`) while `in_quiet`, unless the routine's `loud_ok`. `guests.py`: `settings.guest_wifi` {ssid, password}; `guest_card` sends it in the overview and `_status` only while the mode is `guests` (panel Today tab, the card). `run_log.daily_counts` (every day, zeros included) feeds `daily_routine/routine/history` (access; the card's **History**, `historyChart`). `timeline.tomorrow` (via `coordinator.next_run_after`, at most `MAX_RUNS_A_DAY` per trigger, step labels from `progress.step_label`) feeds `daily_routine/plan/tomorrow` (access; Today's **See tomorrow**, `_tomorrowHtml`). Library: `lib_sunrise_alarm` (`useAlarm`), `lib_leaving_check` (`useOpenings` → `_openings()`), `lib_laundry` (`wOnValue` copies the trigger's sensor), through `_libraryStep`.
- 1.30.0: lived-in `pattern` (`random` / `last_week`: `lived_in.replay_wanted` over `habits.events` from a week ago, `_replay`; None falls back to random). `run_log.needs_attention` (14 days, ≥ 3 tries, half or more failed or skipped) is the overview's `attention` (Routines tab, `attentionHtml`). `coordinator._who` (set by `_note_who` from presence triggers, by main routine id) fills `{who}` in service data (`_with_who`). Step `check_battery` ({below}; `battery.low_batteries` over sensors and binary sensors with device class battery, `_notify`). Routine `hot_shift` ({entity_id, above, minutes}; `weather_shift.shift_minutes` on `_forecast_cache` in `_next_run_datetime` through `_hot`; `_forecast_entities` reads those weathers and `_check_forecasts` reschedules when `rt["hot_moved"]` changes). Step `tick_list` ({items, minutes, on_timeout}; `_wait_ticks` puts `rt["ticklist"]` in the status and waits on `tick_done`; `async_tick_item` / `daily_routine/routine/tick` (access) from the card and panel, `tickListHtml`). `energy.month_energy` (30 days of `coordinator.power`, `KEEP_RUNS` 60) is the overview's `energy` (Activity, `energyHtml`). Library: `lib_welcome_announce`, `lib_batteries`, `lib_bedtime_check`.
- Value triggers (`value_above` / `value_below`) fire on a crossing, not on every reading. Turn-off levels create a companion routine named `<id>_off` through `_companion_for`.
- Panel tests stub the DOM. When you change a template or an editor field, add or update a Node test.
- Keep UI wording plain. Show names, never entity ids, in the panel. Actions come from the `ACTIONS` list in the panel, each with a plain label. Add new actions there, and keep "Other (advanced)" for anything not listed.

## Open decisions (ask before acting)

- None. The license is MIT (chosen by the user on 2026-10-18).

<!-- BEGIN bisman-automations shared rules -->
<!-- This block is the same in every bisman-automations repo. Edit it in
     bisman-automations/.github (templates/CLAUDE.shared.md) and copy it to
     every repo; the Validate workflow's "standards" job checks it matches. -->

## Shared rules (every bisman-automations repo)

### Releases

- The version lives in `custom_components/<domain>/manifest.json`. Anything
  else that repeats it is called out in the repo-specific notes below and is
  bumped in the same commit.
- Versions follow [Semantic Versioning](https://semver.org): patch for fixes,
  translations, docs and CI; minor for new features, options or entities;
  major when something users rely on is removed or renamed (entities,
  services, events, options) or setup has to be redone.
- Check what is released before choosing a version:
  `git ls-remote --tags origin | awk -F/ '{print $3}' | sort -V | tail -1`
  (`sort -V`: plain sorting puts `v1.10.0` before `v1.9.0`). Never add
  changes to a released version. If the manifest version has no tag yet,
  add to its CHANGELOG section instead of bumping again.
- Every push to `main` ships as a release. In that same commit, named
  `Release X.Y.Z`, bump the manifest version and write the notes directly
  under a new `## [X.Y.Z] - YYYY-MM-DD` heading in `CHANGELOG.md` (Keep a
  Changelog: Added, Changed, Deprecated, Removed, Fixed, Security), with
  that version's compare link at the bottom. There is no `## [Unreleased]`
  section and no `[Unreleased]:` compare link. New work is not collected
  under a holding heading and moved later; the release commit is where the
  heading and the entries are written. The standards check fails if a
  `## [Unreleased]` heading is present.
- Ask Donavan before every push. Push fast-forward only: never force-push,
  rebase or amend anything already pushed, or rewrite history in any other
  way. Rewriting history orphans the release tags (it broke drafting in
  ha-button-actions).
- Pushing to `main` runs **Draft release**, which creates or refreshes a
  draft `vX.Y.Z` with that version's CHANGELOG section as the notes, the
  commits since the last release and `<domain>.zip`. **Donavan publishes
  the draft himself**, and publishing creates the tag. Never create or push
  tags, publish releases, or edit published releases.
- Publishing runs **Release check**: the tag must match the manifest version
  and the CHANGELOG, and be on `main`. If it fails, fix it with a new patch
  release, not by moving the tag.

### Commits

- Author commits as `Donavan Becker <donavan.becker@icloud.com>` (set
  `git config user.name` / `user.email` in the clone). HACS's
  default-repository check needs the submitter to have a fair share of the
  commits. Credit an AI assistant with a `Co-Authored-By:` trailer if you
  like, never as the author.
- Subject lines are plain, imperative and say what changed for the user
  ("Show Pico remotes as unavailable when Lutron isn't running"). No
  `fix:`/`feat:` prefixes. Release commits are "Release X.Y.Z".
- Commit and push only when asked.

### CI (shared workflows)

- `.github/workflows/` holds four thin callers of the reusable workflows in
  [bisman-automations/.github](https://github.com/bisman-automations/.github):
  `test.yml` (lint, frontend, pytest on the oldest supported and the newest
  Home Assistant, plus the upcoming one as information only), `validate.yml`
  (hassfest, HACS, standards), `draft-release.yml` and `release-check.yml`.
  Don't add jobs to the callers. Use the shared workflow's inputs, or change
  the shared workflow so every repo gets the improvement.
- `requirements_test.txt` leaves `pytest-homeassistant-custom-component`
  unpinned (newest Home Assistant) and pins ruff (and mypy where used).
  `requirements_test_min.txt` pins the harness release for the minimum in
  `hacs.json`: the newest harness release of that month. CI fails if they
  drift apart.
- Raising the minimum Home Assistant means changing, together: `hacs.json`,
  `requirements_test_min.txt`, `python-min` in `test.yml` (3.13 up to
  2026.2, 3.14 from 2026.3), the README's Home Assistant badge, and a
  CHANGELOG entry.
- Home Assistant 2026.3 and newer need Python 3.14 (3.14.2+).
- The published hassfest rejects `categories` in `manifest.json` and Pillow
  in `requirements`. Leave both out.
- Dependabot (`.github/dependabot.yml`) proposes action and pinned-tool
  updates weekly. It ignores the test harness on purpose.
- Test and Validate also run every Thursday, the day after Home Assistant's
  betas come out. The **next** test job (newest pre-release harness) is
  information only: when it fails, fix the cause before that release ships.

### Before saying work is done

Run from the repo root, in a Python 3.14 venv with `requirements_test.txt`
installed:

```
ruff check .
ruff format --check .
pytest
```

Also run `node --test tests/*.mjs` when the repo has Node tests, mypy when
the repo-specific notes list it, and the tests in a second venv with
`requirements_test_min.txt` when a change could behave differently on the
oldest supported Home Assistant. The standards check (also in CI) is
`python <path to bisman-automations/.github>/scripts/check_repo.py .`.

### Code and docs

- Support every Home Assistant from the `hacs.json` minimum to the newest
  release. Check for deprecations in the newest release and the beta.
- Keep entity IDs, unique IDs, service names and event names stable. When
  one has to change, migrate it and say so under Changed in the CHANGELOG.
- `strings.json` and `translations/en.json` hold the same keys. Home
  Assistant reads only `translations/`, so change both in the same commit.
- Never put personal details in the repo (README, CHANGELOG, code, tests,
  screenshots): no names, addresses, account or device identifiers, or real
  entity IDs. Use placeholders such as `light.living_room`.
- `README.md` is user documentation with the standard header (badges, HACS
  and "add integration" My buttons) and sections Installation, Setup, ...,
  Troubleshooting, Development, License. Use plain language and show names,
  not entity IDs, wherever the UI does.
<!-- END bisman-automations shared rules -->
