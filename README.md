<p align="center">
  <img src="https://raw.githubusercontent.com/bisman-automations/ha-daily-routine/main/custom_components/daily_routine/brand/icon.png" alt="Daily Routine icon" width="128">
</p>

# Daily Routine

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.9%2B-blue.svg)](https://www.home-assistant.io/)
[![GitHub Release](https://img.shields.io/github/v/release/bisman-automations/ha-daily-routine)](https://github.com/bisman-automations/ha-daily-routine/releases)
[![Test](https://github.com/bisman-automations/ha-daily-routine/actions/workflows/test.yml/badge.svg)](https://github.com/bisman-automations/ha-daily-routine/actions/workflows/test.yml)
[![Validate](https://github.com/bisman-automations/ha-daily-routine/actions/workflows/validate.yml/badge.svg)](https://github.com/bisman-automations/ha-daily-routine/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/bisman-automations/ha-daily-routine)](LICENSE)

**Room-by-room, safety-aware daily routines for Home Assistant.** Every room you already have in Home Assistant appears in the sidebar. Open a room, add a routine, done. Nothing to set up twice.

## What it does

- **Rooms from Home Assistant.** The sidebar shows every area (room) automatically, plus a **Whole house** card for routines that span the house. Create or rename rooms in *Settings → Areas & zones*, and they appear here.
- **Routines per room.** Curtains open at 07:00 in most rooms and at 08:00 in the bedroom: two routines. Lights in a room turn off once its motion sensor has been quiet for 10 minutes.
- **Whole-house routines.** Lights off when everyone has left and the house has been quiet for 15 minutes.
- **Triggers:** a set time, sunrise or sunset (with offset), everyone leaves or someone arrives at a place, or a chosen person leaves or arrives (Home, Work, or any other zone you've set up in Home Assistant), a room has been quiet, a sensor rising above or falling below a level (for example humidity), a device changing to a chosen state (for example a switch turning on), or manual only.
- **Daytime windows.** Any routine can be limited to hours, such as 07:00 to 22:00. Windows can cross midnight.
- **Humidity fan example.** A bathroom fan turns on when humidity goes above 60% and off again below 50%, but only between 07:00 and 22:00. The turn-off level creates a second routine named "(off)" that you can see and edit.
- **Safety first.** Unknown or unavailable people and sensors block a run. Conditions are checked again before every step.
- **Waits instead of giving up.** If a run is blocked (someone is still home, or motion is still happening), it waits until the conditions are met and runs once, up to midnight.
- **Runs as often as needed.** Presence and room triggers run every time they happen (for example, every time everyone leaves), not just once a day.
- **Dry run.** *Test* shows exactly what a routine would do right now, without touching any device.
- **Pause switch.** *Pause all routines* stops every automatic run. Manual runs still work.
- **Skips no-op steps.** A "lights off" step does nothing if the lights are already off.
- **Retries and catch-up.** Retry a failed step, and run a missed time-based routine if Home Assistant restarted a few minutes late.
- **Extra conditions.** Run only when the conditions hold: who is home or away, sensors quiet or active for a while, or an entity in a given state (for example guest mode is off).
- **Notifications** on run, skip, or failure (failures are on by default).
- **Status sensors** per routine (`idle`, `waiting`, `running`, `skipped`, `error`) for dashboards and automations.

## Installation

Requires **Home Assistant 2026.9 or newer**.

### Marketplace (Home Assistant 2026.11 and newer)

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=bisman-automations&repository=ha-daily-routine&category=integration)

1. Select the button above, or go to **Settings > Marketplace**, choose **Add from link**, enter `bisman-automations/ha-daily-routine` under **GitHub link**, and select **Add**. Adding a repository from a link needs a GitHub connection; the Marketplace asks you to connect one if you haven't.
2. Install **Daily Routine**. If a repair asks you to restart Home Assistant, restart it.

New versions show up under **Settings > Updates**. Already installed Daily Routine with HACS? There's nothing to do: Home Assistant moves it into the Marketplace the first time it starts on 2026.11.

### HACS (Home Assistant 2026.10 and older)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bisman-automations&repository=ha-daily-routine&category=integration)

1. Click the button above, or in HACS open the menu, choose **Custom repositories**, and add `https://github.com/bisman-automations/ha-daily-routine` as an **Integration**.
2. Download **Daily Routine** and restart Home Assistant.

### Manual

Download `daily_routine.zip` from the [latest release](https://github.com/bisman-automations/ha-daily-routine/releases/latest), unzip it into `config/custom_components/daily_routine/`, and restart Home Assistant.

### Upgrading from 1.0.0

Your routines are moved into the new storage automatically on the first start. Old per-routine entries become empty entries titled *Daily Routine (migrated – safe to delete)*. Delete them when you are happy with the new setup.

## Setup

[![Open your Home Assistant instance and start setting up Daily Routine.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=daily_routine)

Or go to **Settings → Devices & services → Add integration → Daily Routine**.

Then open **Daily Routine** in the sidebar. Rooms come from your Home Assistant areas, so there is nothing else to set up.

## Using the sidebar

1. **Whole house** or a **room card** → **+ New routine**.
2. Pick a template (for example *Open curtains* or *Lights off when vacant*), or start blank.
3. Set **When**, **Do**, and **Options**. Use **Test (dry run)** to check it, then **Save**.

Room routines target the room itself, so lights or covers you add to that room later are included without editing the routine.

The sidebar has four tabs:

- **Rooms**: pick a room to see and add its routines.
- **Routines**: search every routine by name, room, device, trigger, or day.
- **To-dos**: rules that add, complete or remove a to-do item when a device changes. See [To-do rules](#to-do-rules).
- **Settings** (administrators only): which people routines can use, and where notifications also go.

Each routine card has **Run now**, **Edit**, **Duplicate**, **Enable/Disable**, and **Delete**. Under the routine's details, **Recent runs** lists its last five runs, including skips and failures and why they happened. A yellow warning appears on the card when something the routine needs is missing or unavailable, such as a device or to-do list. Duplicate opens a copy you can change before saving. In the editor, **Linked device** picks the device the routine belongs to.

## Routines as devices

Each routine is its own service under the Daily Routine integration. It is linked to the device you picked: on the routine's page it shows *Connected via*, and on that device's page it appears under *Linked devices*, the same way other integrations' services do. Routines without a device link to the **Daily Routine** device. Deleting a routine removes its service. Home Assistant lists one service per physical device under *Linked devices*, so when several routines use the same device, the others show only under *Connected via*.

## Editing routines in Home Assistant

Every routine also appears under **Settings → Devices & services → Daily Routine**, as its own entry. Open one to change any of its settings: name, on or off, trigger, time, days, place, people, sensors, the quiet time, active hours, extra conditions, linked device, catch-up, retries, notifications and steps. Edits from either place show in both, and the same checks apply.

Steps and extra conditions are written as plain lines:

```
light.turn_off | light.kitchen_ceiling, light.hall
cover.open_cover | area:living_room
light.turn_on | light.hall | {"brightness": 128}
wait 30
```

- A step is `action | targets | extra settings`. Targets are entity ids separated by commas, or `area:` followed by an area id. Extra settings are optional JSON. `wait` takes seconds, or `00:00:30`.
- A to-do step adds, completes or removes an item on a to-do list. It is written `todo add | todo.groceries | Empty dishwasher`: `add`, `complete` or `remove`, then the to-do list and the item name. In the editor, choose **To-do list** as the step type. Complete and remove steps can also check the due date: `todo complete | todo.groceries | Buy milk | due=1 overdue=yes undated=no` acts only on items due today or tomorrow, with overdue items included and undated items left alone. Use `due=any` for no date check, and `due=0` for today only. To-do rules on the To-dos tab are routines built from the same step.

- Conditions are one per line, in the **Extra conditions** list. There are three kinds:
  - `presence everyone away | person.sam, person.alex | zone.home`: everyone listed is away from the place. Use `presence anyone in | person.sam | zone.work` for "at least one person is at Work". `everyone` or `anyone`, then `in` or `away`, then the people and the place.
  - `sensors quiet 10 | binary_sensor.hall_motion`: the sensors have been off for 10 minutes. Use `sensors active 5 | ...` for "one of these has been on within the last 5 minutes".
  - `input_boolean.guest_mode = off`: an entity is in a given state, for example `lock.front_door = unlocked`.
- Every condition in the list has to hold for the routine to run. With more than one, choose **all** or **any one** above the list.
- Routines saved before this change convert on load. "Only when everyone is away" becomes a who-is-away condition, and "Only when the sensors have been quiet" becomes a quiet-sensors condition. Check any routine that set "any one" with those checkboxes, because the converted conditions now count toward that choice.

Deleting an entry deletes its routine.

## To-do rules

A to-do rule adds, completes or removes an item on a to-do list when a device or sensor changes to a state you choose. Each rule is a routine, so it also gets pause, conditions, run history, retries, notifications, dry run and backup. Open the **To-dos** tab and choose **New to-do rule**:

- **Device or sensor to watch**: any entity.
- **Changes to**: one or more states, separated by commas, such as `on` or `on, off`. The suggestions are the states the device has reported in the last 30 days, and any text is accepted.
- **Hold for**: optional minutes. The device must still be in one of those states after this long before the rule runs. A change to another state in the meantime cancels it.
- **Action**: add, complete or remove an item.
- **Only when due** (complete and remove): act only on items due today, within 1, 7 or 21 days, or on any date. Overdue items and items with no due date are left alone unless you tick **Also when it is overdue** or **Also when it has no due date**.
- **To-do list** and **Item text**.

For a dishwasher, two rules cover a full cycle:

- The clean sensor changes to `on`: add "Empty dishwasher".
- The clean sensor changes to `off`: complete "Empty dishwasher".

A filter sensor works the same way: changes to `on` adds "Clean dishwasher filter", and `off` completes it.

How the rules behave:

- **Add** does nothing if an open item with that name is already on the list. The run is recorded as "already on the list".
- **Complete** and **remove** act only on an open item with that name. Names match ignoring capitals and extra spaces.
- A rule runs when the device changes into one of its states. Staying in that state does not run it again.
- If the list cannot be read or changed, nothing else happens. The card shows the problem and the run is recorded as failed.
- **Run now** makes the change straight away. **Test (dry run)** in the routine editor shows what would happen without changing the list.
- Pausing or deleting a rule does not change the items already on the list.

To run other steps when an item is checked off, make a routine with the **When an item is checked off a to-do list** trigger. Name the item, or leave it empty for any item.

## Backup

On **Settings** in the sidebar (administrators only), **Export routines** saves every routine, including to-do rules, to a file. **Import routines** loads a file saved earlier. A routine in the file replaces the one with the same id. Routines that fail the checks are listed and the others still load. Turn-off routines are rebuilt from the routine they belong to, so they are not exported. A linked device that isn't on this Home Assistant is linked to the Daily Routine device instead.

## Diagnostics

On **Settings → Devices & services → Daily Routine**, choose *Download diagnostics* to get the routines, their status and the settings, for reporting a problem. Phone names and hidden profiles are redacted. Routines still contain entity ids, so check the file before posting it publicly.

## Activity

The **Activity** tab shows how routines have been doing, for **Today**, the **7 days** or the **30 days**:

- **Totals:** how many runs were triggered, how many succeeded, failed or were skipped, and the success rate.
- **Needs attention:** routines whose devices or lists are missing or unavailable right now, routines that failed several times in a row, and routines with no runs in the period.
- **Runs per day:** a bar for each day, split into succeeded, failed and skipped.
- **Why runs failed** and **Why runs were skipped:** each reason with how often it happened, in plain words, such as *Action not found* or *Conditions were not met by the end of the day*.
- **By routine:** each routine's counts, its last run and its last failure.
- **Recent activity:** the latest events, filterable to succeeded, failed or skipped.

The history is saved with Home Assistant, keeps the last 30 days, and is removed for a routine when that routine is deleted.

## Settings

- **People in routines.** Turn off the profiles that aren't people, such as a shared display, so they don't appear when you build a routine. A routine that already uses someone keeps them.
- **Notifications.** Routine runs, skips and failures always appear in Home Assistant's notifications. Tick any phones or devices, shown by name, to send them there too. Any number can be selected.
- **Who can see Daily Routine.** Admins always can. Routines show who's home and when, so anyone else only sees them once an admin checks their name here. Home Assistant can't show a sidebar entry to only some people, so once anyone who isn't an admin is checked, everyone sees Daily Routine in the sidebar, and people who aren't checked get a page saying they don't have access. Only admins can change routines and settings.
- **Timing.** Four values apply to every routine: how long a to-do step waits for a list that is still loading (default 30 seconds), how often the health check runs (default every 15 minutes), how long a problem must last before it is announced (default 60 seconds), and a startup quiet period with no health alerts (default 5 minutes). Raise the list wait if a cloud list such as iCloud takes a while to load after a restart.

## Routine reference

| Field | Meaning |
|-------|---------|
| Trigger | `time`, `sunrise`, `sunset`, `everyone_leaves`, `someone_arrives`, `person_leaves`, `person_arrives`, `room_clear`, `value_above`, `value_below`, `state_is`, `todo_done`, or `manual` |
| Device and states | For a device-state trigger: the device to watch, the states it changes to (several, separated by commas, such as `on, off`), and an optional hold time in minutes. The routine runs when the device changes into one of those states, not while it stays in them. A change to another state during the hold time cancels the run |
| To-do list and item | For the checked-off trigger: the to-do list to watch, and an optional item name. With no name, any item counts |
| Place | For leave and arrive triggers: the zone that counts, such as Home or Work. Defaults to Home |
| Sensor and level | For value triggers: a numeric sensor, the level it must cross, and for "above" an optional level to turn back off |
| Only run between | Optional start and end time. Outside these hours the routine does nothing. Windows can cross midnight |
| Time / offset | For time and sun triggers (offset in minutes, negative is before) |
| Days | Which days it may run |
| People | For presence triggers. Only people with location tracking can be checked |
| Sensors and quiet for | For the room is quiet trigger: the motion or occupancy sensors that must stay off for N minutes. Motion restarts the count |
| Extra conditions | A list of conditions that must hold. Each one is **who is home or away** (everyone or anyone, at or away from a place), **sensors quiet or active** for N minutes, or **a device's state**. With more than one, choose whether **all** of them must hold or **any one** is enough. Each line is described under Editing routines in Home Assistant |
| Run every time | Run on every trigger, not once a day (default on for presence and room triggers) |
| Skip steps already done | Skip a step whose devices are already in the target state (default on) |
| Catch up | If Home Assistant was restarting when a time routine was due, still run it once it is back, within a window of up to 3 hours ("Don't run it late" turns this off). Home Assistant gets about 30 seconds to load, then the routine runs as soon as it can. If a condition blocks it, it is retried each minute until the window closes |
| Retry | Retry a failed step up to 3 times |
| Steps | Actions chosen by name, such as "Turn off lights" (with a room, specific devices, or no target), or any service under "Other (advanced)", plus waits |
| Notify | On run, on skip, on failure, and when it needs attention (a device or list is missing or unavailable). The attention message is sent once a problem has lasted a minute, and again only after it clears and returns |

### Trigger notes

- **Room is quiet** runs once each time the room's sensors have been quiet for the chosen minutes. It does not fire just because Home Assistant restarted in a quiet room.
- **Everyone leaves / someone arrives** fire on the change itself. With sensors selected, they wait until the room is quiet. "Everyone leaves Work" means every person has left that zone, and "someone arrives at Work" means the first person to reach it after everyone had left. A person who is at another place counts as away from Home.
- **Person leaves / person arrives** fire when one of the chosen people goes from the place to somewhere else, or from elsewhere to the place. Other people's moves don't count, and a person whose state is unknown or unavailable never triggers it.
- **Above / below a level** fire when the sensor crosses the level, not on every reading. Staying above it does not run the routine again. The turn-off level for "above" creates a companion routine named "(off)" that runs when the sensor drops below that level.
- **Checked off** runs when an item on the chosen to-do list is checked off. Items already done when the routine is saved do not run it. Items checked off while Home Assistant was stopped run it once after startup, however many there were.
- **Device changes to a state** runs once each time the device moves into one of its states. If Home Assistant restarts while the device has changed and is still in one of its states, the routine runs once after startup.
- **Outside the window** a crossing is ignored. The routine does not catch up when the window opens. It runs at the next crossing inside the window.

## Services

| Service | Description |
|---------|-------------|
| `daily_routine.run_routine` | Run a routine by id. Conditions still apply; the once-a-day rule does not. |
| `daily_routine.reload_routines` | Restart all schedules and watchers. |

Both are open to automations, admins and the people allowed to see Daily Routine. They report an error when Daily Routine isn't running or the routine id is unknown.

```yaml
service: daily_routine.run_routine
data:
  routine_id: kitchen_lights_off
```

Entities:

- `switch.pause_all_routines`: on = all automatic routines paused. Admins, the people allowed to see Daily Routine, and automations can switch it.
- Routine status sensors: one per routine, on that routine's service device. The entity id is set by Home Assistant. Routines created on Home Assistant 2026.10 or later include the device name in it (for example `sensor.daily_routine_<routine>_status`). Upgraded routines keep their existing id. The state is `idle`, `waiting`, `running`, `skipped`, or `error`. Attributes include `last_run`, `last_result`, `next_run`, `block_reason`, `waiting`, and `repeat`.

## Events

- `daily_routine_routine_started`
- `daily_routine_routine_finished` (`result`: `success` or a failure reason)
- `daily_routine_routine_skipped` (`result`: `disabled`, `paused`, `already_ran_today`, `conditions_not_met`, `waiting:<reason>`, …)

Every event includes `routine_id` and `name`.

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

## Troubleshooting

### Diagnostics and debug logs

When something goes wrong, download diagnostics from **Settings → Devices & services → Daily Routine → ⋮ → Download diagnostics** and check the file before you share it.

For debug logs, use **Enable debug logging** on the integration page, or add this to `configuration.yaml` and restart:

```yaml
logger:
  logs:
    custom_components.daily_routine: debug
```

Then open an [issue](https://github.com/bisman-automations/ha-daily-routine/issues/new/choose) with the diagnostics, the log, and your Home Assistant and Daily Routine versions.

## Development

- Routines live in Home Assistant storage (`.storage/daily_routine.routines`). The sidebar talks to them through websocket commands in `panel.py`.
- Pure routine validation is in `routines.py`. Scheduling and execution are in `coordinator.py`. Room discovery uses the area, device, and entity registries.
- The Python tests cover scheduling, safety, migration and setup; `tests/test_panel_frontend.mjs` holds the sidebar panel smoke tests.

Tests run inside a real Home Assistant through
[pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component).
You need Python 3.14 for the current Home Assistant.

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest
```

The frontend tests run with `node --test tests/*.mjs`.

To test against the oldest supported Home Assistant (2026.9), use Python 3.14 and
`pip install -r requirements_test_min.txt` instead.

CI runs the same checks on every push and pull request, on both Home Assistant
versions, plus hassfest, HACS validation and the shared repo standards, using the
workflows in [bisman-automations/.github](https://github.com/bisman-automations/.github).
Releases are drafted from [CHANGELOG.md](CHANGELOG.md); see [CLAUDE.md](CLAUDE.md).

## License

MIT. See [LICENSE](LICENSE).

Issues and pull requests welcome: https://github.com/bisman-automations/ha-daily-routine/issues
