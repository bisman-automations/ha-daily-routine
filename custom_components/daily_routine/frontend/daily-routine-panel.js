/* Daily Routine sidebar panel.
 * Rooms come from Home Assistant areas. Pick a room, then add or edit its routines.
 * All data goes over websocket commands registered in panel.py.
 */
const DOMAIN = "daily_routine";
const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const SENSOR_CLASSES = ["motion", "occupancy", "presence", "moving"];
const REFRESH_MS = 15000;
// Pages that show routine cards, and so redraw when a run starts or finishes.
const LIST_VIEWS = ["rooms", "room", "all", "todos", "today"];

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
  mode_changes: "When the house mode changes",
  alarm: "Before (or after) my phone's alarm",
  calendar_event: "Before (or after) a calendar event",
  forecast: "When the forecast shows rain, snow or a freeze",
  cheapest: "At the cheapest electricity hour",
  tag: "When an NFC tag is scanned",
  button: "When a button is pressed",
  manual: "Only when I press Run",
};
// The simple editor's optional sections, added with a chip.
const SIMPLE_SECTIONS = [
  ["more", "Or also when"], ["conditions", "Only if"], ["dates", "Only some dates or hours"],
  ["ask", "Ask me first"], ["notify", "Notify me"], ["skip", "Skip or pause"], ["options", "More options"],
];
// House modes, in the order they're offered.
const MODES = [["home", "Home"], ["away", "Away"], ["night", "Night"], ["guests", "Guests"], ["vacation", "Vacation"]];
const modeName = (mode) => (MODES.find(([id]) => id === mode) || [mode, mode])[1];
// Triggers a routine can have as well as its own ("Or also when").
const MORE_TRIGGER_KINDS = ["time", "sunrise", "sunset", "everyone_leaves", "someone_arrives", "person_leaves", "person_arrives", "value_above", "value_below", "state_is", "mode_changes", "alarm", "calendar_event", "tag", "button"];
const MAX_MORE_TRIGGERS = 4;
const WEATHER_NAMES = {
  "clear-night": "Clear (night)", cloudy: "Cloudy", exceptional: "Exceptional", fog: "Fog", hail: "Hail", lightning: "Lightning",
  "lightning-rainy": "Thunderstorm", partlycloudy: "Partly cloudy", pouring: "Pouring", rainy: "Rainy", snowy: "Snowy",
  "snowy-rainy": "Sleet", sunny: "Sunny", windy: "Windy", "windy-variant": "Windy and cloudy",
};
// What a step's "only if" can check.
const ONLY_IF_KINDS = [["", "Always"], ["state", "A device's state"], ["mode", "The house mode"], ["workday", "Workday or day off"], ["day_type", "The kind of day"], ["weather", "The weather"], ["forecast", "The forecast"]];
const NORMAL_DAY = "Normal day";
const ROOM_FILTERS = [["", "Every room picked"], ["occupied", "Only rooms someone is in"], ["empty", "Only empty rooms"]];

// A device's name with the integration it comes from: the same light can be
// in Hue, Matter and HomeKit, and the lists show which is which.
function withIntegration(name, integration) {
  if (!integration || String(name).endsWith(`(${integration})`)) return name;
  return `${name} (${integration})`;
}
// "Together" steps become steps marked "at the same time as the step before".
function flatSteps(actions) {
  return (actions || []).flatMap((a) => (a.together
    ? a.together.map((sub, j) => (j ? { ...sub, __withPrev: true } : { ...sub }))
    : [a]));
}

// Steps marked "at the same time as the step before" are grouped into one
// "together" step, as the server stores them.
function groupSteps(raws, steps) {
  const out = [];
  const can = (raw) => ["service", "todo", "run_routine", "set_mode"].some((k) => k in raw);
  raws.forEach((raw, i) => {
    if (!steps[i].withPrev || !out.length) {
      out.push(raw);
      return;
    }
    const prev = out[out.length - 1];
    if (!can(raw) || !(prev.together || can(prev))) {
      throw new Error(`Step ${i + 1}: only actions, to-dos, routines and house modes can run at the same time as the step before.`);
    }
    if (prev.together) prev.together.push(raw);
    else out[out.length - 1] = { together: [prev, raw] };
  });
  return out;
}

function cloneCond(c) {
  return { ...c, people: [...(c.people || [])], sensors: [...(c.sensors || [])], modes: [...(c.modes || [])], conditions: [...(c.conditions || [])], types: [...(c.types || [])], entities: [...(c.entities || [])] };
}
// A condition from the editor as the server stores it (the same for a routine's
// conditions and a step's "only if").
function condToRaw(c) {
  if (c.kind === "presence") return { kind: "presence", who: c.who, where: c.where, people: [...c.people], zone: c.zone || "zone.home" };
  if (c.kind === "sensors") return { kind: "sensors", mode: c.mode, minutes: Number(c.minutes) || 0, sensors: [...c.sensors] };
  if (c.kind === "calendar") return { kind: "calendar", entity_id: c.entity_id, when: c.when === "event" ? "event" : "no_event", match: (c.match || "").trim() };
  if (c.kind === "mode") return { kind: "mode", modes: [...(c.modes || [])] };
  if (c.kind === "charging") return { kind: "charging", entities: [...(c.entities || [])] };
  if (c.kind === "value") return { kind: "value", entity_id: c.entity_id, below: c.below === "" || c.below == null ? null : Number(c.below), above: c.above === "" || c.above == null ? null : Number(c.above) };
  if (c.kind === "workday") return { kind: "workday", entity_id: c.entity_id, when: c.when === "day_off" ? "day_off" : "workday" };
  if (c.kind === "day_type") return c.person ? { kind: "day_type", types: [...(c.types || [])], person: c.person } : { kind: "day_type", types: [...(c.types || [])] };
  if (c.kind === "forecast") return { kind: "forecast", entity_id: c.entity_id, conditions: [...(c.conditions || [])], below: c.below === "" || c.below == null ? null : Number(c.below), hours: Math.max(1, Math.min(48, Math.round(Number(c.hours) || 6))), when: c.when === "coming" ? "coming" : "not_coming" };
  if (c.kind === "weather") {
    const test = ["is", "is_not", "above", "below"].includes(c.test) ? c.test : "is";
    return test === "is" || test === "is_not"
      ? { kind: "weather", entity_id: c.entity_id, test, conditions: [...(c.conditions || [])] }
      : { kind: "weather", entity_id: c.entity_id, test, value: c.value === "" || c.value == null ? null : Number(c.value) };
  }
  return { kind: "state", entity_id: c.entity_id, state: c.state };
}
const PRESENCE_TRIGGERS = ["everyone_leaves", "someone_arrives", "person_leaves", "person_arrives"];
// Triggers that react to something happening. A run they start doesn't wait the
// rest of the day for its conditions by default: the moment has passed.
const EVENT_TRIGGERS = [...PRESENCE_TRIGGERS, "value_above", "value_below", "state_is", "todo_done", "mode_changes", "forecast", "tag", "button"];
const defaultWait = (trigger) => (EVENT_TRIGGERS.includes(trigger) ? "no" : "day");
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
  conditions_not_met: "Conditions were not met in time",
  trigger_ended: "What started it was over before the conditions were met",
  cooldown: "It ran a moment ago",
  disabled: "Routine was turned off",
  already_ran_today: "Already ran today",
};

// Plain words for the codes the coordinator records when a run is skipped or fails.
function activityReason(code) {
  if (!code) return "";
  if (SKIP_REASON_LABELS[code]) return SKIP_REASON_LABELS[code];
  if (REASON_LABELS[code]) return REASON_LABELS[code].charAt(0).toUpperCase() + REASON_LABELS[code].slice(1);
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
const STATE_LABELS = { idle: "Ready", waiting: "Waiting", held: "Waiting for list", running: "Running", asking: "Asking", skipped: "Skipped", error: "Error" };
const REASON_LABELS = {
  someone_home: "someone is still in that place",
  sensors_active: "motion right now",
  clear_timer_running: "waiting for the room to be quiet",
  presence_not_met: "people are not where this routine needs them",
  sensors_not_met: "sensors are not in the state this routine needs",
  clear_timer_not_started: "waiting for sensors",
  extra_condition_not_met: "an extra condition is not met",
  conditions_not_met: "conditions were not met",
  trigger_ended: "what started it was over",
  cooldown: "it ran a moment ago",
  outside_hours: "outside its hours",
  calendar_not_met: "the calendar doesn't allow it today",
  paused_until: "paused for a while",
  skipped_once: "skipped once, as asked",
  asked_skip: "you answered Skip",
  no_answer_skip: "nobody answered, so it was skipped",
  wrong_mode: "the house isn't in the right mode",
  workday_not_met: "it isn't the right kind of day (workday or day off)",
  weather_not_met: "the weather doesn't match",
  day_type_not_met: "it isn't the right kind of day",
  forecast_not_met: "the forecast doesn't allow it",
  calendar_hold: "a calendar hold is on",
  skip_mode: "the house is in a mode it doesn't run in",
  skip_date: "today is one of the days it's set to skip",
  charging_not_met: "not every phone is charging",
  value_not_met: "a sensor isn't at the right level",
  disabled: "it's turned off",
  paused: "all routines are paused",
  skip_next: "its next run is set to be skipped",
  not_today: "today isn't one of its days (or outside its dates)",
  already_ran_today: "it already ran today",
};
// What a step can do, in plain words. The service id is only used under the hood.
const ACTIONS = [
  { service: "light.turn_on", label: "Turn on lights", fields: [
    { key: "brightness_pct", label: "Brightness", type: "pct", optional: true },
    { key: "color_temp_kelvin", label: "White (warm to cool)", type: "kelvin", optional: true },
    { key: "rgb_color", label: "Color", type: "color", optional: true },
    { key: "transition", label: "Fade in over (minutes)", type: "minutes", optional: true },
  ] },
  { service: "light.turn_off", label: "Turn off lights", fields: [
    { key: "transition", label: "Fade out over (minutes)", type: "minutes", optional: true },
  ] },
  { service: "switch.turn_on", label: "Turn on switches" },
  { service: "switch.turn_off", label: "Turn off switches" },
  { service: "fan.turn_on", label: "Turn on fans", fields: [{ key: "percentage", label: "Speed", type: "pct", optional: true }] },
  { service: "fan.turn_off", label: "Turn off fans" },
  { service: "cover.open_cover", label: "Open curtains and blinds" },
  { service: "cover.close_cover", label: "Close curtains and blinds" },
  { service: "cover.stop_cover", label: "Stop curtains and blinds" },
  { service: "cover.set_cover_position", label: "Move curtains and blinds to", fields: [{ key: "position", label: "Open by", type: "pct" }] },
  { service: "lock.lock", label: "Lock doors" },
  { service: "lock.unlock", label: "Unlock doors" },
  { service: "scene.turn_on", label: "Activate a scene" },
  { service: "script.turn_on", label: "Run a script" },
  { service: "media_player.media_pause", label: "Pause media" },
  { service: "media_player.media_play", label: "Play media" },
  { service: "media_player.volume_set", label: "Set speaker volume", fields: [{ key: "volume_level", label: "Volume", type: "percent" }] },
  { service: "tts.speak", label: "Say something on a speaker", voice: true, fields: [
    { key: "message", label: "What to say", type: "text", placeholder: "for example Time to get ready for school" },
    { key: "media_player_entity_id", label: "Speaker", type: "entity", domain: "media_player" },
  ] },
  { service: "climate.set_temperature", label: "Set the thermostat", fields: [
    { key: "temperature", label: "Temperature", type: "number", step: 0.5 },
    { key: "hvac_mode", label: "Mode", type: "select", optional: true, options: [["heat", "Heat"], ["cool", "Cool"], ["heat_cool", "Heat and cool"], ["auto", "Auto"], ["off", "Off"]] },
  ] },
  { service: "climate.turn_off", label: "Turn off the thermostat" },
  { service: "vacuum.start", label: "Start the vacuum" },
  { service: "vacuum.return_to_base", label: "Send the vacuum home" },
  { service: "notify.persistent_notification", label: "Show a Home Assistant notification", untargeted: true, fields: [
    { key: "message", label: "Message", type: "text", placeholder: "for example Rain is coming" },
  ] },
  { service: "daily_routine.remember_states", label: "Remember which lights and switches are on", domains: ["light", "switch", "fan"], fields: [
    { key: "name", label: "Remember as", type: "text", placeholder: "for example Before we left" },
  ] },
  { service: "daily_routine.put_back_states", label: "Put back what was on", untargeted: true, fields: [
    { key: "name", label: "Remembered as", type: "text", placeholder: "the same name as the Remember step" },
  ] },
];
// Home Assistant's own descriptions of every action's settings (hass.services),
// so an action not in ACTIONS still gets proper fields instead of JSON.
let SERVICE_DESCRIPTIONS = {};
const actionSpec = (service) => ACTIONS.find((a) => a.service === service) || describedSpec(service);

function describedSpec(service) {
  const [domain, name] = String(service || "").split(".");
  const described = SERVICE_DESCRIPTIONS?.[domain]?.[name];
  if (!described) return undefined;
  const fields = [];
  const add = (key, f) => {
    const field = fieldFromSelector(key, f);
    if (field) fields.push(field);
  };
  for (const [key, f] of Object.entries(described.fields || {})) {
    if (f?.fields) for (const [sub, g] of Object.entries(f.fields)) add(sub, g);
    else add(key, f);
  }
  return { service, label: described.name || service, fields, described: true };
}

// One setting of an action, from its selector in Home Assistant's description.
function fieldFromSelector(key, f) {
  if (key === "entity_id") return null;
  const sel = f?.selector || {};
  const kind = Object.keys(sel)[0] || "text";
  const opts = sel[kind] || {};
  const label = f.name || humanize(key);
  const optional = !f.required;
  const base = { key, label, optional, help: f.description || "" };
  if (kind === "number") return { ...base, type: "number", step: opts.step === "any" ? "any" : opts.step || 1, min: opts.min, max: opts.max, unit: opts.unit_of_measurement || "" };
  if (kind === "boolean") return { ...base, type: "bool" };
  if (kind === "select") {
    const options = (opts.options || []).map((o) => (typeof o === "object" ? [o.value, o.label || o.value] : [o, humanize(String(o))]));
    return { ...base, type: "select", options, multiple: Boolean(opts.multiple) };
  }
  if (kind === "color_rgb") return { ...base, type: "color" };
  if (kind === "color_temp") return { ...base, type: "kelvin" };
  if (kind === "time") return { ...base, type: "time" };
  if (kind === "entity") return { ...base, type: "entity", domain: [opts.domain || (opts.filter?.[0]?.domain)].flat().filter(Boolean)[0] || "" };
  if (kind === "object") return { ...base, type: "object" };
  return { ...base, type: "text", placeholder: f.example !== undefined ? String(f.example) : "" };
}

function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex || ""));
  return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : null;
}
function rgbToHex(rgb) {
  return Array.isArray(rgb) && rgb.length === 3 ? `#${rgb.map((n) => Number(n).toString(16).padStart(2, "0")).join("")}` : "";
}

// Fields an action asks for, turned into its data.
function fieldsToData(spec, values, index) {
  const data = {};
  for (const f of spec?.fields || []) {
    const raw = values?.[f.key];
    if (raw === undefined || raw === null || String(raw).trim() === "") {
      if (f.optional) continue;
      throw new Error(`Step ${index + 1}: fill in ${f.label.toLowerCase()}.`);
    }
    if (f.type === "bool") {
      if (raw === true || raw === "true") data[f.key] = true;
      else if (raw === false || raw === "false") data[f.key] = false;
      continue;
    }
    if (f.type === "color") {
      const rgb = hexToRgb(raw);
      if (rgb) data[f.key] = rgb;
      continue;
    }
    if (f.type === "object") {
      try {
        data[f.key] = typeof raw === "string" ? JSON.parse(raw) : raw;
      } catch (_err) {
        throw new Error(`Step ${index + 1}: ${f.label.toLowerCase()} isn't valid.`);
      }
      continue;
    }
    if (f.type === "select" && f.multiple) {
      const list = Array.isArray(raw) ? raw : String(raw).split(",").map((x) => x.trim()).filter(Boolean);
      if (list.length) data[f.key] = list;
      continue;
    }
    if (["percent", "pct", "number", "minutes", "kelvin"].includes(f.type)) {
      const n = Number(raw);
      if (Number.isNaN(n)) throw new Error(`Step ${index + 1}: ${f.label.toLowerCase()} must be a number.`);
      if (f.type === "percent") data[f.key] = Math.max(0, Math.min(100, n)) / 100;
      else if (f.type === "pct") data[f.key] = Math.max(0, Math.min(100, n));
      else if (f.type === "minutes") {
        if (n > 0) data[f.key] = Math.round(Math.min(n, 120) * 60);
      } else data[f.key] = n;
    } else {
      data[f.key] = String(raw).trim();
    }
  }
  return data;
}

function dataToFields(spec, data) {
  const fields = {};
  const rest = { ...(data || {}) };
  for (const f of spec?.fields || []) {
    if (!(f.key in rest)) continue;
    const value = rest[f.key];
    if (f.type === "color") fields[f.key] = rgbToHex(value);
    else if (f.type === "object") fields[f.key] = JSON.stringify(value);
    else fields[f.key] = f.type === "percent" ? Math.round(Number(value) * 100) : f.type === "minutes" ? Math.round((Number(value) / 60) * 100) / 100 : value;
    delete rest[f.key];
  }
  return { fields, rest };
}

function formatDay(day) {
  if (!day) return "";
  const date = new Date(`${day}T12:00:00`);
  return isNaN(date) ? day : date.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

function todayIso() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}
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
  {
    id: "away_mode", title: "Away mode when everyone leaves", scope: "house",
    hint: "Whole house: switch the house to Away once everyone has left. Lived-in lights (in Settings) follow it.",
    draft: { trigger: "everyone_leaves", usePeopleList: true, actions: [{ kind: "mode", mode: "away" }] },
  },
  {
    id: "home_mode", title: "Home mode when someone arrives", scope: "house",
    hint: "Whole house: switch the house back to Home when someone gets back.",
    draft: { trigger: "someone_arrives", usePeopleList: true, actions: [{ kind: "mode", mode: "home" }] },
  },
  {
    id: "night_mode", title: "Night mode at bedtime", scope: "house",
    hint: "Whole house: switch the house to Night at 10:30 pm, lock up and turn the lights off. Add “Run another routine” steps for anything else.",
    draft: { trigger: "time", time: "22:30:00", actions: [{ kind: "mode", mode: "night" }, { service: "lock.lock", targetMode: "all_areas" }, { service: "light.turn_off", targetMode: "all_areas" }] },
  },
  { id: "blank", title: "Blank routine", hint: "Start from scratch.", scope: "any", draft: {} },
];

// The built-in library: fuller routines than the templates, several steps
// each, by topic. Devices are left for you to pick where a room can't say.
const LIBRARY = [
  {
    id: "lib_gentle_wake", topic: "Mornings", title: "Gentle wake-up", scope: "room",
    hint: "On weekdays at 6:45 am, the bedroom lights fade up over 15 minutes while the speaker plays and turns up slowly. Pick the speaker.",
    draft: { trigger: "time", time: "06:45:00", days: ["mon", "tue", "wed", "thu", "fri"], actions: [
      { service: "light.turn_on", targetMode: "area", fields: { brightness_pct: 70, color_temp_kelvin: 2700, transition: 15 } },
      { service: "media_player.media_play", targetMode: "entities" },
      { kind: "ramp", entities: [], rFrom: 5, rTo: 30, rMinutes: 15 },
    ] },
  },
  {
    id: "lib_sunrise_alarm", topic: "Mornings", title: "Sunrise alarm light", scope: "room",
    hint: "Half an hour before your phone's next alarm, the bedroom lights come up slowly like a sunrise, warm at first. Pick the alarm (the Next alarm sensor from the Home Assistant app).",
    draft: { trigger: "alarm", useAlarm: true, offset_minutes: -30, actions: [
      { service: "light.turn_on", targetMode: "area", fields: { brightness_pct: 100, color_temp_kelvin: 2700, transition: 30 } },
    ] },
  },
  {
    id: "lib_school_year", topic: "Mornings", title: "School-year wake-up", scope: "room",
    hint: "On weekdays from September to mid-June at 6:45 am, this room's lights come on softly. It stops by itself for the summer.",
    draft: { trigger: "time", time: "06:45:00", days: ["mon", "tue", "wed", "thu", "fri"], from_date: "09-01", until_date: "06-15", actions: [
      { service: "light.turn_on", targetMode: "area", fields: { brightness_pct: 50, color_temp_kelvin: 3000, transition: 5 } },
    ] },
  },
  {
    id: "lib_school_morning", topic: "Mornings", title: "School morning", scope: "house",
    hint: "On weekdays at 7:00 am: open every curtain and say it's time to get ready. Pick the voice and speaker, and add + Kind of day to keep it to school days.",
    draft: { trigger: "time", time: "07:00:00", days: ["mon", "tue", "wed", "thu", "fri"], actions: [
      { service: "cover.open_cover", targetMode: "all_areas" },
      { service: "tts.speak", targetMode: "entities", fields: { message: "Good morning! Time to get ready for school." } },
    ] },
  },
  {
    id: "lib_morning_heat", topic: "Mornings", title: "Warm house before getting up", scope: "house",
    hint: "At 6:00 am on weekdays, set the thermostat to 21 °. Pick the thermostat.",
    draft: { trigger: "time", time: "06:00:00", days: ["mon", "tue", "wed", "thu", "fri"], actions: [
      { service: "climate.set_temperature", targetMode: "entities", fields: { temperature: 21 } },
    ] },
  },
  {
    id: "lib_phones_bedtime", topic: "Evenings", title: "Bedtime when the phones are charging", scope: "house",
    hint: "From 9:00 pm, as soon as every phone you tick is on its charger, Night mode and the lights go down. Waits until 1:00 am. Tick the phones under Only if.",
    draft: { trigger: "time", time: "21:00:00", wait: "until", wait_until: "01:00", conditions: [{ kind: "charging", entities: [] }], actions: [
      { kind: "mode", mode: "night" },
      { service: "light.turn_off", targetMode: "all_areas", fields: { transition: 2 } },
    ] },
  },
  {
    id: "lib_holiday_lights", topic: "Evenings", title: "Holiday lights", scope: "house",
    hint: "From December 1 to January 6, the plugs you pick turn on at sunset and off at 11:00 pm (add a second routine for off). Pick the plugs.",
    draft: { trigger: "sunset", from_date: "12-01", until_date: "01-06", actions: [{ service: "switch.turn_on", targetMode: "entities" }] },
  },
  {
    id: "lib_goodnight", topic: "Evenings", title: "Goodnight", scope: "house",
    hint: "At 10:30 pm: Night mode, lock up, close the curtains, then the lights fade out over 2 minutes.",
    draft: { trigger: "time", time: "22:30:00", actions: [
      { kind: "mode", mode: "night" },
      { service: "lock.lock", targetMode: "all_areas" },
      { service: "cover.close_cover", targetMode: "all_areas" },
      { service: "light.turn_off", targetMode: "all_areas", fields: { transition: 2 } },
    ] },
  },
  {
    id: "lib_wind_down", topic: "Evenings", title: "Wind down", scope: "room",
    hint: "An hour before bed: warm, dim lights in this room and the music turned down slowly over 20 minutes. Pick the speaker.",
    draft: { trigger: "time", time: "21:30:00", actions: [
      { service: "light.turn_on", targetMode: "area", fields: { brightness_pct: 35, color_temp_kelvin: 2200, transition: 5 } },
      { kind: "ramp", entities: [], rFrom: 30, rTo: 8, rMinutes: 20 },
    ] },
  },
  {
    id: "lib_occupied_lights", topic: "Evenings", title: "Sunset lights where people are", scope: "house",
    hint: "At sunset, turn on the lights only in rooms someone is in (by their motion sensors).",
    draft: { trigger: "sunset", actions: [{ service: "light.turn_on", targetMode: "all_areas", rooms: "occupied", fields: { brightness_pct: 60 } }] },
  },
  {
    id: "lib_leaving", topic: "Leaving and arriving", title: "Leaving home", scope: "house",
    hint: "Once everyone has left: Away mode, lock up, lights off and the thermostat off.",
    draft: { trigger: "everyone_leaves", usePeopleList: true, actions: [
      { kind: "mode", mode: "away" },
      { service: "lock.lock", targetMode: "all_areas" },
      { service: "light.turn_off", targetMode: "all_areas" },
      { service: "climate.turn_off", targetMode: "all_areas" },
    ] },
  },
  {
    id: "lib_leaving_check", topic: "Leaving and arriving", title: "Did I leave something open?", scope: "house",
    hint: "Once everyone has left, check the doors, windows, garage and locks, and send one notification naming anything still open or unlocked. Nothing is sent when it's all shut.",
    draft: { trigger: "everyone_leaves", usePeopleList: true, actions: [
      { kind: "check_open", entities: [], useOpenings: true, cStop: false },
    ] },
  },
  {
    id: "lib_welcome_announce", topic: "Leaving and arriving", title: "Say who's home", scope: "house",
    hint: "When someone arrives, say “… is home” on a speaker (not during quiet hours). {who} in a message is the person who arrived. Pick the voice and speaker.",
    draft: { trigger: "someone_arrives", usePeopleList: true, actions: [
      { service: "tts.speak", targetMode: "entities", fields: { message: "{who} is home." } },
    ] },
  },
  {
    id: "lib_arriving", topic: "Leaving and arriving", title: "Welcome home", scope: "house",
    hint: "When someone arrives: Home mode, and the lights on if it's after sunset (set the hours under Options).",
    draft: { trigger: "someone_arrives", usePeopleList: true, actions: [
      { kind: "mode", mode: "home" },
      { service: "light.turn_on", targetMode: "all_areas", fields: { brightness_pct: 70 } },
    ] },
  },
  {
    id: "lib_empty_rooms_off", topic: "Saving energy", title: "Lights off in empty rooms", scope: "house",
    hint: "Every evening at 9:00 pm, turn off the lights in rooms nobody is in.",
    draft: { trigger: "time", time: "21:00:00", actions: [{ service: "light.turn_off", targetMode: "all_areas", rooms: "empty" }] },
  },
  {
    id: "lib_fans_off", topic: "Saving energy", title: "Fans off when the room is empty", scope: "room",
    hint: "Turns this room's fans off once its motion sensors have been quiet for 15 minutes.",
    draft: { trigger: "room_clear", clear_minutes: 15, useRoomSensors: true, actions: [{ service: "fan.turn_off", targetMode: "area" }] },
  },
  {
    id: "lib_vacuum", topic: "Chores", title: "Vacuum while everyone's out", scope: "house",
    hint: "Starts the vacuum 10 minutes after everyone has left, on weekdays. Pick the vacuum.",
    draft: { trigger: "everyone_leaves", usePeopleList: true, days: ["mon", "tue", "wed", "thu", "fri"], actions: [
      { kind: "delay", seconds: 600 },
      { service: "vacuum.start", targetMode: "entities" },
    ] },
  },
  {
    id: "lib_laundry", topic: "Chores", title: "Laundry finished", scope: "house",
    hint: "When the washer's power goes over 10 W it has started; once it stays under 5 W for 5 minutes, you get a notification. Pick the washer's power sensor. Turn on Notify me → When it runs to get it on your phone too.",
    draft: { trigger: "value_above", valueClass: "power", valueThreshold: 10, actions: [
      { kind: "wait_until", wEntity: "", wOnValue: true, wBy: "level", wBelow: 5, wAbove: "", wStates: "", wFor: 5, wMinutes: 240, wTimeout: "stop" },
      { service: "notify.persistent_notification", targetMode: "none", fields: { message: "The laundry is done." } },
    ] },
  },
  {
    id: "lib_watering", topic: "Chores", title: "Water the garden, not after rain", scope: "house",
    hint: "Every morning at 6:00 am, the sprinkler runs unless the rain sensor says more than 5 mm fell. Pick the sprinkler and the rain sensor under Only if.",
    draft: { trigger: "time", time: "06:00:00", conditions: [{ kind: "value", entity_id: "", below: 5, above: "" }], actions: [
      { service: "switch.turn_on", targetMode: "entities" },
    ] },
  },
  {
    id: "lib_batteries", topic: "Chores", title: "Battery check", scope: "house",
    hint: "Every Sunday at 10:00 am, one notification naming every battery under 20%, so nothing goes flat unnoticed.",
    draft: { trigger: "time", time: "10:00:00", days: ["sun"], actions: [{ kind: "battery", bBelow: 20 }] },
  },
  {
    id: "lib_bedtime_check", topic: "Evenings", title: "Bedtime checklist", scope: "house",
    hint: "At 9:30 pm, a checklist shows on the dashboard card; once it's all ticked off, the lights go off.",
    draft: { trigger: "time", time: "21:30:00", actions: [
      { kind: "ticks", tItems: "Back door locked\nStove off\nTablets charging", tMinutes: 60, tTimeout: "continue" },
      { service: "light.turn_off", targetMode: "all_areas", fields: { transition: 1 } },
    ] },
  },
  {
    id: "lib_room_look", topic: "Comfort", title: "A look for this room", scope: "room",
    hint: "Save how this room looks for reading, a movie or cleaning, and put it back with one tap. Set the lights, press Save how they look now, and add the Daily Routine card with buttons: true to tap it from a dashboard.",
    draft: { trigger: "manual", group: "Looks", actions: [{ kind: "scene", entities: [], saved: {}, transition: "" }] },
  },
  {
    id: "lib_movie", topic: "Comfort", title: "Movie time", scope: "room",
    hint: "Press Run now (or ask Assist): close the curtains and dim this room's lights to 10%.",
    draft: { trigger: "manual", actions: [
      { service: "cover.close_cover", targetMode: "area" },
      { service: "light.turn_on", targetMode: "area", fields: { brightness_pct: 10, transition: 0.5 } },
    ] },
  },
];

// How long after a restart a missed routine may still run, in minutes.
const CATCH_UP_CHOICES = [0, 15, 30, 60, 120, 180];
// Global timing settings: key, label, help, and the same limits the server checks.
const TIMING_FIELDS = [
  ["list_wait_seconds", "Wait for a to-do list (seconds)", "How long a to-do step waits for a list that has not loaded yet", 0, 300],
  ["health_check_minutes", "Health check every (minutes)", "How often every routine's devices and lists are checked", 1, 60],
  ["health_alert_seconds", "Health alert delay (seconds)", "How long a problem must last before it is announced", 0, 600],
  ["startup_quiet_minutes", "Startup quiet period (minutes)", "No health alerts for this long after Home Assistant starts", 0, 60],
  ["step_timeout_seconds", "Give up on a device after (seconds)", "How long a step waits for a device that doesn't answer (0 = no limit). Scripts aren't limited", 0, 600],
  ["verify_seconds", "Check a step worked within (seconds)", "After turning something on or off, opening, closing or locking it, how long to wait for it to change (0 = don't check)", 0, 120],
];
const TIMING_DEFAULTS = { list_wait_seconds: 30, health_check_minutes: 15, health_alert_seconds: 60, startup_quiet_minutes: 5, step_timeout_seconds: 30, verify_seconds: 10 };
// Minutes before a scheduled run that the heads-up is sent.
const HEADS_UP_CHOICES = [0, 5, 10, 15, 30, 60];
// A yearly "MM-DD" as a date input's value (this year), and back.
function monthDayInput(md) {
  return md ? `${new Date().getFullYear()}-${md}` : "";
}
function monthDay(value) {
  const text = String(value || "").trim();
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text.slice(5) : null;
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function describeMonthDay(md) {
  const [m, d] = md.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]}`;
}
// Try a device that couldn't be changed again after this many minutes.
const RETRY_LATER_CHOICES = [0, 2, 5, 10, 15, 30, 60];

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

// Long lists of devices (checkboxes) and device drop-downs get a search box.
function isDevicePicker(el) {
  if (el.tagName === "SELECT") {
    const options = [...el.options].filter((o) => o.value);
    if (options.length < 10) return false;
    if (el.dataset.sf === "actionChoice") return false;
    if (["device_id", "routine"].includes(el.dataset.f || el.dataset.sf)) return true;
    return options.filter((o) => o.value.includes(".")).length >= options.length / 2;
  }
  if (el.querySelector("[data-cweather]")) return false; // kinds of weather, not devices
  return el.querySelectorAll("label").length >= 8;
}

function filterPicker(el, text) {
  const q = text.trim().toLowerCase();
  if (el.tagName === "SELECT") {
    if (!el._all) el._all = [...el.options].map((o) => ({ value: o.value, text: o.text }));
    const current = el.value;
    el.innerHTML = el._all
      .filter((o) => !q || !o.value || o.value === current || o.text.toLowerCase().includes(q))
      .map((o) => `<option value="${esc(o.value)}" ${o.value === current ? "selected" : ""}>${esc(o.text)}</option>`)
      .join("");
    el.value = current;
    return;
  }
  for (const label of el.querySelectorAll("label")) {
    label.style.display = !q || label.textContent.toLowerCase().includes(q) ? "" : "none";
  }
}

// How reliably the routine's devices answered over 30 days.
// An ISO date a number of days later.
function addDays(iso, days) {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// A running routine's checklist: tap a thing to tick it off.
function tickListHtml(routineId, ticklist) {
  if (!ticklist) return "";
  const ticked = new Set(ticklist.ticked || []);
  return `<ul class="ticklist">${ticklist.items.map((item) => {
    const done = ticked.has(item);
    return `<li><button class="${done ? "" : "ghost"}" data-act="tick" data-id="${esc(routineId)}" data-item="${esc(item)}" data-done="${done ? "" : "1"}" aria-pressed="${done}">${done ? "✓" : "○"} ${esc(item)}</button></li>`;
  }).join("")}</ul>`;
}

// Routines that keep failing or being skipped, with what to look at.
function attentionHtml(rows) {
  if (!rows?.length) return "";
  return `<section class="card attention"><h3>Not working well</h3>
    <p class="muted small">These worked on fewer than half of their tries in the last two weeks. <strong>Why not?</strong> and <strong>History</strong> on their cards say more.</p>
    <ul class="plain">${rows.map((r) => `<li><strong>${esc(r.name)}</strong> <span class="muted small">ran ${r.ran} of ${r.tries} times${r.failed ? ` · failed ${r.failed}` : ""}${r.skipped ? ` · skipped ${r.skipped}` : ""}</span>
      <button class="ghost" data-act="why" data-id="${esc(r.routine_id)}">Why not?</button></li>`).join("")}</ul></section>`;
}

// The last 30 days' power change by routine.
function energyHtml(energy) {
  const rows = energy?.rows || [];
  if (!rows.length) return "";
  const watts = (w) => (Math.abs(w) >= 1000 ? `${Math.round(Math.abs(w) / 100) / 10} kW` : `${Math.round(Math.abs(w))} W`);
  return `<section class="card"><h3>Power over the last 30 days</h3>
    <p class="muted small">From the power sensors on the devices each routine changes, a minute after each run.${energy.turned_off ? ` Altogether routines turned off ${esc(watts(energy.turned_off))}.` : ""}</p>
    <ul class="plain">${rows.map((r) => `<li><strong>${esc(r.name)}</strong> <span class="muted small">${r.runs} ${r.runs === 1 ? "run" : "runs"} · ${r.average_change <= 0 ? "turned off" : "turned on"} ${esc(watts(r.average_change))} a run on average</span></li>`).join("")}</ul></section>`;
}

// The guest network, while the house is in Guests mode.
function guestWifiHtml(wifi) {
  if (!wifi) return "";
  return `<section class="card guest-wifi"><h3>Guest Wi-Fi</h3>
    <p class="wifi-line"><span class="muted small">Network</span><strong>${esc(wifi.ssid)}</strong></p>
    ${wifi.password ? `<p class="wifi-line"><span class="muted small">Password</span><strong class="mono">${esc(wifi.password)}</strong></p>` : `<p class="muted small">No password.</p>`}
  </section>`;
}

// Ran, skipped and failed per day as stacked bars (oldest first), with the
// totals underneath.
function historyChart(days) {
  const list = days || [];
  const max = Math.max(1, ...list.map((d) => d.ran + d.failed + d.skipped));
  const w = 10;
  const gap = 3;
  const h = 80;
  const width = list.length * (w + gap);
  const bars = list.map((d, i) => {
    const x = i * (w + gap);
    let y = h;
    const parts = [["ran", "ok"], ["skipped", "skip"], ["failed", "fail"]].map(([key, cls]) => {
      if (!d[key]) return "";
      const height = Math.max(2, Math.round((d[key] / max) * h));
      y -= height;
      return `<rect class="${cls}" x="${x}" y="${y}" width="${w}" height="${height}" rx="2"><title>${esc(d.day)}: ${d[key]} ${key}</title></rect>`;
    }).join("");
    return parts || `<rect class="none" x="${x}" y="${h - 2}" width="${w}" height="2" rx="1"><title>${esc(d.day)}: nothing</title></rect>`;
  }).join("");
  const sum = (key) => list.reduce((n, d) => n + d[key], 0);
  const first = list[0]?.day || "";
  const last = list[list.length - 1]?.day || "";
  return `<svg class="history-chart" viewBox="0 0 ${width} ${h + 14}" role="img" aria-label="Runs per day for ${list.length} days">${bars}
      <text x="0" y="${h + 12}" class="axis">${esc(first.slice(5))}</text><text x="${width}" y="${h + 12}" class="axis" text-anchor="end">${esc(last.slice(5))}</text></svg>
    <p class="muted small"><span class="key ok"></span> Ran ${sum("ran")} <span class="key skip"></span> Skipped ${sum("skipped")} <span class="key fail"></span> Failed ${sum("failed")} · last ${list.length} days</p>`;
}

function reliabilityHtml(rel) {
  if (!rel || rel.score == null) return "";
  const worst = (rel.worst || []).map((w) => `${w.name}${w.failed ? ` failed ${w.failed}×` : ` needed a retry ${w.retried}×`}`).join(", ");
  return `<div class="muted small reliability ${rel.score < 90 ? "warn-text" : ""}">📶 Its devices answered ${rel.score}% of the time${worst ? ` · ${esc(worst)}` : ""}</div>`;
}

// Power before and after the last run, and the average change.
function powerHtml(power) {
  if (!power?.last) return "";
  const watts = (w) => (Math.abs(w) >= 1000 ? `${Math.round(w / 100) / 10} kW` : `${Math.round(w)} W`);
  const change = power.average_change;
  const avg = power.runs > 1 ? ` · ${change <= 0 ? "saves" : "adds"} about ${watts(Math.abs(change))} on average` : "";
  return `<div class="muted small power-line">⚡ Last run: ${watts(power.last.before)} → ${watts(power.last.after)}${avg}</div>`;
}

function toggleIn(list, value, on) {
  const i = list.indexOf(value);
  if (on && i < 0) list.push(value);
  if (!on && i >= 0) list.splice(i, 1);
}

function clockText(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

// How much of a timed step (a wait) has passed, from 0 to 1.
function stepPart(from, to) {
  if (!from || !to) return 0;
  const span = new Date(to) - new Date(from);
  if (span <= 0) return 1;
  return Math.max(0, Math.min(1, (Date.now() - new Date(from)) / span));
}

function barPercent(step, total, part) {
  return Math.max(3, Math.min(100, Math.round(((step - 1 + part) / Math.max(1, total)) * 100)));
}

// How far a running routine has got: a bar and "Step 2 of 5 · Close Bedroom curtains".
function progressHtml(progress) {
  if (!progress) {
    return `<div class="progress indeterminate" role="progressbar" aria-label="Starting"><div class="bar"></div></div>
      <div class="small progress-text">Starting…</div>`;
  }
  const { step, total, label, until, step_started: stepStarted } = progress;
  const part = stepPart(stepStarted, until);
  const pct = barPercent(step, total, part);
  const left = until ? Math.max(0, Math.round((new Date(until) - Date.now()) / 1000)) : 0;
  const timed = until && stepStarted ? ` data-from="${esc(stepStarted)}" data-to="${esc(until)}" data-step="${step}" data-total="${total}"` : "";
  return `<div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${step - 1}" aria-label="Step ${step} of ${total}"><div class="bar"${timed} style="width:${pct}%"></div></div>
    <div class="small progress-text">Step ${step} of ${total} · ${esc(label)}${until ? `<span data-until="${esc(until)}">${left ? ` · ${clockText(left)} left` : ""}</span>` : ""}</div>`;
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
    if (hass?.services) SERVICE_DESCRIPTIONS = hass.services;
    if (!this._data) this._load();
    if (this.isConnected) this._subscribe();
  }

  connectedCallback() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._wire();
    this._subscribe();
    // A slower check in case live updates aren't coming through.
    this._timer = setInterval(() => {
      if (document.hidden || this._live) return;
      if (LIST_VIEWS.includes(this._view.name)) this._refresh();
      if (this._view.name === "activity") this._loadActivity(true);
    }, REFRESH_MS);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    clearInterval(this._ticker);
    this._ticker = null;
    const unsub = this._unsub;
    this._unsub = null;
    this._live = false;
    if (unsub) unsub.then((stop) => stop && stop()).catch(() => {});
  }

  // Run states pushed by Home Assistant as they change: Running, the step it's on, and done.
  _subscribe() {
    if (this._unsub || !this._hass?.connection?.subscribeMessage) return;
    this._unsub = this._hass.connection
      .subscribeMessage((status) => this._onStatus(status), { type: `${DOMAIN}/subscribe` });
    this._unsub.then(() => { this._live = true; }).catch(() => { this._unsub = null; this._live = false; });
  }

  _onStatus(status) {
    this._live = true;
    if (!this._data) return;
    if (status.revision !== this._data.revision) {
      this._load(true);
      return;
    }
    const logChanged = this._logCount !== undefined && status.log_count !== this._logCount;
    this._logCount = status.log_count;
    const { revision, paused, statuses, mode, lived_in, guest_wifi } = status;
    const json = JSON.stringify({ revision, paused, statuses, mode, lived_in, guest_wifi });
    if (json !== this._statusJson) {
      this._statusJson = json;
      this._data.statuses = statuses;
      this._data.paused = paused;
      if (mode) this._data.mode = mode;
      if (lived_in) this._data.lived_in = lived_in;
      this._data.guest_wifi = guest_wifi || null;
      if (LIST_VIEWS.includes(this._view.name) || ["activity", "settings"].includes(this._view.name)) this._render();
      else if (["editor", "todo-editor", "templates"].includes(this._view.name)) {
        const pick = this.shadowRoot?.querySelector("[data-mode-pick]");
        if (pick && mode) pick.value = mode;
      }
    }
    if (logChanged && this._view.name === "activity") this._loadActivity(true);
    if ((logChanged || json !== this._todayJson) && this._view.name === "today") {
      this._todayJson = json;
      this._loadToday(true);
    }
    this._tick();
  }

  // Counts down a waiting step on the cards without asking Home Assistant again.
  _tick() {
    const running = Object.values(this._data?.statuses || {}).some((s) => s.state === "running");
    if (running && !this._ticker) {
      this._ticker = setInterval(() => this._updateCountdowns(), 1000);
    } else if (!running && this._ticker) {
      clearInterval(this._ticker);
      this._ticker = null;
    }
  }

  _updateCountdowns() {
    for (const el of this.shadowRoot?.querySelectorAll("[data-until]") || []) {
      const left = Math.max(0, Math.round((new Date(el.dataset.until) - Date.now()) / 1000));
      el.textContent = left ? ` · ${clockText(left)} left` : "";
    }
    for (const bar of this.shadowRoot?.querySelectorAll(".bar[data-to]") || []) {
      const part = stepPart(bar.dataset.from, bar.dataset.to);
      bar.style.width = `${barPercent(Number(bar.dataset.step), Number(bar.dataset.total), part)}%`;
    }
  }

  _wire() {
    if (this._wired) return;
    this._wired = true;
    this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
    this.shadowRoot.addEventListener("change", (e) => this._onField(e));
    this.shadowRoot.addEventListener("input", (e) => this._onField(e, true));
    // Drag a folded step to move it.
    this.shadowRoot.addEventListener("dragstart", (e) => {
      const li = e.target.closest?.(".step-fold");
      if (!li) return;
      this._dragFrom = Number(li.dataset.step);
      e.dataTransfer?.setData("text/plain", li.dataset.step);
    });
    this.shadowRoot.addEventListener("dragover", (e) => {
      if (this._dragFrom !== undefined && e.target.closest?.(".step-fold")) e.preventDefault();
    });
    this.shadowRoot.addEventListener("drop", (e) => {
      const li = e.target.closest?.(".step-fold");
      if (!li || this._dragFrom === undefined || !this._draft) return;
      e.preventDefault();
      const to = Number(li.dataset.step);
      const [moved] = this._draft.actions.splice(this._dragFrom, 1);
      this._draft.actions.splice(to, 0, moved);
      this._dragFrom = undefined;
      this._render();
    });
    // Remember whether "Going away?" is open across redraws (toggle doesn't bubble).
    this.shadowRoot.addEventListener("toggle", (e) => {
      if (e.target?.classList?.contains("away")) this._awayOpen = e.target.open;
    }, true);
  }

  async _call(message) {
    return this._hass.callWS(message);
  }

  async _load(silent = false) {
    if (!this._hass) return;
    // The thin progress bar shows through the host class, so no redraw is needed to start it.
    this.classList.toggle("busy", !silent);
    let changed = true;
    if (!this._me) {
      try {
        this._me = await this._call({ type: `${DOMAIN}/access/me` });
      } catch (_err) {
        this._me = { allowed: true, is_admin: !!this._hass.user?.is_admin };
      }
    }
    if (this._me.allowed === false) {
      this.classList.remove("busy");
      this._render();
      return;
    }
    if (this._tags === undefined && this._me?.is_admin) {
      this._tags = [];
      this._call({ type: "tag/list" }).then((tags) => { this._tags = Array.isArray(tags) ? tags : []; }).catch(() => {});
    }
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
      this._onStatus(status);
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
    return this._devices().find((d) => d.area_id === roomId && !d.service)?.id || "";
  }

  _linkedName(deviceId) {
    const device = this._devices().find((d) => d.id === deviceId);
    return device ? this._deviceLabel(device) : "Daily Routine (no device)";
  }

  // "Office Curtains (SwitchBot Cloud)": which integration the device comes
  // from, so devices with the same name can be told apart.
  _deviceLabel(device) {
    return device.integration ? `${device.name} (${device.integration})` : device.name;
  }

  _deviceOptions(selected, roomId) {
    // Only real devices, not services; a routine already linked to a service
    // keeps it listed so saving doesn't unlink it.
    const devices = this._devices().filter((d) => !d.service || d.id === selected).sort((a, b) =>
      Number(roomId && b.area_id === roomId) - Number(roomId && a.area_id === roomId) || a.name.localeCompare(b.name));
    const none = `<option value="" ${selected ? "" : "selected"}>Daily Routine (no device)</option>`;
    const options = devices.map((d) => {
      const place = d.area_name ? ` · ${d.area_name}` : "";
      return `<option value="${esc(d.id)}" ${d.id === selected ? "selected" : ""}>${esc(this._deviceLabel(d))}${esc(place)}</option>`;
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
    const main = this._describeOne(routine);
    const more = (routine.more_triggers || []).map((t) => this._describeOne({ ...routine, ...t, active_from: null, active_to: null, random_minutes: 0 }));
    return [main, ...more].join(" · or ");
  }

  _tagName(id) {
    const tag = (this._tags || []).find((t) => t.id === id);
    return tag?.name || id || "a tag";
  }

  _describeOne(routine) {
    const when = TRIGGER_LABELS[routine.trigger] || routine.trigger;
    if (routine.trigger === "mode_changes") {
      return `House mode changes to ${(routine.modes || []).map(modeName).join(" or ")}`;
    }
    const offsetText = (m) => (m ? `${Math.abs(m)} min ${m < 0 ? "before" : "after"}` : "At");
    if (routine.trigger === "alarm") return `${offsetText(routine.offset_minutes)} ${this._nameFor(routine.watch_entity)}`;
    if (routine.trigger === "calendar_event") {
      const what = routine.watch_item ? `“${routine.watch_item}”` : "any event";
      return `${offsetText(routine.offset_minutes)} ${what} in ${this._nameFor(routine.watch_entity)}`;
    }
    if (routine.trigger === "forecast") {
      const kinds = (routine.watch_states || []).map((k) => WEATHER_NAMES[k] || k).join(", ");
      const cold = routine.value_threshold != null ? `below ${routine.value_threshold}°` : "";
      return `${[kinds, cold].filter(Boolean).join(" or ")} forecast within ${routine.forecast_hours || 3} h`;
    }
    if (routine.trigger === "cheapest") return `Cheapest hour between ${hhmm(routine.active_from)} and ${hhmm(routine.active_to)}`;
    if (routine.trigger === "tag") return `Tag “${this._tagName(routine.watch_item)}” is scanned`;
    if (routine.trigger === "button") {
      const presses = (routine.watch_states || []).length ? ` (${routine.watch_states.join(" or ")})` : "";
      return `${this._nameFor(routine.watch_entity)} is pressed${presses}`;
    }
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
    const vary = routine.random_minutes ? ` ± ${routine.random_minutes} min` : "";
    if (routine.trigger === "time") return `${when} ${hhmm(routine.time)}${vary}${windowText}`;
    if (routine.trigger === "sunrise" || routine.trigger === "sunset") {
      const offset = routine.offset_minutes ? ` ${routine.offset_minutes > 0 ? "+" : ""}${routine.offset_minutes} min` : "";
      return `${when}${offset}${vary}${windowText}`;
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
      id: "", name: "", group: "", room: roomId || null, device_id: this._defaultDevice(roomId), enabled: true, zone: "zone.home",
      value_entity: "", value_threshold: "", off_threshold: "", watch_entity: "", watch_states: "", hold_minutes: 0, watch_item: "", active_from: "", active_to: "",
      trigger: "time", time: "07:00:00", offset_minutes: 0, random_minutes: 0, skip_modes: [], not_before: "", not_after: "", paused_until: "", skip_next: false, days: [...WEEKDAYS],
      people: [], sensors: [], clear_minutes: 0,
      conditions: [], conditions_match: "all", repeat: false, catch_up_minutes: 0, retries: 0, skip_noop: true, wait: "day", wait_until: "12:00", wait_minutes: 5,
      notify_on_run: false, notify_on_skip: false, notify_on_error: true, notify_on_health: false, wait_when_unavailable: false,
      modes: [], more_triggers: [], forecast_kinds: [], forecast_hours: 3, ask_first: false, ask_minutes: 10, ask_default: "run",
      actions: [{ kind: "service", service: "", targetMode: roomId ? "area" : "entities", area: roomId || "", entities: [], data: "" }],
      isNew: true,
    };
  }

  _applyTemplate(template, roomId) {
    const draft = this._newDraft(roomId);
    const spec = template.draft || {};
    draft.name = template.title === "Blank routine" ? "" : template.title;
    if (spec.trigger) draft.trigger = spec.trigger;
    draft.wait = defaultWait(draft.trigger);
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
    if (spec.modes) draft.modes = [...spec.modes];
    if (spec.days) draft.days = [...spec.days];
    if (spec.group) draft.group = spec.group;
    if (spec.conditions) draft.conditions.push(...spec.conditions.map(cloneCond));
    for (const key of ["offset_minutes", "watch_entity", "watch_item", "value_entity", "forecast_hours", "active_from", "active_to", "from_date", "until_date", "wait", "wait_until"]) {
      if (spec[key] !== undefined) draft[key] = spec[key];
    }
    if (spec.forecast_kinds) draft.forecast_kinds = [...spec.forecast_kinds];
    if (spec.useAlarm) draft.watch_entity = this._data.alarm_sensors?.[0]?.entity_id || "";
    if (spec.actions) {
      draft.actions = spec.actions.map((a) => (a.kind
        ? this._libraryStep(a, draft)
        : { kind: "service", service: a.service, targetMode: a.targetMode, area: roomId || "", entities: [...(a.entities || [])], data: "", fields: { ...(a.fields || {}) }, rooms: a.rooms || "" }));
    }
    if (draft.actions.length === 1) draft.actions[0]._open = true;
    return draft;
  }

  // A library step, filled from this home: the power sensor the trigger
  // watches, or every door, window, garage and lock.
  _libraryStep(a, draft) {
    const step = { service: "", targetMode: "none", area: "", entities: [], data: "", ...a };
    if (a.wOnValue) step.wEntity = draft.value_entity || "";
    if (a.useOpenings) step.entities = this._openings().map((e) => e.entity_id);
    delete step.wOnValue;
    delete step.useOpenings;
    return step;
  }

  // Doors, windows, garage doors and locks: what a leaving-home check looks at.
  _openings() {
    const doors = (this._data.binary_sensors || []).filter((s) => ["door", "window", "garage_door", "opening"].includes(s.device_class));
    const others = (this._data.all_entities || []).filter((e) => ["lock.", "cover."].some((p) => e.entity_id.startsWith(p)) && (e.entity_id.startsWith("lock.") || /garage|door|gate|window/i.test(`${e.entity_id} ${e.name}`)));
    return [...doors, ...others];
  }

  _draftFromRoutine(routine) {
    const areaIds = this._areas().map((a) => a.id);
    return {
      id: routine.id, name: routine.name, group: routine.group || "", room: routine.room || null, device_id: routine.device_id || "", enabled: routine.enabled, zone: routine.zone || "zone.home",
      value_entity: routine.value_entity || "", watch_entity: routine.watch_entity || "", watch_states: (routine.watch_states || []).join(", "), hold_minutes: routine.hold_minutes || 0, watch_item: routine.watch_item || "", value_threshold: routine.value_threshold ?? "", off_threshold: routine.off_threshold ?? "",
      active_from: routine.active_from ? hhmm(routine.active_from) : "", active_to: routine.active_to ? hhmm(routine.active_to) : "",
      trigger: routine.trigger, time: routine.time, offset_minutes: routine.offset_minutes || 0, days: [...routine.days],
      random_minutes: routine.random_minutes || 0, skip_modes: [...(routine.skip_modes || [])], ignore_holds: Boolean(routine.ignore_holds), loud_ok: Boolean(routine.loud_ok), skip_dates: [...(routine.skip_dates || [])],
      hotEntity: routine.hot_shift?.entity_id || "", hotAbove: routine.hot_shift?.above ?? 28, hotMinutes: routine.hot_shift?.minutes ?? -60,
      not_before: routine.not_before ? hhmm(routine.not_before) : "", not_after: routine.not_after ? hhmm(routine.not_after) : "", paused_until: routine.paused_until || "", skip_next: Boolean(routine.skip_next),
      people: [...routine.people], sensors: [...routine.sensors], clear_minutes: routine.clear_minutes,
      conditions: (routine.conditions || []).map((c) => ({ ...c, people: [...(c.people || [])], sensors: [...(c.sensors || [])] })),
      conditions_match: routine.conditions_match || "all", repeat: routine.repeat,
      wait: routine.wait || "day", wait_until: hhmm(routine.wait_until || "12:00:00"), wait_minutes: routine.wait_minutes || 5, cooldown_minutes: routine.cooldown_minutes || 0,
      integration_order: routine.integration_order ? [...routine.integration_order] : null,
      catch_up_minutes: routine.catch_up_minutes, retries: routine.retries, skip_noop: routine.skip_noop,
      on_step_error: routine.on_step_error || "stop", retry_later_minutes: routine.retry_later_minutes ?? 5,
      from_date: routine.from_date || null, until_date: routine.until_date || null, heads_up_minutes: routine.heads_up_minutes || 0,
      notify_on_run: routine.notify_on_run, notify_on_skip: routine.notify_on_skip, notify_on_error: routine.notify_on_error,
      notify_on_health: Boolean(routine.notify_on_health),
      wait_when_unavailable: Boolean(routine.wait_when_unavailable),
      modes: [...(routine.modes || [])],
      forecast_kinds: routine.trigger === "forecast" ? [...(routine.watch_states || [])] : [],
      forecast_hours: routine.forecast_hours || 3,
      ask_first: Boolean(routine.ask_first),
      ask_minutes: routine.ask_minutes || 10,
      ask_default: routine.ask_default || "run",
      more_triggers: (routine.more_triggers || []).map((t) => ({
        ...t, people: [...(t.people || [])], modes: [...(t.modes || [])], watch_states: (t.watch_states || []).join(", "),
        value_threshold: t.value_threshold ?? "", value_entity: t.value_entity || "", watch_entity: t.watch_entity || "",
      })),
      actions: flatSteps(routine.actions).map((a) => {
        const blank = { service: "", targetMode: "none", area: "", entities: [], data: "" };
        if ("run_routine" in a) return { ...blank, kind: "routine", routine: a.run_routine };
        if ("set_mode" in a) return { ...blank, kind: "mode", mode: a.set_mode };
        if ("briefing" in a) return { ...blank, kind: "briefing", bVoice: a.briefing.voice, bSpeaker: a.briefing.speaker, bWeather: a.briefing.weather || "", entities: [...a.briefing.calendars], bDay: a.briefing.day_type !== false };
        if ("check_battery" in a) return { ...blank, kind: "battery", bBelow: a.check_battery.below };
        if ("tick_list" in a) return { ...blank, kind: "ticks", tItems: a.tick_list.items.join("\n"), tMinutes: a.tick_list.minutes, tTimeout: a.tick_list.on_timeout };
        if ("check_open" in a) return { ...blank, kind: "check_open", entities: [...a.check_open.entity_id], cStop: Boolean(a.check_open.stop) };
        if ("wait_cheaper" in a) return { ...blank, kind: "cheaper", cEntity: a.wait_cheaper.entity_id, cMinutes: a.wait_cheaper.minutes };
        if ("volume_ramp" in a) return {
          ...blank, kind: "ramp", entities: [...a.volume_ramp.entity_id],
          rFrom: a.volume_ramp.from, rTo: a.volume_ramp.to, rMinutes: a.volume_ramp.minutes,
        };
        if ("wait_until" in a) return {
          ...blank, kind: "wait_until", wEntity: a.wait_until.entity_id, wStates: a.wait_until.states.join(", "),
          wMinutes: a.wait_until.minutes, wTimeout: a.wait_until.on_timeout,
          wBy: a.wait_until.below != null || a.wait_until.above != null ? "level" : "state",
          wBelow: a.wait_until.below ?? "", wAbove: a.wait_until.above ?? "", wFor: a.wait_until.for_minutes || 0,
        };
        if (a.service === "scene.apply") {
          const saved = a.data?.entities || {};
          return { ...blank, kind: "scene", entities: Object.keys(saved), saved: structuredClone(saved), transition: a.data?.transition ?? "" };
        }
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
        const spec = actionSpec(a.service);
        const { fields, rest } = dataToFields(spec, a.data);
        return {
          kind: "service", service: a.service, targetMode, area: areas[0] || "", entities: [...entities], rooms: a.rooms || "",
          voice: spec?.voice ? entities[0] || "" : "", fields,
          data: Object.keys(rest).length ? JSON.stringify(rest, null, 2) : "",
        };
      }).map((step, i, all) => {
        const source = flatSteps(routine.actions)[i];
        const out = source.only_if ? { ...step, onlyIf: cloneCond(source.only_if) } : step;
        if (source.__withPrev) out.withPrev = true;
        if (all.length === 1) out._open = true;
        return out;
      }),
      isNew: false,
    };
  }

  _draftToRaw(draft) {
    const areaIds = this._areas().map((a) => a.id);
    const actions = draft.actions.map((step, index) => {
      if (step.kind === "delay") return { delay: Number(step.seconds) || 0 };
      if (step.kind === "routine") {
        if (!step.routine) throw new Error(`Step ${index + 1}: choose the routine to run.`);
        return { run_routine: step.routine };
      }
      if (step.kind === "mode") return { set_mode: step.mode || "home" };
      if (step.kind === "briefing") {
        if (!step.bVoice) throw new Error(`Step ${index + 1}: choose the voice.`);
        if (!step.bSpeaker) throw new Error(`Step ${index + 1}: choose the speaker.`);
        return { briefing: { voice: step.bVoice, speaker: step.bSpeaker, weather: step.bWeather || null, calendars: [...(step.entities || [])], day_type: step.bDay !== false } };
      }
      if (step.kind === "battery") return { check_battery: { below: Math.max(1, Math.min(99, Math.round(Number(step.bBelow) || 20))) } };
      if (step.kind === "ticks") {
        const items = String(step.tItems || "").split("\n").map((x) => x.trim()).filter(Boolean);
        if (!items.length) throw new Error(`Step ${index + 1}: list the things to tick off.`);
        return { tick_list: { items, minutes: Math.max(1, Math.min(240, Math.round(Number(step.tMinutes) || 30))), on_timeout: step.tTimeout === "stop" ? "stop" : "continue" } };
      }
      if (step.kind === "check_open") {
        if (!(step.entities || []).length) throw new Error(`Step ${index + 1}: choose the doors and windows to check.`);
        return { check_open: { entity_id: [...step.entities], stop: Boolean(step.cStop) } };
      }
      if (step.kind === "cheaper") {
        if (!step.cEntity) throw new Error(`Step ${index + 1}: choose the electricity price sensor.`);
        return { wait_cheaper: { entity_id: step.cEntity, minutes: Math.max(15, Math.min(720, Math.round(Number(step.cMinutes) || 180))) } };
      }
      if (step.kind === "ramp") {
        if (!step.entities.length) throw new Error(`Step ${index + 1}: pick the speakers to turn up.`);
        const pct = (v, d) => Math.max(0, Math.min(100, Math.round(v === "" || v == null ? d : Number(v))));
        return { volume_ramp: { entity_id: [...step.entities], from: pct(step.rFrom, 5), to: pct(step.rTo, 30), minutes: Math.max(1, Math.min(60, Math.round(Number(step.rMinutes) || 10))) } };
      }
      if (step.kind === "wait_until") {
        if (!step.wEntity) throw new Error(`Step ${index + 1}: choose the device to wait for.`);
        const level = step.wBy === "level";
        const states = level ? [] : splitStates(step.wStates);
        const num = (v) => (v === "" || v == null ? null : Number(v));
        if (!level && !states.length) throw new Error(`Step ${index + 1}: type the state to wait for.`);
        if (level && num(step.wBelow) == null && num(step.wAbove) == null) throw new Error(`Step ${index + 1}: type the level to wait for.`);
        const wait = { entity_id: step.wEntity, states, minutes: Math.max(1, Math.min(240, Number(step.wMinutes) || 10)), on_timeout: step.wTimeout === "stop" ? "stop" : "continue" };
        if (level && num(step.wBelow) != null) wait.below = num(step.wBelow);
        if (level && num(step.wAbove) != null) wait.above = num(step.wAbove);
        if (Number(step.wFor) > 0) wait.for_minutes = Math.min(240, Math.round(Number(step.wFor)));
        return { wait_until: wait };
      }
      if (step.kind === "scene") {
        const saved = Object.fromEntries(step.entities.filter((id) => step.saved?.[id]).map((id) => [id, step.saved[id]]));
        if (!Object.keys(saved).length) throw new Error(`Step ${index + 1}: pick the devices and press Save how they look now.`);
        const data = { entities: saved };
        if (step.transition !== "" && step.transition != null) data.transition = Number(step.transition) * 60;
        return { service: "scene.apply", data };
      }
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
      const spec = actionSpec(step.service.trim());
      let target = {};
      if (spec?.voice) {
        if (!step.voice) throw new Error(`Step ${index + 1}: choose a voice.`);
        target = { entity_id: [step.voice] };
      } else if (step.targetMode === "area" && step.area) target = { area_id: [step.area] };
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
      Object.assign(data, fieldsToData(spec, step.fields, index));
      const raw = { service: step.service.trim(), data };
      if (Object.keys(target).length && !spec?.untargeted) raw.target = target;
      if (step.rooms && raw.target?.area_id) raw.rooms = step.rooms;
      return raw;
    }).map((raw, i) => {
      const cond = draft.actions[i].onlyIf;
      if (!cond?.kind || "delay" in raw) return raw;
      return { ...raw, only_if: condToRaw(cond) };
    });
    const grouped = groupSteps(actions, draft.actions);
    return {
      id: draft.id || slugify(draft.name),
      name: draft.name.trim(),
      group: String(draft.group || "").trim(),
      enabled: draft.enabled,
      room: draft.room || null,
      device_id: draft.device_id || null,
      zone: draft.zone || "zone.home",
      value_entity: draft.value_entity || null,
      watch_entity: draft.watch_entity || null,
      watch_states: draft.trigger === "forecast" ? [...(draft.forecast_kinds || [])] : splitStates(draft.watch_states),
      hold_minutes: Number(draft.hold_minutes) || 0,
      watch_item: String(draft.watch_item || "").trim(),
      value_threshold: draft.value_threshold === "" || draft.value_threshold == null ? null : Number(draft.value_threshold),
      off_threshold: draft.off_threshold === "" || draft.off_threshold == null ? null : Number(draft.off_threshold),
      active_from: draft.active_from || null,
      active_to: draft.active_to || null,
      trigger: draft.trigger,
      time: draft.time,
      offset_minutes: Number(draft.offset_minutes) || 0,
      random_minutes: Number(draft.random_minutes) || 0,
      skip_modes: [...(draft.skip_modes || [])],
      ignore_holds: Boolean(draft.ignore_holds),
      loud_ok: Boolean(draft.loud_ok),
      skip_dates: [...(draft.skip_dates || [])],
      hot_shift: ["time", "sunrise", "sunset"].includes(draft.trigger) && draft.hotEntity && Number(draft.hotMinutes)
        ? { entity_id: draft.hotEntity, above: Number(draft.hotAbove), minutes: Math.round(Number(draft.hotMinutes)) }
        : null,
      not_before: ["sunrise", "sunset"].includes(draft.trigger) && draft.not_before ? `${draft.not_before}:00`.slice(0, 8) : null,
      not_after: ["sunrise", "sunset"].includes(draft.trigger) && draft.not_after ? `${draft.not_after}:00`.slice(0, 8) : null,
      paused_until: draft.paused_until || null,
      skip_next: Boolean(draft.skip_next),
      days: draft.days,
      people: draft.people,
      sensors: draft.sensors,
      clear_minutes: Number(draft.clear_minutes) || 0,
      conditions: draft.conditions
        .map(condToRaw)
        .filter((c) => (c.kind !== "state" && c.kind !== "calendar") || (c.entity_id && (c.kind === "calendar" || c.state))),
      modes: draft.trigger === "mode_changes" ? [...(draft.modes || [])] : [],
      more_triggers: draft.trigger === "manual" ? [] : (draft.more_triggers || []).map((t) => ({
        trigger: t.trigger, time: t.time || "07:00:00", offset_minutes: Number(t.offset_minutes) || 0,
        people: [...(t.people || [])], zone: t.zone || "zone.home", modes: [...(t.modes || [])],
        value_entity: t.value_entity || null, value_threshold: t.value_threshold === "" || t.value_threshold == null ? null : Number(t.value_threshold),
        watch_entity: t.watch_entity || null, watch_states: splitStates(t.watch_states || ""), hold_minutes: Number(t.hold_minutes) || 0,
        watch_item: String(t.watch_item || "").trim(),
      })),
      conditions_match: draft.conditions_match === "any" ? "any" : "all",
      wait: ["day", "until", "minutes", "no"].includes(draft.wait) ? draft.wait : "day",
      wait_minutes: Math.max(1, Math.min(240, Number(draft.wait_minutes) || 5)),
      cooldown_minutes: Math.max(0, Math.min(1440, Number(draft.cooldown_minutes) || 0)),
      integration_order: Array.isArray(draft.integration_order) ? [...draft.integration_order] : null,
      wait_until: `${draft.wait_until || "12:00"}:00`.slice(0, 8),
      repeat: draft.repeat,
      catch_up_minutes: Number(draft.catch_up_minutes) || 0,
      retries: Number(draft.retries) || 0,
      on_step_error: draft.on_step_error === "continue" ? "continue" : "stop",
      from_date: draft.from_date || null,
      until_date: draft.until_date || null,
      heads_up_minutes: Number(draft.heads_up_minutes) || 0,
      retry_later_minutes: Number(draft.retry_later_minutes) || 0,
      skip_noop: draft.skip_noop,
      notify_on_run: draft.notify_on_run,
      notify_on_skip: draft.notify_on_skip,
      notify_on_error: draft.notify_on_error,
      notify_on_health: Boolean(draft.notify_on_health),
      wait_when_unavailable: Boolean(draft.wait_when_unavailable),
      ask_first: Boolean(draft.ask_first),
      ask_minutes: Math.max(1, Math.min(60, Number(draft.ask_minutes) || 10)),
      ask_default: draft.ask_default === "skip" ? "skip" : "run",
      forecast_hours: Math.max(1, Math.min(48, Number(draft.forecast_hours) || 3)),
      actions: grouped,
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
          const template = TEMPLATES.find((t) => t.id === el.dataset.tpl) || LIBRARY.find((t) => t.id === el.dataset.tpl)
            || this._suggestions().find((t) => t.id === el.dataset.tpl) || this._habitIdeas().find((t) => t.id === el.dataset.tpl);
          this._draft = this._applyTemplate(template, template?.room ?? room);
          this._editorSession = null;
          this._simpleOpen = null;
          this._view = { name: "editor", room };
          this._preview = null;
          this._notice = null;
          break;
        }
        case "edit": {
          const routine = this._data.routines.find((r) => r.id === el.dataset.id);
          if (!routine) return;
          this._draft = this._draftFromRoutine(routine);
          this._editorSession = null;
          this._simpleOpen = null;
          if (STATE_TRIGGERS.includes(routine.trigger)) this._loadStateHints(routine.watch_entity);
          this._view = { name: "editor", room: routine.room || null };
          this._preview = null;
          this._notice = null;
          break;
        }
        case "run": {
          const id = el.dataset.id;
          const name = this._data.routines.find((r) => r.id === id)?.name || "The routine";
          el.disabled = true;
          const result = await this._call({ type: `${DOMAIN}/routine/run`, routine_id: id });
          if (result.started) {
            this._data.statuses[id] = { ...(this._data.statuses[id] || {}), state: "running", progress: null };
            this._showNotice(`Started “${name}”.`);
            this._tick();
          } else if (result.reason === "already_running") {
            this._showNotice(`“${name}” is already running.`);
          } else {
            const why = result.reason === "disabled" ? "it's turned off" : (REASON_LABELS[result.reason] || "its conditions are not met right now");
            this._showNotice(`“${name}” didn't run: ${why}.`, true);
          }
          break;
        }
        case "order-move": {
          const by = Number(el.dataset.by);
          const routineScope = el.dataset.scope === "routine";
          const order = this._fullOrder(routineScope ? this._draft.integration_order : this._data.settings?.integration_order);
          const i = order.indexOf(el.dataset.domain);
          const j = i + by;
          if (i < 0 || j < 0 || j >= order.length) break;
          [order[i], order[j]] = [order[j], order[i]];
          if (routineScope) {
            this._draft.integration_order = order;
            this._render();
          } else {
            await this._saveSettings({ integration_order: order });
          }
          break;
        }
        case "try-again": {
          const result = await this._call({ type: `${DOMAIN}/routine/try_again`, routine_id: el.dataset.id });
          this._showNotice(result.done ? "Tried again: done." : "Tried again, but it still didn't work. See the routine's recent runs.", !result.done);
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
        case "skip-next":
          await this._call({ type: `${DOMAIN}/routine/skip_next`, routine_id: el.dataset.id, skip: el.dataset.skip === "1" });
          this._showNotice(el.dataset.skip === "1" ? "The next run will be skipped." : "The next run will go ahead.");
          await this._load();
          if (this._view.name === "today") await this._loadToday(true);
          return;
        case "resume":
          await this._call({ type: `${DOMAIN}/routines/pause_until`, routine_ids: [el.dataset.id], until: null });
          await this._load();
          return;
        case "away-pause": {
          const until = this._awayUntil;
          if (!until) throw new Error("Pick the last day to skip.");
          const ids = [...(this._awayPicks || new Set((this._data.routines || []).filter((r) => r.kind !== "todo" && !r.companion_of).map((r) => r.id)))];
          if (!ids.length) throw new Error("Pick at least one routine.");
          await this._call({ type: `${DOMAIN}/routines/pause_until`, routine_ids: ids, until });
          this._showNotice(`Paused ${ids.length} routine${ids.length === 1 ? "" : "s"} until ${formatDay(until)}.`);
          await this._load();
          return;
        }
        case "away-resume": {
          const ids = (this._data.routines || []).filter((r) => this._pausedDay(r)).map((r) => r.id);
          await this._call({ type: `${DOMAIN}/routines/pause_until`, routine_ids: ids, until: null });
          this._showNotice("Routines resumed.");
          await this._load();
          return;
        }
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
          if (tab === "settings") { this._access = null; this._accessFailed = false; }
          this._view = { name: tab === "routines" ? "all" : ["todos", "settings", "activity", "today"].includes(tab) ? tab : "rooms" };
          this._draft = null;
          this._preview = null;
          this._notice = null;
          if (tab === "activity") {
            this._render();
            await this._loadActivity();
            return;
          }
          if (tab === "today") {
            this._render();
            await this._loadToday();
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
          this._draft.actions.push({ _open: true, kind: "service", service: "", targetMode: this._draft.room ? "area" : "entities", area: this._draft.room || "", entities: [], data: "" });
          break;
        case "add-delay":
          this._draft.actions.push({ _open: true, kind: "delay", seconds: 5, service: "", targetMode: "none", area: "", entities: [], data: "" });
          break;
        case "tick":
          try {
            await this._call({ type: `${DOMAIN}/routine/tick`, routine_id: el.dataset.id, item: el.dataset.item, done: el.dataset.done === "1" });
          } catch (err) {
            this._showNotice(err.message || String(err), true);
          }
          return;
        case "history": {
          const id = el.dataset.id;
          this._history = this._history || {};
          if (this._history[id]) {
            delete this._history[id];
            break;
          }
          try {
            this._history[id] = await this._call({ type: `${DOMAIN}/routine/history`, routine_id: id, days: 30 });
          } catch (err) {
            this._showNotice(err.message || String(err), true);
          }
          break;
        }
        case "tomorrow":
          if (this._tomorrow) {
            this._tomorrow = null;
            break;
          }
          try {
            this._tomorrow = await this._call({ type: `${DOMAIN}/plan/tomorrow` });
          } catch (err) {
            this._showNotice(err.message || String(err), true);
          }
          break;
        case "quiet-save": {
          const root = this.shadowRoot;
          await this._saveSettings({ quiet_hours: {
            enabled: root.querySelector("[data-quiet=enabled]").checked,
            from: root.querySelector("[data-quiet=from]").value,
            until: root.querySelector("[data-quiet=until]").value,
          } });
          await this._load(true);
          return;
        }
        case "wifi-save": {
          const root = this.shadowRoot;
          await this._saveSettings({ guest_wifi: { ssid: root.querySelector("[data-wifi=ssid]").value, password: root.querySelector("[data-wifi=password]").value } });
          await this._load(true);
          return;
        }
        case "why": {
          const id = el.dataset.id;
          this._why = this._why || {};
          if (this._why[id]) {
            delete this._why[id];
            break;
          }
          try {
            this._why[id] = await this._call({ type: `${DOMAIN}/routine/why`, routine_id: id });
          } catch (err) {
            this._showNotice(err.message || String(err), true);
          }
          break;
        }
        case "hold-add":
          this._holdRows = [...this._holdRowsNow(), { calendar: this._calendars()[0]?.entity_id || "", words: "" }];
          break;
        case "hold-remove":
          this._holdRows = this._holdRowsNow().filter((_r, i) => i !== Number(el.dataset.i));
          break;
        case "holds-save": {
          const rows = this._holdRowsNow();
          this._holdRows = rows;
          if (await this._saveSettings({ holds: rows.map((r) => ({ calendar: r.calendar, words: splitStates(r.words) })) })) {
            this._holdRows = null;
            await this._load(true);
          }
          return;
        }
        case "undo-recent":
          try {
            const result = await this._call({ type: `${DOMAIN}/undo/recent`, minutes: 60 });
            this._showNotice(result.undone.length ? `Put back: ${result.undone.join(", ")}.` : "Nothing to put back.");
            await this._load(true);
          } catch (err) {
            this._showNotice(err.message || String(err), true);
          }
          return;
        case "group-share":
          this._groupShare = this._groupShare === el.dataset.group ? null : el.dataset.group;
          break;
        case "weekly-send":
          try {
            const result = await this._call({ type: `${DOMAIN}/weekly/send` });
            this._showNotice(result.text);
          } catch (err) {
            this._showNotice(err.message || String(err), true);
          }
          return;
        case "weekly-save": {
          const root = this.shadowRoot;
          await this._saveSettings({
            weekly_report: {
              enabled: Boolean(root.querySelector("[data-weekly='enabled']")?.checked),
              weekday: Number(root.querySelector("[data-weekly='weekday']")?.value ?? 6),
              time: root.querySelector("[data-weekly='time']")?.value || "18:00",
            },
          });
          return;
        }
        case "group-pause":
        case "group-resume": {
          const day = new Date();
          day.setDate(day.getDate() + Number(el.dataset.days || 0));
          const until = act === "group-pause" ? `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}` : null;
          try {
            await this._call({ type: `${DOMAIN}/group/pause`, group: el.dataset.group, until });
            this._showNotice(until ? `${el.dataset.group} is paused through ${formatDay(until)}.` : `${el.dataset.group} runs again.`);
            await this._load();
          } catch (err) {
            this._showNotice(err.message || String(err), true);
          }
          return;
        }
        case "idea-dismiss":
          try {
            await this._call({ type: `${DOMAIN}/idea/dismiss`, idea_id: el.dataset.idea });
            this._data.habits = (this._data.habits || []).filter((h) => h.id !== el.dataset.idea);
          } catch (err) {
            this._showNotice(err.message || String(err), true);
          }
          break;
        case "day-type-add":
          this._dayTypeRows = [...this._dayTypeRowsNow(), { name: "", calendar: this._calendars()[0]?.entity_id || "", words: "" }];
          break;
        case "day-type-remove":
          this._dayTypeRows = this._dayTypeRowsNow().filter((_r, i) => i !== Number(el.dataset.i));
          break;
        case "day-types-save":
          try {
            const rows = this._dayTypeRowsNow();
            this._dayTypeRows = rows;
            const saved = await this._saveSettings({ day_types: rows.filter((r) => r.name.trim() || r.words.trim()).map((r) => ({ name: r.name.trim(), calendar: r.calendar, words: splitStates(r.words), person: r.person || null })) });
            if (saved) {
              this._dayTypeRows = null;
              await this._load(true);
            }
          } catch (err) {
            this._showNotice(err.message || String(err), true);
          }
          return;
        case "add-kind": {
          const blank = { _open: true, service: "", targetMode: "none", area: "", entities: [], data: "" };
          const kind = el.dataset.kind;
          if (kind === "wait_until") this._draft.actions.push({ ...blank, kind, wEntity: "", wStates: "", wMinutes: 10, wTimeout: "continue" });
          else if (kind === "routine") this._draft.actions.push({ ...blank, kind, routine: "" });
          else if (kind === "scene") this._draft.actions.push({ ...blank, kind, entities: [], saved: {}, transition: "" });
          else if (kind === "briefing") this._draft.actions.push({ ...blank, kind, bVoice: (this._data.all_entities || []).find((e) => e.entity_id.startsWith("tts."))?.entity_id || "", bSpeaker: "", bWeather: this._data.weathers?.[0]?.entity_id || "", entities: [], bDay: true });
          else if (kind === "cheaper") this._draft.actions.push({ ...blank, kind, cEntity: "", cMinutes: 180 });
          else if (kind === "battery") this._draft.actions.push({ ...blank, kind, bBelow: 20 });
          else if (kind === "ticks") this._draft.actions.push({ ...blank, kind, tItems: "", tMinutes: 30, tTimeout: "continue" });
          else if (kind === "check_open") this._draft.actions.push({ ...blank, kind, entities: this._openings().map((e) => e.entity_id), cStop: false });
          else if (kind === "ramp") this._draft.actions.push({ ...blank, kind, entities: [], rFrom: 5, rTo: 30, rMinutes: 10 });
          else this._draft.actions.push({ ...blank, kind: "mode", mode: "night" });
          break;
        }
        case "clear-field": {
          const step = this._draft.actions[Number(el.dataset.i)];
          if (step?.fields) delete step.fields[el.dataset.key];
          break;
        }
        case "answer": {
          const run = el.dataset.run === "1";
          await this._call({ type: `${DOMAIN}/routine/answer`, routine_id: el.dataset.id, run });
          this._showNotice(run ? "Running it now." : "Skipped this time.");
          return;
        }
        case "scene-capture": {
          const step = this._draft.actions[Number(el.dataset.i)];
          if (!step.entities.length) throw new Error("Pick the devices first.");
          step.saved = {};
          for (const id of step.entities) {
            const state = this._hass?.states?.[id];
            if (state) step.saved[id] = captureLook(state);
          }
          break;
        }
        case "share-open":
          this._shareFor = this._shareFor === el.dataset.id ? null : el.dataset.id;
          break;
        case "share-copy": {
          const code = this.shadowRoot.querySelector("[data-share-code]")?.value || "";
          try {
            await navigator.clipboard.writeText(code);
            this._showNotice("Copied. Paste it into Daily Routine's Import on another home.");
          } catch (_err) {
            this.shadowRoot.querySelector("[data-share-code]")?.select();
          }
          return;
        }
        case "import-routine": {
          const code = this.shadowRoot.querySelector("[data-import-code]")?.value || "";
          if (code.trim().startsWith("DR2:")) {
            const { routines, missing } = this._importGroup(code);
            for (const raw of routines) await this._call({ type: `${DOMAIN}/routine/save`, routine: raw });
            this._showNotice(`Imported ${routines.length} routines${missing.length ? `. Not in this home, so check these: ${missing.join(", ")}` : ""}.`);
            this._view = { name: "all" };
            await this._load();
            return;
          }
          const { draft, missing } = this._importRoutine(code);
          this._draft = draft;
          this._editorSession = null;
          this._simpleOpen = null;
          this._view = { name: "editor", room: draft.room || null };
          this._preview = null;
          this._notice = missing.length ? `Imported. Not in this home, so check these steps: ${missing.join(", ")}.` : "Imported. Check it, then Save.";
          break;
        }
        case "vacation-start": {
          const until = this._awayUntil;
          if (!until) throw new Error("Pick the last day you're away.");
          const ids = [...(this._awayPicks || new Set((this._data.routines || []).filter((r) => r.kind !== "todo" && !r.companion_of).map((r) => r.id)))];
          const lived = this.shadowRoot.querySelector("[data-vacation-lived]")?.checked !== false;
          const summary = this.shadowRoot.querySelector("[data-vacation-summary]")?.checked !== false;
          await this._call({ type: `${DOMAIN}/vacation/start`, until, routine_ids: ids, lived_in: lived, summary });
          this._showNotice(`Enjoy your trip! Vacation until ${formatDay(until)}.`);
          await this._load();
          return;
        }
        case "vacation-end":
          await this._call({ type: `${DOMAIN}/vacation/end` });
          this._showNotice("Welcome home. Everything is back how it was.");
          await this._load();
          return;
        case "summary-send": {
          const res = await this._call({ type: `${DOMAIN}/summary/send` });
          this._showNotice(`Sent: ${res.text}`);
          return;
        }
        case "copy-open":
          this._copyFor = el.dataset.id;
          break;
        case "copy-cancel":
          this._copyFor = null;
          break;
        case "copy-do": {
          const rooms = [...this.shadowRoot.querySelectorAll("[data-copy-room]")].filter((b) => b.checked).map((b) => b.dataset.copyRoom);
          if (!rooms.length) {
            this._showNotice("Pick at least one room.", true);
            return;
          }
          const result = await this._call({ type: `${DOMAIN}/routine/copy`, routine_id: el.dataset.id, rooms });
          this._copyFor = null;
          this._showNotice(`Copied to ${result.routines.length} room${result.routines.length === 1 ? "" : "s"}.`);
          await this._load(true);
          return;
        }
        case "editor-default":
          return;
        case "open-section":
          this._simpleOpen?.add(el.dataset.key);
          break;
        case "close-section":
          this._simpleOpen?.delete(el.dataset.key);
          break;
        case "switch-editor":
          // Only the view changes; the routine being edited stays as it is.
          this._editorSession = el.dataset.mode;
          this._simpleOpen = null;
          break;
        case "toggle-step": {
          const step = this._draft.actions[Number(el.dataset.i)];
          if (step) step._open = !step._open;
          break;
        }
        case "add-more-trigger":
          this._draft.more_triggers = [...(this._draft.more_triggers || []), { trigger: "time", time: "22:00:00", offset_minutes: 0, people: [], zone: "zone.home", modes: [], value_entity: "", value_threshold: "", watch_entity: "", watch_states: "", hold_minutes: 0 }];
          break;
        case "remove-more-trigger":
          this._draft.more_triggers.splice(Number(el.dataset.i), 1);
          break;
        case "undo": {
          const id = el.dataset.id;
          const name = this._data.routines.find((r) => r.id === id)?.name || "the routine";
          el.disabled = true;
          await this._call({ type: `${DOMAIN}/routine/undo`, routine_id: id });
          this._showNotice(`Put things back the way they were before “${name}”.`);
          break;
        }
        case "add-todo":
          this._draft.actions.push({ _open: true, kind: "todo", todoOp: "add", todoList: "", todoItem: "", service: "", targetMode: "none", area: "", entities: [], data: "" });
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
        case "skip-date-add": {
          const day = this.shadowRoot.querySelector("[data-skip-date]")?.value;
          if (!day) return;
          this._draft.skip_dates = [...new Set([...(this._draft.skip_dates || []), day])].sort();
          break;
        }
        case "skip-date-remove":
          this._draft.skip_dates = (this._draft.skip_dates || []).filter((x) => x !== el.dataset.day);
          break;
        case "skip-date-week":
          this._draft.skip_dates = [...new Set([...(this._draft.skip_dates || []), ...(this._draft.skip_dates || []).map((x) => addDays(x, 7))])].sort().slice(0, 31);
          break;
        case "add-condition": {
          const kind = el.dataset.kind || "state";
          const zone = this._draft.zone || "zone.home";
          if (kind === "presence") this._draft.conditions.push({ kind, who: "everyone", where: "away", people: [], zone });
          else if (kind === "sensors") this._draft.conditions.push({ kind, mode: "quiet", minutes: 10, sensors: [] });
          else if (kind === "calendar") this._draft.conditions.push({ kind, entity_id: this._calendars()[0]?.entity_id || "", when: "no_event", match: "" });
          else if (kind === "mode") this._draft.conditions.push({ kind, modes: ["home"] });
          else if (kind === "workday") this._draft.conditions.push({ kind, entity_id: this._data.workday_sensors?.[0]?.entity_id || "", when: "workday" });
          else if (kind === "charging") this._draft.conditions.push({ kind, entities: [] });
          else if (kind === "value") this._draft.conditions.push({ kind, entity_id: "", below: "", above: "" });
          else if (kind === "weather") this._draft.conditions.push({ kind, entity_id: this._data.weathers?.[0]?.entity_id || "", test: "is", conditions: ["rainy", "pouring"], value: "" });
          else if (kind === "day_type") this._draft.conditions.push({ kind, types: [NORMAL_DAY] });
          else if (kind === "forecast") this._draft.conditions.push({ kind, entity_id: this._data.weathers?.[0]?.entity_id || "", conditions: ["rainy", "pouring"], below: "", hours: 6, when: "not_coming" });
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
    if (el.dataset.pickerSearch !== undefined) {
      this._searchText[el.dataset.pickerSearch] = el.value;
      const target = this._pickers?.get(el);
      if (target) filterPicker(target, el.value);
      return;
    }
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
    if (!live && el.dataset.reportSetting !== undefined) {
      this._saveSettings({ [el.dataset.reportSetting]: el.checked });
      return;
    }
    if (!live && el.dataset.setting !== undefined) {
      if (el.value !== "") this._saveSettings({ [el.dataset.setting]: Number(el.value) });
      return;
    }
    if (!live && el.dataset.accessUser !== undefined) {
      this._saveAccess();
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
    if (el.dataset.awayUntil !== undefined) {
      this._awayUntil = el.value;
      this._awayOpen = true;
      return;
    }
    if (el.dataset.away !== undefined) {
      const all = (this._data.routines || []).filter((r) => r.kind !== "todo" && !r.companion_of).map((r) => r.id);
      this._awayPicks = this._awayPicks || new Set(all);
      el.checked ? this._awayPicks.add(el.dataset.away) : this._awayPicks.delete(el.dataset.away);
      this._awayOpen = true;
      return;
    }
    if (!live && el.dataset.modePick !== undefined) {
      this._setMode(el.value);
      return;
    }
    if (!live && el.dataset.summary !== undefined) {
      const current = this._data.settings?.daily_summary || { enabled: false, time: "21:00", only_away: false };
      const value = el.type === "checkbox" ? el.checked : el.value;
      this._saveSettings({ daily_summary: { ...current, [el.dataset.summary]: value } });
      return;
    }
    if (!live && el.dataset.editorDefault !== undefined) {
      this._saveSettings({ editor: el.value });
      return;
    }
    if (!live && el.dataset.editorOwn !== undefined) {
      try {
        if (el.value) localStorage.setItem("daily-routine-editor", el.value);
        else localStorage.removeItem("daily-routine-editor");
      } catch (_err) { /* private browsing */ }
      this._showNotice("Saved for this device.");
      return;
    }
    if (el.dataset.lived !== undefined) {
      if (!live) this._saveLivedIn();
      return;
    }
    if (!this._draft) return;
    const value = el.type === "checkbox" ? el.checked : el.value;
    const d = this._draft;
    if (el.dataset.xi !== undefined) {
      const t = d.more_triggers[Number(el.dataset.xi)];
      if (!t) return;
      if (el.dataset.xpeople !== undefined) toggleIn(t.people, el.dataset.xpeople, el.checked);
      else if (el.dataset.xmode !== undefined) toggleIn(t.modes, el.dataset.xmode, el.checked);
      else if (el.dataset.xf === "time") t.time = value.length === 5 ? `${value}:00` : value;
      else t[el.dataset.xf] = value;
      if (el.dataset.rerender !== undefined && !live) this._render();
      return;
    }
    if (el.dataset.oi !== undefined) {
      const step = d.actions[Number(el.dataset.oi)];
      if (!step) return;
      if (el.dataset.cf === "kind") {
        step.onlyIf = value ? this._newOnlyIf(value) : null;
        if (!live) this._render();
        return;
      }
      const cond = step.onlyIf;
      if (!cond) return;
      if (el.dataset.cmode !== undefined) toggleIn(cond.modes, el.dataset.cmode, el.checked);
      else if (el.dataset.cweather !== undefined) toggleIn(cond.conditions, el.dataset.cweather, el.checked);
      else if (el.dataset.cdaytype !== undefined) toggleIn((cond.types ||= []), el.dataset.cdaytype, el.checked);
      else cond[el.dataset.cf] = value;
      if (el.dataset.rerender !== undefined && !live) this._render();
      return;
    }

    if (el.dataset.skipmode !== undefined) {
      d.skip_modes = d.skip_modes || [];
      toggleIn(d.skip_modes, el.dataset.skipmode, el.checked);
      return;
    }
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
      if (field === "trigger" && d.isNew && d.wait === defaultWait(d.trigger)) d.wait = defaultWait(value);
      if (field === "room") {
        // Steps aimed at the old room follow the routine to its new room.
        const old = d.room || "";
        for (const step of d.actions) {
          if (step.targetMode === "area" && (step.area === old || !step.area)) {
            if (value) step.area = value;
            else step.targetMode = "all_areas";
          }
        }
        d.room = value || null;
        if (!live) this._render();
        return;
      }
      if (field === "ask_first") d.ask_first = Boolean(value);
      else if (field === "time") d.time = value.length === 5 ? `${value}:00` : value;
      else if (field === "from_date" || field === "until_date") d[field] = monthDay(value);
      else if (field === "ownOrder") d.integration_order = value ? this._fullOrder(this._data.settings?.integration_order) : null;
      else if (field === "enabled" || field === "skip_next" || field === "repeat" || field === "skip_noop" || field === "notify_on_run" || field === "notify_on_skip" || field === "notify_on_error" || field === "notify_on_health" || field === "wait_when_unavailable") d[field] = Boolean(value);
      else d[field] = value;
      if (el.dataset.rerender !== undefined && !live) this._render();
      return;
    }
    if (el.dataset.sfield !== undefined) {
      const step = d.actions[Number(el.dataset.sfi)];
      if (!step) return;
      step.fields = { ...(step.fields || {}), [el.dataset.sfield]: value };
      return;
    }
    if (el.dataset.si !== undefined) {
      const step = d.actions[Number(el.dataset.si)];
      if (!step) return;
      if (el.dataset.sf === "withPrev") {
        step.withPrev = Boolean(value);
        if (!live) this._render();
        return;
      }
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
      if (el.dataset.cmode !== undefined) {
        cond.modes = cond.modes || [];
        toggleIn(cond.modes, el.dataset.cmode, el.checked);
        return;
      }
      if (el.dataset.cdaytype !== undefined) {
        cond.types = cond.types || [];
        toggleIn(cond.types, el.dataset.cdaytype, el.checked);
        return;
      }
      if (el.dataset.ccharge !== undefined) {
        cond.entities = cond.entities || [];
        toggleIn(cond.entities, el.dataset.ccharge, el.checked);
        return;
      }
      if (el.dataset.cweather !== undefined) {
        cond.conditions = cond.conditions || [];
        toggleIn(cond.conditions, el.dataset.cweather, el.checked);
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

  _newOnlyIf(kind) {
    if (kind === "mode") return { kind, modes: ["home"] };
    if (kind === "workday") return { kind, entity_id: this._data.workday_sensors?.[0]?.entity_id || "", when: "workday" };
    if (kind === "day_type") return { kind, types: [NORMAL_DAY] };
    if (kind === "forecast") return { kind, entity_id: this._data.weathers?.[0]?.entity_id || "", conditions: ["rainy", "pouring"], below: "", hours: 6, when: "not_coming" };
    if (kind === "weather") return { kind, entity_id: this._data.weathers?.[0]?.entity_id || "", test: "is_not", conditions: ["rainy", "pouring"], value: "" };
    return { kind: "state", entity_id: "", state: "" };
  }

  async _setMode(mode) {
    try {
      const result = await this._call({ type: `${DOMAIN}/mode/set`, mode });
      this._data.mode = result.mode;
      this._showNotice(`The house is in ${modeName(result.mode)} mode.`);
    } catch (err) {
      this._showNotice(err.message || String(err), true);
    }
    this._render();
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
    if (this._me?.allowed === false) {
      this.shadowRoot.innerHTML = `${STYLES}<main><section class="card">
        <h2>You don't have access to Daily Routine</h2>
        <p class="muted">Ask an admin to give you access. They can do it in Daily Routine, under Settings.</p>
      </section></main>`;
      return;
    }
    let body;
    if (!this._data) body = `<div class="skeleton-grid" aria-busy="true" aria-label="Loading">${'<div class="skeleton-card"></div>'.repeat(4)}</div>`;
    else if (this._view.name === "room") body = this._renderRoom();
    else if (this._view.name === "all") body = this._renderAll();
    else if (this._view.name === "settings") body = this._renderSettings();
    else if (this._view.name === "activity") body = this._renderActivity();
    else if (this._view.name === "today") body = this._renderToday();
    else if (this._view.name === "templates") body = this._renderTemplates();
    else if (this._view.name === "editor") body = this._renderEditor();
    else if (this._view.name === "todos") body = this._renderTodos();
    else if (this._view.name === "todo-editor") body = this._renderTodoEditor();
    else body = this._renderRooms();

    // The editor shows its own notice next to its buttons.
    const editing = this._view.name === "editor";
    const notice = !editing && this._notice ? `<div class="notice ${this._error ? "error" : ""}">${esc(this._notice)}</div>` : "";
    const banner = this._data?.paused && !editing ? this._pausedBanner() : "";
    const running = this._data && ["rooms", "activity", "todos"].includes(this._view.name) ? this._runningStrip() : "";
    this.shadowRoot.innerHTML = `${STYLES}<main>${this._renderHeader()}${banner}${notice}${running}${body}</main>`;
    this._labelIntegrations();
    this._addSearches();
  }

  // Every device list shows which integration each device comes from.
  _labelIntegrations() {
    const root = this.shadowRoot;
    const map = this._data?.integrations;
    if (!root?.querySelectorAll || !map) return;
    for (const option of root.querySelectorAll("select option")) {
      const integration = map[option.value];
      if (integration && !option.dataset.integ) {
        option.dataset.integ = "1";
        option.textContent = withIntegration(option.textContent, integration);
      }
    }
    for (const label of root.querySelectorAll(".checks label")) {
      const input = label.querySelector("input");
      if (!input || label.querySelector(".integ")) continue;
      const id = [...Object.values(input.dataset), input.value].find((v) => map[v]);
      if (!id) continue;
      const tag = document.createElement("span");
      tag.className = "integ muted small";
      tag.textContent = ` (${map[id]})`;
      label.append(tag);
    }
  }

  // A search box above every long list of devices to pick from (checkboxes or a
  // drop-down), so the right one is quick to find. What's typed survives redraws.
  _addSearches() {
    const root = this.shadowRoot;
    if (!root?.querySelectorAll || typeof document === "undefined") return;
    this._searchText = this._searchText || {};
    this._pickers = new WeakMap();
    let n = 0;
    for (const el of root.querySelectorAll(".checks, select")) {
      if (!isDevicePicker(el)) continue;
      const key = `${this._view.name}:${n++}`;
      const input = document.createElement("input");
      input.type = "search";
      input.className = "picker-search";
      input.placeholder = "Search…";
      input.setAttribute("aria-label", "Search");
      input.dataset.pickerSearch = key;
      el.before(input);
      this._pickers.set(input, el);
      if (this._searchText[key]) {
        input.value = this._searchText[key];
        filterPicker(el, input.value);
      }
    }
  }

  _tabs() {
    const tabs = [
      { id: "rooms", label: "Rooms" },
      { id: "today", label: "Today" },
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
    if (this._view.name === "today") return "today";
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
        ${this._data ? this._modePicker() : ""}
        ${this._data ? `<button class="${paused ? "warn" : "ghost"}" data-act="pause">${paused ? "Resume routines" : "Pause all"}</button>` : ""}
        <button class="ghost" data-act="refresh" title="Refresh">⟳</button>
      </div>
    </header>`;
  }

  _modePicker() {
    const mode = this._data.mode || "home";
    if (!this._hass?.user?.is_admin) return `<span class="pill" title="House mode">${esc(modeName(mode))}</span>`;
    return `<label class="mode-pick"><span class="visually-hidden">House mode</span><select data-mode-pick aria-label="House mode">${MODES.map(([id, label]) => `<option value="${id}" ${id === mode ? "selected" : ""}>${esc(label)}</option>`).join("")}</select></label>`;
  }

  _titleFor() {
    if (this._view.name === "all") return "Routines";
    if (this._view.name === "todos") return "To-dos";
    if (this._view.name === "todo-editor") return this._todoDraft?.isNew ? "New to-do rule" : "Edit to-do rule";
    if (this._view.name === "settings") return "Settings";
    if (this._view.name === "activity") return "Activity";
    if (this._view.name === "today") return "Today";
    if (this._view.name === "rooms") return "Daily Routine";
    if (this._view.name === "editor") return this._draft?.isNew ? "New routine" : "Edit routine";
    if (this._view.name === "templates") return "New routine";
    return this._view.room ? this._area(this._view.room)?.name || "Room" : "Whole house";
  }

  // Every routine running right now, at the top of the page, so you can see it from anywhere.
  _runningStrip() {
    const running = (this._data.routines || []).filter((r) => this._data.statuses[r.id]?.state === "running");
    if (!running.length) return "";
    const rows = running.map((r) => `<div class="card"><strong>${esc(r.name)}</strong> <span class="muted small">${esc(this._roomName(r.room))}</span>
      ${progressHtml(this._data.statuses[r.id].progress)}</div>`).join("");
    return `<section class="running-strip" aria-live="polite" aria-label="Running now"><h2 class="small muted">Running now</h2>${rows}</section>`;
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
    const busy = routines.some((r) => ["waiting", "error"].includes(this._data.statuses[r.id]?.state));
    const running = routines.filter((r) => this._data.statuses[r.id]?.state === "running").length;
    const badge = running
      ? `<span class="pill run">${running} running</span>`
      : (busy ? `<span class="pill warn">Needs attention</span>` : "");
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
    const waitLine = status.waiting
      ? `<div class="small skip-line"><span class="pill warn">Runs as soon as its conditions are met${status.wait_ends ? `, until ${esc(new Date(status.wait_ends).getHours() === 0 && new Date(status.wait_ends).getMinutes() === 0 ? "midnight" : new Date(status.wait_ends).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }))}` : ""}</span></div>`
      : "";
    return `<article class="card routine ${routine.enabled ? "" : "disabled"}">
      <div class="routine-head">
        <div>
          <h3>${esc(routine.name)}</h3>
          ${options.showRoom ? `<div class="muted small">${esc(this._roomName(routine.room))}</div>` : ""}
          <div class="muted small">${esc(this._describeTrigger(routine))} · ${routine.days.length === 7 ? "every day" : routine.days.join(", ")}${routine.from_date ? ` · ${esc(describeMonthDay(routine.from_date))} to ${esc(describeMonthDay(routine.until_date))}` : ""}</div>
          <div class="muted small">Next: ${esc(formatTime(status.next_run))}${esc(reason)}${detail}</div>
          ${waitLine}${this._skipHtml(routine)}
          ${state === "running" ? progressHtml(status.progress) : ""}
          ${state === "running" ? tickListHtml(routine.id, status.ticklist) : ""}
          ${powerHtml(status.power)}${reliabilityHtml(status.reliability)}
          <div class="muted small">Linked to: ${esc(this._linkedName(routine.device_id))}</div>
          ${this._historyHtml(status)}
          ${this._healthHtml(routine)}
        </div>
        <span class="pill ${state === "error" ? "error" : state === "waiting" || state === "held" || state === "asking" ? "warn" : state === "running" ? "run" : ""}">${esc(STATE_LABELS[state] || state)}</span>
      </div>
      <div class="row-actions">
        ${state === "asking" ? `<button class="primary" data-act="answer" data-id="${esc(routine.id)}" data-run="1">Run it</button>
        <button class="ghost" data-act="answer" data-id="${esc(routine.id)}" data-run="0">Skip it</button>` : `<button class="ghost" data-act="run" data-id="${esc(routine.id)}" ${state === "running" ? "disabled" : ""}>${state === "running" ? "Running…" : "Run now"}</button>`}
        ${status.can_try_again ? `<button class="ghost" data-act="try-again" data-id="${esc(routine.id)}">Try again</button>` : ""}
        ${status.can_undo ? `<button class="ghost" data-act="undo" data-id="${esc(routine.id)}" title="Put things back the way they were before it last ran">Undo</button>` : ""}
        <button class="ghost" data-act="skip-next" data-id="${esc(routine.id)}" data-skip="${routine.skip_next ? "" : "1"}">${routine.skip_next ? "Don't skip" : "Skip next run"}</button>
        <button class="ghost" data-act="why" data-id="${esc(routine.id)}" title="Why won't it run (or why didn't it)?">Why not?</button>
        <button class="ghost" data-act="history" data-id="${esc(routine.id)}" title="Ran, skipped and failed, day by day">History</button>
        <button class="ghost" data-act="edit" data-id="${esc(routine.id)}">Edit</button>
        <button class="ghost" data-act="duplicate" data-id="${esc(routine.id)}">Duplicate</button>
        ${routine.room ? `<button class="ghost" data-act="copy-open" data-id="${esc(routine.id)}">Copy to rooms</button>` : ""}
        <button class="ghost" data-act="share-open" data-id="${esc(routine.id)}">Share</button>
        <button class="ghost" data-act="toggle-enabled" data-id="${esc(routine.id)}">${routine.enabled ? "Disable" : "Enable"}</button>
        <button class="ghost danger" data-act="delete" data-id="${esc(routine.id)}">Delete</button>
      </div>
      ${this._why?.[routine.id] ? this._whyHtml(routine, this._why[routine.id]) : ""}
      ${this._history?.[routine.id] ? `<div class="copy-panel">${historyChart(this._history[routine.id].days)}
        <div class="row-actions"><button class="ghost" data-act="history" data-id="${esc(routine.id)}">Close</button></div></div>` : ""}
      ${this._copyFor === routine.id ? this._copyPanel(routine) : ""}
      ${this._shareFor === routine.id ? this._sharePanel(routine) : ""}
    </article>`;
  }

  // Why a routine won't run on its own now, or why it was last skipped.
  _whyHtml(routine, why) {
    const words = (code) => REASON_LABELS[String(code).split(":")[0]] || activityReason(code) || String(code).replaceAll("_", " ");
    const now = why.reasons.length
      ? `<p class="small">Right now it wouldn't run on its own, because:</p><ul class="small">${why.reasons.map((r) => `<li>${esc(words(r))}</li>`).join("")}</ul>`
      : `<p class="small">Nothing is stopping it. ${why.next_run ? `It runs next ${esc(formatTime(why.next_run))}.` : "It runs when its trigger happens."}</p>`;
    const missing = (why.unavailable || []).length ? `<p class="small">Waiting for: ${esc(why.unavailable.join(", "))}.</p>` : "";
    const last = why.last_skip
      ? `<p class="muted small">Last skipped ${esc(fmtTime(why.last_skip.at))}: ${esc(words(why.last_skip.reason || "conditions_not_met"))}.</p>` : "";
    return `<div class="copy-panel why">${now}${missing}${last}
      <div class="row-actions"><button class="ghost" data-act="why" data-id="${esc(routine.id)}">Close</button></div></div>`;
  }

  // Share a routine as a code another home can import. Rooms travel by name.
  _sharePanel(routine) {
    return `<div class="copy-panel">
      <p class="small">Send this code to someone with Daily Routine. They paste it under <strong>+ New routine</strong> → <strong>Import a routine</strong>. Rooms are matched by name; devices they don't have are pointed out so they can pick their own.</p>
      <textarea data-share-code readonly rows="4">${esc(shareCode(routine, this._areas()))}</textarea>
      <div class="row-actions"><button class="primary" data-act="share-copy">Copy</button><button class="ghost" data-act="share-open" data-id="${esc(routine.id)}">Close</button></div>
    </div>`;
  }

  // A shared routine as a new draft: its rooms matched by name here.
  _importRoutine(code) {
    let shared;
    try {
      shared = JSON.parse(decodeURIComponent(escape(atob(code.trim().replace(/^DR1:/, "")))));
    } catch (_err) {
      throw new Error("That isn't a Daily Routine code. Copy the whole code and try again.");
    }
    if (!shared?.routine?.actions) throw new Error("That isn't a Daily Routine code. Copy the whole code and try again.");
    const areas = this._areas();
    const byName = (name) => areas.find((a) => a.name.toLowerCase() === String(name || "").toLowerCase())?.id;
    const roomFor = (id) => (areas.some((a) => a.id === id) ? id : byName(shared.areas?.[id]));
    const missing = new Set();
    const known = new Set((this._data.all_entities || []).map((e) => e.entity_id));
    const fix = (action) => {
      if (action.together) return { ...action, together: action.together.map(fix) };
      const target = action.target ? { ...action.target } : null;
      if (target?.area_id) target.area_id = target.area_id.map(roomFor).filter(Boolean);
      for (const id of target?.entity_id || []) if (!known.has(id)) missing.add(id);
      return target ? { ...action, target } : action;
    };
    const routine = { ...shared.routine, room: shared.routine.room ? roomFor(shared.routine.room) || null : null, actions: shared.routine.actions.map(fix) };
    const draft = { ...this._draftFromRoutine(routine), id: "", isNew: true, enabled: true };
    return { draft, missing: [...missing] };
  }

  // A group's code: every routine, rooms matched by name, new ids here.
  _importGroup(code) {
    let shared;
    try {
      shared = JSON.parse(decodeURIComponent(escape(atob(code.trim().replace(/^DR2:/, "")))));
    } catch (_err) {
      throw new Error("That isn't a Daily Routine code. Copy the whole code and try again.");
    }
    if (!Array.isArray(shared?.routines)) throw new Error("That isn't a Daily Routine code. Copy the whole code and try again.");
    const missing = new Set();
    const routines = shared.routines.map((routine) => {
      const one = this._importRoutine(`DR1:${btoa(unescape(encodeURIComponent(JSON.stringify({ v: 1, routine, areas: shared.areas || {} }))))}`);
      one.missing.forEach((m) => missing.add(m));
      // A new id each (the name and a few random letters), like a new routine.
      return this._draftToRaw({ ...one.draft, isNew: true });
    });
    return { routines, missing: [...missing] };
  }

  // Copy a routine to other rooms: steps aimed at its room aim at each new room.
  _copyPanel(routine) {
    const rooms = (this._data.areas || []).filter((a) => a.id !== routine.room);
    return `<div class="copy-panel">
      <p class="small">Copy “${esc(routine.name)}” to these rooms. Steps for ${esc(this._roomName(routine.room))} will be for each new room instead; steps for specific devices stay the same, so check them after.</p>
      <div class="checks">${rooms.map((a) => `<label class="check"><input type="checkbox" data-copy-room="${esc(a.id)}"> ${esc(a.name)}</label>`).join("")}</div>
      <div class="row-actions"><button class="primary" data-act="copy-do" data-id="${esc(routine.id)}">Copy</button><button class="ghost" data-act="copy-cancel">Cancel</button></div>
    </div>`;
  }

  _pausedDay(routine) {
    return routine.paused_until && routine.paused_until >= todayIso() ? routine.paused_until : null;
  }

  _skipHtml(routine) {
    const paused = this._pausedDay(routine);
    const parts = [];
    if (paused) parts.push(`<span class="pill warn">Paused until ${esc(formatDay(paused))}</span> <button class="link" data-act="resume" data-id="${esc(routine.id)}">Resume</button>`);
    if (routine.skip_next) parts.push(`<span class="pill warn">The next run will be skipped</span>`);
    return parts.length ? `<div class="small skip-line">${parts.join(" ")}</div>` : "";
  }

  // Pause many routines while you're away.
  _awayCard() {
    const routines = (this._data?.routines || []).filter((r) => r.kind !== "todo" && !r.companion_of);
    if (!routines.length) return "";
    const paused = routines.filter((r) => this._pausedDay(r));
    const picks = this._awayPicks || new Set(routines.map((r) => r.id));
    const list = routines.map((r) => `<label class="check"><input type="checkbox" data-away="${esc(r.id)}" ${picks.has(r.id) ? "checked" : ""}> ${esc(r.name)}${this._pausedDay(r) ? ` <span class="muted small">(paused until ${esc(formatDay(this._pausedDay(r)))})</span>` : ""}</label>`).join("");
    const vacation = this._data.settings?.vacation;
    if (vacation) {
      return `<section class="card away vacation-on">
        <strong>🏖 On vacation until ${esc(formatDay(vacation.until))}</strong>
        <p class="muted small">The house is in Vacation mode${(vacation.routines || []).length ? `, ${vacation.routines.length} routine${vacation.routines.length === 1 ? " is" : "s are"} paused` : ""}${this._data.settings?.lived_in?.enabled ? ", lived-in lights are on" : ""}${this._data.settings?.daily_summary?.enabled ? ", and a summary comes each evening" : ""}. It all goes back the day after.</p>
        <div class="row-actions"><button class="primary" data-act="vacation-end">Back home</button></div>
      </section>`;
    }
    return `<details class="card away" ${this._awayOpen ? "open" : ""}>
      <summary><strong>Going away?</strong> <span class="muted small">${paused.length ? `${paused.length} routine${paused.length === 1 ? " is" : "s are"} paused` : "Pause routines until a date, or go on vacation"}</span></summary>
      <p class="muted small">Picked routines don't run on their own through the day you choose, then carry on as usual. Run now still works.</p>
      <label>Skip through<input type="date" data-away-until value="${esc(this._awayUntil || "")}" min="${todayIso()}"></label>
      <div class="checks">${list}</div>
      <div class="row-actions">
        <button class="primary" data-act="away-pause">Pause the picked routines</button>
        ${paused.length ? `<button class="ghost" data-act="away-resume">Resume all</button>` : ""}
      </div>
      ${this._me?.is_admin ? `<h4>Or go on vacation</h4>
      <p class="muted small">All in one tap: the house goes to Vacation mode and the picked routines pause through that day. The day after (or when you press Back home) everything goes back how it was.</p>
      <label class="check"><input type="checkbox" data-vacation-lived checked> Turn on lived-in lights${(this._data.settings?.lived_in?.lights || []).length ? "" : " (pick the lights under Settings)"}</label>
      <label class="check"><input type="checkbox" data-vacation-summary checked> Send me a summary of what ran each evening</label>
      <div class="row-actions"><button class="primary" data-act="vacation-start">Start vacation</button></div>` : ""}
    </details>`;
  }

  // The last few runs, newest first. Shown closed so the card stays short.
  _historyHtml(status) {
    const runs = (status.history || []).slice(0, 5);
    if (!runs.length) return "";
    const items = runs.map((run) => {
      const line = `${esc(formatTime(run.at))} · ${esc(this._runText(run))}`;
      return (run.steps || []).length
        ? `<li><details class="replay-run"><summary>${line} <span class="muted">· see each step</span></summary>${replayHtml(run.steps)}</details></li>`
        : `<li>${line}</li>`;
    }).join("");
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
      this._render();
      return false;
    }
    this._render();
    return true;
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
        <span class="pill ${state === "error" ? "error" : state === "waiting" || state === "held" || state === "asking" ? "warn" : state === "running" ? "run" : ""}">${esc(STATE_LABELS[state] || state)}</span>
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
    const energy = energyHtml(this._data.energy);

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
          .map((e) => `<li><time class="muted small">${esc(fmtTime(e.at))}</time> <strong>${esc(e.name)}</strong> <span class="pill ${e.outcome === "failed" ? "error" : e.outcome === "skipped" ? "warn" : ""}">${ACTIVITY_OUTCOMES[e.outcome] || esc(e.outcome)}</span>${e.reason ? `<div class="muted small">${esc(activityReason(e.reason))}</div>` : ""}${e.outcome === "ran" && e.detail ? `<div class="muted small">${esc(e.detail)}</div>` : ""}${(e.steps || []).length ? `<details class="replay-run"><summary class="small">See each step</summary>${replayHtml(e.steps)}</details>` : ""}</li>`)
          .join("")}</ul>`
      : `<p class="muted small">Nothing in this period matches that filter.</p>`;

    return `${windows}
      ${tiles}
      ${attentionCard}${energy}
      <section class="card"><h3>Runs per day</h3>${days}</section>
      <section class="card"><h3>Why runs failed</h3>${reasonList(a.failure_reasons, "No failures in this period.")}</section>
      <section class="card"><h3>Why runs were skipped</h3><p class="muted small">Triggered, but the conditions were not met, so nothing ran.</p>${reasonList(a.skip_reasons, "Nothing was skipped in this period.")}</section>
      <section class="card"><h3>By routine</h3>${byRoutine}</section>
      <section class="card"><h3>Devices that gave trouble</h3>
        <p class="muted small">Devices a step couldn't change, or had to send the command to twice, in this period. One that keeps showing up may need a closer look: its batteries, its signal, or its integration.</p>
        ${(a.devices || []).length ? `<div class="table-wrap"><table><thead><tr><th>Device</th><th>Couldn't change</th><th>Needed a second try</th><th>Worked</th><th>Last problem</th></tr></thead><tbody>${a.devices
          .map((dv) => `<tr><td>${esc(dv.name)}</td><td>${dv.failed}</td><td>${dv.retried}</td><td>${dv.success_rate === null ? "—" : `${dv.success_rate}%`}</td><td>${dv.last_problem ? `${esc(fmtTime(dv.last_problem.at))}<br><span class="muted small">${esc(dv.last_problem.reason || "")}</span>` : "—"}</td></tr>`)
          .join("")}</tbody></table></div>` : `<p class="muted small">No device gave trouble in this period.</p>`}
      </section>
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
      ${this._editorCard()}
      ${this._summaryCard()}
      ${this._livedInCard()}
      ${this._dayTypesCard()}
      ${this._weeklyCard()}
      ${this._holdsCard()}
      ${this._quietCard()}
      ${this._reportsCard()}
      ${this._guestWifiCard()}
      ${this._renderAccess()}
      ${this._renderIntegrationOrder()}
      <section class="card">
        <h3>Timing</h3>
        <p class="muted small">How patiently Daily Routine waits on slow devices and cloud lists, such as an iCloud to-do list after a restart. Changes are saved when you leave a box.</p>
        <div class="timing">${timing}</div>
      </section>`;
  }

  // Integrations whose devices are also in another one (a curtain through Matter,
  // Bluetooth and the cloud), loaded once when they're first shown.
  _integrationList() {
    if (this._integrations === undefined && !this._integrationsLoading) {
      this._integrationsLoading = true;
      this._call({ type: `${DOMAIN}/integrations` })
        .then((res) => {
          this._integrations = res?.integrations || [];
        })
        .catch(() => {
          this._integrations = [];
        })
        .finally(() => {
          this._integrationsLoading = false;
          if (["settings", "editor"].includes(this._view.name)) this._render();
        });
    }
    return this._integrations;
  }

  // The full order: the saved one first, then the rest, local before cloud.
  _fullOrder(saved) {
    const known = this._integrationList() || [];
    const domains = known.map((i) => i.domain);
    return [...(saved || []).filter((d) => domains.includes(d)), ...domains.filter((d) => !(saved || []).includes(d))];
  }

  _orderRows(order, scope) {
    const known = this._integrationList() || [];
    const byDomain = Object.fromEntries(known.map((i) => [i.domain, i]));
    return `<ol class="order">${order
      .map((domain, i) => {
        const item = byDomain[domain] || { name: domain, local: false };
        return `<li><span class="grow">${esc(item.name)} <span class="muted small">${item.local ? "local" : "cloud"}</span></span>
          <button class="ghost" data-act="order-move" data-scope="${scope}" data-domain="${esc(domain)}" data-by="-1" ${i === 0 ? "disabled" : ""} aria-label="Move ${esc(item.name)} up">↑</button>
          <button class="ghost" data-act="order-move" data-scope="${scope}" data-domain="${esc(domain)}" data-by="1" ${i === order.length - 1 ? "disabled" : ""} aria-label="Move ${esc(item.name)} down">↓</button></li>`;
      })
      .join("")}</ol>`;
  }

  _summaryCard() {
    const st = this._data.settings?.daily_summary || { enabled: false, time: "21:00", only_away: false };
    return `<section class="card">
      <h3>Daily summary</h3>
      <p class="muted small">A notification each day of what ran, what failed and what was skipped, and why. It goes where notifications go (above).</p>
      <label class="check"><input type="checkbox" data-summary="enabled" ${st.enabled ? "checked" : ""}> Send a daily summary</label>
      <label>At<input type="time" data-summary="time" value="${esc(st.time)}"></label>
      <label class="check"><input type="checkbox" data-summary="only_away" ${st.only_away ? "checked" : ""}> Only while the house is Away or on Vacation</label>
      <div class="row-actions"><button class="ghost" data-act="summary-send">Send today's now</button></div>
    </section>`;
  }

  _editorCard() {
    const shared = this._data.settings?.editor === "full" ? "full" : "simple";
    let own = "";
    try {
      own = localStorage.getItem("daily-routine-editor") || "";
    } catch (_err) { /* private browsing */ }
    return `<section class="card">
      <h3>Routine editor</h3>
      <p class="muted small"><strong>Simple</strong> starts with one line (when, where, what to do) and adds the rest only when you ask for it. <strong>Full</strong> shows every option at once. Both change the same routine, and you can switch while editing.</p>
      <label>For everyone<select data-editor-default>
        <option value="simple" ${shared === "simple" ? "selected" : ""}>Simple</option>
        <option value="full" ${shared === "full" ? "selected" : ""}>Full</option></select></label>
      <label>On this device<select data-editor-own>
        <option value="" ${own ? "" : "selected"}>The same as everyone</option>
        <option value="simple" ${own === "simple" ? "selected" : ""}>Simple</option>
        <option value="full" ${own === "full" ? "selected" : ""}>Full</option></select></label>
    </section>`;
  }

  _livedInCard() {
    const li = this._data.settings?.lived_in || { enabled: false, lights: [], start: "sunset", end: "23:00", modes: ["away", "vacation"] };
    const status = this._data.lived_in || {};
    const lights = (this._data.all_entities || []).filter((e) => e.entity_id.startsWith("light.") || e.entity_id.startsWith("switch."));
    const checks = lights.map((e) => `<label class="check"><input type="checkbox" data-lived="light" data-val="${esc(e.entity_id)}" ${li.lights.includes(e.entity_id) ? "checked" : ""}> ${esc(e.name)}</label>`).join("");
    const modes = MODES.map(([id, label]) => `<label class="chip"><input type="checkbox" data-lived="mode" data-val="${id}" ${li.modes.includes(id) ? "checked" : ""}> ${esc(label)}</label>`).join("");
    const on = (status.on || []).map((id) => this._nameFor(id));
    return `<section class="card">
      <h3>Lived-in lights</h3>
      <p class="muted small">While the house is in one of the modes below (Away or Vacation to start with), the lights you pick turn on and off at random in the evening, a few at a time, so the house looks lived in. They're turned off again at the end, and when you come home.</p>
      <label class="check"><input type="checkbox" data-lived="enabled" ${li.enabled ? "checked" : ""}> Turn on lived-in lights</label>
      ${status.active ? `<p class="pill run">On now${on.length ? `: ${esc(on.join(", "))}` : ""}</p>` : ""}
      <label>How<select data-lived="pattern"><option value="random" ${li.pattern !== "last_week" ? "selected" : ""}>At random, a few at a time</option><option value="last_week" ${li.pattern === "last_week" ? "selected" : ""}>Like last week: the lights you switched by hand a week ago, at the same times</option></select></label>
      <p class="muted small">Like last week uses what you did by hand on the same evening a week earlier; on an evening with nothing to copy, it goes back to random.</p>
      <h4>In these modes</h4><div class="chips">${modes}</div>
      <div class="inline">
        <label>From<select data-lived="startKind" data-rerender>
          <option value="sunset" ${li.start === "sunset" ? "selected" : ""}>Sunset</option>
          <option value="time" ${li.start !== "sunset" ? "selected" : ""}>A set time</option></select></label>
        ${li.start !== "sunset" ? `<label>Time<input type="time" data-lived="start" value="${esc(li.start)}"></label>` : ""}
        <label>Until<input type="time" data-lived="end" value="${esc(li.end)}"></label>
      </div>
      <h4>Lights</h4>
      <div class="checks scroll">${checks || `<p class="muted small">No lights found.</p>`}</div>
    </section>`;
  }

  // Rows of the Kinds of day card: what's typed, else what's saved.
  _dayTypeRowsNow() {
    const rows = [...(this.shadowRoot?.querySelectorAll?.("[data-dt-row]") || [])];
    if (rows.length) {
      return rows.map((row) => ({
        name: row.querySelector("[data-dt='name']")?.value || "",
        calendar: row.querySelector("[data-dt='calendar']")?.value || "",
        words: row.querySelector("[data-dt='words']")?.value || "",
        person: row.querySelector("[data-dt='person']")?.value || "",
      }));
    }
    return this._dayTypeRows || (this._data.settings?.day_types || []).map((t) => ({ ...t, words: (t.words || []).join(", ") }));
  }

  _reportsCard() {
    const st = this._data.settings || {};
    return `<section class="card">
      <h3>After a restart, and coming home</h3>
      <label class="check"><input type="checkbox" data-report-setting="missed_report" ${st.missed_report ? "checked" : ""}> After Home Assistant restarts, tell me which routines' times passed while it was off, with a <strong>Run them now</strong> button</label>
      <label class="check"><input type="checkbox" data-report-setting="away_summary" ${st.away_summary ? "checked" : ""}> When the house leaves Away or Vacation, tell me what ran while we were gone</label>
    </section>`;
  }

  _quietCard() {
    const q = this._data.settings?.quiet_hours || { enabled: false, from: "22:00", until: "07:00" };
    return `<section class="card">
      <h3>Quiet hours</h3>
      <p class="muted small">Between these times routines leave the speakers alone: no announcements, no music, no turning the volume up, no saying the day ahead. Turning something down or off still works. A routine can be allowed to use them anyway, for an alarm.</p>
      <label class="check"><input type="checkbox" data-quiet="enabled" ${q.enabled ? "checked" : ""}> Keep the speakers quiet</label>
      <div class="inline"><label>From<input type="time" data-quiet="from" value="${esc(q.from)}"></label><label>Until<input type="time" data-quiet="until" value="${esc(q.until)}"></label></div>
      <div class="row-actions"><button class="primary" data-act="quiet-save">Save</button></div>
    </section>`;
  }

  _guestWifiCard() {
    const w = this._data.settings?.guest_wifi || { ssid: "", password: "" };
    return `<section class="card">
      <h3>Guest Wi-Fi</h3>
      <p class="muted small">While the house is in <strong>Guests</strong> mode, the Today tab and the Daily Routine dashboard card show this network, so visitors can read it off a wall tablet. Leave both empty to show nothing.</p>
      <div class="inline"><label>Network name<input type="text" data-wifi="ssid" maxlength="32" value="${esc(w.ssid)}"></label>
      <label>Password<input type="text" data-wifi="password" maxlength="63" value="${esc(w.password)}" autocomplete="off"></label></div>
      <div class="row-actions"><button class="primary" data-act="wifi-save">Save</button></div>
    </section>`;
  }

  _holdsCard() {
    const rows = this._holdRows || (this._data.settings?.holds || []).map((h) => ({ calendar: h.calendar, words: (h.words || []).join(", ") }));
    const calendars = this._calendars();
    return `<section class="card">
      <h3>Calendar holds</h3>
      <p class="muted small">While an event is on in one of these calendars (any event, or one with these words in its title), routines don't run on their own. For example “Babysitter” or “Vacation”. A routine can be set to run anyway. <strong>Run now</strong> always works.</p>
      ${this._data.hold ? `<p class="pill warn">On now: ${esc(this._data.hold)}</p>` : ""}
      ${calendars.length ? `${rows.map((r, i) => `<div class="hold-row" data-hold-row>
          <label>Calendar<select data-hold="calendar">${calendars.map((c) => `<option value="${esc(c.entity_id)}" ${c.entity_id === r.calendar ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></label>
          <label class="grow">When an event says<input data-hold="words" value="${esc(r.words)}" placeholder="any event, or words such as babysitter, vacation"></label>
          <button class="ghost" data-act="hold-remove" data-i="${i}">Remove</button></div>`).join("")}
        <div class="row-actions"><button class="ghost" data-act="hold-add">+ Calendar hold</button><button class="primary" data-act="holds-save">Save</button></div>`
        : `<p class="muted small">Add a calendar to Home Assistant to use this.</p>`}
    </section>`;
  }

  _holdRowsNow() {
    const rows = [...(this.shadowRoot?.querySelectorAll?.("[data-hold-row]") || [])];
    if (rows.length) return rows.map((row) => ({ calendar: row.querySelector("[data-hold='calendar']")?.value || "", words: row.querySelector("[data-hold='words']")?.value || "" }));
    return this._holdRows || (this._data.settings?.holds || []).map((h) => ({ calendar: h.calendar, words: (h.words || []).join(", ") }));
  }

  _weeklyCard() {
    const w = this._data.settings?.weekly_report || { enabled: false, weekday: 6, time: "18:00" };
    const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
    return `<section class="card">
      <h3>Weekly report</h3>
      <p class="muted small">Once a week, a notification of the last seven days: how many runs worked, failed or were skipped, which routines failed most, and which didn't run at all. It goes where notifications go (above).</p>
      <label class="check"><input type="checkbox" data-weekly="enabled" ${w.enabled ? "checked" : ""}> Send a weekly report</label>
      <div class="inline">
        <label>On<select data-weekly="weekday">${days.map((name, i) => `<option value="${i}" ${Number(w.weekday) === i ? "selected" : ""}>${name}</option>`).join("")}</select></label>
        <label>At<input type="time" data-weekly="time" value="${esc(w.time)}"></label>
      </div>
      <div class="row-actions"><button class="primary" data-act="weekly-save">Save</button><button class="ghost" data-act="weekly-send">Send it now</button></div>
    </section>`;
  }

  _dayTypesCard() {
    const rows = this._dayTypeRows || (this._data.settings?.day_types || []).map((t) => ({ ...t, words: (t.words || []).join(", ") }));
    const calendars = this._calendars();
    const rowHtml = rows.map((r, i) => `<div class="day-type" data-dt-row>
        <label>Name<input data-dt="name" maxlength="40" value="${esc(r.name)}" placeholder="School day"></label>
        <label>Calendar<select data-dt="calendar">${calendars.map((c) => `<option value="${esc(c.entity_id)}" ${c.entity_id === r.calendar ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></label>
        <label>For<select data-dt="person"><option value="">Everyone</option>${(this._data.people || []).filter((p) => !p.hidden).map((p) => `<option value="${esc(p.entity_id)}" ${r.person === p.entity_id ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
        <label class="grow">When an event says<input data-dt="words" value="${esc(r.words)}" placeholder="any event, or words such as no school, holiday"></label>
        <button class="ghost" data-act="day-type-remove" data-i="${i}">Remove</button></div>`).join("");
    return `<section class="card">
      <h3>Kinds of day</h3>
      <p class="muted small">Name the kinds of day your routines care about, such as School day, Holiday or Work from home, and the calendar that says so. Each day is the first kind with a matching event today (any event, or one with these words in its title); any other day is a Normal day. Routines then use <strong>+ Kind of day</strong> under their conditions.</p>
      ${this._data.day_type ? `<p class="pill">Today: ${esc(this._data.day_type)}</p>` : ""}
      ${calendars.length ? `${rowHtml}
      <div class="row-actions"><button class="ghost" data-act="day-type-add">+ Kind of day</button><button class="primary" data-act="day-types-save">Save</button></div>`
        : `<p class="muted small">Add a calendar to Home Assistant (Google, iCloud, a school's calendar link…) to use this.</p>`}
    </section>`;
  }

  async _saveLivedIn() {
    const root = this.shadowRoot;
    const current = this._data.settings?.lived_in || {};
    const picked = (kind) => [...root.querySelectorAll(`[data-lived="${kind}"]`)].filter((b) => b.checked).map((b) => b.dataset.val);
    const startKind = root.querySelector('[data-lived="startKind"]')?.value || "sunset";
    const startTime = root.querySelector('[data-lived="start"]')?.value;
    const lived = {
      enabled: Boolean(root.querySelector('[data-lived="enabled"]')?.checked),
      pattern: root.querySelector('[data-lived="pattern"]')?.value || current.pattern || "random",
      lights: picked("light"),
      modes: picked("mode"),
      start: startKind === "sunset" ? "sunset" : (startTime || (current.start !== "sunset" && current.start) || "18:00"),
      end: root.querySelector('[data-lived="end"]')?.value || current.end || "23:00",
    };
    if (!lived.modes.length) lived.modes = ["away", "vacation"];
    await this._saveSettings({ lived_in: lived });
  }

  async _loadToday(silent = false) {
    if (!this._hass) return;
    try {
      this._today = await this._call({ type: `${DOMAIN}/today` });
    } catch (err) {
      if (!silent) this._showNotice(err.message || String(err), true);
    }
    if (this._view.name === "today") this._render();
  }

  // What already ran today and what's still to come, in time order.
  _renderToday() {
    const t = this._today;
    if (!t) return `<p class="muted">Loading today…</p>`;
    const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    const routine = (id) => this._data.routines.find((r) => r.id === id);
    const running = (this._data.routines || []).filter((r) => this._data.statuses[r.id]?.state === "running");
    const done = [...t.done].reverse().map((e) => {
      const outcome = ACTIVITY_OUTCOMES[e.outcome] || e.outcome;
      const why = e.outcome === "skipped" ? activityReason(e.reason) : (e.outcome === "failed" ? e.reason || e.detail : e.detail);
      return `<li class="tl ${esc(e.outcome)}"><span class="tl-time">${esc(clock(e.at))}</span>
        <span class="tl-body"><strong>${esc(e.name)}</strong> <span class="pill ${e.outcome === "failed" ? "error" : e.outcome === "skipped" ? "warn" : ""}">${esc(outcome)}</span>${why ? `<div class="muted small">${esc(why)}</div>` : ""}</span></li>`;
    }).join("");
    const planned = t.planned.map((p) => {
      const r = routine(p.routine_id);
      const skip = p.skip_next;
      const paused = p.paused_until;
      return `<li class="tl planned"><span class="tl-time">${esc(clock(p.at))}</span>
        <span class="tl-body"><strong>${esc(p.name)}</strong> <span class="muted small">${esc(this._roomName(p.room))}</span>
          ${skip ? `<span class="pill warn">Will be skipped</span>` : ""}${paused ? `<span class="pill warn">Paused until ${esc(formatDay(paused))}</span>` : ""}
          ${r ? `<div class="row-actions">
            <button class="ghost" data-act="run" data-id="${esc(p.routine_id)}">Run now</button>
            <button class="ghost" data-act="skip-next" data-id="${esc(p.routine_id)}" data-skip="${skip ? "" : "1"}">${skip ? "Don't skip" : "Skip"}</button>
          </div>` : ""}</span></li>`;
    }).join("");
    const watching = t.watching.map((w) => {
      const r = routine(w.routine_id);
      return `<li>${esc(w.name)} <span class="muted small">· ${esc(r ? this._describeTrigger(r) : "")}</span></li>`;
    }).join("");
    const runningRows = running.map((r) => `<li class="tl running"><span class="tl-time">Now</span><span class="tl-body"><strong>${esc(r.name)}</strong>${progressHtml(this._data.statuses[r.id].progress)}${tickListHtml(r.id, this._data.statuses[r.id].ticklist)}</span></li>`).join("");
    const habits = this._habitCards();
    return `<p class="muted small">House mode: <strong>${esc(modeName(t.mode))}</strong>${this._data.day_type && (this._data.settings?.day_types || []).length ? ` · ${esc(this._data.day_type)}` : ""}${t.paused ? " · all routines are paused" : ""}</p>
      ${this._data.hold ? `<p class="pill warn">Calendar hold: ${esc(this._data.hold)}. Routines don't run on their own until it's over.</p>` : ""}
      ${guestWifiHtml(this._data.guest_wifi)}
      ${this._data.can_undo_recent ? `<div class="row-actions"><button class="ghost" data-act="undo-recent" title="Put back what routines changed in the last hour">Undo the last hour</button></div>` : ""}
      ${habits ? `<section class="card"><h3>Ideas from what you do</h3>${habits}</section>` : ""}
      <section class="card"><h3>Still to come</h3>
        ${runningRows || planned ? `<ul class="timeline">${runningRows}${planned}</ul>` : `<p class="muted">Nothing else is planned for today.</p>`}
      </section>
      ${watching ? `<section class="card"><h3>Runs when something happens</h3><ul class="small">${watching}</ul></section>` : ""}
      <section class="card"><h3>Already today</h3>
        ${done ? `<ul class="timeline">${done}</ul>` : `<p class="muted">Nothing has run yet today.</p>`}
      </section>
      <section class="card"><h3>Tomorrow</h3>
        <p class="muted small">A dry run: what would run tomorrow and when, step by step. Nothing is run. Routines that wait for something to happen aren't on a clock, so they're listed apart.</p>
        <div class="row-actions"><button class="ghost" data-act="tomorrow">${this._tomorrow ? "Hide tomorrow" : "See tomorrow"}</button></div>
        ${this._tomorrow ? this._tomorrowHtml(this._tomorrow) : ""}
      </section>`;
  }

  _tomorrowHtml(plan) {
    const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    const rows = plan.runs.map((r) => `<li class="tl planned"><span class="tl-time">${esc(clock(r.at))}</span>
      <span class="tl-body"><strong>${esc(r.name)}</strong> <span class="muted small">${esc(this._roomName(r.room))}</span>
        ${r.paused ? `<span class="pill warn">Paused</span>` : ""}${r.skip_modes.length ? `<span class="muted small"> · not in ${esc(r.skip_modes.map(modeName).join(", "))}</span>` : ""}
        <ol class="small dry-steps">${r.steps.map((label) => `<li>${esc(label)}</li>`).join("")}</ol></span></li>`).join("");
    const watching = plan.watching.length ? `<p class="muted small">Also when something happens: ${esc(plan.watching.map((w) => w.name).join(", "))}.</p>` : "";
    return `${plan.paused ? `<p class="pill warn">All routines are paused.</p>` : ""}${rows ? `<ul class="timeline">${rows}</ul>` : `<p class="muted">Nothing is planned for tomorrow.</p>`}${watching}`;
  }

  _renderIntegrationOrder() {
    const known = this._integrationList();
    const body = known === undefined
      ? `<p class="muted small">Loading…</p>`
      : known.length
        ? this._orderRows(this._fullOrder(this._data.settings?.integration_order), "settings")
        : `<p class="muted small">None of your devices is in more than one integration yet. When one is (Home Assistant shows them under Linked devices), it appears here.</p>`;
    return `<section class="card">
        <h3>Devices in more than one integration</h3>
        <p class="muted small">A device can be in Home Assistant more than once, for example a curtain through Matter, Bluetooth and the cloud. A step then uses one at a time, in this order, and only tries the next if the device didn't change. Routines can have their own order.</p>
        ${body}
      </section>`;
  }

  _renderAccess() {
    const a = this._access;
    if (!a) {
      if (!this._accessLoading && !this._accessFailed) {
        this._accessLoading = true;
        this._call({ type: `${DOMAIN}/access/get` })
          .then((res) => {
            this._access = { people: res?.people || [], ids: new Set(res?.user_ids || []) };
          })
          .catch((err) => {
            // Don't ask again on every redraw; leaving Settings and coming back retries.
            this._accessFailed = true;
            this._showNotice(err.message || String(err), true);
          })
          .finally(() => {
            this._accessLoading = false;
            if (this._view.name === "settings") this._render();
          });
      }
      const text = this._accessFailed ? "Couldn't load the list of people." : "Loading…";
      return `<section class="card"><h3>Who can see Daily Routine</h3><p class="muted small">${text}</p></section>`;
    }
    const others = a.people.filter((p) => !p.is_admin);
    const people = others.length
      ? others
          .map((p) => `<label class="check"><input type="checkbox" data-access-user="${esc(p.id)}" ${a.ids.has(p.id) ? "checked" : ""}> ${esc(p.name)}</label>`)
          .join("")
      : `<p class="muted small">Everyone with a Home Assistant login is an admin.</p>`;
    const everyone = [...a.ids].some((id) => others.some((p) => p.id === id));
    return `<section class="card">
        <h3>Who can see Daily Routine</h3>
        <p class="muted small">Admins always can. Routines show who's home and when, so anyone else only sees them if you check them here. Only admins can change routines and settings.</p>
        <div class="checks">${people}</div>
        ${everyone ? `<p class="muted small">Home Assistant can't show a sidebar entry to only some people, so everyone sees Daily Routine in the sidebar. People who aren't checked get a page saying they don't have access.</p>` : ""}
      </section>`;
  }

  async _saveAccess() {
    const ids = [...this.shadowRoot.querySelectorAll("[data-access-user]")]
      .filter((box) => box.checked)
      .map((box) => box.dataset.accessUser);
    try {
      const res = await this._call({ type: `${DOMAIN}/access/set`, user_ids: ids });
      this._access = { people: res?.people || [], ids: new Set(res?.user_ids || []) };
      this._showNotice("Access saved");
    } catch (err) {
      this._showNotice(err.message || String(err), true);
    }
    this._render();
  }

  _roomName(roomId) {
    return roomId ? this._area(roomId)?.name || roomId : "Whole house";
  }

  _renderAll() {
    return `${this._awayCard()}${attentionHtml(this._data.attention)}<div class="toolbar"><input type="search" data-search value="${esc(this._query || "")}"
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
    const groups = [...new Set(matches.map((r) => r.group || ""))].sort((a, b) => (a ? (b ? a.localeCompare(b) : -1) : 1));
    if (groups.length === 1 && !groups[0]) return matches.map((routine) => this._routineCard(routine, { showRoom: true })).join("");
    const admin = this._hass?.user?.is_admin;
    return groups.map((g) => {
      const members = matches.filter((r) => (r.group || "") === g);
      const paused = members.length && members.every((r) => this._pausedDay(r));
      const head = g
        ? `<div class="group-head"><h2>${esc(g)} <span class="muted small">${members.length}</span></h2>${admin ? `<div class="row-actions">
            ${paused ? `<button class="ghost" data-act="group-resume" data-group="${esc(g)}">Resume all</button>`
              : `<button class="ghost" data-act="group-pause" data-group="${esc(g)}" data-days="0">Pause today</button>
                 <button class="ghost" data-act="group-pause" data-group="${esc(g)}" data-days="6">Pause a week</button>`}
            <button class="ghost" data-act="group-share" data-group="${esc(g)}">Share all</button></div>` : ""}</div>
          ${this._groupShare === g ? `<div class="copy-panel"><p class="small">Send this code to someone with Daily Routine: it brings all ${members.length} routines of ${esc(g)}. They paste it under <strong>+ New routine</strong> → <strong>Import a routine</strong>.</p>
            <textarea data-share-code readonly rows="4">${esc(groupShareCode(members, this._areas()))}</textarea>
            <div class="row-actions"><button class="primary" data-act="share-copy">Copy</button><button class="ghost" data-act="group-share" data-group="${esc(g)}">Close</button></div></div>` : ""}`
        : `<div class="group-head"><h2>Not in a group <span class="muted small">${members.length}</span></h2></div>`;
      return `<section class="group">${head}${members.map((routine) => this._routineCard(routine, { showRoom: true })).join("")}</section>`;
    }).join("");
  }

  _groups() {
    return [...new Set((this._data?.routines || []).map((r) => r.group).filter(Boolean))].sort();
  }

  // Ideas from this home: what it has (curtains, a phone alarm, locks, motion
  // sensors...) that no routine uses yet.
  _suggestions() {
    const ideas = [];
    const routines = this._data.routines || [];
    const used = (pred) => routines.some(pred);
    const inRoom = (area, domain, cls) => (area.entities || []).filter((e) => e.domain === domain && (!cls || e.device_class === cls));
    for (const area of this._data.areas || []) {
      const roomRoutines = routines.filter((r) => r.room === area.id);
      const covers = inRoom(area, "cover");
      if (covers.length && !roomRoutines.some((r) => r.actions.some((a) => String(a.service || "").startsWith("cover.")))) {
        ideas.push({ id: `idea:curtains:${area.id}`, room: area.id, title: `Close ${area.name} curtains at sunset`, hint: `${area.name} has curtains or blinds that no routine moves yet.`,
          draft: { trigger: "sunset", actions: [{ service: "cover.close_cover", targetMode: "area" }] } });
      }
      const motion = (area.entities || []).filter((e) => e.domain === "binary_sensor" && ["motion", "occupancy", "presence"].includes(e.device_class));
      const lights = inRoom(area, "light");
      if (motion.length && lights.length && !roomRoutines.some((r) => r.trigger === "room_clear")) {
        ideas.push({ id: `idea:empty:${area.id}`, room: area.id, title: `${area.name} lights off when it's empty`, hint: `Turns ${area.name}'s lights off once its motion sensors have been quiet for 10 minutes.`,
          draft: { trigger: "room_clear", clear_minutes: 10, useRoomSensors: true, actions: [{ service: "light.turn_off", targetMode: "area" }] } });
      }
      const humidity = inRoom(area, "sensor", "humidity");
      const fans = [...inRoom(area, "fan"), ...(area.entities || []).filter((e) => e.domain === "switch" && /fan/i.test(e.name || e.entity_id))];
      if (humidity.length && fans.length && !roomRoutines.some((r) => VALUE_TRIGGERS.includes(r.trigger))) {
        ideas.push({ id: `idea:fan:${area.id}`, room: area.id, title: `${area.name} fan on when it's steamy`, hint: "Turns the fan on above 70% humidity, and off again below 60%.",
          draft: { trigger: "value_above", valueClass: "humidity", valueThreshold: 70, offThreshold: 60, actions: [{ service: `${fans[0].domain}.turn_on`, targetMode: "entities", entities: [fans[0].entity_id] }] } });
      }
    }
    for (const alarm of this._data.alarm_sensors || []) {
      if (used((r) => r.trigger === "alarm" && r.watch_entity === alarm.entity_id)) continue;
      ideas.push({ id: `idea:wake:${alarm.entity_id}`, room: null, title: `Wake-up lights before ${alarm.name}`, hint: "Fades lights up over 10 minutes, starting 15 minutes before the alarm. Pick the lights.",
        draft: { trigger: "alarm", watch_entity: alarm.entity_id, offset_minutes: -15, actions: [{ service: "light.turn_on", targetMode: "entities", fields: { brightness_pct: 60, transition: 10 } }] } });
    }
    const tracked = this._trackedPeople();
    const locks = (this._data.areas || []).flatMap((a) => inRoom(a, "lock"));
    if (tracked.length && locks.length && !used((r) => r.trigger === "everyone_leaves")) {
      ideas.push({ id: "idea:lockup", room: null, title: "Lock up when everyone leaves", hint: "Locks every door and switches the house to Away once everyone has left.",
        draft: { trigger: "everyone_leaves", usePeopleList: true, actions: [{ service: "lock.lock", targetMode: "all_areas" }, { kind: "mode", mode: "away" }] } });
    }
    const weather = (this._data.weathers || [])[0];
    if (weather && !used((r) => r.trigger === "forecast")) {
      ideas.push({ id: "idea:rain", room: null, title: "Tell me when rain is coming", hint: "A notification when rain shows up in the next 3 hours.",
        draft: { trigger: "forecast", watch_entity: weather.entity_id, forecast_hours: 3, forecast_kinds: ["rainy", "pouring", "lightning-rainy"],
          actions: [{ service: "notify.persistent_notification", targetMode: "none", fields: { message: "Rain is coming in the next few hours." } }] } });
    }
    return ideas.slice(0, 8);
  }

  // What people keep doing by hand around the same time, offered as routines.
  _habitIdeas() {
    return (this._data?.habits || []).map((h) => {
      const verb = h.service.endsWith("open_cover") ? "Open" : h.service.endsWith("close_cover") ? "Close" : h.on ? "Turn on" : "Turn off";
      const weekdays = h.days.length === 5 ? "on weekdays" : "every day";
      return {
        id: `habit:${h.id}`, habit: h.id, room: null,
        title: `${verb} ${h.name} at ${hhmm(h.time)}`,
        hint: `You've done this by hand around then on ${h.count} of the last 14 days${h.when_dark && h.someone_home ? ", when it was dark and someone was home" : h.when_dark ? ", when it was dark" : h.someone_home ? ", when someone was home" : ""}. A routine can do it ${weekdays}${h.when_dark || h.someone_home ? ", only then" : ""}.`,
        draft: {
          trigger: "time", time: h.time, days: [...h.days],
          conditions: [
            ...(h.when_dark ? [{ kind: "state", entity_id: "sun.sun", state: "below_horizon" }] : []),
            ...(h.someone_home ? [{ kind: "presence", who: "anyone", where: "in", people: this._trackedPeople(), zone: "zone.home" }] : []),
          ],
          actions: [{ service: h.service, targetMode: "entities", entities: [h.entity_id] }],
        },
      };
    });
  }

  _habitCards() {
    const ideas = this._habitIdeas();
    if (!ideas.length) return "";
    const admin = this._hass?.user?.is_admin;
    return `<div class="ideas">${ideas.map((t) => `<div class="card template idea habit">
        <h3>${esc(t.title)}</h3><div class="muted small">${esc(t.hint)}</div>
        <div class="row-actions"><button class="primary" data-act="template" data-tpl="${esc(t.id)}" data-room="">Make a routine</button>
        ${admin ? `<button class="ghost" data-act="idea-dismiss" data-idea="${esc(t.habit)}">No thanks</button>` : ""}</div></div>`).join("")}</div>`;
  }

  // The built-in library: ready-made routines with several steps, by topic.
  _libraryHtml(roomId) {
    const items = LIBRARY.filter((t) => (roomId ? t.scope !== "house" : t.scope !== "room"));
    if (!items.length) return "";
    const topics = [...new Set(items.map((t) => t.topic))];
    return `<details class="card library"><summary><strong>Library</strong> <span class="muted small">${items.length} ready-made routines, by topic</span></summary>
      ${topics.map((topic) => `<h4>${esc(topic)}</h4><div class="grid">${items.filter((t) => t.topic === topic).map((t) => `<button class="card template" data-act="template" data-tpl="${esc(t.id)}" data-room="${esc(roomId || "")}">
        <h3>${esc(t.title)}</h3><div class="muted small">${esc(t.hint)}</div></button>`).join("")}</div>`).join("")}
    </details>`;
  }

  _renderTemplates() {
    const roomId = this._view.room ?? null;
    const options = TEMPLATES.filter((t) => (roomId ? t.scope !== "house" : t.scope !== "room"));
    const ideas = this._suggestions().filter((i) => !roomId || i.room === roomId);
    const ideaCards = ideas.length ? `<h2 class="small muted">Ideas for your home</h2><div class="ideas">${ideas.map((t) => `<button class="card template idea" data-act="template" data-tpl="${esc(t.id)}" data-room="${esc(t.room || "")}">
        <h3>${esc(t.title)}</h3><div class="muted small">${esc(t.hint)}</div></button>`).join("")}</div><h2 class="small muted">Or start from</h2>` : "";
    const importBox = `<details class="card import"><summary><strong>Import a routine</strong> <span class="muted small">from a code someone shared</span></summary>
      <textarea data-import-code rows="4" placeholder="DR1:…"></textarea>
      <div class="row-actions"><button class="primary" data-act="import-routine">Import</button></div></details>`;
    const habits = !roomId ? this._habitCards() : "";
    const habitBlock = habits ? `<h2 class="small muted">From what you do by hand</h2>${habits}` : "";
    return `${habitBlock}${ideaCards}${this._libraryHtml(roomId)}${importBox}<p class="muted">Start from a template, or a blank routine. You can change everything afterwards.</p>
      <div class="grid">${options.map((t) => `<button class="card template" data-act="template" data-tpl="${t.id}" data-room="${esc(roomId || "")}">
        <h3>${esc(t.title)}</h3><div class="muted small">${esc(t.hint)}</div></button>`).join("")}</div>`;
  }

  // Which editor opens: this device's own choice, else the one in Settings.
  _editorMode() {
    if (this._editorSession) return this._editorSession;
    if (this._editorOverride) return this._editorOverride;
    try {
      const own = localStorage.getItem("daily-routine-editor");
      if (own === "simple" || own === "full") return own;
    } catch (_err) { /* private browsing */ }
    return this._data?.settings?.editor === "full" ? "full" : "simple";
  }

  _renderEditor() {
    const d = this._draft;
    if (!d) return "";
    return this._editorMode() === "simple" ? this._renderSimple(d) : this._renderFull(d);
  }

  _editorSwitch(mode) {
    const other = mode === "simple" ? "full" : "simple";
    return `<p class="switch-editor"><button class="link" data-act="switch-editor" data-mode="${other}">${other === "full" ? "Switch to the full editor" : "Switch to the simple editor"}</button></p>`;
  }

  _footer(d) {
    const preview = this._preview ? this._renderPreview(this._preview) : "";
    return `${preview}
      ${this._notice ? `<div class="notice ${this._error ? "error" : ""}">${esc(this._notice)}</div>` : ""}
      <div class="footer">
        ${d.isNew ? "" : `<button class="ghost danger" data-act="delete">Delete</button>`}
        <button class="ghost" data-act="cancel">Cancel</button>
        <button class="ghost" data-act="test">Test (dry run)</button>
        <button class="primary" data-act="save">Save</button>
      </div>`;
  }

  // ----- the simple editor: a sentence, then only what's asked for -----

  _renderSimple(d) {
    const open = this._simpleOpen || (this._simpleOpen = this._defaultOpen(d));
    const rooms = (this._data.areas || []).map((a) => `<option value="${esc(a.id)}" ${a.id === d.room ? "selected" : ""}>${esc(a.name)}</option>`).join("");
    const chips = SIMPLE_SECTIONS
      .filter(([key]) => !open.has(key) && this._sectionApplies(key, d))
      .map(([key, label]) => `<button class="chip-add" data-act="open-section" data-key="${key}">+ ${esc(label)}</button>`).join("");
    const section = (key, title, body) => (open.has(key) && this._sectionApplies(key, d)
      ? `<section class="card"><div class="step-head"><h3>${esc(title)}</h3><button class="ghost" data-act="close-section" data-key="${key}" aria-label="Hide ${esc(title)}">Hide</button></div>${body}</section>` : "");
    return `<form class="editor simple" onsubmit="return false">
      ${this._editorSwitch("simple")}
      <section class="card sentence">
        <label class="say"><span class="word">Called</span><input data-f="name" value="${esc(d.name)}" placeholder="Morning curtains"></label>
        <div class="say"><span class="word">When</span><div class="grow">${this._whenFields(d)}</div></div>
        <label class="say"><span class="word">In</span><select data-f="room" data-rerender><option value="" ${d.room ? "" : "selected"}>The whole house</option>${rooms}</select></label>
        <div class="say"><span class="word">Do</span><div class="grow">${this._stepsBody(d)}</div></div>
        <label class="say"><span class="word">Group</span><input data-f="group" maxlength="40" list="dr-groups" value="${esc(d.group || "")}" placeholder="optional, for example Bedtime"></label>
        <datalist id="dr-groups">${this._groups().map((g) => `<option value="${esc(g)}"></option>`).join("")}</datalist>
        <label class="check"><input type="checkbox" data-f="enabled" ${d.enabled ? "checked" : ""}> Turned on</label>
      </section>
      ${chips ? `<div class="chips-add" role="group" aria-label="More options">${chips}</div>` : ""}
      ${section("more", "Or also when", this._moreTriggers(d, false))}
      ${section("conditions", "Only if", this._conditionsBody(d))}
      ${section("dates", "Only some dates or hours", this._datesBody(d))}
      ${section("ask", "Ask me first", this._askBody(d))}
      ${section("notify", "Notify me", this._notifyBody(d))}
      ${section("skip", "Skip or pause", this._skipBody(d))}
      ${section("options", "More options", this._optionsBody(d))}
      ${this._footer(d)}
    </form>`;
  }

  // Sections a routine already uses start open in the simple editor.
  _defaultOpen(d) {
    const open = new Set();
    if ((d.more_triggers || []).length) open.add("more");
    if (d.conditions.length) open.add("conditions");
    if (d.from_date || d.until_date || d.active_from || d.active_to || Number(d.heads_up_minutes)) open.add("dates");
    if (d.ask_first) open.add("ask");
    if (d.notify_on_run || d.notify_on_skip || d.notify_on_health) open.add("notify");
    if (d.skip_next || d.paused_until) open.add("skip");
    if (Number(d.retries) || d.on_step_error === "continue" || Number(d.cooldown_minutes) || d.repeat || d.integration_order != null || d.device_id) open.add("options");
    return open;
  }

  _sectionApplies(key, d) {
    if (key === "more") return !["manual", "todo_done", "room_clear"].includes(d.trigger);
    if (key === "ask") return d.trigger !== "manual";
    return true;
  }

  // ----- the full editor: every option at once -----

  _renderFull(d) {
    const roomId = d.room || null;
    return `<form class="editor" onsubmit="return false">
      ${this._editorSwitch("full")}
      <section class="card">
        <h3>Basics</h3>
        <label>Name<input data-f="name" value="${esc(d.name)}" placeholder="e.g. Morning curtains"></label>
        <label>Group (optional)<input data-f="group" maxlength="40" list="dr-groups" value="${esc(d.group || "")}" placeholder="for example Bedtime"><span class="muted small">Routines in a group are listed together and can be paused together.</span></label>
        <datalist id="dr-groups">${this._groups().map((g) => `<option value="${esc(g)}"></option>`).join("")}</datalist>
        <label>Linked device<select data-f="device_id">${this._deviceOptions(d.device_id, roomId)}</select></label>
        <p class="muted small">Each routine is a service under Daily Routine, linked to this device in Home Assistant.</p>
        <label class="check"><input type="checkbox" data-f="enabled" ${d.enabled ? "checked" : ""}> Enabled</label>
        <p class="muted small">${roomId ? `Room: ${esc(this._area(roomId)?.name || roomId)}` : "Whole house"}</p>
      </section>

      <section class="card">
        <h3>When</h3>
        ${this._whenFields(d)}
        ${d.trigger !== "manual" && d.trigger !== "todo_done" && d.trigger !== "room_clear" ? this._moreTriggers(d) : ""}
        ${this._datesBody(d)}
        <h4>Extra conditions</h4>
        ${this._conditionsBody(d)}
      </section>

      <section class="card">
        <h3>Do</h3>
        ${this._stepsBody(d)}
      </section>

      <section class="card">
        <h3>Options</h3>
        ${this._optionsBody(d)}
        ${d.trigger !== "manual" ? `<h4>Ask me first</h4>${this._askBody(d)}` : ""}
        <h4>Skip for now</h4>
        ${this._skipBody(d)}
        <h4>Notify me</h4>
        ${this._notifyBody(d)}
      </section>
      ${this._footer(d)}
    </form>`;
  }

  // ----- sections, shared by both editors -----

  _whenFields(d) {
    const roomId = d.room || null;
    const presence = PRESENCE_TRIGGERS.includes(d.trigger);
    const valueTrigger = VALUE_TRIGGERS.includes(d.trigger);
    const stateTrigger = STATE_TRIGGERS.includes(d.trigger);
    const todoDone = TODO_DONE_TRIGGERS.includes(d.trigger);
    const timeBased = ["time", "sunrise", "sunset"].includes(d.trigger);
    const planned = ["alarm", "calendar_event", "cheapest", "forecast", "mode_changes"].includes(d.trigger);
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
    return `<label>Trigger<select data-f="trigger" data-rerender>${triggerOptions}</select></label>
        ${valueTrigger ? this._valueFields(d, roomId) : ""}
        ${stateTrigger ? this._stateFields(d) : ""}
        ${todoDone ? this._todoDoneFields(d) : ""}
        ${this._plannedFields(d)}
        ${d.trigger === "mode_changes" ? `<h4>To</h4><div class="chips">${MODES.map(([id, label]) => `<label class="chip"><input type="checkbox" data-list="modes" data-val="${id}" ${(d.modes || []).includes(id) ? "checked" : ""}> ${esc(label)}</label>`).join("")}</div>
        <p class="muted small">Runs when the house mode changes to one of these, from the House mode list at the top, a routine, or an automation.</p>` : ""}
        ${d.trigger === "time" ? `<label>Time<input type="time" data-f="time" value="${esc(hhmm(d.time))}"></label>` : ""}
        ${d.trigger === "sunrise" || d.trigger === "sunset" ? `<label>Offset in minutes (negative is before)<input type="number" min="-720" max="720" data-f="offset_minutes" value="${esc(d.offset_minutes)}"></label>
          <div class="inline"><label>But not before (optional)<input type="time" data-f="not_before" value="${esc(d.not_before || "")}"></label>
          <label>And not after (optional)<input type="time" data-f="not_after" value="${esc(d.not_after || "")}"></label></div>
          <p class="muted small">For example sunset, but not before 18:00 in winter and not after 20:00 in summer.</p>` : ""}
        ${timeBased && (this._editorMode() === "full" || Number(d.random_minutes)) ? `<label>Vary the time by up to (minutes, earlier or later)<input type="number" min="0" max="120" data-f="random_minutes" value="${esc(d.random_minutes || 0)}"></label>
        <p class="muted small">A different time each day, so lights while you're away don't look like a timer. 0 runs at the same time every day.</p>` : ""}
        ${timeBased && (this._data.weathers || []).length && (this._editorMode() === "full" || d.hotEntity) ? this._hotFields(d) : ""}
        ${timeBased || valueTrigger || stateTrigger || todoDone || planned ? `<div class="chips">${days}</div>` : ""}
        ${presence ? this._zoneField(d) : ""}
        ${presence ? `<h4>Who</h4><p class="muted small">Pick the people this routine watches. People without location tracking can't be checked.</p><div class="checks">${peopleList}</div>
        <button class="ghost" data-act="tracked-people">Select everyone with tracking</button>` : ""}
        ${sensorsOn ? `<h4>Sensors</h4><p class="muted small">The routine runs once these have been quiet for the time below.</p>
          ${roomId && !roomSensors.length ? `<p class="muted small">This room has no motion sensors yet. Assign one in Home Assistant, or pick another.</p>` : ""}
          <div class="checks">${sensorList}</div>
          <label>Quiet for (minutes)<input type="number" min="0" max="1440" data-f="clear_minutes" value="${esc(d.clear_minutes)}"></label>` : ""}`;
  }

  // Before an alarm or a calendar event, a forecast, or the cheapest hour.
  _plannedFields(d) {
    const offset = (help) => `<label>Minutes before (negative) or after<input type="number" min="-720" max="720" data-f="offset_minutes" value="${esc(d.offset_minutes)}"></label>
      <p class="muted small">${help}</p>`;
    if (d.trigger === "alarm") {
      const alarms = this._data.alarm_sensors || [];
      return `<label>Alarm<select data-f="watch_entity"><option value="">Choose…</option>${alarms.map((a) => `<option value="${esc(a.entity_id)}" ${a.entity_id === d.watch_entity ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select></label>
        ${alarms.length ? "" : `<p class="muted small">Turn on the <strong>Next alarm</strong> sensor in the Home Assistant Companion app on your phone to use this.</p>`}
        ${offset("For example -15 starts it 15 minutes before the alarm. Days with no alarm set, nothing happens.")}`;
    }
    if (d.trigger === "calendar_event") {
      const calendars = this._calendars();
      return `<label>Calendar<select data-f="watch_entity"><option value="">Choose…</option>${calendars.map((c) => `<option value="${esc(c.entity_id)}" ${c.entity_id === d.watch_entity ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></label>
        <label>Events with this in the title (empty: every event)<input data-f="watch_item" value="${esc(d.watch_item || "")}" placeholder="for example School"></label>
        ${offset("For example -30 runs it half an hour before the event starts. All-day events start at midnight.")}`;
    }
    if (d.trigger === "forecast") {
      const weathers = this._data.weathers || [];
      const kinds = Object.entries(WEATHER_NAMES).map(([id, label]) => `<label class="check"><input type="checkbox" data-list="forecast_kinds" data-val="${id}" ${(d.forecast_kinds || []).includes(id) ? "checked" : ""}> ${esc(label)}</label>`).join("");
      return `<label>Forecast<select data-f="watch_entity"><option value="">Choose…</option>${weathers.map((w) => `<option value="${esc(w.entity_id)}" ${w.entity_id === d.watch_entity ? "selected" : ""}>${esc(w.name)}</option>`).join("")}</select></label>
        <label>Within the next (hours)<input type="number" min="1" max="48" data-f="forecast_hours" value="${esc(d.forecast_hours || 3)}"></label>
        <h4>Any of this weather</h4><div class="checks">${kinds}</div>
        <label>Or the temperature drops below (optional)<input type="number" step="any" data-f="value_threshold" value="${esc(d.value_threshold ?? "")}"></label>
        <p class="muted small">Runs once when the forecast starts showing it, and again only after it has cleared.</p>`;
    }
    if (d.trigger === "tag") {
      const tags = this._tags || [];
      return `<label>Tag<select data-f="watch_item"><option value="">Choose…</option>${tags.map((t) => `<option value="${esc(t.id)}" ${t.id === d.watch_item ? "selected" : ""}>${esc(t.name || t.id)}</option>`).join("")}</select></label>
        <p class="muted small">${tags.length ? "Tags are written and named in the Home Assistant app (Settings → Tags). Scanning one with a phone runs the routine." : "No tags yet. Write one with the Home Assistant app on your phone, under Settings → Tags."}</p>`;
    }
    if (d.trigger === "button") {
      const buttons = (this._data.all_entities || []).filter((e) => e.entity_id.startsWith("event."));
      return `<label>Button<select data-f="watch_entity"><option value="">Choose…</option>${buttons.map((b) => `<option value="${esc(b.entity_id)}" ${b.entity_id === d.watch_entity ? "selected" : ""}>${esc(b.name)}</option>`).join("")}</select></label>
        <label>Only these presses (optional)<input data-f="watch_states" value="${esc(d.watch_states || "")}" placeholder="for example single, or double_press"></label>
        <p class="muted small">Any button Home Assistant shows as an event: a Pico, a Zigbee remote, a doorbell, a Button Actions remote. Leave the presses empty for every press.</p>`;
    }
    if (d.trigger === "cheapest") {
      const sensors = (this._data.sensors || []).slice().sort((a, b) => Number(/kwh/i.test(b.unit || "")) - Number(/kwh/i.test(a.unit || "")));
      return `<label>Electricity price<select data-f="value_entity"><option value="">Choose…</option>${sensors.map((x) => `<option value="${esc(x.entity_id)}" ${x.entity_id === d.value_entity ? "selected" : ""}>${esc(x.name)}${x.unit ? ` (${esc(x.unit)})` : ""}</option>`).join("")}</select></label>
        <div class="inline"><label>Cheapest hour from<input type="time" data-f="active_from" value="${esc(d.active_from)}"></label>
        <label>Until<input type="time" data-f="active_to" value="${esc(d.active_to)}"></label></div>
        <p class="muted small">Works with price integrations that list the day's prices (Nord Pool, Tibber, ENTSO-e and others). If the prices aren't known yet, it runs at the start of the hours.</p>`;
    }
    return "";
  }

  _datesBody(d) {
    return `<h4>Only part of the year</h4>
        <p class="muted small">Optional, for things like holiday lights or heating. The year doesn't matter; the dates repeat every year, and can run over New Year (1 November to 31 March). Leave both empty to run all year.</p>
        <div class="inline">
          <label>From<input type="date" data-f="from_date" value="${esc(monthDayInput(d.from_date))}"></label>
          <label>Until<input type="date" data-f="until_date" value="${esc(monthDayInput(d.until_date))}"></label>
        </div>
        ${["time", "sunrise", "sunset", "alarm", "calendar_event", "cheapest"].includes(d.trigger) ? `<label>Heads-up before it runs
          <select data-f="heads_up_minutes">${HEADS_UP_CHOICES.map((m) => `<option value="${m}" ${Number(d.heads_up_minutes || 0) === m ? "selected" : ""}>${m ? `${m} minutes before, with a Skip button` : "No"}</option>`).join("")}</select>
        </label>` : ""}
        ${this._data.settings?.quiet_hours?.enabled ? `<label class="check"><input type="checkbox" data-f="loud_ok" ${d.loud_ok ? "checked" : ""}> Use the speakers during quiet hours too (Settings), for an alarm</label>` : ""}
        ${(this._data.settings?.holds || []).length ? `<label class="check"><input type="checkbox" data-f="ignore_holds" ${d.ignore_holds ? "checked" : ""}> Run even during a calendar hold (Settings)</label>` : ""}
        <h4>Skip these days</h4>
        <div class="chips">${(d.skip_dates || []).map((day) => `<span class="chip">${esc(formatDay(day))} <button class="ghost" data-act="skip-date-remove" data-day="${esc(day)}" aria-label="Don't skip ${esc(day)}">✕</button></span>`).join("") || `<span class="muted small">None.</span>`}</div>
        <div class="inline"><label>Add a day<input type="date" data-skip-date></label>
          <button class="ghost" data-act="skip-date-add">Add</button>
          ${(d.skip_dates || []).length ? `<button class="ghost" data-act="skip-date-week" title="Add the same weekdays a week later">Same days next week</button>` : ""}</div>
        <p class="muted small">It doesn't run on its own on these days, such as a holiday or a day off. <strong>Run now</strong> still works.</p>
        <h4>Not on its own in these house modes</h4>
        <div class="chips">${MODES.map(([id, label]) => `<label class="chip"><input type="checkbox" data-skipmode="${id}" ${(d.skip_modes || []).includes(id) ? "checked" : ""}> ${esc(label)}</label>`).join("")}</div>
        <p class="muted small">For example no bedtime lock-up while there are Guests. <strong>Run now</strong> still works.</p>
        ${d.trigger === "cheapest" ? "" : `<h4>Only run between</h4>
        <p class="muted small">Optional. Outside these hours the routine does nothing. Leave both empty to run at any hour.</p>
        <div class="inline">
          <label>From<input type="time" data-f="active_from" value="${esc(d.active_from)}"></label>
          <label>To<input type="time" data-f="active_to" value="${esc(d.active_to)}"></label>
        </div>`}`;
  }

  _conditionsBody(d) {
    return `<p class="muted small">Only run when these hold. Add as many as you need: who is home or away, sensors that have been quiet or active, or a device's state.</p>
        ${d.conditions.length > 1 ? `<label>Run when<select data-f="conditions_match">
          <option value="all" ${d.conditions_match !== "any" ? "selected" : ""}>All of these are true</option>
          <option value="any" ${d.conditions_match === "any" ? "selected" : ""}>Any one of these is true</option></select></label>` : ""}
        ${this._conditionRows(d)}
        ${d.conditions.length || d.trigger === "everyone_leaves" || d.trigger === "room_clear" ? `<label>If they aren't met when it's time<select data-f="wait" data-rerender>
          <option value="day" ${d.wait === "day" || !d.wait ? "selected" : ""}>Wait, and run as soon as they are (until the end of the day)</option>
          <option value="until" ${d.wait === "until" ? "selected" : ""}>Wait, and run as soon as they are, until…</option>
          <option value="minutes" ${d.wait === "minutes" ? "selected" : ""}>Wait a few minutes, and run as soon as they are</option>
          <option value="no" ${d.wait === "no" ? "selected" : ""}>Skip it until next time</option></select></label>
        ${d.wait === "until" ? `<label>Stop waiting at<input type="time" data-f="wait_until" value="${esc(d.wait_until || "12:00")}"></label>` : ""}
        ${d.wait === "minutes" ? `<label>Wait up to (minutes)<input type="number" min="1" max="240" data-f="wait_minutes" value="${esc(d.wait_minutes || 5)}"></label>` : ""}
        ${EVENT_TRIGGERS.includes(d.trigger) ? `<p class="muted small">This routine reacts to something happening. Waiting long can make it run hours later, when that moment has passed; skipping, or waiting a few minutes, is usually right. A waiting run is dropped once what started it is over (for example everyone has left again).</p>` : ""}` : ""}
        <div class="row-actions">
          <button class="ghost" data-act="add-condition" data-kind="presence">+ Who is home or away</button>
          <button class="ghost" data-act="add-condition" data-kind="sensors">+ Sensors quiet or active</button>
          <button class="ghost" data-act="add-condition" data-kind="state">+ A device's state</button>
          <button class="ghost" data-act="add-condition" data-kind="calendar">+ Calendar day</button>
          <button class="ghost" data-act="add-condition" data-kind="mode">+ House mode</button>
          <button class="ghost" data-act="add-condition" data-kind="workday">+ Workday or day off</button>
          <button class="ghost" data-act="add-condition" data-kind="weather">+ Weather</button>
          <button class="ghost" data-act="add-condition" data-kind="day_type">+ Kind of day</button>
          <button class="ghost" data-act="add-condition" data-kind="forecast">+ Forecast</button>
          <button class="ghost" data-act="add-condition" data-kind="charging">+ Phones charging</button>
          <button class="ghost" data-act="add-condition" data-kind="value">+ A sensor's level</button>
        </div>`;
  }

  // On hot days (by the forecast), the time moves.
  _hotFields(d) {
    const weathers = this._data.weathers || [];
    return `<label>On hot days<select data-f="hotEntity" data-rerender><option value="">Same time every day</option>${weathers.map((w) => `<option value="${esc(w.entity_id)}" ${w.entity_id === d.hotEntity ? "selected" : ""}>${esc(w.name)}</option>`).join("")}</select></label>
      ${d.hotEntity ? `<div class="inline"><label>When the high is at least<input type="number" step="0.5" data-f="hotAbove" value="${esc(d.hotAbove ?? 28)}"></label>
      <label>Move it by (minutes, negative is earlier)<input type="number" min="-180" max="180" data-f="hotMinutes" value="${esc(d.hotMinutes ?? -60)}"></label></div>` : ""}
      ${d.hotEntity ? `<p class="muted small">For example close the blinds an hour earlier when the forecast says 30° or more. Without a forecast it runs at its usual time.</p>` : ""}`;
  }

  _stepsBody(d) {
    const roomId = d.room || null;
    const steps = d.actions.map((step, i) => this._stepEditor(step, i, roomId)).join("");
    return `<ol class="steps">${steps}</ol>
        <div class="row-actions"><button class="ghost" data-act="add-step">+ Action</button><button class="ghost" data-act="add-todo">+ To-do</button><button class="ghost" data-act="add-delay">+ Wait</button>
          <button class="ghost" data-act="add-kind" data-kind="wait_until">+ Wait until…</button>
          <button class="ghost" data-act="add-kind" data-kind="scene">+ Make it look like this</button>
          <button class="ghost" data-act="add-kind" data-kind="routine">+ Run another routine</button>
          <button class="ghost" data-act="add-kind" data-kind="mode">+ Set the house mode</button>
          <button class="ghost" data-act="add-kind" data-kind="ramp">+ Turn the volume up slowly</button>
          <button class="ghost" data-act="add-kind" data-kind="cheaper">+ Wait for cheaper electricity</button>
          <button class="ghost" data-act="add-kind" data-kind="briefing">+ Say the day ahead</button>
          <button class="ghost" data-act="add-kind" data-kind="check_open">+ Check doors and windows</button>
          <button class="ghost" data-act="add-kind" data-kind="battery">+ Check batteries</button>
          <button class="ghost" data-act="add-kind" data-kind="ticks">+ Wait for a checklist</button></div>`;
  }

  _askBody(d) {
    return `<label class="check"><input type="checkbox" data-f="ask_first" data-rerender ${d.ask_first ? "checked" : ""}> Ask on my phone (and the dashboard card) before it runs</label>
      ${d.ask_first ? `<label>Wait for an answer (minutes)<input type="number" min="1" max="60" data-f="ask_minutes" value="${esc(d.ask_minutes || 10)}"></label>
      <label>With no answer
        <select data-f="ask_default">
          <option value="run" ${d.ask_default !== "skip" ? "selected" : ""}>Run it</option>
          <option value="skip" ${d.ask_default === "skip" ? "selected" : ""}>Skip it</option>
        </select></label>
      <p class="muted small">The question goes to the phones picked under Settings → Notifications, with Run and Skip buttons. Running it yourself with Run now doesn't ask.</p>` : ""}`;
  }

  _skipBody(d) {
    return `<label class="check"><input type="checkbox" data-f="skip_next" ${d.skip_next ? "checked" : ""}> Skip the next run (it runs as usual after that)</label>
        <label>Pause until (the last day it's skipped)<input type="date" data-f="paused_until" value="${esc(d.paused_until || "")}"></label>`;
  }

  _notifyBody(d) {
    return `<label class="check"><input type="checkbox" data-f="notify_on_run" ${d.notify_on_run ? "checked" : ""}> When it runs</label>
        <label class="check"><input type="checkbox" data-f="notify_on_skip" ${d.notify_on_skip ? "checked" : ""}> When it is skipped</label>
        <label class="check"><input type="checkbox" data-f="notify_on_error" ${d.notify_on_error ? "checked" : ""}> When it fails</label>
        <label class="check"><input type="checkbox" data-f="notify_on_health" ${d.notify_on_health ? "checked" : ""}> When it needs attention (a device or list is missing or unavailable)</label>`;
  }

  _optionsBody(d) {
    const roomId = d.room || null;
    return `${this._editorMode() === "simple" ? `<label>Linked device<select data-f="device_id">${this._deviceOptions(d.device_id, roomId)}</select></label>
        ${["time", "sunrise", "sunset"].includes(d.trigger) && !Number(d.random_minutes) ? `<label>Vary the time by up to (minutes, earlier or later)<input type="number" min="0" max="120" data-f="random_minutes" value="0"></label>` : ""}` : ""}
        <label class="check"><input type="checkbox" data-f="repeat" ${d.repeat ? "checked" : ""}> Run every time (not just once a day)</label>
        <label class="check"><input type="checkbox" data-f="skip_noop" ${d.skip_noop ? "checked" : ""}> Skip steps that are already done</label>
        ${EVENT_TRIGGERS.includes(d.trigger) || d.trigger === "room_clear" ? `<label>Not again within (minutes after it ran)<input type="number" min="0" max="1440" data-f="cooldown_minutes" value="${esc(d.cooldown_minutes || 0)}"></label>
        <p class="muted small">So a door opened and closed a few times, or a sensor bouncing around its level, doesn't run it each time. 0 runs it every time.</p>` : ""}
        ${this._catchUpField(d.catch_up_minutes)}
        <label>Retry a failed step (times)<input type="number" min="0" max="3" data-f="retries" value="${esc(d.retries)}"></label>
        ${(this._integrationList() || []).length ? `<label>For devices in more than one integration
          <select data-f="ownOrder" data-rerender>
            <option value="" ${d.integration_order == null ? "selected" : ""}>Use the order in Settings</option>
            <option value="1" ${d.integration_order != null ? "selected" : ""}>Its own order</option>
          </select>
        </label>
        ${d.integration_order != null ? this._orderRows(this._fullOrder(d.integration_order), "routine") : ""}` : ""}
        <label>If a step can't change a device
          <select data-f="on_step_error">
            <option value="stop" ${d.on_step_error !== "continue" ? "selected" : ""}>Stop there</option>
            <option value="continue" ${d.on_step_error === "continue" ? "selected" : ""}>Keep going with the other steps</option>
          </select>
        </label>
        <label>Try that device again later
          <select data-f="retry_later_minutes">${RETRY_LATER_CHOICES.map((m) => `<option value="${m}" ${Number(d.retry_later_minutes) === m ? "selected" : ""}>${m ? `After ${m} minutes (up to 3 times)` : "No"}</option>`).join("")}</select>
        </label>
        <label class="check"><input type="checkbox" data-f="wait_when_unavailable" ${d.wait_when_unavailable ? "checked" : ""}> If a to-do list or device is unavailable, wait for it to come back, then run (up to a day)</label>`;
  }

  _conditionRows(d) {
    return d.conditions.map((cond, i) => {
      const remove = `<button class="ghost" data-act="remove-condition" data-i="${i}">Remove</button>`;
      if (cond.kind === "presence") return this._presenceRow(d, cond, i, remove);
      if (cond.kind === "sensors") return this._sensorRow(cond, i, remove);
      if (cond.kind === "calendar") return this._calendarRow(cond, i, remove);
      if (cond.kind === "mode") return this._modeRow(cond, `data-ci="${i}"`, remove);
      if (cond.kind === "workday") return this._workdayRow(cond, `data-ci="${i}"`, remove);
      if (cond.kind === "charging") return this._chargingRow(cond, `data-ci="${i}"`, remove);
      if (cond.kind === "value") return this._valueRow(cond, `data-ci="${i}"`, remove);
      if (cond.kind === "weather") return this._weatherRow(cond, `data-ci="${i}"`, remove);
      if (cond.kind === "day_type") return this._dayTypeRow(cond, `data-ci="${i}"`, remove);
      if (cond.kind === "forecast") return this._forecastRow(cond, `data-ci="${i}"`, remove);
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

  // House mode, workday and weather rows, shared by the conditions and a step's
  // "only if" (attrs says which: data-ci="2" or data-oi="3").
  _modeRow(cond, attrs, remove) {
    const chips = MODES.map(([id, label]) => `<label class="chip"><input type="checkbox" ${attrs} data-cmode="${id}" ${(cond.modes || []).includes(id) ? "checked" : ""}> ${esc(label)}</label>`).join("");
    return `<div class="condition">
      ${remove ? `<div class="step-head"><strong>House mode</strong>${remove}</div>` : ""}
      <p class="muted small">When the house is in one of these modes.</p>
      <div class="chips">${chips}</div>
    </div>`;
  }

  _dayTypes() {
    return [...(this._data?.settings?.day_types || []).map((t) => t.name), NORMAL_DAY];
  }

  _forecastRow(cond, attrs, remove) {
    const weathers = this._data?.weathers || [];
    const options = `<option value="">Choose…</option>${weathers.map((e) => `<option value="${esc(e.entity_id)}" ${e.entity_id === cond.entity_id ? "selected" : ""}>${esc(e.name)}</option>`).join("")}`;
    const kinds = Object.entries(WEATHER_NAMES).map(([id, label]) => `<label class="check"><input type="checkbox" ${attrs} data-cweather="${id}" ${(cond.conditions || []).includes(id) ? "checked" : ""}> ${esc(label)}</label>`).join("");
    return `<div class="condition">
      ${remove ? `<div class="step-head"><strong>The forecast</strong>${remove}</div>` : ""}
      <div class="inline">
        <select ${attrs} data-cf="when" aria-label="When"><option value="not_coming" ${cond.when !== "coming" ? "selected" : ""}>Only when none of this is coming</option><option value="coming" ${cond.when === "coming" ? "selected" : ""}>Only when this is coming</option></select>
        <label>In the next (hours)<input type="number" min="1" max="48" ${attrs} data-cf="hours" value="${esc(cond.hours || 6)}"></label>
        <select ${attrs} data-cf="entity_id" aria-label="Weather">${options}</select>
      </div>
      <div class="checks weather-kinds">${kinds}</div>
      <label>Or the temperature below (optional)<input type="number" step="any" ${attrs} data-cf="below" value="${esc(cond.below ?? "")}"></label>
      <p class="muted small">For example skip watering, or keep the windows shut, when rain is coming. The forecast is read every half hour; until it's read, the routine waits.</p>
    </div>`;
  }

  _dayTypeRow(cond, attrs, remove) {
    const known = this._dayTypes();
    const picked = cond.types || [];
    const names = [...known, ...picked.filter((t) => !known.includes(t))];
    const chips = names.map((t) => `<label class="chip"><input type="checkbox" ${attrs} data-cdaytype="${esc(t)}" ${picked.includes(t) ? "checked" : ""}> ${esc(t)}</label>`).join("");
    return `<div class="condition">
      ${remove ? `<div class="step-head"><strong>Kind of day</strong>${remove}</div>` : ""}
      <div class="chips">${chips}</div>
      ${(this._data?.settings?.day_types || []).some((t) => t.person) ? `<label>Whose<select ${attrs} data-cf="person"><option value="">Everyone's</option>${this._data.people.filter((p) => !p.hidden).map((p) => `<option value="${esc(p.entity_id)}" ${cond.person === p.entity_id ? "selected" : ""}>${esc(p.name)}'s</option>`).join("")}</select></label>` : ""}
      <p class="muted small">${known.length > 1 ? "Only on these kinds of day." : "Set up kinds of day (School day, Holiday…) from your calendars under Settings → Kinds of day."} Today is ${esc(this._data?.day_type || "not known yet")}.</p>
    </div>`;
  }

  // Phones that report charging (the Home Assistant app's battery state).
  _chargers() {
    return (this._data.all_entities || []).filter((e) => /^(sensor|binary_sensor)\./.test(e.entity_id) && /battery_state|is_charging|charging/.test(e.entity_id));
  }

  _chargingRow(cond, attrs, remove) {
    const chargers = this._chargers();
    const known = new Set(chargers.map((e) => e.entity_id));
    const all = [...chargers, ...(cond.entities || []).filter((id) => !known.has(id)).map((id) => ({ entity_id: id, name: this._nameFor(id) }))];
    return `<div class="condition">
      <div class="step-head"><strong>Phones charging</strong>${remove}</div>
      <div class="checks">${all.map((e) => `<label class="check"><input type="checkbox" ${attrs} data-ccharge="${esc(e.entity_id)}" ${(cond.entities || []).includes(e.entity_id) ? "checked" : ""}> ${esc(e.name)}</label>`).join("") || `<p class="muted small">No phones found. The Home Assistant app on a phone adds a battery state sensor.</p>`}</div>
      <p class="muted small">Every phone ticked is on its charger. With a set time and <strong>Wait, and run as soon as they are</strong>, bedtime starts once everyone has plugged in.</p>
    </div>`;
  }

  _valueRow(cond, attrs, remove) {
    const sensors = this._data.sensors || [];
    const options = `<option value="">Choose…</option>${sensors.map((e) => `<option value="${esc(e.entity_id)}" ${e.entity_id === cond.entity_id ? "selected" : ""}>${esc(e.name)}${e.unit ? ` (${esc(e.unit)})` : ""}</option>`).join("")}`;
    return `<div class="condition">
      <div class="step-head"><strong>A sensor's level</strong>${remove}</div>
      <select ${attrs} data-cf="entity_id" aria-label="Sensor">${options}</select>
      <div class="inline"><label>Below (optional)<input type="number" step="any" ${attrs} data-cf="below" value="${esc(cond.below ?? "")}"></label>
      <label>Above (optional)<input type="number" step="any" ${attrs} data-cf="above" value="${esc(cond.above ?? "")}"></label></div>
      <p class="muted small">For example no watering when today's rain is above 5 mm. A sensor that isn't reporting blocks the run.</p>
    </div>`;
  }

  _workdayRow(cond, attrs, remove) {
    const sensors = this._data?.workday_sensors || [];
    const options = `<option value="">Choose…</option>${sensors.map((e) => `<option value="${esc(e.entity_id)}" ${e.entity_id === cond.entity_id ? "selected" : ""}>${esc(e.name)}</option>`).join("")}`;
    return `<div class="condition">
      ${remove ? `<div class="step-head"><strong>Workday or day off</strong>${remove}</div>` : ""}
      <div class="inline">
        <select ${attrs} data-cf="when">
          <option value="workday" ${cond.when !== "day_off" ? "selected" : ""}>On workdays</option>
          <option value="day_off" ${cond.when === "day_off" ? "selected" : ""}>On days off (weekends and holidays)</option></select>
        <select ${attrs} data-cf="entity_id" aria-label="Workday sensor">${options}</select>
      </div>
      <p class="muted small">${sensors.length ? "From Home Assistant's Workday integration, which knows your country's public holidays." : "Add the Workday integration to Home Assistant (Settings → Devices & services) to use this. It knows weekends and your country's public holidays."}</p>
    </div>`;
  }

  _weatherRow(cond, attrs, remove) {
    const weathers = this._data?.weathers || [];
    const options = `<option value="">Choose…</option>${weathers.map((e) => `<option value="${esc(e.entity_id)}" ${e.entity_id === cond.entity_id ? "selected" : ""}>${esc(e.name)}</option>`).join("")}`;
    const test = cond.test || "is";
    const kinds = Object.entries(WEATHER_NAMES).map(([id, label]) => `<label class="check"><input type="checkbox" ${attrs} data-cweather="${id}" ${(cond.conditions || []).includes(id) ? "checked" : ""}> ${esc(label)}</label>`).join("");
    return `<div class="condition">
      ${remove ? `<div class="step-head"><strong>Weather</strong>${remove}</div>` : ""}
      <label>Weather<select ${attrs} data-cf="entity_id">${options}</select></label>
      <label>When<select ${attrs} data-cf="test" data-rerender>
        <option value="is" ${test === "is" ? "selected" : ""}>It's one of these</option>
        <option value="is_not" ${test === "is_not" ? "selected" : ""}>It isn't any of these</option>
        <option value="above" ${test === "above" ? "selected" : ""}>The temperature is above</option>
        <option value="below" ${test === "below" ? "selected" : ""}>The temperature is below</option></select></label>
      ${test === "is" || test === "is_not" ? `<div class="checks">${kinds}</div>` : `<label>Temperature<input type="number" step="any" ${attrs} data-cf="value" value="${esc(cond.value ?? "")}"></label>`}
      ${weathers.length ? "" : `<p class="muted small">Add a weather integration (such as Met.no) to Home Assistant to use this.</p>`}
    </div>`;
  }

  // Other triggers for the same routine. Its days, hours, conditions and steps are shared.
  _moreTriggers(d, heading = true) {
    const rows = (d.more_triggers || []).map((t, i) => this._moreTriggerRow(d, t, i)).join("");
    const add = (d.more_triggers || []).length < MAX_MORE_TRIGGERS
      ? `<button class="ghost" data-act="add-more-trigger">+ Or also when…</button>` : "";
    return `${heading ? "<h4>Or also when</h4>" : ""}
      <p class="muted small">More things that start this routine, such as “at 22:00 or when everyone leaves”. They share the days, hours, conditions and steps.</p>
      ${rows}<div class="row-actions">${add}</div>`;
  }

  _moreTriggerRow(d, t, i) {
    const a = `data-xi="${i}"`;
    const kinds = MORE_TRIGGER_KINDS.map((k) => `<option value="${k}" ${t.trigger === k ? "selected" : ""}>${esc(TRIGGER_LABELS[k])}</option>`).join("");
    let fields = "";
    if (t.trigger === "time") fields = `<label>Time<input type="time" ${a} data-xf="time" value="${esc(hhmm(t.time || "07:00:00"))}"></label>`;
    else if (t.trigger === "sunrise" || t.trigger === "sunset") fields = `<label>Offset in minutes (negative is before)<input type="number" min="-720" max="720" ${a} data-xf="offset_minutes" value="${esc(t.offset_minutes || 0)}"></label>`;
    else if (PRESENCE_TRIGGERS.includes(t.trigger)) {
      const zones = this._data?.zones || [{ entity_id: "zone.home", name: "Home" }];
      const people = this._pickerPeople(d).map((p) => `<label class="check"><input type="checkbox" ${a} data-xpeople="${esc(p.entity_id)}" ${(t.people || []).includes(p.entity_id) ? "checked" : ""}> ${esc(p.name)}</label>`).join("");
      fields = `<label>Place<select ${a} data-xf="zone">${zones.map((z) => `<option value="${esc(z.entity_id)}" ${z.entity_id === (t.zone || "zone.home") ? "selected" : ""}>${esc(z.name)}</option>`).join("")}</select></label>
        <div class="checks">${people || `<p class="muted small">No people are turned on for routines. Turn them on under Settings.</p>`}</div>`;
    } else if (VALUE_TRIGGERS.includes(t.trigger)) {
      const sensors = this._data.sensors || [];
      fields = `<div class="inline"><select ${a} data-xf="value_entity" aria-label="Sensor"><option value="">Choose a sensor…</option>${sensors.map((x) => `<option value="${esc(x.entity_id)}" ${x.entity_id === t.value_entity ? "selected" : ""}>${esc(x.name)}${x.unit ? ` (${esc(x.unit)})` : ""}</option>`).join("")}</select>
        <input type="number" step="any" ${a} data-xf="value_threshold" value="${esc(t.value_threshold ?? "")}" aria-label="Level" placeholder="level"></div>`;
    } else if (t.trigger === "state_is") {
      fields = `<label>Device to watch<select ${a} data-xf="watch_entity">${this._anyEntityOptions(t.watch_entity)}</select></label>
        <label>Changes to<input ${a} data-xf="watch_states" value="${esc(t.watch_states || "")}" placeholder="for example on or locked"></label>
        <label>Hold for (minutes)<input type="number" min="0" max="1440" ${a} data-xf="hold_minutes" value="${esc(t.hold_minutes || 0)}"></label>`;
    } else if (t.trigger === "alarm") {
      const alarms = this._data.alarm_sensors || [];
      fields = `<label>Alarm<select ${a} data-xf="watch_entity"><option value="">Choose…</option>${alarms.map((x) => `<option value="${esc(x.entity_id)}" ${x.entity_id === t.watch_entity ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label>
        <label>Minutes before (negative) or after<input type="number" min="-720" max="720" ${a} data-xf="offset_minutes" value="${esc(t.offset_minutes || 0)}"></label>`;
    } else if (t.trigger === "calendar_event") {
      fields = `<label>Calendar<select ${a} data-xf="watch_entity"><option value="">Choose…</option>${this._calendars().map((x) => `<option value="${esc(x.entity_id)}" ${x.entity_id === t.watch_entity ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label>
        <label>Events with this in the title<input ${a} data-xf="watch_item" value="${esc(t.watch_item || "")}"></label>
        <label>Minutes before (negative) or after<input type="number" min="-720" max="720" ${a} data-xf="offset_minutes" value="${esc(t.offset_minutes || 0)}"></label>`;
    } else if (t.trigger === "mode_changes") {
      fields = `<div class="chips">${MODES.map(([id, label]) => `<label class="chip"><input type="checkbox" ${a} data-xmode="${id}" ${(t.modes || []).includes(id) ? "checked" : ""}> ${esc(label)}</label>`).join("")}</div>`;
    }
    return `<div class="condition">
      <div class="step-head"><strong>Or</strong><button class="ghost" data-act="remove-more-trigger" data-i="${i}">Remove</button></div>
      <select ${a} data-xf="trigger" data-rerender aria-label="Trigger">${kinds}</select>
      ${fields}
    </div>`;
  }

  // A step's "only if": the step runs only when this holds; the rest of the routine still runs.
  _onlyIf(step, i) {
    const cond = step.onlyIf || {};
    const a = `data-oi="${i}"`;
    const kinds = ONLY_IF_KINDS.map(([id, label]) => `<option value="${id}" ${(cond.kind || "") === id ? "selected" : ""}>${esc(label)}</option>`).join("");
    let body = "";
    if (cond.kind === "mode") body = this._modeRow(cond, a, "");
    else if (cond.kind === "workday") body = this._workdayRow(cond, a, "");
    else if (cond.kind === "weather") body = this._weatherRow(cond, a, "");
    else if (cond.kind === "day_type") body = this._dayTypeRow(cond, a, "");
    else if (cond.kind === "forecast") body = this._forecastRow(cond, a, "");
    else if (cond.kind === "state") {
      body = `<div class="inline"><select ${a} data-cf="entity_id" aria-label="Device">${this._anyEntityOptions(cond.entity_id)}</select>
        <input ${a} data-cf="state" value="${esc(cond.state || "")}" placeholder="state, e.g. closed" aria-label="State"></div>`;
    }
    return `<div class="only-if"><label>Only if<select ${a} data-cf="kind" data-rerender>${kinds}</select></label>${body}</div>`;
  }

  _calendars() {
    return (this._data?.all_entities || []).filter((e) => e.entity_id.startsWith("calendar."));
  }

  _calendarRow(cond, i, remove) {
    const calendars = this._calendars();
    const options = `<option value="">Choose a calendar…</option>${calendars.map((c) => `<option value="${esc(c.entity_id)}" ${c.entity_id === cond.entity_id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}`;
    return `<div class="condition">
      <div class="step-head"><strong>Calendar day</strong>${remove}</div>
      <div class="inline">
        <select data-ci="${i}" data-cf="when">
          <option value="no_event" ${cond.when !== "event" ? "selected" : ""}>Not on days with</option>
          <option value="event" ${cond.when === "event" ? "selected" : ""}>Only on days with</option></select>
        <input data-ci="${i}" data-cf="match" value="${esc(cond.match || "")}" placeholder="an event called… (empty: any event)" aria-label="Event name">
      </div>
      <label>In<select data-ci="${i}" data-cf="entity_id">${options}</select></label>
      <p class="muted small">${calendars.length ? "For example, not on days with “No school” in the school calendar, or only on days with “Trash” in the household calendar. The name matches anywhere in the event's title." : "Add a calendar to Home Assistant (such as Local Calendar or Google Calendar) to use this."}</p>
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

  // A step folds to one line; tap it to open it, drag it to move it.
  _stepEditor(step, i, roomId) {
    const summary = this._stepSummary(step);
    const together = i > 0 && step.withPrev ? `<span class="together-note">At the same time as step ${i}</span>` : "";
    const head = `<div class="fold-head"><span class="grip" aria-hidden="true" title="Drag to move">⠿</span>
      <button class="fold-toggle" data-act="toggle-step" data-i="${i}" aria-expanded="${step._open ? "true" : "false"}">
        <span class="num">${i + 1}</span><span class="what">${esc(summary)}${together}</span><span class="chev" aria-hidden="true">${step._open ? "▾" : "▸"}</span></button></div>`;
    const withPrev = i > 0 && step._open && !["delay", "wait_until"].includes(step.kind)
      ? `<label class="check"><input type="checkbox" data-si="${i}" data-sf="withPrev" ${step.withPrev ? "checked" : ""}> At the same time as the step before</label>` : "";
    return `<li class="step-fold ${step._open ? "open" : ""}" draggable="true" data-step="${i}">${head}
      ${step._open ? `${withPrev}${this._stepBody(step, i, roomId)}` : ""}</li>`;
  }

  // One step in a few words, for its folded line.
  _stepSummary(step) {
    const names = (ids) => {
      const list = ids.map((id) => this._nameFor(id));
      return list.length > 2 ? `${list.slice(0, 2).join(", ")} and ${list.length - 2} more` : list.join(" and ");
    };
    let text;
    if (step.kind === "delay") {
      const sec = Number(step.seconds) || 0;
      text = sec >= 60 ? `Wait ${Math.round(sec / 6) / 10} minutes` : `Wait ${sec} seconds`;
    } else if (step.kind === "wait_until") {
      const level = [step.wBelow !== "" && step.wBelow != null ? `below ${step.wBelow}` : "", step.wAbove !== "" && step.wAbove != null ? `above ${step.wAbove}` : ""].filter(Boolean).join(" and ");
      text = step.wEntity ? `Wait until ${this._nameFor(step.wEntity)} is ${step.wBy === "level" ? level || "…" : step.wStates || "…"}${Number(step.wFor) ? ` for ${step.wFor} min` : ""}` : "Wait until a device changes";
    } else if (step.kind === "scene") {
      const count = step.entities.filter((id) => step.saved?.[id]).length;
      text = count ? `Make ${count} ${count === 1 ? "device" : "devices"} look as saved` : "Make it look like this (nothing saved yet)";
    }
    else if (step.kind === "routine") text = `Run ${this._data.routines.find((r) => r.id === step.routine)?.name || "another routine"}`;
    else if (step.kind === "mode") text = `Set the house to ${modeName(step.mode || "home")}`;
    else if (step.kind === "briefing") text = step.bSpeaker ? `Say the day ahead on ${this._nameFor(step.bSpeaker)}` : "Say the day ahead";
    else if (step.kind === "battery") text = `Tell me about batteries under ${step.bBelow ?? 20}%`;
    else if (step.kind === "ticks") {
      const count = String(step.tItems || "").split("\n").filter((x) => x.trim()).length;
      text = count ? `Wait for ${count} ${count === 1 ? "thing" : "things"} to be ticked off` : "Wait for a checklist";
    } else if (step.kind === "check_open") text = step.entities?.length ? `Tell me if any of ${step.entities.length} doors and windows is open` : "Check doors and windows";
    else if (step.kind === "cheaper") text = step.cEntity ? `Wait for the cheapest electricity in the next ${Math.round((Number(step.cMinutes) || 180) / 60 * 10) / 10} hours` : "Wait for cheaper electricity";
    else if (step.kind === "ramp") text = step.entities?.length
      ? `Volume of ${names(step.entities)} from ${step.rFrom ?? 5}% to ${step.rTo ?? 30}% over ${step.rMinutes || 10} min`
      : "Turn the volume up slowly";
    else if (step.kind === "todo") {
      const op = { add: "Add", complete: "Check off", remove: "Remove" }[step.todoOp] || "Change";
      text = `${op} “${step.todoItem || "…"}”${step.todoList ? ` on ${this._nameFor(step.todoList)}` : ""}`;
    } else {
      const spec = actionSpec(step.service);
      const label = spec?.label || step.service || "Choose an action";
      let where = "";
      if (step.targetMode === "area" && step.area) where = this._area(step.area)?.name || step.area;
      else if (step.targetMode === "all_areas") where = "every room";
      else if (step.targetMode === "entities" && step.entities.length) where = names(step.entities);
      const bits = Object.entries(step.fields || {}).filter(([, v]) => v !== "" && v != null).map(([k, v]) => {
        const f = spec?.fields?.find((x) => x.key === k);
        if (!f) return "";
        if (f.type === "pct" || f.type === "percent") return `${v}%`;
        if (f.type === "color") return "color";
        if (f.type === "kelvin") return `${v} K`;
        if (f.type === "minutes") return `over ${v} min`;
        return `${f.label.toLowerCase()} ${v}`;
      }).filter(Boolean);
      text = [label, where].filter(Boolean).join(": ") + (bits.length ? ` · ${bits.join(", ")}` : "");
    }
    if (step.onlyIf?.kind) text += " · only if…";
    return text;
  }

  _stepBody(step, i, roomId) {
    const last = this._draft.actions.length - 1;
    const head = `<div class="step-head"><strong>Step ${i + 1}</strong>
      <div class="row-actions">
        <button class="ghost" data-act="move-step" data-i="${i}" data-dir="-1" ${i === 0 ? "disabled" : ""}>↑</button>
        <button class="ghost" data-act="move-step" data-i="${i}" data-dir="1" ${i === last ? "disabled" : ""}>↓</button>
        <button class="ghost" data-act="remove-step" data-i="${i}">Remove</button></div></div>`;
    const kind = `<label>Type<select data-si="${i}" data-sf="kind" data-rerender>
      <option value="service" ${step.kind === "service" ? "selected" : ""}>Action</option>
      <option value="todo" ${step.kind === "todo" ? "selected" : ""}>To-do list</option>
      <option value="delay" ${step.kind === "delay" ? "selected" : ""}>Wait (seconds)</option>
      <option value="wait_until" ${step.kind === "wait_until" ? "selected" : ""}>Wait until a device changes</option>
      <option value="routine" ${step.kind === "routine" ? "selected" : ""}>Run another routine</option>
      <option value="mode" ${step.kind === "mode" ? "selected" : ""}>Set the house mode</option>
      <option value="scene" ${step.kind === "scene" ? "selected" : ""}>Make it look like this</option>
      <option value="ramp" ${step.kind === "ramp" ? "selected" : ""}>Turn the volume up slowly</option>
      <option value="cheaper" ${step.kind === "cheaper" ? "selected" : ""}>Wait for cheaper electricity</option>
      <option value="briefing" ${step.kind === "briefing" ? "selected" : ""}>Say the day ahead</option>
      <option value="check_open" ${step.kind === "check_open" ? "selected" : ""}>Check doors and windows</option>
      <option value="battery" ${step.kind === "battery" ? "selected" : ""}>Check batteries</option>
      <option value="ticks" ${step.kind === "ticks" ? "selected" : ""}>Wait for a checklist</option></select></label>`;
    const onlyIf = this._onlyIf(step, i);
    if (step.kind === "scene") {
      step.saved ||= {};
      const domains = ["light", "switch", "fan", "cover", "climate", "media_player", "input_boolean", "lock", "valve"];
      const room = (this._areas().find((a) => a.id === this._draft.room)?.entities || []).filter((e) => domains.includes(e.domain));
      const roomIds = new Set(room.map((e) => e.entity_id));
      const others = (this._data.all_entities || []).filter((e) => domains.includes(e.entity_id.split(".")[0]) && !roomIds.has(e.entity_id));
      const saved = step.entities.filter((id) => step.saved[id]);
      return `<div class="step">${head}${kind}
        <p class="muted small">Pick the devices, set them how you want them (with the Home Assistant app, or by hand), then press <strong>Save how they look now</strong>. The routine puts them back exactly like that: on or off, brightness, color, position, temperature.</p>
        <div class="checks">${[...room, ...others].map((e) => checkbox("sentity", e.entity_id, e.name + (roomIds.has(e.entity_id) ? " (this room)" : ""), step.entities.includes(e.entity_id), i)).join("")}</div>
        <div class="row-actions"><button class="primary" data-act="scene-capture" data-i="${i}">Save how they look now</button></div>
        ${saved.length ? `<ul class="plain small saved-look">${saved.map((id) => `<li><strong>${esc(this._nameFor(id))}</strong>: ${esc(lookText(step.saved[id]))}</li>`).join("")}</ul>` : `<p class="muted small">Nothing saved yet.</p>`}
        <label>Fade over (minutes, lights only, optional)<input type="number" min="0" max="60" data-si="${i}" data-sf="transition" value="${esc(step.transition ?? "")}"></label>
        ${onlyIf}</div>`;
    }
    if (step.kind === "briefing") {
      step.entities ||= [];
      const all = this._data.all_entities || [];
      const opts = (prefix, selected, none) => `${none ? `<option value="">${esc(none)}</option>` : `<option value="">Choose…</option>`}${all.filter((e) => e.entity_id.startsWith(prefix)).map((e) => `<option value="${esc(e.entity_id)}" ${e.entity_id === selected ? "selected" : ""}>${esc(e.name)}</option>`).join("")}`;
      return `<div class="step">${head}${kind}
        <p class="muted small">Says good morning, the kind of day, the weather and today's events on a speaker: “Good morning. It's a school day. Right now it's cloudy and 8°, up to 12 today. Today: Dentist at 15:30.”</p>
        <div class="inline"><label>Voice<select data-si="${i}" data-sf="bVoice">${opts("tts.", step.bVoice)}</select></label>
        <label>Speaker<select data-si="${i}" data-sf="bSpeaker">${opts("media_player.", step.bSpeaker)}</select></label></div>
        <label>Weather<select data-si="${i}" data-sf="bWeather">${opts("weather.", step.bWeather, "No weather")}</select></label>
        <h4>Today's events from</h4>
        <div class="checks">${this._calendars().map((e) => checkbox("sentity", e.entity_id, e.name, step.entities.includes(e.entity_id), i)).join("") || `<p class="muted small">No calendars found.</p>`}</div>
        <label class="check"><input type="checkbox" data-si="${i}" data-sf="bDay" ${step.bDay !== false ? "checked" : ""}> Say the kind of day (from Settings → Kinds of day)</label>
        ${onlyIf}</div>`;
    }
    if (step.kind === "battery") {
      return `<div class="step">${head}${kind}
        <p class="muted small">Looks at every battery in the house and sends one notification naming those running low (“Low batteries: Back door 12%, Smoke alarm (low)”). Nothing is sent when they're all fine. Good as a weekly routine.</p>
        <label>Tell me about batteries under (%)<input type="number" min="1" max="99" data-si="${i}" data-sf="bBelow" value="${esc(step.bBelow ?? 20)}"></label>
        ${onlyIf}</div>`;
    }
    if (step.kind === "ticks") {
      return `<div class="step">${head}${kind}
        <p class="muted small">Shows a checklist on the routine's card, the Today tab and the dashboard card, and waits until everything is ticked off, such as a bedtime check: back door locked, stove off, kids' tablets charging.</p>
        <label>Things to tick off, one on each line<textarea rows="4" data-si="${i}" data-sf="tItems">${esc(step.tItems || "")}</textarea></label>
        <div class="inline"><label>Wait up to (minutes)<input type="number" min="1" max="240" data-si="${i}" data-sf="tMinutes" value="${esc(step.tMinutes ?? 30)}"></label>
        <label>If it isn't done by then<select data-si="${i}" data-sf="tTimeout"><option value="continue" ${step.tTimeout !== "stop" ? "selected" : ""}>Go on with the rest</option><option value="stop" ${step.tTimeout === "stop" ? "selected" : ""}>Stop the routine</option></select></label></div>
        ${onlyIf}</div>`;
    }
    if (step.kind === "check_open") {
      step.entities ||= [];
      const openings = this._openings();
      const known = new Set(openings.map((e) => e.entity_id));
      const extra = step.entities.filter((id) => !known.has(id)).map((id) => ({ entity_id: id, name: this._nameFor(id) }));
      return `<div class="step">${head}${kind}
        <p class="muted small">Looks at these and sends one notification naming anything still open, unlocked or on (“Still open: Back door, Front door (unlocked)”). A sensor that isn't reporting is named too. Nothing is sent when it's all shut.</p>
        <div class="checks">${[...openings, ...extra].map((e) => checkbox("sentity", e.entity_id, e.name, step.entities.includes(e.entity_id), i)).join("") || `<p class="muted small">No doors, windows or locks found.</p>`}</div>
        <label class="check"><input type="checkbox" data-si="${i}" data-sf="cStop" ${step.cStop ? "checked" : ""}> Stop the routine when something is open</label>
        ${onlyIf}</div>`;
    }
    if (step.kind === "cheaper") {
      const sensors = (this._data.sensors || []).slice().sort((a, b) => Number(/kwh/i.test(b.unit || "")) - Number(/kwh/i.test(a.unit || "")));
      return `<div class="step">${head}${kind}
        <p class="muted small">Waits until the cheapest hour (or quarter hour) of the next few hours starts, then goes on with the next steps, such as starting the dishwasher. With no prices it goes on straight away.</p>
        <label>Electricity price<select data-si="${i}" data-sf="cEntity"><option value="">Choose…</option>${sensors.map((x) => `<option value="${esc(x.entity_id)}" ${x.entity_id === step.cEntity ? "selected" : ""}>${esc(x.name)}${x.unit ? ` (${esc(x.unit)})` : ""}</option>`).join("")}</select></label>
        <label>Wait up to (minutes)<input type="number" min="15" max="720" step="15" data-si="${i}" data-sf="cMinutes" value="${esc(step.cMinutes || 180)}"></label>
        ${onlyIf}</div>`;
    }
    if (step.kind === "ramp") {
      step.entities ||= [];
      const players = (this._data.all_entities || []).filter((e) => e.entity_id.startsWith("media_player."));
      return `<div class="step">${head}${kind}
        <p class="muted small">Changes the volume a little at a time, for waking up gently or winding down. Start the music first (with an action step) and the speakers go from one level to the other.</p>
        <div class="checks">${players.map((e) => checkbox("sentity", e.entity_id, e.name, step.entities.includes(e.entity_id), i)).join("") || `<p class="muted small">No speakers found.</p>`}</div>
        <div class="inline">
          <label>From (%)<input type="number" min="0" max="100" step="5" data-si="${i}" data-sf="rFrom" value="${esc(step.rFrom ?? 5)}"></label>
          <label>To (%)<input type="number" min="0" max="100" step="5" data-si="${i}" data-sf="rTo" value="${esc(step.rTo ?? 30)}"></label>
          <label>Over (minutes)<input type="number" min="1" max="60" data-si="${i}" data-sf="rMinutes" value="${esc(step.rMinutes || 10)}"></label>
        </div>
        ${onlyIf}</div>`;
    }
    if (step.kind === "routine") {
      const others = (this._data.routines || []).filter((r) => r.id !== this._draft.id && r.kind !== "todo" && !r.companion_of);
      return `<div class="step">${head}${kind}
        <label>Routine<select data-si="${i}" data-sf="routine"><option value="">Choose a routine…</option>${others.map((r) => `<option value="${esc(r.id)}" ${r.id === step.routine ? "selected" : ""}>${esc(r.name)}</option>`).join("")}</select></label>
        <p class="muted small">Runs it as if you pressed Run now (its conditions still apply), and waits for it to finish before the next step.</p>
        ${onlyIf}</div>`;
    }
    if (step.kind === "mode") {
      return `<div class="step">${head}${kind}
        <label>Mode<select data-si="${i}" data-sf="mode">${MODES.map(([id, label]) => `<option value="${id}" ${(step.mode || "home") === id ? "selected" : ""}>${esc(label)}</option>`).join("")}</select></label>
        ${onlyIf}</div>`;
    }
    if (step.kind === "wait_until") {
      return `<div class="step">${head}${kind}
        <label>Device<select data-si="${i}" data-sf="wEntity">${this._anyEntityOptions(step.wEntity || "")}</select></label>
        <label>Wait for<select data-si="${i}" data-sf="wBy" data-rerender>
          <option value="state" ${step.wBy !== "level" ? "selected" : ""}>A state (closed, off…)</option>
          <option value="level" ${step.wBy === "level" ? "selected" : ""}>A level (power, temperature…)</option></select></label>
        ${step.wBy === "level"
          ? `<div class="inline"><label>Below<input type="number" step="any" data-si="${i}" data-sf="wBelow" value="${esc(step.wBelow ?? "")}" placeholder="5"></label>
             <label>Above<input type="number" step="any" data-si="${i}" data-sf="wAbove" value="${esc(step.wAbove ?? "")}"></label></div>
             <p class="muted small">For example the dishwasher's power below 5 W means it has finished.</p>`
          : `<label>Until it's<input data-si="${i}" data-sf="wStates" value="${esc(step.wStates || "")}" placeholder="for example closed, or off"></label>`}
        <label>And it stays so for (minutes, 0: straight away)<input type="number" min="0" max="240" data-si="${i}" data-sf="wFor" value="${esc(step.wFor || 0)}"></label>
        <label>Wait up to (minutes)<input type="number" min="1" max="240" data-si="${i}" data-sf="wMinutes" value="${esc(step.wMinutes || 10)}"></label>
        <label>If it doesn't happen in time<select data-si="${i}" data-sf="wTimeout">
          <option value="continue" ${step.wTimeout !== "stop" ? "selected" : ""}>Go on with the next steps</option>
          <option value="stop" ${step.wTimeout === "stop" ? "selected" : ""}>Stop the routine there</option></select></label>
        ${onlyIf}</div>`;
    }
    if (step.kind === "todo") {
      const ops = Object.entries(TODO_VERBS).map(([value, label]) =>
        `<option value="${value}" ${(step.todoOp || "add") === value ? "selected" : ""}>${esc(label)} an item</option>`).join("");
      return `<div class="step">${head}${kind}
        <label>To-do action<select data-si="${i}" data-sf="todoOp" data-rerender>${ops}</select></label>
        <label>To-do list<select data-si="${i}" data-sf="todoList">${this._pickerOptions(this._data.todo_lists || [], step.todoList || "", "Choose a to-do list…")}</select></label>
        <label>Item text<input data-si="${i}" data-sf="todoItem" value="${esc(step.todoItem || "")}" placeholder="for example Empty dishwasher"></label>
        ${onlyIf}
        ${(step.todoOp || "add") === "add" ? "" : `<label>Only when due<select data-si="${i}" data-sf="todoDue" data-rerender>${dueOptions(step.todoDue || "any")}</select></label>
        ${(step.todoDue || "any") === "any" ? "" : `<label class="check"><input type="checkbox" data-si="${i}" data-sf="todoOverdue" ${step.todoOverdue !== false ? "checked" : ""}> Also when it is overdue</label>
        <label class="check"><input type="checkbox" data-si="${i}" data-sf="todoUndated" ${step.todoUndated ? "checked" : ""}> Also when it has no due date</label>`}`}
      </div>`;
    }
    if (step.kind === "delay") {
      return `<div class="step">${head}${kind}<label>Seconds<input type="number" min="0" step="1" data-si="${i}" data-sf="seconds" value="${esc(step.seconds)}"></label></div>`;
    }
    const domain = step.service.includes(".") ? step.service.split(".")[0] : "";
    const domains = actionSpec(step.service)?.domains;
    const candidates = this._roomEntities(roomId).filter((e) => (domains ? domains.includes(e.domain) : !domain || e.domain === domain));
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
    const spec = actionSpec(step.service);
    const allEntities = this._data?.all_entities || [];
    const pick = (domain, selected, attrs, label) => {
      const options = allEntities.filter((e) => e.entity_id.startsWith(`${domain}.`));
      return `<label>${esc(label)}<select ${attrs}><option value="">Choose…</option>${options.map((e) => `<option value="${esc(e.entity_id)}" ${e.entity_id === selected ? "selected" : ""}>${esc(e.name)}</option>`).join("")}</select></label>`;
    };
    const values = step.fields || {};
    const fields = (spec?.fields || []).map((f) => {
      const attrs = `data-sfi="${i}" data-sfield="${esc(f.key)}"`;
      const value = values[f.key] ?? "";
      const opt = f.optional ? " – optional" : "";
      const help = f.help ? `<span class="muted small">${esc(f.help)}</span>` : "";
      if (f.type === "entity") return pick(f.domain, value, attrs, f.label);
      if (f.type === "percent" || f.type === "pct") return `<label>${esc(f.label)} (%)${opt}<input type="number" min="0" max="100" step="5" ${attrs} value="${esc(value)}"></label>`;
      if (f.type === "minutes") return `<label>${esc(f.label)} – optional<input type="number" min="0" max="120" step="0.5" ${attrs} value="${esc(value)}"></label>`;
      if (f.type === "kelvin") return `<label>${esc(f.label)} (K)${opt}<input type="number" min="1500" max="9000" step="100" ${attrs} value="${esc(value)}" placeholder="2700 warm · 6500 cool"></label>`;
      if (f.type === "color") return `<label>${esc(f.label)}${opt}<span class="inline"><input type="color" ${attrs} value="${esc(value || "#ffffff")}"> ${value ? `<button class="ghost" data-act="clear-field" data-i="${i}" data-key="${esc(f.key)}">No color</button>` : `<span class="muted small">not set</span>`}</span></label>`;
      if (f.type === "bool") return `<label>${esc(f.label)}${opt}<select ${attrs}><option value="" ${value === "" ? "selected" : ""}>Leave as it is</option><option value="true" ${value === true || value === "true" ? "selected" : ""}>Yes</option><option value="false" ${value === false || value === "false" ? "selected" : ""}>No</option></select>${help}</label>`;
      if (f.type === "select" && !f.multiple) return `<label>${esc(f.label)}${opt}<select ${attrs}><option value="">${f.optional ? "Leave as it is" : "Choose…"}</option>${f.options.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>${help}</label>`;
      if (f.type === "time") return `<label>${esc(f.label)}${opt}<input type="time" ${attrs} value="${esc(value)}">${help}</label>`;
      if (f.type === "number") return `<label>${esc(f.label)}${f.unit ? ` (${esc(f.unit)})` : ""}${opt}<input type="number" step="${esc(f.step || 1)}" ${f.min !== undefined ? `min="${esc(f.min)}"` : ""} ${f.max !== undefined ? `max="${esc(f.max)}"` : ""} ${attrs} value="${esc(value)}">${help}</label>`;
      return `<label>${esc(f.label)}${opt}<input ${attrs} value="${esc(Array.isArray(value) ? value.join(", ") : value)}" placeholder="${esc(f.placeholder || "")}">${help}</label>`;
    }).join("");
    const voice = spec?.voice ? pick("tts", step.voice || "", `data-si="${i}" data-sf="voice"`, "Voice") : "";
    const roomFilter = ["area", "all_areas"].includes(step.targetMode)
      ? `<label>In<select data-si="${i}" data-sf="rooms">${ROOM_FILTERS.map(([v, l]) => `<option value="${v}" ${(step.rooms || "") === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>
        <span class="muted small">By the rooms' motion, occupancy or presence sensors. A room without any is left alone.</span></label>`
      : "";
    const target = spec?.voice || spec?.untargeted ? "" : `<label>Applies to<select data-si="${i}" data-sf="targetMode" data-rerender>${targetModes}</select></label>
      ${roomFilter}${entityChecks}`;
    return `<div class="step">${head}${kind}
      <label>Action<select data-si="${i}" data-sf="actionChoice" data-rerender>${actionOptions}</select></label>
      ${customField}
      ${voice}
      ${target}
      ${fields}
      ${step.data && step.data.trim() ? `<details class="leftover"><summary class="muted small">Other settings this step has</summary>
        <p class="muted small">Settings without a field of their own, kept as they are. Clear the box to remove them.</p>
        <textarea data-si="${i}" data-sf="data" rows="2">${esc(step.data)}</textarea></details>` : ""}
      ${onlyIf}
    </div>`;
  }

  // Every entity in Home Assistant, not only the ones in a room.
  _anyEntityOptions(selected) {
    const all = this._data?.all_entities || [];
    return `<option value="">Choose…</option>${all.map((e) => `<option value="${esc(e.entity_id)}" ${e.entity_id === selected ? "selected" : ""}>${esc(e.name)}</option>`).join("")}`;
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

// How a device looks now, for "Make it look like this" (scene.apply).
const LOOK_ATTRIBUTES = ["brightness", "color_temp_kelvin", "rgb_color", "current_position", "current_tilt_position", "percentage", "temperature", "target_temp_low", "target_temp_high", "preset_mode", "volume_level", "source"];
function captureLook(state) {
  const look = { state: state.state };
  for (const key of LOOK_ATTRIBUTES) if (state.attributes?.[key] != null) look[key] = state.attributes[key];
  return look;
}
function lookText(look) {
  const parts = [look.state];
  if (look.brightness != null) parts.push(`${Math.round((look.brightness / 255) * 100)}%`);
  if (look.color_temp_kelvin != null) parts.push(`${look.color_temp_kelvin} K`);
  if (look.rgb_color) parts.push(`color ${look.rgb_color.join(", ")}`);
  if (look.current_position != null) parts.push(`${look.current_position}% open`);
  if (look.temperature != null) parts.push(`${look.temperature}°`);
  return parts.join(", ");
}

// A routine as a code to share: base64 JSON, with its rooms' names.
// Several routines (a group) in one code: "DR2:".
function groupShareCode(routines, areas) {
  const parts = routines.map((r) => JSON.parse(decodeURIComponent(escape(atob(shareCode(r, areas).slice(4))))));
  const names = Object.assign({}, ...parts.map((p) => p.areas));
  return `DR2:${btoa(unescape(encodeURIComponent(JSON.stringify({ v: 2, routines: parts.map((p) => p.routine), areas: names }))))}`;
}

function shareCode(routine, areas) {
  const keep = { ...routine };
  for (const key of ["id", "device_id", "paused_until", "skip_next", "companion_of", "kind"]) delete keep[key];
  const used = new Set([routine.room, ...JSON.stringify(routine.actions).match(/"area_id":\[[^\]]*\]/g)?.flatMap((m) => JSON.parse(m.slice(10))) || []].filter(Boolean));
  const names = Object.fromEntries(areas.filter((a) => used.has(a.id)).map((a) => [a.id, a.name]));
  return `DR1:${btoa(unescape(encodeURIComponent(JSON.stringify({ v: 1, routine: keep, areas: names }))))}`;
}

// A run's steps, replayed: what each did, how long it took, how devices ended up.
const REPLAY_ICONS = { ok: "✓", skipped: "↷", failed: "✗", stopped: "■" };
function replayHtml(steps) {
  return `<ol class="replay">${steps.map((st) => `<li class="r-${esc(st.result)}"><span class="r-icon" aria-hidden="true">${REPLAY_ICONS[st.result] || "•"}</span><span><strong>${esc(st.label)}</strong> <span class="muted small">${st.seconds >= 1 ? `${Math.round(st.seconds)} s` : ""}</span>${st.note ? `<div class="small">${esc(st.note)}</div>` : ""}${(st.devices || []).length ? `<div class="muted small">${st.devices.map((d) => `${esc(d.name)}: ${esc(d.state)}`).join(" · ")}</div>` : ""}</span></li>`).join("")}</ol>`;
}

function checkbox(list, value, label, checked, stepIndex) {
  const attrs = stepIndex !== undefined
    ? `data-sentity="${stepIndex}" data-val="${esc(value)}"`
    : `data-list="${list}" data-val="${esc(value)}"`;
  return `<label class="check"><input type="checkbox" ${attrs} ${checked ? "checked" : ""}> ${esc(label)}</label>`;
}

const STYLES = `<style>
  ol.replay { list-style: none; padding: 0; margin: 6px 0; }
  ol.replay li { display: flex; gap: 8px; align-items: flex-start; padding: 4px 0; border-bottom: 1px solid var(--divider-color, #e0e0e0); }
  ol.replay .r-icon { width: 1.2em; text-align: center; font-weight: 700; }
  ol.replay .r-ok .r-icon { color: var(--success-color, #43a047); }
  ol.replay .r-failed .r-icon { color: var(--error-color, #db4437); }
  ol.replay .r-skipped .r-icon, ol.replay .r-stopped .r-icon { color: var(--warning-color, #ffa600); }
  details.replay-run summary { cursor: pointer; }
  textarea[data-share-code], textarea[data-import-code] { width: 100%; box-sizing: border-box; font-family: monospace; font-size: 12px; }
  .vacation-on { border-left: 4px solid var(--primary-color, #03a9f4); }
  .saved-look { margin: 6px 0; }
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
  button.link { border: none; background: none; padding: 0 4px; color: var(--primary-color, #03a9f4); text-decoration: underline; }
  .skip-line { margin-top: 4px; }
  details.away { margin-bottom: 12px; }
  details.away summary { cursor: pointer; }
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
  .pill.run { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
  .progress { height: 6px; border-radius: 3px; background: var(--secondary-background-color, #eee); overflow: hidden; margin: 8px 0 4px; }
  .progress .bar { height: 100%; background: var(--primary-color, #03a9f4); transition: width .4s ease; }
  .progress.indeterminate .bar { width: 35%; animation: dr-slide 1.2s ease-in-out infinite; }
  @keyframes dr-slide { from { transform: translateX(-100%); } to { transform: translateX(300%); } }
  .progress-text { color: var(--primary-text-color); }
  .running-strip { display: grid; gap: 8px; margin-bottom: 12px; }
  .timeline { list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }
  .tl { display: grid; grid-template-columns: 76px 1fr; gap: 10px; align-items: start; padding-left: 10px; border-left: 3px solid var(--divider-color, #ddd); }
  .tl.ran { border-left-color: var(--success-color, #43a047); }
  .tl.failed { border-left-color: var(--error-color, #db4437); }
  .tl.skipped { border-left-color: var(--warning-color, #ffa600); }
  .tl.running { border-left-color: var(--primary-color, #03a9f4); }
  .tl-time { font-variant-numeric: tabular-nums; color: var(--secondary-text-color); padding-top: 2px; }
  .steps { list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; }
  .step-fold { border: 1px solid var(--divider-color, #ddd); border-radius: 12px; padding: 6px 10px; background: var(--card-background-color, #fff); }
  .step-fold.open { padding-bottom: 12px; }
  .fold-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .grip { cursor: grab; color: var(--secondary-text-color); user-select: none; }
  .fold-toggle { flex: 1; display: flex; gap: 10px; align-items: center; text-align: left; background: none; border: 0; padding: 6px 0; font: inherit; color: var(--primary-text-color); cursor: pointer; min-width: 0; }
  .fold-toggle .num { flex: none; width: 24px; height: 24px; border-radius: 50%; display: inline-grid; place-items: center; font-size: 13px; background: var(--secondary-background-color, #eee); }
  .fold-toggle .what { flex: 1; min-width: 0; overflow-wrap: anywhere; }
  .together-note { display: block; font-size: 12px; color: var(--primary-color, #03a9f4); }
  .fold-toggle .chev { color: var(--secondary-text-color); }
  .step-fold .step { border: 0; padding: 0; margin: 0; }
  .sentence .say { display: grid; grid-template-columns: 64px 1fr; gap: 8px; align-items: start; margin-bottom: 10px; }
  .sentence .say > .word { font-weight: 600; padding-top: 10px; color: var(--primary-color); }
  .sentence .say > .grow { min-width: 0; }
  .sentence label.say { flex-direction: unset; }
  .chips-add { display: flex; flex-wrap: wrap; gap: 8px; margin: 4px 0 12px; }
  .chip-add { font: inherit; border-radius: 999px; padding: 8px 14px; border: 1px dashed var(--primary-color, #03a9f4); background: transparent; color: var(--primary-color, #03a9f4); cursor: pointer; }
  .switch-editor { margin: 0 0 8px; text-align: right; }
  button.link { background: none; border: 0; padding: 4px 0; color: var(--primary-color, #03a9f4); font: inherit; cursor: pointer; text-decoration: underline; }
  .copy-panel { margin-top: 10px; padding: 10px 12px; border-radius: 10px; background: var(--secondary-background-color, #f2f2f2); }
  .ideas { display: grid; gap: 8px; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); margin-bottom: 16px; }
  .group-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; margin: 16px 0 4px; }
  .group-head h2 { margin: 0; font-size: 1.05rem; }
  .power-line { margin-top: 2px; }
  .warn-text { color: var(--warning-color, #c77700); }
  .integ { white-space: nowrap; }
  .card.habit .row-actions { margin-top: 8px; }
  .library h4 { margin: 12px 0 6px; }
  .day-type { display: grid; grid-template-columns: 1fr 1fr 1fr 2fr auto; gap: 8px; align-items: end; margin-bottom: 8px; }
  .hold-row { display: grid; grid-template-columns: 1fr 2fr auto; gap: 8px; align-items: end; margin-bottom: 8px; }
  @media (max-width: 600px) { .day-type, .hold-row { grid-template-columns: 1fr; } }
  .picker-search { width: 100%; box-sizing: border-box; margin: 6px 0 4px; padding: 8px 10px; font: inherit; border-radius: 8px;
    border: 1px solid var(--divider-color, #ddd); background: var(--card-background-color, #fff); color: var(--primary-text-color); }
  input[type="color"] { width: 56px; height: 36px; padding: 2px; flex: none; }
  details.leftover textarea { width: 100%; box-sizing: border-box; }
  .only-if { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--divider-color, #ddd); }
  .mode-pick select { min-width: 110px; }
  .checks.scroll { max-height: 260px; overflow: auto; }
  .visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  .running-strip .card { padding: 10px 14px; }
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
  .history-chart { width: 100%; max-width: 420px; height: auto; display: block; margin: 4px 0; }
  .history-chart .ok { fill: var(--success-color, #43a047); }
  .history-chart .skip { fill: var(--warning-color, #ffa600); }
  .history-chart .fail { fill: var(--error-color, #db4437); }
  .history-chart .none { fill: var(--divider-color, #ccc); }
  .history-chart .axis { font-size: 9px; fill: var(--secondary-text-color); }
  .guest-wifi .wifi-line { display: flex; flex-direction: column; margin: 6px 0; font-size: 20px; }
  .mono { font-family: var(--code-font-family, monospace); letter-spacing: .03em; }
  .ticklist { list-style: none; padding: 0; margin: 6px 0; display: flex; flex-wrap: wrap; gap: 6px; }
  .ticklist button { font-size: 15px; padding: 8px 12px; }
  .attention li { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 4px 0; }
  .dry-steps { margin: 4px 0 0; padding-left: 18px; color: var(--secondary-text-color); }
  .key.fail { background: var(--error-color, #db4437); }
  .key.skip { background: var(--warning-color, #ffa600); }
  ul.plain { list-style: none; padding: 0; margin: 8px 0; display: grid; gap: 8px; }
  ul.reasons li, ul.events li { display: flex; justify-content: space-between; gap: 12px; }
  ul.events li { display: block; padding-bottom: 8px; border-bottom: 1px solid var(--divider-color, #eee); }
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--divider-color, #eee); vertical-align: top; }
  .timing { display: grid; gap: 12px; }
  ol.order { margin: 8px 0; padding-left: 24px; display: grid; gap: 6px; }
  ol.order li { display: flex; align-items: center; gap: 8px; }
  ol.order .grow { flex: 1; min-width: 0; }
  ol.order button { min-width: 40px; }
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
    :host(.busy)::before, .skeleton-card, .progress.indeterminate .bar { animation: none; }
    .progress .bar { transition: none; }
  }
</style>`;

customElements.define("daily-routine-panel", DailyRoutinePanel);
