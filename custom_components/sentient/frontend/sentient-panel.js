/* Sentient sidebar panel — ships with the integration, not editable from the dashboard editor.
   Finds Sentient's own sensors through the entity registry (platform "sentient"), so it works in any house.
   Two views: Overview (what the house knows and is doing) and Settings (memories plus every option,
   drawn with Home Assistant's own form from the schema the integration sends). */
(() => {
  if (customElements.get("sentient-panel")) return;

  const KEYS = ["memories", "last_spoken", "likely_room", "watches", "routines", "visitors", "health"];
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const hm = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }); };
  const day = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleDateString("en-GB", { day: "numeric", month: "short" }); };
  const left = (iso, fallback) => {
    const d = iso ? new Date(iso) : null;
    const m = d && !isNaN(d) ? Math.max(0, Math.round((d - Date.now()) / 60000)) : fallback;
    if (m == null) return "LIVE";
    return m >= 60 ? `${Math.floor(m / 60)}H ${m % 60}M` : `${m} MIN`;
  };
  const CAT_ICON = { fact: "mdi:information-outline", preference: "mdi:heart-outline", person: "mdi:account-outline", routine: "mdi:calendar-clock", note: "mdi:note-text-outline" };

  // ha-form is lazy-loaded by the frontend; opening a card editor pulls it (and the selectors) in.
  let formReady;
  const loadForm = () => formReady || (formReady = (async () => {
    if (customElements.get("ha-form")) return true;
    try {
      const helpers = await window.loadCardHelpers();
      for (const type of ["tile", "entities"]) {
        if (customElements.get("ha-form")) break;
        const card = await helpers.createCardElement({ type, entity: "sun.sun", entities: [] });
        if (card && card.constructor.getConfigElement) await card.constructor.getConfigElement();
      }
    } catch (e) { /* fall through to the wait */ }
    await Promise.race([customElements.whenDefined("ha-form"), new Promise((r) => setTimeout(r, 5000))]);
    return !!customElements.get("ha-form");
  })());

  class SentientPanel extends HTMLElement {
    set hass(hass) {
      this._hass = hass;
      if (!this.shadowRoot) this._build();
      if (this._form) this._form.hass = hass;
      const ids = this._ids();
      const sig = KEYS.map((k) => { const s = ids[k] && hass.states[ids[k]]; return s ? s.last_updated : ""; }).join("|");
      if (sig !== this._sig) {
        const memChanged = ids.memories && hass.states[ids.memories] && hass.states[ids.memories].last_updated !== this._memSig;
        this._sig = sig;
        this._render();
        if (memChanged) { this._memSig = hass.states[ids.memories].last_updated; if (this._view === "settings") this._loadMemories(); }
      }
    }
    set narrow(n) { this._narrow = n; if (this._menu) this._menu.narrow = n; }
    set panel(p) { this._panel = p; }
    set route(r) {
      this._route = r;
      const want = r && String(r.path || "").startsWith("/settings") ? "settings" : "overview";
      if (this.shadowRoot && want !== this._view) this._show(want, false);
      else if (!this.shadowRoot) this._view = want;
    }

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
      if (confirmText && !window.confirm(confirmText)) return Promise.resolve();
      return this._hass.callService("sentient", service, data).catch((err) => window.alert(err.message || String(err)));
    }
    _$(id) { return this.shadowRoot.getElementById(id); }

    _build() {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.innerHTML = `<style>${CSS}</style>
        <div class="top"><span id="menu"></span><span class="ttl" id="ttl">SENTIENT</span>
          <nav class="tabs"><button data-view="overview" class="on"><ha-icon icon="mdi:view-dashboard-outline"></ha-icon><span>OVERVIEW</span></button>
            <button data-view="settings"><ha-icon icon="mdi:cog-outline"></ha-icon><span>SETTINGS</span></button></nav>
          <form class="ask" id="askf"><ha-icon icon="mdi:creation-outline"></ha-icon><input id="ask" placeholder="Ask anything…" autocomplete="off"></form>
          <button class="brief" id="brief"><ha-icon icon="mdi:text-box-outline"></ha-icon><span>BRIEFING</span></button></div>
        <div class="reply" id="reply" hidden></div>
        <div class="page" id="ov"><div id="head" class="card head"></div>
          <div class="cols"><div class="col stack"><div id="health" class="card"></div><div id="watch" class="card"></div></div>
            <div id="rout" class="col stack"></div>
            <div class="col stack"><div id="visit" class="card"></div><div id="said" class="card"></div></div></div></div>
        <div class="page set" id="set" hidden><nav class="card side" id="side"></nav><div class="card body" id="body"></div></div>`;
      const menu = document.createElement("ha-menu-button");
      menu.hass = this._hass; menu.narrow = this._narrow;
      this._$("menu").appendChild(menu);
      this._menu = menu;
      const brief = this._$("brief");
      brief.addEventListener("click", () => {
        if (brief.classList.contains("busy")) return;
        brief.classList.add("busy"); brief.querySelector("span").textContent = "PREPARING";
        const done = () => { brief.classList.remove("busy"); brief.querySelector("span").textContent = "BRIEFING"; };
        this._hass.callService("sentient", "briefing", { priority: "urgent" }).then(() => setTimeout(done, 4000), (e) => { done(); window.alert(e.message || e); });
      });
      this.shadowRoot.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => this._show(b.dataset.view, true)));
      this._$("askf").addEventListener("submit", (ev) => { ev.preventDefault(); this._ask(); });
      this.shadowRoot.addEventListener("click", (ev) => this._click(ev));
      this.shadowRoot.addEventListener("submit", (ev) => this._submit(ev));
      this.shadowRoot.addEventListener("input", (ev) => { if (ev.target.id === "mfind") { this._find = ev.target.value; this._drawMemories(); } });
      this.shadowRoot.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape" && this._editing) { this._editing = null; this._drawMemories(); }
      });
      this._group = "memories";
      this._show(this._view || "overview", false);
    }

    _show(view, push) {
      this._view = view;
      this.shadowRoot.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.view === view));
      this._$("ov").hidden = view !== "overview";
      this._$("set").hidden = view !== "settings";
      if (push && this._route) {
        const base = this._route.prefix || "/sentient";
        history.replaceState(history.state, "", view === "settings" ? `${base}/settings` : base);
      }
      if (view === "settings") this._openSettings();
      else this._render();
    }

    // ---------- overview ----------
    _render() {
      if (!this._hass || !this.shadowRoot) return;
      if (this._menu) this._menu.hass = this._hass;
      const R = (id) => this._$(id);
      const room = this._st("likely_room");
      const persona = this._attr("likely_room", "persona", "Sentient");
      R("ttl").textContent = String(persona).toUpperCase();
      if (this._view === "settings") return;

      if (!room) {
        R("head").innerHTML = `<div class="e">SENTIENT IS STARTING — OR ISN'T SET UP YET</div>`;
        return;
      }
      const scores = this._attr("likely_room", "scores", {});
      const top = Math.max(1, ...Object.values(scores));
      const bars = Object.entries(scores).slice(0, 5).map(([k, v]) =>
        `<div class="b"><span>${esc(k).toUpperCase()}</span><div><i style="width:${Math.round((v / top) * 100)}%"></i></div></div>`).join("");
      const stat = (n, l, go) => `<div class="s"${go ? ` data-act="goto" data-id="${go}"` : ""}><div class="n">${esc(n)}</div><div class="l">${l}</div></div>`;
      const st = (k) => (this._st(k) ? this._st(k).state : "—");
      R("head").innerHTML = `<img src="/sentient_static/logo.png" alt="">
        <div class="m"><div class="ti">${esc(persona).toUpperCase()}</div>
          <div class="sub">${this._attr("likely_room", "quiet_hours") ? "QUIET HOURS" : "AWAKE"} · PEOPLE IN ${esc(room.state).toUpperCase()} · ${this._attr("likely_room", "ai_checks_today", 0)} AI CHECKS TODAY</div>
          <div class="st">${stat(st("memories"), "MEMORIES ›", "memories")}${stat(st("watches"), "WATCHING")}${stat(st("routines"), "ROUTINES")}${stat(st("visitors"), "VISITORS TODAY")}${stat(st("health"), "PROBLEMS")}</div></div>
        <div class="rm"><div class="t">WHERE PEOPLE ARE</div>${bars || '<div class="b">NO RECENT MOTION</div>'}</div>`;

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

      // house health
      const issues = this._attr("health", "issues", []), updates = this._attr("health", "updates", []);
      const healthRows = issues.slice(0, 6).map((i) => i.kind === "offline"
        ? `<div class="r bad"><ha-icon icon="mdi:lan-disconnect"></ha-icon><b>${esc(i.name)}</b><u>OFFLINE${i.since ? " · " + (Date.now() - new Date(i.since) > 864e5 ? day(i.since) : hm(i.since)) : ""}</u></div>`
        : `<div class="r warn"><ha-icon icon="mdi:battery-alert-variant-outline"></ha-icon><b>${esc(i.name)}</b><u>${i.level == null ? "LOW" : i.level + "%"}</u></div>`).join("")
        + (updates.length ? `<div class="r"><ha-icon icon="mdi:package-up"></ha-icon><b>${updates.length} update${updates.length > 1 ? "s" : ""} waiting · ${esc(updates.slice(0, 3).join(", "))}</b></div>` : "");
      R("health").innerHTML = this._h("HOUSE HEALTH", issues.length ? `${issues.length} PROBLEM${issues.length > 1 ? "S" : ""}` : "ALL GOOD")
        + (healthRows || '<div class="e">EVERYTHING ONLINE · BATTERIES FINE</div>');

      // visitors
      const visits = this._attr("visitors", "recent", []);
      R("visit").innerHTML = this._h("VISITORS", `${st("visitors")} TODAY`) + (visits.slice(0, 5).map((v) =>
        `<div class="r"><ha-icon icon="${v.kind === "delivery" ? "mdi:package-variant-closed" : "mdi:doorbell-video"}"></ha-icon>
          <b>${v.name ? esc(v.name) + " — " : ""}${esc(v.description)}${v.parcel_left ? " · PARCEL LEFT" : ""}</b><u>${hm(v.time)}</u></div>`).join("")
        || '<div class="e">NO ONE AT THE DOOR YET</div>');

      // said & noticed
      const log = this._attr("last_spoken", "log", []).slice(0, 6).map((l) => l.said
        ? `<div class="r"><ha-icon icon="mdi:account-voice"></ha-icon><b>${esc(l.said)}</b><u>${hm(l.time)}</u></div>`
        : `<div class="r dim"><ha-icon icon="mdi:volume-off"></ha-icon><b>${esc(l.held)}</b><u>${esc(String(l.reason || "").split(":")[0].replace("_", " ")).toUpperCase()}</u></div>`).join("");
      const noticed = this._attr("last_spoken", "noticed", []).slice(0, 3).map((d) =>
        `<div class="r dim"><ha-icon icon="mdi:radar"></ha-icon><b>${esc((d.changes || []).join("; "))}</b><u>${esc(d.result).toUpperCase()}</u></div>`).join("");
      R("said").innerHTML = this._h("SAID &amp; NOTICED", "") + (log + noticed || '<div class="e">QUIET SO FAR</div>');
    }

    // ---------- settings ----------
    async _openSettings() {
      this._drawSide();
      this._loadMemories();
      if (!this._settings && !this._settingsErr) {
        try {
          this._settings = await this._hass.callWS({ type: "sentient/settings", language: this._hass.language });
        } catch (e) {
          this._settingsErr = e.code === "unauthorized" ? "Only an administrator can change settings." : (e.message || String(e));
        }
        this._drawSide();
      }
      this._drawBody();
    }

    _drawSide() {
      const groups = (this._settings && this._settings.groups) || [];
      const item = (id, icon, title, sub) =>
        `<button class="nav${this._group === id ? " on" : ""}" data-act="group" data-id="${id}"><ha-icon icon="${icon}"></ha-icon><span><b>${esc(title)}</b>${sub ? `<i>${esc(sub)}</i>` : ""}</span></button>`;
      const count = this._memories ? this._memories.length : this._st("memories") ? this._st("memories").state : "";
      this._$("side").innerHTML = `<div class="sh">SETTINGS</div>` + item("memories", "mdi:brain", "Memories", count !== "" ? `${count} remembered` : "")
        + groups.map((g) => item(g.id, g.icon, g.title, g.blurb)).join("")
        + (this._settingsErr ? `<div class="note">${esc(this._settingsErr)}</div>` : "");
    }

    async _loadMemories() {
      try {
        const res = await this._hass.callWS({ type: "sentient/memories" });
        this._memories = res.items; this._cats = res.categories; this._admin = res.admin;
      } catch (e) {
        this._memories = this._attr("memories", "items", []);
        this._cats = ["fact", "preference", "person", "routine", "note"];
      }
      if (this._view === "settings") { this._drawSide(); if (this._group === "memories") this._drawMemories(); }
    }

    _drawBody() {
      if (this._group === "memories") { this._form = null; this._drawMemories(); return; }
      const g = this._settings && this._settings.groups.find((x) => x.id === this._group);
      if (!g) { this._group = "memories"; this._drawMemories(); return; }
      const body = this._$("body");
      body.innerHTML = `<div class="bh"><ha-icon icon="${g.icon}"></ha-icon><div><div class="bt">${esc(g.title).toUpperCase()}</div><div class="bs">${esc(g.blurb)}</div></div></div>
        <div id="formwrap" class="formwrap"><div class="e">LOADING…</div></div>
        <div class="bar"><span id="status"></span><button class="btn ghost" data-act="reset">UNDO CHANGES</button><button class="btn" data-act="save">SAVE</button></div>`;
      this._draft = { ...this._settings.data };
      this._dirty = false;
      this._status("");
      loadForm().then((ok) => {
        if (this._group !== g.id) return;
        const wrap = this._$("formwrap");
        if (!ok) {
          wrap.innerHTML = `<div class="e">THE SETTINGS FORM COULDN'T LOAD · REFRESH THE PAGE AND TRY AGAIN</div>`;
          return;
        }
        const S = this._settings;
        const form = document.createElement("ha-form");
        form.hass = this._hass;
        form.schema = g.schema;
        form.data = this._draft;
        form.computeLabel = (s) => S.labels[s.name] || s.name;
        form.computeHelper = (s) => S.helpers[s.name] || "";
        form.localizeValue = (key) => S.selector[key] || key.split(".").pop();
        form.addEventListener("value-changed", (ev) => {
          this._draft = { ...ev.detail.value };
          form.data = this._draft;
          this._dirty = true;
          this._status("UNSAVED CHANGES", "warn");
        });
        wrap.innerHTML = "";
        wrap.appendChild(form);
        this._form = form;
      });
    }

    _status(text, kind) {
      const el = this._$("status");
      if (el) { el.textContent = text; el.className = kind || ""; }
    }

    async _save() {
      const g = this._settings.groups.find((x) => x.id === this._group);
      if (!g) return;
      const names = g.schema.map((s) => s.name);
      const data = {};
      for (const n of names) if (this._draft[n] !== undefined && this._draft[n] !== null && this._draft[n] !== "") data[n] = this._draft[n];
      this._status("SAVING…");
      try {
        const res = await this._hass.callWS({ type: "sentient/settings/save", group: g.id, data });
        this._settings.data = res.data;
        this._draft = { ...res.data };
        if (this._form) this._form.data = this._draft;
        this._dirty = false;
        this._status("SAVED ✓", "ok");
      } catch (e) {
        this._status(e.message || String(e), "bad");
      }
    }

    _drawMemories() {
      if (this._view !== "settings" || this._group !== "memories") return;
      const body = this._$("body");
      const items = this._memories || [];
      const q = (this._find || "").toLowerCase().trim();
      const shown = q ? items.filter((m) => m.text.toLowerCase().includes(q) || m.category.includes(q)) : items;
      const cats = this._cats || [];
      const opts = (sel) => cats.map((c) => `<option value="${c}"${c === sel ? " selected" : ""}>${c[0].toUpperCase() + c.slice(1)}</option>`).join("");
      const byCat = {};
      items.forEach((m) => { byCat[m.category] = (byCat[m.category] || 0) + 1; });
      const keepFocus = this.shadowRoot.activeElement && this.shadowRoot.activeElement.id === "mfind";
      const rows = shown.map((m) => this._editing === m.id
        ? `<form class="r edit" data-form="edit" data-id="${esc(m.id)}"><ha-icon icon="${CAT_ICON[m.category] || "mdi:brain"}"></ha-icon>
            <input name="text" value="${esc(m.text)}" autocomplete="off"><select name="category">${opts(m.category)}</select>
            <button class="btn sm" type="submit">SAVE</button><a class="x" data-act="noedit" title="Cancel">✕</a></form>`
        : `<div class="r mem"><ha-icon icon="${CAT_ICON[m.category] || "mdi:brain"}"></ha-icon><b class="wrap" data-act="edit" data-id="${esc(m.id)}" title="Tap to edit">${esc(m.text)}</b>
            <s>${esc(m.category).toUpperCase()}</s><u>${day(m.created)}${m.hits ? ` · USED ${m.hits}×` : ""}</u>
            <a class="x" data-act="edit" data-id="${esc(m.id)}" title="Edit">✎</a><a class="x" data-act="forget" data-id="${esc(m.id)}" data-text="${esc(m.text)}" title="Forget">✕</a></div>`).join("");
      body.innerHTML = `<div class="bh"><ha-icon icon="mdi:brain"></ha-icon><div><div class="bt">MEMORIES</div>
          <div class="bs">What the house knows and shares with every voice assistant. Tap one to edit it.</div></div>
          <div class="chips">${Object.entries(byCat).map(([c, n]) => `<span>${esc(c).toUpperCase()} ${n}</span>`).join("")}</div></div>
        <form class="add" data-form="add"><ha-icon icon="mdi:plus"></ha-icon><input name="text" placeholder="Add a memory, e.g. “The bins go out on Thursday night”" autocomplete="off">
          <select name="category">${opts("fact")}</select><button class="btn sm" type="submit">ADD</button></form>
        <div class="find"><ha-icon icon="mdi:magnify"></ha-icon><input id="mfind" placeholder="Search ${items.length} memories…" value="${esc(this._find || "")}" autocomplete="off"></div>
        <div class="list">${rows || `<div class="e">${q ? "NOTHING MATCHES" : "NOTHING REMEMBERED YET · SAY “REMEMBER THAT…”"}</div>`}</div>`;
      if (keepFocus) { const f = this._$("mfind"); f.focus(); f.setSelectionRange(f.value.length, f.value.length); }
      if (this._editing) { const i = body.querySelector("form.edit input"); if (i) i.focus(); }
    }

    // ---------- events ----------
    _click(ev) {
      const el = ev.target.closest("[data-act]");
      if (!el) return;
      const { act, id, text } = el.dataset;
      if (act === "forget") this._call("forget", { what: id }, `Forget: ${text}?`);
      else if (act === "cancel") this._call("cancel_watch", { what: id }, `Stop: ${text}?`);
      else if (act === "adopt") this._call("adopt_routine", { routine: id }, text);
      else if (act === "goto") { this._group = id; this._show("settings", true); }
      else if (act === "group") {
        if (this._dirty && !window.confirm("Leave without saving your changes?")) return;
        this._dirty = false; this._group = id; this._editing = null; this._drawSide(); this._drawBody();
      } else if (act === "save") this._save();
      else if (act === "reset") this._drawBody();
      else if (act === "edit") { this._editing = id; this._drawMemories(); }
      else if (act === "noedit") { this._editing = null; this._drawMemories(); }
    }

    async _submit(ev) {
      const form = ev.target;
      if (!form.dataset || !form.dataset.form) return;
      ev.preventDefault();
      const text = form.elements.text.value.trim(), category = form.elements.category.value;
      if (!text) return;
      try {
        if (form.dataset.form === "add") {
          await this._hass.callService("sentient", "remember", { text, category });
          form.elements.text.value = "";
        } else {
          await this._hass.callWS({ type: "sentient/memory/update", memory_id: form.dataset.id, text, category });
          this._editing = null;
        }
        this._loadMemories();
      } catch (e) { window.alert(e.message || String(e)); }
    }

    async _agent() {
      const chosen = this._attr("likely_room", "chat_agent");
      if (chosen) return chosen;
      if (this._agentId !== undefined) return this._agentId;
      try {
        const res = await this._hass.callWS({ type: "assist_pipeline/pipeline/list" });
        const pipe = (res.pipelines || []).find((p) => p.id === res.preferred_pipeline);
        this._agentId = pipe ? pipe.conversation_engine : null;
      } catch (e) { this._agentId = null; }
      return this._agentId;
    }

    async _ask() {
      const input = this._$("ask"), reply = this._$("reply");
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
      const rx = this._$("rx");
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
[hidden]{display:none !important}
button{font:inherit;color:inherit;background:none;border:none;padding:0;cursor:pointer}
.top{display:flex;align-items:center;gap:10px;height:56px;padding:0 16px;border-bottom:1px solid rgba(0,255,255,.12);background:rgba(7,11,22,.85);position:sticky;top:0;z-index:2}
.top .ttl{flex:none;font-size:14px;font-weight:600;letter-spacing:.42em;color:#00fff7;text-shadow:0 0 8px rgba(0,255,247,.5)}
.tabs{display:flex;gap:4px;margin-left:8px;padding:3px;border-radius:14px;border:1px solid rgba(0,255,247,.18);background:rgba(0,255,247,.03)}
.tabs button{display:flex;align-items:center;gap:6px;padding:5px 12px;border-radius:11px;font-size:10px;font-weight:700;letter-spacing:.16em;color:rgba(150,220,235,.7)}
.tabs button ha-icon{--mdc-icon-size:15px}
.tabs button.on{color:#00fff7;background:rgba(0,255,247,.12);box-shadow:0 0 10px rgba(0,255,247,.2) inset}
.brief{display:flex;align-items:center;gap:6px;font-size:10px;font-weight:700;letter-spacing:.16em;padding:6px 12px;border-radius:12px;color:#7fefff;border:1px solid rgba(0,255,247,.5);background:rgba(0,255,247,.08);box-shadow:0 0 10px rgba(0,255,247,.18)}
.brief ha-icon{--mdc-icon-size:14px;color:#00fff7} .brief.busy{opacity:.7}
.ask{flex:1;display:flex;align-items:center;gap:8px;max-width:640px;margin:0 auto;padding:6px 12px;border-radius:14px;border:1px solid rgba(0,255,247,.28);background:rgba(0,255,247,.05)}
.ask ha-icon,.add ha-icon,.find ha-icon{--mdc-icon-size:16px;color:#00fff7;flex:none}
input,select{font:inherit;font-size:13px;color:#e6f5fa}
.ask input,.add input,.find input{flex:1;min-width:0;background:none;border:none;outline:none}
input::placeholder{color:rgba(150,220,235,.45)}
select{background:#0d1626;border:1px solid rgba(0,255,247,.3);border-radius:8px;padding:5px 8px;outline:none}
.reply{display:flex;align-items:flex-start;gap:12px;max-width:1564px;margin:12px auto 0;padding:12px 16px;border-radius:16px;border:1px solid rgba(0,255,247,.3);background:rgba(0,255,247,.06);box-shadow:0 0 14px rgba(0,255,247,.12)}
.reply .q{flex:none;max-width:35%;font-size:11px;font-weight:700;letter-spacing:.08em;color:rgba(150,220,235,.7)}
.reply .a{flex:1;font-size:14px;line-height:1.45;color:#e6f5fa} .reply .a.thinking{color:#00fff7;letter-spacing:.2em;font-size:11px;font-weight:700}
.reply .a.err{color:#ff8fa3}
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
.s[data-act]{cursor:pointer} .s[data-act]:hover{border-color:rgba(0,255,247,.55)}
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
.r.bad{border-color:rgba(255,90,120,.35);background:rgba(255,80,110,.06)} .r.bad ha-icon,.r.bad u{color:#ff7088}
.r.warn{border-color:rgba(255,190,85,.35);background:rgba(255,180,60,.05)} .r.warn ha-icon,.r.warn u{color:#ffbe55}
.r b{font-weight:400;font-size:12.5px;color:rgba(230,245,250,.93);flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.r b.wrap{white-space:normal}
.r s{text-decoration:none;flex:none;font-size:8.5px;font-weight:700;letter-spacing:.12em;padding:2px 7px;border-radius:8px;color:#7fefff;border:1px solid rgba(0,255,247,.35)}
.r u{text-decoration:none;flex:none;font-size:9px;font-weight:700;letter-spacing:.08em;color:rgba(150,220,235,.7)}
.x{flex:none;width:24px;height:24px;display:flex;align-items:center;justify-content:center;border-radius:50%;font-size:12px;color:rgba(150,220,235,.5);cursor:pointer}
.x:hover{color:#ff7088;background:rgba(255,80,110,.12)}
.rt{cursor:pointer;padding:10px 12px} .rt .r{margin:0} .rt:active{transform:scale(.99)}
.rt.on{border-color:rgba(61,255,168,.55)} .rt.on s{color:#3dffa8;border-color:#3dffa8}
.rt.off s{color:rgba(255,120,140,.85);border-color:rgba(255,120,140,.6)}
.hint{font-size:8.5px;letter-spacing:.14em;color:rgba(150,220,235,.55);margin:6px 2px 0}
.e{padding:18px;text-align:center;font-size:11px;letter-spacing:.2em;color:rgba(150,200,215,.45)}
/* settings */
.set{display:grid;grid-template-columns:300px minmax(0,1fr);gap:16px;align-items:start}
.side{position:sticky;top:74px;padding:12px;display:flex;flex-direction:column;gap:4px}
.sh{font-size:11px;font-weight:700;letter-spacing:.32em;color:#00fff7;padding:4px 10px 10px;text-shadow:0 0 6px rgba(0,255,247,.5)}
.nav{display:flex;align-items:center;gap:12px;text-align:left;padding:10px 12px;border-radius:12px;border:1px solid transparent}
.nav ha-icon{--mdc-icon-size:20px;color:#5fd8e6;flex:none}
.nav span{display:flex;flex-direction:column;min-width:0} .nav b{font-weight:500;font-size:13.5px;color:#e6f5fa}
.nav i{font-style:normal;font-size:10.5px;color:rgba(150,220,235,.6);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nav:hover{background:rgba(0,255,247,.05)}
.nav.on{border-color:rgba(0,255,247,.45);background:rgba(0,255,247,.09);box-shadow:0 0 12px rgba(0,255,247,.15)} .nav.on ha-icon{color:#00fff7}
.note{margin-top:8px;padding:10px;border-radius:10px;font-size:11px;color:#ffbe55;border:1px dashed rgba(255,190,85,.4)}
.body{padding:20px 22px;min-height:400px}
.bh{display:flex;align-items:center;gap:14px;margin-bottom:16px}
.bh>ha-icon{--mdc-icon-size:28px;color:#00fff7;filter:drop-shadow(0 0 6px rgba(0,255,247,.6))}
.bh>div:not(.chips){flex:1}
.bt{font-size:15px;font-weight:700;letter-spacing:.32em;color:#dff6fa} .bs{font-size:12px;color:rgba(150,220,235,.7);margin-top:3px}
.chips{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.chips span{font-size:8.5px;font-weight:700;letter-spacing:.12em;padding:3px 8px;border-radius:9px;color:#7fefff;border:1px solid rgba(0,255,247,.3)}
.add,.find{display:flex;align-items:center;gap:10px;margin-bottom:10px;padding:8px 12px;border-radius:12px;border:1px dashed rgba(0,255,247,.35);background:rgba(0,255,247,.03)}
.find{border-style:solid;border-color:rgba(0,255,247,.15)}
.list .r{padding:9px 10px}
.r.mem b{cursor:text} .r.mem b:hover{color:#fff}
.r.edit{border-color:rgba(0,255,247,.5);background:rgba(0,255,247,.08)}
.r.edit input{flex:1;min-width:0;background:rgba(0,0,0,.25);border:1px solid rgba(0,255,247,.3);border-radius:8px;padding:6px 8px;outline:none}
.btn{flex:none;font-size:10px;font-weight:700;letter-spacing:.16em;padding:8px 16px;border-radius:10px;color:#041016;background:linear-gradient(90deg,#00c8ff,#00fff7);box-shadow:0 0 12px rgba(0,255,247,.35)}
.btn.sm{padding:6px 12px} .btn.ghost{color:#7fefff;background:none;border:1px solid rgba(0,255,247,.35);box-shadow:none}
.bar{display:flex;align-items:center;justify-content:flex-end;gap:10px;margin-top:18px;padding-top:14px;border-top:1px solid rgba(0,255,247,.15);position:sticky;bottom:0}
#status{flex:1;font-size:10px;font-weight:700;letter-spacing:.16em;color:rgba(150,220,235,.6)}
#status.ok{color:#3dffa8} #status.warn{color:#ffbe55} #status.bad{color:#ff7088;letter-spacing:.02em;font-weight:400;font-size:12px}
.formwrap{--primary-color:#00fff7;--accent-color:#00fff7;--primary-text-color:#e6f5fa;--secondary-text-color:rgba(150,220,235,.75);
  --disabled-text-color:rgba(150,220,235,.4);--divider-color:rgba(0,255,247,.18);--card-background-color:#0c1424;--secondary-background-color:#0c1424;
  --input-fill-color:rgba(0,255,247,.04);--mdc-text-field-fill-color:rgba(0,255,247,.04);--mdc-select-fill-color:rgba(0,255,247,.04);
  --mdc-theme-primary:#00fff7;--mdc-theme-surface:#0c1424;--mdc-theme-text-primary-on-background:#e6f5fa;
  --mdc-text-field-ink-color:#e6f5fa;--mdc-text-field-label-ink-color:rgba(150,220,235,.75);--mdc-text-field-idle-line-color:rgba(0,255,247,.3);
  --mdc-select-ink-color:#e6f5fa;--mdc-select-label-ink-color:rgba(150,220,235,.75);--mdc-select-idle-line-color:rgba(0,255,247,.3);
  --input-ink-color:#e6f5fa;--input-label-ink-color:rgba(150,220,235,.75);--input-idle-line-color:rgba(0,255,247,.3);--input-dropdown-icon-color:#7fefff;
  --ha-card-background:#0c1424;--sidebar-text-color:#e6f5fa;--state-icon-color:#00fff7;--paper-item-icon-color:#00fff7;
  --switch-checked-color:#00fff7;--switch-checked-button-color:#00fff7;--switch-checked-track-color:#0aa;
  --wa-color-neutral-fill-quiet:rgba(0,255,247,.06);--wa-color-neutral-fill-normal:rgba(0,255,247,.12);--wa-color-neutral-fill-loud:#5fd8e6;
  --wa-color-neutral-border-quiet:rgba(0,255,247,.22);--wa-color-neutral-border-normal:rgba(0,255,247,.35);--wa-color-neutral-border-loud:rgba(0,255,247,.6);
  --wa-color-neutral-on-quiet:#e6f5fa;--wa-color-neutral-on-normal:#e6f5fa;--wa-color-neutral-on-loud:#041016;
  --wa-color-brand-fill-quiet:rgba(0,255,247,.10);--wa-color-brand-fill-normal:rgba(0,255,247,.25);--wa-color-brand-fill-loud:#00fff7;
  --wa-color-brand-border-quiet:rgba(0,255,247,.35);--wa-color-brand-border-normal:#00c8d8;--wa-color-brand-border-loud:#00fff7;
  --wa-color-brand-on-quiet:#7fefff;--wa-color-brand-on-normal:#e6f5fa;--wa-color-brand-on-loud:#041016;
  --wa-color-text-quiet:rgba(150,220,235,.75);--wa-color-text-normal:#e6f5fa;--wa-color-surface-default:#0c1424;--wa-color-surface-raised:#101a2c;
  --wa-color-surface-border:rgba(0,255,247,.22);--wa-form-control-background-color:rgba(0,255,247,.04);--wa-form-control-border-color:rgba(0,255,247,.25);
  --wa-form-control-value-color:#e6f5fa;--wa-form-control-placeholder-color:rgba(150,220,235,.45);--wa-form-control-label-color:rgba(150,220,235,.8);
  --wa-form-control-hint-color:rgba(150,220,235,.6);
  --ha-color-fill-neutral-quiet-resting:rgba(0,255,247,.05);--ha-color-fill-neutral-quiet-hover:rgba(0,255,247,.09);--ha-color-fill-neutral-normal-resting:rgba(0,255,247,.10);
  --ha-color-fill-primary-quiet-resting:rgba(0,255,247,.10);--ha-color-fill-primary-normal-resting:rgba(0,255,247,.2);--ha-color-fill-primary-loud-resting:#00c8d8;
  --ha-color-on-primary-quiet:#7fefff;--ha-color-on-primary-normal:#e6f5fa;--ha-color-on-primary-loud:#041016;
  --ha-color-border-neutral-quiet:rgba(0,255,247,.22);--ha-color-border-neutral-normal:rgba(0,255,247,.35);
  --ha-color-text-primary:#e6f5fa;--ha-color-text-secondary:rgba(150,220,235,.75);--ha-color-form-background:rgba(0,255,247,.04);
  --ha-color-surface-default:#0c1424;--ha-color-on-neutral-quiet:#e6f5fa;--ha-color-on-neutral-normal:#e6f5fa;color-scheme:dark;
  color:#e6f5fa}
.formwrap ha-form{display:block;max-width:880px}
@media (max-width: 1100px){ .cols{grid-template-columns:1fr 1fr} .rm{display:none} .set{grid-template-columns:230px minmax(0,1fr)} .nav i{display:none} }
@media (max-width: 900px){ .ask{display:none} }
@media (max-width: 720px){ .cols{grid-template-columns:1fr} .head{flex-direction:column;text-align:center} .head img{width:120px;height:120px} .ti{font-size:22px} .st{justify-content:center}
  .set{grid-template-columns:1fr} .side{position:static;flex-direction:row;overflow-x:auto} .sh{display:none} .nav span{display:none} .tabs span{display:none} }
`;

  customElements.define("sentient-panel", SentientPanel);
})();
