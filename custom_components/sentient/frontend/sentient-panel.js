/* Sentient sidebar panel — ships with the integration, not editable from the dashboard editor.
   Finds Sentient's own sensors through the entity registry (platform "sentient"), so it works in any house. */
(() => {
  if (customElements.get("sentient-panel")) return;

  const KEYS = ["memories", "last_spoken", "likely_room", "watches", "routines", "visitors"];
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const hm = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }); };
  const left = (iso, fallback) => {
    const d = iso ? new Date(iso) : null;
    const m = d && !isNaN(d) ? Math.max(0, Math.round((d - Date.now()) / 60000)) : fallback;
    if (m == null) return "LIVE";
    return m >= 60 ? `${Math.floor(m / 60)}H ${m % 60}M` : `${m} MIN`;
  };

  class SentientPanel extends HTMLElement {
    set hass(hass) {
      this._hass = hass;
      if (!this.shadowRoot) this._build();
      const ids = this._ids();
      const sig = KEYS.map((k) => { const s = ids[k] && hass.states[ids[k]]; return s ? s.last_updated : ""; }).join("|");
      if (sig !== this._sig) { this._sig = sig; this._render(); }
    }
    set narrow(n) { this._narrow = n; if (this._menu) this._menu.narrow = n; }
    set panel(p) { this._panel = p; }

    connectedCallback() { this._tick = setInterval(() => this._render(), 30000); }
    disconnectedCallback() { clearInterval(this._tick); }

    _ids() {
      if (this._idCache && this._idCache.n === Object.keys(this._hass.entities || {}).length) return this._idCache.ids;
      const ids = {};
      for (const e of Object.values(this._hass.entities || {})) {
        if (e.platform === "sentient" && KEYS.includes(e.translation_key)) ids[e.translation_key] = e.entity_id;
      }
      this._idCache = { n: Object.keys(this._hass.entities || {}).length, ids };
      return ids;
    }
    _st(key) { const id = this._ids()[key]; return id ? this._hass.states[id] : undefined; }
    _attr(key, name, dflt) { const s = this._st(key); return (s && s.attributes && s.attributes[name] != null) ? s.attributes[name] : dflt; }
    _call(service, data, confirmText) {
      if (confirmText && !window.confirm(confirmText)) return;
      this._hass.callService("sentient", service, data).catch((err) => window.alert(err.message || String(err)));
    }

    _build() {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.innerHTML = `<style>${CSS}</style>
        <div class="top"><span id="menu"></span><span class="ttl" id="ttl">SENTIENT</span>
          <form class="ask" id="askf"><ha-icon icon="mdi:creation-outline"></ha-icon><input id="ask" placeholder="Ask anything…" autocomplete="off"></form>
          <button class="brief" id="brief"><ha-icon icon="mdi:text-box-outline"></ha-icon><span>BRIEFING</span></button>
          <button class="cog" id="cog" title="Settings"><ha-icon icon="mdi:cog-outline"></ha-icon></button></div>
        <div class="reply" id="reply" hidden></div>
        <div class="page"><div id="head" class="card head"></div>
          <div class="cols"><div class="card col"><div id="mem"></div>
            <form class="add" id="addf"><ha-icon icon="mdi:plus"></ha-icon><input id="addm" placeholder="Add a memory…" autocomplete="off"></form></div><div id="rout" class="col stack"></div>
            <div class="col stack"><div id="watch" class="card"></div><div id="visit" class="card"></div><div id="said" class="card"></div></div></div></div>`;
      const menu = document.createElement("ha-menu-button");
      menu.hass = this._hass; menu.narrow = this._narrow;
      this.shadowRoot.getElementById("menu").appendChild(menu);
      this._menu = menu;
      const brief = this.shadowRoot.getElementById("brief");
      brief.addEventListener("click", () => {
        if (brief.classList.contains("busy")) return;
        brief.classList.add("busy"); brief.querySelector("span").textContent = "PREPARING";
        const done = () => { brief.classList.remove("busy"); brief.querySelector("span").textContent = "BRIEFING"; };
        this._hass.callService("sentient", "briefing", { priority: "urgent" }).then(() => setTimeout(done, 4000), (e) => { done(); window.alert(e.message || e); });
      });
      this.shadowRoot.getElementById("cog").addEventListener("click", () => {
        history.pushState(null, "", "/config/integrations/integration/sentient");
        window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
      });
      this.shadowRoot.getElementById("addf").addEventListener("submit", (ev) => {
        ev.preventDefault();
        const input = this.shadowRoot.getElementById("addm"), text = input.value.trim();
        if (!text) return;
        this._hass.callService("sentient", "remember", { text }).then(() => { input.value = ""; }, (e) => window.alert(e.message || e));
      });
      this.shadowRoot.getElementById("askf").addEventListener("submit", (ev) => { ev.preventDefault(); this._ask(); });
      this.shadowRoot.addEventListener("click", (ev) => {
        const el = ev.target.closest("[data-act]");
        if (!el) return;
        const { act, id, text } = el.dataset;
        if (act === "forget") this._call("forget", { what: id }, `Forget: ${text}?`);
        if (act === "cancel") this._call("cancel_watch", { what: id }, `Stop: ${text}?`);
        if (act === "adopt") this._call("adopt_routine", { routine: id }, text);
      });
    }

    _render() {
      if (!this._hass || !this.shadowRoot) return;
      if (this._menu) this._menu.hass = this._hass;
      const R = (id) => this.shadowRoot.getElementById(id);
      const room = this._st("likely_room");
      const persona = this._attr("likely_room", "persona", "Sentient");
      R("ttl").textContent = String(persona).toUpperCase();

      if (!room) {
        R("head").innerHTML = `<div class="e">SENTIENT IS STARTING — OR ISN'T SET UP YET</div>`;
        return;
      }
      // header
      const scores = this._attr("likely_room", "scores", {});
      const top = Math.max(1, ...Object.values(scores));
      const bars = Object.entries(scores).slice(0, 5).map(([k, v]) =>
        `<div class="b"><span>${esc(k).toUpperCase()}</span><div><i style="width:${Math.round((v / top) * 100)}%"></i></div></div>`).join("");
      const stat = (n, l) => `<div class="s"><div class="n">${esc(n)}</div><div class="l">${l}</div></div>`;
      const st = (k) => (this._st(k) ? this._st(k).state : "—");
      R("head").innerHTML = `<img src="/sentient_static/logo.png" alt="">
        <div class="m"><div class="ti">${esc(persona).toUpperCase()}</div>
          <div class="sub">${this._attr("likely_room", "quiet_hours") ? "QUIET HOURS" : "AWAKE"} · PEOPLE IN ${esc(room.state).toUpperCase()} · ${this._attr("likely_room", "ai_checks_today", 0)} AI CHECKS TODAY</div>
          <div class="st">${stat(st("memories"), "MEMORIES")}${stat(st("watches"), "WATCHING")}${stat(st("routines"), "ROUTINES")}${stat(st("visitors"), "VISITORS TODAY")}</div></div>
        <div class="rm"><div class="t">WHERE PEOPLE ARE</div>${bars || '<div class="b">NO RECENT MOTION</div>'}</div>`;

      // memories
      const items = this._attr("memories", "items", []);
      R("mem").innerHTML = this._h("MEMORIES", `${items.length} KNOWN`) + (items.map((m) =>
        `<div class="r"><b title="${esc(m.text)}">${esc(m.text)}</b><s>${esc(m.category).toUpperCase()}</s>
          <a class="x" data-act="forget" data-id="${esc(m.id)}" data-text="${esc(m.text)}" title="Forget">✕</a></div>`).join("")
        || '<div class="e">NOTHING REMEMBERED YET · SAY “REMEMBER THAT…”</div>');

      // routines
      const routines = this._attr("routines", "routines", []);
      const on = routines.filter((r) => r.adopted).length;
      R("rout").innerHTML = `<div class="card">${this._h("ROUTINES", `${on} AUTOMATED · ${routines.length} LEARNED`, true)}</div>` +
        (routines.slice(0, 8).map((r) => {
          const tag = r.adopted ? "HOUSE DOES IT" : r.declined ? "DECLINED" : r.offered ? "OFFERED" : "LEARNED";
          const cls = r.adopted ? "on" : r.declined ? "off" : "";
          const ask = r.adopted ? `Stop the house doing: ${r.routine}` : `Let the house do this from now on: ${r.routine}`;
          return `<div class="card rt ${cls}" data-act="adopt" data-id="${esc(r.id)}" data-text="${esc(ask)}">
            <div class="r"><b class="wrap">${esc(r.routine)}</b><s>${tag}</s></div>
            <div class="hint">${esc(r.how_sure).toUpperCase()} · TAP TO ${r.adopted ? "STOP" : "LET THE HOUSE DO IT"}</div></div>`;
        }).join("") || '<div class="card"><div class="e">NO ROUTINES LEARNED YET — CHECKED NIGHTLY</div></div>');

      // watches
      const watches = this._attr("watches", "watches", []);
      R("watch").innerHTML = this._h("WATCHES &amp; REMINDERS", `${watches.length} ACTIVE`) + (watches.map((w) => {
        const ic = w.kind === "reminder" ? "mdi:bell-ring-outline" : w.kind === "camera" ? "mdi:cctv" : "mdi:eye-outline";
        return `<div class="r"><ha-icon icon="${ic}"></ha-icon><b>${esc(w.what)}${w.spot ? " · " + esc(w.spot) : ""}</b><u>${left(w.ends, w.minutes_left)}</u>
          <a class="x" data-act="cancel" data-id="${esc(w.id)}" data-text="${esc(w.what)}" title="Cancel">✕</a></div>`;
      }).join("") || '<div class="e">NOTHING BEING WATCHED · SAY “KEEP AN EYE ON…”</div>');

      // visitors
      const visits = this._attr("visitors", "recent", []);
      R("visit").innerHTML = this._h("VISITORS", `${st("visitors")} TODAY`) + (visits.slice(0, 5).map((v) =>
        `<div class="r"><ha-icon icon="${v.kind === "delivery" ? "mdi:package-variant-closed" : "mdi:doorbell-video"}"></ha-icon>
          <b>${v.name ? esc(v.name) + " — " : ""}${esc(v.description)}${v.parcel_left ? " · PARCEL LEFT" : ""}</b><u>${hm(v.time)}</u></div>`).join("")
        || '<div class="e">NO ONE AT THE DOOR YET</div>');

      // said & noticed
      const log = this._attr("last_spoken", "log", []).slice(0, 5).map((l) => l.said
        ? `<div class="r"><ha-icon icon="mdi:account-voice"></ha-icon><b>${esc(l.said)}</b><u>${hm(l.time)}</u></div>`
        : `<div class="r dim"><ha-icon icon="mdi:volume-off"></ha-icon><b>${esc(l.held)}</b><u>${esc(String(l.reason || "").split(":")[0].replace("_", " ")).toUpperCase()}</u></div>`).join("");
      const noticed = this._attr("last_spoken", "noticed", []).slice(0, 3).map((d) =>
        `<div class="r dim"><ha-icon icon="mdi:radar"></ha-icon><b>${esc((d.changes || []).join("; "))}</b><u>${esc(d.result).toUpperCase()}</u></div>`).join("");
      R("said").innerHTML = this._h("SAID &amp; NOTICED", "") + (log + noticed || '<div class="e">QUIET SO FAR</div>');
    }

    async _agent() {
      if (this._agentId !== undefined) return this._agentId;
      try {
        const res = await this._hass.callWS({ type: "assist_pipeline/pipeline/list" });
        const pipe = (res.pipelines || []).find((p) => p.id === res.preferred_pipeline);
        this._agentId = pipe ? pipe.conversation_engine : null;
      } catch (e) { this._agentId = null; }
      return this._agentId;
    }

    async _ask() {
      const input = this.shadowRoot.getElementById("ask"), reply = this.shadowRoot.getElementById("reply");
      const text = input.value.trim();
      if (!text) return;
      input.value = ""; reply.hidden = false;
      reply.innerHTML = `<span class="q">${esc(text)}</span><span class="a thinking">THINKING…</span>`;
      try {
        const msg = { type: "conversation/process", text, language: this._hass.language };
        const agent = await this._agent();
        if (agent) msg.agent_id = agent;
        if (this._cid) msg.conversation_id = this._cid;
        const res = await this._hass.callWS(msg);
        this._cid = res.conversation_id;
        const speech = (((res.response || {}).speech || {}).plain || {}).speech || "Done.";
        reply.innerHTML = `<span class="q">${esc(text)}</span><span class="a">${esc(speech)}</span><a class="x" id="rx">✕</a>`;
      } catch (e) {
        reply.innerHTML = `<span class="q">${esc(text)}</span><span class="a err">${esc(e.message || e)}</span><a class="x" id="rx">✕</a>`;
      }
      const rx = this.shadowRoot.getElementById("rx");
      if (rx) rx.addEventListener("click", () => { reply.hidden = true; this._cid = null; });
    }

    _h(title, pill, tight) {
      return `<div class="h${tight ? " tight" : ""}"><span class="t">${title}</span><i></i>${pill ? `<em>${pill}</em>` : ""}</div>`;
    }
  }

  const CSS = `
:host{display:block;min-height:100vh;color:#dff6fa;font-family:var(--ha-font-family-body, Roboto, sans-serif);
  background:radial-gradient(ellipse at 20% 0%, rgba(0,255,255,0.07), transparent 50%), radial-gradient(ellipse at 90% 100%, rgba(0,140,255,0.06), transparent 50%), #070b16}
*{box-sizing:border-box}
button{font:inherit;color:inherit;background:none;border:none;padding:0;cursor:pointer}
.top{display:flex;align-items:center;gap:10px;height:56px;padding:0 16px;border-bottom:1px solid rgba(0,255,255,.12);background:rgba(7,11,22,.85);position:sticky;top:0;z-index:2}
.top .ttl{flex:1;font-size:14px;font-weight:600;letter-spacing:.42em;color:#00fff7;text-shadow:0 0 8px rgba(0,255,247,.5)}
.brief{display:flex;align-items:center;gap:6px;font-size:10px;font-weight:700;letter-spacing:.16em;padding:6px 12px;border-radius:12px;color:#7fefff;border:1px solid rgba(0,255,247,.5);background:rgba(0,255,247,.08);box-shadow:0 0 10px rgba(0,255,247,.18)}
.brief ha-icon{--mdc-icon-size:14px;color:#00fff7} .brief.busy{opacity:.7}
.top .ttl{flex:none}
.ask{flex:1;display:flex;align-items:center;gap:8px;max-width:640px;margin:0 auto;padding:6px 12px;border-radius:14px;border:1px solid rgba(0,255,247,.28);background:rgba(0,255,247,.05)}
.ask ha-icon,.add ha-icon{--mdc-icon-size:16px;color:#00fff7;flex:none}
.ask input,.add input{flex:1;min-width:0;background:none;border:none;outline:none;color:#e6f5fa;font:inherit;font-size:13px}
.ask input::placeholder,.add input::placeholder{color:rgba(150,220,235,.45)}
.cog{width:34px;height:34px;display:flex;align-items:center;justify-content:center;border-radius:50%;color:#7fefff}
.cog:hover{background:rgba(0,255,247,.1)} .cog ha-icon{--mdc-icon-size:18px}
.reply{display:flex;align-items:flex-start;gap:12px;max-width:1564px;margin:12px auto 0;padding:12px 16px;border-radius:16px;border:1px solid rgba(0,255,247,.3);background:rgba(0,255,247,.06);box-shadow:0 0 14px rgba(0,255,247,.12)}
.reply[hidden]{display:none}
.reply .q{flex:none;max-width:35%;font-size:11px;font-weight:700;letter-spacing:.08em;color:rgba(150,220,235,.7)}
.reply .a{flex:1;font-size:14px;line-height:1.45;color:#e6f5fa} .reply .a.thinking{color:#00fff7;letter-spacing:.2em;font-size:11px;font-weight:700}
.reply .a.err{color:#ff8fa3}
.add{display:flex;align-items:center;gap:8px;margin-top:8px;padding:7px 10px;border-radius:10px;border:1px dashed rgba(0,255,247,.3)}
.page{max-width:1600px;margin:0 auto;padding:18px}
.card{position:relative;border-radius:20px;padding:14px 16px;overflow:hidden;
  background:radial-gradient(circle at 20% 0%, rgba(0,255,255,0.09), transparent 45%),linear-gradient(160deg, rgba(10,15,25,0.96), rgba(18,26,50,0.92));
  border:1px solid rgba(0,255,255,0.28);box-shadow:0 0 14px rgba(0,255,255,0.14), inset 0 0 22px rgba(0,255,255,0.05)}
.head{display:flex;align-items:center;gap:22px;min-height:200px}
.head img{width:170px;height:170px;border-radius:18px;box-shadow:0 0 24px rgba(0,255,247,.35);flex:none;object-fit:cover}
.m{flex:1;min-width:0}
.ti{font-size:30px;font-weight:300;letter-spacing:.45em;color:#dff6fa;text-shadow:0 0 14px rgba(0,255,247,.6)}
.sub{margin-top:4px;font-size:10px;font-weight:700;letter-spacing:.22em;color:rgba(150,220,235,.75)}
.st{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}
.s{flex:1;min-width:110px;padding:9px 10px;border-radius:12px;border:1px solid rgba(0,255,247,.22);background:rgba(0,255,247,.06)}
.s .n{font-size:22px;color:#fff;text-shadow:0 0 8px rgba(0,255,247,.5)} .s .l{font-size:8.5px;font-weight:700;letter-spacing:.16em;color:rgba(150,220,235,.7);margin-top:2px}
.rm{width:300px;flex:none} .rm .t{font-size:10px;font-weight:700;letter-spacing:.24em;color:#00fff7;margin-bottom:8px}
.b{display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:9px;font-weight:700;letter-spacing:.1em;color:rgba(180,220,235,.8)}
.b span{width:92px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis} .b div{flex:1;height:6px;border-radius:3px;background:rgba(0,255,247,.1);overflow:hidden}
.b i{display:block;height:100%;border-radius:3px;background:linear-gradient(90deg,#00a8ff,#00fff7);box-shadow:0 0 6px rgba(0,255,247,.6)}
.cols{display:grid;grid-template-columns:repeat(3, minmax(0, 1fr));gap:16px;margin-top:16px;align-items:start}
.stack{display:flex;flex-direction:column;gap:10px}
.h{display:flex;align-items:center;gap:10px;margin-bottom:10px} .h.tight{margin:0}
.h .t{font-size:12px;font-weight:700;letter-spacing:.32em;color:#00fff7;text-shadow:0 0 6px rgba(0,255,247,.5)}
.h i{flex:1;height:1px;background:linear-gradient(90deg,rgba(0,255,247,.45),transparent)}
.h em{font-style:normal;font-size:9px;font-weight:700;letter-spacing:.14em;padding:3px 9px;border-radius:10px;color:#7fefff;border:1px solid rgba(0,255,247,.45);background:rgba(0,255,247,.08);white-space:nowrap}
.r{display:flex;align-items:center;gap:10px;padding:7px 10px;margin-bottom:5px;border-radius:10px;border:1px solid rgba(0,255,247,.10);background:rgba(0,255,247,.035)}
.r ha-icon{--mdc-icon-size:16px;color:#00fff7;flex:none} .r.dim{opacity:.62} .r.dim ha-icon{color:#5aa9b8}
.r b{font-weight:400;font-size:12.5px;color:rgba(230,245,250,.93);flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.r b.wrap{white-space:normal}
.r s{text-decoration:none;flex:none;font-size:8.5px;font-weight:700;letter-spacing:.12em;padding:2px 7px;border-radius:8px;color:#7fefff;border:1px solid rgba(0,255,247,.35)}
.r u{text-decoration:none;flex:none;font-size:9px;font-weight:700;letter-spacing:.08em;color:rgba(150,220,235,.7)}
.x{flex:none;width:22px;height:22px;display:flex;align-items:center;justify-content:center;border-radius:50%;font-size:11px;color:rgba(150,220,235,.5);cursor:pointer}
.x:hover{color:#ff7088;background:rgba(255,80,110,.12)}
.rt{cursor:pointer;padding:10px 12px} .rt .r{margin:0} .rt:active{transform:scale(.99)}
.rt.on{border-color:rgba(61,255,168,.55)} .rt.on s{color:#3dffa8;border-color:#3dffa8}
.rt.off s{color:rgba(255,120,140,.85);border-color:rgba(255,120,140,.6)}
.hint{font-size:8.5px;letter-spacing:.14em;color:rgba(150,220,235,.55);margin:6px 2px 0}
.e{padding:18px;text-align:center;font-size:11px;letter-spacing:.2em;color:rgba(150,200,215,.45)}
@media (max-width: 1100px){ .cols{grid-template-columns:1fr 1fr} .rm{display:none} }
@media (max-width: 900px){ .ask{display:none} }
@media (max-width: 720px){ .cols{grid-template-columns:1fr} .head{flex-direction:column;text-align:center} .head img{width:120px;height:120px} .ti{font-size:22px} .st{justify-content:center} }
`;

  customElements.define("sentient-panel", SentientPanel);
})();
