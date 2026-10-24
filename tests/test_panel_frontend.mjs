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
  revision: 1,
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
      people: [], sensors: ["binary_sensor.kitchen_motion"],
      clear_minutes: 10, conditions: [], conditions_match: "all", repeat: true, catch_up_minutes: 0, retries: 0, skip_noop: true,
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

const BLANK = { title: "Blank", draft: { trigger: "time", time: "07:00:00", actions: [{ service: "light.turn_off", targetMode: "area" }] } };

function editorWith(conditions) {
  const panel = makePanel();
  panel._draft = panel._applyTemplate(BLANK, "kitchen");
  panel._draft.conditions = conditions;
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  return panel;
}

test("the When section has no separate checkboxes for away or quiet; they are conditions now", () => {
  const panel = editorWith([]);
  const html = panel.shadowRoot.innerHTML;
  assert.doesNotMatch(html, /Only when everyone is away/);
  assert.doesNotMatch(html, /Only when the sensors have been quiet/);
  assert.match(html, /data-act="add-condition" data-kind="presence"/);
  assert.match(html, /data-act="add-condition" data-kind="sensors"/);
  assert.match(html, /data-act="add-condition" data-kind="state"/);
});

test("adding a condition of each kind creates the right shape", () => {
  const panel = editorWith([]);
  panel._onClick({ target: { closest: () => ({ dataset: { act: "add-condition", kind: "presence" } }) } });
  panel._onClick({ target: { closest: () => ({ dataset: { act: "add-condition", kind: "sensors" } }) } });
  panel._onClick({ target: { closest: () => ({ dataset: { act: "add-condition", kind: "state" } }) } });
  assert.deepEqual(panel._draft.conditions.map((c) => c.kind), ["presence", "sensors", "state"]);
  assert.equal(panel._draft.conditions[0].who, "everyone");
  assert.equal(panel._draft.conditions[0].where, "away");
  assert.equal(panel._draft.conditions[1].mode, "quiet");
});

test("a presence condition renders who, away or at, a place, and tracked people", () => {
  const panel = editorWith([{ kind: "presence", who: "anyone", where: "in", people: ["person.alex"], zone: "zone.work" }]);
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /<option value="anyone" selected>Anyone<\/option>/);
  assert.match(html, /<option value="in" selected>is at<\/option>/);
  assert.match(html, /<option value="zone.work" selected>Work<\/option>/);
  assert.match(html, /data-cpeople="person.alex" checked/);
});

test("the all-or-any choice appears once there is more than one condition, and is saved", () => {
  const one = editorWith([{ kind: "state", entity_id: "light.kitchen", state: "on" }]);
  assert.doesNotMatch(one.shadowRoot.innerHTML, /data-f="conditions_match"/);

  const two = editorWith([
    { kind: "state", entity_id: "light.kitchen", state: "on" },
    { kind: "state", entity_id: "light.hall", state: "off" },
  ]);
  assert.match(two.shadowRoot.innerHTML, /data-f="conditions_match"/);
  two._draft.conditions_match = "any";
  assert.equal(two._draftToRaw(two._draft).conditions_match, "any");
  two._draft.conditions_match = undefined;
  assert.equal(two._draftToRaw(two._draft).conditions_match, "all");
});

test("a sensors condition sends its mode, minutes and sensors", () => {
  const panel = editorWith([{ kind: "sensors", mode: "quiet", minutes: 10, sensors: ["binary_sensor.kitchen_motion"] }]);
  let html = panel.shadowRoot.innerHTML;
  assert.match(html, /<option value="quiet" selected>Quiet for<\/option>/);
  assert.match(html, /data-csensor="binary_sensor.kitchen_motion" checked/);

  panel._onField({ target: { dataset: { ci: "0", cf: "mode" }, value: "active" } });
  panel._onField({ target: { dataset: { ci: "0", cf: "minutes" }, value: "5" } });
  const raw = panel._draftToRaw(panel._draft);
  assert.deepEqual(raw.conditions, [{ kind: "sensors", mode: "active", minutes: 5, sensors: ["binary_sensor.kitchen_motion"] }]);
  assert.equal("require_states" in raw, false);
  assert.equal("require_everyone_away" in raw, false);
  assert.equal("require_sensors_clear" in raw, false);
});

test("condition states are picked by name, with Other for anything else", () => {
  const panel = editorWith([{ kind: "state", entity_id: "lock.front_door", state: "unlocked" }]);
  let html = panel.shadowRoot.innerHTML;
  assert.match(html, /<option value="unlocked" selected>Unlocked<\/option>/);
  assert.doesNotMatch(html, /placeholder="state"/, "a known state needs no text box");

  panel._draft.conditions = [{ kind: "state", entity_id: "light.kitchen", state: "dimmed", custom: true }];
  panel._render();
  html = panel.shadowRoot.innerHTML;
  assert.match(html, /data-cf="state" value="dimmed"/);
});

test("choosing Other in a state condition clears the state, shows a text box, and keeps what is typed", () => {
  const panel = editorWith([{ kind: "state", entity_id: "lock.front_door", state: "locked" }]);
  panel._onField({ target: { dataset: { ci: "0", cf: "stateChoice" }, value: "__custom" } });
  assert.match(panel.shadowRoot.innerHTML, /data-cf="state"/, "the text box appears under Other");
  assert.equal(panel._draft.conditions[0].state, "");

  panel._onField({ target: { dataset: { ci: "0", cf: "state" }, value: "jammed", type: "text" } }, true);
  assert.deepEqual(panel._draftToRaw(panel._draft).conditions, [{ kind: "state", entity_id: "lock.front_door", state: "jammed" }]);
});

test("a state condition left on Choose a state is not saved", () => {
  const panel = editorWith([{ kind: "state", entity_id: "lock.front_door", state: "" }]);
  assert.deepEqual(panel._draftToRaw(panel._draft).conditions, []);
});

test("the house-empty template starts with a quiet-sensors condition, not old flags", () => {
  const panel = makePanel();
  const template = panel._templates().find((t) => t.id === "house_empty");
  const draft = panel._applyTemplate(template, null);
  const sensorCond = draft.conditions.find((c) => c.kind === "sensors");
  assert.ok(sensorCond, "a sensors condition is added");
  assert.equal(sensorCond.mode, "quiet");
  assert.equal(sensorCond.minutes, 15);
  const raw = panel._draftToRaw(draft);
  assert.equal("require_sensors_clear" in raw, false);
});

test("per-person triggers are offered in the editor with a people picker and a place", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Blank", draft: { trigger: "person_leaves", actions: [{ service: "light.turn_off", targetMode: "all_areas" }] } }, null);
  panel._view = { name: "editor", room: null };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /<option value="person_leaves"\s+selected>When a person leaves a place<\/option>/);
  assert.match(html, /data-f="zone"/);
  assert.match(html, /data-list="people"/);
});

test("a per-person routine names the people and the place in its summary", () => {
  const panel = makePanel();
  const text = panel._describeTrigger({ trigger: "person_leaves", zone: "zone.work", people: ["person.alex"] });
  assert.equal(text, "When a person leaves a place (Work) · Alex");
});

test("the catch-up choice shows plain words and keeps a value that is not one of the presets", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Blank", draft: { trigger: "time", time: "07:00:00", actions: [{ service: "light.turn_off", targetMode: "area" }] } }, "kitchen");
  panel._view = { name: "editor", room: "kitchen" };
  panel._draft.catch_up_minutes = 45;
  panel._render();
  let html = panel.shadowRoot.innerHTML;
  assert.match(html, /<option value="45" selected>Within 45 minutes<\/option>/);
  assert.match(html, /<option value="30"\s*>Within 30 minutes<\/option>/);
  assert.match(html, /<option value="60"\s*>Within 1 hour<\/option>/);
  assert.doesNotMatch(html, /catch_up_minutes" value=/, "the old number box is gone");
  panel._onField({ target: { dataset: { f: "catch_up_minutes" }, value: "120", type: "select-one" } });
  assert.equal(panel._draftToRaw(panel._draft).catch_up_minutes, 120);
});

test("duplicate opens a copy with its own name, no id, and a new id when saved", () => {
  const panel = makePanel();
  const routine = panel._data.routines[0];
  const copy = { ...panel._draftFromRoutine(routine), id: "", name: `${routine.name} (copy)`, isNew: true };
  panel._draft = copy;
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.name, "Kitchen lights off (copy)");
  assert.notEqual(raw.id, routine.id);
  assert.match(raw.id, /^kitchen_lights_off_copy_/);
  assert.equal(panel._draft.isNew, true);
});

test("the routine card offers Duplicate next to Edit", () => {
  const panel = makePanel();
  panel._view = { name: "room", room: "kitchen" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /data-act="duplicate" data-id="kitchen_lights"/);
});

test("three new templates fill in people, schedules and sunrise", () => {
  const panel = makePanel();
  const bedtime = panel._applyTemplate(panel._templates().find((t) => t.id === "bedtime_curtains"), null);
  assert.equal(bedtime.time, "22:00:00");
  assert.equal(bedtime.actions[0].service, "cover.close_cover");
  const sunrise = panel._applyTemplate(panel._templates().find((t) => t.id === "lights_off_at_sunrise"), null);
  assert.equal(sunrise.trigger, "sunrise");
  const someone = panel._applyTemplate(panel._templates().find((t) => t.id === "lights_off_when_someone_leaves"), null);
  assert.equal(someone.trigger, "person_leaves");
  assert.deepEqual(someone.people, ["person.alex"]);
});

test("the background refresh asks only for run states, and redraws only when they change", async () => {
  const panel = makePanel();
  panel._view = { name: "rooms" };
  panel._hass = {};
  panel._statusJson = JSON.stringify({ revision: panel._data.revision, paused: panel._data.paused, statuses: panel._data.statuses });
  const calls = [];
  panel._call = async (message) => {
    calls.push(message.type);
    return { revision: panel._data.revision, paused: false, statuses: { kitchen_lights: { state: "idle", waiting: false, conditions_ok: true, next_run: null } } };
  };
  let renders = 0;
  panel._render = () => { renders += 1; };
  await panel._refresh();
  assert.deepEqual(calls, ["daily_routine/status"]);
  assert.equal(renders, 0, "nothing changed, so nothing is drawn");

  panel._call = async () => ({ revision: panel._data.revision, paused: true, statuses: panel._data.statuses });
  await panel._refresh();
  assert.equal(renders, 1);
  assert.equal(panel._data.paused, true);
});

test("a new revision from the status poll reloads the whole overview", async () => {
  const panel = makePanel();
  panel._hass = {};
  const types = [];
  panel._call = async (message) => {
    types.push(message.type);
    if (message.type === "daily_routine/status") return { revision: panel._data.revision + 1, paused: false, statuses: {} };
    return { ...structuredClone(overview), revision: panel._data.revision + 1 };
  };
  panel._render = () => {};
  await panel._refresh();
  assert.deepEqual(types, ["daily_routine/status", "daily_routine/overview"]);
});

test("importing a file that is not JSON says so, and nothing is sent", async () => {
  const panel = makePanel();
  const sent = [];
  panel._call = async (message) => { sent.push(message.type); return {}; };
  await assert.rejects(() => panel._importText("not json"), /isn't a Daily Routine backup/);
  assert.deepEqual(sent, []);
});

test("importing a backup sends its routines and reports anything skipped", async () => {
  const panel = makePanel();
  const sent = [];
  panel._call = async (message) => {
    sent.push(message);
    if (message.type === "daily_routine/import") return { imported: 1, problems: [{ id: "broken", reason: "Add at least one step." }] };
    return { ...structuredClone(overview) };
  };
  panel._render = () => {};
  await panel._importText(JSON.stringify({ format: "daily_routine", version: 1, routines: [{ id: "morning", name: "Morning" }] }));
  assert.equal(sent[0].type, "daily_routine/import");
  assert.deepEqual(sent[0].routines, [{ id: "morning", name: "Morning" }]);
  assert.match(panel._notice, /Imported 1 routine\.\s+Skipped: broken: Add at least one step\./);
});

test("a routine card lists its recent runs, closed, in plain words", () => {
  const panel = makePanel();
  const routine = panel._data.routines[0];
  const status = {
    history: [
      { at: "2026-10-23T07:00:00", outcome: "ran", detail: "" },
      { at: "2026-10-22T07:00:00", outcome: "skipped", detail: "extra_condition_not_met" },
      { at: "2026-10-21T07:00:00", outcome: "failed", detail: "error:light.turn_off" },
    ],
  };
  const html = panel._historyHtml(status);
  assert.match(html, /<details class="history"><summary[^>]*>Recent runs<\/summary>/);
  assert.match(html, /· ran</);
  assert.match(html, /skipped: an extra condition is not met/);
  assert.match(html, /failed: error:light\.turn_off/);
  assert.doesNotMatch(html, /extra_condition_not_met</, "no raw reason codes in the labels");
  assert.equal(panel._historyHtml({ history: [] }), "");
  assert.ok(panel._routineCard(routine, {}) !== undefined);
});

test("only the five newest runs are listed on a card", () => {
  const panel = makePanel();
  const history = Array.from({ length: 8 }, (_, i) => ({ at: `2026-10-${10 + i}T07:00:00`, outcome: "ran", detail: "" }));
  const html = panel._historyHtml({ history });
  assert.equal((html.match(/<li>/g) || []).length, 5);
});

test("a state trigger takes several states and a hold time, and saves them", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Blank", draft: { trigger: "state_is", actions: [{ service: "light.turn_off", targetMode: "entities", entities: ["light.kitchen"], data: "" }] } }, null);
  panel._draft.watch_entity = "input_boolean.dinner_done";
  panel._draft.watch_states = "on, off";
  panel._draft.hold_minutes = "5";
  panel._view = { name: "editor", room: null };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /<option value="state_is"\s+selected>When a device changes to a value<\/option>/);
  assert.match(html, /data-f="watch_entity"/);
  assert.match(html, /data-f="watch_states"[^>]*value="on, off"/);
  assert.match(html, /data-f="hold_minutes"/);
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.watch_entity, "input_boolean.dinner_done");
  assert.deepEqual(raw.watch_states, ["on", "off"]);
  assert.equal(raw.hold_minutes, 5);
});

test("a state trigger is summarised with its states and hold time", () => {
  const panel = makePanel();
  const text = panel._describeTrigger({
    trigger: "state_is", watch_entity: "input_boolean.dinner_done", watch_states: ["on", "off"], hold_minutes: 5,
    active_from: null, active_to: null,
  });
  assert.match(text, /input_boolean\.dinner_done becomes on or off for 5 min$/);
});

test("an empty state trigger is saved without a device or states", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Blank", draft: { trigger: "time", time: "07:00:00", actions: [{ service: "light.turn_off", targetMode: "area" }] } }, "kitchen");
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.watch_entity, null);
  assert.deepEqual(raw.watch_states, []);
  assert.equal(raw.hold_minutes, 0);
});

const dishwasherRule = () => ({
  id: "empty_dishwasher", name: "Empty dishwasher", kind: "todo", enabled: true, room: null, device_id: null, zone: "zone.home",
  trigger: "state_is", watch_entity: "binary_sensor.dishwasher_clean", watch_states: ["on"], hold_minutes: 0,
  time: "07:00:00", offset_minutes: 0, days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], people: [], sensors: [],
  clear_minutes: 0, conditions: [], conditions_match: "all", repeat: true, catch_up_minutes: 0, retries: 0, skip_noop: true,
  notify_on_run: false, notify_on_skip: false, notify_on_error: true,
  actions: [{ todo: { op: "add", list: "todo.groceries", item: "Empty dishwasher" } }],
});

function todoPanel() {
  const panel = makePanel();
  Object.assign(panel._data, {
    all_entities: [
      { entity_id: "binary_sensor.dishwasher_clean", name: "Dishwasher clean", state: "off" },
      { entity_id: "todo.groceries", name: "Groceries", state: "2" },
    ],
    todo_lists: [{ entity_id: "todo.groceries", name: "Groceries", state: "2" }],
  });
  panel._data.routines.push(dishwasherRule());
  panel._data.statuses.empty_dishwasher = { state: "idle", waiting: false, history: [] };
  return panel;
}

test("the To-dos tab lists to-do rules only; rooms and the routines list leave them out", () => {
  const panel = todoPanel();
  panel._view = { name: "todos" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /<h3>Empty dishwasher<\/h3>/);
  assert.doesNotMatch(panel.shadowRoot.innerHTML, /Kitchen lights off/);

  panel._view = { name: "all" };
  panel._render();
  assert.doesNotMatch(panel.shadowRoot.innerHTML, /Empty dishwasher/);
  assert.equal(panel._routinesFor(null).length, 0);
});

test("the To-dos tab opens the to-do page", () => {
  const panel = todoPanel();
  assert.ok(panel._tabs().some((t) => t.id === "todos" && t.label === "To-dos"));
  panel._onClick({ target: { closest: () => ({ dataset: { act: "tab", tab: "todos" } }) } });
  assert.equal(panel._view.name, "todos");
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /<h1>To-dos<\/h1>/);
  assert.match(panel.shadowRoot.innerHTML, /aria-selected="true"[^>]*>To-dos</);
});

test("a to-do rule card says what it does in names, and shows its last run", () => {
  const panel = todoPanel();
  panel._data.statuses.empty_dishwasher.history = [{ at: "2026-10-23T20:00:00", outcome: "ran", detail: "Empty dishwasher: already on the list" }];
  panel._view = { name: "todos" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Add “Empty dishwasher” on Groceries/);
  assert.match(html, /Dishwasher clean becomes on/);
  assert.match(html, /Empty dishwasher: already on the list/);
  assert.doesNotMatch(html, /binary_sensor\.dishwasher_clean/, "entity ids are not shown");
});

test("a to-do rule card warns when its list or device is unavailable", () => {
  const panel = todoPanel();
  panel._data.health = { empty_dishwasher: ["Groceries is unavailable right now."] };
  panel._view = { name: "todos" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /Groceries is unavailable right now\./);
});

test("a routine card shows health warnings from the overview", () => {
  const panel = makePanel();
  panel._data.health = { kitchen_lights: ["Kitchen motion is unavailable right now."] };
  panel._view = { name: "room", room: "kitchen" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /Kitchen motion is unavailable right now\./);
});

test("the to-do editor saves a to-do rule as a routine with one to-do step", async () => {
  const panel = todoPanel();
  const sent = [];
  panel._hass = {
    callWS: async (message) => {
      sent.push(message);
      if (message.type === `daily_routine/overview`) return structuredClone(panel._data);
      return message.type === "daily_routine/routine/save" ? { routine: { ...message.routine } } : {};
    },
  };
  panel._onClick({ target: { closest: () => ({ dataset: { act: "todo-new" } }) } });
  panel._render();
  let html = panel.shadowRoot.innerHTML;
  assert.match(html, /<option value="todo\.groceries"\s*>Groceries<\/option>/);
  assert.match(html, /<option value="binary_sensor\.dishwasher_clean"\s*>Dishwasher clean<\/option>/);

  panel._onField({ target: { dataset: { tf: "watch_entity" }, value: "binary_sensor.dishwasher_clean", type: "select-one" } });
  panel._onField({ target: { dataset: { tf: "watch_states" }, value: "on", type: "text" } }, true);
  panel._onField({ target: { dataset: { tf: "list" }, value: "todo.groceries", type: "select-one" } });
  panel._onField({ target: { dataset: { tf: "item" }, value: "Empty dishwasher", type: "text" } }, true);
  await panel._saveTodoDraft();

  const saves = sent.filter((m) => m.type === "daily_routine/routine/save");
  assert.equal(saves.length, 1);
  const routine = saves[0].routine;
  assert.equal(routine.kind, "todo");
  assert.equal(routine.trigger, "state_is");
  assert.equal(routine.watch_entity, "binary_sensor.dishwasher_clean");
  assert.deepEqual(routine.watch_states, ["on"]);
  assert.equal(routine.hold_minutes, 0);
  assert.deepEqual(routine.actions, [{ todo: { op: "add", list: "todo.groceries", item: "Empty dishwasher" } }]);
  assert.equal(panel._view.name, "todos", "saving returns to the To-dos tab");
});

test("the to-do editor says what is missing instead of saving an incomplete rule", async () => {
  const panel = todoPanel();
  panel._hass = { callWS: async () => ({}) };
  panel._todoDraft = { ...panel._newTodoDraft(), watch_entity: "binary_sensor.dishwasher_clean", watch_states: "on", item: "Empty dishwasher" };
  await assert.rejects(panel._saveTodoDraft(), /Choose a to-do list/);
});

test("suggested states come from the device history and leave out unavailable", () => {
  const panel = todoPanel();
  panel._stateHints = { "binary_sensor.dishwasher_clean": ["off", "on", "unavailable"] };
  panel._todoDraft = { ...panel._newTodoDraft(), watch_entity: "binary_sensor.dishwasher_clean" };
  panel._view = { name: "todo-editor" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /<datalist id="state-suggestions">[\s\S]*<option value="on">/);
  assert.doesNotMatch(html, /<option value="unavailable">/);
});

test("pausing a to-do rule saves the routine turned off", async () => {
  const panel = todoPanel();
  const sent = [];
  panel._hass = {
    callWS: async (message) => {
      sent.push(message);
      return message.type === "daily_routine/overview" ? structuredClone(panel._data) : {};
    },
  };
  await panel._onClick({ target: { closest: () => ({ dataset: { act: "todo-toggle", id: "empty_dishwasher" } }) } });
  const save = sent.find((m) => m.type === "daily_routine/routine/save");
  assert.equal(save.routine.enabled, false);
  assert.equal(save.routine.kind, "todo");
});

test("a routine can have a to-do step, shown and saved in plain words", () => {
  const panel = todoPanel();
  panel._draft = panel._draftFromRoutine({ ...overview.routines[0], actions: [{ todo: { op: "remove", list: "todo.groceries", item: "Old item" } }] });
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /<option value="todo"\s+selected>To-do list<\/option>/);
  assert.match(html, /<option value="remove"\s+selected>Remove an item<\/option>/);
  assert.match(html, /data-sf="todoItem"[^>]*value="Old item"/);
  assert.deepEqual(panel._draftToRaw(panel._draft).actions, [{ todo: { op: "remove", list: "todo.groceries", item: "Old item" } }]);
});

test("a to-do step without a list or item is rejected with the step number", () => {
  const panel = makePanel();
  panel._draft = panel._newDraft(null);
  panel._draft.actions = [{ kind: "todo", todoOp: "add", todoList: "", todoItem: "Milk" }];
  assert.throws(() => panel._draftToRaw(panel._draft), /Step 1: choose a to-do list/);
});

test("the dry run says what a to-do step would do, in words", () => {
  const panel = todoPanel();
  panel._draft = panel._newDraft(null);
  panel._view = { name: "editor", room: null };
  panel._preview = {
    conditions_ok: true, block_reason: null, paused: false,
    steps: [{ type: "todo", op: "add", list: "todo.groceries", item: "Empty dishwasher", outcome: "would_add" }],
  };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /Add “Empty dishwasher” on Groceries → would be added/);
});

test("importing a file sends routines only and reports skipped ones", async () => {
  const panel = todoPanel();
  const sent = [];
  panel._hass = {
    callWS: async (message) => {
      sent.push(message);
      if (message.type === "daily_routine/import") return { imported: 1, problems: [{ id: "x", reason: "bad" }] };
      return structuredClone(panel._data);
    },
  };
  await panel._importText(JSON.stringify({ routines: [dishwasherRule()] }));
  const call = sent.find((m) => m.type === "daily_routine/import");
  assert.deepEqual(Object.keys(call).sort(), ["routines", "type"]);
  assert.match(panel._notice, /Imported 1 routine\. Skipped: x: bad/);
});

test("a checked-off trigger is summarised by its item and list", () => {
  const panel = makePanel();
  panel._data.all_entities = [{ entity_id: "todo.groceries", name: "Groceries", state: "2" }];
  const text = panel._describeTrigger({ trigger: "todo_done", watch_entity: "todo.groceries", watch_item: "Milk", active_from: null, active_to: null });
  assert.equal(text, "“Milk” checked off Groceries");
  const any = panel._describeTrigger({ trigger: "todo_done", watch_entity: "todo.groceries", watch_item: "", active_from: null, active_to: null });
  assert.equal(any, "any item checked off Groceries");
});

test("the checked-off trigger offers a to-do list and an optional item name, and saves them", () => {
  const panel = makePanel();
  panel._data.todo_lists = [{ entity_id: "todo.groceries", name: "Groceries", state: "2" }];
  panel._draft = panel._applyTemplate({ title: "Blank", draft: { trigger: "todo_done", actions: [{ service: "light.turn_on", targetMode: "area" }] } }, null);
  panel._draft.watch_entity = "todo.groceries";
  panel._draft.watch_item = "  Milk ";
  panel._view = { name: "editor", room: null };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /<option value="todo_done"\s+selected>When an item is checked off a to-do list<\/option>/);
  assert.match(html, /data-f="watch_item"/);
  assert.doesNotMatch(html, /data-f="watch_states"/, "no state box for this trigger");
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.watch_entity, "todo.groceries");
  assert.equal(raw.watch_item, "Milk");
});

test("the notify-on-health choice is shown under Options and saved with the routine", () => {
  const panel = makePanel();
  panel._draft = panel._newDraft(null);
  panel._draft.actions = [{ kind: "service", service: "light.turn_on", targetMode: "none", area: "", entities: [], data: "" }];
  panel._view = { name: "editor", room: null };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /data-f="notify_on_health"/);
  panel._onField({ target: { dataset: { f: "notify_on_health" }, type: "checkbox", checked: true, value: "on" } });
  assert.equal(panel._draftToRaw(panel._draft).notify_on_health, true);
  const loaded = panel._draftFromRoutine({ ...overview.routines[0], notify_on_health: true });
  assert.equal(loaded.notify_on_health, true);
});

test("a complete step offers the due-date choices, and a add step does not", () => {
  const panel = todoPanel();
  panel._draft = panel._newDraft(null);
  panel._draft.actions = [{ kind: "todo", todoOp: "complete", todoList: "todo.groceries", todoItem: "Milk", todoDue: "any", todoOverdue: true, todoUndated: false, service: "", targetMode: "none", area: "", entities: [], data: "" }];
  panel._view = { name: "editor", room: null };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /data-sf="todoDue"/);
  assert.match(panel.shadowRoot.innerHTML, /<option value="0"[^>]*>Due today only<\/option>/);
  panel._draft.actions[0].todoOp = "add";
  panel._render();
  assert.doesNotMatch(panel.shadowRoot.innerHTML, /data-sf="todoDue"/);
});

test("a due window is saved with the step and read back, and any date is saved as no check", () => {
  const panel = todoPanel();
  panel._draft = panel._newDraft(null);
  panel._draft.actions = [{ kind: "todo", todoOp: "complete", todoList: "todo.groceries", todoItem: "Milk", todoDue: "1", todoOverdue: false, todoUndated: true, service: "", targetMode: "none", area: "", entities: [], data: "" }];
  panel._draft.actions[0].todoItem = "Milk";
  const raw = panel._draftToRaw(panel._draft);
  assert.deepEqual(raw.actions[0].todo, { op: "complete", list: "todo.groceries", item: "Milk", due: { within: 1, overdue: false, undated: true } });

  const loaded = panel._draftFromRoutine({ ...overview.routines[0], actions: [{ todo: raw.actions[0].todo }] });
  assert.equal(loaded.actions[0].todoDue, "1");
  assert.equal(loaded.actions[0].todoOverdue, false);
  assert.equal(loaded.actions[0].todoUndated, true);

  panel._draft.actions[0].todoDue = "any";
  assert.equal(panel._draftToRaw(panel._draft).actions[0].todo.due, undefined);
});

test("the to-do rule editor saves its due window and says it in the list", async () => {
  const panel = todoPanel();
  const sent = [];
  panel._hass = {
    callWS: async (message) => {
      sent.push(message);
      if (message.type === "daily_routine/overview") return structuredClone(panel._data);
      return message.type === "daily_routine/routine/save" ? { routine: { ...message.routine } } : {};
    },
  };
  panel._todoDraft = { ...panel._newTodoDraft(), watch_entity: "binary_sensor.dishwasher_clean", watch_states: "on", op: "complete", list: "todo.groceries", item: "Empty dishwasher", due: "0", overdue: true, undated: false };
  await panel._saveTodoDraft();
  const routine = sent.find((m) => m.type === "daily_routine/routine/save").routine;
  assert.deepEqual(routine.actions[0].todo.due, { within: 0, overdue: true, undated: false });

  assert.equal(panel._todoSentence(routine), "Complete “Empty dishwasher” on Groceries · due today");
});

test("timing card shows the four global settings, with defaults where none are saved yet", () => {
  const panel = makePanel();
  panel._data.settings = { ...panel._data.settings, list_wait_seconds: 45 };
  panel._view = { name: "settings" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /<h3>Timing<\/h3>/);
  assert.match(html, /data-setting="list_wait_seconds" value="45"/);
  assert.match(html, /data-setting="health_check_minutes" value="15"/);
  assert.match(html, /data-setting="health_alert_seconds" value="60"/);
  assert.match(html, /data-setting="startup_quiet_minutes" value="5"/);
  assert.match(html, /min="1" max="60" step="1" data-setting="health_check_minutes"/);
});

test("changing one timing box saves only that setting, and an empty box saves nothing", () => {
  const panel = makePanel();
  panel._view = { name: "settings" };
  const sent = [];
  panel._call = async (message) => {
    sent.push(message);
    return { settings: message.settings };
  };
  panel._onField({ target: { dataset: { setting: "health_check_minutes" }, value: "30" } });
  panel._onField({ target: { dataset: { setting: "list_wait_seconds" }, value: "" } });
  assert.equal(sent.length, 1);
  assert.equal(sent[0].type, "daily_routine/settings/save");
  assert.equal(sent[0].settings.health_check_minutes, 30);
  assert.deepEqual(sent[0].settings.hidden_people, ["person.skylight"]);
});

test("typing in a timing box does not save until the box is left", () => {
  const panel = makePanel();
  const sent = [];
  panel._call = async (message) => { sent.push(message); return { settings: message.settings }; };
  panel._onField({ target: { dataset: { setting: "startup_quiet_minutes" }, value: "7" } }, true);
  assert.equal(sent.length, 0);
});
