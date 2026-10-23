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
  manual: "Only when I press Run",
};
const PRESENCE_TRIGGERS = ["everyone_leaves", "someone_arrives", "person_leaves", "person_arrives"];
const PER_PERSON_TRIGGERS = ["person_leaves", "person_arrives"];
const VALUE_TRIGGERS = ["value_above", "value_below"];
const STATE_LABELS = { idle: "Ready", waiting: "Waiting", running: "Running", skipped: "Skipped", error: "Error" };
const REASON_LABELS = {
  someone_home: "someone is still in that place",
  sensors_active: "motion right now",
  clear_timer_running: "waiting for the room to be quiet",
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
    draft: { trigger: "everyone_leaves", clear_minutes: 15, useAllPeople: true, useHouseSensors: true, actions: [{ service: "light.turn_off", targetMode: "all_areas" }] },
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
      if (["rooms", "room", "all"].includes(this._view.name)) this._refresh();
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
    const skipped = result.problems.map((p) => `${p.id}: ${p.reason}`).join(" ");
    this._showNotice(`Imported ${count}.${skipped ? ` Skipped: ${skipped}` : ""}`, result.imported === 0 && result.problems.length > 0);
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
    return (this._data?.routines || []).filter((r) => (r.room || null) === (roomId || null));
  }

  _nameFor(entityId) {
    const entity = this._index().byEntity.get(entityId)
      || this._data?.people?.find((p) => p.entity_id === entityId)
      || this._data?.sensors?.find((x) => x.entity_id === entityId);
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
      value_entity: "", value_threshold: "", off_threshold: "", active_from: "", active_to: "",
      trigger: "time", time: "07:00:00", offset_minutes: 0, days: [...WEEKDAYS],
      people: [], require_everyone_away: false, sensors: [], require_sensors_clear: false, clear_minutes: 0,
      require_states: [], conditions_match: "all", repeat: false, catch_up_minutes: 0, retries: 0, skip_noop: true,
      notify_on_run: false, notify_on_skip: false, notify_on_error: true,
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
    if (spec.trigger === "room_clear") draft.require_sensors_clear = true;
    if (spec.useRoomSensors) draft.sensors = this._roomSensors(roomId).map((e) => e.entity_id);
    if (spec.useHouseSensors) draft.sensors = this._data.binary_sensors.filter((s) => SENSOR_CLASSES.includes(s.device_class)).map((s) => s.entity_id);
    if (spec.valueClass) draft.value_entity = this._pickSensor(roomId, spec.valueClass);
    if (spec.valueThreshold !== undefined) draft.value_threshold = String(spec.valueThreshold);
    if (spec.offThreshold !== undefined) draft.off_threshold = String(spec.offThreshold);
    if (spec.usePeopleList) draft.people = this._trackedPeople();
    if (spec.useAllPeople) {
      draft.people = this._trackedPeople();
      draft.require_everyone_away = true;
      draft.require_sensors_clear = true;
    }
    if (spec.trigger === "everyone_leaves") draft.require_everyone_away = true;
    if (spec.actions) {
      draft.actions = spec.actions.map((a) => ({ kind: "service", service: a.service, targetMode: a.targetMode, area: roomId || "", entities: [], data: "" }));
    }
    return draft;
  }

  _draftFromRoutine(routine) {
    const areaIds = this._areas().map((a) => a.id);
    return {
      id: routine.id, name: routine.name, room: routine.room || null, device_id: routine.device_id || "", enabled: routine.enabled, zone: routine.zone || "zone.home",
      value_entity: routine.value_entity || "", value_threshold: routine.value_threshold ?? "", off_threshold: routine.off_threshold ?? "",
      active_from: routine.active_from ? hhmm(routine.active_from) : "", active_to: routine.active_to ? hhmm(routine.active_to) : "",
      trigger: routine.trigger, time: routine.time, offset_minutes: routine.offset_minutes || 0, days: [...routine.days],
      people: [...routine.people], require_everyone_away: routine.require_everyone_away,
      sensors: [...routine.sensors], require_sensors_clear: routine.require_sensors_clear, clear_minutes: routine.clear_minutes,
      require_states: routine.require_states.map((x) => ({ ...x })), conditions_match: routine.conditions_match || "all", repeat: routine.repeat,
      catch_up_minutes: routine.catch_up_minutes, retries: routine.retries, skip_noop: routine.skip_noop,
      notify_on_run: routine.notify_on_run, notify_on_skip: routine.notify_on_skip, notify_on_error: routine.notify_on_error,
      actions: routine.actions.map((a) => {
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
      value_threshold: draft.value_threshold === "" || draft.value_threshold == null ? null : Number(draft.value_threshold),
      off_threshold: draft.off_threshold === "" || draft.off_threshold == null ? null : Number(draft.off_threshold),
      active_from: draft.active_from || null,
      active_to: draft.active_to || null,
      trigger: draft.trigger,
      time: draft.time,
      offset_minutes: Number(draft.offset_minutes) || 0,
      days: draft.days,
      people: draft.people,
      require_everyone_away: draft.require_everyone_away,
      sensors: draft.sensors,
      require_sensors_clear: draft.require_sensors_clear,
      clear_minutes: Number(draft.clear_minutes) || 0,
      require_states: draft.require_states
        .filter((x) => x.entity_id && x.state)
        .map((x) => ({ entity_id: x.entity_id, state: x.state })),
      conditions_match: draft.conditions_match === "any" ? "any" : "all",
      repeat: draft.repeat,
      catch_up_minutes: Number(draft.catch_up_minutes) || 0,
      retries: Number(draft.retries) || 0,
      skip_noop: draft.skip_noop,
      notify_on_run: draft.notify_on_run,
      notify_on_skip: draft.notify_on_skip,
      notify_on_error: draft.notify_on_error,
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
        case "tab": {
          const tab = el.dataset.tab;
          this._view = { name: tab === "routines" ? "all" : tab === "settings" ? "settings" : "rooms" };
          this._draft = null;
          this._preview = null;
          this._notice = null;
          break;
        }
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
        case "add-condition":
          this._draft.require_states.push({ entity_id: "", state: "on" });
          break;
        case "remove-condition":
          this._draft.require_states.splice(Number(el.dataset.i), 1);
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
    if (el.dataset.importFile !== undefined) {
      const file = el.files?.[0];
      el.value = "";
      if (file) this._importFile(file);
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
      if (field === "time") d.time = value.length === 5 ? `${value}:00` : value;
      else if (field === "require_everyone_away" || field === "require_sensors_clear" || field === "enabled" || field === "repeat" || field === "skip_noop" || field === "notify_on_run" || field === "notify_on_skip" || field === "notify_on_error") d[field] = Boolean(value);
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
      const row = d.require_states[Number(el.dataset.ci)];
      if (row && el.dataset.cf === "stateChoice") {
        row.custom = value === CUSTOM_ACTION;
        row.state = row.custom ? "" : value;
        if (!live) this._render();
      } else if (row) {
        row[el.dataset.cf] = value;
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
    else if (this._view.name === "templates") body = this._renderTemplates();
    else if (this._view.name === "editor") body = this._renderEditor();
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
    ];
    if (this._hass?.user?.is_admin) tabs.push({ id: "settings", label: "Settings" });
    return tabs;
  }

  _activeTab() {
    if (this._view.name === "all") return "routines";
    if (this._view.name === "settings") return "settings";
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
    if (this._view.name === "settings") return "Settings";
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
    return (this._data?.routines || []).filter((routine) => {
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
    const timeBased = ["time", "sunrise", "sunset"].includes(d.trigger);
    const sensorsOn = d.trigger === "room_clear" || d.require_sensors_clear;
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

    const conditions = d.require_states.map((row, i) => {
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
      return `<div class="inline">
      <select data-ci="${i}" data-cf="entity_id" data-rerender>${this._entityOptions(row.entity_id)}</select>
      ${stateField}
      <button class="ghost" data-act="remove-condition" data-i="${i}">Remove</button></div>`;
    }).join("");

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
        ${d.trigger === "time" ? `<label>Time<input type="time" data-f="time" value="${esc(hhmm(d.time))}"></label>` : ""}
        ${d.trigger === "sunrise" || d.trigger === "sunset" ? `<label>Offset in minutes (negative is before)<input type="number" min="-720" max="720" data-f="offset_minutes" value="${esc(d.offset_minutes)}"></label>` : ""}
        ${timeBased || valueTrigger ? `<div class="chips">${days}</div>` : ""}
        ${presence || d.require_everyone_away ? this._zoneField(d) : ""}
        ${presence ? `<h4>Who</h4><p class="muted small">Pick the people this routine watches. People without location tracking can't be checked.</p><div class="checks">${peopleList}</div>
        <button class="ghost" data-act="tracked-people">Select everyone with tracking</button>` : ""}
        ${!presence && d.trigger !== "room_clear" ? `<label class="check"><input type="checkbox" data-f="require_everyone_away" data-rerender ${d.require_everyone_away ? "checked" : ""}> Only when everyone is away</label>` : ""}
        ${!presence && d.require_everyone_away && d.trigger !== "room_clear" ? `<div class="checks">${peopleList}</div>` : ""}
        ${d.trigger !== "room_clear" && !presence ? `<label class="check"><input type="checkbox" data-f="require_sensors_clear" data-rerender ${d.require_sensors_clear ? "checked" : ""}> Only when the sensors have been quiet</label>` : ""}
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
        ${d.require_states.length > 1 ? `<label>Run when<select data-f="conditions_match">
          <option value="all" ${d.conditions_match !== "any" ? "selected" : ""}>All of these are true</option>
          <option value="any" ${d.conditions_match === "any" ? "selected" : ""}>Any one of these is true</option></select></label>` : ""}
        <p class="muted small">Only run when these are true, for example a guest-mode switch being off.</p>
        ${conditions}
        <button class="ghost" data-act="add-condition">+ Add condition</button>
      </section>

      <section class="card">
        <h3>Do</h3>
        ${steps}
        <div class="row-actions"><button class="ghost" data-act="add-step">+ Service step</button><button class="ghost" data-act="add-delay">+ Wait</button></div>
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

  _stepEditor(step, i, roomId) {
    const last = this._draft.actions.length - 1;
    const head = `<div class="step-head"><strong>Step ${i + 1}</strong>
      <div class="row-actions">
        <button class="ghost" data-act="move-step" data-i="${i}" data-dir="-1" ${i === 0 ? "disabled" : ""}>↑</button>
        <button class="ghost" data-act="move-step" data-i="${i}" data-dir="1" ${i === last ? "disabled" : ""}>↓</button>
        <button class="ghost" data-act="remove-step" data-i="${i}">Remove</button></div></div>`;
    const kind = `<label>Type<select data-si="${i}" data-sf="kind" data-rerender>
      <option value="service" ${step.kind === "service" ? "selected" : ""}>Action</option>
      <option value="delay" ${step.kind === "delay" ? "selected" : ""}>Wait (seconds)</option></select></label>`;
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
    const steps = preview.steps.map((s, i) => s.type === "delay"
      ? `<li>Wait ${esc(s.seconds)} s</li>`
      : `<li>${esc(actionLabel(s.service))} → ${esc(outcomes[s.outcome] || s.outcome)}${s.entities?.length ? `<br><span class="muted small">${s.entities.map((e) => esc(this._nameFor(e))).join(", ")}</span>` : ""}</li>`).join("");
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
  .inline { display: grid; grid-template-columns: 1fr 1fr auto; gap: 6px; align-items: center; }
  .step { border-top: 1px solid var(--divider-color, #e0e0e0); padding-top: 8px; margin-top: 8px; }
  .step-head { display: flex; justify-content: space-between; align-items: center; }
  .preview ol { padding-left: 20px; }
  .footer { display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; margin: 12px 0 24px; }
  .template { cursor: pointer; }
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
