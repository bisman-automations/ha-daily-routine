// Smoke tests for the sidebar panel. Run with: node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";

const defined = new Map();
globalThis.HTMLElement = class {};
globalThis.customElements = {
  define(name, cls) {
    defined.set(name, cls);
  },
};

await import("../custom_components/daily_routine/frontend/daily-routine-panel.js");

test("panel element is registered", () => {
  assert.ok(defined.has("daily-routine-panel"));
});

test("entity picker lists only the requested domain, sorted by name", () => {
  const Panel = defined.get("daily-routine-panel");
  const panel = new Panel();
  panel._hass = {
    states: {
      "person.sam": { entity_id: "person.sam", attributes: { friendly_name: "Sam" } },
      "light.hall": { entity_id: "light.hall", attributes: { friendly_name: "Hall" } },
      "person.alex": { entity_id: "person.alex", attributes: { friendly_name: "Alex" } },
    },
  };
  const people = panel._entitiesByDomain("person");
  assert.deepEqual(
    people.map((p) => p.id),
    ["person.alex", "person.sam"],
  );
});
