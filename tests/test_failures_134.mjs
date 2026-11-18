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

// 1.34.0: why a run failed in words on the card, in Why not? and in each step;
// the progress bar in its own block; the editor's ticked devices on top.

const overview = {
  paused: false,
  revision: 1,
  areas: [
    {
      id: "bedroom", name: "Master Bedroom", icon: null,
      entities: [
        { entity_id: "cover.mbr_curtains", name: "Master Bedroom Curtains", domain: "cover", device_class: "curtain", state: "closed" },
        { entity_id: "cover.mbr_left_ble", name: "Master Bedroom Left Curtain (BLE)", domain: "cover", device_class: "curtain", state: "closed" },
        { entity_id: "cover.mbr_right_ble", name: "Master Bedroom Right Curtain (BLE)", domain: "cover", device_class: "curtain", state: "closed" },
        { entity_id: "cover.mbr_left_cloud", name: "Master Bedroom Left Curtain (Cloud)", domain: "cover", device_class: "curtain", state: "closed" },
        { entity_id: "cover.mbr_right_cloud", name: "Master Bedroom Right Curtain (Cloud)", domain: "cover", device_class: "curtain", state: "closed" },
      ],
    },
  ],
  devices: [{ id: "dev_mbr", name: "Master Bedroom Curtains", area_id: "bedroom", area_name: "Master Bedroom", integration: "Matter" }],
  people: [],
  settings: { hidden_people: [], notify_services: [] },
  notify_targets: [],
  zones: [{ entity_id: "zone.home", name: "Home" }],
  sensors: [],
  binary_sensors: [
    { entity_id: "binary_sensor.mbr_motion", name: "Bedroom motion", device_class: "motion", state: "off" },
    { entity_id: "binary_sensor.hall_motion", name: "Hall motion", device_class: "motion", state: "off" },
  ],
  routines: [
    {
      id: "mbr", name: "Open Master Bedroom Curtains", enabled: true, room: "bedroom", device_id: "dev_mbr", trigger: "time",
      time: "08:16:00", offset_minutes: 0, days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
      people: [], sensors: [], clear_minutes: 0, conditions: [], conditions_match: "all", repeat: false,
      catch_up_minutes: 0, retries: 0, skip_noop: true, notify_on_run: false, notify_on_skip: false, notify_on_error: true,
      actions: [{ service: "cover.open_cover", target: { area_id: ["bedroom"] }, data: {} }],
    },
  ],
  statuses: { mbr: { state: "idle", waiting: false, conditions_ok: true, next_run: null } },
};

const RICH = "Master Bedroom Right Curtain (BLE) (SwitchBot) couldn't be reached: Failed to connect (tried Matter first)";

function makePanel() {
  const panel = new (defined.get("daily-routine-panel"))();
  panel.attachShadow();
  panel._data = structuredClone(overview);
  panel._me = { allowed: true, is_admin: true };
  panel._editorOverride = "full";
  return panel;
}

function card(panel) {
  panel._view = { name: "room", room: "bedroom" };
  panel._render();
  return panel.shadowRoot.innerHTML;
}

test("the card's last: line says why, not error:cover.open_cover", () => {
  const panel = makePanel();
  panel._data.statuses.mbr = { ...panel._data.statuses.mbr, state: "error", last_result: "error:cover.open_cover", last_reason: RICH };
  const html = card(panel);
  assert.match(html, /last: <span class="last-why" title="[^"]*">Master Bedroom Right Curtain \(BLE\) \(SwitchBot\) couldn&#39;t be reached: Failed to connect/);
  assert.doesNotMatch(html, /error:cover\.open_cover/);
  assert.doesNotMatch(html, /last: cover\.open_cover/);
});

test("without a reason from the run, the code is put in words, never the raw service", () => {
  const panel = makePanel();
  panel._data.statuses.mbr = { ...panel._data.statuses.mbr, state: "error", last_result: "error:cover.open_cover" };
  const html = card(panel);
  assert.match(html, /last: <span class="last-why"[^>]*>A step failed \(open cover\)</);
  assert.doesNotMatch(html, /Action failed: cover\.open_cover/);
});

test("each step of a failed run lists every way it tried", () => {
  const panel = makePanel();
  panel._data.statuses.mbr = {
    ...panel._data.statuses.mbr,
    history: [{
      at: new Date().toISOString(), outcome: "failed", detail: RICH,
      steps: [{ step: 1, label: "Open Master Bedroom", result: "failed", seconds: 41, note: RICH,
        why: ["Tried first: Master Bedroom Curtains (Matter) stayed closed after 10 s (wanted open)", RICH] }],
    }],
  };
  const html = card(panel);
  assert.match(html, /see each step/);
  assert.match(html, /<ul class="why-lines small"><li>Tried first: Master Bedroom Curtains \(Matter\) stayed closed after 10 s \(wanted open\)<\/li><\/ul>/);
});

test("Why not? shows setup notes, the last failure and the order devices are tried", () => {
  const panel = makePanel();
  panel._why = {
    mbr: {
      reasons: [], next_run: null, last_skip: null, unavailable: [],
      notes: ["Only if, condition 1: no sensors are picked, so it is ignored. Pick the sensors, or remove it."],
      last_failure: { at: new Date().toISOString(), reason: RICH, steps: [{ step: 1, label: "Open Master Bedroom", result: "failed", note: RICH, why: ["Tried first: Matter stayed closed", RICH] }] },
      plan: [{ step: 1, label: "Open Master Bedroom", ways: ["Master Bedroom Curtains (Matter) → then Left and Right (SwitchBot) → then Left and Right (SwitchBot Cloud)"] }],
    },
  };
  const html = card(panel);
  assert.match(html, /Check its setup:/);
  assert.match(html, /no sensors are picked, so it is ignored/);
  assert.match(html, /Last failed [^<]*?: Master Bedroom Right Curtain/);
  assert.match(html, /Tried first: Matter stayed closed/);
  assert.match(html, /The order devices are tried in/);
  assert.match(html, /Step 1: Master Bedroom Curtains \(Matter\) → then/);
});

test("Not working well says the last failure", () => {
  const panel = makePanel();
  panel._data.attention = [{ routine_id: "mbr", name: "Open Master Bedroom Curtains", tries: 5, ran: 1, failed: 4, skipped: 0, score: 20, last_problem: RICH }];
  panel._view = { name: "all" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /Not working well[\s\S]*Last failure: Master Bedroom Right Curtain \(BLE\)/);
});

// ----- the progress bar (the blue shape on the card) -----

test("a one-step run with no end time shows a real bar, not a 3% dot, in its own block", () => {
  const panel = makePanel();
  panel._data.statuses.mbr = {
    ...panel._data.statuses.mbr, state: "running", last_result: "error:cover.open_cover", last_reason: RICH,
    progress: { step: 1, total: 1, label: "Open Master Bedroom", started: new Date().toISOString(), step_started: new Date().toISOString(), until: null },
  };
  const html = card(panel);
  assert.match(html, /<div class="run-progress"><div class="progress" role="progressbar"[^>]*aria-label="Step 1 of 1"><div class="bar working" style="width:50%"><\/div><\/div>\s*<div class="small progress-text">Step 1 of 1 · Open Master Bedroom<\/div><\/div>/);
  assert.doesNotMatch(html, /width:3%/);
  // The text column takes the card's width, so the bar is as wide as the card.
  assert.match(html, /\.routine-head > div:first-child \{ flex: 1; min-width: 0; \}/);
  // Its track shows on any theme, and the step line is a block under the bar.
  assert.match(html, /\.progress \{ position: relative;[^}]*color-mix\(in srgb, var\(--primary-color/);
  assert.match(html, /\.progress-text \{ display: block;/);
});

test("a timed step still fills by time", () => {
  const panel = makePanel();
  const from = new Date(Date.now() - 60000).toISOString();
  const until = new Date(Date.now() + 60000).toISOString();
  panel._data.statuses.mbr = { ...panel._data.statuses.mbr, state: "running", progress: { step: 1, total: 2, label: "Wait", step_started: from, until } };
  const html = card(panel);
  assert.match(html, /<div class="bar" data-from="[^"]+" data-to="[^"]+" data-step="1" data-total="2" style="width:25%">/);
});

// ----- the editor -----

test("an Only if sensor condition with nothing ticked is flagged and can't be saved", () => {
  const panel = makePanel();
  panel._draft = panel._draftFromRoutine(overview.routines[0]);
  panel._draft.conditions.push({ kind: "sensors", mode: "quiet", minutes: 5, sensors: [] });
  panel._view = { name: "editor", room: "bedroom" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /class="small warn-text empty-cond" role="alert">⚠️ No sensors picked/);
  assert.throws(() => panel._draftToRaw(panel._draft), /Only if, condition 1 \(Sensors\): no sensors are picked/);
  panel._draft.conditions[0].sensors.push("binary_sensor.mbr_motion");
  panel._render();
  assert.doesNotMatch(panel.shadowRoot.innerHTML, /empty-cond/);
  assert.deepEqual(panel._draftToRaw(panel._draft).conditions[0].sensors, ["binary_sensor.mbr_motion"]);
});

test("ticking the first sensor redraws, so the warning goes away", () => {
  const panel = makePanel();
  panel._draft = panel._draftFromRoutine(overview.routines[0]);
  panel._draft.conditions.push({ kind: "sensors", mode: "quiet", minutes: 5, sensors: [] });
  panel._view = { name: "editor", room: "bedroom" };
  panel._render();
  let drawn = 0;
  const render = panel._render.bind(panel);
  panel._render = () => { drawn += 1; render(); };
  panel._onField({ target: { dataset: { ci: "0", csensor: "binary_sensor.mbr_motion" }, type: "checkbox", checked: true, value: "on" } });
  assert.equal(drawn, 1);
  panel._onField({ target: { dataset: { ci: "0", csensor: "binary_sensor.hall_motion" }, type: "checkbox", checked: true, value: "on" } });
  assert.equal(drawn, 1, "the second one doesn't need a redraw");
});

test("a device step with nothing picked can't be saved (it would fail every run)", () => {
  const panel = makePanel();
  panel._draft = panel._draftFromRoutine(overview.routines[0]);
  Object.assign(panel._draft.actions[0], { targetMode: "entities", entities: [] });
  assert.throws(() => panel._draftToRaw(panel._draft), /Step 1: pick the devices or a room it is for\./);
  panel._draft.actions[0].entities = ["cover.mbr_curtains"];
  assert.deepEqual(panel._draftToRaw(panel._draft).actions[0].target, { entity_id: ["cover.mbr_curtains"] });
});

// A small stand-in for the browser's elements: enough for the Selected section.
class Node {
  constructor(tag, attrs = {}) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = { ...(attrs.dataset || {}) };
    this.type = attrs.type;
    this.checked = Boolean(attrs.checked);
    this.className = attrs.className || "";
    this.textContent = attrs.text || "";
    const node = this;
    this.classList = {
      toggle(name, on) {
        const set = new Set(node.className.split(" ").filter(Boolean));
        if (on) set.add(name); else set.delete(name);
        node.className = [...set].join(" ");
      },
      contains(name) { return node.className.split(" ").includes(name); },
    };
  }
  set innerHTML(html) {
    this._html = html;
    const box = new Node("input", { type: "checkbox", dataset: { selOnly: /data-sel-only="([^"]+)"/.exec(html)?.[1] } });
    box.checked = / checked>/.test(html);
    this.children = [new Node("span", { className: "pill sel-count" }), Object.assign(new Node("label", { className: "sel-only" }), { children: [box] })];
  }
  get innerHTML() { return this._html || ""; }
  _detach(child) { if (child.parent) child.parent.children = child.parent.children.filter((c) => c !== child); child.parent = this; }
  append(...nodes) { for (const n of nodes) { this._detach(n); this.children.push(n); } }
  prepend(...nodes) { for (const n of [...nodes].reverse()) { this._detach(n); this.children.unshift(n); } }
  querySelector(sel) {
    for (const c of this.children) {
      if ((sel === "input" && c.tagName === "INPUT") || (sel.startsWith(".") && c.className.split(" ").includes(sel.slice(1)))) return c;
      const deep = c.querySelector(sel);
      if (deep) return deep;
    }
    return null;
  }
}

function deviceList(ticked) {
  const list = new Node("div", { className: "checks" });
  for (const [id, name] of overview.areas[0].entities.map((e) => [e.entity_id, e.name])) {
    const label = new Node("label", { className: "check", text: name });
    label.append(new Node("input", { type: "checkbox", checked: ticked.includes(id), dataset: { sentity: "0", val: id } }));
    list.append(label);
  }
  return list;
}

test("the editor's device lists put what's ticked on top, with a count and Show only selected", () => {
  globalThis.document = { createElement: (tag) => new Node(tag) };
  const panel = makePanel();
  panel._view = { name: "editor", room: "bedroom" };
  const list = deviceList(["cover.mbr_right_cloud", "cover.mbr_left_ble"]);
  panel.shadowRoot.querySelectorAll = (sel) => (sel === ".checks" ? [list] : []);
  panel._addSelected();
  const names = list.children.map((c) => c.className.startsWith("sel-") ? `[${c.className}]` : c.textContent);
  assert.deepEqual(names, [
    "[sel-bar]", "[sel-head sel-picked]",
    "Master Bedroom Left Curtain (BLE)", "Master Bedroom Right Curtain (Cloud)",
    "[sel-head sel-rest]",
    "Master Bedroom Curtains", "Master Bedroom Right Curtain (BLE)", "Master Bedroom Left Curtain (Cloud)",
  ]);
  assert.equal(list.children[0].querySelector(".sel-count").textContent, "2 selected");
  assert.equal(list.classList.contains("only-selected"), false);

  // Tick one more: it moves up; untick one: it goes back to its place.
  const matter = list.children.find((c) => c.textContent === "Master Bedroom Curtains").querySelector("input");
  matter.checked = true;
  matter.closest = () => list;
  panel._afterPick({ target: matter });
  assert.equal(list.children[0].querySelector(".sel-count").textContent, "3 selected");
  assert.equal(list.children[2].textContent, "Master Bedroom Curtains");

  // Show only selected.
  const only = list.children[0].querySelector("input");
  only.checked = true;
  only.closest = () => list;
  panel._afterPick({ target: only });
  assert.equal(list.classList.contains("only-selected"), true);
  assert.ok(panel._onlySelected.has("sel:0"));
  delete globalThis.document;
});

test("short lists and lists that aren't devices are left alone", () => {
  globalThis.document = { createElement: (tag) => new Node(tag) };
  const panel = makePanel();
  panel._view = { name: "editor", room: "bedroom" };
  const weather = new Node("div", { className: "checks" });
  for (const kind of ["rainy", "pouring", "snowy", "fog", "windy"]) {
    const label = new Node("label", { className: "check", text: kind });
    label.append(new Node("input", { type: "checkbox", dataset: { oi: "0", cweather: kind } }));
    weather.append(label);
  }
  panel.shadowRoot.querySelectorAll = (sel) => (sel === ".checks" ? [weather] : []);
  panel._addSelected();
  assert.equal(weather.children.length, 5);
  assert.equal(weather.dataset.selKey, undefined);
  delete globalThis.document;
});

test("the editor says a Linked device is tried first", () => {
  const panel = makePanel();
  panel._draft = panel._draftFromRoutine(overview.routines[0]);
  panel._view = { name: "editor", room: "bedroom" };
  panel._render();
  assert.match(panel.shadowRoot.innerHTML, /class="muted small linked-first">Steps for the room this device is in/);
  panel._draft.device_id = null;
  panel._render();
  assert.doesNotMatch(panel.shadowRoot.innerHTML, /linked-first/);
});

test("Why not? lists 'Linked device first' in the device order", () => {
  const panel = makePanel();
  panel._why = { mbr: { reasons: [], next_run: null, last_skip: null, unavailable: [], notes: [], last_failure: null,
    plan: [{ step: 1, label: "Open Master Bedroom", ways: ["Linked device first: Master Bedroom Curtains (Matter) → then Left and Right (SwitchBot) → then Left and Right (SwitchBot Cloud)"] }] } };
  const html = card(panel);
  assert.match(html, /Step 1: Linked device first: Master Bedroom Curtains \(Matter\) → then/);
});
