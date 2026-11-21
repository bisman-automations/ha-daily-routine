// 1.33.0: live updates send only the routines whose run state changed, and
// the overview leaves out a device list the page already has. The panel and
// the card merge them into what they have.
// Run with: node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";

const defined = new Map();

class FakeElement {
  classList = { toggle() {}, add() {}, remove() {}, contains() { return false; } };
  isConnected = true;

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
globalThis.customElements = { define(name, cls) { defined.set(name, cls); }, get(name) { return defined.get(name); } };
globalThis.window = globalThis.window || {};

await import("../custom_components/daily_routine/frontend/daily-routine-panel.js");
await import("../custom_components/daily_routine/frontend/daily-routine-card.js");

const Panel = defined.get("daily-routine-panel");
const Card = defined.get("daily-routine-card");
const { mergeStatus, keepCatalog } = Panel;

// A fake connection: remembers what was asked and hands back the callback.
function fakeHass(onCall = async () => ({})) {
  const subscribed = [];
  return {
    subscribed,
    callWS: onCall,
    connection: {
      subscribeMessage(callback, message) {
        subscribed.push({ callback, message });
        return Promise.resolve(() => {});
      },
    },
  };
}

test("mergeStatus: a whole status replaces, a delta merges and removes", () => {
  const whole = { revision: 1, paused: false, statuses: { a: { state: "idle" }, b: { state: "idle" } } };
  assert.deepEqual(mergeStatus(null, whole), whole);
  assert.deepEqual(mergeStatus({ statuses: { z: {} } }, whole), whole, "no delta: replaced");
  const merged = mergeStatus(whole, {
    revision: 1, paused: true, delta: true,
    statuses: { a: { state: "running" }, c: { state: "idle" } }, removed: ["b"],
  });
  assert.deepEqual(merged, {
    revision: 1, paused: true,
    statuses: { a: { state: "running" }, c: { state: "idle" } },
  });
  assert.equal(whole.statuses.a.state, "idle", "what we had isn't changed");
  // A delta with nothing to merge into is taken as it is.
  assert.deepEqual(mergeStatus(null, { delta: true, statuses: { a: {} }, removed: [] }), { statuses: { a: {} } });
});

test("keepCatalog keeps the page's own device list when Home Assistant left it out", () => {
  const before = { devices: [{ id: "d" }], integrations: { "light.x": "Hue" }, routines: [] };
  const kept = keepCatalog({ catalog: "v1", catalog_kept: true, routines: [1] }, before);
  assert.deepEqual(kept, { catalog: "v1", routines: [1], devices: [{ id: "d" }], integrations: { "light.x": "Hue" } });
  const fresh = { catalog: "v2", devices: [], integrations: {} };
  assert.equal(keepCatalog(fresh, before), fresh, "sent again: used as sent");
});

test("the panel asks for changes only and merges them", () => {
  const panel = new Panel();
  panel.attachShadow();
  panel._data = { revision: 1, paused: false, statuses: { a: { state: "idle" }, b: { state: "idle" } }, routines: [] };
  panel._view = { name: "editor" };
  const hass = fakeHass();
  panel._hass = hass;
  panel._subscribe();
  assert.equal(hass.subscribed.length, 1);
  assert.deepEqual(hass.subscribed[0].message, { type: "daily_routine/subscribe", delta: true });
  const push = hass.subscribed[0].callback;
  push({ revision: 1, paused: false, log_count: 1, statuses: { a: { state: "idle" }, b: { state: "idle" } } });
  push({ revision: 1, paused: false, log_count: 1, delta: true, statuses: { a: { state: "running", progress: null } }, removed: [] });
  assert.equal(panel._data.statuses.a.state, "running");
  assert.equal(panel._data.statuses.b.state, "idle", "a routine not in the change keeps its status");
  // Marking a card Running on the page doesn't leak into what later changes merge into.
  panel._data.statuses.b = { state: "running" };
  push({ revision: 1, paused: false, log_count: 1, delta: true, statuses: { a: { state: "idle" } }, removed: [] });
  assert.equal(panel._data.statuses.b.state, "idle");
  push({ revision: 1, paused: false, log_count: 1, delta: true, statuses: {}, removed: ["b"] });
  assert.deepEqual(Object.keys(panel._data.statuses), ["a"]);
  clearInterval(panel._ticker);
});

test("the panel sends its catalog version and keeps its device list", async () => {
  const panel = new Panel();
  panel.attachShadow();
  panel._me = { allowed: true, is_admin: true };
  panel._tags = [];
  panel._render = () => {};
  const asked = [];
  const answers = [
    { revision: 1, catalog: "v1", devices: [{ id: "d1" }], integrations: { "light.x": "Hue" }, statuses: {}, routines: [] },
    { revision: 2, catalog: "v1", catalog_kept: true, statuses: {}, routines: [] },
    { revision: 3, catalog: "v2", devices: [{ id: "d2" }], integrations: {}, statuses: {}, routines: [] },
  ];
  panel._hass = fakeHass(async (message) => { asked.push(message); return answers.shift(); });
  await panel._load(true);
  assert.deepEqual(asked[0], { type: "daily_routine/overview" }, "nothing to keep yet");
  await panel._load(true);
  assert.deepEqual(asked[1], { type: "daily_routine/overview", catalog: "v1" });
  assert.deepEqual(panel._data.devices, [{ id: "d1" }]);
  assert.deepEqual(panel._data.integrations, { "light.x": "Hue" });
  assert.equal(panel._data.revision, 2);
  assert.equal(panel._data.catalog_kept, undefined);
  await panel._load(true);
  assert.deepEqual(panel._data.devices, [{ id: "d2" }], "a new device list replaces the old one");
});

test("the card asks for changes only and merges them", () => {
  const card = new Card();
  card.attachShadow();
  card._render = () => {};
  let todayLoads = 0;
  card._loadToday = () => { todayLoads += 1; card._today = {}; };
  const hass = fakeHass();
  card._hass = hass;
  card._subscribe();
  assert.deepEqual(hass.subscribed[0].message, { type: "daily_routine/subscribe", delta: true });
  const push = hass.subscribed[0].callback;
  push({ revision: 1, log_count: 1, statuses: { a: { state: "idle" }, b: { state: "idle" } } });
  push({ revision: 1, log_count: 1, delta: true, statuses: { a: { state: "running" } }, removed: [] });
  assert.deepEqual(card._status.statuses, { a: { state: "running" }, b: { state: "idle" } });
  assert.equal(todayLoads, 1);
});

test("a hidden card keeps merging and draws once when seen again (1.34.2)", () => {
  const card = new Card();
  card.attachShadow();
  let renders = 0;
  card._render = () => { renders += 1; };
  let todayLoads = 0;
  card._loadToday = () => { todayLoads += 1; card._today = {}; };
  const listeners = {};
  globalThis.document = {
    hidden: false,
    addEventListener: (name, fn) => { listeners[name] = fn; },
    removeEventListener: (name) => { delete listeners[name]; },
  };
  const hass = fakeHass();
  card._hass = hass;
  card._subscribe();
  const push = hass.subscribed[0].callback;
  push({ revision: 1, log_count: 1, statuses: { a: { state: "idle" }, b: { state: "idle" } } });
  assert.equal(todayLoads, 1);

  globalThis.document.hidden = true;
  push({ revision: 1, log_count: 1, delta: true, statuses: { a: { state: "running" } }, removed: [] });
  push({ revision: 1, log_count: 2, delta: true, statuses: { b: { state: "running" } }, removed: [] });
  assert.equal(renders, 0, "nothing drawn while hidden");
  assert.equal(todayLoads, 1, "today's plan isn't asked for while hidden");

  let asked = 0;
  card.shadowRoot.querySelectorAll = () => { asked += 1; return []; };
  card._tick();
  assert.equal(asked, 0, "progress bars don't move while hidden");

  globalThis.document.hidden = false;
  listeners.visibilitychange();
  assert.deepEqual(card._status.statuses, { a: { state: "running" }, b: { state: "running" } });
  assert.equal(todayLoads, 2, "the plan is loaded once, with the newest log");
  listeners.visibilitychange();
  assert.equal(todayLoads, 2);
  card._tick();
  assert.ok(asked > 0);

  card.disconnectedCallback();
  assert.equal(listeners.visibilitychange, undefined);
  delete globalThis.document;
});
