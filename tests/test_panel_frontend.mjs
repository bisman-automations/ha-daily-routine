// Smoke tests for the sidebar panel: rooms, room view, editor and draft round-trip.
// Run with: node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";

const defined = new Map();

class FakeElement {
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
  ],
  devices: [
    { id: "lamp_1", name: "Kitchen lamp", area_id: "kitchen", area_name: "Kitchen", model: "Lamp" },
    { id: "tv_1", name: "Living room TV", area_id: null, area_name: null, model: "TV" },
  ],
  people: [{ entity_id: "person.alex", name: "Alex", state: "home" }],
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
