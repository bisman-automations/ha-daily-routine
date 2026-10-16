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
  room_clear: "When the room has been quiet",
  manual: "Only when I press Run",
};
const PRESENCE_TRIGGERS = ["everyone_leaves", "someone_arrives"];
const STATE_LABELS = { idle: "Ready", waiting: "Waiting", running: "Running", skipped: "Skipped", error: "Error" };
const REASON_LABELS = {
  someone_home: "someone is home",
  sensors_active: "motion right now",
  clear_timer_running: "waiting for the room to be quiet",
  clear_timer_not_started: "waiting for sensors",
  extra_condition_not_met: "an extra condition is not met",
  conditions_not_met: "conditions were not met",
};
const SERVICE_SUGGESTIONS = [
  "light.turn_on", "light.turn_off", "switch.turn_on", "switch.turn_off",
  "cover.open_cover", "cover.close_cover", "cover.stop_cover",
  "fan.turn_on", "fan.turn_off", "lock.lock", "lock.unlock",
  "scene.turn_on", "script.turn_on", "media_player.media_pause",
  "notify.persistent_notification",
];

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
  { id: "blank", title: "Blank routine", hint: "Start from scratch.", scope: "any", draft: {} },
];

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
      if (this._view.name === "rooms" || this._view.name === "room") this._load(true);
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
    try {
      this._data = await this._call({ type: `${DOMAIN}/overview` });
    } catch (err) {
      if (!silent) this._showNotice(err.message || String(err), true);
    }
    this._render();
  }

  _showNotice(text, error = false) {
    this._notice = text;
    this._error = error;
  }

  // ----- data helpers -----

  _areas() {
    return this._data?.areas || [];
  }

  _area(id) {
    return this._areas().find((a) => a.id === id);
  }

  _allEntities() {
    const seen = new Map();
    this._areas().forEach((a) => a.entities.forEach((e) => seen.set(e.entity_id, e)));
    return [...seen.values()].sort((a, b) => a.name.localeCompare(b.name));
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
    const entity = this._allEntities().find((e) => e.entity_id === entityId) || this._data?.people?.find((p) => p.entity_id === entityId);
    return entity?.name || entityId;
  }

  _describeTrigger(routine) {
    const when = TRIGGER_LABELS[routine.trigger] || routine.trigger;
    if (routine.trigger === "time") return `${when} ${hhmm(routine.time)}`;
    if (routine.trigger === "sunrise" || routine.trigger === "sunset") {
      const offset = routine.offset_minutes ? ` ${routine.offset_minutes > 0 ? "+" : ""}${routine.offset_minutes} min` : "";
      return `${when}${offset}`;
    }
    return when;
  }

  // ----- draft <-> stored routine -----

  _newDraft(roomId) {
    return {
      id: "", name: "", room: roomId || null, enabled: true,
      trigger: "time", time: "07:00:00", offset_minutes: 0, days: [...WEEKDAYS],
      people: [], require_everyone_away: false, sensors: [], require_sensors_clear: false, clear_minutes: 0,
      require_states: [], repeat: false, catch_up_minutes: 0, retries: 0, skip_noop: true,
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
    if (spec.useAllPeople) {
      draft.people = this._data.people.map((p) => p.entity_id);
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
      id: routine.id, name: routine.name, room: routine.room || null, enabled: routine.enabled,
      trigger: routine.trigger, time: routine.time, offset_minutes: routine.offset_minutes || 0, days: [...routine.days],
      people: [...routine.people], require_everyone_away: routine.require_everyone_away,
      sensors: [...routine.sensors], require_sensors_clear: routine.require_sensors_clear, clear_minutes: routine.clear_minutes,
      require_states: routine.require_states.map((x) => ({ ...x })), repeat: routine.repeat,
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
      trigger: draft.trigger,
      time: draft.time,
      offset_minutes: Number(draft.offset_minutes) || 0,
      days: draft.days,
      people: draft.people,
      require_everyone_away: draft.require_everyone_away,
      sensors: draft.sensors,
      require_sensors_clear: draft.require_sensors_clear,
      clear_minutes: Number(draft.clear_minutes) || 0,
      require_states: draft.require_states.filter((x) => x.entity_id && x.state),
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
        case "toggle-enabled": {
          const routine = this._data.routines.find((r) => r.id === el.dataset.id);
          if (!routine) return;
          await this._call({ type: `${DOMAIN}/routine/save`, routine: { ...routine, enabled: !routine.enabled } });
          break;
        }
        case "delete": {
          if (!window.confirm("Delete this routine?")) return;
          await this._call({ type: `${DOMAIN}/routine/delete`, routine_id: el.dataset.id });
          this._view = { name: "room", room: this._view.room ?? null };
          this._draft = null;
          this._showNotice("Routine deleted.");
          break;
        }
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
    if (!this._draft || !el.dataset) return;
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
      if (row) row[el.dataset.cf] = value;
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
    await this._load(true);
  }

  // ----- rendering -----

  _render() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    let body;
    if (!this._data) body = `<p class="muted">Loading…</p>`;
    else if (this._view.name === "room") body = this._renderRoom();
    else if (this._view.name === "templates") body = this._renderTemplates();
    else if (this._view.name === "editor") body = this._renderEditor();
    else body = this._renderRooms();

    // The editor shows its own notice next to its buttons.
    const editing = this._view.name === "editor";
    const notice = !editing && this._notice ? `<div class="notice ${this._error ? "error" : ""}">${esc(this._notice)}</div>` : "";
    const banner = this._data?.paused && !editing ? this._pausedBanner() : "";
    this.shadowRoot.innerHTML = `${STYLES}<main>${this._renderHeader()}${banner}${notice}${body}</main>`;
  }

  _renderHeader() {
    const isRoot = this._view.name === "rooms";
    const back = isRoot ? "" : `<button class="ghost" data-act="back">← Rooms</button>`;
    const paused = this._data?.paused;
    return `<header>
      <div class="title">${back}<h1>${esc(this._titleFor())}</h1></div>
      <div class="actions">
        ${isRoot && this._data ? `<button class="${paused ? "warn" : "ghost"}" data-act="pause">${paused ? "Resume routines" : "Pause all"}</button>` : ""}
        <button class="ghost" data-act="refresh" title="Refresh">⟳</button>
      </div>
    </header>`;
  }

  _titleFor() {
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

  _routineCard(routine) {
    const status = this._data.statuses[routine.id] || {};
    const state = status.waiting ? "waiting" : status.state || "idle";
    const reason = !status.conditions_ok && status.block_reason ? ` · ${REASON_LABELS[status.block_reason] || status.block_reason}` : "";
    const detail = status.last_result && status.last_result !== "success" && state !== "waiting" ? ` · last: ${esc(status.last_result)}` : "";
    return `<article class="card routine ${routine.enabled ? "" : "disabled"}">
      <div class="routine-head">
        <div>
          <h3>${esc(routine.name)}</h3>
          <div class="muted small">${esc(this._describeTrigger(routine))} · ${routine.days.length === 7 ? "every day" : routine.days.join(", ")}</div>
          <div class="muted small">Next: ${esc(formatTime(status.next_run))}${esc(reason)}${detail}</div>
        </div>
        <span class="pill ${state === "error" ? "error" : state === "waiting" ? "warn" : ""}">${esc(STATE_LABELS[state] || state)}</span>
      </div>
      <div class="row-actions">
        <button class="ghost" data-act="run" data-id="${esc(routine.id)}">Run now</button>
        <button class="ghost" data-act="edit" data-id="${esc(routine.id)}">Edit</button>
        <button class="ghost" data-act="toggle-enabled" data-id="${esc(routine.id)}">${routine.enabled ? "Disable" : "Enable"}</button>
      </div>
    </article>`;
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
    const timeBased = ["time", "sunrise", "sunset"].includes(d.trigger);
    const sensorsOn = d.trigger === "room_clear" || d.require_sensors_clear;
    const roomSensors = this._roomSensors(roomId);
    const sensorOptions = this._data.binary_sensors.map((s) => ({
      entity_id: s.entity_id, name: s.name, inRoom: (roomId ? this._area(roomId)?.entities || [] : []).some((e) => e.entity_id === s.entity_id),
    })).sort((a, b) => Number(b.inRoom) - Number(a.inRoom) || a.name.localeCompare(b.name));

    const triggerOptions = Object.entries(TRIGGER_LABELS).map(([value, label]) =>
      `<option value="${value}" ${d.trigger === value ? "selected" : ""}>${esc(label)}</option>`).join("");

    const days = WEEKDAYS.map((day) => `<label class="chip"><input type="checkbox" data-day="${day}" ${d.days.includes(day) ? "checked" : ""}> ${day}</label>`).join("");

    const peopleList = this._data.people.length
      ? this._data.people.map((p) => checkbox("people", p.entity_id, p.name, d.people.includes(p.entity_id))).join("")
      : `<p class="muted small">No people in Home Assistant yet.</p>`;

    const sensorList = sensorOptions.length
      ? sensorOptions.map((s) => checkbox("sensors", s.entity_id, s.name + (s.inRoom ? " (this room)" : ""), d.sensors.includes(s.entity_id))).join("")
      : `<p class="muted small">No binary sensors found.</p>`;

    const steps = d.actions.map((step, i) => this._stepEditor(step, i, roomId)).join("");

    const conditions = d.require_states.map((row, i) => `<div class="inline">
      <select data-ci="${i}" data-cf="entity_id" data-rerender>${this._entityOptions(row.entity_id)}</select>
      <input data-ci="${i}" data-cf="state" value="${esc(row.state)}" placeholder="state, e.g. on">
      <button class="ghost" data-act="remove-condition" data-i="${i}">Remove</button></div>`).join("");

    const preview = this._preview ? this._renderPreview(this._preview) : "";

    return `<form class="editor" onsubmit="return false">
      <section class="card">
        <h3>Basics</h3>
        <label>Name<input data-f="name" value="${esc(d.name)}" placeholder="e.g. Morning curtains"></label>
        <label class="check"><input type="checkbox" data-f="enabled" ${d.enabled ? "checked" : ""}> Enabled</label>
        <p class="muted small">${roomId ? `Room: ${esc(this._area(roomId)?.name || roomId)}` : "Whole house"}</p>
      </section>

      <section class="card">
        <h3>When</h3>
        <label>Trigger<select data-f="trigger" data-rerender>${triggerOptions}</select></label>
        ${d.trigger === "time" ? `<label>Time<input type="time" data-f="time" value="${esc(hhmm(d.time))}"></label>` : ""}
        ${d.trigger === "sunrise" || d.trigger === "sunset" ? `<label>Offset in minutes (negative is before)<input type="number" min="-720" max="720" data-f="offset_minutes" value="${esc(d.offset_minutes)}"></label>` : ""}
        ${timeBased ? `<div class="chips">${days}</div>` : ""}
        ${presence ? `<h4>Who</h4><p class="muted small">Pick the people this routine watches.</p><div class="checks">${peopleList}</div>` : ""}
        ${!presence && d.trigger !== "room_clear" ? `<label class="check"><input type="checkbox" data-f="require_everyone_away" data-rerender ${d.require_everyone_away ? "checked" : ""}> Only when everyone is away</label>` : ""}
        ${!presence && d.require_everyone_away && d.trigger !== "room_clear" ? `<div class="checks">${peopleList}</div>` : ""}
        ${d.trigger !== "room_clear" && !presence ? `<label class="check"><input type="checkbox" data-f="require_sensors_clear" data-rerender ${d.require_sensors_clear ? "checked" : ""}> Only when the sensors have been quiet</label>` : ""}
        ${sensorsOn ? `<h4>Sensors</h4><p class="muted small">${d.trigger === "room_clear" ? "The routine runs once these have been quiet for the time below." : "Motion or occupancy sensors that must stay quiet."}</p>
          ${roomId && !roomSensors.length ? `<p class="muted small">This room has no motion sensors yet. Assign one in Home Assistant, or pick another.</p>` : ""}
          <div class="checks">${sensorList}</div>
          <label>Quiet for (minutes)<input type="number" min="0" max="1440" data-f="clear_minutes" value="${esc(d.clear_minutes)}"></label>` : ""}
        <h4>Extra conditions</h4>
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
        <label>If it was missed after a restart, still run within (minutes)<input type="number" min="0" max="180" data-f="catch_up_minutes" value="${esc(d.catch_up_minutes)}"></label>
        <label>Retry a failed step (times)<input type="number" min="0" max="3" data-f="retries" value="${esc(d.retries)}"></label>
        <h4>Notify me</h4>
        <label class="check"><input type="checkbox" data-f="notify_on_run" ${d.notify_on_run ? "checked" : ""}> When it runs</label>
        <label class="check"><input type="checkbox" data-f="notify_on_skip" ${d.notify_on_skip ? "checked" : ""}> When it is skipped</label>
        <label class="check"><input type="checkbox" data-f="notify_on_error" ${d.notify_on_error ? "checked" : ""}> When it fails</label>
      </section>

      ${preview}
      ${this._notice ? `<div class="notice ${this._error ? "error" : ""}">${esc(this._notice)}</div>` : ""}
      <datalist id="services">${SERVICE_SUGGESTIONS.map((s) => `<option value="${s}">`).join("")}</datalist>
      <div class="footer">
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
      <option value="service" ${step.kind === "service" ? "selected" : ""}>Service</option>
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
    return `<div class="step">${head}${kind}
      <label>Service<input list="services" data-si="${i}" data-sf="service" value="${esc(step.service)}" placeholder="light.turn_off"></label>
      <label>Applies to<select data-si="${i}" data-sf="targetMode" data-rerender>${targetModes}</select></label>
      ${entityChecks}
      <label>Extra data (optional JSON)<textarea data-si="${i}" data-sf="data" rows="2" placeholder='{"brightness": 128}'>${esc(step.data)}</textarea></label>
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
      : `<li>${esc(s.service)} → ${esc(outcomes[s.outcome] || s.outcome)}${s.entities?.length ? `<br><span class="muted small">${s.entities.map((e) => esc(this._nameFor(e))).join(", ")}</span>` : ""}</li>`).join("");
    const verdict = preview.conditions_ok
      ? `<span class="pill">Conditions pass right now</span>`
      : `<span class="pill warn">Would wait: ${esc(REASON_LABELS[preview.block_reason] || preview.block_reason)}</span>`;
    return `<section class="card preview"><h3>Dry run</h3><p>${verdict}${preview.paused ? ` <span class="pill warn">Routines are paused</span>` : ""}</p><ol>${steps}</ol><p class="muted small">Nothing was run.</p></section>`;
  }
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
</style>`;

customElements.define("daily-routine-panel", DailyRoutinePanel);
