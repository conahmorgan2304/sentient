/* Sentient hub card — the house's voice at a glance, for a main dashboard. Ships with Sentient;
   add it as `type: custom:sentient-hub-card`. No name is shown anywhere.

   What it shows: a core that lights up while a speaker listens, thinks or talks (tap it to open
   Sentient's page); the last thing said; the voice speakers; quick buttons (briefing, hush, guests and
   the light shows chosen in Sentient's settings); what's coming up next; today's numbers; a short
   feed; and, if set, the server's vitals.

   Options (all optional):
     height: 688            fixed height in px (otherwise it sizes to fit)
     feed_rows: 4           how many feed lines
     nodes:                 the voice speakers to show (default: every assist_satellite)
       - entity: assist_satellite.kitchen_speaker_assist_satellite
         name: Kitchen
     system: {cpu: sensor.x, memory: sensor.y, memory_total_mb: 16384, disk: sensor.z, nvr: sensor.w,
              temperature: sensor.t, boot: sensor.b}
*/
(() => {
  if (customElements.get("sentient-hub-card")) return;
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const hm = (d) => (d && !isNaN(d) ? d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "");
  const KEYS = ["last_spoken", "likely_room", "watches", "visitors", "health", "hush", "guest_mode", "party_mode"];
  const SHOWS = {
    disco: ["mdi:party-popper", "DISCO"], party: ["mdi:glass-cocktail", "PARTY"], rainbow: ["mdi:looks", "RAINBOW"],
    chill: ["mdi:weather-night", "CHILL"], halloween: ["mdi:halloween", "HALLOWEEN"], christmas: ["mdi:pine-tree", "XMAS"],
  };
  const FEED = {
    said: ["mdi:message-text-outline", "#7fdcff"], noticed: ["mdi:eye-outline", "rgba(150,210,225,.7)"],
    security: ["mdi:shield-alert-outline", "#ff6b80"], health: ["mdi:heart-pulse", "#ffbe55"],
    visitor: ["mdi:doorbell-video", "#b48cff"], mode: ["mdi:tune-variant", "#00fff7"], watch: ["mdi:cctv", "#b48cff"],
    heating: ["mdi:radiator", "#ffb070"], learned: ["mdi:school-outline", "#3dffa8"],
  };
  const LIVE = { listening: "listen", processing: "think", responding: "speak" };

  class SentientHubCard extends HTMLElement {
    setConfig(c) {
      this._cfg = c || {};
      this._rows = parseInt(this._cfg.feed_rows, 10) || 4;
      this._sig = null;
    }
    static getStubConfig() { return {}; }
    getCardSize() { return 10; }
    getGridOptions() { return { columns: 12, rows: "auto" }; }

    connectedCallback() {
      this._poll = setInterval(() => this._fetch(), 60000);
      this._clock = setInterval(() => { this._sig = null; this._draw(); }, 30000);
    }
    disconnectedCallback() { clearInterval(this._poll); clearInterval(this._clock); }

    set hass(h) {
      const first = !this._hass;
      this._hass = h;
      if (first) this._fetch();
      const ids = this._ids();
      const spoke = ids.last_spoken && h.states[ids.last_spoken];
      if (spoke && spoke.last_updated !== this._spokeSig) { this._spokeSig = spoke.last_updated; if (!first) this._fetch(); }
      const sig = [...KEYS.map((k) => (ids[k] && h.states[ids[k]] ? h.states[ids[k]].last_updated : "")),
        ...this._nodes().map((n) => (h.states[n.entity] || {}).state), ...Object.values(this._sys()).map((e) => (h.states[e] || {}).state)].join("|");
      if (sig !== this._sig) { this._sig = sig; this._draw(); }
    }

    _ids() {
      const n = Object.keys(this._hass.entities || {}).length;
      if (this._idCache && this._idCache.n === n) return this._idCache.ids;
      const ids = {};
      for (const e of Object.values(this._hass.entities || {})) {
        if (e.platform === "sentient" && KEYS.includes(e.translation_key)) ids[e.translation_key] = e.entity_id;
      }
      this._idCache = { n, ids };
      return ids;
    }
    _st(key) { const id = this._ids()[key]; return id ? this._hass.states[id] : undefined; }

    _nodes() {
      if (Array.isArray(this._cfg.nodes) && this._cfg.nodes.length) {
        return this._cfg.nodes.map((n) => (typeof n === "string" ? { entity: n } : n));
      }
      return Object.keys(this._hass.states).filter((e) => e.startsWith("assist_satellite.")).sort().map((entity) => ({ entity }));
    }
    _nodeName(n) {
      if (n.name) return n.name;
      const reg = (this._hass.entities || {})[n.entity];
      const area = reg && (reg.area_id || (reg.device_id && this._hass.devices && (this._hass.devices[reg.device_id] || {}).area_id));
      if (area && this._hass.areas && this._hass.areas[area]) return this._hass.areas[area].name;
      const st = this._hass.states[n.entity];
      return st ? String(st.attributes.friendly_name || n.entity).replace(/assist satellite/i, "").trim() : n.entity;
    }

    _sys() {
      const s = this._cfg.system || {}, S = this._hass.states, out = {};
      const pick = (key, suffixes) => {
        if (s[key]) { out[key] = s[key]; return; }
        const hit = Object.keys(S).find((e) => e.startsWith("sensor.") && suffixes.some((x) => e.endsWith(x)));
        if (hit) out[key] = hit;
      };
      pick("cpu", ["_processor_use"]);
      pick("temperature", ["_processor_temperature"]);
      pick("memory", ["_memory_usage"]);
      pick("disk", ["system_monitor_disk_usage"]);
      if (s.memory) out.memory = s.memory;
      if (s.nvr) out.nvr = s.nvr;
      pick("boot", ["_last_boot"]);
      return out;
    }

    async _fetch() {
      if (!this._hass) return;
      try {
        const r = await this._hass.callWS({ type: "sentient/timeline", hours: 24, limit: 120 });
        this._items = r.items || [];
      } catch (e) { this._items = this._items || []; }
      try {
        const a = await this._hass.callWS({ type: "sentient/items", kind: "alarms" });
        this._alarms = a.items || [];
      } catch (e) { this._alarms = this._alarms || []; }
      this._sig = null;
      this._draw();
    }

    _upNext() {
      const now = Date.now(), soon = [];
      for (const a of this._alarms || []) {
        const t = a._next ? new Date(a._next) : null;
        if (a.enabled !== false && t && !isNaN(t) && t - now < 36 * 3600e3) soon.push({ t, icon: "mdi:alarm", text: a.name + (a.description ? ` · ${a.description}` : "") });
      }
      const w = this._st("watches");
      for (const x of (w && w.attributes && w.attributes.watches) || []) {
        if (x.kind === "reminder") {
          const t = x.ends ? new Date(x.ends) : null;
          soon.push({ t: t && !isNaN(t) ? t : new Date(now + 3600e3), icon: "mdi:bell-ring-outline", text: x.what });
        }
      }
      soon.sort((a, b) => a.t - b.t);
      return soon[0];
    }

    _draw() {
      const h = this._hass;
      if (!h) return;
      if (!this.shadowRoot) {
        this.attachShadow({ mode: "open" });
        this.shadowRoot.addEventListener("click", (e) => this._click(e));
      }
      const S = h.states, nodes = this._nodes();
      const states = nodes.map((n) => (S[n.entity] || {}).state);
      const live = states.map((s) => LIVE[s]).find(Boolean);
      const mode = live || "idle";
      const label = { idle: "STANDING BY", listen: "LISTENING", think: "THINKING", speak: "SPEAKING" }[mode];
      const said = this._st("last_spoken");
      const saidText = said && !["unknown", "unavailable", ""].includes(said.state) ? said.state : "";
      const room = String((this._st("likely_room") || {}).state || "").toLowerCase();
      const hush = this._st("hush"), guest = this._st("guest_mode"), party = this._st("party_mode");

      // nodes
      const nodeHtml = nodes.map((n, i) => {
        const s = states[i], name = this._nodeName(n);
        const c = !s || s === "unavailable" ? "off" : LIVE[s] || "";
        const here = room && name.toLowerCase() === room && c !== "off";
        const icon = n.icon || (n.entity.includes("tablet") ? "mdi:tablet" : "mdi:speaker");
        return `<div class="node ${c}${here ? " here" : ""}" title="${esc(n.entity)}"><i></i><ha-icon icon="${icon}"></ha-icon><span>${esc(name)}</span></div>`;
      }).join("");

      // quick buttons
      const on = (st) => st && st.state === "on";
      const showOn = on(party), showStyle = party && party.attributes.style;
      const shows = party ? (party.attributes.buttons || []).filter((s) => SHOWS[s]) : [];
      if (showOn && SHOWS[showStyle] && !shows.includes(showStyle)) shows.push(showStyle);
      const btn = (act, id, icon, text, lit) => `<button class="q${lit ? " lit" : ""}" data-act="${act}" data-id="${esc(id || "")}"><ha-icon icon="${icon}"></ha-icon><span>${text}</span></button>`;
      const quick = [
        btn("brief", "", "mdi:text-box-outline", this._briefing ? "PREPARING" : "BRIEFING", this._briefing),
        hush ? btn("toggle", hush.entity_id, on(hush) ? "mdi:volume-off" : "mdi:volume-high", on(hush) ? "HUSHED" : "HUSH", on(hush)) : "",
        guest ? btn("toggle", guest.entity_id, "mdi:account-multiple", on(guest) ? "GUESTS IN" : "GUESTS", on(guest)) : "",
        ...shows.map((s) => btn("show", s, SHOWS[s][0], SHOWS[s][1], showOn && showStyle === s)),
      ].join("");

      // up next + today
      const next = this._upNext();
      const today = new Date().toDateString();
      const todays = (this._items || []).filter((i) => new Date(i.time).toDateString() === today);
      const saidCount = todays.filter((i) => i.kind === "said").length;
      const vis = this._st("visitors"), w = this._st("watches"), hl = this._st("health");
      const problems = hl ? parseInt(hl.state, 10) || 0 : 0;
      const tile = (icon, val, lbl, cls, more) => `<div class="tile ${cls || ""}"${more ? ` data-act="more" data-id="${esc(more)}"` : ""}><ha-icon icon="${icon}"></ha-icon><b>${esc(val)}</b><span>${lbl}</span></div>`;
      const tiles = tile("mdi:doorbell-video", vis ? vis.state : "–", "AT THE DOOR", "", vis && vis.entity_id)
        + tile("mdi:message-text-outline", saidCount, "SPOKEN", "")
        + tile("mdi:radar", w ? w.state : "0", "WATCHING", w && +w.state ? "on" : "", w && w.entity_id)
        + tile(problems ? "mdi:alert-outline" : "mdi:shield-check-outline", problems ? problems : "OK", problems ? "PROBLEMS" : "HEALTH", problems ? "warn" : "ok", hl && hl.entity_id);

      // feed: the latest few, one line each
      const feed = (this._items || []).filter((i) => FEED[i.kind] && !(i.kind === "noticed" && /decided it wasn't worth/.test(i.detail || "")))
        .slice(0, this._rows).map((i) => {
          const [icon, col] = FEED[i.kind];
          return `<div class="fr${i.alert ? " alert" : ""}"><ha-icon icon="${icon}" style="color:${col}"></ha-icon><span>${esc(i.text)}</span><em>${hm(new Date(i.time))}</em></div>`;
        }).join("") || '<div class="fr dim"><span>Nothing yet today</span></div>';

      // vitals
      const sys = this._sys(), num = (id) => { const v = parseFloat((S[id] || {}).state); return isNaN(v) ? null : v; };
      const bars = [];
      const bar = (n, v, warn, crit) => {
        if (v == null) return;
        const c = v < 0 ? "crit" : v >= crit ? "crit" : v >= warn ? "warn" : "";
        bars.push(`<div class="m ${c}"><span>${n}</span><div class="b"><i style="width:${Math.max(0, Math.min(100, v))}%"></i></div><em>${v < 0 ? "OFF" : Math.round(v) + "%"}</em></div>`);
      };
      bar("CPU", sys.cpu && num(sys.cpu), 60, 80);
      let mem = sys.memory && num(sys.memory);
      if (mem != null && this._cfg.system && this._cfg.system.memory_total_mb) mem = Math.min(100, (mem / this._cfg.system.memory_total_mb) * 100);
      bar("MEM", mem, 75, 85);
      bar("DSK", sys.disk && num(sys.disk), 80, 90);
      bar("NVR", sys.nvr && num(sys.nvr), 85, 90);
      const temp = sys.temperature && num(sys.temperature);
      let up = "";
      const boot = sys.boot && S[sys.boot] ? new Date(S[sys.boot].state) : null;
      if (boot && !isNaN(boot)) { const ms = Date.now() - boot; up = `UP ${Math.floor(ms / 864e5)}d ${Math.floor((ms % 864e5) / 36e5)}h`; }

      const fixed = parseInt(this._cfg.height, 10);
      this.shadowRoot.innerHTML = `<style>${CSS}</style>
        <ha-card class="${mode}" style="${fixed ? `height:${fixed}px` : ""}"><div class="grid"></div>
          <div class="top"><span>${up}</span><span class="dots">${nodes.map((_, i) => `<i class="${LIVE[states[i]] || (states[i] === "unavailable" ? "off" : "")}"></i>`).join("")}</span>
            <span class="${temp >= 75 ? "hot" : temp >= 60 ? "warm" : ""}">${temp != null ? Math.round(temp) + "°C" : ""}</span></div>
          <div class="mid">
            <button class="orb ${mode}" data-act="open" title="Open"><span class="ring r1"></span><span class="ring r2"></span><span class="ring r3"></span>
              <span class="nuc"><ha-icon icon="${mode === "speak" ? "mdi:waveform" : mode === "think" ? "mdi:dots-horizontal" : "mdi:microphone"}"></ha-icon></span></button>
            <div class="info"><div class="st">${label}</div>
              <div class="said">${saidText ? "“" + esc(saidText) + "”" : "<i>Nothing said yet</i>"}</div>
              ${said && saidText ? `<div class="when">${hm(new Date(said.last_changed))}</div>` : ""}</div>
          </div>
          <div class="nodes">${nodeHtml}</div>
          <div class="quick">${quick}</div>
          ${next ? `<div class="next"><ha-icon icon="${next.icon}"></ha-icon><span class="nl">UP NEXT</span><span class="nt">${esc(next.text)}</span><em>${next.t.toDateString() === new Date().toDateString() ? hm(next.t) : next.t.toLocaleDateString("en-GB", { weekday: "short" }).toUpperCase() + " " + hm(next.t)}</em></div>` : ""}
          <div class="tiles">${tiles}</div>
          <div class="fh"><span>FEED</span><i></i><button class="more" data-act="feed">ALL</button></div>
          <div class="feed">${feed}</div>
          ${bars.length ? `<div class="sys">${bars.join("")}</div>` : ""}
        </ha-card>`;
    }

    _click(e) {
      const el = e.composedPath().find((x) => x.dataset && x.dataset.act);
      if (!el) return;
      const act = el.dataset.act, id = el.dataset.id, h = this._hass;
      const go = (path) => { history.pushState(null, "", path); window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } })); };
      if (act === "open") go("/sentient");
      else if (act === "feed") go("/sentient/timeline");
      else if (act === "toggle") h.callService("switch", "toggle", { entity_id: id });
      else if (act === "more") this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: id }, bubbles: true, composed: true }));
      else if (act === "show") {
        const p = this._st("party_mode");
        const stop = p && p.state === "on" && p.attributes.style === id;
        h.callService("sentient", "party", stop ? { on: false } : { style: id }).catch((err) => window.alert(err.message || err));
      } else if (act === "brief" && !this._briefing) {
        this._briefing = true; this._sig = null; this._draw();
        const done = () => { this._briefing = false; this._sig = null; this._draw(); };
        h.callService("sentient", "briefing", { priority: "urgent" }).then(() => setTimeout(done, 4000), done);
      }
    }
  }

  const CSS = `
  :host{display:block}
  ha-card{position:relative;overflow:hidden;display:flex;flex-direction:column;gap:10px;padding:14px 16px;box-sizing:border-box;
    background:linear-gradient(160deg,rgba(10,16,28,.96),rgba(12,20,36,.92));border:1px solid rgba(0,255,247,.3);border-radius:22px;
    box-shadow:0 0 16px rgba(0,255,247,.16),inset 0 0 22px rgba(0,255,247,.05);color:#d6f6ff;font-family:system-ui,-apple-system,sans-serif}
  .grid{position:absolute;inset:0;pointer-events:none;opacity:.35;
    background-image:linear-gradient(rgba(0,255,247,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(0,255,247,.05) 1px,transparent 1px);background-size:24px 24px}
  .top{display:flex;align-items:center;justify-content:space-between;font-size:10px;font-weight:700;letter-spacing:.2em;color:rgba(150,220,235,.65);z-index:1}
  .top .warm{color:#ffbe55} .top .hot{color:#ff6b80}
  .dots{display:flex;gap:5px} .dots i{width:6px;height:6px;border-radius:50%;background:rgba(0,255,247,.35)}
  .dots i.off{background:rgba(255,107,128,.6)} .dots i.listen,.dots i.speak,.dots i.think{background:#00fff7;box-shadow:0 0 8px #00fff7}
  .mid{display:flex;align-items:center;gap:16px;z-index:1}
  .orb{all:unset;cursor:pointer;position:relative;width:104px;height:104px;flex:none;display:grid;place-items:center;-webkit-tap-highlight-color:transparent}
  .ring{position:absolute;inset:0;border-radius:50%;border:1.5px solid rgba(0,255,247,.35)}
  .r2{inset:12px;border-style:dashed;border-color:rgba(0,255,247,.25);animation:spin 30s linear infinite}
  .r3{inset:24px;border-color:rgba(0,255,247,.55);box-shadow:0 0 18px rgba(0,255,247,.25)}
  .r1{border-top-color:#00fff7;animation:spin 8s linear infinite;filter:drop-shadow(0 0 5px #00fff7)}
  .nuc{width:44px;height:44px;border-radius:50%;display:grid;place-items:center;background:radial-gradient(circle at 35% 35%,rgba(0,255,247,.45),rgba(0,60,80,.6));
    box-shadow:0 0 18px rgba(0,255,247,.45);animation:breathe 4s ease-in-out infinite} .nuc ha-icon{--mdc-icon-size:22px;color:#e9ffff}
  .orb.listen .r1{animation-duration:1.4s} .orb.listen .nuc{animation-duration:1s}
  .orb.think .r1{animation-duration:.7s;border-top-color:#c77dff;filter:drop-shadow(0 0 6px #c77dff)} .orb.think .nuc{background:radial-gradient(circle at 35% 35%,rgba(199,125,255,.55),rgba(50,20,80,.6));box-shadow:0 0 20px rgba(199,125,255,.5)}
  .orb.speak .r3{animation:pulse 1s ease-in-out infinite}
  @keyframes spin{to{transform:rotate(360deg)}} @keyframes breathe{50%{transform:scale(1.07);opacity:.85}} @keyframes pulse{50%{transform:scale(1.08);box-shadow:0 0 28px rgba(0,255,247,.55)}}
  .info{min-width:0;flex:1} .st{font-size:12px;font-weight:800;letter-spacing:.28em;color:#00fff7;text-shadow:0 0 8px rgba(0,255,247,.6);margin-bottom:6px}
  .said{font-size:13.5px;line-height:1.35;color:#e6f7ff;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden} .said i{color:rgba(150,210,225,.55)}
  .when{font-size:10px;letter-spacing:.14em;color:rgba(150,220,235,.55);margin-top:4px}
  .nodes{display:grid;grid-template-columns:repeat(auto-fit,minmax(80px,1fr));gap:6px;z-index:1}
  .node{display:flex;align-items:center;gap:6px;padding:6px 8px;border-radius:11px;border:1px solid rgba(0,255,247,.16);background:rgba(0,255,247,.03);
    font-size:9.5px;font-weight:700;letter-spacing:.12em;color:rgba(190,235,245,.8);text-transform:uppercase;min-width:0}
  .node span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis} .node ha-icon{--mdc-icon-size:15px;color:#5fd8e6;flex:none}
  .node i{width:6px;height:6px;border-radius:50%;background:#3dffa8;box-shadow:0 0 6px #3dffa8;flex:none}
  .node.off{opacity:.5} .node.off i{background:#ff6b80;box-shadow:0 0 6px #ff6b80}
  .node.here{border-color:rgba(0,255,247,.5);background:rgba(0,255,247,.08)}
  .node.listen,.node.think,.node.speak{border-color:#00fff7;box-shadow:0 0 10px rgba(0,255,247,.35)} .node.listen i,.node.speak i,.node.think i{background:#00fff7}
  .quick{display:flex;flex-wrap:wrap;gap:6px;z-index:1}
  .q{all:unset;cursor:pointer;display:flex;align-items:center;gap:6px;padding:7px 11px;border-radius:12px;border:1px solid rgba(0,255,247,.28);
    background:rgba(0,255,247,.04);font-size:10px;font-weight:800;letter-spacing:.14em;color:#bfefff;-webkit-tap-highlight-color:transparent}
  .q ha-icon{--mdc-icon-size:15px;color:#5fd8e6} .q:hover{background:rgba(0,255,247,.1)}
  .q.lit{background:#00fff7;color:#041016;border-color:#00fff7;box-shadow:0 0 14px rgba(0,255,247,.5)} .q.lit ha-icon{color:#041016}
  .q[data-id=halloween].lit{background:#ff8a1f;border-color:#ff8a1f} .q[data-id=christmas].lit{background:#ff4d5e;border-color:#ff4d5e}
  .next{display:flex;align-items:center;gap:8px;padding:8px 11px;border-radius:12px;background:rgba(180,140,255,.07);border:1px solid rgba(180,140,255,.3);z-index:1;min-width:0}
  .next ha-icon{--mdc-icon-size:17px;color:#b48cff;flex:none} .nl{font-size:9px;font-weight:800;letter-spacing:.2em;color:#b48cff;flex:none}
  .nt{flex:1;min-width:0;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis} .next em{font-style:normal;font-size:10.5px;font-weight:700;color:#d9c8ff;letter-spacing:.08em}
  .tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;z-index:1}
  .tile{display:flex;flex-direction:column;align-items:center;gap:2px;padding:8px 4px;border-radius:12px;border:1px solid rgba(0,255,247,.16);background:rgba(0,255,247,.03);cursor:default}
  .tile[data-act]{cursor:pointer} .tile ha-icon{--mdc-icon-size:16px;color:#5fd8e6} .tile b{font-size:18px;font-weight:700;color:#e9ffff;line-height:1.1}
  .tile span{font-size:8.5px;font-weight:800;letter-spacing:.14em;color:rgba(150,220,235,.6)}
  .tile.on{border-color:rgba(0,255,247,.45)} .tile.ok ha-icon{color:#3dffa8} .tile.warn{border-color:rgba(255,190,85,.5)} .tile.warn ha-icon,.tile.warn b{color:#ffbe55}
  .fh{display:flex;align-items:center;gap:8px;font-size:9.5px;font-weight:800;letter-spacing:.24em;color:#00fff7;z-index:1} .fh i{flex:1;height:1px;background:linear-gradient(90deg,rgba(0,255,247,.4),transparent)}
  .more{all:unset;cursor:pointer;font-size:9px;letter-spacing:.16em;color:rgba(150,220,235,.7);padding:2px 8px;border:1px solid rgba(0,255,247,.2);border-radius:8px}
  .feed{display:flex;flex-direction:column;gap:4px;z-index:1;min-height:0;overflow:hidden;flex:1}
  .fr{display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:9px;background:rgba(0,255,247,.025);font-size:11.5px;min-width:0}
  .fr ha-icon{--mdc-icon-size:14px;flex:none} .fr span{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#d6f2fb}
  .fr em{font-style:normal;font-size:10px;color:rgba(150,220,235,.55);flex:none} .fr.alert{background:rgba(255,107,128,.1)} .fr.dim span{color:rgba(150,210,225,.5)}
  .sys{display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:8px;z-index:1;margin-top:auto}
  .m{display:flex;align-items:center;gap:5px;font-size:9px;font-weight:700;letter-spacing:.12em;color:rgba(150,220,235,.65)}
  .m .b{flex:1;height:4px;border-radius:2px;background:rgba(0,255,247,.1);overflow:hidden} .m .b i{display:block;height:100%;background:linear-gradient(90deg,#00a8ff,#00fff7)}
  .m em{font-style:normal;color:#cfefff;min-width:28px;text-align:right} .m.warn .b i{background:#ffbe55} .m.crit .b i{background:#ff6b80} .m.crit em{color:#ff8a9a}
  `;

  customElements.define("sentient-hub-card", SentientHubCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "sentient-hub-card", name: "Sentient hub",
    description: "The house's voice at a glance: speaking, speakers, quick buttons, up next, today and a short feed." });
})();
