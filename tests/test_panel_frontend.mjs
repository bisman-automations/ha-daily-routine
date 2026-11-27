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
    { id: "lamp_1", name: "Kitchen lamp", area_id: "kitchen", area_name: "Kitchen", model: "Lamp", integration: "Philips Hue" },
    { id: "tv_1", name: "Living room TV", area_id: null, area_name: null, model: "TV" },
    { id: "player_1", name: "Kitchen player", area_id: "kitchen", area_name: "Kitchen", model: null, integration: "Music Assistant", service: true },
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
  panel._me = { allowed: true, is_admin: false };
  // These tests check the full editor; the simple one has its own tests.
  panel._editorOverride = "full";
  return panel;
}

// Steps start folded to one line; open them all to check their fields.
function openSteps(panel) {
  for (const step of panel._draft.actions) step._open = true;
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
  assert.match(html, /Linked to: Kitchen lamp \(Philips Hue\)/);
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
  assert.match(html, /<option value="lamp_1" selected>Kitchen lamp \(Philips Hue\) · Kitchen</);
  // A device without a known integration shows its name alone.
  assert.match(html, /<option value="tv_1" >Living room TV</);
  // Services aren't offered, unless the routine is already linked to one.
  assert.doesNotMatch(html, /player_1/);
  panel._draft.device_id = "player_1";
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /<option value="player_1" selected>Kitchen player/);
  panel._draft.device_id = "lamp_1";
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
  assert.doesNotMatch(html, /<option[^>]*notify\./);
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
  openSteps(panel);
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /<option value="light.turn_off" selected>Turn off lights<\/option>/);
  assert.match(html, />Action</);
  assert.doesNotMatch(html, />light\.turn_off</);
  assert.doesNotMatch(html, /<datalist(?! id="dr-groups")/); // the group names are the only list
});

test("choosing Other shows a service name box, and picking a labelled action hides it", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "Blank", draft: { trigger: "manual", actions: [{ service: "light.turn_off", targetMode: "area" }] } }, "kitchen");
  panel._view = { name: "editor", room: "kitchen" };
  panel._draft.actions[0].custom = true;
  panel._draft.actions[0].service = "";
  openSteps(panel);
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /data-sf="service"/);

  panel._draft.actions[0].custom = false;
  panel._draft.actions[0].service = "fan.turn_on";
  openSteps(panel);
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
  // 1.34.0: a result code is put in words, never the raw service.
  assert.match(html, /failed: A step failed \(turn off\)/);
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
  panel._draft = panel._applyTemplate({ title: "Blank", draft: { trigger: "todo_done", actions: [{ service: "light.turn_on", targetMode: "all_areas" }] } }, null);
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
  panel._draft.actions = [{ kind: "service", service: "light.turn_on", targetMode: "entities", area: "", entities: ["light.kitchen"], data: "" }];
  panel._view = { name: "editor", room: null };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /data-f="notify_on_health"/);
  panel._onField({ target: { dataset: { f: "notify_on_health" }, type: "checkbox", checked: true, value: "on" } });
  assert.equal(panel._draftToRaw(panel._draft).notify_on_health, true);
  const loaded = panel._draftFromRoutine({ ...overview.routines[0], notify_on_health: true });
  assert.equal(loaded.notify_on_health, true);
});

test("the wait-for-list option is off for a new routine, shown, and saved", () => {
  const panel = makePanel();
  panel._draft = panel._newDraft(null);
  assert.equal(panel._draft.wait_when_unavailable, false);
  panel._draft.actions = [{ kind: "service", service: "light.turn_on", targetMode: "entities", area: "", entities: ["light.kitchen"], data: "" }];
  panel._view = { name: "editor", room: null };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /data-f="wait_when_unavailable"/);
  panel._onField({ target: { dataset: { f: "wait_when_unavailable" }, type: "checkbox", checked: true, value: "on" } });
  assert.equal(panel._draftToRaw(panel._draft).wait_when_unavailable, true);
  const loaded = panel._draftFromRoutine({ ...overview.routines[0], wait_when_unavailable: true });
  assert.equal(loaded.wait_when_unavailable, true);
});

test("a complete step offers the due-date choices, and a add step does not", () => {
  const panel = todoPanel();
  panel._draft = panel._newDraft(null);
  panel._draft.actions = [{ kind: "todo", todoOp: "complete", todoList: "todo.groceries", todoItem: "Milk", todoDue: "any", todoOverdue: true, todoUndated: false, service: "", targetMode: "none", area: "", entities: [], data: "" }];
  panel._view = { name: "editor", room: null };
  openSteps(panel);
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /data-sf="todoDue"/);
  assert.match(panel.shadowRoot.innerHTML, /<option value="0"[^>]*>Due today only<\/option>/);
  panel._draft.actions[0].todoOp = "add";
  openSteps(panel);
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

const activityFixture = {
  window: "7d",
  totals: { triggered: 5, ran: 2, failed: 1, skipped: 2, success_rate: 67 },
  by_routine: [
    {
      id: "lights", name: "Lights off", enabled: true, ran: 2, failed: 1, skipped: 2,
      last_at: "2026-10-25T14:00:00+00:00", last_outcome: "failed",
      last_failure: { at: "2026-10-25T14:00:00+00:00", reason: "missing_service:light.turn_off" },
      failing_in_a_row: 1,
    },
  ],
  failure_reasons: [{ reason: "missing_service:light.turn_off", count: 1 }],
  skip_reasons: [{ reason: "paused", count: 2 }],
  by_day: [{ day: "2026-10-25", ran: 2, failed: 1, skipped: 2 }],
  recent: [
    { at: "2026-10-25T14:00:00+00:00", routine_id: "lights", name: "Lights off", outcome: "failed", reason: "missing_service:light.turn_off", detail: "" },
    { at: "2026-10-25T13:00:00+00:00", routine_id: "lights", name: "Lights off", outcome: "skipped", reason: "paused", detail: "" },
    { at: "2026-10-25T12:00:00+00:00", routine_id: "lights", name: "Lights off", outcome: "ran", reason: "", detail: "Ran 2 steps" },
  ],
  quiet_routines: ["Lock up"],
  repeated_failures: [],
  health: [{ id: "lights", name: "Lights off", problems: ["Light is unavailable right now."] }],
};

test("activity tab shows totals, why runs failed or were skipped, and what needs attention", () => {
  const panel = makePanel();
  panel._view = { name: "activity" };
  panel._activity = structuredClone(activityFixture);
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /data-tab="activity"/);
  assert.match(html, /<strong>67%<\/strong>/);
  assert.match(html, /Action not found: light\.turn_off/);
  assert.match(html, /Routines were paused/);
  assert.match(html, /Lock up/);
  assert.match(html, /Light is unavailable right now\./);
  assert.match(html, /<td>Lights off<\/td>/);
});

test("activity filter keeps only the chosen outcome in recent activity", () => {
  const panel = makePanel();
  panel._view = { name: "activity" };
  panel._activity = structuredClone(activityFixture);
  panel._activityFilter = "failed";
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Action not found/);
  assert.doesNotMatch(html, /Ran 2 steps/);
  assert.doesNotMatch(html, /pill warn">Skipped/, "skipped runs are left out of the failed filter");
});

test("activity tab says it is loading until the first answer arrives", () => {
  const panel = makePanel();
  panel._view = { name: "activity" };
  panel._activity = null;
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /Loading activity/);
});

test("someone without access gets a page that says so, and no routines", async () => {
  const panel = makePanel();
  panel._data = null;
  panel._me = null;
  const calls = [];
  panel._hass = {
    user: { is_admin: false },
    callWS: async (msg) => {
      calls.push(msg.type);
      if (msg.type === "daily_routine/access/me") return { allowed: false, is_admin: false };
      throw new Error("not expected");
    },
  };
  await panel._load();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /You don(&#39;|')t have access to Daily Routine/);
  assert.deepEqual(calls, ["daily_routine/access/me"]);
});

test("admins pick who else can see Daily Routine in Settings, by name", async () => {
  const panel = makePanel();
  panel._view = { name: "settings" };
  panel._hass = { user: { is_admin: true } };
  panel._access = {
    people: [
      { id: "u1", name: "Parent", is_admin: true },
      { id: "u2", name: "Teen <3", is_admin: false },
      { id: "u3", name: "Guest", is_admin: false },
    ],
    ids: new Set(["u2"]),
  };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Who can see Daily Routine/);
  assert.match(html, /data-access-user="u2" checked/);
  assert.match(html, /data-access-user="u3"\s+>/);
  assert.doesNotMatch(html, /data-access-user="u1"/); // admins always can
  assert.match(html, /Teen &lt;3/);
  assert.match(html, /everyone sees Daily Routine in the sidebar/);
});

test("plain actions ask for their settings instead of JSON, and round-trip", () => {
  const panel = makePanel();
  panel._data.all_entities = [
    { entity_id: "tts.piper", name: "Piper", state: "unknown" },
    { entity_id: "media_player.kitchen", name: "Kitchen speaker", state: "idle" },
  ];
  const stored = {
    ...overview.routines[0],
    id: "wake_up", name: "Wake up", trigger: "time",
    actions: [
      { service: "tts.speak", target: { entity_id: ["tts.piper"] }, data: { message: "Good morning", media_player_entity_id: "media_player.kitchen" } },
      { service: "media_player.volume_set", target: { area_id: ["kitchen"] }, data: { volume_level: 0.35 } },
      { service: "climate.set_temperature", target: { area_id: ["kitchen"] }, data: { temperature: 20.5, hvac_mode: "heat" } },
    ],
  };
  const draft = panel._draftFromRoutine(stored);
  assert.equal(draft.actions[0].voice, "tts.piper");
  assert.deepEqual(draft.actions[0].fields, { message: "Good morning", media_player_entity_id: "media_player.kitchen" });
  assert.equal(draft.actions[1].fields.volume_level, 35);
  // The thermostat's mode is a field of its own now; nothing is left over.
  assert.equal(draft.actions[2].fields.hvac_mode, "heat");
  assert.equal(draft.actions[2].data, "");
  panel._draft = draft;
  panel._view = { name: "editor", room: "kitchen" };
  openSteps(panel);
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Say something on a speaker/);
  assert.match(html, /What to say/);
  assert.match(html, /Volume \(%\)/);
  assert.match(html, /Kitchen speaker/);
  assert.doesNotMatch(html.split("Step 1")[1].split("Step 2")[0], /Applies to/);
  const raw = panel._draftToRaw(draft);
  assert.deepEqual(raw.actions[0], stored.actions[0]);
  assert.equal(raw.actions[1].data.volume_level, 0.35);
  assert.deepEqual(raw.actions[2].data, { hvac_mode: "heat", temperature: 20.5 });

  draft.actions[0].fields.message = "";
  assert.throws(() => panel._draftToRaw(draft), /Step 1: fill in what to say/);
});

test("calendar day condition, varied time and skips round-trip through the editor", () => {
  const panel = makePanel();
  panel._data.all_entities = [{ entity_id: "calendar.school", name: "School", state: "off" }];
  const stored = {
    ...overview.routines[0],
    id: "school_run", name: "School run", trigger: "time", random_minutes: 10,
    paused_until: "2030-01-02", skip_next: true,
    conditions: [{ kind: "calendar", entity_id: "calendar.school", when: "no_event", match: "No school" }],
  };
  const draft = panel._draftFromRoutine(stored);
  const raw = panel._draftToRaw(draft);
  assert.deepEqual(raw.conditions, stored.conditions);
  assert.equal(raw.random_minutes, 10);
  assert.equal(raw.paused_until, "2030-01-02");
  assert.equal(raw.skip_next, true);
  panel._draft = draft;
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Calendar day/);
  assert.match(html, /Not on days with/);
  assert.match(html, /value="No school"/);
  assert.match(html, /Vary the time by up to/);
  assert.match(html, /data-f="skip_next" checked/);
  assert.match(html, /value="2030-01-02"/);
  assert.match(panel._describeTrigger(stored), /± 10 min/);
});

test("routine cards show skips and pauses, and the Routines tab offers Going away", () => {
  const panel = makePanel();
  panel._data.routines[0] = { ...panel._data.routines[0], skip_next: true, paused_until: "2099-12-31" };
  panel._view = { name: "all" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Going away\?/);
  assert.match(html, /1 routine is paused/);
  assert.match(html, /Paused until/);
  assert.match(html, /The next run will be skipped/);
  assert.match(html, /Don't skip/);
  assert.match(html, /data-act="away-pause"/);
  // A pause that's over isn't shown.
  panel._data.routines[0] = { ...panel._data.routines[0], skip_next: false, paused_until: "2000-01-01" };
  panel._render();
  assert.doesNotMatch(panel.shadowRoot.innerHTML, /Paused until/);
  assert.match(panel.shadowRoot.innerHTML, /Skip next run/);
});

test("conditions offer waiting until the end of the day, until a time, or skipping", () => {
  const panel = makePanel();
  panel._draft = panel._draftFromRoutine(panel._data.routines[0]);
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  let html = panel.shadowRoot.innerHTML;
  assert.match(html, /data-f="wait"/);
  assert.match(html, /<option value="day" selected>/);
  assert.doesNotMatch(html, /data-f="wait_until"/);

  panel._draft.wait = "until";
  panel._draft.wait_until = "09:30";
  panel._render();
  html = panel.shadowRoot.innerHTML;
  assert.match(html, /data-f="wait_until" value="09:30"/);
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.wait, "until");
  assert.equal(raw.wait_until, "09:30:00");

  const back = panel._draftFromRoutine({ ...panel._data.routines[0], wait: "no", wait_until: "18:15:00" });
  assert.equal(back.wait, "no");
  assert.equal(back.wait_until, "18:15");
});

test("a time routine without conditions has no waiting choice", () => {
  const panel = makePanel();
  panel._draft = panel._draftFromRoutine({ ...panel._data.routines[0], trigger: "time", sensors: [] });
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  assert.doesNotMatch(panel.shadowRoot.innerHTML, /data-f="wait"/);
});

test("a waiting routine's card says it runs as soon as its conditions are met", () => {
  const panel = makePanel();
  panel._view = { name: "room", room: "kitchen" };
  panel._data.statuses.kitchen_lights = { state: "idle", waiting: true, conditions_ok: false, wait_ends: new Date(2026, 9, 17, 0, 0).toISOString() };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /Runs as soon as its conditions are met, until midnight/);
});

test("editor sets what happens when a step can't change a device, and it round-trips", () => {
  const panel = makePanel();
  const stored = { ...panel._data.routines[0], on_step_error: "continue", retry_later_minutes: 10 };
  panel._draft = panel._draftFromRoutine(stored);
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /<option value="continue" selected>Keep going with the other steps/);
  assert.match(html, /<option value="10" selected>After 10 minutes \(up to 3 times\)/);
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.on_step_error, "continue");
  assert.equal(raw.retry_later_minutes, 10);
  // A routine saved before 1.18.0 has neither: stop, and try again after 5 minutes.
  const { on_step_error, retry_later_minutes, ...old } = stored;
  const fresh = panel._draftToRaw(panel._draftFromRoutine(old));
  assert.equal(fresh.on_step_error, "stop");
  assert.equal(fresh.retry_later_minutes, 5);
});

test("a routine that couldn't change a device offers Try again on its card", () => {
  const panel = makePanel();
  panel._view = { name: "room", room: "kitchen" };
  panel._render();
  assert.doesNotMatch(panel.shadowRoot.innerHTML, /data-act="try-again"/);
  panel._data.statuses.kitchen_lights = { ...panel._data.statuses.kitchen_lights, state: "error", can_try_again: true };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /data-act="try-again" data-id="kitchen_lights">Try again</);
});

test("light steps take an optional brightness and a fade in minutes", () => {
  const panel = makePanel();
  const stored = {
    ...panel._data.routines[0],
    actions: [{ service: "light.turn_on", target: { entity_id: ["light.kitchen"] }, data: { brightness_pct: 30, transition: 900 } }],
  };
  panel._draft = panel._draftFromRoutine(stored);
  assert.deepEqual(panel._draft.actions[0].fields, { brightness_pct: 30, transition: 15 });
  assert.deepEqual(panel._draftToRaw(panel._draft).actions[0].data, { brightness_pct: 30, transition: 900 });
  // Both are optional.
  panel._draft.actions[0].fields = {};
  assert.deepEqual(panel._draftToRaw(panel._draft).actions[0].data, {});
});

test("put back what was on has no target; remember offers lights, switches and fans", () => {
  const panel = makePanel();
  panel._draft = panel._draftFromRoutine({
    ...panel._data.routines[0],
    actions: [
      { service: "daily_routine.remember_states", target: { area_id: ["kitchen"] }, data: { name: "Before we left" } },
      { service: "daily_routine.put_back_states", data: { name: "Before we left" } },
    ],
  });
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Remember which lights and switches are on/);
  assert.match(html, /Put back what was on/);
  const raw = panel._draftToRaw(panel._draft);
  assert.deepEqual(raw.actions[0].target, { area_id: ["kitchen"] });
  assert.equal(raw.actions[1].target, undefined);
  assert.deepEqual(raw.actions[1].data, { name: "Before we left" });
});

test("dates and the heads-up round-trip and show on the card", () => {
  const panel = makePanel();
  const stored = { ...panel._data.routines[0], trigger: "time", time: "07:00:00", from_date: "11-01", until_date: "03-31", heads_up_minutes: 10 };
  panel._draft = panel._draftFromRoutine(stored);
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, new RegExp(`data-f="from_date" value="${new Date().getFullYear()}-11-01"`));
  assert.match(html, /<option value="10" selected>10 minutes before, with a Skip button/);
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.from_date, "11-01");
  assert.equal(raw.until_date, "03-31");
  assert.equal(raw.heads_up_minutes, 10);
  panel._data.routines[0] = { ...panel._data.routines[0], from_date: "11-01", until_date: "03-31" };
  panel._view = { name: "room", room: "kitchen" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /1 Nov to 31 Mar/);
});

test("routines that react to something happening don't wait all day by default", () => {
  const panel = makePanel();
  panel._draft = panel._newDraft("kitchen");
  assert.equal(panel._draft.wait, "day"); // a time routine
  panel._draft.conditions = [{ kind: "state", entity_id: "lock.garage", state: "locked" }];
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  const select = { dataset: { f: "trigger" }, value: "someone_arrives", type: "select-one", closest: () => null };
  panel._onField({ target: select, composedPath: () => [select] });
  assert.equal(panel._draft.trigger, "someone_arrives");
  assert.equal(panel._draft.wait, "no");
  panel._draft.wait = "minutes";
  panel._draft.wait_minutes = 10;
  panel._draft.name = "Unlock on arrival";
  panel._draft.actions[0].service = "lock.unlock";
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /<option value="minutes" selected>Wait a few minutes/);
  assert.match(html, /data-f="wait_minutes" value="10"/);
  assert.match(html, /reacts to something happening/);
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.wait, "minutes");
  assert.equal(raw.wait_minutes, 10);
});

test("routines that react to something happening can wait before running again", () => {
  const panel = makePanel();
  panel._draft = panel._draftFromRoutine({ ...panel._data.routines[0], trigger: "state_is", watch_entity: "binary_sensor.door", watch_states: ["on"], cooldown_minutes: 10 });
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /data-f="cooldown_minutes" value="10"/);
  assert.equal(panel._draftToRaw(panel._draft).cooldown_minutes, 10);
  // Not offered for a time routine.
  panel._draft = panel._draftFromRoutine({ ...panel._data.routines[0], trigger: "time", time: "07:00:00" });
  panel._render();
  assert.doesNotMatch(panel.shadowRoot.innerHTML, /data-f="cooldown_minutes"/);
});

test("integration order: Settings lists linked integrations, and a routine can have its own", async () => {
  const panel = makePanel();
  panel._integrations = [
    { domain: "matter", name: "Matter", local: true },
    { domain: "switchbot", name: "SwitchBot Bluetooth", local: true },
    { domain: "switchbot_cloud", name: "SwitchBot Cloud", local: false },
  ];
  panel._data.settings = { ...(panel._data.settings || {}), hidden_people: [], notify_services: [], integration_order: ["switchbot"] };
  panel._view = { name: "settings" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Devices in more than one integration/);
  assert.match(html, /pair of curtains that also exist separately/);
  assert.match(html, /only the left or right leaves the pair alone/);
  // The saved order first, then the rest.
  assert.deepEqual(panel._fullOrder(["switchbot"]), ["switchbot", "matter", "switchbot_cloud"]);
  assert.match(html, /SwitchBot Cloud <span class="muted small">cloud/);

  panel._draft = panel._draftFromRoutine({ ...panel._data.routines[0], integration_order: ["matter", "switchbot", "switchbot_cloud"] });
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /<option value="1" selected>Its own order/);
  assert.deepEqual(panel._draftToRaw(panel._draft).integration_order, ["matter", "switchbot", "switchbot_cloud"]);
  panel._draft.integration_order = null;
  assert.equal(panel._draftToRaw(panel._draft).integration_order, null);
});

// ----- 1.22.0 -----

test("a running routine shows its step and a progress bar, and Run now is held", () => {
  const panel = makePanel();
  panel._data.statuses.kitchen_lights = {
    state: "running", conditions_ok: true,
    progress: { step: 2, total: 4, label: "Close Bedroom curtains", started: new Date().toISOString(), until: null },
  };
  panel._view = { name: "room", room: "kitchen" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Step 2 of 4 · Close Bedroom curtains/);
  assert.match(html, /role="progressbar"[^>]*aria-valuenow="1"/);
  // 1.34.0: a step with no end time is shown half way through it, moving.
  assert.match(html, /class="bar working" style="width:38%"/);
  assert.match(html, /disabled>Running…/);
  panel._view = { name: "rooms" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /Running now/);
  assert.match(panel.shadowRoot.innerHTML, /1 running/);
});

test("a run just started shows Starting… before the first step arrives", () => {
  const panel = makePanel();
  panel._data.statuses.kitchen_lights = { state: "running", progress: null };
  panel._view = { name: "room", room: "kitchen" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /progress indeterminate/);
  assert.match(panel.shadowRoot.innerHTML, /Starting…/);
});

test("pushed run states redraw the cards and reload Activity when it changes", () => {
  const panel = makePanel();
  let loaded = 0;
  panel._loadActivity = () => { loaded += 1; };
  panel._view = { name: "activity" };
  panel._onStatus({ revision: 1, paused: false, statuses: overview.statuses, log_count: 3, mode: "night" });
  assert.equal(panel._data.mode, "night");
  panel._onStatus({ revision: 1, paused: false, statuses: overview.statuses, log_count: 4, mode: "night" });
  assert.equal(loaded, 1);
  clearInterval(panel._ticker);
});

test("more triggers, new steps and only-if round-trip through the editor", () => {
  const panel = makePanel();
  const routine = {
    ...overview.routines[0], trigger: "time", sensors: [], clear_minutes: 0,
    more_triggers: [
      { trigger: "everyone_leaves", time: "07:00:00", offset_minutes: 0, people: ["person.alex"], zone: "zone.home", modes: [], value_entity: null, value_threshold: null, watch_entity: null, watch_states: [], hold_minutes: 0 },
      { trigger: "mode_changes", time: "07:00:00", offset_minutes: 0, people: [], zone: "zone.home", modes: ["night"], value_entity: null, value_threshold: null, watch_entity: null, watch_states: [], hold_minutes: 0 },
    ],
    actions: [
      { run_routine: "other" },
      { set_mode: "away" },
      { wait_until: { entity_id: "cover.garage", states: ["closed"], minutes: 5, on_timeout: "stop" } },
      { service: "light.turn_off", target: { area_id: ["kitchen"] }, data: {}, only_if: { kind: "weather", entity_id: "weather.home", test: "is_not", conditions: ["rainy"] } },
    ],
  };
  panel._draft = panel._draftFromRoutine(routine);
  panel._view = { name: "editor", room: "kitchen" };
  openSteps(panel);
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Or also when/);
  assert.match(html, /data-xi="0" data-xpeople="person.alex" checked/);
  assert.match(html, /data-xi="1" data-xmode="night" checked/);
  assert.match(html, /Run another routine/);
  assert.match(html, /Wait up to \(minutes\)/);
  assert.match(html, /Only if/);
  const raw = panel._draftToRaw(panel._draft);
  assert.deepEqual(raw.actions.slice(0, 3), routine.actions.slice(0, 3));
  assert.deepEqual(raw.actions[3].only_if, routine.actions[3].only_if);
  assert.equal(raw.more_triggers.length, 2);
  assert.deepEqual(raw.more_triggers[1].modes, ["night"]);
  assert.deepEqual(raw.more_triggers[0].people, ["person.alex"]);
  assert.match(panel._describeTrigger(routine), /or When everyone leaves/);
  assert.match(panel._describeTrigger(routine), /House mode changes to Night/);
});

test("house mode, workday and weather conditions are offered and saved", () => {
  const panel = makePanel();
  panel._data.workday_sensors = [{ entity_id: "binary_sensor.workday_sensor", name: "Workday" }];
  panel._data.weathers = [{ entity_id: "weather.home", name: "Home" }];
  panel._draft = panel._applyTemplate({ title: "Blank routine", draft: {} }, null);
  panel._draft.conditions.push({ kind: "mode", modes: ["home"] }, { kind: "workday", entity_id: "binary_sensor.workday_sensor", when: "day_off" }, { kind: "weather", entity_id: "weather.home", test: "above", value: "25" });
  Object.assign(panel._draft.actions[0], { service: panel._draft.actions[0].service || "light.turn_on", targetMode: "entities", entities: ["light.kitchen"] });
  panel._view = { name: "editor", room: null };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /\+ House mode/);
  assert.match(html, /On days off/);
  assert.match(html, /The temperature is above/);
  panel._draft.name = "x";
  panel._draft.actions[0].service = "light.turn_off";
  const raw = panel._draftToRaw(panel._draft);
  assert.deepEqual(raw.conditions, [
    { kind: "mode", modes: ["home"] },
    { kind: "workday", entity_id: "binary_sensor.workday_sensor", when: "day_off" },
    { kind: "weather", entity_id: "weather.home", test: "above", value: 25 },
  ]);
});

test("Today shows what's still to come with Skip and Run now, and what already ran", () => {
  const panel = makePanel();
  panel._view = { name: "today" };
  panel._today = {
    mode: "home", paused: false,
    planned: [{ at: "2026-11-14T20:00:00", routine_id: "kitchen_lights", name: "Kitchen lights off", room: "kitchen", skip_next: false, paused_until: null }],
    done: [{ at: "2026-11-14T07:00:00", routine_id: "kitchen_lights", name: "Kitchen lights off", outcome: "skipped", reason: "cooldown", detail: "" }],
    watching: [{ routine_id: "kitchen_lights", name: "Kitchen lights off", room: "kitchen" }],
  };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Still to come/);
  assert.match(html, /data-act="skip-next" data-id="kitchen_lights" data-skip="1">Skip/);
  assert.match(html, /Already today/);
  assert.match(html, /Runs when something happens/);
  assert.match(html, /role="tab" data-act="tab" data-tab="today"/);
});

test("Settings has lived-in lights, and the header a house mode picker for admins", () => {
  const panel = makePanel();
  panel._hass = { user: { is_admin: true } };
  panel._data.all_entities = [{ entity_id: "light.porch", name: "Porch light" }, { entity_id: "lock.front", name: "Front door" }];
  panel._data.settings.lived_in = { enabled: true, lights: ["light.porch"], start: "sunset", end: "23:00", modes: ["away", "vacation"] };
  panel._data.lived_in = { active: true, on: ["light.porch"] };
  panel._data.mode = "away";
  panel._view = { name: "settings" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Lived-in lights/);
  assert.match(html, /data-lived="light" data-val="light.porch" checked/);
  assert.doesNotMatch(html, /data-val="lock.front"/);
  assert.match(html, /On now/);
  assert.match(html, /data-mode-pick[^>]*>.*value="away" selected/s);
});

test("any action gets fields from Home Assistant's description, not JSON", () => {
  const panel = makePanel();
  panel.hass = {
    services: {
      media_player: {
        play_media: {
          name: "Play media",
          fields: {
            media: { name: "Media", required: true, selector: { text: {} } },
            enqueue: { name: "Enqueue", selector: { select: { options: ["play", "next", "add"] } } },
            announce: { name: "Announce", selector: { boolean: {} } },
            advanced: { fields: { volume: { name: "Volume", selector: { number: { min: 0, max: 100, unit_of_measurement: "%" } } } } },
          },
        },
      },
    },
  };
  panel._data = structuredClone(overview);
  const stored = {
    ...overview.routines[0], id: "music", name: "Music", trigger: "time",
    actions: [{ service: "media_player.play_media", target: { entity_id: ["media_player.kitchen"] }, data: { media: "jazz", enqueue: "next", announce: true, volume: 30 } }],
  };
  const draft = panel._draftFromRoutine(stored);
  assert.equal(draft.actions[0].data, "");
  assert.deepEqual(draft.actions[0].fields, { media: "jazz", enqueue: "next", announce: true, volume: 30 });
  panel._draft = draft;
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Enqueue/);
  assert.match(html, /value="next" selected/);
  assert.match(html, /Volume \(%\)/);
  assert.doesNotMatch(html, /optional JSON/);
  assert.deepEqual(panel._draftToRaw(draft).actions[0].data, { media: "jazz", enqueue: "next", announce: true, volume: 30 });
});

test("light colors and white temperature are fields", () => {
  const panel = makePanel();
  const stored = {
    ...overview.routines[0], id: "warm", name: "Warm", trigger: "time",
    actions: [{ service: "light.turn_on", target: { area_id: ["kitchen"] }, data: { rgb_color: [255, 128, 0], color_temp_kelvin: 2700 } }],
  };
  const draft = panel._draftFromRoutine(stored);
  assert.equal(draft.actions[0].fields.rgb_color, "#ff8000");
  assert.deepEqual(panel._draftToRaw(draft).actions[0].data, { rgb_color: [255, 128, 0], color_temp_kelvin: 2700 });
});

// ----- 1.24.0 -----

test("the simple editor starts with a sentence and adds the rest from chips", () => {
  const panel = makePanel();
  panel._editorOverride = null;
  panel._draft = panel._draftFromRoutine(overview.routines[0]);
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  let html = panel.shadowRoot.innerHTML;
  assert.match(html, /class="editor simple"/);
  assert.match(html, /<span class="word">When<\/span>/);
  assert.match(html, /data-act="open-section" data-key="ask">\+ Ask me first/);
  assert.match(html, /Switch to the full editor/);
  assert.doesNotMatch(html, /<h3>Ask me first<\/h3>/);
  panel._simpleOpen.add("ask");
  panel._render();
  html = panel.shadowRoot.innerHTML;
  assert.match(html, /<h3>Ask me first<\/h3>/);
  panel._editorSession = "full";
  panel._render();
  assert.doesNotMatch(panel.shadowRoot.innerHTML, /class="editor simple"/);
  assert.match(panel.shadowRoot.innerHTML, /Switch to the simple editor/);
});

test("the editor follows Settings unless this device picked its own", () => {
  const panel = makePanel();
  panel._editorOverride = null;
  assert.equal(panel._editorMode(), "simple");
  panel._data.settings.editor = "full";
  assert.equal(panel._editorMode(), "full");
  panel._view = { name: "settings" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /Routine editor/);
  assert.match(panel.shadowRoot.innerHTML, /data-editor-default>[\s\S]*value="full" selected/);
});

test("steps fold to one line and steps at the same time round-trip", () => {
  const panel = makePanel();
  const routine = {
    ...overview.routines[0],
    actions: [
      { together: [
        { service: "light.turn_off", target: { area_id: ["kitchen"] }, data: {} },
        { service: "fan.turn_off", target: { entity_id: ["fan.bathroom_fan"] }, data: {} },
      ] },
      { delay: 30 },
    ],
  };
  panel._draft = panel._draftFromRoutine(routine);
  assert.equal(panel._draft.actions.length, 3);
  assert.equal(panel._draft.actions[1].withPrev, true);
  panel._view = { name: "editor", room: "kitchen" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /data-act="toggle-step"/);
  openSteps(panel);
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /class="step-fold/);
  assert.match(html, /draggable/);
  assert.match(html, /At the same time as the step before/);
  const raw = panel._draftToRaw(panel._draft);
  assert.deepEqual(raw.actions, routine.actions);
});

test("before an alarm, a calendar event, the forecast and the cheapest hour round-trip", () => {
  const panel = makePanel();
  panel._data.alarm_sensors = [{ entity_id: "sensor.phone_next_alarm", name: "Phone Next alarm" }];
  panel._data.weathers = [{ entity_id: "weather.home", name: "Home" }];
  const cases = [
    { trigger: "alarm", watch_entity: "sensor.phone_next_alarm", offset_minutes: -15 },
    { trigger: "calendar_event", watch_entity: "calendar.family", watch_item: "School", offset_minutes: -30 },
    { trigger: "forecast", watch_entity: "weather.home", watch_states: ["rainy"], forecast_hours: 6 },
    { trigger: "cheapest", value_entity: "sensor.price", active_from: "00:00", active_to: "06:00" },
  ];
  for (const extra of cases) {
    const routine = { ...overview.routines[0], sensors: [], clear_minutes: 0, ...extra };
    panel._draft = panel._draftFromRoutine(routine);
    panel._view = { name: "editor", room: "kitchen" };
    panel._render();
    const raw = panel._draftToRaw(panel._draft);
    assert.equal(raw.trigger, extra.trigger);
    for (const [key, value] of Object.entries(extra)) assert.deepEqual(raw[key], value, `${extra.trigger}: ${key}`);
    assert.ok(panel._describeTrigger(routine).length > 0);
  }
});

test("Ask me first round-trips and an asking routine shows Run it and Skip it", () => {
  const panel = makePanel();
  const routine = { ...overview.routines[0], ask_first: true, ask_minutes: 5, ask_default: "skip" };
  panel._draft = panel._draftFromRoutine(routine);
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.ask_first, true);
  assert.equal(raw.ask_minutes, 5);
  assert.equal(raw.ask_default, "skip");
  panel._data.statuses.kitchen_lights = { state: "asking", ask_until: "2026-11-14T07:10:00", conditions_ok: true };
  panel._view = { name: "room", room: "kitchen" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /data-act="answer"[^>]*>Run it/);
  assert.match(html, /Skip it/);
});

test("Copy to rooms lists the other rooms", () => {
  const panel = makePanel();
  panel._copyFor = "kitchen_lights";
  panel._view = { name: "room", room: "kitchen" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /data-copy-room="bedroom"/);
  assert.doesNotMatch(html, /data-copy-room="kitchen"/);
  assert.match(html, /data-act="copy-do" data-id="kitchen_lights"/);
});

test("ideas come from what the home has and no routine covers yet", () => {
  const panel = makePanel();
  panel._data.alarm_sensors = [{ entity_id: "sensor.phone_next_alarm", name: "Phone Next alarm" }];
  panel._data.weathers = [{ entity_id: "weather.home", name: "Home" }];
  const ideas = panel._suggestions().map((i) => i.id);
  assert.ok(ideas.includes("idea:fan:bathroom"));
  assert.ok(ideas.includes("idea:wake:sensor.phone_next_alarm"));
  assert.ok(ideas.includes("idea:rain"));
  // The kitchen already turns its lights off when it's empty.
  assert.ok(!ideas.includes("idea:empty:kitchen"));
  panel._view = { name: "templates", room: null };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /Ideas for your home/);
});

test("Activity lists devices that gave trouble", () => {
  const panel = makePanel();
  panel._view = { name: "activity" };
  panel._activity = {
    ...structuredClone(activityFixture),
    devices: [{ entity_id: "light.porch", name: "Porch light", ok: 3, retried: 1, failed: 2, success_rate: 67, last_problem: { at: "2026-11-14T07:00:00", reason: "didn't change" } }],
  };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Devices that gave trouble/);
  assert.match(html, /Porch light/);
  assert.match(html, /67%/);
});

// ----- 1.25.0 -----

test("a run can be replayed step by step on the card and in Activity", () => {
  const panel = makePanel();
  const steps = [
    { step: 1, label: "Turn off Kitchen light", result: "ok", seconds: 1.2, devices: [{ entity_id: "light.kitchen", name: "Kitchen light", state: "off" }] },
    { step: 2, label: "Close blinds", result: "failed", seconds: 30, note: "Blinds didn't change" },
    { step: 3, label: "Turn on fan", result: "skipped", seconds: 0, note: "Already done" },
  ];
  panel._data.statuses.kitchen_lights = { state: "idle", conditions_ok: true, history: [{ at: "2026-11-15T07:00:00", outcome: "failed", detail: "x", steps }] };
  panel._view = { name: "room", room: "kitchen" };
  panel._render();
  let html = panel.shadowRoot.innerHTML;
  assert.match(html, /see each step/);
  assert.match(html, /class="r-failed"/);
  assert.match(html, /Kitchen light: off/);
  assert.match(html, /Blinds didn&#39;t change/);
  panel._view = { name: "activity" };
  panel._activity = { ...structuredClone(activityFixture), recent: [{ at: "2026-11-15T07:00:00", name: "Kitchen lights off", outcome: "failed", reason: "error:x", steps }] };
  panel._activityFilter = "all";
  panel._render();
  html = panel.shadowRoot.innerHTML;
  assert.match(html, /See each step/);
});

test("tag and button triggers round-trip and describe themselves", () => {
  const panel = makePanel();
  panel._tags = [{ id: "door-tag", name: "Front door" }];
  panel._data.all_entities = [{ entity_id: "event.pico_button", name: "Pico" }, { entity_id: "light.kitchen", name: "Kitchen light" }];
  for (const extra of [
    { trigger: "tag", watch_item: "door-tag" },
    { trigger: "button", watch_entity: "event.pico_button", watch_states: ["double"] },
  ]) {
    const routine = { ...overview.routines[0], sensors: [], clear_minutes: 0, ...extra };
    panel._draft = panel._draftFromRoutine(routine);
    panel._view = { name: "editor", room: "kitchen" };
    panel._render();
    const html = panel.shadowRoot.innerHTML;
    if (extra.trigger === "tag") assert.match(html, /value="door-tag" selected>Front door/);
    else assert.match(html, /value="event.pico_button" selected>Pico/);
    const raw = panel._draftToRaw(panel._draft);
    for (const [key, value] of Object.entries(extra)) assert.deepEqual(raw[key], value);
  }
  assert.match(panel._describeTrigger({ ...overview.routines[0], trigger: "tag", watch_item: "door-tag" }), /Front door/);
  assert.match(panel._describeTrigger({ ...overview.routines[0], trigger: "button", watch_entity: "event.pico_button", watch_states: ["double"] }), /Pico is pressed \(double\)/);
});

test("make it look like this saves how devices look and round-trips", () => {
  const panel = makePanel();
  panel._hass = { states: { "light.kitchen": { entity_id: "light.kitchen", state: "on", attributes: { brightness: 128, color_temp_kelvin: 2700, friendly_name: "Kitchen light" } } }, user: { is_admin: true } };
  panel._data.all_entities = [{ entity_id: "light.kitchen", name: "Kitchen light" }];
  const routine = { ...overview.routines[0], actions: [{ service: "scene.apply", data: { entities: { "light.kitchen": { state: "on", brightness: 128 } }, transition: 120 } }] };
  panel._draft = panel._draftFromRoutine(routine);
  assert.equal(panel._draft.actions[0].kind, "scene");
  assert.equal(panel._draft.actions[0].transition, 120);
  panel._draft.actions[0].transition = 2;
  panel._view = { name: "editor", room: "kitchen" };
  openSteps(panel);
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Save how they look now/);
  assert.match(html, /Kitchen light<\/strong>: on, 50%/);
  const raw = panel._draftToRaw(panel._draft);
  assert.deepEqual(raw.actions[0], { service: "scene.apply", data: { entities: { "light.kitchen": { state: "on", brightness: 128 } }, transition: 120 } });
  panel._draft.actions[0].saved = {};
  assert.throws(() => panel._draftToRaw(panel._draft), /Save how they look now/);
});

test("wait until a level, held for a while, round-trips", () => {
  const panel = makePanel();
  const wait = { entity_id: "sensor.bathroom_humidity", states: [], minutes: 60, on_timeout: "stop", below: 5, for_minutes: 3 };
  panel._draft = panel._draftFromRoutine({ ...overview.routines[0], actions: [{ wait_until: wait }] });
  assert.equal(panel._draft.actions[0].wBy, "level");
  panel._view = { name: "editor", room: "kitchen" };
  openSteps(panel);
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /stays so for/);
  assert.deepEqual(panel._draftToRaw(panel._draft).actions[0], { wait_until: wait });
  assert.match(panel._stepSummary(panel._draft.actions[0]), /below 5 for 3 min/);
});

test("a routine can be shared as a code and imported, rooms matched by name", () => {
  const panel = makePanel();
  panel._shareFor = "kitchen_lights";
  panel._view = { name: "room", room: "kitchen" };
  panel._render();
  const match = panel.shadowRoot.innerHTML.match(/data-share-code readonly rows="4">([^<]+)</);
  assert.ok(match && match[1].startsWith("DR1:"));
  // Another home: the same room name, another id.
  panel._data.areas = [{ id: "cucina", name: "Kitchen", entities: [] }];
  panel._data.all_entities = [];
  const { draft, missing } = panel._importRoutine(match[1]);
  assert.equal(draft.room, "cucina");
  assert.equal(draft.isNew, true);
  assert.deepEqual(missing, []);
  assert.throws(() => panel._importRoutine("nonsense"), /Daily Routine code/);
});

test("vacation and the daily summary show in the panel", () => {
  const panel = makePanel();
  panel._me = { allowed: true, is_admin: true };
  panel._view = { name: "all" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /Start vacation/);
  panel._data.settings.vacation = { until: "2026-11-20", routines: ["kitchen_lights"] };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /On vacation until/);
  assert.match(panel.shadowRoot.innerHTML, /data-act="vacation-end"/);
  panel._hass = { user: { is_admin: true } };
  panel._view = { name: "settings" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /Daily summary/);
  assert.match(panel.shadowRoot.innerHTML, /data-summary="only_away"/);
});

// ----- 1.26.0 -----

test("ideas from habits become routines, and can be dismissed", () => {
  const panel = makePanel();
  panel._hass = { user: { is_admin: true } };
  panel._data.habits = [{ id: "light.porch:on:18:35", entity_id: "light.porch", on: true, time: "18:35:00", days: ["mon", "tue", "wed", "thu", "fri"], count: 6, name: "Porch", service: "light.turn_on" }];
  const [idea] = panel._habitIdeas();
  assert.equal(idea.title, "Turn on Porch at 18:35");
  assert.match(idea.hint, /6 of the last 14 days.*on weekdays/);
  panel._view = { name: "templates", room: null };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /From what you do by hand/);
  assert.match(html, /data-act="idea-dismiss" data-idea="light.porch:on:18:35"/);
  const draft = panel._applyTemplate(idea, null);
  const raw = panel._draftToRaw(draft);
  assert.equal(raw.time, "18:35:00");
  assert.deepEqual(raw.days, ["mon", "tue", "wed", "thu", "fri"]);
  assert.deepEqual(raw.actions, [{ service: "light.turn_on", data: {}, target: { entity_id: ["light.porch"] } }]);
});

test("volume ramp, rooms someone is in, kind of day and group round-trip", () => {
  const panel = makePanel();
  panel._data.settings.day_types = [{ name: "School day", calendar: "calendar.school", words: [] }];
  const routine = {
    ...panel._data.routines[0],
    group: "Mornings",
    conditions: [{ kind: "day_type", types: ["School day"] }],
    actions: [
      { service: "light.turn_on", data: {}, target: { area_id: ["kitchen"] }, rooms: "occupied" },
      { volume_ramp: { entity_id: ["media_player.kitchen"], from: 5, to: 30, minutes: 10 } },
    ],
  };
  panel._draft = panel._draftFromRoutine(routine);
  assert.equal(panel._draft.actions[1].kind, "ramp");
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.group, "Mornings");
  assert.deepEqual(raw.conditions, [{ kind: "day_type", types: ["School day"] }]);
  assert.equal(raw.actions[0].rooms, "occupied");
  assert.deepEqual(raw.actions[1], { volume_ramp: { entity_id: ["media_player.kitchen"], from: 5, to: 30, minutes: 10 } });
  panel._view = { name: "editor", room: "kitchen" };
  openSteps(panel);
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Only rooms someone is in/);
  assert.match(html, /Turn the volume up slowly/);
  assert.match(html, /data-cdaytype="School day" checked/);
  assert.match(html, /data-cdaytype="Normal day" >/);
  assert.match(html, /data-f="group" maxlength="40" list="dr-groups" value="Mornings"/);
  // A ramp needs speakers; rooms only go with rooms.
  panel._draft.actions[1].entities = [];
  assert.throws(() => panel._draftToRaw(panel._draft), /pick the speakers/);
  panel._draft.actions[1].entities = ["media_player.kitchen"];
  panel._draft.actions[0].targetMode = "entities";
  panel._draft.actions[0].entities = ["light.kitchen"];
  assert.equal(panel._draftToRaw(panel._draft).actions[0].rooms, undefined);
});

test("routines are listed by group, with pause buttons for admins", () => {
  const panel = makePanel();
  panel._hass = { user: { is_admin: true } };
  const base = panel._data.routines[0];
  panel._data.routines = [
    { ...base, id: "a", name: "A", group: "Bedtime" },
    { ...base, id: "b", name: "B", group: "" },
  ];
  panel._data.statuses = { a: { state: "idle" }, b: { state: "idle" } };
  panel._view = { name: "all" };
  const html = panel._allResults();
  assert.match(html, /<h2>Bedtime <span class="muted small">1<\/span><\/h2>/);
  assert.match(html, /data-act="group-pause" data-group="Bedtime" data-days="6"/);
  assert.match(html, /Not in a group/);
  assert.ok(html.indexOf("Bedtime") < html.indexOf("Not in a group"));
});

test("a routine card shows the power before and after its last run", () => {
  const panel = makePanel();
  const routine = panel._data.routines[0];
  panel._data.statuses[routine.id] = { state: "idle", power: { last: { before: 640, after: 85 }, average_change: -1520, runs: 3 } };
  const html = panel._routineCard(routine);
  assert.match(html, /⚡ Last run: 640 W → 85 W · saves about 1.5 kW on average/);
});

test("every library routine opens in the editor", () => {
  const panel = makePanel();
  panel._view = { name: "templates", room: null };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /<strong>Library<\/strong>/);
  const ids = [...panel.shadowRoot.innerHTML.matchAll(/data-tpl="(lib_[a-z_]+)"/g)].map((m) => m[1]);
  assert.ok(ids.includes("lib_goodnight") && !ids.includes("lib_gentle_wake"));
  panel._view = { name: "templates", room: "kitchen" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /data-tpl="lib_gentle_wake"/);
  const wake = panel._applyTemplate({ id: "x", title: "Gentle wake-up", draft: { trigger: "time", time: "06:45:00", days: ["mon"], actions: [{ kind: "ramp", entities: ["media_player.kitchen"], rFrom: 5, rTo: 30, rMinutes: 15 }] } }, "kitchen");
  assert.deepEqual(panel._draftToRaw(wake).actions[0].volume_ramp, { entity_id: ["media_player.kitchen"], from: 5, to: 30, minutes: 15 });
});

test("device lists show each device's integration", () => {
  const panel = makePanel();
  panel._data.integrations = { "light.a": "Philips Hue", "light.b": "Matter" };
  const option = { value: "light.a", textContent: "Entry light", dataset: {} };
  const plain = { value: "", textContent: "Choose…", dataset: {} };
  const appended = [];
  const input = { dataset: { lived: "light", val: "light.b" }, value: "on" };
  const label = { querySelector: (sel) => (sel === "input" ? input : appended.length ? appended[0] : null), append: (el) => appended.push(el) };
  globalThis.document = { createElement: () => ({}) };
  panel.shadowRoot.querySelectorAll = (sel) => (sel === "select option" ? [option, plain] : sel === ".checks label" ? [label] : []);
  panel._labelIntegrations();
  panel._labelIntegrations(); // twice: still labelled once
  assert.equal(option.textContent, "Entry light (Philips Hue)");
  assert.equal(plain.textContent, "Choose…");
  assert.equal(appended.length, 1);
  assert.equal(appended[0].textContent, " (Matter)");
  delete globalThis.document;
});

// ----- 1.27.0 -----

test("Why not? lists the reasons in plain words", async () => {
  const panel = makePanel();
  const routine = panel._data.routines[0];
  panel._why = { [routine.id]: { reasons: ["skip_mode", "wrong_mode"], next_run: null, last_skip: { at: "2026-11-14T07:00:00", reason: "cooldown" }, unavailable: ["Kitchen list"] } };
  const html = panel._routineCard(routine);
  assert.match(html, /data-act="why"/);
  assert.match(html, /the house is in a mode it doesn(&#39;|')t run in/);
  assert.match(html, /the house isn(&#39;|')t in the right mode/);
  assert.match(html, /Waiting for: Kitchen list/);
  assert.match(html, /Last skipped .*it ran a moment ago/);
  panel._why = { [routine.id]: { reasons: [], next_run: null, last_skip: null, unavailable: [] } };
  assert.match(panel._routineCard(routine), /Nothing is stopping it/);
});

test("sun limits, house modes it skips and waiting for cheaper electricity round-trip", () => {
  const panel = makePanel();
  const routine = {
    ...panel._data.routines[0], trigger: "sunset", not_before: "18:00:00", not_after: "20:30:00", skip_modes: ["guests"],
    actions: [{ wait_cheaper: { entity_id: "sensor.price", minutes: 120 } }, { service: "light.turn_on", data: {}, target: { area_id: ["kitchen"] } }],
  };
  panel._draft = panel._draftFromRoutine(routine);
  assert.equal(panel._draft.actions[0].kind, "cheaper");
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.not_before, "18:00:00");
  assert.equal(raw.not_after, "20:30:00");
  assert.deepEqual(raw.skip_modes, ["guests"]);
  assert.deepEqual(raw.actions[0], { wait_cheaper: { entity_id: "sensor.price", minutes: 120 } });
  panel._view = { name: "editor", room: "kitchen" };
  openSteps(panel);
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /But not before/);
  assert.match(html, /data-skipmode="guests" checked/);
  assert.match(html, /Wait for cheaper electricity/);
  // A time trigger has no sun limits.
  panel._draft.trigger = "time";
  assert.equal(panel._draftToRaw(panel._draft).not_before, null);
  panel._draft.actions[0].cEntity = "";
  assert.throws(() => panel._draftToRaw(panel._draft), /price sensor/);
});

test("habit ideas carry their conditions, and the weekly report has a card", () => {
  const panel = makePanel();
  panel._hass = { user: { is_admin: true } };
  panel._data.habits = [{ id: "x", entity_id: "light.porch", on: true, time: "18:35:00", days: ["mon", "tue", "wed", "thu", "fri"], count: 6, name: "Porch", service: "light.turn_on", when_dark: true, someone_home: true }];
  const [idea] = panel._habitIdeas();
  assert.match(idea.hint, /when it was dark and someone was home.*only then/);
  const draft = panel._applyTemplate(idea, null);
  const raw = panel._draftToRaw(draft);
  assert.deepEqual(raw.conditions.map((c) => c.kind), ["state", "presence"]);
  assert.equal(raw.conditions[0].state, "below_horizon");
  panel._data.settings.weekly_report = { enabled: true, weekday: 0, time: "08:00" };
  panel._view = { name: "settings" };
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Weekly report/);
  assert.match(html, /<option value="0" selected>Monday<\/option>/);
  assert.match(html, /data-act="weekly-send"/);
});

// ----- 1.28.0 -----

test("briefing step and forecast condition round-trip", () => {
  const panel = makePanel();
  panel._data.weathers = [{ entity_id: "weather.home", name: "Home" }];
  const routine = {
    ...panel._data.routines[0],
    conditions: [{ kind: "forecast", entity_id: "weather.home", conditions: ["rainy"], below: null, hours: 4, when: "not_coming" }],
    actions: [{ briefing: { voice: "tts.piper", speaker: "media_player.kitchen", weather: "weather.home", calendars: ["calendar.family"], day_type: true } }],
  };
  panel._draft = panel._draftFromRoutine(routine);
  assert.equal(panel._draft.actions[0].kind, "briefing");
  const raw = panel._draftToRaw(panel._draft);
  assert.deepEqual(raw.actions[0], routine.actions[0]);
  assert.deepEqual(raw.conditions[0], routine.conditions[0]);
  panel._view = { name: "editor", room: "kitchen" };
  openSteps(panel);
  panel._render();
  const html = panel.shadowRoot.innerHTML;
  assert.match(html, /Say the day ahead/);
  assert.match(html, /Only when none of this is coming/);
  panel._draft.actions[0].bSpeaker = "";
  assert.throws(() => panel._draftToRaw(panel._draft), /choose the speaker/);
});

test("a group shares as one code and imports as new routines", () => {
  const panel = makePanel();
  panel._hass = { user: { is_admin: true } };
  const base = panel._data.routines[0];
  panel._data.routines = [{ ...base, id: "a", name: "Lights off", group: "Bedtime" }, { ...base, id: "b", name: "Lock up", group: "Bedtime" }];
  panel._data.statuses = { a: { state: "idle" }, b: { state: "idle" } };
  panel._groupShare = "Bedtime";
  const html = panel._allResults();
  const code = html.match(/data-share-code readonly rows="4">([^<]+)</)[1];
  assert.ok(code.startsWith("DR2:"));
  const { routines, missing } = panel._importGroup(code);
  assert.match(routines[0].id, /^lights_off/);
  assert.match(routines[1].id, /^lock_up/);
  assert.equal(routines[0].group, "Bedtime");
  assert.equal(routines[0].room, "kitchen");
  assert.deepEqual(missing, []);
  assert.throws(() => panel._importGroup("DR2:nope"), /isn't a Daily Routine code/);
});

test("reliability, holds and Undo the last hour show", () => {
  const panel = makePanel();
  const routine = panel._data.routines[0];
  panel._data.statuses[routine.id] = { state: "idle", reliability: { score: 82, worst: [{ name: "Hall lamp", failed: 2, retried: 1 }] } };
  assert.match(panel._routineCard(routine), /answered 82% of the time · Hall lamp failed 2×/);
  panel._data.settings.holds = [{ calendar: "calendar.family", words: ["babysitter"] }];
  panel._data.all_entities = [{ entity_id: "calendar.family", name: "Family" }];
  panel._data.hold = "Babysitter";
  panel._view = { name: "settings" };
  panel._render();
  let html = panel.shadowRoot.innerHTML;
  assert.match(html, /Calendar holds/);
  assert.match(html, /On now: Babysitter/);
  assert.match(html, /value="babysitter"/);
  panel._today = { planned: [], done: [], watching: [], mode: "home", paused: false };
  panel._data.can_undo_recent = true;
  panel._view = { name: "today" };
  panel._render();
  html = panel.shadowRoot.innerHTML;
  assert.match(html, /data-act="undo-recent"/);
  assert.match(html, /Calendar hold: Babysitter/);
});

test("check doors and windows step round-trips and the library fills the openings", () => {
  const panel = makePanel();
  panel._data.binary_sensors = [...(panel._data.binary_sensors || []), { entity_id: "binary_sensor.back_door", name: "Back door", device_class: "door" }];
  panel._data.all_entities = [...(panel._data.all_entities || []), { entity_id: "lock.front_door", name: "Front door" }];
  const template = { title: "Did I leave something open?", draft: { trigger: "manual", actions: [{ kind: "check_open", entities: [], useOpenings: true, cStop: false }] } };
  const draft = panel._applyTemplate(template, null);
  assert.deepEqual(draft.actions[0].entities, ["binary_sensor.back_door", "lock.front_door"]);
  assert.equal(draft.actions[0].useOpenings, undefined);
  draft.name = "Leaving";
  const payload = panel._draftToRaw(draft);
  assert.deepEqual(payload.actions[0], { check_open: { entity_id: ["binary_sensor.back_door", "lock.front_door"], stop: false } });
  const back = panel._draftFromRoutine({ ...panel._data.routines[0], actions: payload.actions });
  assert.equal(back.actions[0].kind, "check_open");
  assert.match(panel._stepSummary(back.actions[0]), /Tell me if any of 2 doors and windows is open/);
  assert.throws(() => panel._draftToRaw({ ...draft, actions: [{ ...draft.actions[0], entities: [] }] }), /doors and windows/);
});

test("laundry library step watches the trigger's power sensor", () => {
  const panel = makePanel();
  const step = panel._libraryStep({ kind: "wait_until", wOnValue: true, wEntity: "" }, { value_entity: "sensor.washer_power" });
  assert.equal(step.wEntity, "sensor.washer_power");
  assert.equal(step.wOnValue, undefined);
});

test("quiet hours, guest Wi-Fi and the loud_ok option", () => {
  const panel = makePanel();
  panel._data.settings.quiet_hours = { enabled: true, from: "22:00", until: "07:00" };
  panel._data.settings.guest_wifi = { ssid: "Visitors", password: "pw" };
  panel._view = { name: "settings" };
  panel._render();
  let html = panel.shadowRoot.innerHTML;
  assert.match(html, /Quiet hours/);
  assert.match(html, /data-quiet="from" value="22:00"/);
  assert.match(html, /data-wifi="ssid" maxlength="32" value="Visitors"/);
  const draft = panel._draftFromRoutine({ ...panel._data.routines[0], loud_ok: true });
  assert.equal(draft.loud_ok, true);
  draft.name = "Alarm";
  assert.equal(panel._draftToRaw(draft).loud_ok, true);
  panel._today = { planned: [], done: [], watching: [], mode: "guests", paused: false };
  panel._data.guest_wifi = { ssid: "Visitors", password: "p<w" };
  panel._view = { name: "today" };
  panel._render();
  html = panel.shadowRoot.innerHTML;
  assert.match(html, /Guest Wi-Fi/);
  assert.match(html, /p&lt;w/);
  assert.match(html, /data-act="tomorrow"/);
});

test("tomorrow's dry run and a routine's history chart", () => {
  const panel = makePanel();
  panel._tomorrow = { date: "2026-11-16", paused: false, watching: [{ routine_id: "x", name: "Porch light" }],
    runs: [{ at: "2026-11-16T07:00:00-06:00", routine_id: "r", name: "Morning", room: null, steps: ["Turn on the lights", "Wait 1 minute"], paused: true, skip_modes: ["guests"] }] };
  const html = panel._tomorrowHtml(panel._tomorrow);
  assert.match(html, /Morning/);
  assert.match(html, /<li>Turn on the lights<\/li><li>Wait 1 minute<\/li>/);
  assert.match(html, /Paused/);
  assert.match(html, /Also when something happens: Porch light/);
  const routine = panel._data.routines[0];
  panel._history = { [routine.id]: { days: [{ day: "2026-11-14", ran: 2, skipped: 1, failed: 0 }, { day: "2026-11-15", ran: 0, skipped: 0, failed: 1 }] } };
  const card = panel._routineCard(routine);
  assert.match(card, /class="history-chart"/);
  assert.match(card, /Ran 2 .*Skipped 1 .*Failed 1/);
  assert.match(card, /data-act="history"/);
});

test("battery and checklist steps round-trip, and the hot-day fields", () => {
  const panel = makePanel();
  panel._data.weathers = [{ entity_id: "weather.home", name: "Home" }];
  const routine = { ...panel._data.routines[0], trigger: "time", time: "18:00:00", hot_shift: { entity_id: "weather.home", above: 30, minutes: -60 },
    actions: [{ check_battery: { below: 15 } }, { tick_list: { items: ["Stove off", "Door locked"], minutes: 20, on_timeout: "stop" } }] };
  const draft = panel._draftFromRoutine(routine);
  assert.equal(draft.actions[0].kind, "battery");
  assert.equal(draft.actions[1].tItems, "Stove off\nDoor locked");
  assert.equal(draft.hotEntity, "weather.home");
  const raw = panel._draftToRaw(draft);
  assert.deepEqual(raw.actions[0], { check_battery: { below: 15 } });
  assert.deepEqual(raw.actions[1], { tick_list: { items: ["Stove off", "Door locked"], minutes: 20, on_timeout: "stop" } });
  assert.deepEqual(raw.hot_shift, { entity_id: "weather.home", above: 30, minutes: -60 });
  assert.match(panel._stepSummary(draft.actions[1]), /Wait for 2 things to be ticked off/);
  assert.match(panel._hotFields(draft), /When the high is at least/);
  assert.equal(panel._draftToRaw({ ...draft, trigger: "manual" }).hot_shift, null);
  assert.throws(() => panel._draftToRaw({ ...draft, actions: [{ ...draft.actions[1], tItems: "" }] }), /things to tick off/);
});

test("a running checklist, routines that need a look and the month's power show", () => {
  const panel = makePanel();
  const routine = panel._data.routines[0];
  panel._data.statuses[routine.id] = { state: "running", progress: { step: 1, total: 2, label: "Checklist" }, ticklist: { items: ["Stove off", "Door"], ticked: ["Door"] } };
  const card = panel._routineCard(routine);
  assert.match(card, /data-act="tick"[^>]*data-item="Stove off"[^>]*data-done="1"/);
  assert.match(card, /✓ Door/);
  panel._data.attention = [{ routine_id: routine.id, name: "Morning curtains", tries: 6, ran: 1, failed: 2, skipped: 3, score: 17 }];
  panel._view = { name: "all" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /Not working well[\s\S]*Morning curtains[\s\S]*ran 1 of 6 times/);
  panel._data.energy = { rows: [{ routine_id: "a", name: "Goodnight", runs: 3, total_change: -1500, average_change: -500 }], turned_off: 1500 };
  panel._activity = structuredClone(activityFixture);
  panel._view = { name: "activity" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /Power over the last 30 days[\s\S]*turned off 1.5 kW[\s\S]*Goodnight/);
});

test("lived-in lights can copy last week", () => {
  const panel = makePanel();
  panel._data.settings.lived_in = { enabled: true, lights: [], start: "sunset", end: "23:00", modes: ["away"], pattern: "last_week" };
  panel._view = { name: "settings" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /<option value="last_week" selected>Like last week/);
});

test("phones charging and sensor level conditions round-trip", () => {
  const panel = makePanel();
  panel._data.all_entities = [...(panel._data.all_entities || []), { entity_id: "sensor.pixel_battery_state", name: "Pixel battery state" }];
  panel._data.sensors = [{ entity_id: "sensor.rain_today", name: "Rain today", unit: "mm" }];
  const routine = { ...panel._data.routines[0], conditions: [{ kind: "charging", entities: ["sensor.pixel_battery_state"] }, { kind: "value", entity_id: "sensor.rain_today", below: 5, above: null }] };
  const draft = panel._draftFromRoutine(routine);
  const raw = panel._draftToRaw(draft);
  assert.deepEqual(raw.conditions[0], { kind: "charging", entities: ["sensor.pixel_battery_state"] });
  assert.deepEqual(raw.conditions[1], { kind: "value", entity_id: "sensor.rain_today", below: 5, above: null });
  const rows = panel._conditionRows(draft);
  assert.match(rows, /data-ccharge="sensor.pixel_battery_state" checked/);
  assert.match(rows, /Rain today \(mm\)/);
});

test("skip days round-trip and copy to next week", () => {
  const panel = makePanel();
  const draft = panel._draftFromRoutine({ ...panel._data.routines[0], skip_dates: ["2030-12-24", "2030-12-25"] });
  assert.deepEqual(panel._draftToRaw(draft).skip_dates, ["2030-12-24", "2030-12-25"]);
  panel._draft = draft;
  panel._view = { name: "editor" };
  panel._editorOverride = "full";
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /data-act="skip-date-week"/);
});

test("restart report and coming-home summary settings show", () => {
  const panel = makePanel();
  panel._data.settings.missed_report = true;
  panel._view = { name: "settings" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /data-report-setting="missed_report" checked/);
  assert.match(panel.shadowRoot.innerHTML, /data-report-setting="away_summary" >/);
});

// ----- 1.31.1: less work -----

test("a hidden page keeps the newest status and draws it when seen again", () => {
  const panel = makePanel();
  let renders = 0;
  panel._render = () => { renders += 1; };
  const listeners = {};
  globalThis.document = {
    hidden: true,
    addEventListener: (name, fn) => { listeners[name] = fn; },
    removeEventListener: (name) => { delete listeners[name]; },
  };
  const base = { revision: panel._data.revision, paused: false, log_count: 0, mode: "home" };
  panel._onStatus({ ...base, statuses: { a: { state: "running" } } });
  panel._onStatus({ ...base, statuses: { a: { state: "idle" } } });
  assert.equal(renders, 0);
  assert.equal(panel._hiddenStatus.statuses.a.state, "idle");
  globalThis.document.hidden = false;
  listeners.visibilitychange();
  assert.equal(renders, 1);
  assert.equal(panel._data.statuses.a.state, "idle");
  assert.equal(panel._hiddenStatus, null);
  panel.disconnectedCallback();
  assert.equal(listeners.visibilitychange, undefined);
  delete globalThis.document;
});

test("countdowns don't update while the page is hidden", () => {
  const panel = makePanel();
  let asked = 0;
  panel.shadowRoot.querySelectorAll = () => { asked += 1; return []; };
  globalThis.document = { hidden: true };
  panel._updateCountdowns();
  assert.equal(asked, 0);
  globalThis.document.hidden = false;
  panel._updateCountdowns();
  assert.ok(asked > 0);
  delete globalThis.document;
});

test("curtain open and close steps keep their own closed and open limits", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "x", draft: { trigger: "time", actions: [{ service: "cover.close_cover", targetMode: "area" }] } }, "kitchen");
  const step = panel._draft.actions[0];
  step.closedAt = "3";
  step.openAt = "90";
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.actions[0].closed_at, 3);
  assert.equal(raw.actions[0].open_at, 90);
  assert.equal(raw.actions[0].data.closed_at, undefined);

  const back = panel._draftFromRoutine({ ...panel._data.routines[0], actions: raw.actions });
  assert.equal(back.actions[0].closedAt, 3);
  assert.equal(back.actions[0].openAt, 90);

  step.openAt = "2";
  assert.throws(() => panel._draftToRaw(panel._draft), /Step 1: counts as open at must be more/);
});

test("closed and open limits are left out of other steps", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "x", draft: { trigger: "time", actions: [{ service: "light.turn_on", targetMode: "area" }] } }, "kitchen");
  panel._draft.actions[0].closedAt = "3";
  const raw = panel._draftToRaw(panel._draft);
  assert.equal(raw.actions[0].closed_at, undefined);
});

test("the closed and open limits show only on open and close curtain steps", () => {
  const panel = makePanel();
  panel._draft = panel._applyTemplate({ title: "x", draft: { trigger: "time", actions: [{ service: "cover.open_cover", targetMode: "area" }, { service: "cover.stop_cover", targetMode: "area" }] } }, "kitchen");
  assert.match(panel._stepBody(panel._draft.actions[0], 0, "kitchen"), /Counts as closed at/);
  assert.match(panel._stepBody(panel._draft.actions[0], 0, "kitchen"), /Counts as open at/);
  assert.doesNotMatch(panel._stepBody(panel._draft.actions[1], 1, "kitchen"), /Counts as closed at/);
});
