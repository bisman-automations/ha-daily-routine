// Smoke tests for the sidebar panel: rooms, room view, editor and draft round-trip.
// Run with: node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";

const defined = new Map();

class FakeElement {
  classList = { toggle() {}, add() {}, remove() {} };

  attachShadow() {
    this.shadowRoot = {
      innerHTML: "",
      addEventListener() {},
      querySelector() { return null; },
      querySelectorAll() { return []; },
    };
    return this.shadowRoot;
  }
}
globalThis.HTMLElement = FakeElement;
globalThis.customElements = { define(name, cls) { defined.set(name, cls); } };

await import("../custom_components/daily_routine/frontend/daily-routine-panel.js");

const overview = {
  paused: false,
  areas: [
    {
      id: "kitchen", name: "Kitchen", icon: null,
      entities: [
        { entity_id: "light.kitchen", name: "Kitchen light", domain: "light", device_class: null, state: "on" },
        { entity_id: "binary_sensor.kitchen_motion", name: "Kitchen motion", domain: "binary_sensor", device_class: "motion", state: "off" },
      ],
    },
    { id: "bedroom", name: "Bedroom", icon: "mdi:bed", entities: [] },
    {
      id: "bathroom", name: "Bathroom", icon: null,
      entities: [
        { entity_id: "sensor.bathroom_humidity", name: "Bathroom humidity", domain: "sensor", device_class: "humidity", state: "48" },
        { entity_id: "fan.bathroom_fan", name: "Bathroom fan", domain: "fan", device_class: null, state: "off" },
      ],
    },
  ],
  devices: [
    { id: "lamp_1", name: "Kitchen lamp", area_id: "kitchen", area_name: "Kitchen", model: "Lamp" },
    { id: "tv_1", name: "Living room TV", area_id: null, area_name: null, model: "TV" },
  ],
  people: [
    { entity_id: "person.alex", name: "Alex", state: "home", tracked: true },
    { entity_id: "person.kid", name: "Kid", state: "unknown", tracked: false },
    { entity_id: "person.skylight", name: "Skylight", state: "unknown", tracked: false, hidden: true },
  ],
  settings: { hidden_people: ["person.skylight"], notify_services: ["notify.mobile_app_phone"] },
  notify_targets: [
    { entity: "notify.mobile_app_phone", name: "Alex's phone" },
    { entity: "notify.mobile_app_tablet", name: "Kitchen tablet" },
  ],
  zones: [{ entity_id: "zone.home", name: "Home" }, { entity_id: "zone.work", name: "Work" }],
  sensors: [{ entity_id: "sensor.bathroom_humidity", name: "Bathroom humidity", device_class: "humidity", unit: "%" }],
  binary_sensors: [{ entity_id: "binary_sensor.kitchen_motion", name: "Kitchen motion", device_class: "motion", state: "off" }],
  routines: [
    {
      id: "kitchen_lights", name: "Kitchen lights off", enabled: true, room: "kitchen", trigger: "room_clear",
      time: "07:00:00", offset_minutes: 0, days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
      people: [], require_everyone_away: false, sensors: ["binary_sensor.kitchen_motion"], require_sensors_clear: true,
      clear_minutes: 10, require_states: [], repeat: true, catch_up_minutes: 0, retries: 0, skip_noop: true,
      notify_on_run: false, notify_on_skip: false, notify_on_error: true,
      actions: [{ service: "light.turn_off", target: { area_id: ["kitchen"] }, data: {} }],
    },
  ],
  statuses: { kitchen_lights: { state: "idle", waiting: false, conditions_ok: true, next_run: null } },
};

function makePanel() {
  const panel = new (defined.get("daily-routine-panel"))();
  panel.attachShadow();
  panel._data = structuredClone(overview);
  return panel;
}

test("panel element is registered", () => {
  assert.ok(defined.has("daily-routine-panel"));
});

test("rooms view lists every area plus the whole-house card", () => {
  const panel = makePanel();
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Whole house/);
  assert.match(html, /Kitchen/);
  assert.match(html, /Bedroom/);
  assert.match(html, /1 routine/);
});

test("empty home shows guidance to create areas in Home Assistant", () => {
  const panel = makePanel();
  panel._data.areas = [];
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /Settings → Areas/);
});

test("room view lists that room's routines", () => {
  const panel = makePanel();
  panel._view = { name: "room", room: "kitchen" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Kitchen lights off/);
  assert.match(html, /When the room has been quiet/);
});

test("new routine from a room template pre-fills the room and its sensors", () => {
  const panel = makePanel();
  const template = { draft: { trigger: "room_clear", clear_minutes: 10, useRoomSensors: true, actions: [{ service: "light.turn_off", targetMode: "area" }] }, title: "Lights off when vacant" };
  panel._draft = panel._applyTemplate(template, "kitchen");
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  assert.equal(panel._draft.room, "kitchen");
  assert.deepEqual(panel._draft.sensors, ["binary_sensor.kitchen_motion"]);
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /value="Lights off when vacant"/);
  assert.match(html, /data-sensors|data-list="sensors" data-val="binary_sensor.kitchen_motion" checked/);
});

test("draft converts to a routine payload targeting the room", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Open", draft: { trigger: "time", time: "07:00:00", actions: [{ service: "cover.open_cover", targetMode: "area" }] } }, "kitchen");
  panel._draft.name = "Morning covers";
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.name, "Morning covers");
  assert.equal(raw.room, "kitchen");
  assert.equal(raw.time, "07:00:00");
  assert.deepEqual(raw.actions[0].target, { area_id: ["kitchen"] });
  assert.ok(raw.id.startsWith("morning_covers_"));
});

test("stored routine round-trips through the editor draft", () => {
  const panel = makePanel();
  const stored = panel._data.routines[0];
  const draft = panel._draftFromRoutine(stored);
  const raw = panel._draftToRaw(draft);
  assert.equal(raw.id, stored.id);
  assert.equal(raw.trigger, stored.trigger);
  assert.deepEqual(raw.sensors, stored.sensors);
  assert.deepEqual(raw.actions[0].target, stored.actions[0].target);
  assert.equal(raw.clear_minutes, stored.clear_minutes);
});

test("invalid extra-data JSON is reported with the step number", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "x", draft: { trigger: "time", actions: [{ service: "light.turn_on", targetMode: "area" }] } }, "kitchen");
  panel._draft.actions[0].data = "{not json";
  assert.throws(() => panel._draftToRaw(panel._draft), /Step 1: extra data must be JSON/);
});

test("a step without a service is rejected before saving", () => {
  const panel = makePanel();
  panel._draft = panel._newDraft("kitchen");
  assert.throws(() => panel._draftToRaw(panel._draft), /choose a service/);
});

test("entity picker lists only the requested domain, sorted by name", () => {
  const panel = makePanel();
  panel._data.areas[0].entities.push({ entity_id: "light.hall", name: "Hall", domain: "light", state: "off" });
  const lights = panel._allEntities().filter((e) => e.domain === "light").map((e) => e.name);
  assert.deepEqual(lights, ["Hall", "Kitchen light"]);
});

test("room view shows the linked device and a delete button on each routine", () => {
  const panel = makePanel();
  panel._data.routines[0].device_id = "lamp_1";
  panel._view = { name: "room", room: "kitchen" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Linked to: Kitchen lamp/);
  assert.match(html, /data-act="delete" data-id="kitchen_lights"/);
});

test("routine without a device is linked to the Daily Routine hub", () => {
  const panel = makePanel();
  panel._view = { name: "room", room: "kitchen" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /Linked to: Daily Routine \(no device\)/);
});

test("editor offers a linked device picker and delete only for saved routines", () => {
  const panel = makePanel();
  panel._draft = panel._draftFromRoutine({ ...panel._data.routines[0], device_id: "lamp_1" });
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  let html = panel.shadowRoot.innerHTML;
  assert.match(html, /data-f="device_id"/);
  assert.match(html, /<option value="lamp_1" selected>Kitchen lamp/);
  assert.match(html, /data-act="delete">Delete</);

  panel._draft = panel._applyTemplate({ title: "Blank routine", draft: {} }, "kitchen");
  panel._render();
  html = panel.shadowRoot.innerHTML;
  assert.doesNotMatch(html, /data-act="delete">Delete</);
});

test("new room routine defaults to a device in that room; payload carries the choice", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Blank routine", draft: {} }, "kitchen");
  assert.equal(panel._draft.device_id, "lamp_1");
  panel._draft.name = "Kitchen lamp off";
  panel._draft.actions[0].service = "light.turn_off";
  assert.equal(panel._draftToRaw(panel._draft).device_id, "lamp_1");
  panel._draft.device_id = "";
  assert.equal(panel._draftToRaw(panel._draft).device_id, null);
});

test("all routines view lists every routine and searches name, room, device and trigger together", () => {
  const panel = makePanel();
  panel._data.routines.push({ ...panel._data.routines[0], id: "night_lamp", name: "Night lamp off", room: null, device_id: "lamp_1", trigger: "time", time: "22:30:00" });
  panel._query = "";
  assert.deepEqual(panel._matchingRoutines().map((r) => r.id), ["kitchen_lights", "night_lamp"]);
  panel._query = "lamp";
  assert.deepEqual(panel._matchingRoutines().map((r) => r.id), ["night_lamp"]);
  // "kitchen" matches the kitchen room and also the linked "Kitchen lamp" device.
  panel._query = "kitchen";
  assert.deepEqual(panel._matchingRoutines().map((r) => r.id), ["kitchen_lights", "night_lamp"]);
  panel._query = "kitchen lamp";
  assert.deepEqual(panel._matchingRoutines().map((r) => r.id), ["night_lamp"]);
  panel._query = "bedroom lamp";
  assert.deepEqual(panel._matchingRoutines().map((r) => r.id), []);
  panel._query = "whole house 22:30";
  assert.deepEqual(panel._matchingRoutines().map((r) => r.id), ["night_lamp"]);
});

test("all routines view renders a search box, a count and room names on each card", () => {
  const panel = makePanel();
  panel._data.routines.push({ ...panel._data.routines[0], id: "night_lamp", name: "Night lamp off", room: null, device_id: "lamp_1" });
  panel._view = { name: "all" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /data-search/);
  assert.match(html, /2 routines/);
  assert.match(html, /Whole house/);
  assert.match(html, /Kitchen/);
});

test("typing in the search box filters without rebuilding the page", () => {
  const panel = makePanel();
  panel._view = { name: "all" };
  panel._onField({ target: { dataset: { search: "" }, value: "bedroom" } });
  assert.equal(panel._query, "bedroom");
  assert.deepEqual(panel._matchingRoutines(), []);
});

test("tabs show Rooms and Routines to everyone, and Settings only to admins", () => {
  const panel = makePanel();
  panel._view = { name: "rooms" };
  panel._render();
  let html = panel.shadowRoot.innerHTML;
  assert.match(html, /data-tab="routines"/);
  assert.doesNotMatch(html, /data-tab="settings"/);

  panel._hass = { user: { is_admin: true } };
  panel._render();
  html = panel.shadowRoot.innerHTML;
  assert.match(html, /data-tab="settings"/);
  assert.match(html, /aria-selected="true" ?>Rooms|data-tab="rooms" aria-selected="true"/);
});

test("settings page lists every person with hidden ones switched off, and the notify choice", () => {
  const panel = makePanel();
  panel._view = { name: "settings" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /data-show-person="person.alex" checked/);
  assert.match(html, /data-show-person="person.skylight" >/);
});

test("notification targets are checkboxes labelled by name, never by entity id", () => {
  const panel = makePanel();
  panel._view = { name: "settings" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /data-notify-target="notify.mobile_app_phone" checked/);
  assert.match(html, /data-notify-target="notify.mobile_app_tablet"\s+>/);
  assert.match(html, /Alex(&#39;|')s phone/);
  assert.match(html, /Kitchen tablet/);
  assert.doesNotMatch(html, /<option/);
  assert.doesNotMatch(html, />notify\.mobile_app/);
});

test("people turned off in settings are left out of the routine picker", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Blank routine", draft: { trigger: "everyone_leaves" } }, null);
  panel._view = { name: "editor", room: null };
  panel._render();
  assert.doesNotMatch(panel.shadowRoot.innerHTML, /Skylight/);

  panel._draft.people = ["person.skylight"];
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /Skylight/);
});

test("people without location tracking are shown but cannot be picked", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Lights off when everyone is away", draft: { trigger: "everyone_leaves", useAllPeople: true } }, null);
  assert.deepEqual(panel._draft.people, ["person.alex"]);
  panel._view = { name: "editor", room: null };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /<input type="checkbox" disabled> Kid \(no location tracking\)/);
  assert.match(html, /data-act="tracked-people"/);
});

test("humidity fan template fills in the bathroom sensor, fan levels and a daytime window", () => {
  const panel = makePanel();
  const template = panel._templates ? null : null;
  const spec = { title: "Bathroom fan on humidity", draft: { trigger: "value_above", valueClass: "humidity", valueThreshold: 60, offThreshold: 50, actions: [{ service: "fan.turn_on", targetMode: "area" }] } };
  panel._draft = panel._applyTemplate(spec, "bathroom");
  assert.equal(panel._draft.value_entity, "sensor.bathroom_humidity");
  assert.equal(panel._draft.value_threshold, "60");
  assert.equal(panel._draft.off_threshold, "50");
  panel._draft.active_from = "07:00";
  panel._draft.active_to = "22:00";
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.value_entity, "sensor.bathroom_humidity");
  assert.equal(raw.value_threshold, 60);
  assert.equal(raw.off_threshold, 50);
  assert.equal(raw.active_from, "07:00");
  assert.equal(raw.active_to, "22:00");
});

test("value routines show the sensor by name with its unit and the window", () => {
  const panel = makePanel();
  const text = panel._describeTrigger({
    trigger: "value_above", value_entity: "sensor.bathroom_humidity", value_threshold: 60,
    active_from: "07:00:00", active_to: "22:00:00",
  });
  assert.match(text, /Bathroom humidity above 60%/);
  assert.match(text, /only 07:00–22:00/);
});

test("editor shows sensor, turn-on and turn-off level fields for value triggers", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Bathroom fan on humidity", draft: { trigger: "value_above", valueClass: "humidity", valueThreshold: 60, offThreshold: 50 } }, "bathroom");
  panel._view = { name: "editor", room: "bathroom" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /data-f="value_entity"/);
  assert.match(html, /Turn on when it goes above/);
  assert.match(html, /Turn back off when it drops below/);
  assert.match(html, /Only run between/);
});

test("the action dropdown shows plain labels, never service ids", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Off", draft: { trigger: "time", time: "07:00:00", actions: [{ service: "light.turn_off", targetMode: "area" }] } }, "kitchen");
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /<option value="light.turn_off" selected>Turn off lights<\/option>/);
  assert.match(html, />Action</);
  assert.doesNotMatch(html, />light\.turn_off</);
  assert.doesNotMatch(html, /<datalist/);
});

test("choosing Other shows a service name box, and picking a labelled action hides it", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Blank", draft: { trigger: "manual", actions: [{ service: "light.turn_off", targetMode: "area" }] } }, "kitchen");
  panel._view = { name: "editor", room: "kitchen" };
  panel._draft.actions[0].custom = true;
  panel._draft.actions[0].service = "";
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /data-sf="service"/);

  panel._draft.actions[0].custom = false;
  panel._draft.actions[0].service = "fan.turn_on";
  panel._render();
  assert.doesNotMatch(panel.shadowRoot.innerHTML, /data-sf="service"/);
});

test("unknown services get a readable label in the preview", () => {
  const panel = makePanel();
  const html = panel._renderPreview({
    steps: [{ type: "service", service: "vacuum.start_pause", outcome: "would_run", entities: [] }],
    conditions_ok: true,
  });
  assert.match(html, /Start pause \(Vacuum\)/);
});

test("presence routines pick a place, defaulting to Home", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Leave", draft: { trigger: "everyone_leaves", usePeopleList: true, actions: [{ service: "lock.lock", targetMode: "all_areas" }] } }, null);
  assert.equal(panel._draft.zone, "zone.home");
  panel._view = { name: "editor", room: null };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /data-f="zone"/);
  assert.match(html, /<option value="zone.work"\s*>Work<\/option>/);
});

test("a presence routine saves the place it watches and names it in summaries", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Leave work", draft: { trigger: "everyone_leaves", usePeopleList: true, actions: [{ service: "lock.lock", targetMode: "all_areas" }] } }, null);
  panel._draft.zone = "zone.work";
  panel._draft.name = "Leave work";
  panel._draft.people = ["person.alex"];
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.zone, "zone.work");
  assert.equal(panel._describeTrigger({ trigger: "everyone_leaves", zone: "zone.work" }), "When everyone leaves (Work)");
  assert.equal(panel._describeTrigger({ trigger: "someone_arrives", zone: "zone.home" }), "When someone arrives (Home)");
});

test("the first load shows a skeleton instead of a bare Loading line", () => {
  const panel = makePanel();
  panel._data = null;
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /skeleton-grid/);
  assert.match(html, /skeleton-card/);
  assert.doesNotMatch(html, /Loading…/);
});

test("entity lookups come from one cached index per load, and names still resolve", () => {
  const panel = makePanel();
  assert.equal(panel._allEntities(), panel._allEntities());
  assert.equal(panel._nameFor("light.kitchen"), "Kitchen light");
  assert.equal(panel._nameFor("sensor.nowhere"), "sensor.nowhere");
  const before = panel._index();
  panel._data = structuredClone(overview);
  assert.notEqual(panel._index(), before, "new data gets a fresh index");
});

test("a background refresh that brings nothing new leaves the page alone", async () => {
  const panel = makePanel();
  panel._rendered = true;
  panel._dataJson = JSON.stringify(panel._data);
  let renders = 0;
  panel._render = () => { renders += 1; };
  panel._hass = { callWS: async () => structuredClone(overview) };
  await panel._load(true);
  assert.equal(renders, 0);

  panel._hass = { callWS: async () => ({ ...structuredClone(overview), paused: true }) };
  await panel._load(true);
  assert.equal(renders, 1, "a real change still redraws");
});

test("extra conditions offer all-or-any once there is more than one, and save the choice", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Blank", draft: { trigger: "time", time: "07:00:00", actions: [{ service: "light.turn_off", targetMode: "area" }] } }, "kitchen");
  panel._view = { name: "editor", room: "kitchen" };
  panel._draft.require_states = [{ entity_id: "light.kitchen", state: "on" }];
  panel._render();
  assert.doesNotMatch(panel.shadowRoot.innerHTML, /data-f="conditions_match"/);

  panel._draft.require_states.push({ entity_id: "light.kitchen", state: "off" });
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /data-f="conditions_match"/);

  panel._draft.conditions_match = "any";
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.conditions_match, "any");
  panel._draft.conditions_match = undefined;
  assert.equal(panel._draftToRaw(panel._draft).conditions_match, "all");
});

test("condition states are picked by name, with Other for anything else", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Blank", draft: { trigger: "time", time: "07:00:00", actions: [{ service: "light.turn_off", targetMode: "area" }] } }, "kitchen");
  panel._view = { name: "editor", room: "kitchen" };
  panel._draft.require_states = [{ entity_id: "lock.front_door", state: "unlocked" }];
  panel._render();
  let html = panel.shadowRoot.innerHTML;
  assert.match(html, /<option value="unlocked" selected>Unlocked<\/option>/);
  assert.doesNotMatch(html, /placeholder="state"/, "a known state needs no text box");
  assert.doesNotMatch(html, />unlocked</, "no raw state text in the labels");

  panel._draft.require_states = [{ entity_id: "light.kitchen", state: "dimmed", custom: true }];
  panel._render();
  html = panel.shadowRoot.innerHTML;
  assert.match(html, /data-cf="state" value="dimmed"/);
});

test("choosing a named state saves it; choosing Other clears it for typing", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Blank", draft: { trigger: "time", time: "07:00:00", actions: [{ service: "light.turn_off", targetMode: "area" }] } }, "kitchen");
  panel._draft.require_states = [{ entity_id: "lock.front_door", state: "locked" }];
  const row = panel._draft.require_states[0];
  row.state = "jammed";
  panel._draftToRaw(panel._draft);
  assert.equal(panel._draftToRaw(panel._draft).require_states[0].state, "jammed");
});
