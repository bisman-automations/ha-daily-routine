/* Daily Routine sidebar panel.
 * Rooms come from Home Assistant areas. Pick a room, then add or edit its routines.
 * All data goes over websocket commands registered in panel.py.
 */
const DOMAIN = "daily_routine";
const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const SENSOR_CLASSES = ["motion", "occupancy", "presence", "moving"];
const REFRESH_MS = 15000;

const TRIGGER_LABELS = {
  time: "At a set time",
  sunrise: "At sunrise",
  sunset: "At sunset",
  everyone_leaves: "When everyone leaves",
  someone_arrives: "When someone arrives",
  person_leaves: "When a person leaves a place",
  person_arrives: "When a person arrives at a place",
  room_clear: "When the room has been quiet",
  value_above: "When a sensor goes above a level",
  value_below: "When a sensor drops below a level",
  state_is: "When a device changes to a value",
  todo_done: "When an item is checked off a to-do list",
  manual: "Only when I press Run",
};
const PRESENCE_TRIGGERS = ["everyone_leaves", "someone_arrives", "person_leaves", "person_arrives"];
const PER_PERSON_TRIGGERS = ["person_leaves", "person_arrives"];
const VALUE_TRIGGERS = ["value_above", "value_below"];
const STATE_TRIGGERS = ["state_is"];
const TODO_DONE_TRIGGERS = ["todo_done"];
const TODO_VERBS = { add: "Add", complete: "Complete", remove: "Remove" };
const ACTIVITY_WINDOWS = [["today", "Today"], ["7d", "7 days"], ["30d", "30 days"]];
const ACTIVITY_FILTERS = [["all", "All"], ["ran", "Succeeded"], ["failed", "Failed"], ["skipped", "Skipped"]];
const ACTIVITY_OUTCOMES = { ran: "Succeeded", failed: "Failed", skipped: "Skipped" };
const SKIP_REASON_LABELS = {
  paused: "Routines were paused",
  conditions_not_met: "Conditions were not met by the end of the day",
  disabled: "Routine was turned off",
  already_ran_today: "Already ran today",
};

// Plain words for the codes the coordinator records when a run is skipped or fails.
function activityReason(code) {
  if (!code) return "";
  if (SKIP_REASON_LABELS[code]) return SKIP_REASON_LABELS[code];
  if (code.startsWith("missing_service:")) return `Action not found: ${code.slice("missing_service:".length)}`;
  if (code === "error:todo_list_unavailable") return "To-do list was not available";
  if (code.startsWith("error:todo_")) return "A to-do step failed";
  if (code.startsWith("error:")) return `Action failed: ${code.slice("error:".length)}`;
  if (code.startsWith("conditions_failed_mid_run:")) return `Conditions stopped the run partway: ${code.slice("conditions_failed_mid_run:".length)}`;
  return code;
}

function fmtTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
const TODO_OUTCOME_LABELS = {
  added: "added",
  already_on_list: "already on the list",
  completed: "completed",
  no_open_item: "nothing open to complete",
  removed: "removed",
  not_on_list: "not on the list",
  list_unavailable: "the list is not available",
  failed: "could not change the list",
  would_add: "would be added",
  would_complete: "would be completed",
  would_remove: "would be removed",
  not_due: "not due in the chosen window",
};
const STATE_LABELS = { idle: "Ready", waiting: "Waiting", running: "Running", skipped: "Skipped", error: "Error" };
const REASON_LABELS = {
  someone_home: "someone is still in that place",
  sensors_active: "motion right now",
  clear_timer_running: "waiting for the room to be quiet",
  presence_not_met: "people are not where this routine needs them",
  sensors_not_met: "sensors are not in the state this routine needs",
  clear_timer_not_started: "waiting for sensors",
  extra_condition_not_met: "an extra condition is not met",
  conditions_not_met: "conditions were not met",
  outside_hours: "outside its hours",
};
// What a step can do, in plain words. The service id is only used under the hood.
const ACTIONS = [
  { service: "light.turn_on", label: "Turn on lights" },
  { service: "light.turn_off", label: "Turn off lights" },
  { service: "switch.turn_on", label: "Turn on switches" },
  { service: "switch.turn_off", label: "Turn off switches" },
  { service: "fan.turn_on", label: "Turn on fans" },
  { service: "fan.turn_off", label: "Turn off fans" },
  { service: "cover.open_cover", label: "Open curtains and blinds" },
  { service: "cover.close_cover", label: "Close curtains and blinds" },
  { service: "cover.stop_cover", label: "Stop curtains and blinds" },
  { service: "lock.lock", label: "Lock doors" },
  { service: "lock.unlock", label: "Unlock doors" },
  { service: "scene.turn_on", label: "Activate a scene" },
  { service: "script.turn_on", label: "Run a script" },
  { service: "media_player.media_pause", label: "Pause media" },
  { service: "notify.persistent_notification", label: "Show a Home Assistant notification" },
];
const CUSTOM_ACTION = "__custom";

// States a condition can check, by kind of device. Anything else is typed in under "Other".
const CONDITION_STATES = {
  lock: ["locked", "unlocked", "jammed"],
  cover: ["open", "closed", "opening", "closing"],
  person: ["home", "not_home"],
  device_tracker: ["home", "not_home"],
  media_player: ["playing", "paused", "idle", "on", "off"],
  climate: ["heat", "cool", "heat_cool", "auto", "off"],
  on_off: ["on", "off"],
};
const STATE_NAMES = {
  locked: "Locked", unlocked: "Unlocked", jammed: "Jammed", open: "Open", closed: "Closed",
  opening: "Opening", closing: "Closing", home: "Home", not_home: "Away", playing: "Playing",
  paused: "Paused", idle: "Idle", on: "On", off: "Off", heat: "Heat", cool: "Cool",
  heat_cool: "Heat or cool", auto: "Auto",
};

function humanize(text) {
  const spaced = String(text || "").replace(/_/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function actionLabel(service) {
  if (!service) return "";
  const known = ACTIONS.find((a) => a.service === service);
  if (known) return known.label;
  const [domain, action = ""] = service.split(".");
  return `${humanize(action)} (${humanize(domain)})`;
}

const TEMPLATES = [
  {
    id: "open_curtains", title: "Open curtains", hint: "At a set time, open this room's curtains.", scope: "room",
    draft: { trigger: "time", time: "07:00:00", actions: [{ service: "cover.open_cover", targetMode: "area" }] },
  },
  {
    id: "close_curtains", title: "Close curtains", hint: "At sunset, close this room's curtains.", scope: "room",
    draft: { trigger: "sunset", actions: [{ service: "cover.close_cover", targetMode: "area" }] },
  },
  {
    id: "vacant_lights_off", title: "Lights off when vacant", hint: "Turn this room's lights off once its motion sensors have been quiet.", scope: "room",
    draft: { trigger: "room_clear", clear_minutes: 10, useRoomSensors: true, actions: [{ service: "light.turn_off", targetMode: "area" }] },
  },
  {
    id: "lights_off_at_time", title: "Lights off at a time", hint: "Turn this room's lights off at a set time.", scope: "room",
    draft: { trigger: "time", time: "23:00:00", actions: [{ service: "light.turn_off", targetMode: "area" }] },
  },
  {
    id: "house_empty", title: "Lights off when everyone is away", hint: "Whole house: once everyone has left and the motion sensors have been quiet.", scope: "house",
    draft: { trigger: "everyone_leaves", quietMinutes: 15, useAllPeople: true, actions: [{ service: "light.turn_off", targetMode: "all_areas" }] },
  },
  {
    id: "humidity_fan", title: "Bathroom fan on humidity", scope: "room",
    hint: "Turn this room's fan on when humidity rises above a level, and back off when it drops. Set \u201conly run between\u201d to limit it to daytime.",
    draft: { trigger: "value_above", valueClass: "humidity", valueThreshold: 60, offThreshold: 50, actions: [{ service: "fan.turn_on", targetMode: "area" }] },
  },
  {
    id: "fan_off_when_empty", title: "Fan off when the room is empty", scope: "room",
    hint: "Turn this room's fan off once its motion sensors have been quiet.",
    draft: { trigger: "room_clear", clear_minutes: 10, useRoomSensors: true, actions: [{ service: "fan.turn_off", targetMode: "area" }] },
  },
  {
    id: "night_lights", title: "Night lights at sunset", scope: "room",
    hint: "Turn this room's lights on at sunset.",
    draft: { trigger: "sunset", actions: [{ service: "light.turn_on", targetMode: "area" }] },
  },
  {
    id: "morning_curtains", title: "Open all curtains", scope: "house",
    hint: "Whole house: open every curtain at a set time.",
    draft: { trigger: "time", time: "07:00:00", actions: [{ service: "cover.open_cover", targetMode: "all_areas" }] },
  },
  {
    id: "bedtime_lock", title: "Lock up at bedtime", scope: "house",
    hint: "Whole house: lock every lock at a set time.",
    draft: { trigger: "time", time: "22:30:00", actions: [{ service: "lock.lock", targetMode: "all_areas" }] },
  },
  {
    id: "away_lock", title: "Lock up when everyone leaves", scope: "house",
    hint: "Whole house: lock every lock once everyone has left.",
    draft: { trigger: "everyone_leaves", usePeopleList: true, actions: [{ service: "lock.lock", targetMode: "all_areas" }] },
  },
  {
    id: "welcome_home", title: "Welcome home lights", scope: "house",
    hint: "Whole house: turn the lights on when someone arrives.",
    draft: { trigger: "someone_arrives", usePeopleList: true, actions: [{ service: "light.turn_on", targetMode: "all_areas" }] },
  },
  {
    id: "lights_off_when_someone_leaves", title: "Lights off when someone leaves", scope: "house",
    hint: "Whole house: when any of the chosen people leaves Home, turn every light off.",
    draft: { trigger: "person_leaves", usePeopleList: true, actions: [{ service: "light.turn_off", targetMode: "all_areas" }] },
  },
  {
    id: "bedtime_curtains", title: "Close all curtains at bedtime", scope: "house",
    hint: "Whole house: close every curtain at 10:00 pm.",
    draft: { trigger: "time", time: "22:00:00", actions: [{ service: "cover.close_cover", targetMode: "all_areas" }] },
  },
  {
    id: "lights_off_at_sunrise", title: "Lights off at sunrise", scope: "house",
    hint: "Whole house: turn every light off when the sun comes up.",
    draft: { trigger: "sunrise", actions: [{ service: "light.turn_off", targetMode: "all_areas" }] },
  },
  { id: "blank", title: "Blank routine", hint: "Start from scratch.", scope: "any", draft: {} },
];

// How long after a restart a missed routine may still run, in minutes.
const CATCH_UP_CHOICES = [0, 15, 30, 60, 120, 180];
// Global timing settings: key, label, help, and the same limits the server checks.
const TIMING_FIELDS = [
  ["list_wait_seconds", "Wait for a to-do list (seconds)", "How long a to-do step waits for a list that has not loaded yet", 0, 300],
  ["health_check_minutes", "Health check every (minutes)", "How often every routine's devices and lists are checked", 1, 60],
  ["health_alert_seconds", "Health alert delay (seconds)", "How long a problem must last before it is announced", 0, 600],
  ["startup_quiet_minutes", "Startup quiet period (minutes)", "No health alerts for this long after Home Assistant starts", 0, 60],
];
const TIMING_DEFAULTS = { list_wait_seconds: 30, health_check_minutes: 15, health_alert_seconds: 60, startup_quiet_minutes: 5 };

function catchUpLabel(minutes) {
  if (minutes === 0) return "Don't run it late";
  if (minutes < 60) return `Within ${minutes} minutes`;
  return minutes % 60 === 0 ? `Within ${minutes / 60} hour${minutes === 60 ? "" : "s"}` : `Within ${minutes} minutes`;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function slugify(text) {
  const slug = String(text || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);
  return (slug || "routine") + "_" + Math.random().toString(36).slice(2, 6);
}

// Which due dates a complete or remove step acts on. "any" means no check.
const DUE_CHOICES = [
  ["any", "Any date"],
  ["0", "Due today only"],
  ["1", "Due today or tomorrow"],
  ["7", "Due within 7 days"],
  ["21", "Due within 3 weeks"],
];

function dueOptions(selected) {
  return DUE_CHOICES.map(([value, label]) => `<option value="${value}" ${value === selected ? "selected" : ""}>${esc(label)}</option>`).join("");
}

// The stored due check for a step, or undefined when the step has no check.
function dueFrom(op, choice, overdue, undated) {
  if (op === "add" || !choice || choice === "any") return undefined;
  return { within: Number(choice), overdue: overdue !== false, undated: Boolean(undated) };
}

function dueSummary(due) {
  if (!due || due.within === null || due.within === undefined) return "";
  return due.within === 0 ? " · due today" : ` · due within ${due.within} days`;
}

function splitStates(text) {
  return String(text || "").split(",").map((s) => s.trim()).filter(Boolean);
}

function hhmm(time) {
  return String(time || "07:00:00").slice(0, 5);
}

function formatTime(iso) {
  if (!iso) return "–";
  const date = new Date(iso);
  return isNaN(date) ? iso : date.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
}

class DailyRoutinePanel extends HTMLElement {
  constructor() {
    super();
    this._data = null;
    this._view = { name: "rooms" };
    this._draft = null;
    this._preview = null;
    this._notice = null;
    this._error = false;
    this._wired = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._data) this._load();
  }

  connectedCallback() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._wire();
    this._timer = setInterval(() => {
      if (document.hidden) return;
      if (["rooms", "room", "all", "todos"].includes(this._view.name)) this._refresh();
      if (this._view.name === "activity") this._loadActivity(true);
    }, REFRESH_MS);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  _wire() {
    if (this._wired) return;
    this._wired = true;
    this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
    this.shadowRoot.addEventListener("change", (e) => this._onField(e));
    this.shadowRoot.addEventListener("input", (e) => this._onField(e, true));
  }

  async _call(message) {
    return this._hass.callWS(message);
  }

  async _load(silent = false) {
    if (!this._hass) return;
    // The thin progress bar shows through the host class, so no redraw is needed to start it.
    this.classList.toggle("busy", !silent);
    let changed = true;
    try {
      const data = await this._call({ type: `${DOMAIN}/overview` });
      const json = JSON.stringify(data);
      changed = json !== this._dataJson || !this._data;
      this._data = data;
      this._dataJson = json;
    } catch (err) {
      changed = false;
      if (!silent) this._showNotice(err.message || String(err), true);
    }
    this.classList.remove("busy");
    if (this._data) {
      const { revision, paused, statuses } = this._data;
      this._statusJson = JSON.stringify({ revision, paused, statuses });
    }
    // A background refresh that brought nothing new leaves the page alone.
    if (silent && !changed && this._rendered) return;
    this._render();
  }

  // The background refresh only asks for run states, which are small. Routines themselves are
  // reloaded only when the revision says they changed.
  async _refresh() {
    if (!this._hass || !this._data) return this._load(true);
    try {
      const status = await this._call({ type: `${DOMAIN}/status` });
      if (status.revision !== this._data.revision) return this._load(true);
      const json = JSON.stringify(status);
      if (json === this._statusJson) return;
      this._statusJson = json;
      this._data.statuses = status.statuses;
      this._data.paused = status.paused;
      this._render();
    } catch (_err) {
      // Nothing to do: the next refresh tries again.
    }
  }

  async _exportRoutines() {
    const backup = await this._call({ type: `${DOMAIN}/export` });
    const url = URL.createObjectURL(new Blob([JSON.stringify(backup, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "daily-routine-backup.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    this._showNotice(`Exported ${backup.routines.length} routine${backup.routines.length === 1 ? "" : "s"}.`);
  }

  async _importFile(file) {
    try {
      await this._importText(await file.text());
    } catch (err) {
      this._showNotice(err.message || String(err), true);
    }
    this._render();
  }

  async _importText(text) {
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch (_err) {
      throw new Error("That file isn't a Daily Routine backup. Export one from this page to get a file that loads.");
    }
    const routines = Array.isArray(parsed) ? parsed : parsed?.routines;
    if (!Array.isArray(routines)) throw new Error("That file has no routines in it.");
    const result = await this._call({ type: `${DOMAIN}/import`, routines });
    await this._load();
    const count = `${result.imported} routine${result.imported === 1 ? "" : "s"}`;
    const skipped = (result.problems || []).map((p) => `${p.id}: ${p.reason}`).join(" ");
    this._showNotice(`Imported ${count}.${skipped ? ` Skipped: ${skipped}` : ""}`, result.imported === 0 && skipped !== "");
  }

  _showNotice(text, error = false) {
    this._notice = text;
    this._error = error;
  }

  // ----- data helpers -----

  _devices() {
    return this._data?.devices || [];
  }

  _defaultDevice(roomId) {
    if (!roomId) return "";
    return this._devices().find((d) => d.area_id === roomId)?.id || "";
  }

  _linkedName(deviceId) {
    const device = this._devices().find((d) => d.id === deviceId);
    return device ? device.name : "Daily Routine (no device)";
  }

  _deviceOptions(selected, roomId) {
    const devices = [...this._devices()].sort((a, b) =>
      Number(roomId && b.area_id === roomId) - Number(roomId && a.area_id === roomId) || a.name.localeCompare(b.name));
    const none = `<option value="" ${selected ? "" : "selected"}>Daily Routine (no device)</option>`;
    const options = devices.map((d) => {
      const place = d.area_name ? ` · ${d.area_name}` : "";
      return `<option value="${esc(d.id)}" ${d.id === selected ? "selected" : ""}>${esc(d.name)}${esc(place)}</option>`;
    });
    return [none, ...options].join("");
  }

  _areas() {
    return this._data?.areas || [];
  }

  // Lookup tables, rebuilt only when new data arrives (not on every call).
  _index() {
    if (this._idx && this._idx.source === this._data) return this._idx;
    const areas = this._areas();
    const byArea = new Map(areas.map((a) => [a.id, a]));
    const seen = new Map();
    areas.forEach((a) => a.entities.forEach((e) => seen.set(e.entity_id, e)));
    const entities = [...seen.values()].sort((a, b) => a.name.localeCompare(b.name));
    const byEntity = new Map(entities.map((e) => [e.entity_id, e]));
    this._idx = { source: this._data, byArea, entities, byEntity };
    return this._idx;
  }

  _area(id) {
    return this._index().byArea.get(id);
  }

  _allEntities() {
    return this._index().entities;
  }

  _roomEntities(roomId) {
    return roomId ? this._area(roomId)?.entities || [] : this._allEntities();
  }

  _roomSensors(roomId) {
    return this._roomEntities(roomId).filter((e) => e.domain === "binary_sensor" && SENSOR_CLASSES.includes(e.device_class));
  }

  _routinesFor(roomId) {
    return (this._data?.routines || []).filter((r) => r.kind !== "todo" && (r.room || null) === (roomId || null));
  }

  _nameFor(entityId) {
    const entity = this._index().byEntity.get(entityId)
      || this._data?.people?.find((p) => p.entity_id === entityId)
      || this._data?.sensors?.find((x) => x.entity_id === entityId)
      || this._data?.all_entities?.find((x) => x.entity_id === entityId);
    return entity?.name || entityId;
  }

  _describeTrigger(routine) {
    const when = TRIGGER_LABELS[routine.trigger] || routine.trigger;
    const windowText = routine.active_from && routine.active_to ? ` · only ${hhmm(routine.active_from)}–${hhmm(routine.active_to)}` : "";
    if (VALUE_TRIGGERS.includes(routine.trigger)) {
      const level = `${routine.value_threshold}${this._unitSuffix(routine.value_entity)}`;
      const word = routine.trigger === "value_above" ? "above" : "below";
      return `${this._nameFor(routine.value_entity)} ${word} ${level}${windowText}`;
    }
    if (TODO_DONE_TRIGGERS.includes(routine.trigger)) {
      const item = routine.watch_item ? `“${routine.watch_item}”` : "any item";
      return `${item} checked off ${this._nameFor(routine.watch_entity)}${windowText}`;
    }
    if (STATE_TRIGGERS.includes(routine.trigger)) {
      const hold = routine.hold_minutes ? ` for ${routine.hold_minutes} min` : "";
      return `${this._nameFor(routine.watch_entity)} becomes ${(routine.watch_states || []).join(" or ")}${hold}${windowText}`;
    }
    if (PRESENCE_TRIGGERS.includes(routine.trigger)) {
      const who = PER_PERSON_TRIGGERS.includes(routine.trigger) && routine.people.length
        ? ` · ${routine.people.map((id) => this._nameFor(id)).join(", ")}`
        : "";
      return `${when} (${this._zoneName(routine.zone)})${who}${windowText}`;
    }
    if (routine.trigger === "time") return `${when} ${hhmm(routine.time)}${windowText}`;
    if (routine.trigger === "sunrise" || routine.trigger === "sunset") {
      const offset = routine.offset_minutes ? ` ${routine.offset_minutes > 0 ? "+" : ""}${routine.offset_minutes} min` : "";
      return `${when}${offset}${windowText}`;
    }
    return `${when}${windowText}`;
  }

  _zoneName(zoneId) {
    const zones = this._data?.zones || [{ entity_id: "zone.home", name: "Home" }];
    return zones.find((z) => z.entity_id === (zoneId || "zone.home"))?.name || zoneId || "Home";
  }

  _templates() {
    return TEMPLATES;
  }

  _catchUpField(current) {
    const value = Number(current) || 0;
    const choices = CATCH_UP_CHOICES.includes(value) ? CATCH_UP_CHOICES : [...CATCH_UP_CHOICES, value].sort((a, b) => a - b);
    const options = choices.map((m) => `<option value="${m}" ${m === value ? "selected" : ""}>${esc(catchUpLabel(m))}</option>`).join("");
    return `<label>If Home Assistant was restarting when it was due<select data-f="catch_up_minutes">${options}</select></label>
      <p class="muted small">The routine still runs once Home Assistant is back, as long as that is within this window.</p>`;
  }

  _zoneField(draft) {
    const zones = this._data?.zones || [{ entity_id: "zone.home", name: "Home" }];
    const current = draft.zone || "zone.home";
    const options = zones.map((z) => `<option value="${esc(z.entity_id)}" ${z.entity_id === current ? "selected" : ""}>${esc(z.name)}</option>`).join("");
    return `<label>Place<select data-f="zone">${options}</select></label>
      <p class="muted small">The Home Assistant zone that counts as leaving or arriving, for example Home or Work.</p>`;
  }

  _unitSuffix(entityId) {
    const unit = this._data?.sensors?.find((s) => s.entity_id === entityId)?.unit || "";
    if (!unit) return "";
    return unit === "%" ? "%" : ` ${unit}`;
  }

  _trackedPeople() {
    return this._data.people.filter((p) => p.tracked && !p.hidden).map((p) => p.entity_id);
  }

  _pickSensor(roomId, deviceClass) {
    const pool = roomId ? this._roomEntities(roomId) : this._allEntities();
    return pool.find((e) => e.domain === "sensor" && e.device_class === deviceClass)?.entity_id || "";
  }

  _stateFields(draft) {
    return `<label>Device to watch<select data-f="watch_entity" data-rerender>${this._entityOptions(draft.watch_entity)}</select></label>
      <label>Run when it changes to<input data-f="watch_states" list="state-suggestions" value="${esc(draft.watch_states)}" placeholder="for example on or locked"></label>
      <datalist id="state-suggestions">${this._stateOptionsFor(draft.watch_entity)}</datalist>
      <p class="muted small">Separate several states with commas. Suggestions are the states this device has reported in the last 30 days.</p>
      <label>Hold for (minutes)<input type="number" min="0" max="1440" data-f="hold_minutes" value="${esc(draft.hold_minutes)}"></label>
      <p class="muted small">Leave at 0 to run straight away. With a hold time, the device must still be in one of those states after that many minutes.</p>`;
  }

  _todoDoneFields(draft) {
    return `<label>To-do list<select data-f="watch_entity">${this._pickerOptions(this._data.todo_lists || [], draft.watch_entity, "Choose a to-do list…")}</select></label>
      <label>Item name (optional)<input data-f="watch_item" value="${esc(draft.watch_item)}" placeholder="leave empty for any item"></label>
      <p class="muted small">Runs when the item is checked off. With no name, it runs for any item. Items checked off while Home Assistant was stopped run it once after startup.</p>`;
  }

  _valueFields(draft, roomId) {
    const inRoom = (sensor) => roomId && this._roomEntities(roomId).some((e) => e.entity_id === sensor.entity_id);
    const sensors = [...(this._data.sensors || [])].sort((a, b) => Number(inRoom(b)) - Number(inRoom(a)) || a.name.localeCompare(b.name));
    const options = [`<option value="">Choose…</option>`, ...sensors.map((sensor) => {
      const unit = sensor.unit ? ` (${sensor.unit})` : "";
      return `<option value="${esc(sensor.entity_id)}" ${sensor.entity_id === draft.value_entity ? "selected" : ""}>${esc(sensor.name)}${esc(unit)}</option>`;
    })].join("");
    const above = draft.trigger === "value_above";
    const unit = this._unitSuffix(draft.value_entity).trim();
    return `<label>Sensor<select data-f="value_entity">${options}</select></label>
      <label>${above ? "Turn on when it goes above" : "Run when it drops below"}${unit ? ` (${esc(unit)})` : ""}
        <input type="number" step="any" data-f="value_threshold" value="${esc(draft.value_threshold)}"></label>
      ${above ? `<label>Turn back off when it drops below (optional)${unit ? ` (${esc(unit)})` : ""}
        <input type="number" step="any" data-f="off_threshold" value="${esc(draft.off_threshold)}"></label>
        <p class="muted small">Leave empty to turn things off yourself. Otherwise a second routine, named “(off)”, turns them back off when the level drops.</p>` : ""}`;
  }

  // ----- draft <-> stored routine -----

  _newDraft(roomId) {
    return {
      id: "", name: "", room: roomId || null, device_id: this._defaultDevice(roomId), enabled: true, zone: "zone.home",
      value_entity: "", value_threshold: "", off_threshold: "", watch_entity: "", watch_states: "", hold_minutes: 0, watch_item: "", active_from: "", active_to: "",
      trigger: "time", time: "07:00:00", offset_minutes: 0, days: [...WEEKDAYS],
      people: [], sensors: [], clear_minutes: 0,
      conditions: [], conditions_match: "all", repeat: false, catch_up_minutes: 0, retries: 0, skip_noop: true,
      notify_on_run: false, notify_on_skip: false, notify_on_error: true, notify_on_health: false,
      actions: [{ kind: "service", service: "", targetMode: roomId ? "area" : "entities", area: roomId || "", entities: [], data: "" }],
      isNew: true,
    };
  }

  _applyTemplate(template, roomId) {
    const draft = this._newDraft(roomId);
    const spec = template.draft || {};
    draft.name = template.title === "Blank routine" ? "" : template.title;
    if (spec.trigger) draft.trigger = spec.trigger;
    if (spec.time) draft.time = spec.time;
    if (spec.clear_minutes) draft.clear_minutes = spec.clear_minutes;
    if (spec.useRoomSensors) draft.sensors = this._roomSensors(roomId).map((e) => e.entity_id);
    if (spec.valueClass) draft.value_entity = this._pickSensor(roomId, spec.valueClass);
    if (spec.valueThreshold !== undefined) draft.value_threshold = String(spec.valueThreshold);
    if (spec.offThreshold !== undefined) draft.off_threshold = String(spec.offThreshold);
    if (spec.usePeopleList) draft.people = this._trackedPeople();
    if (spec.useAllPeople) draft.people = this._trackedPeople();
    if (spec.quietMinutes) {
      const houseSensors = this._data.binary_sensors.filter((s) => SENSOR_CLASSES.includes(s.device_class)).map((s) => s.entity_id);
      draft.conditions.push({ kind: "sensors", mode: "quiet", minutes: spec.quietMinutes, sensors: houseSensors });
    }
    if (spec.actions) {
      draft.actions = spec.actions.map((a) => ({ kind: "service", service: a.service, targetMode: a.targetMode, area: roomId || "", entities: [], data: "" }));
    }
    return draft;
  }

  _draftFromRoutine(routine) {
    const areaIds = this._areas().map((a) => a.id);
    return {
      id: routine.id, name: routine.name, room: routine.room || null, device_id: routine.device_id || "", enabled: routine.enabled, zone: routine.zone || "zone.home",
      value_entity: routine.value_entity || "", watch_entity: routine.watch_entity || "", watch_states: (routine.watch_states || []).join(", "), hold_minutes: routine.hold_minutes || 0, watch_item: routine.watch_item || "", value_threshold: routine.value_threshold ?? "", off_threshold: routine.off_threshold ?? "",
      active_from: routine.active_from ? hhmm(routine.active_from) : "", active_to: routine.active_to ? hhmm(routine.active_to) : "",
      trigger: routine.trigger, time: routine.time, offset_minutes: routine.offset_minutes || 0, days: [...routine.days],
      people: [...routine.people], sensors: [...routine.sensors], clear_minutes: routine.clear_minutes,
      conditions: (routine.conditions || []).map((c) => ({ ...c, people: [...(c.people || [])], sensors: [...(c.sensors || [])] })),
      conditions_match: routine.conditions_match || "all", repeat: routine.repeat,
      catch_up_minutes: routine.catch_up_minutes, retries: routine.retries, skip_noop: routine.skip_noop,
      notify_on_run: routine.notify_on_run, notify_on_skip: routine.notify_on_skip, notify_on_error: routine.notify_on_error,
      notify_on_health: Boolean(routine.notify_on_health),
      actions: routine.actions.map((a) => {
        if ("todo" in a) return {
          kind: "todo", todoOp: a.todo.op, todoList: a.todo.list, todoItem: a.todo.item,
          todoDue: a.todo.due ? String(a.todo.due.within ?? "any") : "any",
          todoOverdue: a.todo.due ? a.todo.due.overdue : true,
          todoUndated: a.todo.due ? a.todo.due.undated : false,
          service: "", targetMode: "none", area: "", entities: [], data: "",
        };
        if ("delay" in a) return { kind: "delay", seconds: a.delay, service: "", targetMode: "none", area: "", entities: [], data: "" };
        const areas = a.target?.area_id || [];
        const entities = a.target?.entity_id || [];
        let targetMode = "none";
        if (areas.length > 1 && areas.length >= areaIds.length) targetMode = "all_areas";
        else if (areas.length) targetMode = "area";
        else if (entities.length) targetMode = "entities";
        return {
          kind: "service", service: a.service, targetMode, area: areas[0] || "", entities: [...entities],
          data: Object.keys(a.data || {}).length ? JSON.stringify(a.data, null, 2) : "",
        };
      }),
      isNew: false,
    };
  }

  _draftToRaw(draft) {
    const areaIds = this._areas().map((a) => a.id);
    const actions = draft.actions.map((step, index) => {
      if (step.kind === "delay") return { delay: Number(step.seconds) || 0 };
      if (step.kind === "todo") {
        const item = (step.todoItem || "").trim();
        if (!step.todoList) throw new Error(`Step ${index + 1}: choose a to-do list.`);
        if (!item) throw new Error(`Step ${index + 1}: type the item text.`);
        const todo = { op: step.todoOp || "add", list: step.todoList, item };
        const due = dueFrom(todo.op, step.todoDue, step.todoOverdue, step.todoUndated);
        if (due) todo.due = due;
        return { todo };
      }
      if (!step.service.trim()) throw new Error(`Step ${index + 1}: choose a service.`);
      let target = {};
      if (step.targetMode === "area" && step.area) target = { area_id: [step.area] };
      else if (step.targetMode === "all_areas") target = { area_id: areaIds };
      else if (step.targetMode === "entities" && step.entities.length) target = { entity_id: [...step.entities] };
      let data = {};
      if (step.data && step.data.trim()) {
        try {
          data = JSON.parse(step.data);
        } catch (_err) {
          throw new Error(`Step ${index + 1}: extra data must be JSON, for example {"brightness": 128}.`);
        }
      }
      const raw = { service: step.service.trim(), data };
      if (Object.keys(target).length) raw.target = target;
      return raw;
    });
    return {
      id: draft.id || slugify(draft.name),
      name: draft.name.trim(),
      enabled: draft.enabled,
      room: draft.room || null,
      device_id: draft.device_id || null,
      zone: draft.zone || "zone.home",
      value_entity: draft.value_entity || null,
      watch_entity: draft.watch_entity || null,
      watch_states: splitStates(draft.watch_states),
      hold_minutes: Number(draft.hold_minutes) || 0,
      watch_item: String(draft.watch_item || "").trim(),
      value_threshold: draft.value_threshold === "" || draft.value_threshold == null ? null : Number(draft.value_threshold),
      off_threshold: draft.off_threshold === "" || draft.off_threshold == null ? null : Number(draft.off_threshold),
      active_from: draft.active_from || null,
      active_to: draft.active_to || null,
      trigger: draft.trigger,
      time: draft.time,
      offset_minutes: Number(draft.offset_minutes) || 0,
      days: draft.days,
      people: draft.people,
      sensors: draft.sensors,
      clear_minutes: Number(draft.clear_minutes) || 0,
      conditions: draft.conditions
        .map((c) => {
          if (c.kind === "presence") return { kind: "presence", who: c.who, where: c.where, people: [...c.people], zone: c.zone || "zone.home" };
          if (c.kind === "sensors") return { kind: "sensors", mode: c.mode, minutes: Number(c.minutes) || 0, sensors: [...c.sensors] };
          return { kind: "state", entity_id: c.entity_id, state: c.state };
        })
        .filter((c) => c.kind !== "state" || (c.entity_id && c.state)),
      conditions_match: draft.conditions_match === "any" ? "any" : "all",
      repeat: draft.repeat,
      catch_up_minutes: Number(draft.catch_up_minutes) || 0,
      retries: Number(draft.retries) || 0,
      skip_noop: draft.skip_noop,
      notify_on_run: draft.notify_on_run,
      notify_on_skip: draft.notify_on_skip,
      notify_on_error: draft.notify_on_error,
      notify_on_health: Boolean(draft.notify_on_health),
      actions,
    };
  }

  // ----- events -----

  async _onClick(event) {
    const el = event.target.closest?.("[data-act]");
    if (!el) return;
    const act = el.dataset.act;
    const room = el.dataset.room !== undefined ? (el.dataset.room || null) : this._view.room ?? null;
    try {
      switch (act) {
        case "refresh":
          await this._load();
          return;
        case "open-room":
          this._view = { name: "room", room: el.dataset.room || null };
          this._notice = null;
          break;
        case "back":
          this._view = { name: "rooms" };
          this._draft = null;
          this._preview = null;
          this._notice = null;
          break;
        case "new":
          this._view = { name: "templates", room };
          break;
        case "template": {
          const template = TEMPLATES.find((t) => t.id === el.dataset.tpl);
          this._draft = this._applyTemplate(template, room);
          this._view = { name: "editor", room };
          this._preview = null;
          this._notice = null;
          break;
        }
        case "edit": {
          const routine = this._data.routines.find((r) => r.id === el.dataset.id);
          if (!routine) return;
          this._draft = this._draftFromRoutine(routine);
          if (STATE_TRIGGERS.includes(routine.trigger)) this._loadStateHints(routine.watch_entity);
          this._view = { name: "editor", room: routine.room || null };
          this._preview = null;
          this._notice = null;
          break;
        }
        case "run": {
          const result = await this._call({ type: `${DOMAIN}/routine/run`, routine_id: el.dataset.id });
          this._showNotice(result.ran ? "Routine ran." : "Did not run – its conditions are not met right now.", !result.ran);
          break;
        }
        case "duplicate": {
          const routine = this._data.routines.find((r) => r.id === el.dataset.id);
          if (!routine) return;
          // A copy is a new routine: it gets its own id when saved.
          this._draft = { ...this._draftFromRoutine(routine), id: "", name: `${routine.name} (copy)`, isNew: true };
          this._view = { name: "editor", room: routine.room || null };
          this._preview = null;
          this._notice = null;
          break;
        }
        case "export":
          await this._exportRoutines();
          break;
        case "import":
          this.shadowRoot.querySelector("[data-import-file]").click();
          return;
        case "toggle-enabled": {
          const routine = this._data.routines.find((r) => r.id === el.dataset.id);
          if (!routine) return;
          await this._call({ type: `${DOMAIN}/routine/save`, routine: { ...routine, enabled: !routine.enabled } });
          break;
        }
        case "delete": {
          const id = el.dataset.id || this._draft?.id;
          if (!id || !window.confirm("Delete this routine? Its service device is removed too.")) return;
          await this._call({ type: `${DOMAIN}/routine/delete`, routine_id: id });
          this._view = { name: "room", room: this._view.room ?? null };
          this._draft = null;
          this._showNotice("Routine deleted.");
          break;
        }
        case "todo-new":
          this._todoDraft = this._newTodoDraft();
          this._view = { name: "todo-editor" };
          this._notice = null;
          break;
        case "todo-edit": {
          const rule = this._todoRule(el.dataset.id);
          if (!rule) return;
          this._todoDraft = this._todoDraftFromRoutine(rule);
          this._view = { name: "todo-editor" };
          this._notice = null;
          this._loadStateHints(rule.watch_entity);
          break;
        }
        case "todo-toggle": {
          const rule = this._todoRule(el.dataset.id);
          if (!rule) return;
          await this._call({ type: `${DOMAIN}/routine/save`, routine: { ...rule, enabled: !rule.enabled } });
          await this._load();
          return;
        }
        case "todo-save":
          await this._saveTodoDraft();
          break;
        case "todo-cancel":
          this._view = { name: "todos" };
          this._todoDraft = null;
          this._notice = null;
          break;
        case "todo-delete": {
          const id = el.dataset.id || this._todoDraft?.id;
          if (!id || !window.confirm("Delete this to-do rule? Items already on the list stay.")) return;
          await this._call({ type: `${DOMAIN}/routine/delete`, routine_id: id });
          this._view = { name: "todos" };
          this._todoDraft = null;
          this._showNotice("To-do rule deleted.");
          await this._load();
          return;
        }
        case "tab": {
          const tab = el.dataset.tab;
          this._view = { name: tab === "routines" ? "all" : tab === "todos" ? "todos" : tab === "settings" ? "settings" : tab === "activity" ? "activity" : "rooms" };
          this._draft = null;
          this._preview = null;
          this._notice = null;
          if (tab === "activity") {
            this._render();
            await this._loadActivity();
            return;
          }
          break;
        }
        case "activity-window":
          this._activityWindow = el.dataset.window;
          await this._loadActivity();
          return;
        case "activity-filter":
          this._activityFilter = el.dataset.filter;
          break;
        case "tracked-people":
          this._draft.people = this._data.people.filter((p) => p.tracked && !p.hidden).map((p) => p.entity_id);
          break;
        case "pause":
          await this._call({ type: `${DOMAIN}/pause`, paused: !this._data.paused });
          break;
        case "add-step":
          this._draft.actions.push({ kind: "service", service: "", targetMode: this._draft.room ? "area" : "entities", area: this._draft.room || "", entities: [], data: "" });
          break;
        case "add-delay":
          this._draft.actions.push({ kind: "delay", seconds: 5, service: "", targetMode: "none", area: "", entities: [], data: "" });
          break;
        case "add-todo":
          this._draft.actions.push({ kind: "todo", todoOp: "add", todoList: "", todoItem: "", service: "", targetMode: "none", area: "", entities: [], data: "" });
          break;
        case "remove-step":
          this._draft.actions.splice(Number(el.dataset.i), 1);
          break;
        case "move-step": {
          const i = Number(el.dataset.i);
          const j = i + Number(el.dataset.dir);
          if (j < 0 || j >= this._draft.actions.length) return;
          [this._draft.actions[i], this._draft.actions[j]] = [this._draft.actions[j], this._draft.actions[i]];
          break;
        }
        case "add-condition": {
          const kind = el.dataset.kind || "state";
          const zone = this._draft.zone || "zone.home";
          if (kind === "presence") this._draft.conditions.push({ kind, who: "everyone", where: "away", people: [], zone });
          else if (kind === "sensors") this._draft.conditions.push({ kind, mode: "quiet", minutes: 10, sensors: [] });
          else this._draft.conditions.push({ kind: "state", entity_id: "", state: "on" });
          break;
        }
        case "remove-condition":
          this._draft.conditions.splice(Number(el.dataset.i), 1);
          break;
        case "test":
          await this._testDraft();
          break;
        case "save":
          await this._saveDraft();
          break;
        case "cancel":
          this._view = { name: "room", room: this._draft?.room ?? this._view.room ?? null };
          this._draft = null;
          this._preview = null;
          this._notice = null;
          break;
        default:
          return;
      }
    } catch (err) {
      this._showNotice(err.message || String(err), true);
    }
    this._render();
  }

  _onField(event, live = false) {
    const el = event.target;
    if (!el.dataset) return;
    if (el.dataset.tf !== undefined) {
      this._onTodoField(el, live);
      return;
    }
    if (el.dataset.importFile !== undefined) {
      const file = el.files?.[0];
      el.value = "";
      if (file) this._importFile(file);
      return;
    }
    if (!live && el.dataset.setting !== undefined) {
      if (el.value !== "") this._saveSettings({ [el.dataset.setting]: Number(el.value) });
      return;
    }
    if (!live && (el.dataset.showPerson !== undefined || el.dataset.notifyTarget !== undefined)) {
      if (el.dataset.showPerson !== undefined) {
        this._saveSettings({ hidden_people: this._hiddenFromDom() });
      } else {
        const targets = [...this.shadowRoot.querySelectorAll("[data-notify-target]")]
          .filter((box) => box.checked)
          .map((box) => box.dataset.notifyTarget);
        this._saveSettings({ notify_services: targets });
      }
      return;
    }
    if (el.dataset.search !== undefined) {
      this._query = el.value;
      const results = this.shadowRoot.querySelector("[data-results]");
      if (results) results.innerHTML = this._allResults();
      const count = this.shadowRoot.querySelector("[data-count]");
      if (count) count.textContent = this._countText();
      return;
    }
    if (!this._draft) return;
    const value = el.type === "checkbox" ? el.checked : el.value;
    const d = this._draft;

    if (el.dataset.day !== undefined) {
      const set = new Set(d.days);
      el.checked ? set.add(el.dataset.day) : set.delete(el.dataset.day);
      d.days = WEEKDAYS.filter((day) => set.has(day));
      return;
    }
    if (el.dataset.list) {
      const list = d[el.dataset.list];
      const i = list.indexOf(el.dataset.val);
      if (el.checked && i < 0) list.push(el.dataset.val);
      if (!el.checked && i >= 0) list.splice(i, 1);
      this._render();
      return;
    }
    if (el.dataset.f) {
      const field = el.dataset.f;
      if (field === "watch_entity" && !live) this._loadStateHints(value);
      if (field === "time") d.time = value.length === 5 ? `${value}:00` : value;
      else if (field === "enabled" || field === "repeat" || field === "skip_noop" || field === "notify_on_run" || field === "notify_on_skip" || field === "notify_on_error" || field === "notify_on_health") d[field] = Boolean(value);
      else d[field] = value;
      if (el.dataset.rerender !== undefined && !live) this._render();
      return;
    }
    if (el.dataset.si !== undefined) {
      const step = d.actions[Number(el.dataset.si)];
      if (!step) return;
      if (el.dataset.sf === "actionChoice") {
        if (value === CUSTOM_ACTION) {
          step.custom = true;
          step.service = "";
        } else {
          step.custom = false;
          step.service = value;
        }
        if (!live) this._render();
        return;
      }
      step[el.dataset.sf] = el.dataset.sf === "seconds" ? Number(value) || 0 : value;
      if (el.dataset.sf === "entities") return;
      if (el.dataset.rerender !== undefined && !live) this._render();
      return;
    }
    if (el.dataset.sentity !== undefined) {
      const step = d.actions[Number(el.dataset.sentity)];
      const i = step.entities.indexOf(el.dataset.val);
      if (el.checked && i < 0) step.entities.push(el.dataset.val);
      if (!el.checked && i >= 0) step.entities.splice(i, 1);
      return;
    }
    if (el.dataset.ci !== undefined) {
      const cond = d.conditions[Number(el.dataset.ci)];
      if (!cond) return;
      if (el.dataset.cpeople !== undefined) {
        const i = cond.people.indexOf(el.dataset.cpeople);
        if (el.checked && i < 0) cond.people.push(el.dataset.cpeople);
        if (!el.checked && i >= 0) cond.people.splice(i, 1);
        return;
      }
      if (el.dataset.csensor !== undefined) {
        const i = cond.sensors.indexOf(el.dataset.csensor);
        if (el.checked && i < 0) cond.sensors.push(el.dataset.csensor);
        if (!el.checked && i >= 0) cond.sensors.splice(i, 1);
        return;
      }
      if (cond.kind !== "state") {
        cond[el.dataset.cf] = el.dataset.cf === "minutes" ? Number(value) || 0 : value;
        if (el.dataset.rerender !== undefined && !live) this._render();
        return;
      }
      if (el.dataset.cf === "stateChoice") {
        cond.custom = value === CUSTOM_ACTION;
        cond.state = cond.custom ? "" : value;
        if (!live) this._render();
      } else {
        cond[el.dataset.cf] = value;
      }
    }
  }

  async _testDraft() {
    const raw = this._draftToRaw(this._draft);
    this._preview = await this._call({ type: `${DOMAIN}/routine/preview`, routine: raw });
    this._showNotice(null);
  }

  async _saveDraft() {
    const raw = this._draftToRaw(this._draft);
    const result = await this._call({ type: `${DOMAIN}/routine/save`, routine: raw });
    this._view = { name: "room", room: result.routine.room || null };
    this._draft = null;
    this._preview = null;
    this._showNotice(`Saved “${result.routine.name}”.`);
    await this._load();
  }

  // ----- rendering -----

  _render() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._rendered = true;
    let body;
    if (!this._data) body = `<div class="skeleton-grid" aria-busy="true" aria-label="Loading">${'<div class="skeleton-card"></div>'.repeat(4)}</div>`;
    else if (this._view.name === "room") body = this._renderRoom();
    else if (this._view.name === "all") body = this._renderAll();
    else if (this._view.name === "settings") body = this._renderSettings();
    else if (this._view.name === "activity") body = this._renderActivity();
    else if (this._view.name === "templates") body = this._renderTemplates();
    else if (this._view.name === "editor") body = this._renderEditor();
    else if (this._view.name === "todos") body = this._renderTodos();
    else if (this._view.name === "todo-editor") body = this._renderTodoEditor();
    else body = this._renderRooms();

    // The editor shows its own notice next to its buttons.
    const editing = this._view.name === "editor";
    const notice = !editing && this._notice ? `<div class="notice ${this._error ? "error" : ""}">${esc(this._notice)}</div>` : "";
    const banner = this._data?.paused && !editing ? this._pausedBanner() : "";
    this.shadowRoot.innerHTML = `${STYLES}<main>${this._renderHeader()}${banner}${notice}${body}</main>`;
  }

  _tabs() {
    const tabs = [
      { id: "rooms", label: "Rooms" },
      { id: "routines", label: "Routines" },
      { id: "todos", label: "To-dos" },
      { id: "activity", label: "Activity" },
    ];
    if (this._hass?.user?.is_admin) tabs.push({ id: "settings", label: "Settings" });
    return tabs;
  }

  _activeTab() {
    if (this._view.name === "all") return "routines";
    if (["todos", "todo-editor"].includes(this._view.name)) return "todos";
    if (this._view.name === "settings") return "settings";
    if (this._view.name === "activity") return "activity";
    return "rooms";
  }

  _renderHeader() {
    const inRoom = ["room", "templates", "editor"].includes(this._view.name);
    const back = inRoom ? `<button class="ghost" data-act="back">← Rooms</button>` : "";
    const paused = this._data?.paused;
    const active = this._activeTab();
    const tabs = this._tabs()
      .map((t) => `<button role="tab" data-act="tab" data-tab="${t.id}" aria-selected="${t.id === active}">${t.label}</button>`)
      .join("");
    return `<header>
      <div class="title">${back}<h1>${esc(this._titleFor())}</h1></div>
      <nav role="tablist" aria-label="Daily Routine sections">${tabs}</nav>
      <div class="actions">
        ${this._data ? `<button class="${paused ? "warn" : "ghost"}" data-act="pause">${paused ? "Resume routines" : "Pause all"}</button>` : ""}
        <button class="ghost" data-act="refresh" title="Refresh">⟳</button>
      </div>
    </header>`;
  }

  _titleFor() {
    if (this._view.name === "all") return "Routines";
    if (this._view.name === "todos") return "To-dos";
    if (this._view.name === "todo-editor") return this._todoDraft?.isNew ? "New to-do rule" : "Edit to-do rule";
    if (this._view.name === "settings") return "Settings";
    if (this._view.name === "activity") return "Activity";
    if (this._view.name === "rooms") return "Daily Routine";
    if (this._view.name === "editor") return this._draft?.isNew ? "New routine" : "Edit routine";
    if (this._view.name === "templates") return "New routine";
    return this._view.room ? this._area(this._view.room)?.name || "Room" : "Whole house";
  }

  _pausedBanner() {
    return `<div class="notice warn">All routines are paused. Manual runs still work.</div>`;
  }

  _renderRooms() {
    const areas = this._areas();
    const houseCount = this._routinesFor(null).length;
    const cards = [
      this._roomCard({ id: null, name: "Whole house", icon: "mdi:home-outline", entities: this._allEntities() }, houseCount),
      ...areas.map((area) => this._roomCard(area, this._routinesFor(area.id).length)),
    ];
    const empty = areas.length
      ? ""
      : `<section class="empty"><h2>No rooms yet</h2><p>Rooms come from Home Assistant. Create areas in <strong>Settings → Areas &amp; zones</strong> and they will appear here.</p></section>`;
    return `<p class="muted">Pick a room to see and add its routines.</p><div class="grid">${cards.join("")}</div>${empty}`;
  }

  _roomCard(room, routineCount) {
    const entities = room.entities || [];
    const lights = entities.filter((e) => e.domain === "light").length;
    const sensors = entities.filter((e) => e.domain === "binary_sensor" && SENSOR_CLASSES.includes(e.device_class)).length;
    const routines = room.id === null ? this._routinesFor(null) : this._routinesFor(room.id);
    const busy = routines.some((r) => ["waiting", "running", "error"].includes(this._data.statuses[r.id]?.state));
    const badge = busy ? `<span class="pill warn">Needs attention</span>` : "";
    return `<button class="card room" data-act="open-room" data-room="${esc(room.id || "")}">
      <ha-icon icon="${esc(room.icon || "mdi:floor-plan")}"></ha-icon>
      <div class="room-name">${esc(room.name)}</div>
      <div class="muted small">${lights} light${lights === 1 ? "" : "s"} · ${sensors} motion sensor${sensors === 1 ? "" : "s"}</div>
      <div class="room-foot"><span>${routineCount} routine${routineCount === 1 ? "" : "s"}</span>${badge}</div>
    </button>`;
  }

  _renderRoom() {
    const roomId = this._view.room ?? null;
    const routines = this._routinesFor(roomId);
    const list = routines.length
      ? routines.map((r) => this._routineCard(r)).join("")
      : `<section class="empty"><h2>No routines here yet</h2><p>Add one below, for example opening the curtains in the morning.</p></section>`;
    return `<div class="toolbar"><span class="muted">${routines.length} routine${routines.length === 1 ? "" : "s"}</span>
      <button class="primary" data-act="new" data-room="${esc(roomId || "")}">+ New routine</button></div>
      <div class="list">${list}</div>`;
  }

  _routineCard(routine, options = {}) {
    const status = this._data.statuses[routine.id] || {};
    const state = status.waiting ? "waiting" : status.state || "idle";
    const reason = !status.conditions_ok && status.block_reason ? ` · ${REASON_LABELS[status.block_reason] || status.block_reason}` : "";
    const detail = status.last_result && status.last_result !== "success" && state !== "waiting" ? ` · last: ${esc(status.last_result)}` : "";
    return `<article class="card routine ${routine.enabled ? "" : "disabled"}">
      <div class="routine-head">
        <div>
          <h3>${esc(routine.name)}</h3>
          ${options.showRoom ? `<div class="muted small">${esc(this._roomName(routine.room))}</div>` : ""}
          <div class="muted small">${esc(this._describeTrigger(routine))} · ${routine.days.length === 7 ? "every day" : routine.days.join(", ")}</div>
          <div class="muted small">Next: ${esc(formatTime(status.next_run))}${esc(reason)}${detail}</div>
          <div class="muted small">Linked to: ${esc(this._linkedName(routine.device_id))}</div>
          ${this._historyHtml(status)}
          ${this._healthHtml(routine)}
        </div>
        <span class="pill ${state === "error" ? "error" : state === "waiting" ? "warn" : ""}">${esc(STATE_LABELS[state] || state)}</span>
      </div>
      <div class="row-actions">
        <button class="ghost" data-act="run" data-id="${esc(routine.id)}">Run now</button>
        <button class="ghost" data-act="edit" data-id="${esc(routine.id)}">Edit</button>
        <button class="ghost" data-act="duplicate" data-id="${esc(routine.id)}">Duplicate</button>
        <button class="ghost" data-act="toggle-enabled" data-id="${esc(routine.id)}">${routine.enabled ? "Disable" : "Enable"}</button>
        <button class="ghost danger" data-act="delete" data-id="${esc(routine.id)}">Delete</button>
      </div>
    </article>`;
  }

  // The last few runs, newest first. Shown closed so the card stays short.
  _historyHtml(status) {
    const runs = (status.history || []).slice(0, 5);
    if (!runs.length) return "";
    const items = runs.map((run) => `<li>${esc(formatTime(run.at))} · ${esc(this._runText(run))}</li>`).join("");
    return `<details class="history"><summary class="muted small">Recent runs</summary><ul class="small">${items}</ul></details>`;
  }

  _runText(run) {
    if (run.outcome === "ran") return run.detail || "ran";
    if (run.outcome === "skipped") return `skipped: ${REASON_LABELS[run.detail] || humanize(run.detail) || "not run"}`;
    return `failed: ${run.detail}`;
  }

  _hiddenFromDom() {
    return [...this.shadowRoot.querySelectorAll("[data-show-person]")]
      .filter((box) => !box.checked)
      .map((box) => box.dataset.showPerson);
  }

  async _saveSettings(patch) {
    const current = this._data.settings || { hidden_people: [], notify_services: [] };
    try {
      const result = await this._call({ type: `${DOMAIN}/settings/save`, settings: { ...current, ...patch } });
      this._data.settings = result.settings;
      const hidden = new Set(result.settings.hidden_people);
      this._data.people = this._data.people.map((p) => ({ ...p, hidden: hidden.has(p.entity_id) }));
      this._showNotice("Settings saved.");
    } catch (err) {
      this._showNotice(err.message || String(err), true);
    }
    this._render();
  }

  _pickerPeople(draft) {
    return this._data.people.filter((p) => !p.hidden || draft.people.includes(p.entity_id));
  }

  // ----- to-do rules -----
  // A to-do rule is an ordinary routine: a device changes to a state, then one to-do step runs.

  _todoRules() {
    return (this._data?.routines || []).filter((r) => r.kind === "todo");
  }

  _todoRule(id) {
    return this._todoRules().find((r) => r.id === id) || null;
  }

  _newTodoDraft() {
    return { id: "", name: "", enabled: true, watch_entity: "", watch_states: "", hold_minutes: 0, op: "add", list: "", item: "", due: "any", overdue: true, undated: false, isNew: true };
  }

  _todoDraftFromRoutine(rule) {
    const step = rule.actions[0]?.todo || {};
    return {
      id: rule.id, name: rule.name || "", enabled: rule.enabled, watch_entity: rule.watch_entity || "",
      watch_states: (rule.watch_states || []).join(", "), hold_minutes: rule.hold_minutes || 0,
      op: step.op || "add", list: step.list || "", item: step.item || "", isNew: false,
      due: step.due ? String(step.due.within ?? "any") : "any",
      overdue: step.due ? step.due.overdue : true,
      undated: step.due ? step.due.undated : false,
    };
  }

  _todoSentence(rule) {
    const step = rule.actions[0]?.todo || {};
    return `${TODO_VERBS[step.op] || "Change"} “${step.item || ""}” on ${this._nameFor(step.list)}${dueSummary(step.due)}`;
  }

  _healthHtml(routine) {
    return (this._data.health?.[routine.id] || []).map((text) => `<div class="notice warn small">${esc(text)}</div>`).join("");
  }

  _renderTodos() {
    const rules = this._todoRules();
    const cards = rules.length
      ? rules.map((rule) => this._todoCard(rule)).join("")
      : `<section class="empty"><h2>No to-do rules yet</h2><p>A to-do rule watches a device or sensor. When it changes to a state you choose, an item is added, completed or removed on a to-do list. For example, add “Empty dishwasher” when the clean sensor turns off.</p></section>`;
    return `<div class="toolbar"><button class="primary" data-act="todo-new">+ New to-do rule</button></div>
      <div class="list">${cards}</div>`;
  }

  _todoCard(rule) {
    const status = this._data.statuses[rule.id] || {};
    const state = status.waiting ? "waiting" : status.state || "idle";
    return `<article class="card routine ${rule.enabled ? "" : "disabled"}">
      <div class="routine-head">
        <div>
          <h3>${esc(rule.name)}</h3>
          <div class="muted small">${esc(this._todoSentence(rule))}</div>
          <div class="muted small">${esc(this._describeTrigger(rule))}</div>
          ${this._historyHtml(status)}
          ${this._healthHtml(rule)}
        </div>
        <span class="pill ${state === "error" ? "error" : state === "waiting" ? "warn" : ""}">${esc(STATE_LABELS[state] || state)}</span>
      </div>
      <div class="row-actions">
        <button class="ghost" data-act="run" data-id="${esc(rule.id)}">Run now</button>
        <button class="ghost" data-act="todo-toggle" data-id="${esc(rule.id)}">${rule.enabled ? "Pause" : "Resume"}</button>
        <button class="ghost" data-act="todo-edit" data-id="${esc(rule.id)}">Edit</button>
        <button class="ghost danger" data-act="todo-delete" data-id="${esc(rule.id)}">Delete</button>
      </div>
    </article>`;
  }

  _pickerOptions(list, selected, placeholder) {
    const options = list.map((e) => `<option value="${esc(e.entity_id)}" ${e.entity_id === selected ? "selected" : ""}>${esc(e.name)}</option>`);
    return `<option value="">${esc(placeholder)}</option>${options.join("")}`;
  }

  _stateOptionsFor(entityId) {
    const domain = (entityId || "").split(".")[0];
    const kind = ["lock", "cover", "person", "device_tracker", "media_player", "climate"].includes(domain)
      ? domain
      : ["light", "switch", "fan", "input_boolean", "binary_sensor", "automation", "siren"].includes(domain) ? "on_off" : null;
    const current = (this._data?.all_entities || []).find((e) => e.entity_id === entityId)?.state;
    const seen = this._stateHints?.[entityId] || [];
    const known = kind ? CONDITION_STATES[kind] : [];
    const values = [...new Set([current, ...seen, ...known])].filter((v) => v && !["unknown", "unavailable"].includes(v));
    return values.map((v) => `<option value="${esc(v)}">${esc(STATE_NAMES[v] || v)}</option>`).join("");
  }

  // The states a device has reported in the last 30 days, so the state box can suggest them.
  async _loadStateHints(entityId) {
    if (!entityId || !this._hass) return;
    this._stateHints = this._stateHints || {};
    if (this._stateHints[entityId]) return;
    const start = new Date(Date.now() - 30 * 24 * 3600 * 1000).toISOString();
    try {
      const history = await this._hass.callWS({
        type: "history/history_during_period",
        start_time: start,
        entity_ids: [entityId],
        minimal_response: true,
        no_attributes: true,
      });
      const rows = history?.[entityId] || [];
      this._stateHints[entityId] = [...new Set(rows.map((r) => r.s ?? r.state).filter(Boolean))];
    } catch (_err) {
      this._stateHints[entityId] = [];
    }
    const root = this.shadowRoot;
    const list = root && typeof root.getElementById === "function" ? root.getElementById("state-suggestions") : null;
    if (list && (this._draft?.watch_entity === entityId || this._todoDraft?.watch_entity === entityId)) {
      list.innerHTML = this._stateOptionsFor(entityId);
    }
  }

  _renderTodoEditor() {
    const d = this._todoDraft || this._newTodoDraft();
    const ops = Object.entries(TODO_VERBS).map(([value, label]) =>
      `<option value="${value}" ${d.op === value ? "selected" : ""}>${esc(label)} an item</option>`).join("");
    return `<section class="editor">
      <label>Name<input data-tf="name" value="${esc(d.name)}" placeholder="Shown in the To-dos list (optional)"></label>
      <h4>When</h4>
      <label>Device or sensor to watch<select data-tf="watch_entity" data-rerender>${this._pickerOptions(this._data.all_entities || [], d.watch_entity, "Choose…")}</select></label>
      <label>Changes to<input data-tf="watch_states" list="state-suggestions" value="${esc(d.watch_states)}" placeholder="for example on or off"></label>
      <datalist id="state-suggestions">${this._stateOptionsFor(d.watch_entity)}</datalist>
      <p class="muted small">Separate several states with commas. Suggestions are the states this device has reported in the last 30 days.</p>
      <label>Hold for (minutes)<input type="number" min="0" max="1440" data-tf="hold_minutes" value="${esc(d.hold_minutes)}"></label>
      <p class="muted small">Leave at 0 to act straight away. With a hold time, the device must still be in one of those states after that many minutes.</p>
      <h4>Do</h4>
      <label>Action<select data-tf="op" data-rerender>${ops}</select></label>
      <label>To-do list<select data-tf="list">${this._pickerOptions(this._data.todo_lists || [], d.list, "Choose a to-do list…")}</select></label>
      <label>Item text<input data-tf="item" value="${esc(d.item)}" placeholder="for example Empty dishwasher"></label>
      ${d.op === "add" ? "" : `<label>Only when due<select data-tf="due" data-rerender>${dueOptions(d.due || "any")}</select></label>
      ${(d.due || "any") === "any" ? "" : `<label class="check"><input type="checkbox" data-tf="overdue" ${d.overdue !== false ? "checked" : ""}> Also when it is overdue</label>
      <label class="check"><input type="checkbox" data-tf="undated" ${d.undated ? "checked" : ""}> Also when it has no due date</label>`}`}
      <label class="check"><input type="checkbox" data-tf="enabled" ${d.enabled ? "checked" : ""}> Turned on</label>
      <div class="row-actions">
        <button data-act="todo-save">Save</button>
        <button class="ghost" data-act="todo-cancel">Cancel</button>
        ${d.isNew ? "" : `<button class="ghost danger" data-act="todo-delete" data-id="${esc(d.id)}">Delete</button>`}
      </div>
    </section>`;
  }

  _onTodoField(el, live) {
    const draft = this._todoDraft;
    if (!draft) return;
    const field = el.dataset.tf;
    draft[field] = el.type === "checkbox" ? el.checked : el.value;
    if (live) return;
    if (el.dataset.rerender !== undefined) {
      this._render();
      return;
    }
    if (field === "watch_entity") {
      this._render();
      this._loadStateHints(el.value);
    }
  }

  async _saveTodoDraft() {
    const d = this._todoDraft;
    const states = splitStates(d.watch_states);
    const item = String(d.item || "").trim();
    if (!d.watch_entity) throw new Error("Choose the device or sensor to watch.");
    if (!states.length) throw new Error("Type the state the device changes to.");
    if (!d.list) throw new Error("Choose a to-do list.");
    if (!item) throw new Error("Type the item text.");
    const name = String(d.name || "").trim() || `${TODO_VERBS[d.op] || "Change"} ${item}`;
    const rule = {
      id: d.id || slugify(name),
      name,
      kind: "todo",
      enabled: Boolean(d.enabled),
      room: null,
      device_id: null,
      zone: "zone.home",
      trigger: "state_is",
      watch_entity: d.watch_entity,
      watch_states: states,
      hold_minutes: Number(d.hold_minutes) || 0,
      value_entity: null,
      value_threshold: null,
      off_threshold: null,
      active_from: null,
      active_to: null,
      time: "07:00:00",
      offset_minutes: 0,
      days: [...WEEKDAYS],
      people: [],
      sensors: [],
      clear_minutes: 0,
      conditions: [],
      conditions_match: "all",
      repeat: true,
      catch_up_minutes: 0,
      retries: 0,
      skip_noop: true,
      notify_on_run: false,
      notify_on_skip: false,
      notify_on_error: true,
      actions: [{ todo: { op: d.op || "add", list: d.list, item, ...(dueFrom(d.op, d.due, d.overdue, d.undated) ? { due: dueFrom(d.op, d.due, d.overdue, d.undated) } : {}) } }],
    };
    const result = await this._call({ type: `${DOMAIN}/routine/save`, routine: rule });
    this._view = { name: "todos" };
    this._todoDraft = null;
    this._showNotice(`Saved “${result.routine.name}”.`);
    await this._load();
  }

  async _loadActivity(silent = false) {
    if (!this._hass) return;
    this._activityWindow = this._activityWindow || "7d";
    this._activityFilter = this._activityFilter || "all";
    try {
      const data = await this._call({ type: `${DOMAIN}/activity`, window: this._activityWindow });
      this._activity = data;
    } catch (err) {
      if (!silent) this._showNotice(err.message || String(err), true);
    }
    if (this._view.name === "activity") this._render();
  }

  _renderActivity() {
    const windows = `<div class="chips" role="group" aria-label="Period">${ACTIVITY_WINDOWS
      .map(([id, label]) => `<button class="${this._activityWindow === id ? "" : "ghost"}" data-act="activity-window" data-window="${id}" aria-pressed="${this._activityWindow === id}">${label}</button>`)
      .join("")}</div>`;
    const a = this._activity;
    if (!a) return `${windows}<p class="muted">Loading activity…</p>`;

    const t = a.totals;
    const tile = (label, value) => `<div class="tile"><span class="muted small">${esc(label)}</span><strong>${esc(String(value))}</strong></div>`;
    const tiles = `<div class="tiles">${tile("Triggered", t.triggered)}${tile("Succeeded", t.ran)}${tile("Failed", t.failed)}${tile("Skipped", t.skipped)}${tile("Success rate", t.success_rate === null ? "—" : `${t.success_rate}%`)}</div>`;

    const attention = [];
    for (const h of a.health) attention.push(`<li><strong>${esc(h.name)}</strong> needs attention: ${esc(h.problems.join(" "))}</li>`);
    for (const r of a.repeated_failures) attention.push(`<li><strong>${esc(r.name)}</strong> failed ${r.count} times in a row</li>`);
    if (a.quiet_routines.length) attention.push(`<li>No runs in this period: ${esc(a.quiet_routines.join(", "))}</li>`);
    const attentionCard = attention.length
      ? `<section class="card"><h3>Needs attention</h3><ul class="plain">${attention.join("")}</ul></section>`
      : "";

    const maxDay = Math.max(1, ...a.by_day.map((d) => d.ran + d.failed + d.skipped));
    const days = a.by_day.length
      ? `<div class="bars" role="img" aria-label="Runs per day">${a.by_day
          .map((d) => {
            const total = d.ran + d.failed + d.skipped;
            const seg = (n, cls) => (n ? `<span class="seg ${cls}" style="flex:${n}"></span>` : "");
            return `<div class="bar-col" title="${esc(d.day)}: ${total} triggered, ${d.ran} succeeded, ${d.failed} failed, ${d.skipped} skipped"><div class="bar" style="height:${Math.round((total / maxDay) * 100)}%">${seg(d.skipped, "skip")}${seg(d.failed, "fail")}${seg(d.ran, "ok")}</div><span class="muted small">${esc(d.day.slice(5))}</span></div>`;
          })
          .join("")}</div><p class="muted small"><span class="key ok"></span> Succeeded <span class="key fail"></span> Failed <span class="key skip"></span> Skipped</p>`
      : `<p class="muted small">Nothing has run in this period yet.</p>`;

    const reasonList = (rows, empty) => rows.length
      ? `<ul class="plain reasons">${rows.map((r) => `<li><span>${esc(activityReason(r.reason))}</span><strong>${r.count}</strong></li>`).join("")}</ul>`
      : `<p class="muted small">${esc(empty)}</p>`;

    const byRoutine = a.by_routine.length
      ? `<div class="table-wrap"><table><thead><tr><th>Routine</th><th>Succeeded</th><th>Failed</th><th>Skipped</th><th>Last run</th><th>Last failure</th></tr></thead><tbody>${a.by_routine
          .map((r) => `<tr><td>${esc(r.name)}${r.enabled ? "" : ` <span class="muted small">· off</span>`}</td><td>${r.ran}</td><td>${r.failed}</td><td>${r.skipped}</td><td>${esc(fmtTime(r.last_at))}</td><td>${r.last_failure ? `${esc(fmtTime(r.last_failure.at))}<br><span class="muted small">${esc(activityReason(r.last_failure.reason))}</span>` : "—"}</td></tr>`)
          .join("")}</tbody></table></div>`
      : `<p class="muted small">No routines yet.</p>`;

    const filter = this._activityFilter || "all";
    const filters = `<div class="chips" role="group" aria-label="Filter activity">${ACTIVITY_FILTERS
      .map(([id, label]) => `<button class="${filter === id ? "" : "ghost"}" data-act="activity-filter" data-filter="${id}" aria-pressed="${filter === id}">${label}</button>`)
      .join("")}</div>`;
    const shown = a.recent.filter((e) => filter === "all" || e.outcome === filter);
    const events = shown.length
      ? `<ul class="plain events">${shown
          .map((e) => `<li><time class="muted small">${esc(fmtTime(e.at))}</time> <strong>${esc(e.name)}</strong> <span class="pill ${e.outcome === "failed" ? "error" : e.outcome === "skipped" ? "warn" : ""}">${ACTIVITY_OUTCOMES[e.outcome] || esc(e.outcome)}</span>${e.reason ? `<div class="muted small">${esc(activityReason(e.reason))}</div>` : ""}${e.outcome === "ran" && e.detail ? `<div class="muted small">${esc(e.detail)}</div>` : ""}</li>`)
          .join("")}</ul>`
      : `<p class="muted small">Nothing in this period matches that filter.</p>`;

    return `${windows}
      ${tiles}
      ${attentionCard}
      <section class="card"><h3>Runs per day</h3>${days}</section>
      <section class="card"><h3>Why runs failed</h3>${reasonList(a.failure_reasons, "No failures in this period.")}</section>
      <section class="card"><h3>Why runs were skipped</h3><p class="muted small">Triggered, but the conditions were not met, so nothing ran.</p>${reasonList(a.skip_reasons, "Nothing was skipped in this period.")}</section>
      <section class="card"><h3>By routine</h3>${byRoutine}</section>
      <section class="card"><h3>Recent activity</h3>${filters}${events}</section>`;
  }

  _renderSettings() {
    const settings = this._data.settings || { hidden_people: [], notify_services: [] };
    const hidden = new Set(settings.hidden_people || []);
    const shown = this._data.people.filter((p) => !hidden.has(p.entity_id)).length;
    const people = this._data.people.length
      ? this._data.people
          .map((p) => `<label class="check"><input type="checkbox" data-show-person="${esc(p.entity_id)}" ${hidden.has(p.entity_id) ? "" : "checked"}> ${esc(p.name)}${p.tracked ? "" : ` <span class="muted small">· no location tracking</span>`}</label>`)
          .join("")
      : `<p class="muted small">No people in Home Assistant yet.</p>`;
    const selected = new Set(settings.notify_services || []);
    const targets = (this._data.notify_targets || []).length
      ? this._data.notify_targets
          .map((t) => `<label class="check"><input type="checkbox" data-notify-target="${esc(t.entity)}" ${selected.has(t.entity) ? "checked" : ""}> ${esc(t.name)}</label>`)
          .join("")
      : `<p class="muted small">No phones or notification services found. Install the Home Assistant app on a phone and it will appear here.</p>`;
    const timing = TIMING_FIELDS.map(
      ([key, label, help, min, max]) => `<label>${esc(label)}<input type="number" min="${min}" max="${max}" step="1" data-setting="${key}" value="${esc(settings[key] ?? TIMING_DEFAULTS[key])}"><span class="muted small">${esc(help)} (${min} to ${max})</span></label>`,
    ).join("");
    return `<section class="card">
        <h3>People in routines</h3>
        <p class="muted small">Choose who appears when you build a routine. Turn off profiles that aren't people, like a shared display. Routines that already use someone keep them.</p>
        <p class="pill">${shown} of ${this._data.people.length} shown</p>
        <div class="checks">${people}</div>
      </section>
      <section class="card">
        <h3>Backup</h3>
        <p class="muted small">Save every routine to a file, or load a file saved earlier. A routine in the file replaces the one with the same id.</p>
        <div class="row-actions">
          <button class="ghost" data-act="export">Export routines</button>
          <button class="ghost" data-act="import">Import routines…</button>
          <input type="file" accept="application/json,.json" data-import-file hidden>
        </div>
      </section>
      <section class="card">
        <h3>Notifications</h3>
        <p class="muted small">Routine runs, skips and failures always appear in Home Assistant. Pick any phones or devices to send them there too.</p>
        <div class="checks">${targets}</div>
      </section>
      <section class="card">
        <h3>Timing</h3>
        <p class="muted small">How patiently Daily Routine waits on slow devices and cloud lists, such as an iCloud to-do list after a restart. Changes are saved when you leave a box.</p>
        <div class="timing">${timing}</div>
      </section>`;
  }

  _roomName(roomId) {
    return roomId ? this._area(roomId)?.name || roomId : "Whole house";
  }

  _renderAll() {
    return `<div class="toolbar"><input type="search" data-search value="${esc(this._query || "")}"
        placeholder="Search by name, room, device, trigger or day" aria-label="Search routines"></div>
      <div class="muted small" data-count>${esc(this._countText())}</div>
      <div class="list" data-results>${this._allResults()}</div>`;
  }

  _countText() {
    const total = this._data?.routines?.length || 0;
    const shown = this._matchingRoutines().length;
    if (!total) return "No routines yet";
    return shown === total ? `${total} routine${total === 1 ? "" : "s"}` : `${shown} of ${total} routines`;
  }

  _matchingRoutines() {
    const terms = (this._query || "").toLowerCase().split(/\s+/).filter(Boolean);
    return (this._data?.routines || []).filter((routine) => routine.kind !== "todo").filter((routine) => {
      const text = this._searchText(routine).toLowerCase();
      return terms.every((term) => text.includes(term));
    });
  }

  _searchText(routine) {
    const status = this._data.statuses[routine.id] || {};
    return [
      routine.name,
      this._roomName(routine.room),
      this._describeTrigger(routine),
      this._linkedName(routine.device_id),
      routine.enabled ? "enabled" : "disabled",
      STATE_LABELS[status.state] || status.state || "",
      routine.days.join(" "),
    ].join(" ");
  }

  _allResults() {
    if (!(this._data?.routines || []).length) {
      return `<section class="empty"><h2>No routines yet</h2><p>Open a room and add your first routine.</p></section>`;
    }
    const matches = this._matchingRoutines();
    if (!matches.length) {
      return `<section class="empty"><h2>No matches</h2><p>Nothing matches “${esc(this._query)}”. Try fewer words.</p></section>`;
    }
    return matches.map((routine) => this._routineCard(routine, { showRoom: true })).join("");
  }

  _renderTemplates() {
    const roomId = this._view.room ?? null;
    const options = TEMPLATES.filter((t) => (roomId ? t.scope !== "house" : t.scope !== "room"));
    return `<p class="muted">Start from a template, or a blank routine. You can change everything afterwards.</p>
      <div class="grid">${options.map((t) => `<button class="card template" data-act="template" data-tpl="${t.id}" data-room="${esc(roomId || "")}">
        <h3>${esc(t.title)}</h3><div class="muted small">${esc(t.hint)}</div></button>`).join("")}</div>`;
  }

  _renderEditor() {
    const d = this._draft;
    if (!d) return "";
    const roomId = d.room || null;
    const presence = PRESENCE_TRIGGERS.includes(d.trigger);
    const valueTrigger = VALUE_TRIGGERS.includes(d.trigger);
    const stateTrigger = STATE_TRIGGERS.includes(d.trigger);
    const todoDone = TODO_DONE_TRIGGERS.includes(d.trigger);
    const timeBased = ["time", "sunrise", "sunset"].includes(d.trigger);
    const sensorsOn = d.trigger === "room_clear";
    const roomSensors = this._roomSensors(roomId);
    const sensorOptions = this._data.binary_sensors.map((s) => ({
      entity_id: s.entity_id, name: s.name, inRoom: (roomId ? this._area(roomId)?.entities || [] : []).some((e) => e.entity_id === s.entity_id),
    })).sort((a, b) => Number(b.inRoom) - Number(a.inRoom) || a.name.localeCompare(b.name));

    const triggerOptions = Object.entries(TRIGGER_LABELS).map(([value, label]) =>
      `<option value="${value}" ${d.trigger === value ? "selected" : ""}>${esc(label)}</option>`).join("");

    const days = WEEKDAYS.map((day) => `<label class="chip"><input type="checkbox" data-day="${day}" ${d.days.includes(day) ? "checked" : ""}> ${day}</label>`).join("");

    const pickerPeople = this._pickerPeople(d);
    const peopleList = pickerPeople.length
      ? pickerPeople.map((p) => personCheckbox(p, d.people.includes(p.entity_id))).join("")
      : `<p class="muted small">No people are turned on for routines. Turn them on under Settings.</p>`;

    const sensorList = sensorOptions.length
      ? sensorOptions.map((s) => checkbox("sensors", s.entity_id, s.name + (s.inRoom ? " (this room)" : ""), d.sensors.includes(s.entity_id))).join("")
      : `<p class="muted small">No binary sensors found.</p>`;

    const steps = d.actions.map((step, i) => this._stepEditor(step, i, roomId)).join("");

    const conditions = this._conditionRows(d);

    const preview = this._preview ? this._renderPreview(this._preview) : "";

    return `<form class="editor" onsubmit="return false">
      <section class="card">
        <h3>Basics</h3>
        <label>Name<input data-f="name" value="${esc(d.name)}" placeholder="e.g. Morning curtains"></label>
        <label>Linked device<select data-f="device_id">${this._deviceOptions(d.device_id, roomId)}</select></label>
        <p class="muted small">Each routine is a service under Daily Routine, linked to this device in Home Assistant.</p>
        <label class="check"><input type="checkbox" data-f="enabled" ${d.enabled ? "checked" : ""}> Enabled</label>
        <p class="muted small">${roomId ? `Room: ${esc(this._area(roomId)?.name || roomId)}` : "Whole house"}</p>
      </section>

      <section class="card">
        <h3>When</h3>
        <label>Trigger<select data-f="trigger" data-rerender>${triggerOptions}</select></label>
        ${valueTrigger ? this._valueFields(d, roomId) : ""}
        ${stateTrigger ? this._stateFields(d) : ""}
        ${todoDone ? this._todoDoneFields(d) : ""}
        ${d.trigger === "time" ? `<label>Time<input type="time" data-f="time" value="${esc(hhmm(d.time))}"></label>` : ""}
        ${d.trigger === "sunrise" || d.trigger === "sunset" ? `<label>Offset in minutes (negative is before)<input type="number" min="-720" max="720" data-f="offset_minutes" value="${esc(d.offset_minutes)}"></label>` : ""}
        ${timeBased || valueTrigger || stateTrigger || todoDone ? `<div class="chips">${days}</div>` : ""}
        ${presence ? this._zoneField(d) : ""}
        ${presence ? `<h4>Who</h4><p class="muted small">Pick the people this routine watches. People without location tracking can't be checked.</p><div class="checks">${peopleList}</div>
        <button class="ghost" data-act="tracked-people">Select everyone with tracking</button>` : ""}
        ${sensorsOn ? `<h4>Sensors</h4><p class="muted small">${d.trigger === "room_clear" ? "The routine runs once these have been quiet for the time below." : "Motion or occupancy sensors that must stay quiet."}</p>
          ${roomId && !roomSensors.length ? `<p class="muted small">This room has no motion sensors yet. Assign one in Home Assistant, or pick another.</p>` : ""}
          <div class="checks">${sensorList}</div>
          <label>Quiet for (minutes)<input type="number" min="0" max="1440" data-f="clear_minutes" value="${esc(d.clear_minutes)}"></label>` : ""}
        <h4>Only run between</h4>
        <p class="muted small">Optional. Outside these hours the routine does nothing. Leave both empty to run at any hour.</p>
        <div class="inline">
          <label>From<input type="time" data-f="active_from" value="${esc(d.active_from)}"></label>
          <label>To<input type="time" data-f="active_to" value="${esc(d.active_to)}"></label>
        </div>
        <h4>Extra conditions</h4>
        <p class="muted small">Only run when these hold. Add as many as you need: who is home or away, sensors that have been quiet or active, or a device's state.</p>
        ${d.conditions.length > 1 ? `<label>Run when<select data-f="conditions_match">
          <option value="all" ${d.conditions_match !== "any" ? "selected" : ""}>All of these are true</option>
          <option value="any" ${d.conditions_match === "any" ? "selected" : ""}>Any one of these is true</option></select></label>` : ""}
        ${conditions}
        <div class="row-actions">
          <button class="ghost" data-act="add-condition" data-kind="presence">+ Who is home or away</button>
          <button class="ghost" data-act="add-condition" data-kind="sensors">+ Sensors quiet or active</button>
          <button class="ghost" data-act="add-condition" data-kind="state">+ A device's state</button>
        </div>
      </section>

      <section class="card">
        <h3>Do</h3>
        ${steps}
        <div class="row-actions"><button class="ghost" data-act="add-step">+ Service step</button><button class="ghost" data-act="add-todo">+ To-do step</button><button class="ghost" data-act="add-delay">+ Wait</button></div>
      </section>

      <section class="card">
        <h3>Options</h3>
        <label class="check"><input type="checkbox" data-f="repeat" ${d.repeat ? "checked" : ""}> Run every time (not just once a day)</label>
        <label class="check"><input type="checkbox" data-f="skip_noop" ${d.skip_noop ? "checked" : ""}> Skip steps that are already done</label>
        ${this._catchUpField(d.catch_up_minutes)}
        <label>Retry a failed step (times)<input type="number" min="0" max="3" data-f="retries" value="${esc(d.retries)}"></label>
        <h4>Notify me</h4>
        <label class="check"><input type="checkbox" data-f="notify_on_run" ${d.notify_on_run ? "checked" : ""}> When it runs</label>
        <label class="check"><input type="checkbox" data-f="notify_on_skip" ${d.notify_on_skip ? "checked" : ""}> When it is skipped</label>
        <label class="check"><input type="checkbox" data-f="notify_on_error" ${d.notify_on_error ? "checked" : ""}> When it fails</label>
        <label class="check"><input type="checkbox" data-f="notify_on_health" ${d.notify_on_health ? "checked" : ""}> When it needs attention (a device or list is missing or unavailable)</label>
      </section>

      ${preview}
      ${this._notice ? `<div class="notice ${this._error ? "error" : ""}">${esc(this._notice)}</div>` : ""}
      <div class="footer">
        ${d.isNew ? "" : `<button class="ghost danger" data-act="delete">Delete</button>`}
        <button class="ghost" data-act="cancel">Cancel</button>
        <button class="ghost" data-act="test">Test (dry run)</button>
        <button class="primary" data-act="save">Save</button>
      </div>
    </form>`;
  }

  _conditionRows(d) {
    return d.conditions.map((cond, i) => {
      const remove = `<button class="ghost" data-act="remove-condition" data-i="${i}">Remove</button>`;
      if (cond.kind === "presence") return this._presenceRow(d, cond, i, remove);
      if (cond.kind === "sensors") return this._sensorRow(cond, i, remove);
      return this._stateRow(cond, i, remove);
    }).join("");
  }

  _presenceRow(d, cond, i, remove) {
    const zones = this._data?.zones || [{ entity_id: "zone.home", name: "Home" }];
    const zoneOptions = zones.map((z) => `<option value="${esc(z.entity_id)}" ${z.entity_id === cond.zone ? "selected" : ""}>${esc(z.name)}</option>`).join("");
    const who = `<select data-ci="${i}" data-cf="who">
      <option value="everyone" ${cond.who !== "anyone" ? "selected" : ""}>Everyone</option>
      <option value="anyone" ${cond.who === "anyone" ? "selected" : ""}>Anyone</option></select>`;
    const where = `<select data-ci="${i}" data-cf="where">
      <option value="away" ${cond.where !== "in" ? "selected" : ""}>is away from</option>
      <option value="in" ${cond.where === "in" ? "selected" : ""}>is at</option></select>`;
    const people = this._pickerPeople(d).map((p) => {
      const checked = cond.people.includes(p.entity_id);
      const disabled = !p.tracked && !checked ? "disabled" : "";
      return `<label class="check ${disabled ? "muted" : ""}"><input type="checkbox" data-ci="${i}" data-cpeople="${esc(p.entity_id)}" ${checked ? "checked" : ""} ${disabled}> ${esc(p.name)}${p.tracked ? "" : " (no location tracking)"}</label>`;
    }).join("");
    return `<div class="condition">
      <div class="step-head"><strong>Who is home or away</strong>${remove}</div>
      <div class="inline2">${who} ${where}<select data-ci="${i}" data-cf="zone">${zoneOptions}</select></div>
      <div class="checks">${people || `<p class="muted small">No people are turned on for routines. Turn them on under Settings.</p>`}</div>
    </div>`;
  }

  _sensorRow(cond, i, remove) {
    const sensors = this._data.binary_sensors.map((s) => {
      const checked = cond.sensors.includes(s.entity_id);
      return `<label class="check"><input type="checkbox" data-ci="${i}" data-csensor="${esc(s.entity_id)}" ${checked ? "checked" : ""}> ${esc(s.name)}</label>`;
    }).join("");
    return `<div class="condition">
      <div class="step-head"><strong>Sensors</strong>${remove}</div>
      <div class="inline2">
        <select data-ci="${i}" data-cf="mode">
          <option value="quiet" ${cond.mode !== "active" ? "selected" : ""}>Quiet for</option>
          <option value="active" ${cond.mode === "active" ? "selected" : ""}>Active within the last</option></select>
        <input type="number" min="0" max="1440" data-ci="${i}" data-cf="minutes" value="${esc(cond.minutes)}" aria-label="minutes">
      </div>
      <p class="muted small">${cond.mode === "active" ? "True when any of these has seen motion within those minutes." : "True when none of these has seen motion for those minutes."}</p>
      <div class="checks">${sensors || `<p class="muted small">No binary sensors found.</p>`}</div>
    </div>`;
  }

  _stateRow(row, i, remove) {
    const domain = (row.entity_id || "").split(".")[0];
    const kind = domain === "lock" || domain === "cover" || domain === "person" || domain === "device_tracker" || domain === "media_player" || domain === "climate"
      ? domain
      : (["light", "switch", "fan", "input_boolean", "binary_sensor", "automation", "siren"].includes(domain) ? "on_off" : null);
    const states = kind ? CONDITION_STATES[kind] : null;
    const known = states ? states.includes(row.state) : false;
    const custom = row.custom || (!known && row.state !== "" && row.state !== undefined);
    const stateOptions = states
      ? [
          `<option value="" ${!row.state && !custom ? "selected" : ""}>Choose a state…</option>`,
          ...states.map((st) => `<option value="${esc(st)}" ${st === row.state && !custom ? "selected" : ""}>${esc(STATE_NAMES[st] || st)}</option>`),
          `<option value="${CUSTOM_ACTION}" ${custom ? "selected" : ""}>Other…</option>`,
        ].join("")
      : null;
    const stateField = stateOptions
      ? `<select data-ci="${i}" data-cf="stateChoice" data-rerender>${stateOptions}</select>${custom ? `<input data-ci="${i}" data-cf="state" value="${esc(row.state)}" placeholder="state">` : ""}`
      : `<input data-ci="${i}" data-cf="state" value="${esc(row.state)}" placeholder="state, e.g. on">`;
    return `<div class="condition">
      <div class="step-head"><strong>A device's state</strong>${remove}</div>
      <div class="inline">
      <select data-ci="${i}" data-cf="entity_id" data-rerender>${this._entityOptions(row.entity_id)}</select>
      ${stateField}</div>
    </div>`;
  }

  _stepEditor(step, i, roomId) {
    const last = this._draft.actions.length - 1;
    const head = `<div class="step-head"><strong>Step ${i + 1}</strong>
      <div class="row-actions">
        <button class="ghost" data-act="move-step" data-i="${i}" data-dir="-1" ${i === 0 ? "disabled" : ""}>↑</button>
        <button class="ghost" data-act="move-step" data-i="${i}" data-dir="1" ${i === last ? "disabled" : ""}>↓</button>
        <button class="ghost" data-act="remove-step" data-i="${i}">Remove</button></div></div>`;
    const kind = `<label>Type<select data-si="${i}" data-sf="kind" data-rerender>
      <option value="service" ${step.kind === "service" ? "selected" : ""}>Action</option>
      <option value="todo" ${step.kind === "todo" ? "selected" : ""}>To-do list</option>
      <option value="delay" ${step.kind === "delay" ? "selected" : ""}>Wait (seconds)</option></select></label>`;
    if (step.kind === "todo") {
      const ops = Object.entries(TODO_VERBS).map(([value, label]) =>
        `<option value="${value}" ${(step.todoOp || "add") === value ? "selected" : ""}>${esc(label)} an item</option>`).join("");
      return `<div class="step">${head}${kind}
        <label>To-do action<select data-si="${i}" data-sf="todoOp" data-rerender>${ops}</select></label>
        <label>To-do list<select data-si="${i}" data-sf="todoList">${this._pickerOptions(this._data.todo_lists || [], step.todoList || "", "Choose a to-do list…")}</select></label>
        <label>Item text<input data-si="${i}" data-sf="todoItem" value="${esc(step.todoItem || "")}" placeholder="for example Empty dishwasher"></label>
        ${(step.todoOp || "add") === "add" ? "" : `<label>Only when due<select data-si="${i}" data-sf="todoDue" data-rerender>${dueOptions(step.todoDue || "any")}</select></label>
        ${(step.todoDue || "any") === "any" ? "" : `<label class="check"><input type="checkbox" data-si="${i}" data-sf="todoOverdue" ${step.todoOverdue !== false ? "checked" : ""}> Also when it is overdue</label>
        <label class="check"><input type="checkbox" data-si="${i}" data-sf="todoUndated" ${step.todoUndated ? "checked" : ""}> Also when it has no due date</label>`}`}
      </div>`;
    }
    if (step.kind === "delay") {
      return `<div class="step">${head}${kind}<label>Seconds<input type="number" min="0" step="1" data-si="${i}" data-sf="seconds" value="${esc(step.seconds)}"></label></div>`;
    }
    const domain = step.service.includes(".") ? step.service.split(".")[0] : "";
    const candidates = this._roomEntities(roomId).filter((e) => !domain || e.domain === domain);
    const targetModes = [
      roomId ? `<option value="area" ${step.targetMode === "area" ? "selected" : ""}>This room</option>` : "",
      `<option value="all_areas" ${step.targetMode === "all_areas" ? "selected" : ""}>Every room</option>`,
      `<option value="entities" ${step.targetMode === "entities" ? "selected" : ""}>Specific devices</option>`,
      `<option value="none" ${step.targetMode === "none" ? "selected" : ""}>No target</option>`,
    ].join("");
    const entityChecks = step.targetMode === "entities"
      ? `<div class="checks">${candidates.map((e) => checkbox(`sentity`, e.entity_id, e.name, step.entities.includes(e.entity_id), i)).join("") || `<p class="muted small">No matching devices here.</p>`}</div>`
      : "";
    const known = ACTIONS.some((a) => a.service === step.service);
    const custom = !known && (step.custom || step.service !== "");
    const actionOptions = [
      `<option value="" ${!step.service && !custom ? "selected" : ""}>Choose an action…</option>`,
      ...ACTIONS.map((a) => `<option value="${esc(a.service)}" ${a.service === step.service ? "selected" : ""}>${esc(a.label)}</option>`),
      `<option value="${CUSTOM_ACTION}" ${custom ? "selected" : ""}>Other (advanced)…</option>`,
    ].join("");
    const customField = custom
      ? `<label>Service name<input data-si="${i}" data-sf="service" value="${esc(step.service)}" placeholder="domain.service"></label>`
      : "";
    return `<div class="step">${head}${kind}
      <label>Action<select data-si="${i}" data-sf="actionChoice" data-rerender>${actionOptions}</select></label>
      ${customField}
      <label>Applies to<select data-si="${i}" data-sf="targetMode" data-rerender>${targetModes}</select></label>
      ${entityChecks}
      <label>Advanced: extra settings (optional JSON)<textarea data-si="${i}" data-sf="data" rows="2" placeholder='{"brightness": 128}'>${esc(step.data)}</textarea></label>
    </div>`;
  }

  _entityOptions(selected) {
    const all = this._allEntities();
    const options = all.map((e) => `<option value="${esc(e.entity_id)}" ${e.entity_id === selected ? "selected" : ""}>${esc(e.name)}</option>`);
    return `<option value="">Choose…</option>${options.join("")}`;
  }

  _renderPreview(preview) {
    const outcomes = {
      would_run: "would run",
      already_in_state: "already done – skipped",
      partly_in_state: "runs for the devices not yet done",
    };
    const steps = preview.steps.map((s) => {
      if (s.type === "delay") return `<li>Wait ${esc(s.seconds)} s</li>`;
      if (s.type === "todo") return `<li>${esc(TODO_VERBS[s.op] || s.op)} “${esc(s.item)}” on ${esc(this._nameFor(s.list))} → ${esc(TODO_OUTCOME_LABELS[s.outcome] || s.outcome)}</li>`;
      return `<li>${esc(actionLabel(s.service))} → ${esc(outcomes[s.outcome] || s.outcome)}${s.entities?.length ? `<br><span class="muted small">${s.entities.map((e) => esc(this._nameFor(e))).join(", ")}</span>` : ""}</li>`;
    }).join("");
    const verdict = preview.conditions_ok
      ? `<span class="pill">Conditions pass right now</span>`
      : `<span class="pill warn">Would wait: ${esc(REASON_LABELS[preview.block_reason] || preview.block_reason)}</span>`;
    return `<section class="card preview"><h3>Dry run</h3><p>${verdict}${preview.paused ? ` <span class="pill warn">Routines are paused</span>` : ""}</p><ol>${steps}</ol><p class="muted small">Nothing was run.</p></section>`;
  }
}

function personCheckbox(person, checked) {
  if (!person.tracked && !checked) {
    return `<label class="check muted"><input type="checkbox" disabled> ${esc(person.name)} (no location tracking)</label>`;
  }
  const note = person.tracked ? "" : " (no location tracking)";
  return checkbox("people", person.entity_id, person.name + note, checked);
}

function checkbox(list, value, label, checked, stepIndex) {
  const attrs = stepIndex !== undefined
    ? `data-sentity="${stepIndex}" data-val="${esc(value)}"`
    : `data-list="${list}" data-val="${esc(value)}"`;
  return `<label class="check"><input type="checkbox" ${attrs} ${checked ? "checked" : ""}> ${esc(label)}</label>`;
}

const STYLES = `<style>
  :host { display: block; }
  main { font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); color: var(--primary-text-color, #212121);
         background: var(--primary-background-color, #fafafa); min-height: 100%; padding: 16px; box-sizing: border-box; }
  header { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
  .title { display: flex; align-items: center; gap: 8px; }
  h1 { font-size: 22px; font-weight: 500; margin: 0; }
  h3 { margin: 0 0 6px; font-size: 16px; font-weight: 500; }
  h4 { margin: 14px 0 4px; font-size: 14px; font-weight: 500; }
  button { font: inherit; border-radius: 8px; padding: 8px 12px; cursor: pointer; border: 1px solid var(--divider-color, #e0e0e0);
           background: var(--card-background-color, #fff); color: var(--primary-text-color, #212121); }
  button.primary { background: var(--primary-color, #03a9f4); color: #fff; border-color: transparent; }
  button.warn { background: var(--warning-color, #ffa600); color: #000; border-color: transparent; }
  button.danger { color: var(--error-color, #b3261e); }
  .toolbar input[type="search"] { width: 100%; max-width: 32rem; }
  header nav { display: flex; gap: 4px; flex-wrap: wrap; }
  header nav button { border-radius: 999px; }
  header nav button[aria-selected="true"] { font-weight: 600; border-color: currentColor; }
  button.ghost { background: transparent; }
  button:disabled { opacity: .4; cursor: default; }
  input, select, textarea { font: inherit; width: 100%; box-sizing: border-box; padding: 8px; border-radius: 8px;
           border: 1px solid var(--divider-color, #cfcfcf); background: var(--card-background-color, #fff); color: var(--primary-text-color, #212121); }
  input[type=checkbox] { width: auto; }
  label { display: block; margin: 8px 0; font-size: 14px; }
  label.check { display: flex; align-items: center; gap: 8px; }
  .card { background: var(--card-background-color, #fff); border-radius: 12px; padding: 14px; box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.12));
          border: 0; text-align: left; color: inherit; display: block; width: 100%; box-sizing: border-box; margin-bottom: 12px; }
  button.card { cursor: pointer; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 12px; }
  .room ha-icon { --mdc-icon-size: 28px; color: var(--primary-color, #03a9f4); }
  .room-name { font-size: 17px; margin: 6px 0 2px; }
  .room-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; font-size: 13px; }
  .list { display: grid; gap: 4px; }
  .routine-head { display: flex; justify-content: space-between; gap: 8px; align-items: flex-start; }
  .routine.disabled { opacity: .6; }
  .row-actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
  .toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
  .pill { display: inline-block; font-size: 12px; padding: 3px 8px; border-radius: 999px; background: var(--secondary-background-color, #eee); white-space: nowrap; }
  .pill.warn { background: var(--warning-color, #ffa600); color: #000; }
  .pill.error { background: var(--error-color, #db4437); color: #fff; }
  .muted { color: var(--secondary-text-color, #727272); }
  .small { font-size: 13px; }
  .notice { padding: 10px 12px; border-radius: 8px; margin: 8px 0; background: var(--secondary-background-color, #eee); }
  .notice.error { background: var(--error-color, #db4437); color: #fff; }
  .notice.warn { background: var(--warning-color, #ffa600); color: #000; }
  .empty { text-align: center; padding: 24px; color: var(--secondary-text-color, #727272); }
  .editor { display: grid; gap: 4px; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
  .chip { display: inline-flex; align-items: center; gap: 4px; background: var(--secondary-background-color, #eee); border-radius: 999px; padding: 4px 10px; margin: 0; }
  .checks { display: grid; gap: 2px; max-height: 220px; overflow: auto; padding: 4px 0; }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; margin: 12px 0; }
  .tile { display: grid; gap: 4px; padding: 12px; border-radius: 12px; background: var(--card-background-color, #fff); box-shadow: 0 1px 3px rgba(0,0,0,.1); }
  .tile strong { font-size: 22px; }
  .bars { display: flex; align-items: flex-end; gap: 6px; height: 140px; padding-top: 8px; overflow-x: auto; }
  .bar-col { flex: 1 0 28px; display: grid; grid-template-rows: 1fr auto; justify-items: center; height: 100%; min-width: 28px; }
  .bar { width: 100%; align-self: end; display: flex; flex-direction: column-reverse; border-radius: 6px 6px 0 0; overflow: hidden; min-height: 2px; }
  .seg { display: block; }
  .seg.ok { background: var(--success-color, #43a047); }
  .seg.fail { background: var(--error-color, #db4437); }
  .seg.skip { background: var(--warning-color, #ffa600); }
  .key { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin: 0 4px 0 8px; }
  .key.ok { background: var(--success-color, #43a047); }
  .key.fail { background: var(--error-color, #db4437); }
  .key.skip { background: var(--warning-color, #ffa600); }
  ul.plain { list-style: none; padding: 0; margin: 8px 0; display: grid; gap: 8px; }
  ul.reasons li, ul.events li { display: flex; justify-content: space-between; gap: 12px; }
  ul.events li { display: block; padding-bottom: 8px; border-bottom: 1px solid var(--divider-color, #eee); }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--divider-color, #eee); vertical-align: top; }
  .timing { display: grid; gap: 12px; }
  .timing label { display: grid; gap: 4px; }
  .inline { display: grid; grid-template-columns: 1fr 1fr auto; gap: 6px; align-items: center; }
  .inline2 { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .inline2 select, .inline2 input { width: auto; flex: 1 1 auto; }
  .condition { border-top: 1px solid var(--divider-color, #e0e0e0); padding-top: 8px; margin-top: 8px; }
  .step { border-top: 1px solid var(--divider-color, #e0e0e0); padding-top: 8px; margin-top: 8px; }
  .step-head { display: flex; justify-content: space-between; align-items: center; }
  .preview ol { padding-left: 20px; }
  .footer { display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; margin: 12px 0 24px; }
  .template { cursor: pointer; }
  .history { margin-top: 6px; }
  .history summary { cursor: pointer; }
  .history ul { margin: 4px 0 0; padding-left: 18px; }
  @media (max-width: 600px) { main { padding: 12px; } }
  :host(.busy)::before { content: ""; position: fixed; top: 0; left: 0; right: 0; height: 3px; z-index: 10;
    background: linear-gradient(90deg, transparent, var(--primary-color, #03a9f4), transparent);
    background-size: 200% 100%; animation: dr-progress 1.1s linear infinite; }
  @keyframes dr-progress { from { background-position: 200% 0; } to { background-position: -200% 0; } }
  .skeleton-grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); }
  .skeleton-card { height: 96px; border-radius: 12px;
    background: linear-gradient(90deg, rgba(127,127,127,.12) 25%, rgba(127,127,127,.24) 37%, rgba(127,127,127,.12) 63%);
    background-size: 400% 100%; animation: dr-shimmer 1.4s ease infinite; }
  @keyframes dr-shimmer { from { background-position: 100% 0; } to { background-position: 0 0; } }
  @media (prefers-reduced-motion: reduce) {
    :host(.busy)::before, .skeleton-card { animation: none; }
  }
</style>`;

customElements.define("daily-routine-panel", DailyRoutinePanel);
