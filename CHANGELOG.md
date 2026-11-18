# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.34.0] - 2026-11-18

### Changed

- **Failures say which device, which integration and why.** A step that fails now names each device that didn't do it, the integration it was reached through, and the concrete reason: Home Assistant's own error, "is unavailable", "timed out", or "stayed closed after 10 s (wanted open)". For a device in more than one integration it also says which ways were tried first ("tried Matter first"), and each step under **see each step** lists every way and what happened. This is used everywhere a failure shows: the card's "last:" line, recent runs, History, Activity, **Not working well**, **Why not?** and the notification. A raw action name such as `cover.open_cover` is no longer shown as the reason.
- **The routine's Linked device is tried first.** When a routine has a Linked device (under More options, usually the Matter one) and a step is for that device's room or for the devices it is linked to, the Linked device is sent the command first, on its own, ahead of the integration order. Only if it fails, is unavailable or doesn't change within the check time are the others tried, one integration at a time in the integration order (for curtains: left and right over Bluetooth, then the Cloud). Its linked devices are never sent the command at the same time as it. This also works without Device Linker: in the Linked device's room, the step's other devices of the same kind (curtains for curtains, not the room's blind) are its fallbacks, by integration. A step that doesn't reach the Linked device works as before. Why not? shows "Linked device first" in the order devices are tried, a failure says "tried the linked device, Matter, first", and the editor explains it under Linked device.
- **A step with no devices says so.** A step with nothing to send its action to used to fail with "cover.open_cover didn't work". It now says "“Open” has no devices to send it to: pick the devices or a room in the step", or Home Assistant's error if there was something else wrong. The editor no longer saves a device step with nothing picked.
- **Why not? checks the setup.** It lists setup problems (a step with no devices or room, a step that finds no devices of its kind, an "Only if" sensor condition with no sensors), the last failure with each step's reasons, and the order the ways of a linked device are tried in.
- **An "Only if" sensor condition with no sensors is flagged.** The editor shows a warning on it and won't save it ("pick the sensors, or remove it"). One that arrives some other way is ignored, not treated as quiet or active, and Why not? says so.
- **Ticked devices are listed first.** Every list of devices to tick in the editor shows the ticked ones in a **Selected** section at the top, with a count and **Show only selected**.
- **The progress bar on a running routine's card shows properly.** A step with no end time (opening curtains, for example) showed as a 3% sliver, so "Step 1 of 1" was only a small blue dot, on a track that was hard to see. The bar now spans the card, its track shows on any theme, a step with no end time is shown half done and moving, and the step line sits under the bar in its own block. The dashboard card does the same.

### Fixed

- **A device that is only in one integration on one side is no longer taken for a pair.** With a Matter pair of curtains, an integration that has only one of the two sides (for example only the right curtain in SwitchBot Cloud, or only the right one over Bluetooth) was treated like the Matter pair, as if it moved both. The other curtain could then be left as it was while the step counted as done, or a side's last fallback was skipped. Only a device linked to both sides counts as the pair now, and a curtain that only a later integration reaches still gets that integration when the earlier ones are done.

## [1.33.0] - 2026-11-18

### Changed

- **Steps on linked devices are planned faster.** Which devices are in more than one integration is worked out once, and again only when a device, entity or area changes, instead of reading every device in the house for every step. On a test system with 660 devices, planning a step went from about 0.8 ms to under 0.1 ms.
- **Saving a routine leaves the others alone.** Saving or deleting a routine restarts only that routine (and its turn-off routine and other triggers). The others keep their schedules and watchers, and a retry or a heads-up already planned for them is no longer dropped.
- **The Activity tab opens faster.** Its numbers are worked out in one pass over the run history instead of once per routine. With 5,000 runs and 40 routines, the 30-day view takes about 4 ms instead of 24 ms.
- **Live updates send only what changed.** While a routine runs, the sidebar and the dashboard card get only the routines whose state changed, about 1 KB instead of every routine's status (about 24 KB with 40 routines).
- **The sidebar doesn't fetch the device list again.** When routines change, the sidebar reloads without the device list and each entity's integration unless they changed, about 140 KB instead of 260 KB on a large system.
- **Calendars read once and at the same time.** A calendar that both a condition and a "before an event" routine (or a hold) use is read once every 30 minutes instead of twice, and all calendars and weather forecasts are read at the same time instead of one after another.
- **Starting up doesn't wait for slow calendars or forecasts.** Daily Routine reads the calendars its conditions need and then starts. The coming days' events and the forecasts are read straight after, in the background.
- **Health checks only when a device goes away or comes back.** A device a routine watches turning on and off no longer plans a new health check every time. Only going missing, unavailable or unknown, or coming back, does.
- **Browsers keep the sidebar and card scripts until the next update.** Their addresses now include the version, so an update still loads the new ones straight away.

## [1.32.0] - 2026-11-17

### Changed

- **Devices in more than one integration are reached by integration, including pairs.** A device that's in Home Assistant more than once, such as a SwitchBot curtain through Matter, Bluetooth and the cloud, is still reached one integration at a time, in the order you set. A Matter device that is a pair of curtains (left and right also present over Bluetooth and the cloud, including when Device Linker puts the pair in two links) is used as one call first, but only when the step is for both curtains or names the pair itself. A step for only the left or right leaves the pair alone and uses that side's integrations in the same order. If the step lists only the Bluetooth or cloud entities, a higher-priority way that covers the same curtains is still used first. The two channels of a two-gang switch are still not grouped. Failure messages name the last integration that was tried.

## [1.31.3] - 2026-11-15

### Changed

- **Screenshots in the README.** The README now shows the Rooms, Routines, Today, Activity and Settings tabs, the simple editor, the dashboard card, a phone and the dark theme.

## [1.31.2] - 2026-11-15

### Changed

- **Status sensors do less.** Each routine's status sensor now works out only what it shows (not the run history, device reliability, power and holds the sidebar uses), and writes its state only when that changed. With 40 routines, a stream of sensor changes takes about half the time it did.
- **Routines found faster.** Looking a routine up by its id (done for every sensor on every update) no longer reads through the whole list each time.

### Fixed

- **Skip days in the evening.** A routine's skip days were checked against the computer's clock instead of Home Assistant's time zone. On Home Assistant OS (which runs on UTC) that dropped today from **Skip these days** during the evening in the Americas, so the routine still ran. Today now counts until midnight where you are.

## [1.31.1] - 2026-11-15

### Changed

- **One update at a time.** When lots of things change at once (a burst of sensors, a routine's steps), the routine status sensors and the sidebar are now updated once for that moment instead of once per change. With 40 routines, a burst of 300 sensor changes did about 1,600 status checks; it now does 40. The pause switch, House mode and Kind of day still update straight away, so automations that change them and read them back see the new value.
- **Fewer writes while routines run.** What routines save while they run (waiting, done, failed, power used) is now written once a couple of seconds later, together with everything else that changed, instead of after every step. Your own changes (saving a routine, settings, skipping, pausing) are still written straight away, and Home Assistant writes anything still waiting when it shuts down.
- **Habits cost less.** Daily Routine now only looks at changes to lights, switches, fans and the like when it learns your habits (Home Assistant skips the rest before asking it), drops old habit notes from the oldest end instead of reading them all again, and works out habit ideas again only when something they depend on changes.
- **Lived-in lights rest when off.** The once-a-minute check now runs only while lived-in lights are on, or still have lights to turn off.
- **Sidebar and card push.** Live updates are built once for every open screen, aren't sent when nothing changed, and a sidebar in a hidden tab or on a switched-off screen catches up once when it's seen again instead of redrawing every time.

## [1.31.0] - 2026-11-15

### Added

- **Phones charging.** A new condition: every phone you tick is on its charger. With a set time and "wait, and run as soon as they are", bedtime starts once everyone has plugged in. The library has it ready.
- **A sensor's level.** A new condition: a sensor below or above a level, such as no watering when today's rain is over 5 mm. The library has a watering routine that skips after rain.
- **Looks for a room, one tap away.** A new library routine saves how a room looks (reading, a movie, cleaning). The dashboard card's new `buttons: true` option shows Run now routines as big buttons.
- **Missed while Home Assistant was off.** Under **Settings**, after a restart you can get one notification naming the routines whose time passed while it was off, with a **Run them now** button.
- **Seasonal routines.** The library has holiday lights (December 1 to January 6) and a school-year wake-up (September to mid-June) that start and stop by themselves.
- **Skip days.** A routine can skip chosen days, such as a holiday or a day off; **Same days next week** copies them a week later. **Why not?** says when it's a skip day.
- **Coming home summary.** Under **Settings**, when the house leaves Away or Vacation, one notification says what ran while you were gone.

## [1.30.0] - 2026-11-15

### Added

- **Lived-in lights like last week.** Instead of at random, lived-in lights can copy what you did by hand on the same evening a week earlier, at the same times. On an evening with nothing to copy they go back to random.
- **Not working well.** The Routines tab lists routines that worked on fewer than half of their tries in the last two weeks, with **Why not?** for each.
- **Say who's home.** A new library routine says "… is home" on a speaker when someone arrives. `{who}` in a step's message is the person who arrived or left.
- **Battery check.** A new step sends one notification naming every battery under the level you pick (and sensors that say their battery is low). The library has it ready as a weekly routine.
- **Hot days move the time.** A routine at a set time or the sun can run earlier (or later) when the day's forecast high reaches a temperature, such as closing the blinds an hour earlier on a 30° day.
- **Wait for a checklist.** A new step shows a checklist on the routine's card, the Today tab and the dashboard card ("Back door locked, Stove off…") and waits until everything is ticked off. The library has a bedtime checklist.
- **Power over the last 30 days.** The Activity tab shows, for each routine with power sensors on its devices, how much it turned off (or on) per run, and the total.

## [1.29.0] - 2026-11-15

### Added

- **Sunrise alarm light.** A new library routine: half an hour before your phone's next alarm, the bedroom lights come up slowly and warm, like a sunrise.
- **Did I leave something open?** A new step, **Check doors and windows**, looks at the doors, windows, garage doors and locks you pick and sends one notification naming anything still open or unlocked (or not reporting). Nothing is sent when it's all shut, and it can stop the routine instead. The library has it ready as a routine for when everyone leaves.
- **Guest Wi-Fi.** Under **Settings**, the guest network's name and password. While the house is in Guests mode, the Today tab and the dashboard card show them, big enough to read off a wall tablet.
- **Laundry finished.** A new library routine: when the washer's power goes up it has started, and once it stays low for 5 minutes you get a notification.
- **Quiet hours.** Under **Settings**, times when routines leave the speakers alone: no announcements, music, turning the volume up or saying the day ahead. Turning something down or off still works, and a routine (an alarm) can be allowed to use the speakers anyway.
- **A routine's history.** **History** on a routine's card shows the last 30 days as bars: how many times it ran, was skipped or failed each day.
- **See tomorrow.** On the Today tab, a dry run of tomorrow: what would run and when, with each routine's steps, paused ones marked. Nothing is run.

## [1.28.0] - 2026-11-15

### Added

- **Say the day ahead.** A step that says good morning, the kind of day, the weather (now and today's high) and today's events on a speaker.
- **The forecast as a condition.** Run only when rain (or snow, or a cold spell…) is coming in the next hours, or only when it isn't, such as no watering before rain. It also works as a step's **Only if**.
- **Kinds of day for one person.** A kind of day can be someone's own, from their own calendar (a child's school calendar), and a **Kind of day** condition can follow that person's.
- **Undo the last hour.** One button on the Today tab puts back what every routine changed in the last hour.
- **Calendar holds.** Under **Settings**, while an event like "Babysitter" or "Vacation" is on in a calendar you pick, routines don't run on their own. A routine can be set to run anyway, and **Why not?** says when a hold is stopping it.
- **How reliable its devices are.** A routine's card shows how often its devices answered over the last 30 days, and which ones had the most trouble.
- **Share a whole group.** **Share all** on a group gives one code for every routine in it; **Import a routine** brings them all in at once, with rooms matched by name.

## [1.27.0] - 2026-11-15

### Added

- **Why not?** A button on every routine's card says, in plain words, what would stop it running on its own right now (paused, the wrong house mode, a condition, not one of its days…), what it's waiting for, and why it was last skipped.
- **Sunset, but not too early or too late.** Sunrise and sunset routines can have **But not before** and **And not after** times, such as sunset but not before 18:00 in winter and not after 20:00 in summer.
- **Not in some house modes.** **Not on its own in these house modes** skips a routine's own runs while the house is, say, in Guests mode (no bedtime lock-up while friends stay over). **Run now** still works.
- **A weekly report.** Under **Settings**, a notification once a week on the day and time you pick: how many runs worked, failed or were skipped, which routines failed most, and which didn't run at all. **Send it now** shows it straight away.
- **Smarter ideas from what you do.** An idea from something done by hand now notices when it was always done after dark, or with someone home, and the routine it makes checks for that too.
- **Wait for cheaper electricity.** A new step waits for the cheapest hour (or quarter hour) of the next few hours, using a price sensor, before going on, so one step of a routine can start the dishwasher when power is cheap.
- **A Kind of day sensor.** Today's kind of day (from **Settings → Kinds of day**) is a sensor, for automations and other integrations.
- **A card for one group.** The Daily Routine dashboard card takes `group: Bedtime` to show only that group's routines, with a button for administrators to pause the group for today or resume it.

## [1.26.0] - 2026-11-15

### Added

- **Ideas from what you do.** Daily Routine notices lights, switches, fans and curtains you change by hand (in the app, by voice or at the wall) around the same time on most days, such as the porch light around 18:35 on weekdays, and offers a routine for it on the Today tab and under **+ New routine**. **No thanks** stops offering one. Changes made by automations, scripts and routines don't count.
- **Power before and after.** When a routine's devices have power sensors, its card shows the power they used before and a minute after its last run (640 W → 85 W), and what it saves or adds on average.
- **Only rooms someone is in.** A step for rooms can be limited to the rooms someone is in, or the empty ones, by each room's motion, occupancy or presence sensors. A room without any is left alone.
- **Turn the volume up slowly.** A new step changes speakers' volume a little at a time over the minutes you pick, for waking up gently or winding down.
- **Kinds of day.** Under **Settings → Kinds of day**, name days such as School day or Holiday and the calendar (and words) that say so. Routines can then run only on some kinds of day with **+ Kind of day**, as a condition or a step's **Only if**. The Today tab says what kind of day it is.
- **Groups.** Give routines a group, such as Bedtime. The Routines tab lists them by group, with **Pause today**, **Pause a week** and **Resume all** for the whole group.
- **A library of routines.** Under **+ New routine**, **Library** has ready-made routines with several steps, by topic: Gentle wake-up, Goodnight, Wind down, Leaving home, Lights off in empty rooms and more.

### Changed

- **Every device list says where a device comes from.** Wherever you pick devices (steps, lived-in lights, conditions, triggers), each one shows its integration, such as *Entry light (Philips Hue)*, so a light that's in Hue, Matter and HomeKit can be told apart.

## [1.25.0] - 2026-11-15

### Added

- **See each step of a run.** Under **Recent runs** on a routine's card, and on the Activity tab, **See each step** replays a run: each step, how long it took, whether it worked, was skipped or failed and why, and how its devices ended up. "Why didn't the lights go off?" is answered there.
- **An NFC tag or a button starts it.** New triggers: **When an NFC tag is scanned** (Home Assistant's tags, written with the phone app) and **When a button is pressed** (any button Home Assistant shows as an event: a Pico, a Zigbee remote, a doorbell), optionally only some presses, such as a double press.
- **Vacation in one tap.** Under **Going away?**, **Start vacation** switches the house to Vacation mode, pauses the routines you pick through the last day, turns on lived-in lights and sends a summary each evening. The day after (or with **Back home**) it all goes back how it was.
- **A daily summary.** Under **Settings**, a notification each day at the time you pick, saying what ran, what failed and what was skipped, and why. It can be sent only while the house is Away or on Vacation.
- **Make it look like this.** A step that saves how devices look (on or off, brightness, color, position, temperature) and puts them back exactly so. **Undo** puts them back how they were before.
- **Wait for a level.** **Wait until…** can wait for a number to go below or above a level, such as the dishwasher's power under 5 W, and for it (or a state) to stay so for some minutes before going on.
- **Share a routine.** **Share** on a routine's card gives a code; **Import a routine** under **+ New routine** turns it into a new routine, with rooms matched by name and devices the home doesn't have pointed out.

## [1.24.0] - 2026-11-15

### Added

- **Before your alarm.** A routine can run a set time before a phone's next alarm (the Companion app's Next alarm sensor) or a date-and-time helper, such as wake-up lights 15 minutes before. When the alarm moves, so does the routine; with no alarm set, it doesn't run.
- **Before a calendar event.** Run a set time before events in a calendar, all of them or only ones whose name has some text, such as "School". The next three days are read every half hour.
- **When the forecast says so.** Run when rain, snow or another kind of weather shows up in the next hours of the forecast, or the temperature will drop below a level. It runs once when the forecast changes to match, not again until it stops matching.
- **In the cheapest hour.** With an electricity price sensor (Nord Pool, ENTSO-e, Tibber and others that list prices by the hour), run in the cheapest hour inside the hours you pick, such as the dishwasher between midnight and 06:00. Once chosen, the day's hour stays put. Without prices it runs at the start of the hours.
- **Ask me first.** A routine can ask before it runs on its own: a notification with **Run** and **Don't run** buttons on your phone, and the same on its card. Nobody answers within the minutes you choose, and it does what you picked: run anyway, or skip. **Run now** never asks.
- **Steps at the same time.** Tick **At the same time as the step before** to start steps together, such as closing every room's curtains at once, instead of one after another.
- **Copy to other rooms.** **Copy to rooms** on a routine's card makes a copy for each room you tick. Steps for the routine's room are for the new room instead; steps for specific devices stay as they are.
- **A dashboard card.** Add the **Daily Routine** card to any dashboard to see what's running (with its progress), what's asking, and what's next today, with **Run now** and **Skip**. It can show one room or only whole-house routines.
- **Devices that gave trouble.** The Activity tab lists devices a step couldn't change, or had to send the command to twice, with how often each worked and its last problem, so a device with a weak signal or low batteries stands out.
- **A simple editor.** New routines open as one sentence: what it's called, when, in which room, and what to do. Everything else (more triggers, conditions, dates and hours, Ask me first, notifications, skip and pause, options) is a button that adds just that part. **Switch to the full editor** shows every option at once. Under **Settings**, **Routine editor** picks which one opens, for everyone and, if you like, differently on this device.
- **Ideas for your home.** **+ New routine** suggests routines from what your home has and no routine does yet, such as closing curtains at sunset, a steamy bathroom's fan, wake-up lights before your phone's alarm, locking up when everyone leaves, and a heads-up when rain is coming.

### Changed

- **Shorter editor.** Steps fold to one line saying what they do; tap one to change it, and drag steps to reorder them.
- **Everyone allowed to see Daily Routine can use Run now, Skip, Try again and Undo**, on the page and on the dashboard card. Changing routines and settings is still for admins.

## [1.23.0] - 2026-11-14

### Added

- **Search wherever you pick a device.** Long lists of lights, sensors and other devices, and device drop-downs, have a search box above them, in the routine editor, the to-do rules and Settings.
- **Every action's settings are proper fields.** Instead of a box for extra settings in JSON, each action shows its own settings: a light's brightness, white temperature, color and fade, a fan's speed, how far to open curtains, the thermostat's mode, and for any other action, the settings Home Assistant describes for it (numbers, choices, yes or no, times). Settings a saved step already had that have no field are kept, under "Other settings this step has".

## [1.22.0] - 2026-11-14

### Added

- **See what's running.** A running routine now shows **Running** and a progress bar on its card, with the step it's on ("Step 2 of 4 · Close Bedroom curtains") and a countdown during a wait. Running routines are also listed at the top of the Rooms page, and a room with one running shows it.
- **More than one trigger.** Under **Or also when**, a routine can have up to 4 more triggers, such as "at 22:00 or when everyone leaves". They share the routine's days, hours, conditions and steps.
- **New steps:** **Wait until…** pauses until a device shows a state (a garage door closed, say), then goes on or stops. **Run another routine** runs it and waits for it to finish. **Set the house mode** switches the mode.
- **Only if, on one step.** Any step can have its own condition: a device's state, the house mode, workday or day off, or the weather. When it doesn't hold, just that step is skipped.
- **Undo.** For two hours after a routine runs, **Undo** on its card puts the lights, switches, fans, curtains, locks and thermostats it changed back how they were. Also the `daily_routine.undo_routine` action.
- **House modes.** Home, Away, Night, Guests or Vacation. Pick the mode at the top of the page, with a routine step, with the new **House mode** entity, or with `daily_routine.set_mode`. Routines can run only in some modes (a new **House mode** condition), or start when the mode changes (the new **When the house mode changes** trigger). New templates switch to Away when everyone leaves, back to Home when someone arrives, and to Night at bedtime.
- **Lived-in lights.** Under **Settings**: while the house is in Away or Vacation mode, the lights you pick turn on and off at random in the evening so the house looks lived in. They're turned off at the end of the evening and when you come home.
- **Workday and weather conditions.** Run only on workdays or days off (with Home Assistant's Workday integration, which knows public holidays), or only when the weather is, or isn't, rainy, sunny and so on, or above or below a temperature.
- **Today tab.** Everything still planned today, in time order, with **Run now** and **Skip** on each, the routines that run when something happens, and what already ran.
- **Assist.** "Run bedtime", "skip the morning curtains", "set the house to night" and "what's the next routine?" work by voice or typing to Assist, for admins and the people allowed to see Daily Routine.

### Changed

- **Run now starts at once.** The page no longer waits for the whole routine to finish before saying anything: the card shows Running straight away, and a routine that can't run says why ("didn't run: the house isn't in the right mode"). Pressing it again while it's running doesn't start a second run.
- **A cleaner logo.** The icon is redrawn sharp, with a clock for the time of day, and no longer shows holes on a dark background (such as GitHub's dark theme).
- **The page updates live.** Cards, Today and Activity update as routines start and finish, instead of every 15 seconds.

## [1.21.1] - 2026-11-14

### Changed

- **Renaming an entity keeps it running.** From Home Assistant 2026.11, when you change one of this integration's entity IDs, the entity moves to its new ID in place instead of being removed and added again, as Home Assistant now expects. Everything keeps working the same.

## [1.21.0] - 2026-11-14

### Added

- **Devices in more than one integration are reached one way at a time.** A device that's in Home Assistant more than once, such as a SwitchBot curtain through Matter, SwitchBot (Bluetooth) and SwitchBot Cloud, used to get every command three times. Now a step sends it through the first integration in your order, checks the curtain actually closed, and only then tries the next one. The step fails only if every way did. Home Assistant has to show the devices as linked (they share a MAC address or another identifier).
- **Choose the order.** **Settings → Devices in more than one integration** lists the integrations your linked devices come from; move them up or down. Integrations you haven't placed come after, local ones before cloud ones. Each routine can use that order or its own (**For devices in more than one integration** under Options).

### Changed

- **Curtains and blinds still opening or closing get up to two minutes** to finish before the step counts them as not having changed.

## [1.20.0] - 2026-11-14

### Added

- **Wait a few minutes.** A new choice for **If they aren't met when it's time**: wait up to a number of minutes you pick, and run as soon as the conditions hold.
- **Not again within a few minutes.** Routines that react to something happening (and the room is quiet) have a new **Not again within** setting: after running, they don't run again on their own for that many minutes, so a door opened and closed a few times, or a sensor bouncing around its level, doesn't run it each time. Off unless you set it; **Run now** isn't held back.

### Changed

- **A run started by something happening ends when that's over.** A routine that reacts to someone arriving, everyone leaving, a person, a device's state or a sensor's level, and is waiting for its conditions, is now dropped once that moment has passed: for example, an "unlock when someone arrives" run waiting for the lock to be locked no longer unlocks it hours later, once everyone has left again. The Activity tab shows "What started it was over".
- **New routines that react to something happening don't wait all day.** They start with **Skip it until next time**, and the editor explains why. Existing routines keep their setting; think about changing ones that wait until the end of the day, such as unlocking a door when someone arrives.

### Fixed

- **A phone's location dropping out no longer counts as arriving or leaving.** When everyone was away and one tracker went unavailable for a moment, "someone arrives" could run. After a restart, trackers loading could look like everyone leaving.
- **Person leaves or arrives no longer misses a move made while the location was unavailable** (home, unavailable, away now counts once).
- **A device or sensor coming back from unavailable isn't a change.** "Device changes to a state" no longer runs when a device reconnects in the state it was already in (unless you picked unavailable as a state), and "above or below a level" no longer runs when a sensor reconnects on the same side of the level.

## [1.19.0] - 2026-11-14

### Added

- **Heads-up before a run.** A new routine setting sends a notification 5 to 60 minutes before a time or sun routine runs, saying when. On your phone it has a **Skip it** button, which skips just that run.
- **Put things back.** Two new steps: **Remember which lights and switches are on** keeps, under a name you choose, the lights, switches and fans that are on (and each light's brightness and color), and **Put back what was on** turns exactly those back on later. Use them in a "leaving" and a "coming home" routine, so only what was on comes back on. They're also actions for automations: `daily_routine.remember_states` and `daily_routine.put_back_states`.
- **Fade lights.** **Turn on lights** and **Turn off lights** can fade over a number of minutes, and **Turn on lights** can set a brightness, for a gentle wake-up or bedtime.
- **Only part of the year.** A routine can run only between two dates each year, such as 1 November to 31 March for holiday lights or heating. The card shows the dates, and the next run shows the first day when it's out of season.

## [1.18.0] - 2026-11-14

### Added

- **One device that doesn't answer no longer spoils the step.** When a step turns several devices on or off and one can't be reached (for example a Bluetooth switch out of range), the others still change, and only that device is reported, by name: "Hall light couldn't be reached".
- **Choose what happens next.** A new setting on each routine, **If a step can't change a device**: **Stop there** (the default, as before) or **Keep going with the other steps**, which suits routines like turning everything off when everyone leaves.
- **Tries again later.** The devices that couldn't be changed are tried again after 5 minutes, then any steps that were stopped, up to 3 times. Each try checks the routine's conditions first. Change the wait, or turn it off, with **Try that device again later**.
- **Try again button.** A routine that couldn't change a device shows **Try again** on its card, and a failure message sent to the Companion app has a **Try again** button too.
- **Checks that it worked.** After turning something on or off, opening, closing or locking it, Daily Routine waits up to 10 seconds for the device to show the change, and sends the command once more if it didn't. A device that still didn't change is reported: "Porch light didn't change". Set the time, or turn the check off, under **Settings → Timing**.

### Changed

- **A device that doesn't answer is given up on after 30 seconds** (each try), instead of holding the routine for as long as the device's integration keeps trying. Scripts aren't limited. Set it under **Settings → Timing**.
- **A failed step is one line in the log**, naming the device and why, instead of a long error. Turn on debug logging for the full details.

## [1.17.2] - 2026-11-13

### Changed

- **Smoother updates.** Each kind of entity now tells Home Assistant how many updates it can take at once, as Home Assistant recommends. Nothing changes in how anything looks or works.

## [1.17.1] - 2026-11-10

### Changed

- **Install from the Marketplace by searching for Daily Routine.** Home Assistant 2026.11 renames HACS to the Marketplace, so the install steps start by searching the Marketplace. Searching HACS stays for Home Assistant 2026.10 and older, and adding the repository as a custom repository remains the fallback.
- **Ruff 0.17.4.** Linting uses the current Ruff release.

## [1.17.0] - 2026-11-08

### Added

- **Choose how long a routine waits for its conditions.** When a scheduled run is blocked (for example someone is still home), a new setting under the conditions picks what happens: wait and run as soon as they are met until the end of the day (as before), wait until a time you choose, or skip it until next time. It's in the sidebar editor and in the routine's settings in Home Assistant.
- **The routine card says when a waiting routine will give up**, for example "Runs as soon as its conditions are met, until 9:30 AM".

### Fixed

- **A routine waiting for its conditions now keeps waiting after Home Assistant restarts.** Before, the wait was forgotten and the routine didn't run that day.
- **Saving a routine no longer cancels other routines that are waiting.** Before, saving any routine made every waiting routine give up for the day.
- **A run stopped part way finishes later.** If a condition stops holding in the middle of a routine's steps, the routine now waits for its conditions again and runs from the start once they hold, instead of being done for the day.
- **The waiting choice also shows for "everyone leaves" and "room is quiet" routines**, which wait for the room to be quiet.

## [1.16.0] - 2026-11-07

### Added

- **Skip the next run.** Each routine's card has **Skip next run**: the next time it would run on its own, it doesn't, then it carries on as usual. **Don't skip** takes it back. Also in the editor, and as the `daily_routine.skip_next` action.
- **Going away?** On the Routines tab, pick a date and the routines to pause, and they don't run on their own through that day, then carry on. Each card shows **Paused until** with a **Resume** button, and **Run now** still works. Each routine also has **Pause until** in the editor, and automations can use the new `daily_routine.pause_routines` action.
- **Calendar days.** A new extra condition: only on days with an event in a calendar, or not on days with one, such as not on days with "No school". Type part of the event's name, or leave it empty for any event. A calendar that can't be read blocks the run, like other conditions.
- **Vary the time.** Time, sunrise and sunset routines can run up to a number of minutes earlier or later, a different time each day, so lights while you're away don't look like a timer. A day's time stays the same if Home Assistant restarts.
- **More actions with plain settings:** **Set speaker volume**, **Say something on a speaker**, **Set the thermostat**, **Turn off the thermostat**, **Play media**, **Start the vacuum** and **Send the vacuum home**. Each asks for what it needs, such as the temperature or what to say, instead of JSON.

### Changed

- Test tools: ruff 0.17.3 and mypy 2.4.1.

## [1.15.2] - 2026-10-31

### Changed

- **Linked device** in the routine editor, and *Linked to* on each routine, now show which integration each device comes from, such as *Office Curtains (SwitchBot Cloud)*. Devices with the same name, like a Bluetooth and a Cloud copy of one fan, can now be told apart.
- **Linked device** offers only real devices now, not services such as Music Assistant players or another integration's service. A routine already linked to a service keeps it until you pick something else.

## [1.15.1] - 2026-10-30

### Fixed

- **Routines no longer show "unknown" as their manufacturer and model.** In the device list, each routine now shows Daily Routine as its manufacturer and Routine as its model, and the Daily Routine device shows Daily Routine too.

## [1.15.0] - 2026-10-30

### Added

- **Wait for an unavailable list or device**, a new option on each routine (off by default). When the to-do list a routine changes is unavailable, for example while iCloud is having trouble, the run waits instead of failing, and goes ahead as soon as the list is back. The card shows **Waiting for list**. You're only told the routine needs attention if the list stays unavailable for a whole day.

### Fixed

- Each Daily Routine message showed up twice under **Notifications** when **Show a Home Assistant notification** was one of the notify targets. It now shows once, and the "needs attention" message still goes away by itself when the problem clears.

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

[1.34.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.33.0...v1.34.0
[1.33.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.32.0...v1.33.0
[1.32.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.31.3...v1.32.0
[1.31.3]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.31.2...v1.31.3
[1.31.2]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.31.1...v1.31.2
[1.31.1]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.31.0...v1.31.1
[1.31.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.30.0...v1.31.0
[1.30.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.29.0...v1.30.0
[1.29.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.28.0...v1.29.0
[1.28.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.27.0...v1.28.0
[1.27.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.26.0...v1.27.0
[1.26.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.25.0...v1.26.0
[1.25.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.24.0...v1.25.0
[1.24.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.23.0...v1.24.0
[1.23.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.22.0...v1.23.0
[1.22.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.21.1...v1.22.0
[1.21.1]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.21.0...v1.21.1
[1.21.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.20.0...v1.21.0
[1.20.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.19.0...v1.20.0
[1.19.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.18.0...v1.19.0
[1.18.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.17.2...v1.18.0
[1.17.2]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.17.1...v1.17.2
[1.17.1]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.17.0...v1.17.1
[1.17.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.16.0...v1.17.0
[1.16.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.15.2...v1.16.0
[1.15.2]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.15.1...v1.15.2
[1.15.1]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.15.0...v1.15.1
[1.15.0]: https://github.com/bisman-automations/ha-daily-routine/compare/v1.14.0...v1.15.0
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
