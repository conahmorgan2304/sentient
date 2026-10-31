/* Sentient sidebar panel — ships with the integration, not editable from the dashboard editor.
   Finds Sentient's own sensors through the entity registry (platform "sentient"), so it works in any house.
   Two views: Overview (what the house knows and is doing) and Settings (memories plus every option,
   drawn with Home Assistant's own form from the schema the integration sends). */
(() => {
  if (customElements.get("sentient-panel")) return;

  const KEYS = ["memories", "last_spoken", "likely_room", "watches", "routines", "visitors", "health", "hush", "guest_mode", "chime", "party_mode", "mind", "media_speakers", "house"];
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const hm = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }); };
  const day = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleDateString("en-GB", { day: "numeric", month: "short" }); };
  const left = (iso, fallback) => {
    const d = iso ? new Date(iso) : null;
    const m = d && !isNaN(d) ? Math.max(0, Math.round((d - Date.now()) / 60000)) : fallback;
    if (m == null) return "LIVE";
    return m >= 60 ? `${Math.floor(m / 60)}H ${m % 60}M` : `${m} MIN`;
  };
  const CAT_ICON = { fact: "mdi:information-outline", preference: "mdi:heart-outline", person: "mdi:account-outline", routine: "mdi:calendar-clock", note: "mdi:note-text-outline", correction: "mdi:school-outline" };

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

  const dayNameShort = (iso) => {
    const d = new Date(iso), t = new Date();
    return d.toDateString() === t.toDateString() ? "TODAY" : d.toLocaleDateString("en-GB", { weekday: "short" }).toUpperCase();
  };

  class SentientPanel extends HTMLElement {
    set hass(hass) {
      this._hass = hass;
      if (!this.shadowRoot) this._build();
      if (!this._wizChecked && hass.user && hass.user.is_admin) {
        this._wizChecked = true;
        hass.callWS({ type: "sentient/wizard", language: hass.language }).then((w) => { if (w.show) this._openWizard(w); }, () => {});
      }
      if (this._wizForm) this._wizForm.hass = hass;
      if (this._form) this._form.hass = hass;
      const ids = this._ids();
      const sig = KEYS.map((k) => { const s = ids[k] && hass.states[ids[k]]; return s ? s.last_updated : ""; }).join("|") + "#" + this._security().key;
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
      const path = String((r && r.path) || "");
      const want = path.startsWith("/settings") ? "settings" : path.startsWith("/timeline") ? "timeline" : "overview";
      if (this.shadowRoot && want !== this._view) this._show(want, false);
      else if (!this.shadowRoot) this._view = want;
    }

    connectedCallback() { this._tick = setInterval(() => this._render(), 30000); }
    disconnectedCallback() {
      clearInterval(this._tick);
      if (this._tlSub) { this._tlSub.then((unsub) => unsub()).catch(() => {}); this._tlSub = null; }
    }

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
            <button data-view="timeline"><ha-icon icon="mdi:timeline-clock-outline"></ha-icon><span>TIMELINE</span></button>
            <button data-view="settings"><ha-icon icon="mdi:cog-outline"></ha-icon><span>SETTINGS</span></button></nav>
          <form class="ask" id="askf"><ha-icon icon="mdi:creation-outline"></ha-icon><input id="ask" placeholder="Ask anything…" autocomplete="off"></form>
          <button class="brief" id="brief"><ha-icon icon="mdi:text-box-outline"></ha-icon><span>BRIEFING</span></button></div>
        <div class="reply" id="reply" hidden></div>
        <div class="page" id="ov"><div id="head" class="card head"></div>
          <div class="cols"><div class="col stack"><div id="sug" class="card" hidden></div><div id="clipc" class="card" hidden></div><div id="health" class="card"></div><div id="sec" class="card"></div><div id="watch" class="card"></div><div id="mlc" class="card" hidden></div></div>
            <div class="col stack"><div id="mind" class="card"></div><div id="rout" class="stack"></div></div>
            <div class="col stack"><div id="apl" class="card" hidden></div><div id="ins" class="card" hidden></div><div id="energy" class="card" hidden></div><div id="visit" class="card"></div><div id="said" class="card"></div></div></div></div>
        <div class="page tlp" id="tl" hidden><div class="card"><div class="h"><span class="t">TIMELINE</span><i></i><em id="tlcount"></em></div>
          <div class="digest" id="tldig"></div><div class="filters" id="tlf"></div><div id="tllist"></div></div></div>
        <div class="page set" id="set" hidden><nav class="card side" id="side"></nav><div class="card body" id="body"></div></div>
        <div class="wiz" id="wiz" hidden><div class="card wizc" id="wizc"></div></div>`;
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
      this.shadowRoot.addEventListener("change", (ev) => {
        const id = ev.target.dataset && ev.target.dataset.mltoggle;
        if (!id) return;
        const r = (this._ml.rules || []).find((x) => x.id === id);
        if (r) this._motionDo("sentient/motion_lights/save", { rule: { id, light: r.light, motion: r.motion, enabled: ev.target.checked } }).then(() => { this._drawMotion(); this._drawMlCard(); });
      });
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
      this._$("tl").hidden = view !== "timeline";
      if (push && this._route) {
        const base = this._route.prefix || "/sentient";
        history.replaceState(history.state, "", view === "overview" ? base : `${base}/${view}`);
      }
      if (view === "settings") this._openSettings();
      else if (view === "timeline") this._openTimeline();
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
      if (this._view === "settings") {
        if (this._group === "motion" && !this._mlEdit && Date.now() - (this._mlAt || 0) > 10000) { this._mlAt = Date.now(); this._loadMotion(); }
        return;
      }

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
      const ai = ((this._st("mind") || {}).attributes || {}).ai || {};
      const aiTxt = ai.failing ? "AI NOT ANSWERING · PLAIN WORDS" : ai.capped ? "AI LIMIT REACHED · PLAIN WORDS" : `AI ${ai.today ?? this._attr("likely_room", "ai_checks_today", 0)}${ai.limit ? "/" + ai.limit : ""} TODAY`;
      R("head").innerHTML = `<img src="/sentient_static/logo.png" alt="">
        <div class="m"><div class="ti">${esc(persona).toUpperCase()}</div>
          <div class="sub">${(this._st("house") || {}).state === "asleep" ? "HOUSE ASLEEP" : this._attr("likely_room", "quiet_hours") ? "QUIET HOURS" : "AWAKE"} · PEOPLE IN ${esc(room.state).toUpperCase()} · ${aiTxt}</div>
          <div class="modes">${this._modeChip("hush", "mdi:volume-off", "HUSH 1 HOUR", "HUSHED")}${this._modeChip("guest_mode", "mdi:account-multiple", "GUEST MODE", "GUESTS IN")}${this._showChips()}${this._chimeChip()}${(this._sc || []).map((r) => `<button class="mode run" data-act="scrun" data-id="${esc(r.id)}"><ha-icon icon="mdi:play-circle-outline"></ha-icon>${esc(r.name).toUpperCase()}</button>`).join("")}</div>
          <div class="st">${stat(st("memories"), "MEMORIES ›", "memories")}${stat(st("watches"), "WATCHING")}${stat(st("routines"), "ROUTINES")}${stat(st("visitors"), "VISITORS")}${stat(st("health"), "PROBLEMS")}${stat(ai.today ?? 0, ai.limit ? `AI CALLS / ${ai.limit}` : "AI CALLS")}</div></div>
        <div class="rm"><div class="t">WHERE PEOPLE ARE</div>${bars || '<div class="b">NO RECENT MOTION</div>'}</div>`;

      // suggestions: things it could use but isn't (refreshed every ten minutes; empty when turned off)
      if (!this._sugAt || Date.now() - this._sugAt > 600000) {
        this._sugAt = Date.now();
        this._hass.callWS({ type: "sentient/suggestions" }).then((r) => { this._sug = r.items || []; this._render(); }, () => { this._sug = []; });
      }
      const sug = this._sug || [];
      R("sug").hidden = !sug.length;
      R("sug").innerHTML = sug.length ? this._h("SUGGESTIONS", `${sug.length}`) + sug.map((s) => `<div class="r"><ha-icon icon="mdi:lightbulb-auto-outline"></ha-icon><b class="wrap">${esc(s.text)}</b>
          <button class="btn sm" data-act="sugyes" data-id="${esc(s.id)}">USE IT</button><a class="x" data-act="sugno" data-id="${esc(s.id)}" title="Don't suggest again">✕</a></div>`).join("") : "";

      // clips watched as they happened (refreshed every two minutes; hidden when off and empty)
      if (!this._clipAt || Date.now() - this._clipAt > 120000) {
        this._clipAt = Date.now();
        this._hass.callWS({ type: "sentient/clips" }).then((r) => { this._clips = r; this._render(); }, () => { this._clips = null; });
      }
      const cl = this._clips || { on: false, items: [] };
      const clipIc = { delivery: "mdi:package-variant-closed", visitor: "mdi:account", household: "mdi:home-account", "passer-by": "mdi:walk", animal: "mdi:paw", vehicle: "mdi:car", suspicious: "mdi:alert-outline", nothing: "mdi:minus" };
      R("clipc").hidden = !cl.on && !(cl.items || []).length;
      R("clipc").innerHTML = this._h("CLIPS WATCHED", `${(cl.items || []).length}`) + ((cl.items || []).slice(0, 12).map((c) => `<div class="r"><ha-icon icon="${clipIc[c.kind] || "mdi:filmstrip-box"}"></ha-icon><b class="wrap">${esc(c.local)} · ${esc(c.camera.replace(/_/g, " ").toUpperCase())} — ${esc(c.summary)}${c.whose ? ` <i>(probably ${esc(c.whose)})</i>` : ""}</b>${c.event ? `<a class="btn sm ghost" href="/api/frigate/notifications/${encodeURIComponent(c.event)}/clip.mp4" target="_blank" rel="noopener">PLAY</a>` : ""}</div>`).join("")
        || '<div class="e">NOTHING WATCHED YET · IT WRITES HERE WHEN THE CAMERAS SEE SOMEONE</div>');

      // routines
      const routines = this._attr("routines", "routines", []);
      const on = routines.filter((r) => r.adopted).length;
      const gone = this._attr("routines", "deleted", []);
      const back = gone.length ? `<a class="lnk" data-act="unforget" data-text="Let ${gone.length} deleted routine${gone.length > 1 ? "s" : ""} be learned again?">${gone.length} DELETED · BRING BACK</a>` : "";
      R("rout").innerHTML = `<div class="card">${this._h("ROUTINES &amp; HABITS", `${on} AUTOMATED · ${routines.length} LEARNED`, true)}${back}</div>` +
        (routines.slice(0, 6).map((r) => {
          const tag = r.adopted ? "HOUSE DOES IT" : r.declined ? "DECLINED" : r.offered ? "OFFERED" : "LEARNED";
          const cls = r.adopted ? "on" : r.declined ? "off" : "";
          const ask = r.adopted ? `Stop the house doing: ${r.routine}` : `Let the house do this from now on: ${r.routine}`;
          return `<div class="card rt ${cls}" data-act="adopt" data-id="${esc(r.id)}" data-text="${esc(ask)}">
            <div class="r"><b class="wrap">${esc(r.routine)}</b><s>${tag}</s><a class="x" data-act="unlearn" data-id="${esc(r.id)}" data-text="${esc(r.routine)}" title="Delete">✕</a></div>
            <div class="hint">${esc(r.how_sure).toUpperCase()} · TAP TO ${r.adopted ? "STOP" : "LET THE HOUSE DO IT"}</div></div>`;
        }).join("") || '<div class="card"><div class="e">NO ROUTINES LEARNED YET — CHECKED NIGHTLY</div></div>');

      // watches (left alone while a reminder is being typed)
      const act = this.shadowRoot.activeElement;
      const typing = act && act.closest && act.closest('form[data-form="remind"]');
      const watches = this._attr("watches", "watches", []);
      if (!typing) R("watch").innerHTML = this._h("WATCHES &amp; REMINDERS", `${watches.length} ACTIVE`) + (watches.map((w) => {
        const ic = w.kind === "reminder" ? "mdi:bell-ring-outline" : w.kind === "camera" ? "mdi:cctv" : "mdi:eye-outline";
        return `<div class="r"><ha-icon icon="${ic}"></ha-icon><b>${esc(w.what)}${w.spot ? " · " + esc(w.spot) : ""}</b><u>${left(w.ends, w.minutes_left)}</u>
          <a class="x" data-act="cancel" data-id="${esc(w.id)}" data-text="${esc(w.what)}" title="Cancel">✕</a></div>`;
      }).join("") || '<div class="e">NOTHING BEING WATCHED · SAY “KEEP AN EYE ON…”</div>')
        + `<form class="add" data-form="remind"><ha-icon icon="mdi:bell-plus-outline"></ha-icon><input name="msg" placeholder="Remind…, e.g. take the bins out" autocomplete="off">
            <input name="when" class="when" placeholder="20m · 1h · 18:30" autocomplete="off"><button class="btn sm" type="submit">SET</button></form>`;

      if (this._sc === undefined) { this._sc = null; this._loadShortcuts(); }

      // energy (every 10 minutes)
      if (Date.now() - (this._enAt || 0) > 600000) {
        this._enAt = Date.now();
        this._hass.callWS({ type: "sentient/energy" }).then((r) => { this._en = r; this._drawEnergy(); }, () => {});
      }
      this._drawEnergy();

      // heating & night watch (every 5 minutes)
      if (Date.now() - (this._insAt || 0) > 300000) {
        this._insAt = Date.now();
        this._hass.callWS({ type: "sentient/insights" }).then((r) => { this._ins = r; this._drawInsights(); }, () => {});
      }
      this._drawInsights();

      this._drawSecurity();

      // motion lights
      if (Date.now() - (this._mlAt || 0) > 20000) { this._mlAt = Date.now(); this._loadMotion(); }
      this._drawMlCard();

      // house health
      const issues = this._attr("health", "issues", []), updates = this._attr("health", "updates", []);
      const ago = (t) => t ? (Date.now() - new Date(t) > 864e5 ? day(t) : hm(t)) : "";
      const healthRows = issues.slice(0, 6).map((i) => i.kind === "offline"
        ? `<div class="r bad"><ha-icon icon="mdi:lan-disconnect"></ha-icon><b>${esc(i.name)}</b><u>OFFLINE${i.since ? " · " + ago(i.since) : ""}</u></div>`
        : i.kind === "ai" ? `<div class="r bad"><ha-icon icon="mdi:robot-confused-outline"></ha-icon><b title="${esc(i.detail || "")}">The AI isn't answering</b><u>PLAIN WORDS</u></div>`
        : i.kind === "limit" ? `<div class="r warn"><ha-icon icon="mdi:counter"></ha-icon><b>Today's AI calls are used up</b><u>${esc(i.level)} · PLAIN WORDS</u></div>`
        : i.kind === "speaker" ? `<div class="r bad"><ha-icon icon="mdi:speaker-off"></ha-icon><b title="${esc(i.detail || "")}">Couldn't speak on ${esc(i.name)}</b><u>${ago(i.since)}</u></div>`
        : ["wear", "fight", "frozen"].includes(i.kind) ? `<div class="r warn"><ha-icon icon="${i.kind === "fight" ? "mdi:sword-cross" : i.kind === "frozen" ? "mdi:cctv-off" : "mdi:wrench-clock"}"></ha-icon><b class="wrap">${esc(i.text)}</b></div>`
        : i.kind === "stuck" ? `<div class="r warn"><ha-icon icon="mdi:motion-sensor-off"></ha-icon><b>${esc(i.name)}</b><u>${i.on ? "STUCK ON" : "NOTHING SINCE " + ago(i.since)}</u></div>`
        : `<div class="r warn"><ha-icon icon="mdi:battery-alert-variant-outline"></ha-icon><b>${esc(i.name)}</b><u>${i.level == null ? "LOW" : i.level + "%"}</u></div>`).join("")
        + (updates.length ? `<div class="r"><ha-icon icon="mdi:package-up"></ha-icon><b>${updates.length} update${updates.length > 1 ? "s" : ""} waiting · ${esc(updates.slice(0, 3).join(", "))}</b></div>` : "");
      R("health").innerHTML = this._h("HOUSE HEALTH", issues.length ? `${issues.length} PROBLEM${issues.length > 1 ? "S" : ""}` : "ALL GOOD")
        + (healthRows || '<div class="e">EVERYTHING ONLINE · BATTERIES FINE</div>');

      // mind: what it's thinking, wondering, waiting to check and pass on
      const mind = (this._st("mind") || {}).attributes || {};
      const fresh = mind.last_reflection && Date.now() - new Date(mind.last_reflection) < 16 * 3600e3;
      const mindRows = [
        ...(mind.voice_sessions || []).map((s) => `<div class="r"><ha-icon icon="mdi:account-voice"></ha-icon><b class="wrap">${esc(s.pending_action || s.goal || s.topic || "Voice session")}${s.unanswered_question ? " · " + esc(s.unanswered_question) : ""}<small>${esc((s.listen_reason || "").replaceAll("_", " "))}${s.missing_detail ? " · " + esc(s.missing_detail) : ""}</small></b><u>${esc(s.state.replaceAll("_", " "))}${s.undo_available ? " · UNDO AVAILABLE" : ""}</u></div>`),
        ...(mind.security_incidents || []).slice(0, 3).map((i) => `<div class="r"><ha-icon icon="mdi:shield-search"></ha-icon><b class="wrap">${esc(i.evidence.slice(-3).map((e) => e.text).join(" · "))}<small>${esc(i.link)}</small></b><u>${hm(i.start)}</u></div>`),
        ...(fresh ? (mind.thoughts || []) : []).map((t) => `<div class="r"><ha-icon icon="mdi:lightbulb-on-outline"></ha-icon><b>${esc(t)}</b><u>OVERNIGHT</u></div>`),
        ...(mind.wondering || []).map((q) => `<div class="r"><ha-icon icon="mdi:help-circle-outline"></ha-icon><b>${esc(q)}</b>
          <button class="btn sm ghost" data-act="wonderask">ASK</button><a class="x" data-act="wonderx" data-text="${esc(q)}" title="Don't ask">✕</a></div>`),
        ...(mind.follow_ups || []).map((f) => `<div class="r"><ha-icon icon="mdi:sync"></ha-icon><b>Check back: ${esc(f.about)}</b><u>${esc(f.at)}</u>
          <a class="x" data-act="fux" data-id="${esc(f.id)}" data-text="${esc(f.about)}" title="Cancel">✕</a></div>`),
        ...(mind.to_pass_on || []).map((t) => `<div class="r dim"><ha-icon icon="mdi:account-arrow-right-outline"></ha-icon><b>${esc(t)}</b><u>TO PASS ON</u></div>`),
        ...(mind.lessons || []).slice(-2).map((t) => `<div class="r dim"><ha-icon icon="mdi:school-outline"></ha-icon><b>${esc(t)}</b><u>LESSON</u></div>`),
        ...(mind.recent_conversations || []).slice(0, 3).map((c) => `<div class="r dim"><ha-icon icon="mdi:chat-outline"></ha-icon>
          <b>${esc(((c.conversation || [])[0] || "").replace(/^They: /, ""))}</b><u>${esc(String(c.when || "").split(" ").pop())}${c.room ? " · " + esc(c.room).toUpperCase() : ""}</u></div>`),
      ].join("");
      const mindCount = (mind.wondering || []).length + (mind.follow_ups || []).length;
      // the last hour, on request: straight from the timeline, no AI
      const lastHour = this._lh ? (this._lh.length ? this._lh.map((i) => `<div class="r dim"><ha-icon icon="mdi:clock-outline"></ha-icon>
          <b>${esc(i.text)}</b><u>${hm(i.time)}</u></div>`).join("") : '<div class="e">NOTHING IN THE LAST HOUR</div>') : "";
      R("mind").innerHTML = this._h("MIND", mindCount ? `${mindCount} ON ITS MIND` : `${this._st("mind") ? this._st("mind").state : 0} TALKS TODAY`)
        + (mindRows || '<div class="e">NOTHING ON ITS MIND · THINKS IT OVER EACH NIGHT</div>')
        + `<div class="row lh"><button class="btn sm ghost" data-act="lasthour"><ha-icon icon="mdi:history"></ha-icon> ${this._lh ? "HIDE THE LAST HOUR" : "WHAT HAPPENED THIS LAST HOUR?"}</button></div>`
        + lastHour
        + (ai.by_purpose ? `<div class="hint meter">AI ${ai.today}${ai.limit ? " OF " + ai.limit : ""} TODAY${Object.keys(ai.by_purpose).length ? " · " + Object.entries(ai.by_purpose).slice(0, 3).map(([k, v]) => `${esc(k).toUpperCase()} ${v}`).join(" · ") : ""}${ai.average_per_day != null ? " · USUALLY " + ai.average_per_day + "/DAY" : ""}</div>` : "");
      this._drawAppliances(mind.appliances || []);

      // visitors
      const visits = this._attr("visitors", "recent", []);
      R("visit").innerHTML = this._h("VISITORS", `${st("visitors")} TODAY`) + (visits.slice(0, 4).map((v) =>
        `<div class="r"><ha-icon icon="${v.kind === "delivery" ? "mdi:package-variant-closed" : "mdi:doorbell-video"}"></ha-icon>
          <b>${v.name ? esc(v.name) + " — " : ""}${esc(v.description)}${v.parcel_left ? " · PARCEL LEFT" : ""}</b><u>${hm(v.time)}</u></div>`).join("")
        || '<div class="e">NO ONE AT THE DOOR YET</div>');

      // said & noticed (left alone while a "say it differently" note is being typed)
      const styleOn = !!((this._st("mind") || {}).attributes || {}).style_feedback;
      if (act && act.closest && act.closest('form[data-form="restyle"]')) return;
      const log = this._attr("last_spoken", "log", []).slice(0, 4).map((l) => l.said
        ? `<div class="r"><ha-icon icon="mdi:account-voice"></ha-icon><b>${esc(l.said)}</b><u>${hm(l.time)}</u>${(this._thumbs || {})[l.said] ? `<s class="thd">${this._thumbs[l.said] === "up" ? "👍" : this._thumbs[l.said] === "styled" ? "✎ NOTED" : "👎"}</s>` : `<a class="x th" data-act="thumb" data-id="up" data-text="${esc(l.said)}" data-src="${esc(l.source || "")}" title="Good to hear"><ha-icon icon="mdi:thumb-up-outline"></ha-icon></a><a class="x th" data-act="thumb" data-id="down" data-text="${esc(l.said)}" data-src="${esc(l.source || "")}" title="Not worth saying"><ha-icon icon="mdi:thumb-down-outline"></ha-icon></a>`}${styleOn ? `<a class="x th" data-act="restyle" data-text="${esc(l.said)}" title="Say it differently"><ha-icon icon="mdi:comment-edit-outline"></ha-icon></a>` : ""}</div>
          ${styleOn && this._restyle === l.said ? `<div class="r restyle">${["Shorter", "Plainer", "Less cheeky", "More detail"].map((n) => `<button class="btn sm ghost" data-act="restyleset" data-id="${n}" data-text="${esc(l.said)}">${n.toUpperCase()}</button>`).join("")}
            <form data-form="restyle" data-text="${esc(l.said)}"><input name="note" placeholder="Or say how…" autocomplete="off"><button class="btn sm" type="submit">SAVE</button></form><a class="x" data-act="restylex" title="Close">✕</a></div>` : ""}`
        : `<div class="r dim"><ha-icon icon="mdi:volume-off"></ha-icon><b>${esc(l.held)}</b><u>${esc(String(l.reason || "").split(":")[0].replace("_", " ")).toUpperCase()}</u></div>`).join("");
      const noticed = this._attr("last_spoken", "noticed", []).slice(0, 3).map((d) =>
        `<div class="r dim"><ha-icon icon="mdi:radar"></ha-icon><b>${esc((d.changes || []).join("; "))}</b><u>${esc(d.result).toUpperCase()}</u></div>`).join("");
      R("said").innerHTML = this._h("SAID &amp; NOTICED", "") + (log + noticed || '<div class="e">QUIET SO FAR</div>');
    }

    // ---------- settings ----------
    async _openSettings() {
      this._drawSide();
      this._loadMemories();
      this._loadSpots();
      this._loadMotion();
      this._loadMutes(true);
      this._loadShortcuts();
      ["alarms", "offers", "announce", "checks", "autooff", "rules"].forEach((k) => this._loadItems(k));
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

    _phone() { return window.matchMedia("(max-width: 720px)").matches; }

    _drawSide() {
      const groups = (this._settings && this._settings.groups) || [];
      const n = (k) => (this._it && this._it[k] ? this._it[k].items.length : null);
      const count = this._memories ? this._memories.length : this._st("memories") ? this._st("memories").state : "";
      // Every page the Settings tab has: id -> [icon, title, short line]
      const pages = {
        memories: ["mdi:brain", "Memories", count !== "" ? `${count} remembered` : ""],
        mutes: ["mdi:volume-mute", "Shushed", this._mutes ? `${this._mutes.length} muted` : "Things it won't mention"],
        spots: ["mdi:selection-marker", "Camera spots", this._spots ? `${this._spots.length} taught` : "Named areas the AI zooms into"],
        shortcuts: ["mdi:playlist-play", "Routines", this._sc ? `${this._sc.length} · goodnight, morning…` : "Goodnight, good morning…"],
        motion: ["mdi:motion-sensor", "Motion lights", this._ml ? `${this._ml.rules.length} light${this._ml.rules.length === 1 ? "" : "s"} · after dark` : "Lights that follow movement"],
        alarms: ["mdi:alarm", "Alarms", n("alarms") !== null ? `${n("alarms")} set` : "Alarms, appointments, routines"],
        offers: ["mdi:hand-heart-outline", "Offers", n("offers") !== null ? `${n("offers")} · fancy a brew?` : "Questions it asks — a brew?"],
        announce: ["mdi:bullhorn-outline", "Announcements", n("announce") !== null ? `${n("announce")} · doorbell, gate` : "Doorbell, gate…"],
        autooff: ["mdi:timer-off-outline", "Auto-off", n("autooff") !== null ? `${n("autooff")} · lights, coffee…` : "Off when left on or the room's empty"],
        rules: ["mdi:script-text-outline", "Rules", n("rules") !== null ? `${n("rules")} · when this, do that` : "When this happens, do that"],
        checks: ["mdi:cctv", "Camera checks", n("checks") !== null ? `${n("checks")} watching` : "Delivery van outside?"],
        backup: ["mdi:backup-restore", "Backup", "Download or restore everything"],
      };
      groups.forEach((g) => { pages[g.id] = [g.icon, g.title, g.blurb]; });
      // The menus, in order; anything not listed lands in the last one
      const menus = [
        ["voice", "mdi:account-voice", "Voice & sound", ["voice", "voicebackup", "quiet", "announce", "offers", "mutes", "briefing", "phone"]],
        ["cameras", "mdi:cctv", "Cameras & video", ["door", "spots", "checks", "camsec", "drive"]],
        ["security", "mdi:shield-home", "Security & safety", ["nightwatch", "safety", "health"]],
        ["home", "mdi:home-automation", "Home & routines", ["shortcuts", "rules", "alarms", "autooff", "appliances", "animals", "rain", "bins", "away", "motion", "party", "heating", "energy"]],
        ["brain", "mdi:brain", "Brain & system", ["memories", "mind", "noticing", "page", "backup", "autobackup"]],
      ];
      const placed = new Set(menus.flatMap((m) => m[3]));
      Object.keys(pages).forEach((id) => { if (!placed.has(id)) menus[menus.length - 1][3].push(id); });
      const mine = menus.find((m) => m[3].includes(this._group));
      if (!this._openMenus) this._openMenus = new Set();
      if (mine && this._menuFor !== this._group) { this._menuFor = this._group; if (!this._phone()) this._openMenus.add(mine[0]); }
      const item = (id) => {
        const [icon, title, sub] = pages[id];
        return `<button class="nav${this._group === id ? " on" : ""}" data-act="group" data-id="${id}"><ha-icon icon="${icon}"></ha-icon><span><b>${esc(title)}</b>${sub ? `<i>${esc(sub)}</i>` : ""}</span></button>`;
      };
      this._$("side").innerHTML = `<div class="sh">SETTINGS</div><button class="btn ghost wizbtn" data-act="wizard"><ha-icon icon="mdi:auto-fix"></ha-icon>SET-UP WIZARD</button>` + menus.map(([mid, icon, title, ids]) => {
        const list = ids.filter((id) => pages[id]);
        if (!list.length) return "";
        const open = this._openMenus.has(mid);
        const here = mine && mine[0] === mid;
        return `<div class="menu${open ? " open" : ""}${here ? " here" : ""}">
          <button class="mh" data-act="menu" data-id="${mid}" aria-expanded="${open}"><ha-icon icon="${icon}"></ha-icon><span>${esc(title)}</span><u>${list.length}</u><ha-icon class="chev" icon="mdi:chevron-down"></ha-icon></button>
          <div class="mi">${list.map(item).join("")}</div></div>`;
      }).join("") + (this._settingsErr ? `<div class="note">${esc(this._settingsErr)}</div>` : "");
    }

    async _loadMemories() {
      try {
        const res = await this._hass.callWS({ type: "sentient/memories" });
        this._memories = res.items; this._cats = res.categories; this._admin = res.admin;
      } catch (e) {
        this._memories = this._attr("memories", "items", []);
        this._cats = ["fact", "preference", "person", "routine", "note", "correction"];
      }
      if (this._view === "settings") { this._drawSide(); if (this._group === "memories") this._drawMemories(); }
    }

    _drawBody() {
      if (this._group === "memories") { this._form = null; this._drawMemories(); return; }
      if (this._group === "spots") { this._form = null; this._drawSpots(); return; }
      if (this._group === "motion") { this._form = null; this._drawMotion(); return; }
      if (this._group === "mutes") { this._form = null; this._loadMutes(); return; }
      if (this._group === "shortcuts") { this._form = null; this._loadShortcuts(); return; }
      if (this._group === "backup") { this._form = null; this._drawBackup(); return; }
      if (["alarms", "offers", "announce", "checks", "autooff", "rules"].includes(this._group)) { this._form = null; this._itEdit = null; this._loadItems(this._group); return; }
      const g = this._settings && this._settings.groups.find((x) => x.id === this._group);
      if (!g) { this._group = "memories"; this._drawMemories(); return; }
      const body = this._$("body");
      body.innerHTML = `<div class="bh"><ha-icon icon="${g.icon}"></ha-icon><div><div class="bt">${esc(g.title).toUpperCase()}</div><div class="bs">${esc(g.blurb)}</div></div></div>
        <div id="formwrap" class="formwrap"><div class="e">LOADING…</div></div>
        <div class="bar"><span id="status"></span>${g.id === "safety" ? '<button class="btn ghost" data-act="critest">TEST ALERT</button>' : ""}<button class="btn ghost" data-act="reset">UNDO CHANGES</button><button class="btn" data-act="save">SAVE</button></div>`;
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

    // ---------- set-up wizard ----------
    async _openWizard(w) {
      const el = this._$("wiz");
      el.hidden = false;
      this._$("wizc").innerHTML = '<div class="e">LOOKING AROUND THE HOUSE…</div>';
      try {
        w = w || await this._hass.callWS({ type: "sentient/wizard", language: this._hass.language });
      } catch (e) {
        this._$("wizc").innerHTML = `<div class="e">${esc(e.message || e)}</div><div class="wizbar"><button class="btn ghost" data-act="wizlater">CLOSE</button></div>`;
        return;
      }
      this._wiz = { ...w, i: 0, draft: { ...w.data }, backupPipeline: true };
      this._wizGo(0);
    }

    _closeWizard() { this._$("wiz").hidden = true; this._wizForm = null; }

    _wizGo(i) {
      const W = this._wiz, steps = W.steps, last = steps.length;  // one extra step at the end: test & finish
      W.i = Math.max(0, Math.min(i, last));
      const dots = [...steps, { title: "Test" }].map((s, n) => `<i class="${n === W.i ? "on" : n < W.i ? "done" : ""}"></i>`).join("");
      const head = (icon, title, blurb) => `<div class="wizh"><div class="wizdots">${dots}</div><div class="wizn">STEP ${W.i + 1} OF ${last + 1}</div></div>
        <div class="bh"><ha-icon icon="${icon}"></ha-icon><div><div class="bt">${esc(title).toUpperCase()}</div><div class="bs">${esc(blurb)}</div></div></div>`;
      const c = this._$("wizc");
      if (W.i === last) {
        const persona = W.draft.persona || W.persona;
        c.innerHTML = head("mdi:check-decagram-outline", "Test & finish", `That's it. Say hello on the speakers to check they work, then finish. Everything can be changed later under Settings.`)
          + (W.own_agent ? `<label class="wizchk"><input type="checkbox" id="wizpipe" ${W.backupPipeline ? "checked" : ""}><span>Use <b>${esc(persona)} with backup</b> in my voice pipelines, so simple things still work if the AI is down</span></label>` : "")
          + `<div class="wizres" id="wizres"></div>
          <div class="wizbar"><button class="btn ghost" data-act="wizback">BACK</button><span></span><button class="btn ghost" data-act="wiztest"><ha-icon icon="mdi:speaker-play"></ha-icon> SAY HELLO</button><button class="btn" data-act="wizdone">FINISH</button></div>`;
        const pipe = this._$("wizpipe");
        if (pipe) pipe.addEventListener("change", () => { W.backupPipeline = pipe.checked; });
        this._wizForm = null;
        return;
      }
      const st = steps[W.i];
      const suggested = st.schema.filter((sc) => (W.suggested || []).includes(sc.name)).length;
      c.innerHTML = head(st.icon, st.title, st.blurb)
        + (suggested ? `<div class="wizsug"><ha-icon icon="mdi:auto-fix"></ha-icon>FILLED IN FROM YOUR HOUSE — CHECK AND CHANGE ANYTHING</div>` : "")
        + `<div class="formwrap" id="wizform"><div class="e">LOADING…</div></div>
        <div class="wizbar">${W.i ? '<button class="btn ghost" data-act="wizback">BACK</button>' : '<button class="btn ghost" data-act="wizlater">LATER</button>'}<span id="wizst"></span><button class="btn" data-act="wiznext">NEXT</button></div>`;
      loadForm().then((ok) => {
        if (this._wiz !== W || W.i !== steps.indexOf(st)) return;
        const wrap = this._$("wizform");
        if (!ok) { wrap.innerHTML = '<div class="e">THE FORM COULDN\'T LOAD · REFRESH THE PAGE</div>'; return; }
        const form = document.createElement("ha-form");
        form.hass = this._hass;
        form.schema = st.schema;
        form.data = W.draft;
        form.computeLabel = (sc) => W.labels[sc.name] || sc.name;
        form.computeHelper = (sc) => W.helpers[sc.name] || "";
        form.localizeValue = (key) => W.selector[key] || key.split(".").pop();
        form.addEventListener("value-changed", (ev) => { W.draft = { ...ev.detail.value }; form.data = W.draft; });
        wrap.innerHTML = "";
        wrap.appendChild(form);
        this._wizForm = form;
      });
    }

    async _wizNext() {
      const W = this._wiz, st = W.steps[W.i];
      const data = {};
      for (const sc of st.schema) { const v = W.draft[sc.name]; if (v !== undefined && v !== null && v !== "") data[sc.name] = v; }
      const status = this._$("wizst");
      if (status) { status.textContent = "SAVING…"; status.className = ""; }
      try {
        const res = await this._hass.callWS({ type: "sentient/wizard/save", step: st.id, data });
        W.draft = { ...W.draft, ...res.data };
        this._settings = null;  // the Settings tab reloads with these
        this._wizGo(W.i + 1);
      } catch (e) {
        if (status) { status.textContent = String(e.message || e).toUpperCase(); status.className = "warn"; }
      }
    }

    async _wizTest() {
      const out = this._$("wizres");
      out.innerHTML = '<div class="e">SAYING HELLO…</div>';
      try {
        const r = await this._hass.callWS({ type: "sentient/wizard/finish", test: true });
        out.innerHTML = r.spoken
          ? `<div class="r"><ha-icon icon="mdi:check-circle-outline"></ha-icon><b>Said on ${esc((r.speakers || []).map((s) => (this._hass.states[s] || {}).attributes ? this._hass.states[s].attributes.friendly_name || s : s).join(", "))}</b></div>`
          : `<div class="r warn"><ha-icon icon="mdi:alert-outline"></ha-icon><b>Not said: ${esc(String(r.reason || "").replace(/_/g, " "))}</b></div>`;
      } catch (e) {
        out.innerHTML = `<div class="r bad"><ha-icon icon="mdi:alert-outline"></ha-icon><b>${esc(e.message || e)}</b></div>`;
      }
    }

    async _wizFinish() {
      const W = this._wiz;
      if (W.own_agent && W.backupPipeline) {
        try {
          const { pipelines } = await this._hass.callWS({ type: "assist_pipeline/pipeline/list" });
          const main = W.draft.voice_agent;
          for (const p of pipelines || []) {
            if (!main || p.conversation_engine !== main) continue;
            const { id, ...rest } = p;
            await this._hass.callWS({ type: "assist_pipeline/pipeline/update", pipeline_id: id, ...rest, conversation_engine: W.own_agent });
          }
        } catch (e) { /* the pipeline can be changed by hand */ }
      }
      try { await this._hass.callWS({ type: "sentient/wizard/finish" }); } catch (e) { /* closes anyway */ }
      this._closeWizard();
      this._render();
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
        this._enAt = 0; this._en = null;  // energy card follows its on/off setting
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


    // ---------- camera spots ----------
    async _loadSpots() {
      try { this._spots = (await this._hass.callWS({ type: "sentient/spots" })).spots; } catch (e) { this._spots = []; }
      if (this._view === "settings") { this._drawSide(); if (this._group === "spots") this._drawSpots(); }
    }

    _cameras() {
      return Object.values(this._hass.states).filter((s) => s.entity_id.startsWith("camera.") && s.state !== "unavailable")
        .map((s) => ({ id: s.entity_id, name: s.attributes.friendly_name || s.entity_id }))
        .sort((a, b) => a.name.localeCompare(b.name));
    }

    _snapUrl(cam) {
      const st = this._hass.states[cam];
      const pic = st && st.attributes.entity_picture;
      return pic ? `${pic}${pic.includes("?") ? "&" : "?"}t=${this._snapT || 0}` : "";
    }

    _drawSpots() {
      if (this._view !== "settings" || this._group !== "spots") return;
      const cams = this._cameras();
      const spots = this._spots || [];
      if (!this._cam || !cams.find((c) => c.id === this._cam)) {
        const withSpot = spots.find((sp) => cams.find((c) => c.id === sp.camera));
        this._cam = withSpot ? withSpot.camera : (cams[0] && cams[0].id);
      }
      const here = spots.filter((sp) => sp.camera === this._cam);
      const box = (b, label, cls) => `<div class="box ${cls || ""}" style="left:${b[0] * 100}%;top:${b[1] * 100}%;width:${(b[2] - b[0]) * 100}%;height:${(b[3] - b[1]) * 100}%">${label ? `<span>${esc(label)}</span>` : ""}</div>`;
      const poly = (pts, label, cls) => `<svg class="shape ${cls || ""}" viewBox="0 0 100 100" preserveAspectRatio="none"><polygon points="${pts.map((p) => `${p[0] * 100},${p[1] * 100}`).join(" ")}"/></svg>`
        + (label ? `<div class="box lbl" style="left:${Math.min(...pts.map((p) => p[0])) * 100}%;top:${Math.min(...pts.map((p) => p[1])) * 100}%;width:0;height:0;border:0;box-shadow:none"><span>${esc(label)}</span></div>` : "");
      const mark = (sp) => (sp.points && sp.points.length >= 3 ? poly(sp.points, sp.name) : box(sp.box, sp.name));
      const pts = this._pts || [];
      const draft = this._shape
        ? (pts.length ? poly(pts, this._spName || "new spot", "new") + pts.map((p) => `<i class="dotp" style="left:${p[0] * 100}%;top:${p[1] * 100}%"></i>`).join("") : "")
        : (this._draft_box ? box(this._draft_box, this._spName || "new spot", "new") : "");
      const ready = this._shape ? pts.length >= 3 : !!this._draft_box;
      const body = this._$("body");
      body.innerHTML = `<div class="bh"><ha-icon icon="mdi:selection-marker"></ha-icon><div><div class="bt">CAMERA SPOTS</div>
          <div class="bs">Name a part of a camera's picture — "the rabbit pen", "the drive" — and other names for it. Questions that mention it use the camera that can see it, and send the AI just that part. Draw a shape to leave out what's next to it (a neighbour's car).</div></div></div>
        <div class="spotgrid"><div>
          <div class="row"><select id="spcam">${cams.map((c) => `<option value="${c.id}"${c.id === this._cam ? " selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
            <button class="btn ghost sm" data-act="snap">↻ NEW PICTURE</button></div>
          <div class="snap${this._drawing ? " drawing" : ""}" id="snap">${this._cam ? `<img src="${this._snapUrl(this._cam)}" alt="" draggable="false">` : '<div class="e">NO CAMERAS</div>'}
            ${here.map(mark).join("")}${this._drawing ? draft : ""}</div>
          <div class="hint">${this._drawing ? (this._shape ? "TAP EACH CORNER OF THE AREA, THEN SAVE" : "DRAG ON THE PICTURE TO MARK THE SPOT") : "TIP: “DRAW IT” LETS YOU MARK IT YOURSELF — NO AI NEEDED"}</div>
        </div><div>
          <div class="h"><span class="t">TEACH A SPOT</span><i></i></div>
          <form class="add" data-form="spot"><ha-icon icon="mdi:tag-outline"></ha-icon><input name="name" id="spname" placeholder="Name, e.g. the drive" value="${esc(this._spName || "")}" autocomplete="off"></form>
          <form class="add" data-form="spot"><ha-icon icon="mdi:tag-multiple-outline"></ha-icon><input name="aliases" id="spalias" placeholder="Also called, e.g. driveway, parking space" value="${esc(this._spAlias || "")}" autocomplete="off"></form>
          <div class="row"><button class="btn sm" data-act="spfind">FIND IT WITH AI</button>
            <button class="btn ghost sm" data-act="spdraw">${this._drawing ? "STOP DRAWING" : "DRAW IT"}</button>
            ${this._drawing ? `<button class="btn ghost sm" data-act="spshape">${this._shape ? "BOX INSTEAD" : "SHAPE INSTEAD"}</button>` : ""}
            ${this._drawing && this._shape && pts.length ? `<button class="btn ghost sm" data-act="spundo">UNDO POINT</button>` : ""}
            ${this._drawing ? `<button class="btn sm" data-act="spsave" id="spsave"${ready ? "" : " disabled"}>SAVE SPOT</button>` : ""}</div>
          <div class="spmsg" id="spmsg">${esc(this._spMsg || "")}</div>
          <div class="h" style="margin-top:14px"><span class="t">ON THIS CAMERA</span><i></i><em>${here.length}</em></div>
          ${here.map((sp) => `<div class="r"><ha-icon icon="${sp.points ? "mdi:vector-polygon" : "mdi:selection-marker"}"></ha-icon><b>${esc(sp.name)}</b>
              <input class="alias" data-alias="${esc(sp.name)}" value="${esc(sp.aliases || "")}" placeholder="also called…">
              <a class="x" data-act="spforget" data-id="${esc(sp.name)}" title="Forget">✕</a></div>`).join("") || '<div class="e">NONE YET</div>'}
          <div class="h" style="margin-top:14px"><span class="t">ASK THE CAMERA</span><i></i></div>
          <form class="add" data-form="look"><ha-icon icon="mdi:eye-outline"></ha-icon><input name="q" placeholder="${here[0] ? `Is anything in ${esc(here[0].name)}?` : "What can you see?"}" autocomplete="off">
            <select name="spot"><option value="">Whole picture</option>${here.map((sp) => `<option>${esc(sp.name)}</option>`).join("")}</select><button class="btn sm" type="submit">ASK</button></form>
          <div class="spmsg ans" id="spans">${esc(this._lookAns || "")}</div>
        </div></div>`;
      this._$("spcam") && this._$("spcam").addEventListener("change", (ev) => { this._cam = ev.target.value; this._draft_box = null; this._pts = []; this._lookAns = ""; this._drawSpots(); });
      const nm = this._$("spname");
      if (nm) nm.addEventListener("input", (ev) => { this._spName = ev.target.value; });
      const al = this._$("spalias");
      if (al) al.addEventListener("input", (ev) => { this._spAlias = ev.target.value; });
      body.querySelectorAll("input.alias").forEach((inp) => inp.addEventListener("change", () => {
        this._hass.callWS({ type: "sentient/spots/aliases", name: inp.dataset.alias, aliases: inp.value })
          .then((r) => { this._spots = r.spots; }, (e) => window.alert(e.message || e));
      }));
      const snap = this._$("snap");
      if (snap && this._drawing) {
        const pos = (ev) => { const r = snap.getBoundingClientRect(); return [Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height))]; };
        if (this._shape) {
          snap.addEventListener("pointerdown", (ev) => { ev.preventDefault(); this._pts = [...(this._pts || []), pos(ev)]; this._drawSpots(); });
          return;
        }
        snap.addEventListener("pointerdown", (ev) => { ev.preventDefault(); snap.setPointerCapture(ev.pointerId); this._dragFrom = pos(ev); });
        snap.addEventListener("pointermove", (ev) => {
          if (!this._dragFrom) return;
          const [x, y] = pos(ev), [a, b] = this._dragFrom;
          this._draft_box = [Math.min(a, x), Math.min(b, y), Math.max(a, x), Math.max(b, y)];
          let el = snap.querySelector(".box.new");
          if (!el) { el = document.createElement("div"); el.className = "box new"; snap.appendChild(el); }
          const d = this._draft_box;
          Object.assign(el.style, { left: d[0] * 100 + "%", top: d[1] * 100 + "%", width: (d[2] - d[0]) * 100 + "%", height: (d[3] - d[1]) * 100 + "%" });
        });
        const end = () => { this._dragFrom = null; const b = this._$("spsave"); if (b && this._draft_box) b.disabled = false; };
        snap.addEventListener("pointerup", end);
        snap.addEventListener("pointercancel", end);
        snap.addEventListener("lostpointercapture", end);
      }
    }

    async _spotDo(kind) {
      const name = (this._spName || "").trim();
      const say = (t) => { this._spMsg = t; const m = this._$("spmsg"); if (m) m.textContent = t; };
      try {
        if (kind === "find") {
          if (!name) return say("Give the spot a name first.");
          say(`Looking for ${name}…`);
          const res = await this._hass.callWS({ type: "sentient/spots/learn", camera: this._cam, name });
          this._spots = res.spots; this._spName = ""; say(`Found ${res.result.name}.`);
        } else if (kind === "save") {
          if (!name) return say("Give the spot a name first.");
          const pts = this._pts || [];
          if (this._shape ? pts.length < 3 : !this._draft_box) return say(this._shape ? "Tap at least three corners first." : "Drag on the picture to mark it first.");
          const req = { type: "sentient/spots/set", camera: this._cam, name, box: this._draft_box || [0, 0, 1, 1], aliases: this._spAlias || "" };
          if (this._shape) req.points = pts;
          const res = await this._hass.callWS(req);
          this._spots = res.spots; this._spName = ""; this._spAlias = ""; this._draft_box = null; this._pts = []; this._drawing = false; say(`Saved ${res.result.name}.`);
        }
      } catch (e) { say(e.message || String(e)); }
      this._drawSide(); this._drawSpots();
    }



    // ---------- named routines ----------
    async _loadShortcuts() {
      try { this._sc = (await this._hass.callWS({ type: "sentient/shortcuts" })).items; } catch (e) { this._sc = []; }
      if (this._view === "settings") { this._drawSide(); if (this._group === "shortcuts" && !this._scEdit) this._drawShortcuts(); }
      else this._render();  // routine buttons on the overview
    }

    _drawShortcuts() {
      const rows = (this._sc || []).map((r) => `<div class="card ml"><div class="r"><ha-icon icon="mdi:playlist-play"></ha-icon><b>${esc(r.name)}</b>
            <s>${r.actions.length} STEP${r.actions.length === 1 ? "" : "S"}</s>
            <button class="btn sm" data-act="scrun" data-id="${esc(r.id)}">RUN</button>
            <a class="x" data-act="scedit" data-id="${esc(r.id)}" title="Edit">✎</a><a class="x" data-act="scdel" data-id="${esc(r.id)}" data-text="${esc(r.name)}" title="Delete">✕</a></div>
          <div class="mlsub">${r.aliases ? `<ha-icon icon="mdi:microphone"></ha-icon>“${esc(r.name)}”, ${esc(r.aliases)}` : `<ha-icon icon="mdi:microphone"></ha-icon>“${esc(r.name)}”`}</div>
          <div class="chips left">${r.check_open ? "<span>CHECKS DOORS &amp; WINDOWS</span>" : ""}${r.check_lights ? "<span>CHECKS LIGHTS</span>" : ""}${r.check_locks ? "<span>CHECKS LOCKS</span>" : ""}${r.inputs ? `<span>TAKES ${esc(r.inputs.split("\n").map((l) => l.split(":")[0].trim()).filter(Boolean).join(", ")).toUpperCase()}</span>` : ""}${r.say ? `<span>SAYS “${esc(r.say).toUpperCase()}”</span>` : ""}</div>
          ${r.last_run ? `<div class="hint">LAST RUN ${hm(r.last_run)}</div>` : ""}</div>`).join("");
      this._$("body").innerHTML = `<div class="bh"><ha-icon icon="mdi:playlist-play"></ha-icon><div><div class="bt">ROUTINES</div>
          <div class="bs">Named routines — “goodnight”, “good morning”, “leaving”. Say the name to any voice assistant, tap RUN, or call sentient.run_routine. They can check for anything left open or on first.</div></div></div>
        <div class="mllist">${rows || '<div class="e">NO ROUTINES YET</div>'}</div><div id="scedit"></div>
        <div class="bar"><span id="status"></span><button class="btn" data-act="scadd">+ NEW ROUTINE</button></div>`;
    }

    _openShortcutForm(item) {
      this._scEdit = item;
      const wrap = this._$("scedit");
      wrap.innerHTML = `<div class="card mlform"><div class="h"><span class="t">${item.id ? "EDIT" : "NEW"} ROUTINE</span><i></i></div>
        <div id="scformwrap" class="formwrap"><div class="e">LOADING…</div></div>
        <div class="row end"><span class="spmsg" id="scmsg"></span><button class="btn ghost sm" data-act="sccancel">CANCEL</button><button class="btn sm" data-act="scsave">SAVE</button></div></div>`;
      wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
      const LABELS = { name: "Name", aliases: "Other ways of asking (comma separated)", say: "Say when done",
        check_open: "First check for doors and windows left open", check_lights: "First check for lights left on", check_locks: "First check for anything unlocked",
        inputs: "Details it can be told — one per line, 'name: what it is' (e.g. 'rooms: which rooms — living room, kitchen'). Use them in actions as {{ rooms }}", actions: "Actions" };
      const schema = [
        { name: "name", required: true, selector: { text: {} } },
        { name: "aliases", selector: { text: {} } },
        { name: "say", selector: { text: {} } },
        { name: "check_open", selector: { boolean: {} } },
        { name: "check_lights", selector: { boolean: {} } },
        { name: "check_locks", selector: { boolean: {} } },
        { name: "inputs", selector: { text: { multiline: true } } },
        { name: "actions", selector: { action: {} } },
      ];
      loadForm().then((ok) => {
        const fw = this._$("scformwrap");
        if (!fw || this._scEdit !== item) return;
        if (!ok) { fw.innerHTML = '<div class="e">THE FORM COULDN\'T LOAD · REFRESH THE PAGE</div>'; return; }
        const form = document.createElement("ha-form");
        form.hass = this._hass; form.schema = schema; form.data = this._scDraft;
        form.computeLabel = (sc) => LABELS[sc.name] || sc.name;
        form.addEventListener("value-changed", (ev) => { this._scDraft = { ...ev.detail.value }; form.data = this._scDraft; });
        fw.innerHTML = ""; fw.appendChild(form); this._form = form;
      });
    }

    // ---------- alarms, offers, announcements, camera checks ----------
    async _loadItems(kind, extra) {
      this._it = this._it || {};
      try { this._it[kind] = await this._hass.callWS({ type: "sentient/items", kind, ...(extra || {}) }); } catch (e) { this._it[kind] = { items: [], meta: null, err: e.message || String(e) }; }
      if (this._view === "settings") { this._drawSide(); if (this._group === kind && !this._itEdit) this._drawItems(kind); }
    }

    _drawItems(kind) {
      const data = (this._it || {})[kind];
      const body = this._$("body");
      if (!data || !data.meta) { body.innerHTML = `<div class="e">${esc((data && data.err) || "LOADING…")}</div>`; return; }
      const m = data.meta;
      const when = (it) => kind === "alarms" && it._next ? `<s>NEXT ${esc(new Date(it._next).toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" }).toUpperCase())}</s>` : "";
      const rows = data.items.map((it) => `<div class="card ml${it.enabled === false ? " off" : ""}"><div class="r"><ha-icon icon="${m.icon}"></ha-icon><b>${esc(it.name)}</b>${when(it)}
            <label class="tg" title="On or off"><input type="checkbox" data-ittoggle="${esc(it.id)}"${it.enabled === false ? "" : " checked"}><i></i></label>
            <button class="btn ghost sm" data-act="ittest" data-id="${esc(it.id)}">TRY</button>
            <a class="x" data-act="itedit" data-id="${esc(it.id)}" title="Edit">✎</a><a class="x" data-act="itdel" data-id="${esc(it.id)}" data-text="${esc(it.name)}" title="Delete">✕</a></div>
          <div class="mlsub">${esc(it._summary || "")}</div>${it.last_answer ? `<div class="hint">LAST: ${esc(it.last_answer).toUpperCase()}</div>` : ""}</div>`).join("");
      body.innerHTML = `<div class="bh"><ha-icon icon="${m.icon}"></ha-icon><div><div class="bt">${esc(m.title).toUpperCase()}</div>
          <div class="bs">${esc(m.blurb)}</div></div></div>
        <div class="mllist">${rows || '<div class="e">NONE YET</div>'}</div><div id="itedit"></div>
        <div class="bar"><span id="status"></span><button class="btn" data-act="itadd">${esc(m.add)}</button></div>`;
      body.querySelectorAll("input[data-ittoggle]").forEach((cb) => cb.addEventListener("change", () => {
        const it = data.items.find((x) => x.id === cb.dataset.ittoggle);
        if (!it) return;
        this._hass.callWS({ type: "sentient/items/save", kind, item: { ...it, enabled: cb.checked } })
          .then((r) => { this._it[kind] = r; this._drawItems(kind); }, (e) => window.alert(e.message || e));
      }));
    }

    _openItemForm(kind, item) {
      const data = this._it[kind];
      this._itEdit = item;
      this._itDraft = { ...data.meta.defaults, ...item };
      const wrap = this._$("itedit");
      wrap.innerHTML = `<div class="card mlform"><div class="h"><span class="t">${item.id ? "EDIT" : "NEW"}</span><i></i></div>
        <div id="itformwrap" class="formwrap"><div class="e">LOADING…</div></div>
        <div class="row end"><span class="spmsg" id="itmsg"></span><button class="btn ghost sm" data-act="itcancel">CANCEL</button><button class="btn sm" data-act="itsave">SAVE</button></div></div>`;
      wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
      loadForm().then((ok) => {
        const fw = this._$("itformwrap");
        if (!fw || this._itEdit !== item) return;
        if (!ok) { fw.innerHTML = '<div class="e">THE FORM COULDN\'T LOAD · REFRESH THE PAGE</div>'; return; }
        const form = document.createElement("ha-form");
        form.hass = this._hass; form.schema = data.meta.schema; form.data = this._itDraft;
        form.computeLabel = (sc) => data.meta.labels[sc.name] || sc.name;
        form.addEventListener("value-changed", (ev) => { this._itDraft = { ...ev.detail.value }; form.data = this._itDraft; });
        fw.innerHTML = ""; fw.appendChild(form); this._form = form;
      });
    }

    // ---------- backup ----------
    _drawBackup() {
      this._$("body").innerHTML = `<div class="bh"><ha-icon icon="mdi:backup-restore"></ha-icon><div><div class="bt">BACKUP</div>
          <div class="bs">Everything Sentient has learned and been told — memories, camera spots, motion lights, routines, shushes and which learned routines you adopted — in one file. Restoring adds to what's here and never deletes.</div></div></div>
        <div class="card ml"><div class="r"><ha-icon icon="mdi:download"></ha-icon><b>Download a backup now</b><button class="btn sm" data-act="bkdown">DOWNLOAD</button></div></div>
        <div class="card ml" style="margin-top:12px"><div class="r"><ha-icon icon="mdi:upload"></ha-icon><b>Restore from a backup file</b>
          <label class="tg" title="Also restore settings"><input type="checkbox" id="bkset"><i></i></label><u>ALSO SETTINGS</u>
          <button class="btn sm" data-act="bkup">CHOOSE FILE…</button></div><input type="file" id="bkfile" accept="application/json,.json" hidden></div>
        <div class="card ml" style="margin-top:12px" id="bkauto"><div class="e">LOOKING FOR SAVED BACKUPS…</div></div>
        <div class="spmsg" id="bkmsg"></div>`;
      this._$("bkfile").addEventListener("change", (ev) => this._restore(ev.target.files[0]));
      this._loadBackups();
    }

    async _loadBackups() {
      let res = { items: [], on: false };
      try { res = await this._hass.callWS({ type: "sentient/backups" }); } catch (e) { /* not admin */ }
      const el = this._$("bkauto");
      if (!el) return;
      el.innerHTML = `<div class="r"><ha-icon icon="mdi:calendar-sync-outline"></ha-icon><b>Saved backups ${res.on ? "· WEEKLY IS ON" : "· WEEKLY IS OFF (Settings → Weekly backup)"}</b><button class="btn sm ghost" data-act="bksave">BACK UP NOW</button></div>`
        + (res.items.length ? res.items.map((b) => `<div class="r dim"><ha-icon icon="mdi:file-outline"></ha-icon><b>${esc(b.name)}</b><u>${Math.round(b.size / 1024)} KB</u>
          <button class="btn sm ghost" data-act="bkrestore" data-id="${esc(b.name)}">RESTORE</button></div>`).join("") : '<div class="e">NONE SAVED YET</div>');
    }

    async _download() {
      const data = await this._hass.callWS({ type: "sentient/export" });
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `sentient-backup-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
      this._$("bkmsg").textContent = `Saved ${data.memories.length} memories, ${data.spots.length} spots, ${data.motion_lights.rules.length} motion lights and ${data.routines.length} routines.`;
    }

    async _restore(file) {
      const msg = this._$("bkmsg");
      if (!file) return;
      try {
        const data = JSON.parse(await file.text());
        if (!window.confirm(`Restore ${file.name}? It adds to what's here and doesn't delete anything.`)) return;
        const res = await this._hass.callWS({ type: "sentient/import", data, settings: this._$("bkset").checked });
        msg.textContent = "Restored: " + Object.entries(res).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(", ") + ".";
        this._loadMemories(); this._loadSpots(); this._loadMotion(); this._loadShortcuts();
      } catch (e) { msg.textContent = e.message || String(e); }
    }

    // ---------- shushed ----------
    async _loadMutes(quiet, extra) {
      try { const r = await this._hass.callWS({ type: "sentient/mutes", ...(extra || {}) }); this._mutes = r.mutes; this._learnedQuiet = r.learned || []; } catch (e) { this._mutes = []; this._learnedQuiet = []; }
      if (this._view === "settings") { this._drawSide(); if (this._group === "mutes") this._drawMutes(); }
    }

    _drawMutes() {
      const rows = (this._mutes || []).map((m) => `<div class="r"><ha-icon icon="${m.kind === "entity" ? "mdi:volume-off" : "mdi:text-box-remove-outline"}"></ha-icon>
          <b>${esc(m.name)}</b><u>${m.until ? "UNTIL " + hm(m.until) : "UNTIL UNSHUSHED"}</u><a class="x" data-act="unmute" data-id="${esc(m.id)}" title="Unshush">✕</a></div>`).join("");
      this._$("body").innerHTML = `<div class="bh"><ha-icon icon="mdi:volume-mute"></ha-icon><div><div class="bt">SHUSHED</div>
          <div class="bs">Things the house won't mention — say “stop telling me about the washing machine” (or “for tonight”). Urgent and safety alerts always get through.</div></div></div>
        ${rows || '<div class="e">NOTHING SHUSHED</div>'}
        ${rows ? '<div class="bar"><span></span><button class="btn ghost" data-act="unmuteall">UNSHUSH EVERYTHING</button></div>' : ""}
        <div class="bh" style="margin-top:22px"><ha-icon icon="mdi:school-outline"></ha-icon><div><div class="bt">LEARNED FROM FEEDBACK</div>
          <div class="bs">What the house has picked up from shushes, hushing it straight after a remark, and declined or deleted routines. It fades by itself over a month or so; ✕ forgets it now.</div></div></div>
        ${(this._learnedQuiet || []).map((f) => `<div class="r"><ha-icon icon="${f.kind === "offers" ? "mdi:calendar-remove-outline" : "mdi:comment-off-outline"}"></ha-icon>
          <b>${esc(f.name)}</b><u>${esc(f.effect).toUpperCase()} · ${esc((f.why || [])[0] || "")}</u><a class="x" data-act="unlearnq" data-id="${esc(f.key)}" title="Forget this">✕</a></div>`).join("") || '<div class="e">NOTHING YET</div>'}`;
    }

    // ---------- motion lights ----------
    async _loadMotion() {
      try { this._ml = await this._hass.callWS({ type: "sentient/motion_lights" }); } catch (e) { this._ml = { rules: [] }; }
      if (this._view === "settings") { this._drawSide(); if (this._group === "motion" && !this._mlEdit) this._drawMotion(); }
      else this._drawMlCard();
    }

    _modeChip(key, icon, offLabel, onLabel) {
      const st = this._st(key);
      if (!st) return "";
      const on = st.state === "on";
      const until = on && st.attributes.until ? ` · TILL ${hm(st.attributes.until)}` : "";
      return `<button class="mode${on ? " on" : ""}" data-act="mode" data-id="${esc(st.entity_id)}"><ha-icon icon="${icon}"></ha-icon>${on ? onLabel + until : offLabel}</button>`;
    }

    _showChips() {
      const st = this._st("party_mode");
      if (!st) return "";
      const SHOWS = { disco: ["mdi:party-popper", "DISCO"], party: ["mdi:glass-cocktail", "PARTY"], rainbow: ["mdi:looks", "RAINBOW"],
        chill: ["mdi:weather-night", "CHILL"], halloween: ["mdi:halloween", "HALLOWEEN"], christmas: ["mdi:pine-tree", "CHRISTMAS"] };
      const on = st.state === "on", style = st.attributes.style;
      const list = (st.attributes.buttons || []).filter((s) => SHOWS[s]);
      if (on && style && !list.includes(style) && SHOWS[style]) list.push(style); // whatever is running can always be stopped here
      return list.map((s) => {
        const lit = on && style === s;
        const until = lit && st.attributes.until ? ` · TILL ${hm(st.attributes.until)}` : "";
        const ear = lit && st.attributes.following_sound ? ' <ha-icon icon="mdi:music-note"></ha-icon>' : "";
        return `<button class="mode${lit ? " on" : ""}" data-act="show" data-id="${s}"><ha-icon icon="${SHOWS[s][0]}"></ha-icon>${SHOWS[s][1]}${lit ? " ON" + until : ""}${ear}</button>`;
      }).join("");
    }

    _chimeChip() {
      const st = this._st("chime");
      if (!st) return "";
      const on = st.state === "on";
      return `<button class="mode chime${on ? " lit" : ""}" data-act="mode" data-id="${esc(st.entity_id)}"><ha-icon icon="${on ? "mdi:bell-ring-outline" : "mdi:bell-off-outline"}"></ha-icon>CHIME ${on ? "ON" : "OFF"}</button>`;
    }

    _drawEnergy() {
      const el = this._$("energy"), en = this._en;
      if (!el || !en || en.enabled === false || !(en.devices || []).length) { if (el) el.hidden = true; return; }
      el.hidden = false;
      const top = en.devices.slice(0, 5), max = Math.max(0.01, ...top.map((d) => Math.max(d.kwh, d.usual)));
      const pct = en.usual_kwh > 0 ? Math.round((en.total_kwh / en.usual_kwh) * 100) : null;
      el.innerHTML = this._h("ENERGY TODAY", `${en.total_kwh} KWH${en.cost_today != null ? ` · £${en.cost_today.toFixed(2)}` : ""}${pct != null ? ` · ${pct}% OF USUAL` : ""}`)
        + top.map((d) => `<div class="en${d.unusual ? " hot" : ""}"><span>${esc(d.name)}</span>
            <div class="bar2"><i style="width:${Math.round((d.kwh / max) * 100)}%"></i><b style="left:${Math.min(100, Math.round((d.usual / max) * 100))}%" title="Usual by now"></b></div>
            <u>${d.kwh}${d.unusual ? " ↑" : ""}</u></div>`).join("")
        + `<div class="hint">UP TO ${esc(en.up_to)} · MARKER = USUAL BY NOW</div>`;
    }

    _drawAppliances(list) {
      const el = this._$("apl");
      if (!el) return;
      if (!list.length) { el.hidden = true; return; }
      el.hidden = false;
      const dur = (m) => (m >= 60 ? `${Math.floor(m / 60)}H${m % 60 ? " " + (m % 60) + "M" : ""}` : `${m} MIN`);
      const running = list.filter((a) => a.running).length;
      el.innerHTML = this._h("APPLIANCES", running ? `${running} RUNNING` : "ALL IDLE") + list.slice(0, 6).map((a) => {
        const icon = a.entity_id.startsWith("vacuum.") ? "mdi:robot-vacuum" : /dish/i.test(a.name) ? "mdi:dishwasher" : /dry/i.test(a.name) ? "mdi:tumble-dryer" : "mdi:washing-machine";
        const tag = a.running ? `RUNNING ${dur(a.minutes_so_far || 0)}${a.done_about ? " · DONE ~" + esc(a.done_about) : ""}`
          : a.usual_minutes ? `USUALLY ${dur(a.usual_minutes)}` : `LEARNING · ${a.runs_seen}/3 RUNS`;
        return `<div class="r${a.running ? " on" : " dim"}"><ha-icon icon="${icon}"></ha-icon><b>${esc(a.name)}</b><u>${tag}</u></div>`;
      }).join("");
    }

    _drawInsights() {
      const el = this._$("ins"), r = this._ins;
      if (!el || !r) { if (el) el.hidden = true; return; }
      const h = r.heating || {}, nw = r.night_watch || {};
      const zones = (h.zones || []).filter((z) => z.warms_per_hour || z.comfortable_at);
      if (!zones.length && !nw.on) { el.hidden = true; return; }
      el.hidden = false;
      const up = { ...(h.usually_up || {}) }, wb = h.warm_by || {};
      if (wb.weekdays) up.weekdays = wb.weekdays; if (wb.weekends) up.weekends = wb.weekends;
      const upTxt = [up.weekdays && `WEEKDAYS ${up.weekdays}`, up.weekends && `WEEKENDS ${up.weekends}`].filter(Boolean).join(" · ");
      const zoneRows = zones.map((z) => `<div class="r"><ha-icon icon="mdi:radiator"></ha-icon><b>${esc(z.name)}</b>
          <u>${z.now != null ? z.now + "° NOW · " : ""}${z.warms_per_hour ? z.warms_per_hour + "°/H" : "LEARNING"}${z.comfortable_at ? " · USUALLY " + z.comfortable_at + "°" : ""}</u></div>
          ${z.needs_to_start ? `<div class="hint">TO BE ${z.comfortable_at}° BY ${esc(z.warm_by)} IT NEEDS TO START ${esc(z.needs_to_start)}${h.preheat ? " · WARMS UP EARLY ✓" : ""}</div>` : ""}`).join("");
      const recent = (nw.recent_unusual || []).slice(0, 3);
      const nwRows = nw.on ? `<div class="r"><ha-icon icon="mdi:shield-moon-outline"></ha-icon><b>Night watch</b>
          <u>${nw.learning ? `LEARNING · ${nw.days_of_history} DAYS` : `${(nw.watching || []).length} WATCHED`}${nw.announces ? " · SPEAKS" : ""}</u></div>`
        + (recent.length ? recent.map((t) => `<div class="hint">${esc(t).toUpperCase()}</div>`).join("") : '<div class="hint">NOTHING UNUSUAL IN THE LAST 3 DAYS</div>') : "";
      el.innerHTML = this._h("HEATING &amp; NIGHT WATCH", upTxt ? "WARM BY " + upTxt : "") + zoneRows + nwRows;
    }

    _security() {
      if (this._secCache && Date.now() - this._secCache.t < 2000 && this._secCache.h === this._hass) return this._secCache.v;
      const v = this._securityNow();
      this._secCache = { t: Date.now(), h: this._hass, v };
      return v;
    }

    _securityNow() {
      const OPEN = ["door", "window", "garage_door", "opening"], open = [], unlocked = [], alarms = [];
      for (const st of Object.values(this._hass.states)) {
        const [d] = st.entity_id.split(".");
        const dc = st.attributes.device_class, name = st.attributes.friendly_name || st.entity_id;
        if (d === "binary_sensor" && st.state === "on" && OPEN.includes(dc)) open.push({ name, icon: dc === "window" ? "mdi:window-open-variant" : "mdi:door-open" });
        else if (d === "cover" && ["open", "opening"].includes(st.state) && ["garage", "gate", "door"].includes(dc)) open.push({ name, icon: "mdi:garage-open" });
        else if (d === "lock" && ["unlocked", "open", "opening", "jammed"].includes(st.state)) unlocked.push({ name });
        else if (d === "alarm_control_panel") alarms.push({ name, state: st.state });
      }
      return { open, unlocked, alarms, key: open.map((o) => o.name).join(",") + "|" + unlocked.map((u) => u.name).join(",") + "|" + alarms.map((a) => a.state).join(",") };
    }

    _drawSecurity() {
      const el = this._$("sec");
      if (!el) return;
      const { open, unlocked, alarms } = this._security();
      const bad = open.length + unlocked.length;
      const alarm = alarms[0];
      const armed = alarm && alarm.state.startsWith("armed");
      el.innerHTML = this._h("SECURITY", bad ? `${bad} OPEN` : "ALL SHUT")
        + open.map((o) => `<div class="r warn"><ha-icon icon="${o.icon}"></ha-icon><b>${esc(o.name)}</b><u>OPEN</u></div>`).join("")
        + unlocked.map((u) => `<div class="r bad"><ha-icon icon="mdi:lock-open-variant-outline"></ha-icon><b>${esc(u.name)}</b><u>UNLOCKED</u></div>`).join("")
        + (bad ? "" : '<div class="r"><ha-icon icon="mdi:shield-check-outline"></ha-icon><b>Doors and windows shut</b></div>')
        + (alarm ? `<div class="r${armed ? "" : " dim"}"><ha-icon icon="${armed ? "mdi:shield-lock" : "mdi:shield-off-outline"}"></ha-icon><b>${esc(alarm.name)}</b><u>${esc(alarm.state.replace("armed_", "SET · ").replace("_", " ")).toUpperCase()}</u></div>` : "");
    }

    _drawMlCard() {
      const el = this._$("mlc"), ml = this._ml;
      if (!el || !ml || !ml.rules.length) { if (el) el.hidden = true; return; }
      el.hidden = false;
      const on = ml.rules.filter((r) => r.managing).length;
      el.innerHTML = this._h("MOTION LIGHTS", ml.dark ? (ml.late_night ? "LATE NIGHT · DIMMED" : ml.quiet ? "QUIET HOURS" : "DARK · ACTIVE") : "DAYLIGHT · RESTING")
        + ml.rules.map((r) => {
          const st = this._hass.states[r.light], lit = st && st.state === "on";
          return `<div class="r${r.enabled ? "" : " dim"}"><ha-icon icon="${lit ? "mdi:lightbulb-on" : "mdi:lightbulb-outline"}"${lit ? ' class="lit"' : ""}></ha-icon><b>${esc(this._name(r.light))}</b>
            <u>${!r.enabled ? "OFF" : r.managing ? (r.timer ? "COUNTING DOWN" : "ON BY MOTION") : r.respect_quiet && ml.quiet ? "QUIET" : "READY"}</u>
            <label class="tg"><input type="checkbox" data-mltoggle="${esc(r.id)}"${r.enabled ? " checked" : ""}><i></i></label></div>`;
        }).join("") + (on ? `<div class="hint">${on} ON BY MOTION NOW</div>` : "");
    }

    _name(id) { const s = this._hass.states[id]; return s ? (s.attributes.friendly_name || id) : id; }

    _areaOf(id) {
      const e = (this._hass.entities || {})[id];
      if (!e) return null;
      if (e.area_id) return e.area_id;
      const d = e.device_id && (this._hass.devices || {})[e.device_id];
      return d ? d.area_id : null;
    }

    _motionNear(light) {
      const area = this._areaOf(light);
      if (!area) return [];
      return Object.keys(this._hass.states).filter((id) => id.startsWith("binary_sensor.") && this._areaOf(id) === area
        && ["motion", "occupancy", "presence"].includes(this._hass.states[id].attributes.device_class));
    }

    _drawMotion() {
      if (this._view !== "settings" || this._group !== "motion") return;
      const ml = this._ml || { rules: [] };
      const pill = (on, text, cls) => `<span class="pill ${on ? cls || "on" : ""}">${text}</span>`;
      const rows = ml.rules.map((r) => {
        const st = this._hass.states[r.light];
        const lit = st && st.state === "on";
        const chips = [
          r.respect_quiet ? "OFF IN QUIET HOURS" : "IGNORES QUIET HOURS",
          r.only_dark ? (r.lux_sensor ? `BELOW ${r.lux_below} LX` : "SUNSET → SUNRISE") : "ANY TIME",
          `OFF AFTER ${r.off_after} MIN`,
          r.light.startsWith("light.") ? `${r.brightness}%${r.night_brightness ? ` · ${r.night_brightness}% LATE` : ""}` : "",
        ].filter(Boolean);
        return `<div class="card ml${r.enabled ? "" : " off"}"><div class="r"><ha-icon icon="${lit ? "mdi:lightbulb-on" : "mdi:lightbulb-outline"}" class="${lit ? "lit" : ""}"></ha-icon>
            <b>${esc(this._name(r.light))}</b><s>${r.managing ? (r.timer ? "ON · COUNTING DOWN" : "ON BY MOTION") : lit ? "ON" : "OFF"}</s>
            <label class="tg" title="${r.enabled ? "On" : "Off"}"><input type="checkbox" data-mltoggle="${esc(r.id)}"${r.enabled ? " checked" : ""}><i></i></label>
            <a class="x" data-act="mledit" data-id="${esc(r.id)}" title="Edit">✎</a><a class="x" data-act="mldel" data-id="${esc(r.id)}" data-text="${esc(this._name(r.light))}" title="Remove">✕</a></div>
          <div class="mlsub"><ha-icon icon="mdi:motion-sensor"></ha-icon>${r.motion.map((m) => esc(this._name(m))).join(" · ")}</div>
          <div class="chips left">${chips.map((c) => `<span>${c}</span>`).join("")}</div>
          ${r.last ? `<div class="hint">LAST: ${esc(r.last).toUpperCase()} · ${hm(r.last_time)}</div>` : ""}</div>`;
      }).join("");
      this._$("body").innerHTML = `<div class="bh"><ha-icon icon="mdi:motion-sensor"></ha-icon><div><div class="bt">MOTION LIGHTS</div>
          <div class="bs">Lights that come on with movement between sunset and sunrise and go off when it stops. Each light chooses whether quiet hours keep it off, and how dim it is late at night. A light someone switched on by hand is left alone.</div></div>
          <div class="chips">${pill(ml.dark, ml.dark ? "DARK" : "DAYLIGHT")}${pill(ml.quiet, "QUIET HOURS", "warn")}${pill(ml.late_night, "LATE NIGHT", "warn")}</div></div>
        <form class="add night" data-form="mlnight"><ha-icon icon="mdi:weather-night"></ha-icon><span>Late night (dimmed) from</span>
          <input type="time" name="start" value="${esc(ml.night_start || "23:00")}"><span>until</span><input type="time" name="end" value="${esc(ml.night_end || "06:00")}">
          <button class="btn ghost sm" type="submit">SAVE</button></form>
        <div class="mllist">${rows || '<div class="e">NO MOTION LIGHTS YET</div>'}</div>
        <div id="mledit"></div>
        <div class="bar"><span id="status"></span><button class="btn" data-act="mladd">+ ADD A MOTION LIGHT</button></div>`;
      if (this._mlEdit) this._openMotionForm(this._mlEdit);
    }

    _openMotionForm(rule) {
      this._mlEdit = rule;
      const wrap = this._$("mledit");
      if (!wrap) return;
      wrap.innerHTML = `<div class="card mlform"><div class="h"><span class="t">${rule.id ? "EDIT" : "NEW"} MOTION LIGHT</span><i></i></div>
        <div id="mlformwrap" class="formwrap"><div class="e">LOADING…</div></div>
        <div class="row end"><span class="spmsg" id="mlmsg"></span><button class="btn ghost sm" data-act="mlcancel">CANCEL</button><button class="btn sm" data-act="mlsave">SAVE</button></div></div>`;
      wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
      const LABELS = {
        light: "Light", motion: "Motion sensors", enabled: "On", respect_quiet: "Stay off during quiet hours",
        only_dark: "Only between sunset and sunrise", off_after: "Off after no movement for",
        brightness: "Brightness", night_brightness: "Late-night brightness",
        lux_sensor: "Light sensor (optional)", lux_below: "Dark below",
      };
      const HELP = {
        motion: "Any of these seeing movement turns the light on. Picked from the light's room when you choose the light.",
        respect_quiet: "Off: this light still comes on in quiet hours (handy for landings and stairs).",
        night_brightness: "Used late at night (the window above). 0 keeps the normal brightness.",
        lux_sensor: "For dim rooms: with a light sensor, “dark” means it reads below the level here, not just after sunset.",
      };
      const schema = [
        { name: "light", required: true, selector: { entity: { domain: ["light", "switch"] } } },
        { name: "motion", required: true, selector: { entity: { domain: "binary_sensor", multiple: true } } },
        { name: "enabled", selector: { boolean: {} } },
        { name: "respect_quiet", selector: { boolean: {} } },
        { name: "only_dark", selector: { boolean: {} } },
        { name: "off_after", selector: { number: { min: 0.5, max: 60, step: 0.5, unit_of_measurement: "min", mode: "box" } } },
        { name: "brightness", selector: { number: { min: 1, max: 100, unit_of_measurement: "%", mode: "slider" } } },
        { name: "night_brightness", selector: { number: { min: 0, max: 100, unit_of_measurement: "%", mode: "slider" } } },
        { name: "lux_sensor", selector: { entity: { domain: "sensor", device_class: "illuminance" } } },
        { name: "lux_below", selector: { number: { min: 1, max: 1000, unit_of_measurement: "lx", mode: "box" } } },
      ];
      loadForm().then((ok) => {
        const fw = this._$("mlformwrap");
        if (!fw || this._mlEdit !== rule) return;
        if (!ok) { fw.innerHTML = '<div class="e">THE FORM COULDN\'T LOAD · REFRESH THE PAGE</div>'; return; }
        const form = document.createElement("ha-form");
        form.hass = this._hass; form.schema = schema; form.data = this._mlDraft;
        form.computeLabel = (sc) => LABELS[sc.name] || sc.name;
        form.computeHelper = (sc) => HELP[sc.name] || "";
        form.addEventListener("value-changed", (ev) => {
          const v = { ...ev.detail.value };
          if (v.light && v.light !== this._mlDraft.light && !(v.motion || []).length) v.motion = this._motionNear(v.light);
          this._mlDraft = v; form.data = v;
        });
        fw.innerHTML = ""; fw.appendChild(form); this._form = form;
      });
    }

    async _motionDo(type, data, msgEl) {
      try {
        this._ml = await this._hass.callWS({ type, ...data });
        return true;
      } catch (e) {
        const m = this._$(msgEl || "status"); if (m) { m.textContent = e.message || String(e); m.className = "bad"; }
        return false;
      }
    }


    // ---------- timeline ----------
    async _openTimeline() {
      if (!this._tlSub && this._hass.connection) {
        this._tlSub = this._hass.connection.subscribeEvents((ev) => {
          if (!this._tlItems) return;
          this._tlItems.unshift(ev.data);
          if (this._view === "timeline") this._drawTimeline();
        }, "sentient_timeline");
      }
      try { this._tlItems = (await this._hass.callWS({ type: "sentient/timeline", hours: 72, limit: 400 })).items; }
      catch (e) { this._tlItems = []; }
      this._drawTimeline();
    }

    async _restyleSave(text, note) {
      try {
        await this._hass.callWS({ type: "sentient/feedback/style", text, note });
        this._thumbs = { ...(this._thumbs || {}), [text]: "styled" };
      } catch (e) { /* turned off or not loaded: nothing to do */ }
      this._restyle = null;
      this._render();
    }

    async _lastHour() {
      if (this._lh) { this._lh = null; this._render(); return; }
      try {
        const items = (await this._hass.callWS({ type: "sentient/timeline", hours: 1, limit: 30 })).items || [];
        this._lh = items.filter((i) => !["held", "digest"].includes(i.kind));
      } catch (e) { this._lh = []; }
      this._render();
    }

    _drawTimeline() {
      const KIND = {
        said: ["mdi:account-voice", "SAID"], held: ["mdi:volume-off", "HELD BACK"], mode: ["mdi:tune-variant", "MODE"], noticed: ["mdi:radar", "NOTICED"],
        visitor: ["mdi:doorbell-video", "VISITOR"], watch: ["mdi:bell-ring-outline", "WATCH"],
        light: ["mdi:motion-sensor", "LIGHT"], health: ["mdi:home-heart", "HEALTH"], digest: ["mdi:creation-outline", "DIGEST"], critical: ["mdi:fire-alert", "ALERT"], energy: ["mdi:lightning-bolt", "ENERGY"], security: ["mdi:shield-alert-outline", "SECURITY"], mind: ["mdi:head-lightbulb-outline", "MIND"], loop: ["mdi:sync", "FOLLOW-UP"], drive: ["mdi:car", "DRIVE"], appliance: ["mdi:washing-machine", "APPLIANCE"], animal: ["mdi:rabbit", "ANIMALS"], voice: ["mdi:gesture-tap-button", "VOICE ACTION"], clip: ["mdi:filmstrip-box", "CLIP"],
        heating: ["mdi:radiator", "HEATING"], learned: ["mdi:school-outline", "THIS WEEK"],
      };
      const dig = (this._tlItems || []).find((i) => i.kind === "digest");
      const digEl = this._$("tldig");
      if (!digEl._tap) { digEl._tap = true; digEl.addEventListener("click", (e) => { if (e.target.closest("b")) digEl.classList.toggle("open"); }); }
      digEl.innerHTML = `<ha-icon icon="mdi:creation-outline"></ha-icon><div class="dt">${dig ? `<b>${esc(dig.text)}</b><i>DIGEST · ${dayNameShort(dig.time)} ${hm(dig.time)}</i>` : "<b>Ask for a short AI summary of the last 24 hours.</b>"}</div>
        <button class="btn sm" data-act="digest"${this._digBusy ? " disabled" : ""}>${this._digBusy ? "THINKING…" : "SUMMARISE 24H"}</button>
        <button class="btn sm ghost" data-act="weekly">THIS WEEK</button>`;
      const want = this._tlKind || "all";
      const items = (this._tlItems || []).filter((i) => (want === "all" ? i !== dig : i.kind === want));
      const counts = {};
      (this._tlItems || []).forEach((i) => { counts[i.kind] = (counts[i.kind] || 0) + 1; });
      this._$("tlf").innerHTML = [["all", "ALL", (this._tlItems || []).length], ...Object.entries(KIND).map(([k, v]) => [k, v[1], counts[k] || 0])]
        .map(([k, label, n]) => `<button class="${k === want ? "on" : ""}" data-act="tlkind" data-id="${k}">${label} <b>${n}</b></button>`).join("");
      this._$("tlcount").textContent = "LAST 3 DAYS";
      const dayName = (d) => {
        const today = new Date(); today.setHours(0, 0, 0, 0);
        const that = new Date(d); that.setHours(0, 0, 0, 0);
        const diff = Math.round((today - that) / 864e5);
        return diff === 0 ? "TODAY" : diff === 1 ? "YESTERDAY" : that.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" }).toUpperCase();
      };
      let last = "", html = "";
      for (const i of items) {
        const d = dayName(i.time);
        if (d !== last) { html += `<div class="day">${d}</div>`; last = d; }
        const [icon, label] = KIND[i.kind] || ["mdi:circle-small", i.kind.toUpperCase()];
        html += `<div class="tli k-${esc(i.kind)}"><span class="tm">${hm(i.time)}</span><span class="dot"><ha-icon icon="${icon}"></ha-icon></span>
          <div class="tx"><b>${esc(i.text)}</b>${i.detail ? `<i>${esc(i.detail)}</i>` : ""}</div><s>${label}</s></div>`;
      }
      this._$("tllist").innerHTML = html || '<div class="e">NOTHING YET · THE TIMELINE FILLS AS THE HOUSE SPEAKS, NOTICES AND ACTS</div>';
    }

    // ---------- events ----------
    _click(ev) {
      const el = ev.target.closest("[data-act]");
      if (!el) return;
      const { act, id, text } = el.dataset;
      if (act === "forget") this._call("forget", { what: id }, `Forget: ${text}?`);
      else if (act === "cancel") this._call("cancel_watch", { what: id }, `Stop: ${text}?`);
      else if (act === "wonderask") this._call("wonder", { ask_now: true });
      else if (act === "lasthour") this._lastHour();
      else if (act === "bksave") this._hass.callWS({ type: "sentient/backups/save" }).then((r) => { this._$("bkmsg").textContent = `Saved ${r.name}.`; this._loadBackups(); }, (e) => { this._$("bkmsg").textContent = e.message || String(e); });
      else if (act === "bkrestore") {
        if (!window.confirm(`Restore ${id}? It adds to what's here and doesn't delete anything.`)) return;
        this._hass.callWS({ type: "sentient/backups/restore", name: id, settings: this._$("bkset").checked }).then((res) => {
          this._$("bkmsg").textContent = "Restored: " + Object.entries(res).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(", ") + ".";
          this._loadMemories(); this._loadSpots(); this._loadMotion(); this._loadShortcuts();
        }, (e) => { this._$("bkmsg").textContent = e.message || String(e); });
      }
      else if (act === "sugyes" || act === "sugno") {
        this._sug = (this._sug || []).filter((s) => s.id !== id);
        this._render();
        this._hass.callWS({ type: "sentient/suggestions/act", suggestion: id, accept: act === "sugyes" }).catch(() => {});
      }
      else if (act === "restyle") { this._restyle = this._restyle === text ? null : text; this._render(); }
      else if (act === "restylex") { this._restyle = null; this._render(); }
      else if (act === "restyleset") this._restyleSave(text, id);
      else if (act === "wonderx") this._call("wonder", { dismiss: text });
      else if (act === "fux") this._call("cancel_follow_up", { about: id }, `Stop checking: ${text}?`);
      else if (act === "adopt") this._call("adopt_routine", { routine: id }, text);
      else if (act === "unlearnq") this._loadMutes(false, { unlearn: id });
      else if (act === "unlearn") this._call("forget_routine", { routine: id }, `Delete this for good? It won't be learned or offered again.\n\n${text}`);
      else if (act === "unforget") this._call("forget_routine", { restore: true }, text);
      else if (act === "goto") { this._group = id; this._show("settings", true); }
      else if (act === "wizard") this._openWizard();
      else if (act === "wizback") this._wizGo(this._wiz.i - 1);
      else if (act === "wiznext") this._wizNext();
      else if (act === "wizlater") this._closeWizard();
      else if (act === "wiztest") this._wizTest();
      else if (act === "wizdone") this._wizFinish();
      else if (act === "thumb") {
        const up = id === "up";
        this._thumbs = { ...(this._thumbs || {}), [text]: id };
        this._hass.callWS({ type: "sentient/feedback/thumb", text, source: el.dataset.src || null, up }).then(() => this._render(), () => {});
        this._render();
      }
      else if (act === "menu") {
        if (!this._openMenus) this._openMenus = new Set();
        if (this._openMenus.has(id)) this._openMenus.delete(id); else this._openMenus.add(id);
        this._drawSide();
      } else if (act === "group") {
        if (this._dirty && !window.confirm("Leave without saving your changes?")) return;
        this._dirty = false; this._group = id; this._editing = null; this._mlEdit = null; this._scEdit = null;
        if (this._phone() && this._openMenus) this._openMenus.clear();
        this._drawSide(); this._drawBody();
        if (this._phone()) this._$("body").scrollIntoView({ behavior: "smooth", block: "start" });
      } else if (act === "save") this._save();
      else if (act === "reset") this._drawBody();
      else if (act === "edit") { this._editing = id; this._drawMemories(); }
      else if (act === "noedit") { this._editing = null; this._drawMemories(); }
      else if (act === "snap") { this._snapT = Date.now(); this._drawSpots(); }
      else if (act === "spdraw") { this._drawing = !this._drawing; if (!this._drawing) { this._draft_box = null; this._pts = []; } this._drawSpots(); }
      else if (act === "spshape") { this._shape = !this._shape; this._draft_box = null; this._pts = []; this._drawSpots(); }
      else if (act === "spundo") { this._pts = (this._pts || []).slice(0, -1); this._drawSpots(); }
      else if (act === "spfind") this._spotDo("find");
      else if (act === "spsave") this._spotDo("save");
      else if (act === "mode") { this._hass.callService("switch", "toggle", { entity_id: id }); }
      else if (act === "show") {
        const st = this._st("party_mode");
        const stop = st && st.state === "on" && st.attributes.style === id;
        this._hass.callService("sentient", "party", stop ? { on: false } : { style: id }).catch((e) => window.alert(e.message || e));
      }
      else if (act === "weekly") {
        this._hass.callWS({ type: "sentient/weekly" }).then(() => { this._tlKind = "learned"; this._drawTimeline(); }, (e) => window.alert(e.message || e));
      }
      else if (act === "digest") {
        this._digBusy = true; this._drawTimeline();
        this._hass.callWS({ type: "sentient/digest", hours: 24 }).catch((e) => window.alert(e.message || e))
          .finally(() => { this._digBusy = false; this._drawTimeline(); });
      }
      else if (act === "itadd") { this._openItemForm(this._group, {}); }
      else if (act === "itedit") { const it = ((this._it || {})[this._group] || { items: [] }).items.find((x) => x.id === id); if (it) this._openItemForm(this._group, it); }
      else if (act === "itcancel") { this._itEdit = null; this._form = null; this._drawItems(this._group); }
      else if (act === "itsave") {
        const kind = this._group, msg = this._$("itmsg");
        this._hass.callWS({ type: "sentient/items/save", kind, item: this._itDraft })
          .then((r) => { this._it[kind] = r; this._itEdit = null; this._form = null; this._drawSide(); this._drawItems(kind); },
            (e) => { if (msg) msg.textContent = e.message || String(e); });
      } else if (act === "itdel") {
        if (!window.confirm(`Delete ${text}?`)) return;
        const kind = this._group;
        this._hass.callWS({ type: "sentient/items/delete", kind, item_id: id }).then((r) => { this._it[kind] = r; this._drawSide(); this._drawItems(kind); });
      } else if (act === "ittest") {
        const kind = this._group, st = this._$("status");
        if (st) st.textContent = "TRYING…";
        this._hass.callWS({ type: "sentient/items/test", kind, item_id: id }).then((r) => {
          this._it[kind] = r; this._drawItems(kind);
          const res = r.result || {}, s2 = this._$("status");
          if (s2) s2.textContent = res.match !== undefined ? (res.match ? "YES — " : "NO — ") + (res.seen || "") : res.spoken === false ? `HELD BACK: ${(res.reason || "").toUpperCase()}` : "DONE";
        }, (e) => { const s2 = this._$("status"); if (s2) s2.textContent = e.message || String(e); });
      }
      else if (act === "scadd") { this._scDraft = { name: "", aliases: "", say: "", check_open: false, check_lights: false, check_locks: false, inputs: "", actions: [] }; this._openShortcutForm({}); }
      else if (act === "scedit") { const r = (this._sc || []).find((x) => x.id === id); if (r) { this._scDraft = { ...r }; this._openShortcutForm(r); } }
      else if (act === "sccancel") { this._scEdit = null; this._form = null; this._drawShortcuts(); }
      else if (act === "scsave") {
        const item = {};
        ["id", "name", "aliases", "say", "check_open", "check_lights", "check_locks", "inputs", "actions"].forEach((k) => { if (this._scDraft[k] !== undefined && this._scDraft[k] !== null) item[k] = this._scDraft[k]; });
        this._hass.callWS({ type: "sentient/shortcuts/save", item }).then((r) => { this._sc = r.items; this._scEdit = null; this._form = null; this._drawSide(); this._drawShortcuts(); },
          (e) => { const m = this._$("scmsg"); if (m) m.textContent = e.message || String(e); });
      } else if (act === "scdel") {
        if (!window.confirm(`Delete the routine “${text}”?`)) return;
        this._hass.callWS({ type: "sentient/shortcuts/delete", item_id: id }).then((r) => { this._sc = r.items; this._drawSide(); this._drawShortcuts(); });
      } else if (act === "scrun") {
        const r = (this._sc || []).find((x) => x.id === id);
        this._hass.callService("sentient", "run_routine", { name: r ? r.name : id }).then(() => this._loadShortcuts(), (e) => window.alert(e.message || e));
      } else if (act === "bkdown") this._download().catch((e) => { this._$("bkmsg").textContent = e.message || String(e); });
      else if (act === "bkup") this._$("bkfile").click();
      else if (act === "unmute") this._loadMutes(false, { remove: id });
      else if (act === "unmuteall") this._loadMutes(false, { clear: true });
      else if (act === "critest") {
        if (!window.confirm("Say a test safety alert on every speaker now?")) return;
        this._hass.callWS({ type: "sentient/critical/test" }).then(() => this._status("TEST ALERT SENT ✓", "ok"), (e) => this._status(e.message || String(e), "bad"));
      }
      else if (act === "tlkind") { this._tlKind = id; this._drawTimeline(); }
      else if (act === "mladd") {
        this._mlDraft = { enabled: true, respect_quiet: true, only_dark: true, off_after: 3, brightness: 100, night_brightness: 10, lux_below: 30, motion: [] };
        this._openMotionForm({});
      } else if (act === "mledit") {
        const r = (this._ml.rules || []).find((x) => x.id === id);
        if (r) { this._mlDraft = { ...r }; this._openMotionForm(r); }
      } else if (act === "mlcancel") { this._mlEdit = null; this._form = null; this._drawMotion(); }
      else if (act === "mlsave") {
        const keys = ["id", "light", "motion", "enabled", "respect_quiet", "only_dark", "off_after", "brightness", "night_brightness", "lux_sensor", "lux_below"];
        const rule = {};
        keys.forEach((k) => { if (this._mlDraft[k] !== undefined && this._mlDraft[k] !== null) rule[k] = this._mlDraft[k]; });
        if (!rule.lux_sensor) rule.lux_sensor = null;
        this._motionDo("sentient/motion_lights/save", { rule }, "mlmsg").then((ok) => {
          if (ok) { this._mlEdit = null; this._form = null; this._drawSide(); this._drawMotion(); }
        });
      } else if (act === "mldel") {
        if (!window.confirm(`Stop ${text} following motion?`)) return;
        this._motionDo("sentient/motion_lights/delete", { rule_id: id }).then(() => { this._drawSide(); this._drawMotion(); });
      }
      else if (act === "spforget") {
        if (!window.confirm(`Forget the spot “${id}”?`)) return;
        this._hass.callWS({ type: "sentient/spots/forget", name: id }).then((r) => { this._spots = r.spots; this._drawSide(); this._drawSpots(); }, (e) => window.alert(e.message || e));
      }
    }

    async _submit(ev) {
      const form = ev.target;
      if (!form.dataset || !form.dataset.form) return;
      ev.preventDefault();
      if (form.dataset.form === "mlnight") {
        const ok = await this._motionDo("sentient/motion_lights/night", { start: form.elements.start.value, end: form.elements.end.value });
        if (ok) { this._drawMotion(); const st = this._$("status"); if (st) { st.textContent = "LATE-NIGHT WINDOW SAVED ✓"; st.className = "ok"; } }
        return;
      }
      if (form.dataset.form === "restyle") {
        const note = (new FormData(form).get("note") || "").toString().trim();
        if (note) this._restyleSave(form.dataset.text, note);
        return;
      }
      if (form.dataset.form === "remind") {
        const message = form.elements.msg.value.trim(), when = form.elements.when.value.trim().toLowerCase();
        if (!message) return;
        const data = { message };
        let m;
        if ((m = when.match(/^(\d+(?:\.\d+)?)\s*(m|min|mins|minutes?)?$/))) data.minutes = parseFloat(m[1]);
        else if ((m = when.match(/^(\d+(?:\.\d+)?)\s*(h|hr|hrs|hours?)$/))) data.minutes = parseFloat(m[1]) * 60;
        else if (when) data.at = when;
        else data.minutes = 30;
        try {
          await this._hass.callService("sentient", "remind", data);
          form.elements.msg.value = ""; form.elements.when.value = "";
          if (this.shadowRoot.activeElement) this.shadowRoot.activeElement.blur();
          this._render();
        } catch (e) { window.alert(e.message || String(e)); }
        return;
      }
      if (form.dataset.form === "spot") { this._spotDo(this._drawing && (this._draft_box || (this._pts || []).length >= 3) ? "save" : "find"); return; }
      if (form.dataset.form === "look") {
        const q = form.elements.q.value.trim() || form.elements.q.placeholder, spot = form.elements.spot.value;
        const out = this._$("spans"); out.textContent = "LOOKING…";
        try {
          const res = await this._hass.callWS({ type: "sentient/look", camera: this._cam, question: q, ...(spot ? { spot } : {}) });
          this._lookAns = res.result.answer || "No answer.";
        } catch (e) { this._lookAns = e.message || String(e); }
        out.textContent = this._lookAns;
        return;
      }
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
:host{display:block;height:100vh;height:100dvh;overflow-y:auto;overscroll-behavior-y:contain;width:100%;max-width:100vw;overflow-x:hidden;color:#dff6fa;color-scheme:dark;-webkit-tap-highlight-color:transparent;font-family:var(--ha-font-family-body, Roboto, sans-serif);
  background:radial-gradient(ellipse at 20% 0%, rgba(0,255,255,0.07), transparent 50%), radial-gradient(ellipse at 90% 100%, rgba(0,140,255,0.06), transparent 50%), #070b16}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
input,textarea,select{-webkit-appearance:none;appearance:none}
input:focus,textarea:focus{background-color:transparent}
input:-webkit-autofill{-webkit-box-shadow:0 0 0 40px #0c1424 inset;-webkit-text-fill-color:#e6f5fa}
img,video{max-width:100%}
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
.ask input,.add input,.find input{flex:1;min-width:0;width:0;background:none;border:none;outline:none}
.cols>*,.set>*{min-width:0}
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
.head{display:flex;align-items:center;gap:20px}
.head img{width:128px;height:128px;border-radius:18px;box-shadow:0 0 24px rgba(0,255,247,.35);flex:none;object-fit:cover}
.m{flex:1;min-width:0}
.ti{font-size:26px;font-weight:300;letter-spacing:.45em;color:#dff6fa;text-shadow:0 0 14px rgba(0,255,247,.6)}
.sub{margin-top:4px;font-size:10px;font-weight:700;letter-spacing:.22em;color:rgba(150,220,235,.75)}
.st{display:flex;gap:8px;margin-top:12px}
.s{flex:1 1 0;min-width:0;padding:7px 9px;border-radius:12px;border:1px solid rgba(0,255,247,.22);background:rgba(0,255,247,.06)}
.s[data-act]{cursor:pointer} .s[data-act]:hover{border-color:rgba(0,255,247,.55)}
.s .l{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.s .n{font-size:20px;color:#fff;text-shadow:0 0 8px rgba(0,255,247,.5)} .s .l{font-size:8.5px;font-weight:700;letter-spacing:.16em;color:rgba(150,220,235,.7);margin-top:2px}
.rm{width:250px;flex:none} .rm .t{font-size:10px;font-weight:700;letter-spacing:.24em;color:#00fff7;margin-bottom:8px}
.b{display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:9px;font-weight:700;letter-spacing:.1em;color:rgba(180,220,235,.8)}
.b span{width:92px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis} .b div{flex:1;height:6px;border-radius:3px;background:rgba(0,255,247,.1);overflow:hidden}
.b i{display:block;height:100%;border-radius:3px;background:linear-gradient(90deg,#00a8ff,#00fff7);box-shadow:0 0 6px rgba(0,255,247,.6)}
.cols{display:grid;grid-template-columns:repeat(3, minmax(0, 1fr));gap:16px;margin-top:16px;align-items:start}
.stack{display:flex;flex-direction:column;gap:10px}
.wiz{position:fixed;inset:0;z-index:20;display:flex;align-items:flex-start;justify-content:center;padding:6vh 16px 16px;background:rgba(3,6,14,.78);backdrop-filter:blur(6px);overflow-y:auto}
.wizc{width:100%;max-width:760px;padding:20px 22px}
.wizh{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
.wizdots{display:flex;gap:6px} .wizdots i{width:22px;height:4px;border-radius:2px;background:rgba(0,255,247,.15)} .wizdots i.done{background:rgba(0,255,247,.5)} .wizdots i.on{background:#00fff7;box-shadow:0 0 8px rgba(0,255,247,.7)}
.wizn{font-size:9.5px;font-weight:700;letter-spacing:.2em;color:rgba(150,220,235,.6)}
.wizsug{display:flex;align-items:center;gap:8px;margin:12px 0 4px;padding:8px 12px;border-radius:10px;font-size:10px;font-weight:700;letter-spacing:.14em;color:#7fefff;border:1px dashed rgba(0,255,247,.35);background:rgba(0,255,247,.05)} .wizsug ha-icon{--mdc-icon-size:16px;color:#00fff7}
.wizbar{display:flex;align-items:center;gap:10px;margin-top:18px;padding-top:14px;border-top:1px solid rgba(0,255,247,.15)} .wizbar span{flex:1;font-size:10px;font-weight:700;letter-spacing:.14em} .wizbar span.warn{color:#ffb86b}
.wizbar .btn ha-icon{--mdc-icon-size:14px;vertical-align:-2px}
.wizchk{display:flex;gap:10px;align-items:flex-start;margin-top:16px;font-size:13px;line-height:1.4;color:#dff6fa;cursor:pointer} .wizchk input{-webkit-appearance:checkbox;appearance:auto;margin-top:3px;accent-color:#00fff7}
.wizres{margin-top:12px}
.wizbtn{display:flex;align-items:center;gap:8px;justify-content:center;width:100%;margin:2px 0 10px} .wizbtn ha-icon{--mdc-icon-size:16px}
.th{width:22px;height:22px;opacity:.55} .th:hover{opacity:1;color:#00fff7} .th ha-icon{--mdc-icon-size:14px} .thd{text-decoration:none;font-size:12px}
.hint.meter{margin-top:8px;padding-top:8px;border-top:1px solid rgba(0,255,247,.1)}
/* Big screens (tablets, desktops): the overview fits the screen and each column scrolls on its own */
@media (min-width: 1101px) and (min-height: 600px){
  #ov:not([hidden]){display:flex;flex-direction:column;height:calc(100vh - 56px);height:calc(100dvh - 56px)}
  #ov .cols{flex:1;min-height:0;align-items:stretch;margin-top:14px}
  #ov .col{min-height:0;overflow-y:auto;overscroll-behavior:contain;padding:2px 6px 14px;margin:-2px -6px 0;scrollbar-width:thin;scrollbar-color:rgba(0,255,247,.28) transparent}
  #ov .col>*,#ov .col .stack>*{flex-shrink:0}
  #ov .col::-webkit-scrollbar{width:6px} #ov .col::-webkit-scrollbar-thumb{background:rgba(0,255,247,.28);border-radius:3px}
}
.h{display:flex;align-items:center;gap:10px;margin-bottom:10px} .h.tight{margin:0}
.h .t{flex:none;white-space:nowrap;font-size:12px;font-weight:700;letter-spacing:.26em;color:#00fff7;text-shadow:0 0 6px rgba(0,255,247,.5)}
.h i{flex:1;height:1px;background:linear-gradient(90deg,rgba(0,255,247,.45),transparent)}
.h em{font-style:normal;font-size:9px;font-weight:700;letter-spacing:.14em;padding:3px 9px;border-radius:10px;color:#7fefff;border:1px solid rgba(0,255,247,.45);background:rgba(0,255,247,.08);white-space:nowrap;min-width:0;overflow:hidden;text-overflow:ellipsis}
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
.rt .x{margin-left:8px} .lnk{display:inline-block;margin-top:6px;font-size:11px;letter-spacing:.12em;color:var(--ac,#00fff7);cursor:pointer;opacity:.8} .lnk:hover{opacity:1}
.hint{font-size:8.5px;letter-spacing:.14em;color:rgba(150,220,235,.55);margin:6px 2px 0}
.e{padding:18px;text-align:center;font-size:11px;letter-spacing:.2em;color:rgba(150,200,215,.45)}
/* settings */
.set{display:grid;grid-template-columns:300px minmax(0,1fr);gap:16px;align-items:start}
.side{position:sticky;top:74px;padding:12px;display:flex;flex-direction:column;gap:4px}
.sh{font-size:11px;font-weight:700;letter-spacing:.32em;color:#00fff7;padding:4px 10px 10px;text-shadow:0 0 6px rgba(0,255,247,.5)}
.menu{border-radius:14px;border:1px solid rgba(0,255,247,.08);background:rgba(0,255,247,.015)}
.menu.here{border-color:rgba(0,255,247,.22)}
.mh{display:flex;align-items:center;gap:10px;width:100%;padding:11px 12px;border-radius:14px;text-align:left;font-size:11.5px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;color:#bfefff}
.mh ha-icon{--mdc-icon-size:19px;color:#5fd8e6;flex:none} .mh span{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mh u{text-decoration:none;font-size:10px;letter-spacing:0;font-weight:600;color:rgba(150,220,235,.7);border:1px solid rgba(0,255,247,.2);border-radius:9px;padding:1px 7px}
.mh .chev{--mdc-icon-size:18px;transition:transform .2s} .menu.open .chev{transform:rotate(180deg)}
.mh:hover{background:rgba(0,255,247,.05)} .menu.here .mh>ha-icon:first-child{color:#00fff7}
.mi{display:none;flex-direction:column;gap:2px;padding:0 6px 8px 14px} .menu.open .mi{display:flex}
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
.add .when{flex:none;width:120px;border-left:1px solid rgba(0,255,247,.2);padding-left:10px}
.find{border-style:solid;border-color:rgba(0,255,247,.15)}
.list .r{padding:9px 10px}
.r.mem b{cursor:text} .r.mem b:hover{color:#fff}
.r.edit{border-color:rgba(0,255,247,.5);background:rgba(0,255,247,.08)}
.r.edit input{flex:1;min-width:0;background:rgba(0,0,0,.25);border:1px solid rgba(0,255,247,.3);border-radius:8px;padding:6px 8px;outline:none}
.btn{flex:none;font-size:10px;font-weight:700;letter-spacing:.16em;padding:8px 16px;border-radius:10px;color:#041016;background:linear-gradient(90deg,#00c8ff,#00fff7);box-shadow:0 0 12px rgba(0,255,247,.35)}
.btn.sm{padding:6px 12px} .btn[disabled]{opacity:.35;cursor:default} .btn.ghost{color:#7fefff;background:none;border:1px solid rgba(0,255,247,.35);box-shadow:none}
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
  --ha-color-form-background-hover:rgba(0,255,247,.08);--ha-color-form-background-disabled:rgba(0,255,247,.02);
  --ha-color-fill-neutral-quiet-active:rgba(0,255,247,.12);--ha-color-fill-neutral-normal-hover:rgba(0,255,247,.14);--ha-color-fill-neutral-normal-active:rgba(0,255,247,.18);
  --ha-color-fill-primary-quiet-hover:rgba(0,255,247,.14);--ha-color-fill-primary-quiet-active:rgba(0,255,247,.18);
  --mdc-ripple-color:#00fff7;--mdc-ripple-hover-opacity:.06;--mdc-ripple-focus-opacity:.1;--mdc-ripple-press-opacity:.12;
  --mdc-text-field-hover-line-color:#00fff7;--mdc-text-field-disabled-fill-color:rgba(0,255,247,.02);--mdc-select-hover-line-color:#00fff7;
  --input-hover-line-color:#00fff7;--input-disabled-fill-color:rgba(0,255,247,.02);--input-fill-color-hover:rgba(0,255,247,.08);
  --wa-form-control-background-color-hover:rgba(0,255,247,.08);--wa-color-neutral-fill-quiet-hover:rgba(0,255,247,.1);
  --primary-background-color:#0c1424;--secondary-background-color:#0c1424;--code-editor-background-color:#0c1424;--mdc-theme-background:#0c1424;
  --ha-dialog-surface-background:#0c1424;--mdc-dialog-content-ink-color:#e6f5fa;--list-item-hover-background-color:rgba(0,255,247,.08);
  color:#e6f5fa}
.formwrap ha-form{display:block;max-width:880px}
.spotgrid{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:20px;align-items:start}
.restyle{flex-wrap:wrap;gap:6px} .restyle form{display:flex;gap:6px;flex:1;min-width:180px} .restyle input{flex:1;min-width:0;background:transparent;border:1px solid rgba(0,255,247,.3);border-radius:8px;color:inherit;padding:6px 8px}
.row{display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap} .row select{flex:1;min-width:0}
.snap{position:relative;border-radius:14px;overflow:hidden;border:1px solid rgba(0,255,247,.3);background:#000;aspect-ratio:16/9;user-select:none;touch-action:none}
.snap img{display:block;width:100%;height:100%;object-fit:fill;pointer-events:none}
.snap.drawing{cursor:crosshair;border-color:#00fff7;box-shadow:0 0 16px rgba(0,255,247,.35)}
.box{position:absolute;border:2px solid #00fff7;border-radius:6px;box-shadow:0 0 10px rgba(0,255,247,.6), inset 0 0 18px rgba(0,255,247,.15);pointer-events:none}
.box span{position:absolute;left:-2px;top:-22px;font-size:10px;font-weight:700;letter-spacing:.12em;padding:2px 7px;border-radius:6px;background:#00fff7;color:#041016;white-space:nowrap;text-transform:uppercase}
.shape{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;overflow:visible}
.shape polygon{fill:rgba(0,255,247,.12);stroke:#00fff7;stroke-width:.5;vector-effect:non-scaling-stroke;filter:drop-shadow(0 0 4px rgba(0,255,247,.7))}
.shape.new polygon{fill:rgba(255,190,85,.12);stroke:#ffbe55;stroke-dasharray:4 3}
.dotp{position:absolute;width:10px;height:10px;margin:-5px 0 0 -5px;border-radius:50%;background:#ffbe55;box-shadow:0 0 6px #ffbe55;pointer-events:none}
.box.lbl span{top:-22px}
.r input.alias{flex:1;min-width:80px;font-size:11.5px;background:rgba(0,0,0,.2);border:1px solid rgba(0,255,247,.18);border-radius:8px;padding:4px 8px;outline:none}
.box.new{border-style:dashed;border-color:#ffbe55;box-shadow:0 0 10px rgba(255,190,85,.5)} .box.new span{background:#ffbe55}
.spmsg{min-height:16px;font-size:12px;color:#7fefff;margin:4px 2px} .spmsg.ans{font-size:13.5px;line-height:1.45;color:#e6f5fa}
@media (max-width: 1100px){ .spotgrid{grid-template-columns:1fr} }
.pill{font-size:8.5px;font-weight:700;letter-spacing:.12em;padding:3px 8px;border-radius:9px;color:rgba(150,220,235,.5);border:1px solid rgba(150,220,235,.2)}
.pill.on{color:#00fff7;border-color:rgba(0,255,247,.5)} .pill.warn{color:#ffbe55;border-color:rgba(255,190,85,.5)}
.night{flex-wrap:wrap} .night span{font-size:12px;color:rgba(150,220,235,.8)} .night input[type=time]{flex:none;width:auto;background:rgba(0,0,0,.25);border:1px solid rgba(0,255,247,.3);border-radius:8px;padding:4px 8px;color-scheme:dark}
.mllist{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(360px,100%),1fr));gap:12px;margin-top:6px}
.ml{padding:12px 14px} .ml .r{margin:0;background:none;border:none;padding:0 0 6px} .ml.off{opacity:.55}
.r ha-icon.lit,.ml ha-icon.lit{color:#ffd76a;filter:drop-shadow(0 0 6px rgba(255,215,106,.7))}
.mlsub{display:flex;align-items:center;gap:6px;font-size:11.5px;color:rgba(150,220,235,.75);margin:2px 0 8px} .mlsub ha-icon{--mdc-icon-size:14px;color:#5fd8e6}
.chips.left{justify-content:flex-start}
.mlform{margin-top:14px;border-color:rgba(0,255,247,.55)} .row.end{justify-content:flex-end;margin-top:10px} .row.end .spmsg{flex:1;color:#ff7088}
.tg{position:relative;flex:none;width:34px;height:18px;cursor:pointer} .tg input{position:absolute;opacity:0;width:100%;height:100%;margin:0;cursor:pointer}
.tg i{position:absolute;inset:0;border-radius:9px;background:rgba(150,220,235,.2);transition:.2s;pointer-events:none}
.tg i::after{content:"";position:absolute;left:2px;top:2px;width:14px;height:14px;border-radius:50%;background:#9bb;transition:.2s}
.tg input:checked+i{background:rgba(0,255,247,.35)} .tg input:checked+i::after{left:18px;background:#00fff7;box-shadow:0 0 6px #00fff7}
.tlp{max-width:1100px}
.filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.filters button{font-size:9.5px;font-weight:700;letter-spacing:.14em;padding:5px 10px;border-radius:10px;color:rgba(150,220,235,.7);border:1px solid rgba(0,255,247,.18)}
.filters button b{font-weight:700;color:#7fefff;margin-left:4px} .filters button.on{color:#041016;background:#00fff7;border-color:#00fff7} .filters button.on b{color:#041016}
.day{font-size:10px;font-weight:700;letter-spacing:.3em;color:#00fff7;margin:16px 0 8px;padding-bottom:6px;border-bottom:1px solid rgba(0,255,247,.18)}
.tli{display:grid;grid-template-columns:48px 30px minmax(0,1fr) auto;align-items:center;gap:10px;padding:6px 4px;position:relative}
.tli .tm{font-size:11px;font-weight:700;color:rgba(150,220,235,.7);text-align:right}
.tli .dot{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;border:1px solid rgba(0,255,247,.35);background:rgba(0,255,247,.06)}
.tli .dot ha-icon{--mdc-icon-size:15px;color:#00fff7}
.tli .tx{min-width:0} .tli .tx b{display:block;font-weight:400;font-size:13px;line-height:1.4;color:#e6f5fa;overflow-wrap:anywhere} .tli .tx i{display:block;font-style:normal;font-size:11px;line-height:1.35;color:rgba(150,220,235,.6);margin-top:2px;overflow-wrap:anywhere}
.tli s{text-decoration:none;font-size:8.5px;font-weight:700;letter-spacing:.12em;padding:2px 7px;border-radius:8px;color:#7fefff;border:1px solid rgba(0,255,247,.3)}
.tli.k-held,.tli.k-noticed{opacity:.65} .tli.k-health .dot{border-color:rgba(255,190,85,.5)} .tli.k-health .dot ha-icon{color:#ffbe55}
.tli.k-visitor .dot{border-color:rgba(61,255,168,.5)} .tli.k-visitor .dot ha-icon{color:#3dffa8} .tli.k-light .dot ha-icon{color:#ffd76a} .tli.k-critical .dot{border-color:#ff5a78;background:rgba(255,80,110,.15)} .tli.k-critical .dot ha-icon{color:#ff5a78}
.modes{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
.mode{display:flex;align-items:center;gap:6px;font-size:9.5px;font-weight:700;letter-spacing:.14em;padding:5px 11px;border-radius:12px;color:rgba(150,220,235,.75);border:1px solid rgba(0,255,247,.25)}
.mode ha-icon{--mdc-icon-size:14px} .mode.lit{color:#00fff7;border-color:rgba(0,255,247,.55)} .mode.run{color:#e6f5fa;border-color:rgba(0,255,247,.4);background:rgba(0,255,247,.06)} .mode.on{color:#041016;background:#ffbe55;border-color:#ffbe55;box-shadow:0 0 12px rgba(255,190,85,.45)}
.digest{display:flex;align-items:center;gap:12px;margin-bottom:14px;padding:12px 14px;border-radius:14px;border:1px solid rgba(0,255,247,.35);background:rgba(0,255,247,.06)}
.digest>ha-icon{--mdc-icon-size:22px;color:#00fff7;flex:none} .digest .dt{flex:1;min-width:0}
.digest .dt b{display:block;font-weight:400;font-size:13.5px;line-height:1.45;color:#e6f5fa} .digest .dt i{display:block;font-style:normal;font-size:9px;font-weight:700;letter-spacing:.16em;color:rgba(150,220,235,.6);margin-top:4px}
.en{display:grid;grid-template-columns:120px minmax(0,1fr) 54px;align-items:center;gap:8px;margin-bottom:7px;font-size:11.5px}
.en span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:rgba(230,245,250,.9)} .en u{text-decoration:none;text-align:right;font-weight:700;font-size:10.5px;color:#7fefff}
.bar2{position:relative;height:7px;border-radius:4px;background:rgba(0,255,247,.08)} .bar2 i{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:linear-gradient(90deg,#00a8ff,#00fff7)}
.bar2 b{position:absolute;top:-3px;width:2px;height:13px;background:rgba(255,255,255,.7);border-radius:1px}
.en.hot .bar2 i{background:linear-gradient(90deg,#ff9a3d,#ff5a78)} .en.hot u{color:#ff7088}
.tli.k-energy .dot ha-icon{color:#ffbe55} .tli.k-security .dot ha-icon{color:#ff7088}
@media (max-width: 1100px){ .cols{grid-template-columns:1fr 1fr} .rm{display:none} .set{grid-template-columns:230px minmax(0,1fr)} .nav i{display:none} }
@media (max-width: 900px){ .ask{display:none} }
@media (max-width: 720px){ .page{padding:10px} .top{padding:0 8px;gap:6px} .top .ttl{letter-spacing:.2em;font-size:12px;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:1} .tabs{margin-left:0} .tabs button{padding:6px 9px} .brief span{display:none} .brief{padding:6px 8px}
  .tli{align-items:start} .tli .tm{padding-top:6px} .card{padding:12px} .body{padding:14px 12px} .en{grid-template-columns:90px minmax(0,1fr) 46px} .reply{margin:8px 10px 0} .reply .q{display:none}
  .r{flex-wrap:wrap} .r b{white-space:normal} .formwrap ha-form{max-width:100%} .spotgrid{gap:12px} .digest{flex-wrap:wrap;align-items:flex-start} .digest .dt{flex:1 1 calc(100% - 40px)} .digest .dt b{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:4;overflow:hidden;cursor:pointer} .digest.open .dt b{-webkit-line-clamp:unset;display:block} .digest .btn{flex:1 1 100%;text-align:center;justify-content:center} .filters{flex-wrap:nowrap;overflow-x:auto;padding-bottom:4px} .filters button{flex:none} .modes{justify-content:center} .tli{grid-template-columns:40px 28px minmax(0,1fr)} .tli s{display:none} .add .when{width:84px}  .cols{grid-template-columns:1fr} .head{flex-direction:column;text-align:center} .head img{width:120px;height:120px} .ti{font-size:22px} .st{justify-content:center;flex-wrap:wrap} .st .s{flex:1 1 calc(33.3% - 8px)}
  .set{grid-template-columns:1fr} .side{position:static;padding:8px 0} .sh{display:none} .nav i{display:none} .nav{padding:8px 10px} .tabs span{display:none} }
`;

  customElements.define("sentient-panel", SentientPanel);
})();
