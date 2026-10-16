const DOMAIN = "daily_routine";
const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

const EMPTY_ROUTINE = () => ({
  id: "",
  name: "",
  enabled: true,
  time: "07:00:00",
  days: [...WEEKDAYS],
  require_everyone_away: false,
  require_sensors_clear: false,
  clear_minutes: 0,
  people: [],
  sensors: [],
  notify_on_run: false,
  notify_on_skip: false,
  actions: [],
});

class DailyRoutinePanel extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) {
      this._loaded = true;
      this._load();
    } else if (!this._editing) {
      this._refresh().catch(() => {});
    }
  }

  connectedCallback() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  async _load() {
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._renderLoading();
    try {
      await this._refresh();
    } catch (error) {
      this._renderError(error);
    }
  }

  async _refresh() {
    this._data = await this._hass.callWS({ type: `${DOMAIN}/panel/routines` });
    this._editing = null;
    this._pickerFilter = {};
    this._render();
  }

  _entitiesByDomain(domain) {
    if (!this._hass) return [];
    return Object.values(this._hass.states)
      .filter((s) => s.entity_id.startsWith(domain + "."))
      .map((s) => ({
        id: s.entity_id,
        name: s.attributes.friendly_name || s.entity_id,
      }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }

  _entitiesForTargets() {
    if (!this._hass) return [];
    const prefer = ["cover", "light", "switch", "scene", "script", "input_boolean", "media_player", "climate", "lock", "fan", "button"];
    const all = Object.values(this._hass.states).map((s) => ({
      id: s.entity_id,
      name: s.attributes.friendly_name || s.entity_id,
      domain: s.entity_id.split(".")[0],
    }));
    all.sort((a, b) => {
      const ap = prefer.indexOf(a.domain);
      const bp = prefer.indexOf(b.domain);
      const ao = ap === -1 ? 99 : ap;
      const bo = bp === -1 ? 99 : bp;
      if (ao !== bo) return ao - bo;
      return a.name.localeCompare(b.name);
    });
    return all;
  }

  _servicesList() {
    if (!this._hass?.services) return [];
    const prefer = ["cover", "light", "switch", "scene", "script", "homeassistant", "notify", "input_boolean", "media_player", "climate", "lock", "fan", "button"];
    const out = [];
    for (const [domain, services] of Object.entries(this._hass.services)) {
      for (const service of Object.keys(services)) {
        out.push(`${domain}.${service}`);
      }
    }
    out.sort((a, b) => {
      const ad = a.split(".")[0];
      const bd = b.split(".")[0];
      const ao = prefer.indexOf(ad) === -1 ? 99 : prefer.indexOf(ad);
      const bo = prefer.indexOf(bd) === -1 ? 99 : prefer.indexOf(bd);
      if (ao !== bo) return ao - bo;
      return a.localeCompare(b);
    });
    return out;
  }

  _styles() {
    return `<style>
      :host{--border:var(--divider-color,#dadce0);--primary:var(--primary-color,#03a9f4);display:block;color:var(--primary-text-color);background:var(--primary-background-color);font-family:var(--paper-font-body1_-_font-family,Roboto,Noto,sans-serif);min-height:100vh}
      *{box-sizing:border-box}
      main{padding:24px 20px 48px;max-width:1100px;margin:0 auto}
      header{display:flex;justify-content:space-between;align-items:center;gap:14px;margin-bottom:22px;flex-wrap:wrap}
      header>div{min-width:0}
      h1{margin:0;font-size:28px;font-weight:500;line-height:1.2}
      header p{margin:6px 0 0;color:var(--secondary-text-color)}
      button{font:inherit;border:0;border-radius:8px;padding:9px 14px;cursor:pointer;background:var(--primary);color:var(--text-primary-color,#fff)}
      button.secondary{background:var(--secondary-background-color,#f1f3f4);color:var(--primary-text-color)}
      button.danger{background:var(--error-color,#db4437);color:#fff}
      button.small{padding:5px 10px;font-size:13px}
      button:disabled{opacity:.5;cursor:not-allowed}
      .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
      .card{background:var(--card-background-color,#fff);border:1px solid var(--border);border-radius:14px;padding:16px;display:grid;gap:12px;box-shadow:var(--ha-card-box-shadow,none)}
      .card-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
      .card-title{margin:0;font-size:18px;font-weight:500}
      .badge{font-size:11px;font-weight:600;padding:3px 9px;border-radius:999px;text-transform:uppercase;letter-spacing:.03em;color:#fff;background:#9e9e9e}
      .badge.running{background:#2196f3}.badge.skipped{background:#ff9800}.badge.error{background:#f44336}.badge.idle{background:#9e9e9e}
      .meta{display:grid;grid-template-columns:96px 1fr;gap:5px 10px;font-size:14px}
      .meta .label{color:var(--secondary-text-color)}
      .ok{color:var(--success-color,#2e7d32)}.bad{color:var(--error-color,#db4437)}
      .card-actions{display:flex;gap:8px;flex-wrap:wrap}
      .card-actions button{flex:1;min-width:88px}
      .empty{text-align:center;padding:48px 22px;color:var(--secondary-text-color)}
      .empty h2{margin:0 0 8px;color:var(--primary-text-color)}
      .notice{padding:12px 14px;margin:0 0 14px;border-radius:8px;background:var(--success-color,#2e7d32);color:#fff}
      .notice.error{background:var(--error-color,#db4437)}
      .backdrop{position:fixed;inset:0;background:#0008;display:grid;place-items:start center;padding:24px 12px;z-index:20;overflow:auto}
      .dialog{width:min(720px,100%);background:var(--card-background-color,#fff);border-radius:16px;padding:24px;box-shadow:0 16px 48px #0004;margin-bottom:40px}
      .dialog-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}
      .dialog-head h2{margin:0;font-size:22px;font-weight:500}
      .icon-button{font-size:22px;padding:2px 8px;background:transparent;color:var(--primary-text-color)}
      form{display:grid;gap:16px}
      .field{display:grid;gap:7px}
      .field-label{font-weight:500;font-size:14px}
      .field small{color:var(--secondary-text-color);line-height:1.4}
      input[type=text],input[type=number],input[type=time],select{
        font:inherit;color:var(--primary-text-color);background:var(--input-fill-color,var(--card-background-color,#fff));
        border:1px solid var(--border);border-radius:8px;padding:10px 12px;width:100%
      }
      .row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
      @media(max-width:600px){.row{grid-template-columns:1fr}}
      .checks{display:flex;flex-wrap:wrap;gap:10px 16px}
      .checks label,.days label{display:flex;align-items:center;gap:7px;font-size:14px}
      .days{display:flex;flex-wrap:wrap;gap:8px 12px}
      .picker{border:1px solid var(--border);border-radius:10px;overflow:hidden;background:var(--input-fill-color,var(--card-background-color,#fff))}
      .picker-search{border:0;border-bottom:1px solid var(--border);border-radius:0;padding:10px 12px}
      .picker-list{max-height:180px;overflow:auto;padding:6px}
      .picker-item{display:flex;align-items:flex-start;gap:10px;padding:8px 8px;border-radius:8px;cursor:pointer;font-size:14px}
      .picker-item:hover{background:var(--secondary-background-color,#f1f3f4)}
      .picker-item input{width:auto;margin-top:3px}
      .picker-item .eid{display:block;color:var(--secondary-text-color);font-size:12px;margin-top:2px}
      .chips{display:flex;flex-wrap:wrap;gap:6px}
      .chip{display:inline-flex;align-items:center;gap:6px;background:var(--secondary-background-color,#e8f0fe);color:var(--primary-text-color);border-radius:999px;padding:4px 10px;font-size:12px}
      .chip button{background:transparent;color:inherit;padding:0;font-size:14px;line-height:1}
      .action-block{border:1px solid var(--border);border-radius:12px;padding:14px;display:grid;gap:12px;background:var(--secondary-background-color,#fafafa)}
      .action-head{display:flex;justify-content:space-between;align-items:center}
      .action-head strong{font-size:14px}
      footer{display:flex;justify-content:flex-end;gap:8px;margin-top:8px;padding-top:16px;border-top:1px solid var(--border)}
    </style>`;
  }

  _escape(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
  }

  _formatDate(iso) {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    } catch { return iso; }
  }

  _renderLoading() {
    this.shadowRoot.innerHTML = `${this._styles()}<main><p>Loading Daily Routine…</p></main>`;
  }

  _renderError(error) {
    this.shadowRoot.innerHTML = `${this._styles()}<main><section class="notice error">${this._escape(error?.message || "Unable to load the dashboard.")}</section></main>`;
  }

  _render() {
    const routines = this._data?.routines || [];
    const statuses = this._data?.statuses || {};
    this.shadowRoot.innerHTML = `${this._styles()}
      <main>
        <header>
          <div>
            <h1>Daily Routine</h1>
            <p>${routines.length} routine${routines.length === 1 ? "" : "s"} · add, edit, and run from this panel</p>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="secondary" data-action="refresh">Refresh</button>
            <button data-action="add">How to add</button>
          </div>
        </header>
        ${this._notice ? `<div class="notice ${this._noticeError ? "error" : ""}">${this._escape(this._notice)}</div>` : ""}
        ${
          routines.length
            ? `<div class="grid">${routines.map((r) => this._card(r, statuses[r.id] || {})).join("")}</div>`
            : `<section class="empty"><h2>No routines yet</h2><p>Click <strong>How to add</strong> to create one with the visual editor.</p></section>`
        }
        ${this._editing ? this._editor() : ""}
      </main>`;

    this.shadowRoot.querySelector('[data-action="refresh"]')?.addEventListener("click", () => {
      this._notice = null;
      this._refresh().catch((e) => this._showNotice(e?.message || "Refresh failed", true));
    });
    this.shadowRoot.querySelector('[data-action="add"]')?.addEventListener("click", () => {
      this._showNotice("Add a routine via Settings → Devices & services → Daily Routine → Add service. Configure each routine with its gear icon.");
      this._render();
    });
    this.shadowRoot.querySelectorAll("[data-run]").forEach((btn) => {
      btn.addEventListener("click", () => this._run(btn.dataset.run));
    });
    this.shadowRoot.querySelectorAll("[data-edit]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._showNotice("Edit via Settings → Devices & services → Daily Routine → Configure on that routine.");
        this._render();
      });
    });
    this.shadowRoot.querySelectorAll("[data-delete]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._showNotice("Delete via Settings → Devices & services → Daily Routine → ⋮ on that entry.");
        this._render();
      });
    });
    if (this._editing) this._bindEditor();
  }

  _actionsToEdit(actions) {
    if (!actions.length) return [{ service: "", target_entities: [], data_text: "", isDelay: false }];
    return actions.map((a) => {
      if (a.delay !== undefined) {
        return { service: "", target_entities: [], data_text: String(a.delay), isDelay: true };
      }
      const ents = [];
      const t = a.target || {};
      if (Array.isArray(t.entity_id)) ents.push(...t.entity_id);
      else if (typeof t.entity_id === "string") ents.push(t.entity_id);
      let data_text = "";
      if (a.data && Object.keys(a.data).length) {
        data_text = Object.entries(a.data).map(([k, v]) => `${k}=${v}`).join(", ");
      }
      return { service: a.service || "", target_entities: ents, data_text, isDelay: false };
    });
  }

  _card(r, st) {
    const state = st.state || "idle";
    return `<article class="card">
      <div class="card-head">
        <h2 class="card-title">${this._escape(r.name || r.id)}</h2>
        <span class="badge ${this._escape(state)}">${this._escape(state)}</span>
      </div>
      <div class="meta">
        <span class="label">Enabled</span><span>${r.enabled ? "Yes" : "No"}</span>
        <span class="label">Schedule</span><span>${this._escape(r.time)} · ${(r.days || []).join(", ")}</span>
        <span class="label">Last run</span><span>${this._escape(this._formatDate(st.last_run))}</span>
        <span class="label">Next run</span><span>${this._escape(this._formatDate(st.next_run))}</span>
        <span class="label">Result</span><span>${this._escape(st.last_result || "—")}</span>
        <span class="label">Conditions</span><span class="${st.conditions_ok ? "ok" : "bad"}">${st.conditions_ok ? "OK" : "Not met"}</span>
      </div>
      <div class="card-actions">
        <button data-run="${this._escape(r.id)}">Run now</button>
        <button class="secondary" data-edit="${this._escape(r.id)}">Edit</button>
        <button class="danger" data-delete="${this._escape(r.id)}">Delete</button>
      </div>
    </article>`;
  }

  _entityPicker(key, entities, selected, filterKey) {
    const filter = (this._pickerFilter[filterKey] || "").toLowerCase();
    const selectedSet = new Set(selected || []);
    const filtered = entities.filter(
      (e) =>
        !filter ||
        e.name.toLowerCase().includes(filter) ||
        e.id.toLowerCase().includes(filter)
    );
    const chips = (selected || [])
      .map((id) => {
        const ent = entities.find((e) => e.id === id);
        const label = ent ? ent.name : id;
        return `<span class="chip">${this._escape(label)} <button type="button" data-chip-remove="${this._escape(filterKey)}" data-chip-id="${this._escape(id)}" title="Remove">×</button></span>`;
      })
      .join("");
    const items = filtered
      .slice(0, 80)
      .map(
        (e) => `<label class="picker-item">
          <input type="checkbox" data-pick="${this._escape(filterKey)}" value="${this._escape(e.id)}" ${selectedSet.has(e.id) ? "checked" : ""}/>
          <span>${this._escape(e.name)}<span class="eid">${this._escape(e.id)}</span></span>
        </label>`
      )
      .join("");
    return `
      ${chips ? `<div class="chips">${chips}</div>` : ""}
      <div class="picker">
        <input class="picker-search" type="text" placeholder="Search entities…" data-filter="${this._escape(filterKey)}" value="${this._escape(this._pickerFilter[filterKey] || "")}"/>
        <div class="picker-list">${items || `<div class="picker-item"><span class="eid">No matches</span></div>`}</div>
      </div>`;
  }

  _serviceSelect(idx, selected, isDelay) {
    const services = this._servicesList();
    const filter = (this._pickerFilter[`svc_${idx}`] || "").toLowerCase();
    const filtered = services.filter((s) => !filter || s.toLowerCase().includes(filter));
    const opts = [
      `<option value="">Select a service…</option>`,
      `<option value="__delay__" ${isDelay ? "selected" : ""}>Wait / delay</option>`,
    ];
    for (const s of filtered.slice(0, 200)) {
      opts.push(`<option value="${this._escape(s)}" ${!isDelay && s === selected ? "selected" : ""}>${this._escape(s)}</option>`);
    }
    if (!isDelay && selected && !services.includes(selected)) {
      opts.push(`<option value="${this._escape(selected)}" selected>${this._escape(selected)}</option>`);
    }
    return `
      <input type="text" placeholder="Filter services…" data-filter="svc_${idx}" value="${this._escape(this._pickerFilter[`svc_${idx}`] || "")}"/>
      <select data-action-service="${idx}">${opts.join("")}</select>`;
  }

  _editor() {
    const r = this._editing;
    const people = this._entitiesByDomain("person");
    const sensors = this._entitiesByDomain("binary_sensor");
    const targets = this._entitiesForTargets();
    const daysChecks = WEEKDAYS.map(
      (d) =>
        `<label><input type="checkbox" data-day="${d}" ${(r.days || []).includes(d) ? "checked" : ""}/> ${d}</label>`
    ).join("");

    const actionBlocks = (this._editActions || [])
      .map((a, i) => {
        const isDelay = !!a.isDelay;
        return `<div class="action-block" data-action-idx="${i}">
          <div class="action-head">
            <strong>Step ${i + 1}</strong>
            <button type="button" class="danger small" data-remove-action="${i}">Remove</button>
          </div>
          <div class="field">
            <span class="field-label">Service or delay</span>
            ${this._serviceSelect(i, a.service, isDelay)}
          </div>
          ${
            isDelay
              ? `<div class="field">
                  <span class="field-label">Delay</span>
                  <input type="text" data-action-delay="${i}" value="${this._escape(a.data_text || "5")}" placeholder="5 or 00:00:30"/>
                  <small>Seconds (e.g. 5) or HH:MM:SS</small>
                </div>`
              : `<div class="field">
                  <span class="field-label">Target entities</span>
                  ${this._entityPicker(`targets_${i}`, targets, a.target_entities, `targets_${i}`)}
                  <small>Optional for services that don’t need a target</small>
                </div>
                <div class="field">
                  <span class="field-label">Extra data</span>
                  <input type="text" data-action-data="${i}" value="${this._escape(a.data_text || "")}" placeholder="transition=2"/>
                  <small>Optional key=value pairs, comma-separated</small>
                </div>`
          }
        </div>`;
      })
      .join("");

    return `<div class="backdrop">
      <div class="dialog">
        <div class="dialog-head">
          <h2>${this._editingIsNew ? "Add routine" : "Edit routine"}</h2>
          <button type="button" class="icon-button secondary" data-action="cancel" title="Close">×</button>
        </div>
        <form>
          <div class="field">
            <span class="field-label">ID</span>
            <input data-f="id" type="text" value="${this._escape(r.id)}" ${this._editingIsNew ? "" : "readonly"} placeholder="morning_covers"/>
            <small>Stable id using letters, digits, _ or -</small>
          </div>
          <div class="field">
            <span class="field-label">Name</span>
            <input data-f="name" type="text" value="${this._escape(r.name)}" placeholder="Morning curtains"/>
          </div>
          <div class="row">
            <div class="field">
              <span class="field-label">Time</span>
              <input data-f="time" type="text" value="${this._escape(r.time || "07:00:00")}" placeholder="07:00:00"/>
              <small>HH:MM or HH:MM:SS (24-hour)</small>
            </div>
            <div class="field">
              <span class="field-label">Clear minutes</span>
              <input data-f="clear_minutes" type="number" min="0" max="1440" value="${r.clear_minutes || 0}"/>
            </div>
          </div>
          <div class="field">
            <span class="field-label">Days</span>
            <div class="days">${daysChecks}</div>
          </div>
          <div class="field">
            <span class="field-label">Options</span>
            <div class="checks">
              <label><input type="checkbox" data-f="enabled" ${r.enabled ? "checked" : ""}/> Enabled</label>
              <label><input type="checkbox" data-f="require_everyone_away" ${r.require_everyone_away ? "checked" : ""}/> Require everyone away</label>
              <label><input type="checkbox" data-f="require_sensors_clear" ${r.require_sensors_clear ? "checked" : ""}/> Require sensors clear</label>
              <label><input type="checkbox" data-f="notify_on_run" ${r.notify_on_run ? "checked" : ""}/> Notify on run</label>
              <label><input type="checkbox" data-f="notify_on_skip" ${r.notify_on_skip ? "checked" : ""}/> Notify on skip</label>
            </div>
          </div>
          <div class="field">
            <span class="field-label">People (away check)</span>
            ${this._entityPicker("people", people, r.people, "people")}
          </div>
          <div class="field">
            <span class="field-label">Occupancy / motion sensors</span>
            ${this._entityPicker("sensors", sensors, r.sensors, "sensors")}
            <small>Binary sensors whose <em>on</em> means occupied or motion</small>
          </div>
          <div class="field">
            <span class="field-label">Actions</span>
            ${actionBlocks}
            <div><button type="button" class="secondary small" data-action="add-step">+ Add step</button></div>
          </div>
          <footer>
            <button type="button" class="secondary" data-action="cancel">Cancel</button>
            <button type="button" data-action="save">Save</button>
          </footer>
        </form>
      </div>
    </div>`;
  }

  _bindEditor() {
    const root = this.shadowRoot;
    root.querySelectorAll('[data-action="cancel"]').forEach((el) => {
      el.addEventListener("click", () => {
        this._editing = null;
        this._editActions = null;
        this._render();
      });
    });
    root.querySelector('[data-action="save"]')?.addEventListener("click", () => this._saveEditor());
    root.querySelector('[data-action="add-step"]')?.addEventListener("click", () => {
      this._syncFromDom();
      this._editActions.push({ service: "", target_entities: [], data_text: "", isDelay: false });
      this._render();
    });
    root.querySelectorAll("[data-remove-action]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._syncFromDom();
        this._editActions.splice(parseInt(btn.dataset.removeAction, 10), 1);
        if (!this._editActions.length) {
          this._editActions.push({ service: "", target_entities: [], data_text: "", isDelay: false });
        }
        this._render();
      });
    });
    root.querySelectorAll("[data-filter]").forEach((input) => {
      input.addEventListener("input", () => {
        this._syncFromDom();
        this._pickerFilter[input.dataset.filter] = input.value;
        this._render();
        const again = this.shadowRoot.querySelector(`[data-filter="${input.dataset.filter}"]`);
        if (again) {
          again.focus();
          const len = again.value.length;
          again.setSelectionRange(len, len);
        }
      });
    });
    root.querySelectorAll("[data-action-service]").forEach((sel) => {
      sel.addEventListener("change", () => {
        this._syncFromDom();
        this._render();
      });
    });
    root.querySelectorAll("[data-chip-remove]").forEach((btn) => {
      btn.addEventListener("click", () => {
        this._syncFromDom();
        const key = btn.dataset.chipRemove;
        const id = btn.dataset.chipId;
        if (key === "people") this._editing.people = (this._editing.people || []).filter((x) => x !== id);
        else if (key === "sensors") this._editing.sensors = (this._editing.sensors || []).filter((x) => x !== id);
        else if (key.startsWith("targets_")) {
          const idx = parseInt(key.split("_")[1], 10);
          if (this._editActions[idx]) {
            this._editActions[idx].target_entities = this._editActions[idx].target_entities.filter((x) => x !== id);
          }
        }
        this._render();
      });
    });
    root.querySelectorAll("[data-pick]").forEach((cb) => {
      cb.addEventListener("change", () => {
        this._syncFromDom();
      });
    });
  }

  _checkedPick(key) {
    return [...this.shadowRoot.querySelectorAll(`[data-pick="${key}"]:checked`)].map((el) => el.value);
  }

  _syncFromDom() {
    const root = this.shadowRoot;
    const val = (f) => root.querySelector(`[data-f="${f}"]`);
    if (!this._editing) return;
    this._editing.id = (val("id")?.value || "").trim();
    this._editing.name = (val("name")?.value || "").trim();
    this._editing.time = (val("time")?.value || "07:00:00").trim();
    this._editing.clear_minutes = parseInt(val("clear_minutes")?.value || "0", 10) || 0;
    this._editing.enabled = !!val("enabled")?.checked;
    this._editing.require_everyone_away = !!val("require_everyone_away")?.checked;
    this._editing.require_sensors_clear = !!val("require_sensors_clear")?.checked;
    this._editing.notify_on_run = !!val("notify_on_run")?.checked;
    this._editing.notify_on_skip = !!val("notify_on_skip")?.checked;
    this._editing.days = [...root.querySelectorAll("[data-day]:checked")].map((el) => el.dataset.day);
    this._editing.people = this._checkedPick("people");
    this._editing.sensors = this._checkedPick("sensors");

    const next = [];
    root.querySelectorAll("[data-action-idx]").forEach((block) => {
      const i = block.dataset.actionIdx;
      const service = root.querySelector(`[data-action-service="${i}"]`)?.value || "";
      if (service === "__delay__") {
        next.push({
          service: "",
          target_entities: [],
          data_text: root.querySelector(`[data-action-delay="${i}"]`)?.value || "5",
          isDelay: true,
        });
      } else {
        next.push({
          service,
          target_entities: this._checkedPick(`targets_${i}`),
          data_text: root.querySelector(`[data-action-data="${i}"]`)?.value || "",
          isDelay: false,
        });
      }
    });
    this._editActions = next;
  }

  _parseDataLines(text) {
    if (!text || !text.trim()) return {};
    try {
      const j = JSON.parse(text);
      if (j && typeof j === "object") return j;
    } catch { /* fall through */ }
    const out = {};
    for (const part of text.split(/[,\n]/)) {
      const m = part.trim().match(/^([^=:]+)[=:](.*)$/);
      if (m) {
        let v = m[2].trim();
        if (v === "true") v = true;
        else if (v === "false") v = false;
        else if (/^-?\d+(\.\d+)?$/.test(v)) v = Number(v);
        out[m[1].trim()] = v;
      }
    }
    return out;
  }

  _readEditor() {
    this._syncFromDom();
    const r = this._editing;
    let time = r.time || "07:00:00";
    if (time.length === 5) time += ":00";
    const actions = [];
    for (const a of this._editActions || []) {
      if (a.isDelay) {
        const d = (a.data_text || "5").trim();
        const num = Number(d);
        actions.push({ delay: Number.isFinite(num) && !d.includes(":") ? num : d });
      } else if (a.service) {
        const action = { service: a.service, data: this._parseDataLines(a.data_text) };
        if (a.target_entities?.length) action.target = { entity_id: a.target_entities };
        actions.push(action);
      }
    }
    return {
      id: r.id,
      name: r.name,
      enabled: !!r.enabled,
      time,
      days: r.days || [],
      require_everyone_away: !!r.require_everyone_away,
      require_sensors_clear: !!r.require_sensors_clear,
      clear_minutes: r.clear_minutes || 0,
      people: r.people || [],
      sensors: r.sensors || [],
      notify_on_run: !!r.notify_on_run,
      notify_on_skip: !!r.notify_on_skip,
      actions,
    };
  }

  async _saveEditor() {
    try {
      const routine = this._readEditor();
      if (!routine.id || !routine.name) throw new Error("ID and name are required");
      if (!routine.actions.length) throw new Error("Add at least one action step");
      let routines = [...(this._data?.routines || [])];
      if (this._editingIsNew) {
        if (routines.some((r) => r.id === routine.id)) throw new Error("A routine with that ID already exists");
        routines.push(routine);
      } else {
        routines = routines.map((r) => (r.id === routine.id ? routine : r));
      }
      await this._hass.callWS({ type: `${DOMAIN}/panel/save`, routines });
      this._showNotice("Routine saved");
      await this._refresh();
    } catch (e) {
      this._showNotice(e?.message || String(e), true);
      this._render();
    }
  }

  async _delete(id) {
    if (!confirm(`Delete routine "${id}"?`)) return;
    try {
      const routines = (this._data?.routines || []).filter((r) => r.id !== id);
      await this._hass.callWS({ type: `${DOMAIN}/panel/save`, routines });
      this._showNotice("Routine deleted");
      await this._refresh();
    } catch (e) {
      this._showNotice(e?.message || String(e), true);
      this._render();
    }
  }

  async _run(id) {
    try {
      await this._hass.callWS({ type: `${DOMAIN}/panel/run`, routine_id: id });
      this._showNotice(`Triggered "${id}"`);
      setTimeout(() => this._refresh().catch(() => {}), 800);
      this._render();
    } catch (e) {
      this._showNotice(e?.message || String(e), true);
      this._render();
    }
  }

  _showNotice(msg, isError = false) {
    this._notice = msg;
    this._noticeError = isError;
  }
}

customElements.define("daily-routine-panel", DailyRoutinePanel);
