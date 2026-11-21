// Daily Routine card: today's routines on any dashboard or wall tablet.
// Shows what's running (with its progress), what's asking "Run now?", and
// what's still planned today, with Run now and Skip.
//
//   type: custom:daily-routine-card
//   title: Today            (optional)
//   room: kitchen           (optional: only this room's routines; "house" for whole-house ones)
//   limit: 5                (optional: how many planned runs to show)

const DOMAIN = "daily_routine";
// After the live updates fail (Daily Routine not running, no access), ask
// again at most this often, not on every change in the home (1.34.3).
const RETRY_MS = 30000;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

function percent(progress) {
  if (!progress) return 5;
  const { step, total, step_started: from, until } = progress;
  // A step with no end time is shown half done (1.34.0), not as a 3% dot.
  let part = until ? 0 : 0.5;
  if (from && until) {
    const span = new Date(until) - new Date(from);
    part = span > 0 ? Math.max(0, Math.min(1, (Date.now() - new Date(from)) / span)) : 1;
  }
  return Math.max(3, Math.min(100, Math.round(((step - 1 + part) / Math.max(1, total)) * 100)));
}

// A pushed status merged into the last full one (1.33.0): after the first
// message the subscription sends only the routines whose run state changed
// ("delta", "statuses") and the ones that are gone ("removed"). The same as
// mergeStatus in daily-routine-panel.js (the card loads on its own).
function mergeStatus(prev, next) {
  const { delta, removed, ...rest } = next || {};
  if (!delta || !prev) return rest;
  const statuses = { ...(prev.statuses || {}), ...(rest.statuses || {}) };
  for (const id of removed || []) delete statuses[id];
  return { ...rest, statuses };
}

function pageHidden() {
  return typeof document !== "undefined" && Boolean(document.hidden);
}

class DailyRoutineCard extends HTMLElement {
  static getStubConfig() {
    return { title: "Today" };
  }

  setConfig(config) {
    this._config = { title: "Today", limit: 5, ...(config || {}) };
    this._render();
  }

  getCardSize() {
    return 3;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._unsub && this.isConnected) this._subscribe();
  }

  connectedCallback() {
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
    }
    if (this._hass && !this._unsub) this._subscribe();
    this._ticker = setInterval(() => this._tick(), 1000);
    this._render();
  }

  disconnectedCallback() {
    clearInterval(this._ticker);
    if (this._onVisible) {
      document.removeEventListener("visibilitychange", this._onVisible);
      this._onVisible = null;
    }
    const unsub = this._unsub;
    this._unsub = null;
    if (unsub) unsub.then((stop) => stop && stop()).catch(() => {});
  }

  _subscribe() {
    if (!this._hass?.connection || Date.now() < (this._retryAt || 0)) return;
    // Each subscription starts with a whole status, then sends changes only.
    this._pushed = null;
    this._unsub = this._hass.connection.subscribeMessage((message) => {
      const status = mergeStatus(this._pushed, message);
      this._pushed = status;
      if (pageHidden()) {
        // Nobody's looking (a hidden tab, a tablet with its screen off, the
        // app in the background): keep merging, and draw (and load today's
        // plan) once when the card is seen again (1.34.2).
        this._waitUntilSeen();
        return;
      }
      this._onStatus(status);
    }, { type: `${DOMAIN}/subscribe`, delta: true });
    this._unsub.catch((err) => {
      this._error = err?.code === "unauthorized" ? "You don't have access to Daily Routine." : (err?.message || "Daily Routine isn't running.");
      this._unsub = null;
      this._retryAt = Date.now() + RETRY_MS;
      this._render();
    });
  }

  _waitUntilSeen() {
    this._unseen = true;
    if (this._onVisible || typeof document === "undefined") return;
    this._onVisible = () => {
      if (pageHidden() || !this._unseen || !this._pushed) return;
      this._onStatus(this._pushed);
    };
    document.addEventListener("visibilitychange", this._onVisible);
  }

  _onStatus(status) {
    this._unseen = false;
    const changed = status.log_count !== this._logCount || JSON.stringify(status.statuses) !== this._statusJson || JSON.stringify(status.guest_wifi || null) !== JSON.stringify(this._status?.guest_wifi || null);
    this._status = status;
    this._statusJson = JSON.stringify(status.statuses);
    if (status.log_count !== this._logCount || !this._today) {
      this._logCount = status.log_count;
      this._loadToday();
    } else if (changed) this._render();
  }

  async _loadToday() {
    try {
      this._today = await this._hass.callWS({ type: `${DOMAIN}/today` });
      this._error = null;
    } catch (err) {
      this._error = err?.message || String(err);
    }
    this._render();
  }

  _tick() {
    if (pageHidden()) return;
    for (const bar of this.shadowRoot?.querySelectorAll(".bar[data-p]") || []) {
      const progress = JSON.parse(bar.dataset.p);
      bar.style.width = `${percent(progress)}%`;
    }
  }

  // A card for one group (group: Bedtime) shows only that group's routines.
  _inGroup(group) {
    const wanted = this._config?.group;
    return !wanted || (group || "") === wanted;
  }

  _inRoom(routineRoom) {
    const room = this._config?.room;
    if (!room) return true;
    if (room === "house") return !routineRoom;
    return routineRoom === room;
  }

  async _onClick(event) {
    const el = event.target.closest?.("[data-act]");
    if (!el) return;
    el.disabled = true;
    const id = el.dataset.id;
    try {
      if (el.dataset.act === "run") await this._hass.callWS({ type: `${DOMAIN}/routine/run`, routine_id: id });
      else if (el.dataset.act === "skip") await this._hass.callWS({ type: `${DOMAIN}/routine/skip_next`, routine_id: id, skip: el.dataset.skip === "1" });
      else if (el.dataset.act === "group") {
        const day = new Date();
        const until = el.dataset.pause === "1" ? `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}` : null;
        await this._hass.callWS({ type: `${DOMAIN}/group/pause`, group: this._config.group, until });
      }
      else if (el.dataset.act === "tick") await this._hass.callWS({ type: `${DOMAIN}/routine/tick`, routine_id: id, item: el.dataset.item, done: el.dataset.done === "1" });
      else if (el.dataset.act === "answer") await this._hass.callWS({ type: `${DOMAIN}/routine/answer`, routine_id: id, run: el.dataset.run === "1" });
      this._note = null;
    } catch (err) {
      this._note = err?.message || String(err);
    }
    await this._loadToday();
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const statuses = this._status?.statuses || {};
    const today = this._today;
    let body;
    if (this._error) body = `<p class="muted">${esc(this._error)}</p>`;
    else if (!today) body = `<p class="muted">Loading…</p>`;
    else {
      const rows = [];
      for (const [id, s] of Object.entries(statuses)) {
        if (!this._inRoom(s.room) || !this._inGroup(s.group)) continue;
        if (s.state === "running") {
          rows.push(`<li class="row running"><div class="when">Now</div><div class="what"><strong>${esc(s.name)}</strong>
            <div class="progress"><div class="bar" data-p='${esc(JSON.stringify(s.progress || null))}' style="width:${percent(s.progress)}%"></div></div>
            <div class="small">${s.progress ? `Step ${s.progress.step} of ${s.progress.total} · ${esc(s.progress.label)}` : "Starting…"}</div>
            ${s.ticklist ? `<div class="buttons">${s.ticklist.items.map((item) => {
              const done = (s.ticklist.ticked || []).includes(item);
              return `<button class="${done ? "" : "ghost"}" data-act="tick" data-id="${esc(id)}" data-item="${esc(item)}" data-done="${done ? "" : "1"}">${done ? "✓" : "○"} ${esc(item)}</button>`;
            }).join("")}</div>` : ""}</div></li>`);
        } else if (s.state === "asking") {
          rows.push(`<li class="row asking"><div class="when">Now</div><div class="what"><strong>${esc(s.name)}</strong>
            <div class="small">Run it now?</div>
            <div class="buttons"><button data-act="answer" data-id="${esc(id)}" data-run="1">Run</button><button class="ghost" data-act="answer" data-id="${esc(id)}" data-run="0">Skip</button></div></div></li>`);
        }
      }
      const planned = today.planned.filter((p) => this._inRoom(p.room) && this._inGroup(p.group)).slice(0, Number(this._config.limit) || 5);
      for (const p of planned) {
        rows.push(`<li class="row"><div class="when">${esc(clock(p.at))}</div><div class="what"><strong>${esc(p.name)}</strong>
          ${p.skip_next ? `<span class="pill">Will be skipped</span>` : ""}
          <div class="buttons"><button class="ghost" data-act="run" data-id="${esc(p.routine_id)}">Run now</button>
          <button class="ghost" data-act="skip" data-id="${esc(p.routine_id)}" data-skip="${p.skip_next ? "" : "1"}">${p.skip_next ? "Don't skip" : "Skip"}</button></div></div></li>`);
      }
      body = rows.length ? `<ul>${rows.join("")}</ul>` : `<p class="muted">Nothing else planned today.</p>`;
      if (this._config.buttons) {
        // Room looks and other Run-now routines, as big buttons.
        const manual = Object.entries(statuses).filter(([, st]) => st.trigger === "manual" && st.enabled !== false && this._inRoom(st.room) && this._inGroup(st.group));
        if (manual.length) body = `<div class="presets">${manual.map(([id, st]) => `<button data-act="run" data-id="${esc(id)}">${esc(st.name)}</button>`).join("")}</div>${body}`;
      }
      const wifi = this._status?.guest_wifi;
      if (wifi) {
        body = `<div class="wifi"><div class="small">Guest Wi-Fi</div><div><span class="small">Network</span> <strong>${esc(wifi.ssid)}</strong></div>
          ${wifi.password ? `<div><span class="small">Password</span> <strong class="mono">${esc(wifi.password)}</strong></div>` : ""}</div>${body}`;
      }
      const group = this._config.group;
      if (group && this._hass?.user?.is_admin) {
        const members = Object.values(statuses).filter((s) => (s.group || "") === group);
        const paused = members.length && members.every((s) => s.paused_until && s.paused_until >= new Date().toISOString().slice(0, 10));
        body += `<div class="buttons group">${paused
          ? `<span class="small">Paused.</span><button class="ghost" data-act="group" data-pause="0">Resume ${esc(group)}</button>`
          : `<button class="ghost" data-act="group" data-pause="1">Pause ${esc(group)} for today</button>`}</div>`;
      }
    }
    this.shadowRoot.innerHTML = `<style>
      :host { display: block; }
      ha-card { display: block; padding: 12px 16px; }
      h2 { margin: 0 0 8px; font-size: 18px; font-weight: 500; }
      ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
      .row { display: grid; grid-template-columns: 64px 1fr; gap: 8px; }
      .when { color: var(--secondary-text-color); font-variant-numeric: tabular-nums; padding-top: 2px; }
      .running .when, .asking .when { color: var(--primary-color, #03a9f4); font-weight: 600; }
      .small { font-size: 13px; color: var(--secondary-text-color); }
      .muted { color: var(--secondary-text-color); }
      .buttons { display: flex; gap: 6px; margin-top: 4px; flex-wrap: wrap; }
      button { font: inherit; font-size: 13px; border-radius: 8px; padding: 6px 10px; cursor: pointer; border: 1px solid var(--divider-color, #ddd);
        background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
      button.ghost { background: transparent; color: var(--primary-text-color); }
      button:disabled { opacity: .5; }
      .pill { font-size: 12px; padding: 2px 8px; border-radius: 999px; background: var(--warning-color, #ffa600); color: #000; margin-left: 6px; }
      .progress { height: 6px; border-radius: 3px; overflow: hidden; margin: 6px 0 4px;
        background: color-mix(in srgb, var(--primary-color, #03a9f4) 22%, var(--secondary-background-color, #eee)); }
      .bar { height: 100%; border-radius: 3px; background: var(--primary-color, #03a9f4); transition: width .4s ease; }
      .note { color: var(--error-color); font-size: 13px; }
      .presets { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; margin-bottom: 10px; }
      .presets button { font-size: 15px; padding: 12px; }
      .wifi { padding: 10px 12px; margin-bottom: 10px; border-radius: 12px; background: var(--secondary-background-color, #f3f3f3); font-size: 18px; }
      .mono { font-family: var(--code-font-family, monospace); }
    </style>
    <ha-card>
      ${this._config.title ? `<h2>${esc(this._config.title)}</h2>` : ""}
      ${this._note ? `<p class="note">${esc(this._note)}</p>` : ""}
      ${body}
    </ha-card>`;
  }
}

if (!customElements.get("daily-routine-card")) {
  customElements.define("daily-routine-card", DailyRoutineCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "daily-routine-card",
    name: "Daily Routine",
    description: "Today's routines: what's running, what's next, with Run now and Skip. Set group to show one group, and buttons: true for one-tap Run now buttons.",
  });
}
