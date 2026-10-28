/* Sentient screen card — a wall tablet's screen, run by the house. Ships with Sentient;
   add it as `type: custom:sentient-screen-card`, ideally alone on a page (panel view).

   It shows, by itself:
     alert    something urgent (safety, someone about at night): big, with the camera if there is one
     notice   what the house just said out loud (the doorbell, a door left open, the washing done…)
     morning  when the house wakes up: weather, today, anything open or wrong, an overnight thought
     idle     the house at a glance
   Cards clear themselves (ten minutes; safety alerts stay until dismissed) or tap ✕.

   Options (all optional):
     height: 800        fixed height in px (default: the full screen height)
     weather: weather.x the weather to show (default: the first one)
     glance:            extra things for the idle screen
       - entity: sensor.living_room_temperature
         name: Living room
*/
(() => {
  if (customElements.get("sentient-screen-card")) return;
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const hm = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }); };
  const KEYS = ["screen", "last_spoken", "watches", "visitors", "health", "house", "mind", "drive"];
  const SOURCE_ICON = [
    ["visitors", "mdi:doorbell-video"], ["announce:", "mdi:bullhorn-outline"], ["critical", "mdi:fire-alert"],
    ["camera security", "mdi:cctv"], ["night_watch", "mdi:shield-moon-outline"], ["left_open", "mdi:door-open"],
    ["appliance", "mdi:washing-machine"], ["animals", "mdi:rabbit"], ["check:", "mdi:cctv"], ["watch:", "mdi:eye-outline"],
    ["alarm:", "mdi:alarm"], ["followthrough", "mdi:alert-circle-outline"], ["service", "mdi:message-text-outline"],
  ];
  const WX = {
    "clear-night": "mdi:weather-night", cloudy: "mdi:weather-cloudy", fog: "mdi:weather-fog", hail: "mdi:weather-hail",
    lightning: "mdi:weather-lightning", "lightning-rainy": "mdi:weather-lightning-rainy", partlycloudy: "mdi:weather-partly-cloudy",
    pouring: "mdi:weather-pouring", rainy: "mdi:weather-rainy", snowy: "mdi:weather-snowy", "snowy-rainy": "mdi:weather-snowy-rainy",
    sunny: "mdi:weather-sunny", windy: "mdi:weather-windy", "windy-variant": "mdi:weather-windy-variant", exceptional: "mdi:alert-outline",
  };

  class SentientScreenCard extends HTMLElement {
    setConfig(c) { this._cfg = c || {}; this._sig = null; }
    static getStubConfig() { return {}; }
    getCardSize() { return 12; }
    getGridOptions() { return { columns: 12, rows: "auto" }; }

    connectedCallback() {
      this._clock = setInterval(() => this._drawClock(), 10000);
      this._cam = setInterval(() => this._refreshCamera(), 1500);
    }
    disconnectedCallback() { clearInterval(this._clock); clearInterval(this._cam); }

    set hass(h) {
      this._hass = h;
      if (!this.shadowRoot) this._build();
      const ids = this._ids();
      const wx = this._weather();
      const sig = [...KEYS.map((k) => (ids[k] && h.states[ids[k]] ? h.states[ids[k]].last_updated : "")),
        wx ? wx.last_updated : "", ...(this._cfg.glance || []).map((g) => (h.states[g.entity || g] || {}).state)].join("|");
      if (sig !== this._sig) { this._sig = sig; this._draw(); }
    }

    _ids() {
      const n = Object.keys(this._hass.entities || {}).length;
      if (this._idCache && this._idCache.n === n) return this._idCache.ids;
      const ids = {};
      for (const e of Object.values(this._hass.entities || {})) {
        // sensors only: the Tablet screen switch shares the "screen" key
        if (e.platform === "sentient" && KEYS.includes(e.translation_key) && e.entity_id.startsWith("sensor.")) ids[e.translation_key] = e.entity_id;
      }
      this._idCache = { n, ids };
      return ids;
    }
    _st(k) { const id = this._ids()[k]; return id ? this._hass.states[id] : undefined; }
    _attr(k, a, d) { const s = this._st(k); return s && s.attributes[a] !== undefined ? s.attributes[a] : d; }
    _weather() {
      const S = this._hass.states;
      return S[this._cfg.weather] || Object.values(S).find((s) => s.entity_id.startsWith("weather.") && s.attributes.temperature != null);
    }

    _build() {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.innerHTML = `<style>${CSS}</style><ha-card><div class="wrap" id="wrap">
        <div class="top"><div class="clock"><span id="time"></span><i id="date"></i></div><div class="wx" id="wx"></div></div>
        <div class="main" id="main"></div><div class="foot" id="foot"></div></div></ha-card>`;
      this.shadowRoot.addEventListener("click", (ev) => {
        const x = ev.target.closest("[data-x]");
        if (x) { this._hass.callService("sentient", "clear_screen", { card_id: x.dataset.x }); return; }
        if (ev.target.closest("[data-lh]")) { this._toggleLastHour(); return; }
        const more = ev.target.closest("[data-more]");
        if (more) this.dispatchEvent(new CustomEvent("hass-more-info", { bubbles: true, composed: true, detail: { entityId: more.dataset.more } }));
      });
      const h = parseInt(this._cfg.height, 10);
      this._$("wrap").style.height = h ? `${h}px` : "calc(100vh - var(--header-height, 56px) - 16px)";
    }
    _$(id) { return this.shadowRoot.getElementById(id); }

    _drawClock() {
      if (!this.shadowRoot) return;
      const now = new Date();
      this._$("time").textContent = now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
      this._$("date").textContent = now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" }).toUpperCase();
    }

    _draw() {
      this._drawClock();
      const wx = this._weather();
      this._$("wx").innerHTML = wx ? `<ha-icon icon="${WX[wx.state] || "mdi:weather-partly-cloudy"}"></ha-icon><b>${Math.round(wx.attributes.temperature)}°</b>
        <i>${esc(String(wx.state).replace("partlycloudy", "partly cloudy").replace("-", " ")).toUpperCase()}</i>` : "";
      const screen = this._st("screen");
      const mode = screen ? screen.state : "idle";
      const cards = this._attr("screen", "cards", []);
      const morning = this._attr("screen", "morning", null);
      const wrap = this._$("wrap");
      wrap.className = `wrap ${mode}`;
      const main = this._$("main");
      if (mode === "alert" || mode === "notice") main.innerHTML = this._cards(cards);
      else if (mode === "morning" && morning) main.innerHTML = this._morning(morning);
      else if (this._lh) main.innerHTML = this._lastHour();
      else main.innerHTML = this._idle();
      this._$("foot").innerHTML = this._foot();
      this._refreshCamera(true);
    }

    _icon(source) { const hit = SOURCE_ICON.find(([p]) => String(source || "").startsWith(p)); return hit ? hit[1] : "mdi:message-text-outline"; }

    _cards(cards) {
      const [lead, ...rest] = cards;
      const cam = lead.camera && this._hass.states[lead.camera];
      return `<div class="lead ${lead.kind}"><div class="lh"><ha-icon icon="${this._icon(lead.source)}"></ha-icon><span>${lead.kind === "alert" ? "ALERT" : "JUST NOW"} · ${hm(lead.time)}</span>
          <a class="x" data-x="${esc(lead.id)}" title="Dismiss">✕</a></div>
          <div class="lt">${esc(lead.text)}</div>
          ${cam ? `<div class="cam" data-more="${esc(lead.camera)}"><img id="camimg" data-cam="${esc(lead.camera)}" alt=""><span>${esc(cam.attributes.friendly_name || lead.camera).toUpperCase()} · LIVE</span></div>` : ""}</div>`
        + (rest.length ? `<div class="rest">${rest.map((c) => `<div class="row ${c.kind}"><ha-icon icon="${this._icon(c.source)}"></ha-icon><b>${esc(c.text)}</b><u>${hm(c.time)}</u><a class="x" data-x="${esc(c.id)}">✕</a></div>`).join("")}</div>` : "");
    }

    _refreshCamera(force) {
      const img = this.shadowRoot && this.shadowRoot.getElementById("camimg");
      if (!img || !this._hass) return;
      const st = this._hass.states[img.dataset.cam];
      if (!st || !st.attributes.entity_picture) return;
      if (!force && img.dataset.loading === "1") return;
      img.dataset.loading = "1";
      const next = new Image();
      next.onload = () => { img.src = next.src; img.dataset.loading = ""; };
      next.onerror = () => { img.dataset.loading = ""; };
      next.src = `${st.attributes.entity_picture}${st.attributes.entity_picture.includes("?") ? "&" : "?"}t=${Date.now()}`;
    }

    _morning(m) {
      const hour = new Date().getHours();
      const hello = hour < 12 ? "GOOD MORNING" : "TODAY";
      const sec = (icon, title, items) => (items && items.length ? `<div class="sec"><div class="sh"><ha-icon icon="${icon}"></ha-icon>${title}</div>${items.map((t) => `<div class="si">${esc(t)}</div>`).join("")}</div>` : "");
      const todo = m.to_do ? Object.entries(m.to_do).flatMap(([list, items]) => items.slice(0, 4).map((i) => `${i} · ${list}`)) : [];
      return `<div class="morning"><div class="mh"><span>${hello}</span><a class="x" data-x="morning" title="Dismiss">✕</a></div>
        ${m.weather ? `<div class="mw">${esc(m.weather)}</div>` : ""}
        <div class="secs">
          ${sec("mdi:bell-ring-outline", "REMINDERS", m.reminders_today)}
          ${sec("mdi:calendar-today", "COMING UP", m.calendar_next_24h)}
          ${sec("mdi:format-list-checks", "TO DO", todo)}
          ${sec("mdi:door-open", "OPEN", m.open_now)}
          ${sec("mdi:alert-outline", "NEEDS A LOOK", [...(m.problems || []), ...(m.animals_outside || []), ...(Array.isArray(m.unusual_overnight) ? m.unusual_overnight : [])])}
          ${sec("mdi:lightbulb-on-outline", "A THOUGHT", m.overnight_thoughts)}
        </div></div>`;
    }

    _idle() {
      const S = this._hass.states;
      // the latest thing said, while it's still news: questions for ten minutes, the rest for two hours
      const said = this._attr("last_spoken", "log", []).find((l) => {
        if (!l.said) return false;
        const age = (Date.now() - new Date(l.time).getTime()) / 60000;
        const asking = /^(offer|curious|learning)/.test(l.source || "") || /\?\s*$/.test(l.said);
        return age < (asking ? 10 : 120);
      });
      const watches = this._attr("watches", "watches", []);
      const next = watches.slice().sort((a, b) => String(a.ends).localeCompare(String(b.ends)))[0];
      const mind = (this._st("mind") || {}).attributes || {};
      const running = (mind.appliances || []).filter((a) => a.running);
      const problems = this._attr("health", "issues", []);
      const asleep = (this._st("house") || {}).state === "asleep";
      const tiles = [
        { icon: asleep ? "mdi:sleep" : "mdi:home-heart", val: asleep ? "ASLEEP" : "AWAKE", label: "THE HOUSE" },
        { icon: "mdi:doorbell-video", val: (this._st("visitors") || {}).state || 0, label: "VISITORS TODAY" },
        { icon: problems.length ? "mdi:alert-outline" : "mdi:check-circle-outline", val: problems.length || "ALL GOOD", label: problems.length ? "PROBLEMS" : "HOUSE HEALTH", cls: problems.length ? "warn" : "" },
        ...(this._cfg.glance || []).map((g) => {
          const id = g.entity || g, st = S[id];
          const unit = st && st.attributes.unit_of_measurement ? st.attributes.unit_of_measurement : "";
          return { icon: g.icon || (st && st.attributes.icon) || "mdi:information-outline", val: st ? `${st.state}${unit}` : "–", label: String(g.name || (st && st.attributes.friendly_name) || id).toUpperCase(), more: id };
        }),
      ];
      return `<div class="idle"><div class="tiles">${tiles.map((t) => `<div class="tile ${t.cls || ""}"${t.more ? ` data-more="${esc(t.more)}"` : ""}><ha-icon icon="${t.icon}"></ha-icon><b>${esc(t.val)}</b><i>${esc(t.label)}</i></div>`).join("")}</div>
        <div class="lines">
          ${running.map((a) => `<div class="row"><ha-icon icon="mdi:washing-machine"></ha-icon><b>${esc(a.name)} running</b><u>${a.done_about ? "DONE ~" + esc(a.done_about) : ""}</u></div>`).join("")}
          ${next ? `<div class="row"><ha-icon icon="${next.kind === "reminder" ? "mdi:bell-ring-outline" : "mdi:eye-outline"}"></ha-icon><b>${esc(next.what)}</b><u>${hm(next.ends)}</u></div>` : ""}
          ${problems.slice(0, 2).map((p) => `<div class="row warn"><ha-icon icon="mdi:alert-outline"></ha-icon><b>${esc(p.text || p.name)}</b></div>`).join("")}
          ${said ? `<div class="row dim"><ha-icon icon="mdi:account-voice"></ha-icon><b>${esc(said.said)}</b><u>${hm(said.time)}</u></div>` : ""}
        </div></div>`;
    }

    // "what happened this last hour?" — straight from the timeline, no AI; closes itself after a minute
    async _toggleLastHour() {
      clearTimeout(this._lhTimer);
      if (this._lh) { this._lh = null; this._draw(); return; }
      try {
        const res = await this._hass.callWS({ type: "sentient/timeline", hours: 1, limit: 30 });
        this._lh = (res.items || []).filter((i) => !["held", "digest"].includes(i.kind));
      } catch (e) { this._lh = []; }
      this._lhTimer = setTimeout(() => { this._lh = null; this._draw(); }, 60000);
      this._draw();
    }

    _lastHour() {
      const rows = this._lh.length ? this._lh.map((i) => `<div class="row"><ha-icon icon="${this._icon(i.source || i.kind)}"></ha-icon><b>${esc(i.text)}</b><u>${hm(i.time)}</u></div>`).join("")
        : '<div class="row dim"><b>Nothing in the last hour.</b></div>';
      return `<div class="morning"><div class="mh"><span>THE LAST HOUR</span><a class="x" data-lh="1" title="Close">✕</a></div><div class="lines">${rows}</div></div>`;
    }

    _foot() {
      const mode = (this._st("screen") || {}).state || "idle";
      const label = { alert: "ALERT", notice: "NOTICE", morning: "MORNING", idle: "ALL QUIET" }[mode] || "";
      const btn = this._cfg.last_hour === false || mode !== "idle" ? ""
        : `<a class="lhb" data-lh="1"><ha-icon icon="mdi:history"></ha-icon>${this._lh ? "BACK" : "LAST HOUR"}</a>`;
      return `<span class="dot ${mode}"></span><span>${label}</span>${btn}`;
    }
  }

  const CSS = `
.lhb{margin-left:auto;display:flex;align-items:center;gap:6px;cursor:pointer;padding:6px 12px;border:1px solid rgba(0,255,247,.35);border-radius:999px;color:#7ff6ff;letter-spacing:.12em}
.lhb ha-icon{--mdc-icon-size:16px}
:host{display:block}
ha-card{overflow:hidden;border-radius:22px;border:1px solid rgba(0,255,247,.25);color:#dff6fa;
  background:radial-gradient(ellipse at 50% 0%,rgba(0,255,247,.08),transparent 55%),linear-gradient(160deg,#070c16,#0e1627);box-shadow:0 0 28px rgba(0,255,255,.1)}
.wrap{display:flex;flex-direction:column;padding:22px 26px;box-sizing:border-box;min-height:420px;transition:background .6s}
.wrap.alert{background:radial-gradient(ellipse at 50% 0%,rgba(255,90,90,.16),transparent 60%)}
.top{display:flex;align-items:flex-start;justify-content:space-between}
.clock span{display:block;font-size:64px;font-weight:200;letter-spacing:.04em;line-height:1;color:#e8fbff;text-shadow:0 0 22px rgba(0,255,247,.35)}
.clock i{display:block;margin-top:8px;font-style:normal;font-size:12px;font-weight:700;letter-spacing:.28em;color:rgba(150,220,235,.7)}
.wx{display:flex;align-items:center;gap:10px} .wx ha-icon{--mdc-icon-size:40px;color:#00fff7;filter:drop-shadow(0 0 8px rgba(0,255,247,.5))}
.wx b{font-size:40px;font-weight:300} .wx i{font-style:normal;font-size:10px;font-weight:700;letter-spacing:.2em;color:rgba(150,220,235,.7);max-width:90px}
.main{flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center;gap:16px;padding:18px 0;overflow:auto}
.x{flex:none;margin-left:auto;width:34px;height:34px;display:flex;align-items:center;justify-content:center;border-radius:50%;cursor:pointer;
  color:rgba(150,220,235,.6);border:1px solid rgba(0,255,247,.2);font-size:14px}
.lead{border-radius:20px;padding:20px 22px;border:1px solid rgba(0,255,247,.35);background:rgba(0,255,247,.06);box-shadow:0 0 22px rgba(0,255,247,.12)}
.lead.alert{border-color:rgba(255,107,128,.6);background:rgba(255,107,128,.08);box-shadow:0 0 30px rgba(255,107,128,.25);animation:glow 2s ease-in-out infinite}
.lh{display:flex;align-items:center;gap:10px;font-size:11px;font-weight:700;letter-spacing:.22em;color:#7fefff} .lead.alert .lh{color:#ff8fa3}
.lh ha-icon{--mdc-icon-size:22px}
.lt{margin-top:12px;font-size:30px;line-height:1.25;font-weight:400;color:#f2fcff}
.cam{position:relative;margin-top:16px;border-radius:14px;overflow:hidden;border:1px solid rgba(0,255,247,.3);background:#000;aspect-ratio:16/9;max-height:46vh;cursor:pointer}
.cam img{width:100%;height:100%;object-fit:contain;display:block}
.cam span{position:absolute;left:12px;bottom:10px;font-size:10px;font-weight:700;letter-spacing:.2em;color:#fff;background:rgba(0,0,0,.55);padding:4px 8px;border-radius:6px}
.rest,.lines{display:flex;flex-direction:column;gap:8px}
.row{display:flex;align-items:center;gap:12px;padding:12px 14px;border-radius:14px;border:1px solid rgba(0,255,247,.15);background:rgba(0,255,247,.04)}
.row ha-icon{--mdc-icon-size:20px;color:#00fff7;flex:none} .row b{flex:1;min-width:0;font-weight:400;font-size:16px;line-height:1.3}
.row u{text-decoration:none;flex:none;font-size:11px;font-weight:700;letter-spacing:.14em;color:rgba(150,220,235,.7)}
.row.alert{border-color:rgba(255,107,128,.45)} .row.alert ha-icon{color:#ff8fa3}
.row.warn ha-icon{color:#ffbe55} .row.dim b{color:rgba(220,240,248,.7)}
.morning .mh{display:flex;align-items:center;font-size:30px;font-weight:200;letter-spacing:.3em;color:#e8fbff;text-shadow:0 0 16px rgba(0,255,247,.35)}
.mw{margin-top:6px;font-size:16px;color:rgba(200,235,245,.85)}
.secs{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-top:16px}
.sec{border-radius:16px;padding:14px 16px;border:1px solid rgba(0,255,247,.18);background:rgba(0,255,247,.04)}
.sh{display:flex;align-items:center;gap:8px;font-size:10px;font-weight:700;letter-spacing:.22em;color:#00fff7;margin-bottom:8px} .sh ha-icon{--mdc-icon-size:16px}
.si{font-size:15px;line-height:1.4;color:#e6f5fa;padding:3px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.tile{display:flex;flex-direction:column;align-items:flex-start;gap:6px;padding:16px;border-radius:16px;border:1px solid rgba(0,255,247,.18);background:rgba(0,255,247,.05)}
.tile[data-more]{cursor:pointer} .tile ha-icon{--mdc-icon-size:24px;color:#00fff7} .tile b{font-size:24px;font-weight:400;color:#fff}
.tile i{font-style:normal;font-size:9.5px;font-weight:700;letter-spacing:.2em;color:rgba(150,220,235,.7)}
.tile.warn ha-icon{color:#ffbe55}
.idle{display:flex;flex-direction:column;gap:16px}
.foot{display:flex;align-items:center;gap:8px;font-size:10px;font-weight:700;letter-spacing:.24em;color:rgba(150,220,235,.6)}
.dot{width:8px;height:8px;border-radius:50%;background:#00fff7;box-shadow:0 0 8px #00fff7} .dot.alert{background:#ff6b80;box-shadow:0 0 10px #ff6b80;animation:blink 1s infinite}
.dot.notice{background:#7fefff} .dot.morning{background:#ffd27f;box-shadow:0 0 8px #ffd27f}
@keyframes glow{50%{box-shadow:0 0 44px rgba(255,107,128,.4)}} @keyframes blink{50%{opacity:.3}}
@media (max-width:700px){.clock span{font-size:44px}.lt{font-size:22px}.wrap{padding:16px}}
`;

  customElements.define("sentient-screen-card", SentientScreenCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "sentient-screen-card", name: "Sentient screen", description: "A wall tablet's screen, run by the house: alerts with the camera, notices, a morning card and the house at a glance." });
})();
