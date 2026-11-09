/* Sentient sidebar panel — ships with the integration, not editable from the dashboard editor.
   Finds Sentient's own sensors through the entity registry (platform "sentient"), so it works in any house.
   Two views: Overview (what the house knows and is doing) and Settings (memories plus every option,
   drawn with Home Assistant's own form from the schema the integration sends). */
(() => {
  if (customElements.get("sentient-panel")) return;

  const KEYS = ["memories", "last_spoken", "likely_room", "watches", "routines", "visitors", "health", "hush", "guest_mode", "chime", "party_mode", "mind", "media_speakers", "house", "holiday"];
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const hm = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }); };
  const heldWhy = (reason) => {
    const [kind, what] = String(reason || "").split(/:(.*)/s);
    if (kind === "hushed_by") return what === "announcement" ? "AFTER ANOTHER ANNOUNCEMENT" : "AFTER " + String(what || "SOMETHING ELSE").split(".").pop().replaceAll("_", " ").toUpperCase();
    return ({ quiet_hours: "QUIET HOURS", said_recently: "SAID RECENTLY", duplicate: "JUST SAID", thumbs_down: "THUMBS-DOWN", guest_mode: "GUESTS IN", hourly_limit: "ENOUGH THIS HOUR", no_speaker: "NO SPEAKER", hushed: "HUSHED", holiday: "ON HOLIDAY", room_quiet: "ROOM'S QUIET TIME" })[kind] || kind.replaceAll("_", " ").toUpperCase();
  };
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
      const want = path.startsWith("/settings") ? "settings" : path.startsWith("/timeline") ? "timeline" : path.startsWith("/mind") ? "mind" : path.startsWith("/security") ? "security" : path.startsWith("/house") ? "house" : "overview";
      // a phone notification was tapped: show that moment on the timeline
      try {
        const item = new URLSearchParams(window.location.search).get("item");
        if (want === "timeline" && item && item !== this._tlFocusDone) {
          this._tlFocus = item; this._tlKind = "all";
          if (this.shadowRoot && this._view === "timeline") this._openTimeline();  // already open: fetch it fresh
        }
      } catch (e) { /* no query string */ }
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
            <button data-view="house"><ha-icon icon="mdi:home-lightning-bolt-outline"></ha-icon><span>HOUSE</span></button>
            <button data-view="mind"><ha-icon icon="mdi:head-lightbulb-outline"></ha-icon><span>MIND</span></button>
            <button data-view="security"><ha-icon icon="mdi:shield-home-outline"></ha-icon><span>SECURITY</span></button>
            <button data-view="timeline"><ha-icon icon="mdi:timeline-clock-outline"></ha-icon><span>TIMELINE</span></button>
            <button data-view="settings"><ha-icon icon="mdi:cog-outline"></ha-icon><span>SETTINGS</span></button></nav>
          <form class="ask" id="askf"><ha-icon icon="mdi:creation-outline"></ha-icon><input id="ask" placeholder="Ask anything…" autocomplete="off"></form>
          <button class="brief" id="brief"><ha-icon icon="mdi:text-box-outline"></ha-icon><span>BRIEFING</span></button></div>
        <div class="reply" id="reply" hidden></div>
        <div class="page" id="ov"><div id="head" class="card head"></div><div id="aiwarn" class="warnbar" role="status" hidden></div>
          <div class="lanes ov3">
            <section class="lane" aria-label="Right now"><div class="lt">RIGHT NOW</div><div id="whatsnew" class="card" hidden></div><div id="sug" class="card" hidden></div><div id="today" class="card"></div><div id="around" class="card" hidden></div><div id="health" class="card"></div><div id="sec" class="card"></div></section>
            <section class="lane" aria-label="What's been happening"><div class="lt">WHAT'S BEEN HAPPENING</div><div id="day" class="card" hidden></div><div id="said" class="card said"></div></section>
            <section class="lane" aria-label="Coming up"><div class="lt">COMING UP</div><div id="soon" class="card"></div><div id="watch" class="card"></div><div id="mind" class="card"></div></section>
          </div></div>
        <div class="page" id="mp" hidden><div id="aiwarn2" class="warnbar" role="status" hidden></div><div id="mbrain" class="card brain"></div>
          <div class="lanes ov3">
            <section class="lane" aria-label="Thinking"><div class="lt">THINKING</div><div id="mthink" class="card"></div><div id="mlater" class="card" hidden></div></section>
            <section class="lane" aria-label="Learning"><div class="lt">LEARNING</div><div id="minner" class="card" hidden></div><div id="mknow" class="card" hidden></div><div id="mcheck" class="card" hidden></div><div id="mgoals" class="card" hidden></div></section>
            <section class="lane" aria-label="How it's doing"><div class="lt">HOW IT'S DOING</div><div id="mself" class="card" hidden></div><div id="mvoice" class="card"></div><div id="mupd" class="card" hidden></div><div id="mdeliv" class="card"></div></section>
          </div></div>
        <div class="page" id="hp" hidden><div id="rooms" class="card rooms" hidden></div>
          <div class="lanes ov3">
            <section class="lane" aria-label="Heating"><div class="lt">HEATING</div><div id="heat" class="card"></div></section>
            <section class="lane" aria-label="Energy, appliances and routines"><div class="lt">ROUTINES &amp; APPLIANCES</div><div id="rout" class="card"></div><div id="energy" class="card" hidden></div><div id="apl" class="card" hidden></div></section>
            <section class="lane" aria-label="Comfort"><div class="lt">COMFORT &amp; LIGHTS</div><div id="ins" class="card" hidden></div><div id="mlc" class="card" hidden></div></section>
          </div></div>
        <div class="page" id="scp" hidden>
          <div class="lanes ov3">
            <section class="lane" aria-label="Live"><div class="lt">LIVE</div><div id="live" class="card"></div><div id="sec2" class="card"></div></section>
            <section class="lane" aria-label="Linked events"><div class="lt">LINKED EVENTS</div><div id="sectl" class="card"></div><div id="snaps" class="card" hidden></div></section>
            <section class="lane" aria-label="Around the house"><div class="lt">AROUND THE HOUSE</div><div id="camc" class="card"></div><div id="visit" class="card"></div><div id="minc" class="card" hidden></div><div id="clipc" class="card" hidden></div></section>
          </div></div>
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
      this.shadowRoot.addEventListener("input", (ev) => {
        if (ev.target.id === "mfind") { this._find = ev.target.value; this._drawMemories(); }
        if (ev.target.id === "sfind") { this._sq = ev.target.value; this._drawSide(); }
      });
      this.shadowRoot.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape" && this._editing) { this._editing = null; this._drawMemories(); }
      });
      this._group = "memories";
      this._show(this._view || "overview", false);
    }

    _show(view, push) {
      this._view = view;
      this.setAttribute("view", view);
      this.shadowRoot.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.view === view));
      this._$("ov").hidden = view !== "overview";
      this._$("set").hidden = view !== "settings";
      this._$("tl").hidden = view !== "timeline";
      this._$("mp").hidden = view !== "mind";
      this._$("scp").hidden = view !== "security";
      this._$("hp").hidden = view !== "house";
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
      const stat = (n, l, go) => `<div class="s"${go ? ` data-act="goto" data-id="${esc(go)}"` : ""}><div class="n">${esc(n)}</div><div class="l">${l}</div></div>`;
      const st = (k) => (this._st(k) ? this._st(k).state : "—");
      const ai = ((this._st("mind") || {}).attributes || {}).ai || {};
      const aiTxt = ai.failing ? "AI NOT ANSWERING · PLAIN WORDS" : ai.capped ? "AI LIMIT REACHED · PLAIN WORDS" : `AI ${ai.today ?? this._attr("likely_room", "ai_checks_today", 0)}${ai.limit ? "/" + ai.limit : ""} TODAY`;
      R("head").innerHTML = `<img src="/sentient_static/logo.png" alt="">
        <div class="m"><div class="ti">${esc(persona).toUpperCase()}</div>
          <div class="sub">${(this._st("house") || {}).state === "asleep" ? "HOUSE ASLEEP" : this._attr("likely_room", "quiet_hours") ? "QUIET HOURS" : "AWAKE"} · PEOPLE IN ${esc(room.state).toUpperCase()} · ${aiTxt}</div>
          <div class="modes">${this._modeChip("hush", "mdi:volume-off", "HUSH 1 HOUR", "HUSHED")}${this._modeChip("guest_mode", "mdi:account-multiple", "GUEST MODE", "GUESTS IN")}${this._holidayChip()}${this._showChips()}${this._chimeChip()}${(this._sc || []).map((r) => `<button class="mode run" data-act="scrun" data-id="${esc(r.id)}"><ha-icon icon="mdi:play-circle-outline"></ha-icon>${esc(r.name).toUpperCase()}</button>`).join("")}</div>
          <div class="st">${stat(st("memories"), "MEMORIES ›", "memories")}${stat(st("watches"), "WATCHING")}${stat(st("routines"), "ROUTINES")}${stat(st("visitors"), "VISITORS")}${stat(st("health"), "PROBLEMS")}${stat(ai.today ?? 0, ai.limit ? `AI TASKS / ${ai.limit}` : "AI TASKS")}</div></div>
        <div class="rm"><div class="t">WHERE PEOPLE ARE</div>${bars || '<div class="b">NO RECENT MOTION</div>'}</div>`;

      // when the AI keeps failing or today's calls are used up, say so plainly at the top
      const warn = ai.failing ? `<ha-icon icon="mdi:robot-confused-outline"></ha-icon><b>The AI isn't answering right now<small>Remarks, camera looks and briefings use plain words until it's back${ai.last_error ? " · " + esc(String(ai.last_error).slice(0, 140)) : ""}</small></b>`
        : ai.capped ? `<ha-icon icon="mdi:counter"></ha-icon><b>Today's ${esc(String(ai.limit))} AI tasks are used up<small>Plain words until midnight · the limit is in Settings → Mind</small></b>` : "";
      for (const id of ["aiwarn", "aiwarn2"]) { R(id).hidden = !warn; R(id).innerHTML = warn; }

      // what's new since the last update it was seen (loaded once; GOT IT hides it)
      if (this._newAt === undefined) {
        this._newAt = Date.now();
        this._hass.callWS({ type: "sentient/whats_new" }).then((r) => { this._new = r.releases || []; this._render(); }, () => { this._new = []; });
      }
      const news = this._new || [];
      R("whatsnew").hidden = !news.length;
      R("whatsnew").innerHTML = news.length ? this._h("WHAT'S NEW", esc(news[0].version)) + news.map((rel) => rel.items.map((t) => `<div class="r"><ha-icon icon="mdi:creation-outline"></ha-icon><b class="wrap">${esc(t)}</b></div>`).join("")).join("")
        + `<div class="row"><button class="btn sm" data-act="newseen">GOT IT</button></div>` : "";

      // suggestions: things it could use but isn't (refreshed every ten minutes; empty when turned off)
      if (!this._sugAt || Date.now() - this._sugAt > 600000) {
        this._sugAt = Date.now();
        this._hass.callWS({ type: "sentient/suggestions" }).then((r) => { this._sug = r.items || []; this._render(); }, () => { this._sug = []; });
      }
      const sug = this._sug || [];
      R("sug").hidden = !sug.length;
      R("sug").innerHTML = sug.length ? this._h("SUGGESTIONS", `${sug.length}`) + sug.map((s) => `<div class="r"><ha-icon icon="mdi:lightbulb-auto-outline"></ha-icon><b class="wrap">${esc(s.text)}</b>
          ${s.url ? `<a class="btn sm" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">HOW</a>` : `<button class="btn sm" data-act="sugyes" data-id="${esc(s.id)}">USE IT</button>`}<a class="x" data-act="sugno" data-id="${esc(s.id)}" title="Don't suggest again">✕</a></div>`).join("") : "";

      // the TODAY and COMING UP cards (refreshed every minute while the overview is open)
      if (this._view === "overview" && (!this._todayAt || Date.now() - this._todayAt > 60000)) this._loadToday();
      if (this._view === "overview" && (!this._dayAt || Date.now() - this._dayAt > 120000)) this._loadDay();
      // rooms at a glance (House tab) and the around-the-house card (overview), every 30 seconds while shown
      if ((this._view === "house" || this._view === "overview") && (!this._glAt || Date.now() - this._glAt > 30000)) this._loadGlance();
      // heating zones and schedule (refreshed every 30 seconds while the House tab is open)
      if (this._view === "house" && (!this._heatAt || Date.now() - this._heatAt > 30000) && !this._heatEdit) this._loadHeat();
      // the real cameras (refreshed every minute)
      if (!this._camAt || Date.now() - this._camAt > 60000) {
        this._camAt = Date.now();
        this._hass.callWS({ type: "sentient/cameras" }).then((r) => { this._cams = r; this._drawCameras(); }, () => { this._cams = null; });
      }
      this._drawCameras();

      // clips watched as they happened (refreshed every two minutes; hidden when off and empty)
      if (!this._clipAt || Date.now() - this._clipAt > 120000) {
        this._clipAt = Date.now();
        this._hass.callWS({ type: "sentient/clips" }).then((r) => { this._clips = r; this._render(); }, () => { this._clips = null; });
      }
      const cl = this._clips || { on: false, items: [] };
      const clipIc = { delivery: "mdi:package-variant-closed", visitor: "mdi:account", household: "mdi:home-account", "passer-by": "mdi:walk", animal: "mdi:paw", vehicle: "mdi:car", suspicious: "mdi:alert-outline", nothing: "mdi:minus" };
      R("clipc").hidden = !cl.on && !(cl.items || []).length;
      R("clipc").innerHTML = this._h("CLIPS WATCHED", `${(cl.items || []).length}`) + ((cl.items || []).slice(0, 12).map((c) => `<div class="r"><ha-icon icon="${esc(clipIc[c.kind] || "mdi:filmstrip-box")}"></ha-icon><b class="wrap">${esc(c.local)} · ${esc(c.camera.replace(/_/g, " ").toUpperCase())} — ${esc(c.summary)}${c.whose ? ` <i>(probably ${esc(c.whose)})</i>` : ""}</b>${c.event ? `<a class="btn sm ghost" href="/api/frigate/notifications/${encodeURIComponent(c.event)}/clip.mp4" target="_blank" rel="noopener">PLAY</a>` : ""}</div>`).join("")
        || `<div class="e">NOTHING WATCHED YET${Object.keys(cl.skipped || {}).length ? " · SKIPPED: " + Object.entries(cl.skipped).map(([k, v]) => `${v} ${esc(k).toUpperCase()}`).join(", ") : " · IT WRITES HERE WHEN THE CAMERAS SEE SOMEONE"}</div>`);

      // routines
      const routines = this._attr("routines", "routines", []);
      const on = routines.filter((r) => r.adopted).length;
      const gone = this._attr("routines", "deleted", []);
      const back = gone.length ? `<a class="lnk" data-act="unforget" data-text="Let ${gone.length} deleted routine${gone.length > 1 ? "s" : ""} be learned again?">${gone.length} DELETED · BRING BACK</a>` : "";
      R("rout").innerHTML = this._h("ROUTINES &amp; HABITS", `${on} AUTOMATED · ${routines.length} LEARNED`) + back +
        (routines.slice(0, 6).map((r) => {
          const tag = r.adopted ? "HOUSE DOES IT" : r.declined ? "DECLINED" : r.offered ? "OFFERED" : "LEARNED";
          const cls = r.adopted ? "on" : r.declined ? "off" : "";
          const ask = r.adopted ? `Stop the house doing: ${r.routine}` : `Let the house do this from now on: ${r.routine}`;
          return `<div class="r rtr ${cls}" data-act="adopt" data-id="${esc(r.id)}" data-text="${esc(ask)}"><ha-icon icon="${esc(r.adopted ? "mdi:robot-happy-outline" : "mdi:repeat")}"></ha-icon>
            <b class="wrap">${esc(r.routine)}<small>${esc(r.how_sure)} · tap to ${r.adopted ? "stop" : "let the house do it"}</small></b><s>${tag}</s><a class="x" data-act="unlearn" data-id="${esc(r.id)}" data-text="${esc(r.routine)}" title="Delete">✕</a></div>`;
        }).join("") || '<div class="e">NO ROUTINES LEARNED YET · CHECKED EACH NIGHT</div>');

      // watches (left alone while a reminder is being typed)
      const act = this.shadowRoot.activeElement;
      const typing = act && act.closest && act.closest('form[data-form="remind"]');
      const watches = this._attr("watches", "watches", []);
      if (!typing) R("watch").innerHTML = this._h("WATCHES &amp; REMINDERS", `${watches.length} ACTIVE`) + (watches.map((w) => {
        const ic = w.kind === "reminder" ? "mdi:bell-ring-outline" : w.kind === "camera" ? "mdi:cctv" : "mdi:eye-outline";
        return `<div class="r"><ha-icon icon="${esc(ic)}"></ha-icon><b>${esc(w.what)}${w.spot ? " · " + esc(w.spot) : ""}</b><u>${left(w.ends, w.minutes_left)}</u>
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
        ? `<div class="r bad"><ha-icon icon="mdi:lan-disconnect"></ha-icon><b>${esc(i.name)}${i.with ? `<small>and ${i.with} of its device${i.with > 1 ? "s" : ""}</small>` : ""}${i.why ? `<small>${esc(i.why)}</small>` : ""}</b><u>OFFLINE${i.since ? " · " + ago(i.since) : ""}</u></div>`
        : i.kind === "ai" ? `<div class="r bad"><ha-icon icon="mdi:robot-confused-outline"></ha-icon><b title="${esc(i.detail || "")}">The AI isn't answering</b><u>PLAIN WORDS</u></div>`
        : i.kind === "limit" ? `<div class="r warn"><ha-icon icon="mdi:counter"></ha-icon><b>Today's AI tasks are used up</b><u>${esc(i.level)} · PLAIN WORDS</u></div>`
        : i.kind === "speaker" ? `<div class="r bad"><ha-icon icon="mdi:speaker-off"></ha-icon><b title="${esc(i.detail || "")}">Couldn't speak on ${esc(i.name)}</b><u>${ago(i.since)}</u></div>`
        : ["wear", "fight", "frozen"].includes(i.kind) ? `<div class="r warn"><ha-icon icon="${esc(i.kind === "fight" ? "mdi:sword-cross" : i.kind === "frozen" ? "mdi:cctv-off" : "mdi:wrench-clock")}"></ha-icon><b class="wrap">${esc(i.text)}</b></div>`
        : i.kind === "stuck" ? `<div class="r warn"><ha-icon icon="mdi:motion-sensor-off"></ha-icon><b>${esc(i.name)}</b><u>${i.on ? "STUCK ON" : "NOTHING SINCE " + ago(i.since)}</u></div>`
        : `<div class="r warn"><ha-icon icon="mdi:battery-alert-variant-outline"></ha-icon><b>${esc(i.name)}</b><u>${i.level == null ? "LOW" : i.level + "%"}</u></div>`).join("")
        + (updates.length ? `<div class="r"><ha-icon icon="mdi:package-up"></ha-icon><b>${updates.length} update${updates.length > 1 ? "s" : ""} waiting · ${esc(updates.slice(0, 3).join(", "))}</b></div>` : "");
      R("health").innerHTML = this._h("HOUSE HEALTH", issues.length ? `${issues.length} PROBLEM${issues.length > 1 ? "S" : ""}` : "ALL GOOD")
        + (healthRows || '<div class="e">EVERYTHING ONLINE · BATTERIES FINE</div>');

      // mind: what it's thinking, wondering, waiting to check and pass on
      const mind = (this._st("mind") || {}).attributes || {};
      const fresh = mind.last_reflection && Date.now() - new Date(mind.last_reflection) < 16 * 3600e3;
      const voiceCheckRow = mind.live_voice_check_enabled === false ? "" : this._voiceCheckRow(mind.voice_check);
      const updateRow = this._updateRow(mind.sentient_updates);
      const deliveryRows = (mind.speech_deliveries || []).slice(0, 10).map((d) => `<div class="r"><ha-icon icon="mdi:speaker-multiple"></ha-icon><b class="wrap">Announcement delivery<details><summary>${esc(d.message)}</summary>${d.results.map((r) => `<small>${esc(this._hass.states[r.entity_id]?.attributes?.friendly_name || r.entity_id)} · ${esc(r.status.replaceAll("_", " "))}${r.stage ? " · " + esc(r.stage.replaceAll("_", " ")) : ""}${r.service ? " · " + esc(r.service) : ""}${r.duration_ms != null ? " · " + esc(r.duration_ms) + " ms" : ""}${r.error ? " · " + esc(r.error) : ""}</small>`).join("")}<small>${esc(d.note)}</small></details></b><u>${hm(d.time)}</u></div>`).join("");
      const voiceRows = [
        voiceCheckRow,
        ...(mind.voice_timing_enabled ? (mind.voice_timings || []).slice(0, 5).map((t) => `<div class="r"><ha-icon icon="mdi:timer-outline"></ha-icon><b class="wrap">Voice response · ${esc(t.total_ms)} ms<small>${esc(t.outcome)} · ${t.listen ? "LISTEN" : "STOP"}</small><details><summary>Where time went</summary>${Object.entries(t.stages_ms || {}).map(([k,v]) => `<small>${esc(k.replaceAll("_", " "))}: ${esc(v)} ms</small>`).join("")}<small>Stages can overlap. Agent time includes tools. Microphone capture and final pipeline TTS/playback are not measured.</small></details></b><u>${hm(t.time)}</u></div>`) : []),
        ...(mind.voice_sessions || []).map((s) => `<div class="r"><ha-icon icon="mdi:account-voice"></ha-icon><b class="wrap">${esc(s.pending_action || s.goal || s.topic || "Voice session")}${s.unanswered_question ? " · " + esc(s.unanswered_question) : ""}<small>${esc((s.listen_reason || "").replaceAll("_", " "))}${s.missing_detail ? " · " + esc(s.missing_detail) : ""}</small></b><u>${esc(s.state.replaceAll("_", " "))}${s.undo_available ? " · UNDO AVAILABLE" : ""}</u></div>`),
        ...(mind.conversation_diagnostics || []).slice(0, 8).map((d) => `<div class="r"><ha-icon icon="mdi:message-text-clock"></ha-icon><b class="wrap">${esc(d.request)}<details><summary>Exchange details</summary><small>REPLY · ${esc(d.reply)}</small></details><small>GOAL · ${esc(d.goal || "Unspecified")} · DEVICES · ${esc((d.entities || []).join(", ") || "None selected")}</small><small>${esc((d.reason || "").replaceAll("_", " "))}</small></b><u>${d.listen ? "LISTEN" : "STOP"} · ${hm(d.time)}</u></div>`),
      ].join("");
      const incidentRows = (mind.security_incidents || []).slice(0, 6).map((i) => `<div class="r"><ha-icon icon="mdi:shield-search"></ha-icon><b class="wrap">${esc(i.evidence.slice(-3).map((e) => e.text).join(" · "))}<small>${esc(i.link)}</small></b><u>${hm(i.start)}</u></div>`).join("") + this._securityEventRows(mind);
      const thinkList = [
        ...(fresh ? (mind.thoughts || []) : []).map((t) => `<div class="r"><ha-icon icon="mdi:lightbulb-on-outline"></ha-icon><b>${esc(t)}</b><u>OVERNIGHT</u></div>`),
        ...(mind.wondering || []).map((q) => `<div class="r"><ha-icon icon="mdi:help-circle-outline"></ha-icon><b>${esc(q)}</b>
          <button class="btn sm ghost" data-act="wonderask">ASK</button><a class="x" data-act="wonderx" data-text="${esc(q)}" title="Don't ask">✕</a></div>`),
        ...(mind.follow_ups || []).map((f) => `<div class="r"><ha-icon icon="mdi:sync"></ha-icon><b>Check back: ${esc(f.about)}</b><u>${esc(f.at)}</u>
          <a class="x" data-act="fux" data-id="${esc(f.id)}" data-text="${esc(f.about)}" title="Cancel">✕</a></div>`),
        ...(mind.to_pass_on || []).map((t) => `<div class="r dim"><ha-icon icon="mdi:account-arrow-right-outline"></ha-icon><b>${esc(t)}</b><u>TO PASS ON</u></div>`),
        ...(mind.lessons || []).slice(-2).map((t) => `<div class="r dim"><ha-icon icon="mdi:school-outline"></ha-icon><b>${esc(t)}</b><u>LESSON</u></div>`),
        ...(mind.recent_conversations || []).slice(0, 3).map((c) => `<div class="r dim"><ha-icon icon="mdi:chat-outline"></ha-icon>
          <b>${esc(((c.conversation || [])[0] || "").replace(/^They: /, ""))}</b><u>${esc(String(c.when || "").split(" ").pop())}${c.room ? " · " + esc(c.room).toUpperCase() : ""}</u></div>`),
      ];
      const mindRows = thinkList.join("");
      const mindCount = (mind.wondering || []).length + (mind.follow_ups || []).length;
      // the last hour, on request: straight from the timeline, no AI
      const lastHour = this._lh ? (this._lh.length ? this._lh.map((i) => `<div class="r dim"><ha-icon icon="mdi:clock-outline"></ha-icon>
          <b>${esc(i.text)}</b><u>${hm(i.time)}</u></div>`).join("") : '<div class="e">NOTHING IN THE LAST HOUR</div>') : "";
      const mindTitle = mindCount ? `${mindCount} ON ITS MIND` : `${this._st("mind") ? this._st("mind").state : 0} TALKS TODAY`;
      const meter = (top) => ai.by_purpose ? `<div class="hint meter">AI ${ai.today}${ai.limit ? " OF " + ai.limit : ""} TODAY${Object.keys(ai.by_purpose).length ? " · " + Object.entries(ai.by_purpose).slice(0, top).map(([k, v]) => `${esc(k).toUpperCase()} ${v}`).join(" · ") : ""}${ai.average_per_day != null ? " · USUALLY " + ai.average_per_day + "/DAY" : ""}${ai.service_attempts_today != null ? ` · ${esc(ai.service_attempts_today)} SERVICE ATTEMPTS (${esc(ai.retry_attempts_today || 0)} RETRIES)` : ""}</div>` : "";
      // the overview keeps a short summary; everything else lives on the MIND tab
      R("mind").innerHTML = this._h("MIND", mindTitle)
        + (thinkList.slice(0, 4).join("") || '<div class="e">NOTHING ON ITS MIND · THINKS IT OVER EACH NIGHT</div>')
        + `<div class="row lh"><button class="btn sm ghost" data-act="openmind"><ha-icon icon="mdi:head-lightbulb-outline"></ha-icon> OPEN MIND ›</button></div>`
        + meter(3);
      R("mthink").innerHTML = this._h("ON ITS MIND", mindTitle)
        + (mindRows || '<div class="e">NOTHING ON ITS MIND · THINKS IT OVER EACH NIGHT</div>')
        + `<div class="row lh"><button class="btn sm ghost" data-act="lasthour"><ha-icon icon="mdi:history"></ha-icon> ${this._lh ? "HIDE THE LAST HOUR" : "WHAT HAPPENED THIS LAST HOUR?"}</button></div>`
        + lastHour + meter(12);
      R("mvoice").innerHTML = this._h("VOICE", (mind.voice_sessions || []).length ? `${mind.voice_sessions.length} TALKING` : "")
        + (voiceRows || '<div class="e">NO RECENT VOICE CONVERSATIONS · TURN ON CONVERSATION SESSIONS IN SETTINGS → VOICE TO SEE THEM HERE</div>');
      R("mdeliv").innerHTML = this._h("SPEAKER DELIVERIES", (mind.speech_deliveries || []).length ? `${mind.speech_deliveries.length} RECENT` : "")
        + (deliveryRows || '<div class="e">NO RECENT ANNOUNCEMENTS · OR DELIVERY HISTORY IS OFF IN SETTINGS → VOICE</div>');
      R("minc").hidden = !incidentRows;
      R("minc").innerHTML = incidentRows ? this._h("EVENT DETAILS", "") + incidentRows : "";
      if (this._view === "security") { this._drawLive(mind); this._drawSecTimeline(mind); this._drawSnaps(mind); }
      R("mupd").hidden = !updateRow;
      R("mupd").innerHTML = updateRow ? this._h("UPDATES", "") + updateRow : "";
      if (this._view === "mind") this._loadInner();
      this._drawBrain(mind, ai);
      this._drawAppliances(mind.appliances || []);

      // visitors
      const visits = this._attr("visitors", "recent", []);
      R("visit").innerHTML = this._h("VISITORS", `${st("visitors")} TODAY`) + (visits.slice(0, 8).map((v) =>
        `<div class="r"><ha-icon icon="${esc(v.kind === "delivery" ? "mdi:package-variant-closed" : "mdi:doorbell-video")}"></ha-icon>
          <b>${v.name ? esc(v.name) + " — " : ""}${esc(v.description)}${v.parcel_left ? " · PARCEL LEFT" : ""}</b><u>${hm(v.time)}</u></div>`).join("")
        || '<div class="e">NO ONE AT THE DOOR YET</div>');

      // said & noticed (left alone while a "say it differently" note is being typed)
      const styleOn = !!((this._st("mind") || {}).attributes || {}).style_feedback && !!(this._hass.user && this._hass.user.is_admin);  // it changes settings: admins only
      if (act && act.closest && act.closest('form[data-form="restyle"]')) return;
      const log = this._attr("last_spoken", "log", []).slice(0, 20).map((l) => l.said
        ? `<div class="r"><ha-icon icon="mdi:account-voice"></ha-icon><b>${esc(l.said)}</b><u>${hm(l.time)}</u>${(this._thumbs || {})[l.said] ? `<s class="thd">${this._thumbs[l.said] === "up" ? "👍" : this._thumbs[l.said] === "styled" ? "✎ NOTED" : "👎"}</s>` : `<a class="x th" data-act="thumb" data-id="up" data-text="${esc(l.said)}" data-src="${esc(l.source || "")}" title="Good to hear"><ha-icon icon="mdi:thumb-up-outline"></ha-icon></a><a class="x th" data-act="thumb" data-id="down" data-text="${esc(l.said)}" data-src="${esc(l.source || "")}" title="Not worth saying"><ha-icon icon="mdi:thumb-down-outline"></ha-icon></a>`}${styleOn ? `<a class="x th" data-act="restyle" data-text="${esc(l.said)}" title="Say it differently"><ha-icon icon="mdi:comment-edit-outline"></ha-icon></a>` : ""}</div>
          ${styleOn && this._restyle === l.said ? `<div class="r restyle">${["Shorter", "Plainer", "Less cheeky", "More detail"].map((n) => `<button class="btn sm ghost" data-act="restyleset" data-id="${esc(n)}" data-text="${esc(l.said)}">${n.toUpperCase()}</button>`).join("")}
            <form data-form="restyle" data-text="${esc(l.said)}"><input name="note" placeholder="Or say how…" autocomplete="off"><button class="btn sm" type="submit">SAVE</button></form><a class="x" data-act="restylex" title="Close">✕</a></div>` : ""}`
        : `<div class="r dim"><ha-icon icon="mdi:volume-off"></ha-icon><b>${esc(l.held)}</b><u>${esc(heldWhy(l.reason))}</u></div>`).join("");
      const noticed = this._attr("last_spoken", "noticed", []).slice(0, 6).map((d) =>
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
      ["alarms", "schedules", "offers", "announce", "checks", "autooff", "rules"].forEach((k) => this._loadItems(k));
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
        shortcuts: ["mdi:playlist-play", "Voice routines", this._sc ? `${this._sc.length} · goodnight, morning…` : "Goodnight, good morning…"],
        schedules: ["mdi:calendar-clock", "Automated routines", n("schedules") !== null ? `${n("schedules")} · on a schedule` : "Done by itself at a set time"],
        motion: ["mdi:motion-sensor", "Motion lights", this._ml ? `${this._ml.rules.length} light${this._ml.rules.length === 1 ? "" : "s"} · after dark` : "Lights that follow movement"],
        alarms: ["mdi:alarm", "Alarms", n("alarms") !== null ? `${n("alarms")} set · said at a time` : "Wake-ups, bins, appointments"],
        offers: ["mdi:hand-heart-outline", "Offers", n("offers") !== null ? `${n("offers")} · fancy a brew?` : "Questions it asks — a brew?"],
        announce: ["mdi:bullhorn-outline", "Announcements", n("announce") !== null ? `${n("announce")} · doorbell, gate` : "Doorbell, gate…"],
        autooff: ["mdi:timer-off-outline", "Auto-off", n("autooff") !== null ? `${n("autooff")} · lights, coffee…` : "Off when left on or the room's empty"],
        rules: ["mdi:script-text-outline", "Rules", n("rules") !== null ? `${n("rules")} · when this, do that` : "When this happens, do that"],
        maintenance: ["mdi:wrench-clock", "Maintenance", n("maintenance") !== null ? `${n("maintenance")} counted by use` : "Filters and descaling, by use"],
        rooms: ["mdi:door-open", "Rooms", n("rooms") !== null ? `${n("rooms")} with their own ways` : "Private, gentler, own volume or quiet"],
        checks: ["mdi:cctv", "Camera checks", n("checks") !== null ? `${n("checks")} watching` : "Delivery van outside?"],
        backup: ["mdi:backup-restore", "Backup", "Download or restore everything"],
        usage: ["mdi:chart-timeline-variant-shimmer", "How it's used", this._use ? `${this._use.never.length} on but never used` : "What's used, and what isn't"],
        privacy: ["mdi:shield-lock-outline", "What leaves the house", this._priv ? `${this._priv.totals.times} sent this week` : "AI calls and searches, and forgetting"],
        doctor: ["mdi:stethoscope", "Setup doctor", this._doc ? (this._doc.status === "ok" ? "All good" : `${this._doc.rows.filter((r) => r.status !== "ok").length} to look at`) : "Check the setup and what to fix"],
      };
      groups.forEach((g) => { pages[g.id] = [g.icon, g.title, g.blurb]; });
      // Essentials first; optional features under Extras, each with its on/off state. Anything not listed lands last.
      const data = (this._settings && this._settings.data) || {};
      const SWITCH = { clips: "clip_watch", camsec: "camera_security", nightwatch: "night_watch", away: "away_lights_on", bins: "bins_check",
        rain: "rain_warnings", alexa: "alexa_skill", energy: "energy_insight", autobackup: "auto_backup", screen: "screen", updates: "update_checker",
        party: "party_whole_house", safety: "critical_alerts", heating: "preheat", appliances: "appliance_announce", briefing: "briefings",
        search: "search_hints", door: "faces", animals: "hutch_check" };
      const menus = [
        ["essentials", "voice", "mdi:account-voice", "Voice & sound", ["voice", "quiet", "rooms", "voicebackup", "mutes", "phone"]],
        ["essentials", "house", "mdi:shield-home", "House & safety", ["never", "health", "safety"]],
        ["essentials", "brain", "mdi:brain", "Brain & system", ["doctor", "usage", "privacy", "memories", "mind", "noticing", "page", "updates", "backup", "autobackup"]],
        ["extras", "speaking", "mdi:bullhorn-outline", "Speaking up", ["announce", "offers", "briefing", "alexa", "search"]],
        ["extras", "cameras", "mdi:cctv", "Cameras & video", ["door", "spots", "checks", "camsec", "clips", "drive", "bins", "camwake"]],
        ["extras", "home", "mdi:home-automation", "Home & routines", ["trust", "maintenance", "shortcuts", "schedules", "alarms", "rules", "autooff", "motion", "appliances", "animals", "rain", "away", "party", "heating", "energy", "nightwatch", "screen"]],
      ];
      const placed = new Set(menus.flatMap((m) => m[4]));
      Object.keys(pages).forEach((id) => { if (!placed.has(id)) menus[menus.length - 1][4].push(id); });
      const mine = menus.find((m) => m[4].includes(this._group));
      if (!this._openMenus) this._openMenus = new Set();
      if (mine && this._menuFor !== this._group) { this._menuFor = this._group; if (!this._phone()) this._openMenus.add(mine[1]); }
      const chip = (id) => SWITCH[id] && data[SWITCH[id]] !== undefined ? `<em class="onoff${data[SWITCH[id]] ? " on" : ""}">${data[SWITCH[id]] ? "ON" : "OFF"}</em>` : "";
      const item = (id, sub2) => {
        const [icon, title, sub] = pages[id];
        return `<button class="nav${this._group === id ? " on" : ""}" data-act="group" data-id="${esc(id)}"><ha-icon icon="${esc(icon)}"></ha-icon><span><b>${esc(title)}</b>${(sub2 || sub) ? `<i>${esc(sub2 || sub)}</i>` : ""}</span>${chip(id)}</button>`;
      };
      const side = this._$("side");
      if (!side.querySelector("#sfind")) {
        side.innerHTML = `<div class="sh">SETTINGS</div><button class="btn ghost wizbtn" data-act="wizard"><ha-icon icon="mdi:auto-fix"></ha-icon>SET-UP WIZARD</button>
          <label class="find sfind"><ha-icon icon="mdi:magnify"></ha-icon><input id="sfind" type="search" placeholder="Search settings…" autocomplete="off" value="${esc(this._sq || "")}"></label><div id="snav"></div>`;
      }
      const q = (this._sq || "").trim().toLowerCase();
      let html;
      if (q) {
        // pages whose title or blurb match, then single settings whose label or description match
        const labels = (this._settings && this._settings.labels) || {}, helpers = (this._settings && this._settings.helpers) || {};
        const hit = (t) => String(t || "").toLowerCase().includes(q);
        const pageHits = Object.keys(pages).filter((id) => hit(pages[id][1]) || hit(pages[id][2]));
        const fieldHits = [];
        for (const g of groups) for (const f of g.schema || []) {
          if (hit(labels[f.name]) || hit(helpers[f.name])) fieldHits.push([g.id, labels[f.name] || f.name]);
        }
        html = (pageHits.map((id) => item(id)).join("") + fieldHits.slice(0, 30).map(([gid, label]) => `<button class="nav" data-act="group" data-id="${esc(gid)}"><ha-icon icon="mdi:tune-variant"></ha-icon><span><b>${esc(label)}</b><i>in ${esc(pages[gid] ? pages[gid][1] : gid)}</i></span></button>`).join(""))
          || '<div class="e">NOTHING MATCHES · TRY ANOTHER WORD</div>';
      } else {
        let section = "";
        const simple = data.simple_settings && !this._showAll;
        html = menus.filter((m) => !simple || m[0] === "essentials").map(([sec, mid, icon, title, ids]) => {
          const list = ids.filter((id) => pages[id]);
          if (!list.length) return "";
          const open = this._openMenus.has(mid);
          const here = mine && mine[1] === mid;
          const on = list.filter((id) => SWITCH[id] && data[SWITCH[id]]).length;
          const head = sec !== section ? `<div class="ssec">${sec === "essentials" ? "ESSENTIALS" : "EXTRAS · OPTIONAL FEATURES"}</div>` : "";
          section = sec;
          return head + `<div class="menu${open ? " open" : ""}${here ? " here" : ""}">
            <button class="mh" data-act="menu" data-id="${esc(mid)}" aria-expanded="${esc(open)}"><ha-icon icon="${esc(icon)}"></ha-icon><span>${esc(title)}</span><u>${sec === "extras" ? `${on} ON` : list.length}</u><ha-icon class="chev" icon="mdi:chevron-down"></ha-icon></button>
            <div class="mi">${list.map((id) => item(id)).join("")}</div></div>`;
        }).join("");
      }
      if (!q && data.simple_settings) {
        html += this._showAll ? `<button class="btn ghost wizbtn" data-act="showless">SHOW LESS</button>`
          : `<button class="btn ghost wizbtn" data-act="showall"><ha-icon icon="mdi:plus-circle-outline"></ha-icon>SHOW EVERYTHING</button>`;
      }
      this._$("snav").innerHTML = html + (this._settingsErr ? `<div class="note">${esc(this._settingsErr)}</div>` : "");
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
      if (this._group === "doctor") { this._form = null; this._drawDoctor(); return; }
      if (this._group === "privacy") { this._form = null; this._drawPrivacy(); return; }
      if (this._group === "usage") { this._form = null; this._drawUsage(); return; }
      if (["alarms", "schedules", "offers", "announce", "checks", "autooff", "rules", "rooms", "maintenance"].includes(this._group)) { this._form = null; this._itEdit = null; this._loadItems(this._group); return; }
      const g = this._settings && this._settings.groups.find((x) => x.id === this._group);
      if (!g) { this._group = "memories"; this._drawMemories(); return; }
      const body = this._$("body");
      body.innerHTML = `<div class="bh"><ha-icon icon="${esc(g.icon)}"></ha-icon><div><div class="bt">${esc(g.title).toUpperCase()}</div><div class="bs">${esc(g.blurb)}</div></div></div>
        <div id="formwrap" class="formwrap"><div class="e">LOADING…</div></div>
        <div class="bar"><span id="status"></span>${g.id === "safety" ? '<button class="btn ghost" data-act="critest">TEST ALERT</button>' : ""}${g.id === "reading" ? '<button class="btn ghost" data-act="trytts">TRY THE VOICE</button>' : ""}<button class="btn ghost" data-act="reset">UNDO CHANGES</button><button class="btn" data-act="save">SAVE</button></div>`;
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
        // a setting that only matters while another is on stays hidden until it is (its value is kept)
        const dep = S.depends || {};
        const shown = (d) => g.schema.filter((f) => !dep[f.name] || !g.schema.some((x) => x.name === dep[f.name]) || d[dep[f.name]]);
        form.schema = shown(this._draft);
        form.data = this._draft;
        form.computeLabel = (s) => S.labels[s.name] || s.name;
        form.computeHelper = (s) => S.helpers[s.name] || "";
        form.localizeValue = (key) => S.selector[key] || key.split(".").pop();
        form.addEventListener("value-changed", (ev) => {
          this._draft = { ...this._draft, ...ev.detail.value };
          const next = shown(this._draft);
          if (next.length !== form.schema.length) form.schema = next;
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
      this._wiz = { ...w, i: 0, draft: { ...w.data }, backupPipeline: false }  // the pipelines are the user's: changing them is their choice;
      this._wizGo(0);
    }

    _closeWizard() { this._$("wiz").hidden = true; this._wizForm = null; }

    _wizGo(i) {
      const W = this._wiz, steps = W.steps, last = steps.length;  // one extra step at the end: test & finish
      W.i = Math.max(0, Math.min(i, last));
      const dots = [...steps, { title: "Test" }].map((s, n) => `<i class="${esc(n === W.i ? "on" : n < W.i ? "done" : "")}"></i>`).join("");
      const head = (icon, title, blurb) => `<div class="wizh"><div class="wizdots">${dots}</div><div class="wizn">STEP ${W.i + 1} OF ${last + 1}</div></div>
        <div class="bh"><ha-icon icon="${esc(icon)}"></ha-icon><div><div class="bt">${esc(title).toUpperCase()}</div><div class="bs">${esc(blurb)}</div></div></div>`;
      const c = this._$("wizc");
      if (W.i === last) {
        const persona = W.draft.persona || W.persona;
        c.innerHTML = head("mdi:check-decagram-outline", "Test & finish", `That's it. Say hello on the speakers to check they work, then finish. Everything can be changed later under Settings.`)
          + (W.own_agent ? `<label class="wizchk"><input type="checkbox" id="wizpipe" ${W.backupPipeline ? "checked" : ""}><span>Use <b>${esc(persona)} with backup</b> in my voice pipelines, so simple things still work if the AI is down</span></label>` : "")
          + `<div class="wizres" id="wizres"></div>
          <div class="wizbar"><button class="btn ghost" data-act="wizback">BACK</button><span></span><button class="btn ghost" data-act="wiztest"><ha-icon icon="mdi:speaker-play"></ha-icon> SAY HELLO</button><button class="btn ghost" data-act="wizdoctor"><ha-icon icon="mdi:stethoscope"></ha-icon> FINISH &amp; CHECK THE SETUP</button><button class="btn" data-act="wizdone">FINISH</button></div>`;
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
      const opts = (sel) => cats.map((c) => `<option value="${esc(c)}"${c === sel ? " selected" : ""}>${c[0].toUpperCase() + c.slice(1)}</option>`).join("");
      const byCat = {};
      items.forEach((m) => { byCat[m.category] = (byCat[m.category] || 0) + 1; });
      const keepFocus = this.shadowRoot.activeElement && this.shadowRoot.activeElement.id === "mfind";
      const rows = shown.map((m) => this._editing === m.id
        ? `<form class="r edit" data-form="edit" data-id="${esc(m.id)}"><ha-icon icon="${esc(CAT_ICON[m.category] || "mdi:brain")}"></ha-icon>
            <input name="text" value="${esc(m.text)}" autocomplete="off"><select name="category">${opts(m.category)}</select>
            <button class="btn sm" type="submit">SAVE</button><a class="x" data-act="noedit" title="Cancel">✕</a></form>`
        : `<div class="r mem"><ha-icon icon="${esc(CAT_ICON[m.category] || "mdi:brain")}"></ha-icon><b class="wrap" data-act="edit" data-id="${esc(m.id)}" title="Tap to edit">${esc(m.text)}${(m.links || []).length || m.expires ? `<small>${m.expires && !m.pinned ? "UNTIL " + esc(new Date(m.expires).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }).toUpperCase()) : ""}${m.expires && !m.pinned && (m.links || []).length ? " · " : ""}${(m.links || []).length ? "ABOUT " + esc(m.links.map((l) => l.startsWith("area:") ? l.slice(5).replace(/_/g, " ") : String((this._hass.states[l] || {}).attributes?.friendly_name || l)).join(", ").toUpperCase()) : ""}</small>` : ""}</b>
            <s>${esc(m.category).toUpperCase()}</s><u>${day(m.created)}${m.hits ? ` · USED ${m.hits}×` : ""}</u>
            <a class="x${m.pinned ? " pin" : ""}" data-act="pin" data-id="${esc(m.id)}" title="${m.pinned ? "Pinned: always given to the voice — tap to unpin" : "Pin: always give this to the voice"}"><ha-icon icon="${m.pinned ? "mdi:pin" : "mdi:pin-outline"}"></ha-icon></a><a class="x" data-act="edit" data-id="${esc(m.id)}" title="Edit">✎</a><a class="x" data-act="forget" data-id="${esc(m.id)}" data-text="${esc(m.text)}" title="Forget">✕</a></div>`).join("");
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
      const poly = (pts, label, cls) => `<svg class="shape ${cls || ""}" viewBox="0 0 100 100" preserveAspectRatio="none"><polygon points="${esc(pts.map((p) => `${p[0] * 100},${p[1] * 100}`).join(" "))}"/></svg>`
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
          <div class="row"><select id="spcam">${cams.map((c) => `<option value="${esc(c.id)}"${c.id === this._cam ? " selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
            <button class="btn ghost sm" data-act="snap">↻ NEW PICTURE</button></div>
          <div class="snap${this._drawing ? " drawing" : ""}" id="snap">${this._cam ? `<img src="${esc(this._snapUrl(this._cam))}" alt="" draggable="false">` : '<div class="e">NO CAMERAS</div>'}
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
          ${here.map((sp) => `<div class="r"><ha-icon icon="${esc(sp.points ? "mdi:vector-polygon" : "mdi:selection-marker")}"></ha-icon><b>${esc(sp.name)}</b>
              <input class="alias" data-alias="${esc(sp.name)}" value="${esc(sp.aliases || "")}" placeholder="also called…">
              <a class="x" data-act="spforget" data-id="${esc(sp.name)}" title="Forget">✕</a></div>`).join("") || '<div class="e">NONE YET</div>'}
          <div class="h" style="margin-top:14px"><span class="t">ASK THE CAMERA</span><i></i></div>
          <form class="add" data-form="look"><ha-icon icon="mdi:eye-outline"></ha-icon><input name="q" placeholder="${esc(here[0] ? `Is anything in ${esc(here[0].name)}?` : "What can you see?")}" autocomplete="off">
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
      this._$("body").innerHTML = `<div class="bh"><ha-icon icon="mdi:playlist-play"></ha-icon><div><div class="bt">VOICE ROUTINES</div>
          <div class="bs">Routines you start — “goodnight”, “good morning”, “leaving”. Say the name to any voice assistant, tap RUN, or call sentient.run_routine. They can check for anything left open or on first. Things that should happen by themselves at a set time are Automated routines.</div></div></div>
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
      const when = (it) => (kind === "alarms" || kind === "schedules") && it._next ? `<s>NEXT ${esc(new Date(it._next).toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" }).toUpperCase())}</s>` : "";
      // learned habits the house took over: shown with the schedules, with a way to stop each one
      const learned = (data.learned || []).length ? `<div class="h" style="margin-top:14px"><span class="t">LEARNED HABITS IT DOES FOR YOU</span><i></i></div>`
        + data.learned.map((l) => `<div class="card ml"><div class="r"><ha-icon icon="mdi:school-outline"></ha-icon><b>${esc(l.text)}</b>
            <button class="btn ghost sm" data-act="unadopt" data-id="${esc(l.id)}" data-text="${esc(l.text)}">STOP</button></div></div>`).join("") : "";
      const rows = data.items.map((it) => `<div class="card ml${it.enabled === false ? " off" : ""}"><div class="r"><ha-icon icon="${esc(m.icon)}"></ha-icon><b>${esc(it.name)}</b>${when(it)}
            <label class="tg" title="On or off"><input type="checkbox" data-ittoggle="${esc(it.id)}"${it.enabled === false ? "" : " checked"}><i></i></label>
            <button class="btn ghost sm" data-act="ittest" data-id="${esc(it.id)}">TRY</button>
            <a class="x" data-act="itedit" data-id="${esc(it.id)}" title="Edit">✎</a><a class="x" data-act="itdel" data-id="${esc(it.id)}" data-text="${esc(it.name)}" title="Delete">✕</a></div>
          <div class="mlsub">${esc(it._summary || "")}</div>${it.last_answer ? `<div class="hint">LAST: ${esc(it.last_answer).toUpperCase()}</div>` : ""}</div>`).join("");
      body.innerHTML = `<div class="bh"><ha-icon icon="${esc(m.icon)}"></ha-icon><div><div class="bt">${esc(m.title).toUpperCase()}</div>
          <div class="bs">${esc(m.blurb)}</div></div></div>
        <div class="mllist">${rows || '<div class="e">NONE YET</div>'}</div>${learned}<div id="itedit"></div>
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

    // ---------- setup doctor ----------
    async _drawDoctor(refresh) {
      const body = this._$("body");
      if (!this._doc || refresh) {
        body.innerHTML = `<div class="e">CHECKING…</div>`;
        try { this._doc = await this._hass.callWS({ type: "sentient/doctor" }); } catch (e) { body.innerHTML = `<div class="e">${esc(e.message || String(e))}</div>`; return; }
        this._drawSide();
      }
      if (this._group !== "doctor") return;
      const ic = { ok: "mdi:check-circle-outline", warn: "mdi:alert-outline", bad: "mdi:close-circle-outline" };
      const areas = [...new Set(this._doc.rows.map((r) => r.area))];
      const rows = areas.map((a) => `<div class="h" style="margin-top:12px"><span class="t">${esc(a).toUpperCase()}</span><i></i></div>`
        + this._doc.rows.filter((r) => r.area === a).map((r) => `<div class="card ml doc ${esc(r.status)}"><div class="r"><ha-icon icon="${esc(ic[r.status] || ic.warn)}"></ha-icon><b>${esc(r.name)}</b><u>${esc(r.status === "ok" ? "OK" : r.status === "bad" ? "FIX" : "CHECK")}</u></div>
          <div class="mlsub">${esc(r.detail)}</div>${r.fix ? `<div class="hint">${esc(r.fix)}</div>` : ""}</div>`).join("")).join("");
      body.innerHTML = `<div class="bh"><ha-icon icon="mdi:stethoscope"></ha-icon><div><div class="bt">SETUP DOCTOR</div>
          <div class="bs">Checks Sentient's setup — the AI, the voice pipeline, speakers, voices, cameras and folders — and says what to fix. Nothing is changed and no AI is used. TEST SPEAKERS says a short line on each one.</div></div></div>
        <div class="mllist">${rows}</div><div class="spmsg" id="docmsg"></div>
        <div class="bar"><span id="status"></span><button class="btn ghost" data-act="docspeak">TEST SPEAKERS</button><button class="btn" data-act="docrun">CHECK AGAIN</button></div>`;
    }

    // ---------- how it's used ----------
    async _drawUsage(refresh) {
      const body = this._$("body");
      if (!this._use || refresh) {
        body.innerHTML = `<div class="e">LOADING…</div>`;
        try { this._use = await this._hass.callWS({ type: "sentient/usage" }); } catch (e) { body.innerHTML = `<div class="e">${esc(e.message || String(e))}</div>`; return; }
        this._drawSide();
      }
      if (this._group !== "usage") return;
      const U = this._use;
      const ago = (d) => d ? new Date(d).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "";
      const row = (r, icon, right, act) => `<div class="r"><ha-icon icon="${esc(icon)}"></ha-icon><b class="wrap">${esc(r.name)}${r.last_used ? `<small>last used ${esc(ago(r.last_used))}</small>` : ""}</b>${right}${act ? `<button class="btn sm ghost" data-act="group" data-id="${esc(r.page)}">SETTINGS</button>` : ""}</div>`;
      body.innerHTML = `<div class="bh"><ha-icon icon="mdi:chart-timeline-variant-shimmer"></ha-icon><div><div class="bt">HOW IT'S USED</div>
          <div class="bs">Which optional features are earning their keep — counted when you use one by voice or it says something. Anything on but never used is worth switching off to keep things simple.${U.since ? ` Counting since ${esc(ago(U.since))}.` : ""}</div></div></div>
        <div class="card ml">${this._h("ON BUT NOT USED THIS MONTH", String(U.never.length))}${U.never.map((r) => row(r, "mdi:sleep", "", true)).join("") || `<div class="e">EVERYTHING THAT'S ON GETS USED</div>`}</div>
        <div class="card ml" style="margin-top:12px">${this._h("USED THIS MONTH", String(U.used.length))}${U.used.map((r) => row(r, "mdi:check-circle-outline", `<u>${esc(String(r.used_this_month))}×</u>`)).join("") || '<div class="e">NOTHING COUNTED YET</div>'}</div>
        ${U.off.length ? `<details class="caps"><summary>Switched off (${esc(String(U.off.length))})</summary>${U.off.map((r) => row(r, "mdi:minus-circle-outline", "", true)).join("")}</details>` : ""}
        <div class="bar"><span id="status"></span><button class="btn ghost" data-act="userun">REFRESH</button></div>`;
    }

    // ---------- what leaves the house ----------
    async _drawPrivacy(refresh) {
      const body = this._$("body");
      if (!this._priv || refresh) {
        body.innerHTML = `<div class="e">LOADING…</div>`;
        try { this._priv = await this._hass.callWS({ type: "sentient/privacy" }); } catch (e) { body.innerHTML = `<div class="e">${esc(e.message || String(e))}</div>`; return; }
        this._drawSide();
      }
      if (this._group !== "privacy") return;
      const P = this._priv;
      const n = (v, one, many) => `${v} ${v === 1 ? one : many}`;
      const what = (r) => [r.pictures && n(r.pictures, "picture", "pictures"), r.clips && n(r.clips, "clip", "clips"), r.documents && n(r.documents, "document", "documents")].filter(Boolean).join(", ") || "text only";
      const rows = Object.entries(P.by_feature || {}).map(([f, r]) => `<div class="r"><ha-icon icon="mdi:cloud-upload-outline"></ha-icon><b class="wrap">${esc(f)}<small>${esc(what(r))}</small></b><u>${esc(String(r.times))}×</u></div>`).join("");
      const recent = (P.last_48_hours || []).slice(0, 30).map((s) => `<div class="r dim"><span class="tm">${new Date(s.time).toLocaleString("en-GB", { weekday: "short", hour: "2-digit", minute: "2-digit" })}</span><b class="wrap">${esc(s.feature)}<small>${esc(what(s))} · to ${esc(s.to)}</small></b></div>`).join("");
      body.innerHTML = `<div class="bh"><ha-icon icon="mdi:shield-lock-outline"></ha-icon><div><div class="bt">WHAT LEAVES THE HOUSE</div>
          <div class="bs">Every AI call and web search Sentient makes by itself, by feature — what kind of thing was sent and where, never what it said or showed. Questions you ask a voice go through your voice agent, not here.</div></div></div>
        ${P.keeping_record ? "" : '<div class="hint">THE RECORD IS TURNED OFF · SETTINGS → MIND</div>'}
        <div class="card ml">${this._h("LAST 7 DAYS", `${esc(String(P.totals.times))} SENT · ${esc(String(P.totals.pictures))} PICTURES · ${esc(String(P.totals.clips))} CLIPS`)}${rows || '<div class="e">NOTHING SENT THIS WEEK</div>'}
          ${P.where && P.where.length ? `<div class="hint">SENT TO ${esc(P.where.join(", ")).toUpperCase()}</div>` : ""}</div>
        ${recent ? `<details class="caps"><summary>The last 48 hours, one by one</summary>${recent}</details>` : ""}
        <div class="spmsg" id="privmsg"></div>
        <div class="bar"><span id="status"></span><button class="btn ghost" data-act="privrun">REFRESH</button><button class="btn" data-act="privforget">FORGET THE LAST 48 HOURS</button></div>`;
    }

    // ---------- backup ----------
    _drawBackup() {
      this._$("body").innerHTML = `<div class="bh"><ha-icon icon="mdi:backup-restore"></ha-icon><div><div class="bt">BACKUP</div>
          <div class="bs">Everything Sentient has learned and been told — memories, camera spots, motion lights, routines, shushes and which learned routines you adopted — in one file. Restoring adds to what's here and never deletes.</div></div></div>
        <div class="card ml"><div class="r"><ha-icon icon="mdi:download"></ha-icon><b>Download a backup now</b><button class="btn sm" data-act="bkdown">DOWNLOAD</button></div>
          <div class="r"><ha-icon icon="mdi:file-document-outline"></ha-icon><b class="wrap">A readable copy<small>Memories, routines, lists and settings as plain text to read or print — not for restoring. Secrets and addresses are left out.</small></b><button class="btn sm ghost" data-act="bkread">READABLE COPY</button></div></div>
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
      const rows = (this._mutes || []).map((m) => `<div class="r"><ha-icon icon="${esc(m.kind === "entity" ? "mdi:volume-off" : "mdi:text-box-remove-outline")}"></ha-icon>
          <b>${esc(m.name)}</b><u>${m.until ? "UNTIL " + hm(m.until) : "UNTIL UNSHUSHED"}</u><a class="x" data-act="unmute" data-id="${esc(m.id)}" title="Unshush">✕</a></div>`).join("");
      this._$("body").innerHTML = `<div class="bh"><ha-icon icon="mdi:volume-mute"></ha-icon><div><div class="bt">SHUSHED</div>
          <div class="bs">Things the house won't mention — say “stop telling me about the washing machine” (or “for tonight”). Urgent and safety alerts always get through.</div></div></div>
        ${rows || '<div class="e">NOTHING SHUSHED</div>'}
        ${rows ? '<div class="bar"><span></span><button class="btn ghost" data-act="unmuteall">UNSHUSH EVERYTHING</button></div>' : ""}
        <div class="bh" style="margin-top:22px"><ha-icon icon="mdi:school-outline"></ha-icon><div><div class="bt">LEARNED FROM FEEDBACK</div>
          <div class="bs">What the house has picked up from shushes, hushing it straight after a remark, and declined or deleted routines. It fades by itself over a month or so; ✕ forgets it now.</div></div></div>
        ${(this._learnedQuiet || []).map((f) => `<div class="r"><ha-icon icon="${esc(f.kind === "offers" ? "mdi:calendar-remove-outline" : "mdi:comment-off-outline")}"></ha-icon>
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
      return `<button class="mode${on ? " on" : ""}" data-act="mode" data-id="${esc(st.entity_id)}"><ha-icon icon="${esc(icon)}"></ha-icon>${on ? onLabel + until : offLabel}</button>`;
    }

    _holidayChip() {
      const st = this._st("holiday");
      if (!st || st.state === "unavailable") return "";
      const on = st.state === "on";
      const back = on && st.attributes.until ? ` · TILL ${new Date(st.attributes.until).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" }).toUpperCase()}` : "";
      if (!on) return "";  // started from the switch or by voice; shown here while it's on
      return `<button class="mode on" data-act="mode" data-id="${esc(st.entity_id)}"><ha-icon icon="mdi:island"></ha-icon>ON HOLIDAY${esc(back)}</button>`;
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
        return `<button class="mode${lit ? " on" : ""}" data-act="show" data-id="${esc(s)}"><ha-icon icon="${esc(SHOWS[s][0])}"></ha-icon>${SHOWS[s][1]}${lit ? " ON" + until : ""}${ear}</button>`;
      }).join("");
    }

    _chimeChip() {
      const st = this._st("chime");
      if (!st) return "";
      const on = st.state === "on";
      return `<button class="mode chime${on ? " lit" : ""}" data-act="mode" data-id="${esc(st.entity_id)}"><ha-icon icon="${esc(on ? "mdi:bell-ring-outline" : "mdi:bell-off-outline")}"></ha-icon>CHIME ${on ? "ON" : "OFF"}</button>`;
    }

    _drawEnergy() {
      const el = this._$("energy"), en = this._en;
      if (!el || !en || en.enabled === false || !(en.devices || []).length) { if (el) el.hidden = true; return; }
      el.hidden = false;
      const top = en.devices.slice(0, 5), max = Math.max(0.01, ...top.map((d) => Math.max(d.kwh, d.usual)));
      const pct = en.usual_kwh > 0 ? Math.round((en.total_kwh / en.usual_kwh) * 100) : null;
      el.innerHTML = this._h("ENERGY TODAY", `${en.total_kwh} KWH${en.cost_today != null ? ` · ${en.currency_symbol || "£"}${en.cost_today.toFixed(2)}` : ""}${pct != null ? ` · ${pct}% OF USUAL` : ""}`)
        + top.map((d) => `<div class="en${d.unusual ? " hot" : ""}"><span>${esc(d.name)}</span>
            <div class="bar2"><i style="width:${Math.round((d.kwh / max) * 100)}%"></i><b style="left:${Math.min(100, Math.round((d.usual / max) * 100))}%" title="Usual by now"></b></div>
            <u>${d.kwh}${d.unusual ? " ↑" : ""}</u></div>`).join("")
        + `<div class="hint" title="Energy: today so far, added up from the devices on Home Assistant's Energy dashboard (not the grid meter). Cost: that total times the rate in Settings. % of usual: against a normal day up to this time.">UP TO ${esc(en.up_to)} · MARKER = USUAL BY NOW · ${en.cost_today != null ? "COST = THESE DEVICES × YOUR RATE · " : ""}${pct != null ? "% AGAINST A NORMAL DAY SO FAR" : ""}</div>`;
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
        return `<div class="r${a.running ? " on" : " dim"}"><ha-icon icon="${esc(icon)}"></ha-icon><b>${esc(a.name)}</b><u>${tag}</u></div>`;
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

    // ---------- overview: today and coming up ----------
    _loadToday() {
      this._todayAt = Date.now();
      this._hass.callWS({ type: "sentient/today" }).then((r) => { this._today = r; this._drawToday(); }, () => {});
    }

    _drawToday() {
      const t = this._today, el = this._$("today"), el2 = this._$("soon");
      if (!t || !el || !el2) return;
      const ic = { "clear-night": "mdi:weather-night", cloudy: "mdi:weather-cloudy", fog: "mdi:weather-fog", hail: "mdi:weather-hail", lightning: "mdi:weather-lightning", "lightning-rainy": "mdi:weather-lightning-rainy", partlycloudy: "mdi:weather-partly-cloudy", pouring: "mdi:weather-pouring", rainy: "mdi:weather-rainy", snowy: "mdi:weather-snowy", "snowy-rainy": "mdi:weather-snowy-rainy", sunny: "mdi:weather-sunny", windy: "mdi:weather-windy", exceptional: "mdi:alert-circle-outline" };
      const w = t.weather, hz = (t.heating || {}).zones || [], nx = (t.heating || {}).next;
      const tiles = [];
      if (w) tiles.push(`<div class="tt"><ha-icon icon="${esc(ic[w.condition] || "mdi:weather-partly-cloudy")}"></ha-icon><b>${esc(w.temp)}${esc(w.unit || "°")}</b><span>${esc(String(w.condition).replace(/-/g, " ").toUpperCase())}${w.wind != null ? " · WIND " + esc(w.wind) : ""}</span></div>`);
      for (const z of hz.slice(0, 2)) tiles.push(`<div class="tt${z.action === "heating" ? " warm" : ""}"><ha-icon icon="mdi:thermometer"></ha-icon><b>${esc(z.now ?? "–")}°</b><span>${esc(z.name.toUpperCase())}${z.state === "off" ? " · HEATING OFF" : z.target != null ? " · SET " + esc(z.target) + "°" : ""}</span></div>`);
      const ppl = (t.people || []).map((p) => `<span class="pp${p.home ? " home" : ""}">${esc(p.name.toUpperCase())} · ${p.home ? "HOME" : esc(String(p.where).replace(/_/g, " ").toUpperCase())}</span>`).join("");
      el.innerHTML = this._h("TODAY", new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" }).toUpperCase())
        + `<div class="ttiles">${tiles.join("") || '<div class="e">CHOOSE A WEATHER ENTITY IN SETTINGS → BRIEFINGS</div>'}</div>`
        + (ppl ? `<div class="ppl">${ppl}</div>` : "")
        + (nx ? `<div class="hint">HEATING NEXT: ${esc(new Date(nx.time).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" }).toUpperCase())} ${nx.what === "on" ? "ON TO " + esc(nx.temp) + "°" : "OFF"} · ${esc(nx.name.toUpperCase())}</div>` : "");
      const kic = { alarm: "mdi:alarm", routine: "mdi:calendar-sync", bins: "mdi:trash-can-outline", watch: "mdi:eye-outline" };
      const when = (iso) => { const d = new Date(iso), today = new Date(); const tm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); return d.toDateString() === today.toDateString() ? tm : d.toLocaleDateString(undefined, { weekday: "short" }).toUpperCase() + " " + tm; };
      el2.innerHTML = this._h("COMING UP", (t.soon || []).length ? `${t.soon.length}` : "")
        + ((t.soon || []).map((x) => `<div class="r${x.kind === "routine" ? " dim" : ""}"><ha-icon icon="${esc(kic[x.kind] || "mdi:clock-outline")}"></ha-icon><b>${esc(x.what)}</b><u>${esc(when(x.when))}</u></div>`).join("")
          || '<div class="e">NOTHING DUE IN THE NEXT DAY OR SO</div>');
    }

    // ---------- the day so far (overview) ----------
    _loadDay() {
      this._dayAt = Date.now();
      this._hass.callWS({ type: "sentient/day" }).then((r) => { this._day = r; this._drawDay(); }, () => { this._day = null; this._drawDay(); });
    }

    _drawDay() {
      const el = this._$("day");
      if (!el) return;
      const d = this._day;
      el.hidden = !(d && d.on);
      if (!d || !d.on) { el.innerHTML = ""; return; }
      const G = [["movement", "MOVEMENT"], ["spoke", "SAID"], ["outside", "OUTSIDE"], ["you", "YOU ASKED"]];
      const top = Math.max(1, ...d.hours.map((h) => G.reduce((n, [k]) => n + h[k], 0)));
      const cols = d.hours.map((h, i) => {
        const tot = G.reduce((n, [k]) => n + h[k], 0), later = i > Math.floor(d.now);
        const segs = G.filter(([k]) => h[k]).map(([k]) => `<i class="${k}" style="height:${(h[k] / top * 100).toFixed(1)}%"></i>`).join("");
        return `<div class="dc${later ? " later" : ""}${i === Math.floor(d.now) ? " nowc" : ""}" title="${String(i).padStart(2, "0")}:00 · ${tot} event${tot === 1 ? "" : "s"}">${segs}</div>`;
      }).join("");
      const ticks = [0, 6, 12, 18].map((h) => `<span style="left:${(h / 24 * 100).toFixed(2)}%">${String(h).padStart(2, "0")}</span>`).join("");
      const facts = [d.first_move ? `FIRST UP ${esc(d.first_move)}` : "", d.busiest ? `BUSIEST ${esc(d.busiest)}` : ""].filter(Boolean).join(" · ");
      el.innerHTML = this._h("THE DAY SO FAR", facts)
        + `<div class="dchart">${cols}</div><div class="dticks">${ticks}</div>`
        + `<div class="dkey">${G.map(([k, l]) => `<span class="${k}"><i></i>${d.totals[k]} ${l}</span>`).join("")}</div>`;
    }

    // ---------- what it's thinking (Mind tab) ----------
    _loadInner() {
      if (this._innerAt && Date.now() - this._innerAt < 60000) return;
      this._innerAt = Date.now();
      this._hass.callWS({ type: "sentient/inner" }).then((r) => { this._inner = r; this._drawInner(); }, () => { this._inner = null; this._drawInner(); });
      this._hass.callWS({ type: "sentient/memory/review" }).then((r) => { this._mcheck = r.pairs || []; this._drawCheck(); }, () => {});
    }

    _drawCheck() {
      const el = this._$("mcheck");
      if (!el) return;
      const pairs = this._mcheck || [];
      el.hidden = !pairs.length;
      if (!pairs.length) { el.innerHTML = ""; return; }
      const admin = this._hass.user && this._hass.user.is_admin;
      el.innerHTML = this._h("MEMORY CHECK-UP", `${pairs.length} TO LOOK AT`)
        + pairs.map((p) => `<div class="mc"><div class="mck">${p.kind === "conflict" ? "THESE SAY DIFFERENT THINGS" : "THE SAME THING TWICE"}</div>`
          + `<div class="mct"><b>${esc(p.newer.text)}</b><small>NEWER · ${esc(p.newer.category.toUpperCase())}${p.newer.pinned ? " · PINNED" : ""}</small></div>`
          + `<div class="mct"><b>${esc(p.older.text)}</b><small>OLDER · ${esc(p.older.category.toUpperCase())}${p.older.pinned ? " · PINNED" : ""}</small></div>`
          + (admin ? `<div class="row"><button class="btn sm" data-act="mckeep" data-id="${esc(p.pair)}" data-keep="${esc(p.newer.id)}">KEEP NEWER</button><button class="btn sm ghost" data-act="mckeep" data-id="${esc(p.pair)}" data-keep="${esc(p.older.id)}">KEEP OLDER</button><button class="btn sm ghost" data-act="mckeep" data-id="${esc(p.pair)}" data-keep="">BOTH ARE RIGHT</button></div>` : "")
          + `</div>`).join("");
    }

    _drawInner() {
      const el = this._$("minner");
      if (!el) return;
      const i = this._inner;
      this._drawKnow();
      const off = !i || !(i.learn || i.day || i.thoughts_on);
      el.hidden = off;
      if (off) { el.innerHTML = ""; return; }
      const icon = { lesson: "mdi:school-outline", day: "mdi:weather-sunset-up", guess: "mdi:crystal-ball", memory: "mdi:brain" };
      const admin = this._hass.user && this._hass.user.is_admin;
      const th = i.thoughts || [];
      const bits = [i.corrections != null ? `${i.corrections} CORRECTIONS · 2 WEEKS` : "", i.prediction && i.prediction.tried ? `NEXT ROOM ${Math.round((i.prediction.rate || 0) * 100)}% RIGHT` : ""].filter(Boolean).join(" · ");
      el.innerHTML = this._h("WHAT I'M THINKING", th.length ? `${th.length}` : "")
        + (th.map((t) => `<div class="r"><ha-icon icon="${esc(icon[t.kind] || "mdi:thought-bubble-outline")}"></ha-icon><b class="wrap">${esc(t.text)}<small>${esc(t.why || "")}</small></b>${admin && !["guess", "memory"].includes(t.kind) ? `<button class="btn sm ghost" data-act="innerno" data-id="${esc(t.key)}" title="Dismiss">✕</button>` : ""}</div>`).join("")
          || '<div class="e">NOTHING TO REPORT · IT SAYS SO WHEN IT LEARNS SOMETHING</div>')
        + (i.noisy || []).map((n) => `<div class="r dim"><ha-icon icon="mdi:volume-off"></ha-icon><b class="wrap">I keep deciding the ${esc(n.name.toLowerCase())} isn't worth mentioning (${n.times} times this week)<small>each look costs an AI call</small></b>${admin ? `<button class="btn sm ghost" data-act="unwatch" data-id="${esc(n.entity_id)}">STOP WATCHING</button>` : ""}</div>`).join("")
        + (bits ? `<div class="hint">${esc(bits)}</div>` : "");
    }

    // ---------- rooms at a glance (House tab) and around the house (overview) ----------
    _loadGlance() {
      this._glAt = Date.now();
      this._hass.callWS({ type: "sentient/glance" }).then((r) => { this._gl = r; this._drawRooms(); this._drawAround(); }, () => { this._gl = null; this._drawRooms(); this._drawAround(); });
    }

    _ago(m) {
      return m == null ? "" : m < 1 ? "JUST NOW" : m < 60 ? `${m}M AGO` : m < 1440 ? `${Math.floor(m / 60)}H AGO` : `${Math.floor(m / 1440)}D AGO`;
    }

    _drawRooms() {
      const el = this._$("rooms");
      if (!el) return;
      const rows = this._gl && this._gl.on ? this._gl.rooms || [] : [];
      el.hidden = !rows.length;
      if (!rows.length) { el.innerHTML = ""; return; }
      const lit = rows.reduce((n, r) => n + r.lights_on.length, 0), open = rows.reduce((n, r) => n + r.open.length, 0);
      const tile = (r) => {
        const on = r.lights_on.length;
        const chips = [
          r.lights ? `<button class="rc${on ? " on" : ""}"${on ? ` data-act="roomlights" data-id="${esc(r.area_id)}" title="Turn off: ${esc(r.lights_on.join(", "))}"` : " disabled"}><ha-icon icon="${on ? "mdi:lightbulb-on" : "mdi:lightbulb-outline"}"></ha-icon>${on ? `${on} ON` : "OFF"}</button>` : "",
          r.open.length ? `<span class="rc warn" title="${esc(r.open.join(", "))}"><ha-icon icon="mdi:window-open-variant"></ha-icon>${r.open.length} OPEN</span>` : "",
          r.moving || r.moved_mins != null ? `<span class="rc${r.moving ? " live" : ""}"><ha-icon icon="mdi:motion-sensor"></ha-icon>${r.moving ? "MOVING" : this._ago(r.moved_mins)}</span>` : "",
          r.playing.length ? `<span class="rc live" title="${esc(r.playing.join(", "))}"><ha-icon icon="mdi:music"></ha-icon>${esc(r.playing[0].toUpperCase())}</span>` : "",
        ].join("");
        return `<div class="rt${r.likely ? " here" : ""}${on ? " lit" : ""}"><div class="rn"><ha-icon icon="${esc(r.icon || "mdi:floor-plan")}"></ha-icon><b title="${esc(r.name)}">${esc(r.name)}</b>${r.likely ? "<s>HERE</s>" : ""}</div>`
          + `<div class="rv"><span class="rtemp">${r.temp != null ? esc(r.temp) + "°" : "–"}</span>${r.humidity != null ? `<span class="rh">${esc(r.humidity)}%</span>` : ""}${r.heating ? `<ha-icon class="fl" icon="mdi:fire" title="${esc(r.heating)} heating"></ha-icon>` : ""}</div>`
          + `<div class="rs">${chips}</div></div>`;
      };
      el.innerHTML = this._h("ROOMS", `${rows.length} ROOMS${lit ? ` · ${lit} LIGHT${lit > 1 ? "S" : ""} ON` : ""}${open ? ` · ${open} OPEN` : ""}`) + `<div class="rgrid">${rows.map(tile).join("")}</div>`;
    }

    _drawAround() {
      const el = this._$("around");
      if (!el) return;
      const s = this._gl && this._gl.on ? this._gl.summary : null;
      el.hidden = !s;
      if (!s) { el.innerHTML = ""; return; }
      const rows = [];
      if (s.lights_on.length) rows.push(`<div class="r"><ha-icon icon="mdi:lightbulb-on"></ha-icon><b class="wrap">Lights on<small>${s.lights_on.map((l) => `<a class="lnk" data-act="roomlights" data-id="${esc(l.area_id)}" title="Turn them off">${esc(l.room)}${l.count > 1 ? " ×" + l.count : ""}</a>`).join(" · ")}</small></b><u>TAP A ROOM · OFF</u></div>`);
      else rows.push(`<div class="r dim"><ha-icon icon="mdi:lightbulb-outline"></ha-icon><b>All the lights are off</b></div>`);
      if (s.open.length) rows.push(`<div class="r warn"><ha-icon icon="mdi:window-open-variant"></ha-icon><b class="wrap">${s.open.length} open<small>${esc(s.open.map((o) => o.what).join(" · "))}</small></b></div>`);
      if (s.moving.length) rows.push(`<div class="r"><ha-icon icon="mdi:motion-sensor"></ha-icon><b class="wrap">Movement now<small>${esc(s.moving.join(" · "))}</small></b></div>`);
      if (s.playing.length) rows.push(`<div class="r"><ha-icon icon="mdi:music"></ha-icon><b class="wrap">Playing<small>${esc(s.playing.join(" · "))}</small></b></div>`);
      if (s.warmest) rows.push(`<div class="r"><ha-icon icon="mdi:thermometer-lines"></ha-icon><b class="wrap">Warmest ${esc(s.warmest.room)} ${esc(s.warmest.temp)}°<small>coolest ${esc(s.coolest.room)} ${esc(s.coolest.temp)}°</small></b></div>`);
      el.innerHTML = this._h("AROUND THE HOUSE", s.open.length ? `${s.open.length} OPEN` : "ALL SHUT") + rows.join("");
    }

    _drawKnow() {
      const el = this._$("mknow");
      if (!el) return;
      const k = this._inner && this._inner.knows;
      el.hidden = !k;
      if (!k) { el.innerHTML = ""; return; }
      el.innerHTML = this._h("WHAT I KNOW", `${k.length}`)
        + (k.map((x) => `<div class="r${x.warn ? " warn" : ""}"><ha-icon icon="${esc(x.icon)}"></ha-icon><b class="wrap">${esc(x.text)}${x.why ? `<small>${esc(x.why)}</small>` : ""}</b></div>`).join("")
          || '<div class="e">NOTHING LEARNED YET · GIVE IT A FEW DAYS</div>');
    }

    // the heating's last day: temperature against the setting, with the times it was heating shaded
    _loadHeatGraph() {
      const h = this._heat;
      if (!h || !h.zones.length) return;
      this._hgAt = Date.now();
      const ids = h.zones.slice(0, 3).map((z) => z.entity_id);
      this._hass.callWS({ type: "history/history_during_period", start_time: new Date(Date.now() - 864e5).toISOString(), entity_ids: ids, minimal_response: false, no_attributes: false, significant_changes_only: false })
        .then((r) => { this._hg = r; const g = this._$("hgraph"); if (g) g.innerHTML = this._heatGraph(); }, () => { this._hg = {}; });
    }

    _heatGraph() {
      const h = this._heat, hist = this._hg;
      if (!hist) return '<div class="hg"><div class="hgl" style="left:10px;top:8px">LOADING…</div></div>';
      const end = Date.now(), start = end - 864e5, W = 1440, H = 100;
      const out = [];
      for (const z of h.zones.slice(0, 3)) {
        const rows = hist[z.entity_id] || [];
        if (!rows.length) continue;
        const pts = [], set = [], heat = [];
        let last = null;
        rows.forEach((row, i) => {
          const t = Math.max(start, (row.lu || row.lc || 0) * 1000), a = row.a || last || {};
          last = a;
          const nextT = i + 1 < rows.length ? Math.max(start, (rows[i + 1].lu || rows[i + 1].lc || 0) * 1000) : end;
          if (a.current_temperature != null) pts.push([t, +a.current_temperature]);
          set.push([t, row.s === "off" ? null : a.temperature != null ? +a.temperature : null, nextT]);
          if (a.hvac_action === "heating") heat.push([t, nextT]);
        });
        if (pts.length < 2) continue;
        pts.push([end, pts[pts.length - 1][1]]);
        const vals = [...pts.map((p) => p[1]), ...set.filter((x) => x[1] != null).map((x) => x[1])];
        const lo = Math.floor(Math.min(...vals) - 0.5), hi = Math.ceil(Math.max(...vals) + 0.5);
        const X = (t) => ((t - start) / 864e5 * W).toFixed(1), Y = (v) => (H - 6 - (v - lo) / Math.max(1, hi - lo) * (H - 18)).toFixed(1);
        const line = pts.map((p, i) => `${i ? "L" : "M"}${X(p[0])} ${Y(p[1])}`).join("");
        const steps = set.filter((x) => x[1] != null).map((x) => `M${X(x[0])} ${Y(x[1])}H${X(x[2])}`).join("");
        const shade = heat.map(([a, b]) => `<rect x="${X(a)}" y="0" width="${Math.max(1, X(b) - X(a))}" height="${H}"></rect>`).join("");
        const ticks = [6, 12, 18].map((hh) => { const d = new Date(end); d.setHours(hh, 0, 0, 0); if (d.getTime() > end) d.setDate(d.getDate() - 1); return `<div class="hgl tk" style="left:${((d.getTime() - start) / 864e5 * 100).toFixed(1)}%">${String(hh).padStart(2, "0")}:00</div>`; }).join("");
        out.push(`<div class="hg">${h.zones.length > 1 ? `<div class="hgl nm">${esc(z.name.toUpperCase())}</div>` : ""}<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><g class="heat">${shade}</g><path class="set" d="${steps}"></path><path class="now" d="${line}"></path></svg>`
          + `<div class="hgl" style="right:6px;top:4px">${hi}°</div><div class="hgl" style="right:6px;bottom:4px">${lo}°</div>${ticks}</div>`);
      }
      return out.join("") ? out.join("") + `<div class="hgk"><span class="k now"></span>TEMPERATURE <span class="k set"></span>SET TO <span class="k heat"></span>HEATING</div>` : '<div class="hint">NO HISTORY FOR THE LAST DAY YET</div>';
    }

    // ---------- heating (House tab) ----------
    _loadHeat() {
      this._heatAt = Date.now();
      return this._hass.callWS({ type: "sentient/heating" }).then((r) => { this._heat = r; this._drawHeat(); }, () => { this._heat = null; this._drawHeat(); });
    }

    _drawHeat() {
      const el = this._$("heat");
      if (!el || this._heatEdit) return;
      const h = this._heat;
      if (!h) { el.innerHTML = this._h("HEATING", "") + '<div class="e">LOADING THE HEATING…</div>'; return; }
      const D = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
      const daysTxt = (d) => (d.length === 7 ? "EVERY DAY" : d.join() === D.slice(0, 5).join() ? "WEEKDAYS" : d.join() === "sat,sun" ? "WEEKENDS" : d.map((x) => x.toUpperCase()).join(" "));
      const zname = (id) => ((h.zones.find((z) => z.entity_id === id) || {}).name || id);
      const zone = (z) => {
        const live = this._hass.states[z.entity_id], mode = live ? live.state : z.state;
        const tag = ["unavailable", "unknown"].includes(mode) ? "OFFLINE" : mode === "off" ? "OFF" : z.action === "heating" ? "HEATING" : "IDLE";
        const sch = z.scheduled && z.scheduled.on ? `SCHEDULED ${z.scheduled.temp}° TILL ${z.scheduled.until}` : "";
        const nums = [z.now != null ? `${z.now}° NOW` : "", z.target != null && mode !== "off" ? `TARGET ${z.target}°` : "", sch].filter(Boolean).join(" · ");
        return `<div class="r${tag === "HEATING" ? " on" : tag === "OFFLINE" ? " bad" : tag === "OFF" ? " dim" : ""}"><ha-icon icon="${esc(tag === "HEATING" ? "mdi:radiator" : tag === "OFF" ? "mdi:radiator-off" : "mdi:radiator-disabled")}"></ha-icon><b>${esc(z.name)}</b><u>${esc(nums)}${nums ? " · " : ""}${tag}</u></div>`;
      };
      let html = this._h("HEATING", h.zones.length ? `${h.zones.length} HEATER${h.zones.length > 1 ? "S" : ""}${h.warming_now.length ? " · WARMING " + h.warming_now.join(", ").toUpperCase() : ""}` : "NO THERMOSTATS CHOSEN")
        + (h.zones.map(zone).join("") || '<div class="e">CHOOSE YOUR THERMOSTATS UNDER ZONE SETTINGS</div>');
      if (h.graph && h.zones.length) {
        html += `<div class="sub">LAST 24 HOURS</div><div id="hgraph">${this._heatGraph()}</div>`;
        if (!this._hgAt || Date.now() - this._hgAt > 600000) this._loadHeatGraph();
      }
      if (h.schedule_on) {
        const nx = h.next ? `NEXT: ${new Date(h.next.time).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" }).toUpperCase()} ${h.next.what === "on" ? "ON TO " + h.next.temp + "°" : "OFF"}` : "";
        html += `<div class="sub">SCHEDULE${nx ? " · " + esc(nx) : ""}</div>`;
        if ((h.programmes || []).length && (!h.ticking || Date.now() - new Date(h.ticking) > 180000)) html += `<div class="r bad"><ha-icon icon="mdi:clock-alert-outline"></ha-icon><b class="wrap">The schedule's minute check isn't running<small>Nothing will switch until Home Assistant is restarted — if this stays after a restart, please report it</small></b></div>`;
        html += (h.programmes || []).map((p) => `<div class="prog${p.enabled ? "" : " dim"}"><div class="ph"><b>${esc(p.name)}</b><u>${esc(daysTxt(p.days))} · ${esc(p.heaters.map(zname).join(", "))}</u>`
          + (h.admin ? `<button class="btn sm ghost" data-act="progonoff" data-id="${esc(p.id)}">${p.enabled ? "ON" : "OFF"}</button><button class="btn sm ghost" data-act="progedit" data-id="${esc(p.id)}">EDIT</button><button class="btn sm ghost" data-act="progdel" data-id="${esc(p.id)}" title="Delete">✕</button>` : "")
          + `</div><div class="pbar">${this._progBar(p.periods)}</div><div class="pchips">${p.periods.map((x) => `<span>${esc(x.on)}–${esc(x.off)} · ${esc(x.temp)}°</span>`).join("")}</div>${this._progRan((h.ran || {})[p.name])}</div>`).join("")
          || ((h.suggested || []).length ? "" : '<div class="e">NO HEATING SCHEDULE YET</div>');
        if (!(h.programmes || []).length && (h.suggested || []).length) {
          html += `<div class="hint">A STARTING POINT FROM WHAT I'VE LEARNED (WHEN YOU'RE USUALLY UP AND HOW FAST THE HOUSE WARMS) — NOTHING IS SAVED UNTIL YOU SAY SO</div>`
            + h.suggested.map((p, i) => `<div class="prog sug"><div class="ph"><b>${esc(p.name)}</b><u>${esc(daysTxt(p.days))}</u>${h.admin ? `<button class="btn sm" data-act="progsug" data-id="${i}">USE THIS</button><button class="btn sm ghost" data-act="progsugedit" data-id="${i}">CHANGE IT</button>` : ""}</div><div class="pbar">${this._progBar(p.periods)}</div><div class="pchips">${p.periods.map((x) => `<span>${esc(x.on)}–${esc(x.off)} · ${esc(x.temp)}°</span>`).join("")}</div></div>`).join("");
        }
        if ((h.schedules || []).length) html += `<div class="hint">${h.schedules.length} OLDER SINGLE HEATING TIME${h.schedules.length > 1 ? "S" : ""} STILL RUN · ` + h.schedules.map((x) => `${esc(x.time)} ${esc(x.what)} <a class="lnk" data-act="heatdel" data-id="${esc(x.id)}">REMOVE</a>`).join(" · ") + "</div>";
        if (h.admin && h.zones.length) html += `<div class="row"><button class="btn sm" data-act="prognew">+ NEW SCHEDULE</button></div>`;
      } else {
        html += `<div class="hint">HEATING SCHEDULE IS OFF · <a class="lnk" data-act="goto" data-id="heating">TURN IT ON IN SETTINGS</a></div>`;
      }
      const up = [h.usually_up && h.usually_up.weekdays && `UP ${h.usually_up.weekdays} WEEKDAYS`, h.warm_by && h.warm_by.weekdays && `WARM BY ${h.warm_by.weekdays}`].filter(Boolean).join(" · ");
      if (up) html += `<div class="hint">${esc(up)}</div>`;
      if (h.admin) html += `<div class="row"><button class="btn sm ghost" data-act="heatzones">ZONE SETTINGS</button></div><div id="heatzones"></div>`;
      el.innerHTML = html;
      if (this._heatZonesOpen) this._openHeatZones(true);
    }

    // what a programme did the last time it acted
    _progRan(r) {
      if (!r) return '<div class="pran">HASN\'T SWITCHED ANYTHING SINCE HOME ASSISTANT STARTED</div>';
      const t = new Date(r.time), today = t.toDateString() === new Date().toDateString();
      const when = (today ? "" : t.toLocaleDateString(undefined, { weekday: "short" }).toUpperCase() + " ") + t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      return `<div class="pran${r.ok === false ? " bad" : ""}">LAST: ${esc(when)} · ${r.ok === false ? "✕ " : r.ok ? "✓ " : ""}${esc(String(r.why || "").toUpperCase())}</div>`;
    }

    // a 24-hour bar with the on periods filled in
    _progBar(periods) {
      const m = (t) => { const [a, b] = t.split(":"); return +a * 60 + +b; };
      const segs = [];
      for (const p of periods) {
        const on = m(p.on), off = m(p.off);
        if (on < off) segs.push([on, off]); else { segs.push([on, 1440]); segs.push([0, off]); }
      }
      return segs.map(([a, b]) => `<i style="left:${(a / 14.4).toFixed(2)}%;width:${((b - a) / 14.4).toFixed(2)}%"></i>`).join("")
        + [6, 12, 18].map((hh) => `<s style="left:${(hh / 24 * 100).toFixed(2)}%">${hh}</s>`).join("");
    }

    _editProg(p) {
      const el = this._$("heat"), h = this._heat;
      if (!el || !h) return;
      this._heatEdit = true;
      const D = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
      const cur = p || { name: "", heaters: h.zones.map((z) => z.entity_id), days: D.slice(0, 5), periods: [{ on: "06:00", off: "09:00", temp: 21 }], enabled: true };
      this._progDraft = JSON.parse(JSON.stringify(cur));
      const row = (x, i) => `<div class="pper"><input type="time" data-pi="${i}" data-pk="on" value="${esc(x.on)}"><span>TO</span><input type="time" data-pi="${i}" data-pk="off" value="${esc(x.off)}"><input type="number" data-pi="${i}" data-pk="temp" min="${h.min}" max="${h.max}" step="0.5" value="${esc(x.temp)}"><span>°</span><button class="btn sm ghost" data-act="progrmper" data-id="${i}" title="Remove">✕</button></div>`;
      const draw = () => {
        const d = this._progDraft;
        el.innerHTML = this._h(p && p.id ? "EDIT SCHEDULE" : "NEW SCHEDULE", "")
          + `<form class="hform" id="progform" onsubmit="return false"><input type="text" id="pname" placeholder="Name, e.g. Weekdays" value="${esc(d.name)}" style="flex:1 1 100%">`
          + `<div class="sub" style="flex-basis:100%">HEATERS</div><div class="dchips">${h.zones.map((z) => `<label><input type="checkbox" data-heater="${esc(z.entity_id)}"${d.heaters.includes(z.entity_id) ? " checked" : ""}><span>${esc(z.name.toUpperCase())}</span></label>`).join("")}</div>`
          + `<div class="sub" style="flex-basis:100%">DAYS · <a class="lnk" data-act="progdays" data-id="wk">WEEKDAYS</a> · <a class="lnk" data-act="progdays" data-id="we">WEEKENDS</a> · <a class="lnk" data-act="progdays" data-id="all">EVERY DAY</a></div><div class="dchips">${D.map((x) => `<label><input type="checkbox" data-day="${x}"${d.days.includes(x) ? " checked" : ""}><span>${x.toUpperCase()}</span></label>`).join("")}</div>`
          + `<div class="sub" style="flex-basis:100%">ON / OFF TIMES</div>${d.periods.map(row).join("")}`
          + `<div class="pbar" style="flex-basis:100%">${this._progBar(d.periods.filter((x) => x.on && x.off && x.on !== x.off))}</div>`
          + `<div class="row" style="flex-basis:100%"><button class="btn sm ghost" data-act="progaddper">+ ADD ON/OFF TIME</button><span style="flex:1"></span><span id="progmsg" class="hint"></span><button class="btn sm ghost" data-act="progcancel">CANCEL</button><button class="btn sm" data-act="progsave" data-id="${esc(p ? p.id : "")}">SAVE</button></div></form>`;
        const form = this._$("progform");
        form.addEventListener("input", (e) => {
          const t = e.target, dd = this._progDraft;
          if (t.id === "pname") dd.name = t.value;
          else if (t.dataset.pi != null) { dd.periods[+t.dataset.pi][t.dataset.pk] = t.dataset.pk === "temp" ? Number(t.value) : t.value; const bar = form.querySelector(".pbar"); if (bar) bar.innerHTML = this._progBar(dd.periods.filter((x) => x.on && x.off && x.on !== x.off)); }
        });
        form.addEventListener("change", (e) => {
          const t = e.target, dd = this._progDraft;
          if (t.dataset.heater) dd.heaters = t.checked ? [...new Set([...dd.heaters, t.dataset.heater])] : dd.heaters.filter((x) => x !== t.dataset.heater);
          if (t.dataset.day) dd.days = D.filter((x) => (x === t.dataset.day ? t.checked : dd.days.includes(x)));
        });
      };
      this._progRedraw = draw;
      draw();
    }

    async _saveProg(id) {
      const msg = this._$("progmsg");
      try {
        const body = { type: "sentient/heating/programme/save", programme: this._progDraft };
        if (id) body.programme_id = id;
        await this._hass.callWS(body);
        this._heatEdit = false; this._heatAt = 0; await this._loadHeat();
      } catch (e) { if (msg) msg.textContent = (e.message || String(e)).toUpperCase(); }
    }

    async _openHeatZones(keep) {
      const wrap = this._$("heatzones");
      if (!wrap) return;
      if (!keep && this._heatZonesOpen) { this._heatZonesOpen = false; this._heatEdit = false; wrap.innerHTML = ""; return; }
      this._heatZonesOpen = true; this._heatEdit = true;
      try { if (!this._settings) this._settings = await this._hass.callWS({ type: "sentient/settings", language: this._hass.language }); }
      catch (e) { wrap.innerHTML = `<div class="e">${esc(e.message || e)}</div>`; return; }
      const g = this._settings.groups.find((x) => x.id === "heating_zones");
      if (!g || !(await loadForm())) { wrap.innerHTML = '<div class="e">THE FORM COULDN\'T LOAD</div>'; return; }
      const S = this._settings;
      this._heatDraft = { ...S.data };
      const form = document.createElement("ha-form");
      form.hass = this._hass; form.schema = g.schema; form.data = this._heatDraft;
      form.computeLabel = (sc) => S.labels[sc.name] || sc.name;
      form.computeHelper = (sc) => S.helpers[sc.name] || "";
      form.localizeValue = (key) => S.selector[key] || key.split(".").pop();
      form.addEventListener("value-changed", (ev) => { this._heatDraft = { ...ev.detail.value }; form.data = this._heatDraft; });
      wrap.innerHTML = '<div class="bar"><span id="heatzmsg"></span><button class="btn sm" data-act="heatzsave">SAVE ZONE SETTINGS</button></div>';
      wrap.insertBefore(form, wrap.firstChild);
    }

    async _saveHeatZones() {
      const g = this._settings.groups.find((x) => x.id === "heating_zones");
      const data = {};
      for (const n of g.schema.map((x) => x.name)) if (this._heatDraft[n] !== undefined && this._heatDraft[n] !== null && this._heatDraft[n] !== "") data[n] = this._heatDraft[n];
      const msg = this._$("heatzmsg");
      try {
        const res = await this._hass.callWS({ type: "sentient/settings/save", group: "heating_zones", data });
        this._settings.data = res.data;
        if (msg) msg.textContent = "SAVED ✓";
        this._heatEdit = false; this._heatAt = 0; await this._loadHeat();
      } catch (e) { if (msg) msg.textContent = (e.message || String(e)).toUpperCase(); }
    }

    // ---------- security: live view that follows motion, linked events, snapshots ----------
    _camOf(e) {  // a Frigate event's camera ("gate") or an evidence source ("camera.gate") → a camera entity
      const id = String(e.camera ? "camera." + e.camera : e.source || "");
      return id.startsWith("camera.") && this._hass.states[id] ? id : null;
    }

    _drawLive(mind) {
      const el = this._$("live");
      if (!el) return;
      const cams = ((this._cams || {}).cameras || []).map((c) => c.entity_id);
      const events = (mind.security_events || []).filter((e) => !e.parked);
      const latest = events.find((e) => this._camOf(e));
      const active = events.filter((e) => e.end == null && this._camOf(e));
      if (this._liveFollow !== false && latest) this._liveCam = this._camOf(latest);
      if (!this._liveCam) this._liveCam = cams[0];
      const cam = this._liveCam, st = cam && this._hass.states[cam];
      if (!st) { el.innerHTML = this._h("LIVE", "") + '<div class="e">NO CAMERAS FOUND</div>'; return; }
      const here = active.filter((e) => this._camOf(e) === cam).map((e) => e.label.toUpperCase());
      const pic = String(st.attributes.entity_picture || "");
      const stream = pic.replace("/api/camera_proxy/", "/api/camera_proxy_stream/");
      const key = cam + "|" + stream;
      const head = this._h("LIVE · " + esc(String(st.attributes.friendly_name || cam).toUpperCase()), this._liveFollow === false ? "PINNED" : "FOLLOWING MOTION");
      const chips = `<div class="tchips">${cams.map((c) => `<a class="tchip${c === cam ? " on" : ""}" data-act="livecam" data-id="${esc(c)}">${esc(String((this._hass.states[c] || {}).attributes?.friendly_name || c).toUpperCase())}</a>`).join("")}<a class="tchip${this._liveFollow === false ? "" : " on"}" data-act="livefollow">FOLLOW MOTION</a>${((st.attributes.supported_features || 0) & 2) === 2 ? `<a class="tchip${(() => { try { return JSON.parse(localStorage.getItem("sentient-live-smooth") || "[]").includes(cam); } catch (e) { return false; } })() ? " on" : ""}" data-act="livesmooth" data-id="${esc(cam)}" title="Home Assistant's own video player: smoother, but some cameras show black">SMOOTH VIDEO</a>` : ""}</div>`;
      const badge = here.length ? `<span class="lbadge">● ${esc([...new Set(here)].join(" · "))} NOW</span>` : (latest ? `<span class="lbadge dim">LAST: ${esc(latest.label.toUpperCase())} · ${esc(new Date(latest.start * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}</span>` : "");
      // the plain picture stream works for every camera; Home Assistant's own player (smoother, WebRTC/HLS) is
      // opt-in per camera, because a stream it can't play (an H.265 camera, a restream it can't reach) shows black
      let smooth = false;
      try { smooth = JSON.parse(localStorage.getItem("sentient-live-smooth") || "[]").includes(cam); } catch (e) { /* storage off */ }
      const canSmooth = ((st.attributes.supported_features || 0) & 2) === 2;
      const mode = cam + "|" + (smooth && canSmooth ? "smooth" : "picture");
      if (this._liveCamKey !== mode || !el.querySelector(".lv")) {
        this._liveCamKey = mode;
        this._liveKey = key;
        this._liveCard = null;
        el.innerHTML = head + `<div class="lv"><div class="lvc"></div><div class="lvo">${badge}</div></div>` + chips;
        const holder = el.querySelector(".lvc");
        const picture = () => { holder.innerHTML = `<img alt="" src="${esc(stream)}">`; };
        if (smooth && canSmooth && window.loadCardHelpers) {
          window.loadCardHelpers().then((helpers) => {
            if (this._liveCamKey !== mode) return;
            const card = helpers.createCardElement({ type: "picture-entity", entity: cam, camera_view: "live", show_name: false, show_state: false });
            card.hass = this._hass;
            this._liveCard = card;
            holder.innerHTML = "";
            holder.appendChild(card);
          }, picture);
        } else picture();
      } else {
        if (this._liveCard) this._liveCard.hass = this._hass;
        const img = el.querySelector(".lvc img");
        if (img && this._liveKey !== key) { this._liveKey = key; img.src = stream; }
        el.querySelector(".h").outerHTML = head;
        el.querySelector(".lvo").innerHTML = badge;
        el.querySelector(".tchips").outerHTML = chips;
      }
    }

    _drawSecTimeline(mind) {
      const el = this._$("sectl");
      if (!el) return;
      const hours = this._tlHours || 3, now = Date.now(), from = now - hours * 3600e3;
      const incidents = (mind.security_incidents || []).filter((i) => new Date(i.end || i.start).getTime() >= from);
      const lanes = new Map(), dots = [], links = [];
      const name = (src) => { const st = this._hass.states[src]; return String(st ? st.attributes.friendly_name || src : src).replace(/ (person )?occupancy$/i, "").toUpperCase(); };
      for (const inc of incidents) {
        let prev = null;
        const ev = [...inc.evidence].sort((a, b) => a.time.localeCompare(b.time));
        for (const e of ev) {
          const t = new Date(e.time).getTime();
          if (t < from) continue;
          if (!lanes.has(e.source)) lanes.set(e.source, lanes.size);
          const label = (e.text.match(/reported (\w+)/i) || [])[1] || "motion";
          const d = { t, lane: lanes.get(e.source), label: label.toLowerCase(), id: e.event_id, inc: inc.id };
          dots.push(d);
          if (prev && prev.lane !== d.lane) links.push([prev, d]);
          prev = d;
        }
      }
      const rowH = 26, H = Math.max(1, lanes.size) * rowH + 22;
      const x = (t) => (((t - from) / (now - from)) * 98 + 1);  // percent across
      const y = (lane) => lane * rowH + 14;
      const col = { person: "#00fff7", car: "#ffbe55", bicycle: "#b48cff", motorcycle: "#ff8fa3", cat: "#8cff9e", dog: "#8cff9e", motion: "#7fa9b8" };
      let svg = `<svg viewBox="0 0 100 ${H}" preserveAspectRatio="none" class="tlsvg">`;
      for (const [, i] of lanes) svg += `<line x1="0" x2="100" y1="${y(i)}" y2="${y(i)}" class="tll" vector-effect="non-scaling-stroke"/>`;
      let ticks = "";
      for (let k = 1; k < hours; k++) { const tx = x(from + k * 3600e3); svg += `<line x1="${tx}" x2="${tx}" y1="0" y2="${H - 16}" class="tlg" vector-effect="non-scaling-stroke"/>`; ticks += `<span style="left:${tx}%">${new Date(from + k * 3600e3).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>`; }
      for (const [a, b] of links) svg += `<line x1="${x(a.t)}" y1="${y(a.lane)}" x2="${x(b.t)}" y2="${y(b.lane)}" class="tlk" vector-effect="non-scaling-stroke"/>`;
      svg += "</svg>";
      const dotsHtml = dots.map((d) => `<a class="tld" style="left:${x(d.t)}%;top:${y(d.lane)}px;background:${col[d.label] || col.motion}"${d.id ? ` data-act="secsnap" data-id="${esc(d.id)}"` : ""} title="${esc(d.label)} · ${esc(new Date(d.t).toLocaleTimeString())}"></a>`).join("");
      svg += dotsHtml + `<div class="tlticks">${ticks}</div>`;
      const labels = [...lanes.keys()].map((src) => `<div style="top:${y(lanes.get(src)) - 7}px">${esc(name(src))}</div>`).join("");
      const legend = Object.entries({ person: "PERSON", car: "CAR", bicycle: "BIKE", motorcycle: "MOTORBIKE", cat: "ANIMAL" }).map(([k, v]) => `<span><i style="background:${col[k]}"></i>${v}</span>`).join("");
      el.innerHTML = this._h("LINKED EVENTS", `${incidents.length} INCIDENT${incidents.length === 1 ? "" : "S"} · ${dots.length} SIGHTINGS`)
        + `<div class="tchips">${[1, 3, 6, 12].map((hh) => `<a class="tchip${hh === hours ? " on" : ""}" data-act="sectlh" data-id="${hh}">${hh}H</a>`).join("")}</div>`
        + (dots.length ? `<div class="tlw"><div class="tlnames">${labels}</div><div class="tlplot" style="height:${H}px">${svg}</div></div><div class="tlleg">${legend}<span><b>—</b> SAME INCIDENT, ANOTHER CAMERA</span></div>`
          : '<div class="e">NOTHING SEEN IN THIS TIME</div>')
        + (this._secSnap ? `<div class="snapbig"><img alt="" src="/api/frigate/notifications/${encodeURIComponent(this._secSnap)}/thumbnail.jpg"><a class="btn sm ghost" href="/api/frigate/notifications/${encodeURIComponent(this._secSnap)}/clip.mp4" target="_blank" rel="noopener">PLAY CLIP</a><a class="x" data-act="secsnap" data-id="">✕</a></div>` : "");
    }

    _drawSnaps(mind) {
      const el = this._$("snaps");
      if (!el) return;
      const evs = (mind.security_events || []).filter((e) => e.has_snapshot && !e.parked).slice(0, 8);
      el.hidden = !evs.length;
      if (!evs.length) return;
      el.innerHTML = this._h("LATEST SNAPSHOTS", `${evs.length}`) + `<div class="sgrid">${evs.map((e) => `<a class="sg" data-act="secsnap" data-id="${esc(e.id)}"><img loading="lazy" alt="" src="/api/frigate/notifications/${encodeURIComponent(e.id)}/thumbnail.jpg"><span>${esc(e.camera.replace(/_/g, " ").toUpperCase())} · ${esc(e.label.toUpperCase())} · ${esc(new Date(e.start * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}</span></a>`).join("")}</div>`;
    }

    _drawCameras() {
      const el = this._$("camc");
      if (!el) return;
      const c = this._cams;
      if (!c || !(c.cameras || []).length) { el.hidden = true; return; }
      el.hidden = false;
      const busy = c.cameras.reduce((n, x) => n + (x.today || 0), 0);
      el.innerHTML = this._h("CAMERAS", `${c.cameras.length} · ${busy} DETECTIONS TODAY`)
        + c.cameras.map((x) => {
          const live = this._hass.states[x.entity_id] || { state: x.state };
          const ok = !["unavailable", "unknown"].includes(live.state);
          return `<div class="r rtr${ok ? "" : " bad"}${this._liveCam === x.entity_id ? " on" : ""}" data-act="livecam" data-id="${esc(x.entity_id)}"><ha-icon icon="${esc(ok ? "mdi:cctv" : "mdi:video-off-outline")}"></ha-icon><b>${esc(x.name)}</b>`
            + `<u>${esc(ok ? String(live.state).toUpperCase() : "OFFLINE")}${x.today ? " · " + x.today + " TODAY" : ""}</u></div>`;
        }).join("")
        + (c.hidden ? `<div class="hint">${c.hidden} EXTRA VIEWS HIDDEN (SUB-STREAMS, SCREENSHOTS, MAPS)</div>` : "");
    }

    _drawSecurity() {
      for (const id of ["sec", "sec2"]) this._drawSecurityInto(this._$(id));
    }

    _drawSecurityInto(el) {
      if (!el) return;
      const { open, unlocked, alarms } = this._security();
      const bad = open.length + unlocked.length;
      const alarm = alarms[0];
      const armed = alarm && alarm.state.startsWith("armed");
      el.innerHTML = this._h("SECURITY", bad ? `${bad} OPEN` : "ALL SHUT")
        + open.map((o) => `<div class="r warn"><ha-icon icon="${esc(o.icon)}"></ha-icon><b>${esc(o.name)}</b><u>OPEN</u></div>`).join("")
        + unlocked.map((u) => `<div class="r bad"><ha-icon icon="mdi:lock-open-variant-outline"></ha-icon><b>${esc(u.name)}</b><u>UNLOCKED</u></div>`).join("")
        + (bad ? "" : '<div class="r"><ha-icon icon="mdi:shield-check-outline"></ha-icon><b>Doors and windows shut</b></div>')
        + (alarm ? `<div class="r${armed ? "" : " dim"}"><ha-icon icon="${esc(armed ? "mdi:shield-lock" : "mdi:shield-off-outline")}"></ha-icon><b>${esc(alarm.name)}</b><u>${esc(alarm.state.replace("armed_", "SET · ").replace("_", " ")).toUpperCase()}</u></div>` : "");
    }

    _drawMlCard() {
      const el = this._$("mlc"), ml = this._ml;
      if (!el || !ml || !ml.rules.length) { if (el) el.hidden = true; return; }
      el.hidden = false;
      const on = ml.rules.filter((r) => r.managing).length;
      el.innerHTML = this._h("MOTION LIGHTS", ml.dark ? (ml.late_night ? "LATE NIGHT · DIMMED" : ml.quiet ? "QUIET HOURS" : "DARK · ACTIVE") : "DAYLIGHT · RESTING")
        + ml.rules.map((r) => {
          const st = this._hass.states[r.light], lit = st && st.state === "on";
          return `<div class="r${r.enabled ? "" : " dim"}"><ha-icon icon="${esc(lit ? "mdi:lightbulb-on" : "mdi:lightbulb-outline")}"${lit ? ' class="lit"' : ""}></ha-icon><b>${esc(this._name(r.light))}</b>
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
      const pill = (on, text, cls) => `<span class="pill ${on ? cls || "on" : ""}">${esc(text)}</span>`;
      const rows = ml.rules.map((r) => {
        const st = this._hass.states[r.light];
        const lit = st && st.state === "on";
        const chips = [
          r.respect_quiet ? "OFF IN QUIET HOURS" : "IGNORES QUIET HOURS",
          r.only_dark ? (r.lux_sensor ? `BELOW ${r.lux_below} LX` : "SUNSET → SUNRISE") : "ANY TIME",
          `OFF AFTER ${r.off_after} MIN`,
          r.light.startsWith("light.") ? `${r.brightness}%${r.night_brightness ? ` · ${r.night_brightness}% LATE` : ""}` : "",
        ].filter(Boolean);
        return `<div class="card ml${r.enabled ? "" : " off"}"><div class="r"><ha-icon icon="${esc(lit ? "mdi:lightbulb-on" : "mdi:lightbulb-outline")}" class="${esc(lit ? "lit" : "")}"></ha-icon>
            <b>${esc(this._name(r.light))}</b><s>${r.managing ? (r.timer ? "ON · COUNTING DOWN" : "ON BY MOTION") : lit ? "ON" : "OFF"}</s>
            <label class="tg" title="${esc(r.enabled ? "On" : "Off")}"><input type="checkbox" data-mltoggle="${esc(r.id)}"${r.enabled ? " checked" : ""}><i></i></label>
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
      const items = (this._tlItems || []).filter((i) => (want === "all" ? i !== dig : want === "phones" ? i.phoned : i.kind === want));
      const phoned = (this._tlItems || []).filter((i) => i.phoned).length;
      const counts = {};
      (this._tlItems || []).forEach((i) => { counts[i.kind] = (counts[i.kind] || 0) + 1; });
      this._$("tlf").innerHTML = [["all", "ALL", (this._tlItems || []).length], ["phones", "SENT TO PHONES", phoned], ...Object.entries(KIND).map(([k, v]) => [k, v[1], counts[k] || 0])]
        .map(([k, label, n]) => `<button class="${esc(k === want ? "on" : "")}" data-act="tlkind" data-id="${esc(k)}">${esc(label)} <b>${esc(n)}</b></button>`).join("");
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
        const focus = i.time === (this._tlFocus || this._tlFocusDone);
        html += `<div class="tli k-${esc(i.kind)}${focus ? " focus" : ""}"${focus ? ' id="tlfocus"' : ""}><span class="tm">${hm(i.time)}</span><span class="dot"><ha-icon icon="${esc(icon)}"></ha-icon></span>
          <div class="tx"><b>${esc(i.text)}</b>${i.detail ? `<i>${esc(i.detail)}</i>` : ""}${i.why ? `<i>Why: ${esc(i.why)}</i>` : ""}${i.phoned ? `<i>Sent to phones: ${esc(i.phoned)}</i>` : ""}</div><s>${esc(label)}</s></div>`;
      }
      this._$("tllist").innerHTML = html || (want === "phones" ? '<div class="e">NOTHING SENT TO PHONES IN THE LAST 3 DAYS</div>' : '<div class="e">NOTHING YET · THE TIMELINE FILLS AS THE HOUSE SPEAKS, NOTICES AND ACTS</div>');
      const spot = this._$("tlfocus");
      if (spot && this._tlFocus && this._tlItems) {
        this._tlFocusDone = this._tlFocus;
        this._tlFocus = null;  // scroll there once; it stays highlighted
        spot.scrollIntoView({ block: "center" });
      }
    }

    _updateRow(info) {
      if (!info?.enabled || !this._hass.user?.is_admin) return "";
      const label = info.error ? "CHECK FAILED" : info.update_available ? "UPDATE AVAILABLE" : info.latest_version ? "NO NEWER RELEASE" : "NOT CHECKED YET";
      return `<div class="r stackr"><ha-icon icon="mdi:update"></ha-icon><b class="wrap">SENTIENT · ${esc(label)}<small>Loaded: ${esc(info.installed_version || "unknown")} · Latest: ${esc(info.latest_version || "unknown")}</small><small>${esc(info.error || info.note || "")}</small><small>${info.last_checked ? "Last check: " + esc(new Date(info.last_checked).toLocaleString()) : ""}</small><small role="status">${esc(this._updatesFeedback || "")}</small><a href="https://github.com/conahmorgan2304/sentient/releases" target="_blank" rel="noopener noreferrer">Release notes</a></b><button class="btn sm ghost" data-act="checkupdates" ${this._updatesSaving ? "disabled" : ""}>${this._updatesSaving ? "CHECKING…" : "CHECK NOW"}</button><button class="btn sm ghost" data-act="refreshpanel">REFRESH PANEL</button></div>`;
    }

    async _checkUpdates() {
      if (this._updatesSaving) return;
      this._updatesSaving = true;
      this._updatesFeedback = "Checking…";
      this._render();
      try {
        const result = await this._hass.callWS({ type: "sentient/update_check" });
        const hacs = !result.error && result.latest_version ? await this._refreshHacs() : false;
        this._updatesFeedback = result.error ? `Check failed: ${result.error}` : result.latest_version ? `Latest stable release: ${result.latest_version}. ${result.update_available ? (hacs ? "HACS has been told to look again; the update should show in HACS shortly." : "Update available — under Settings → Updates (Marketplace), or in HACS on older Home Assistant.") : "No newer release found."}` : "Checker disabled.";
      } catch (error) {
        this._updatesFeedback = error.message || String(error);
      } finally {
        this._updatesSaving = false;
        this._render();
      }
    }

    async _refreshHacs() {
      // best effort, HACS only (before Home Assistant 2026.11): it refreshed repositories now and then, so a new release
      // could take a while to show. The Marketplace has no such call, so this simply does nothing there.
      try {
        const repos = await this._hass.callWS({ type: "hacs/repositories/list", categories: ["integration"] });
        const repo = (repos || []).find((r) => String(r.full_name || "").toLowerCase() === "conahmorgan2304/sentient");
        if (!repo) return false;
        await this._hass.callWS({ type: "hacs/repository/refresh", repository: String(repo.id) });
        return true;
      } catch (_err) {
        return false;
      }
    }

    _voiceCheckRow(sensorValue) {
      const voiceCheck = this._voiceCheckState(sensorValue);
      return this._hass.user?.is_admin ? `<div class="r"><ha-icon icon="mdi:microphone-message"></ha-icon><b class="wrap">LIVE VOICE CHECK<small>${voiceCheck ? esc(voiceCheck.status.replaceAll("_", " ")) : "Start, then ask your voice device: How is the system? Say No thankyou afterwards."}</small>${voiceCheck ? `<small>${esc(voiceCheck.instructions)}</small><small>Replies heard: ${voiceCheck.heard_replies === null ? "not confirmed" : voiceCheck.heard_replies ? "confirmed by you" : "not heard"}</small><details><summary>Observed turns</summary>${voiceCheck.steps.map((s) => `<small>${esc(s.input_text)} → ${esc(s.reply)} · ${s.continue_listening ? "LISTEN" : "STOP"} · ${s.passed ? "EXPECTED" : "UNEXPECTED"}<br>Agent: ${esc(s.agent || "not supplied")} · Device: ${esc(s.satellite_id || s.device_id || "not supplied")}</small>`).join("")}<small>${esc(voiceCheck.limits)}</small></details>` : ""}</b></div><div class="row vc"><button class="btn sm ghost" ${this._voiceCheckSaving ? "disabled" : ""} data-act="voicecheck" data-id="start">DISCUSSION</button><button class="btn sm ghost" ${this._voiceCheckSaving ? "disabled" : ""} data-act="voicecheck" data-id="start_single">SINGLE REPLY</button><button class="btn sm ghost" ${this._voiceCheckSaving ? "disabled" : ""} data-act="voicecheck" data-id="start_question">QUESTION</button><button class="btn sm ghost" ${this._voiceCheckSaving ? "disabled" : ""} data-act="voicecheck" data-id="start_cancel">CANCEL</button>${voiceCheck ? `<button class="btn sm ghost" ${this._voiceCheckSaving ? "disabled" : ""} data-act="voicecheck" data-id="clear">CLEAR</button>` : ""}</div>${["passed_agent_checks", "failed_agent_checks"].includes(voiceCheck?.status) && voiceCheck.heard_replies === null ? `<div class="r"><b>Did you hear the replies?</b><button class="btn sm ghost" ${this._voiceCheckSaving ? "disabled" : ""} data-act="voicecheck" data-id="heard" data-run="${esc(voiceCheck.id)}">YES</button><button class="btn sm ghost" ${this._voiceCheckSaving ? "disabled" : ""} data-act="voicecheck" data-id="not_heard" data-run="${esc(voiceCheck.id)}">NO</button></div>` : ""}${this._voiceCheckSaving ? '<div class="r" role="status">Saving voice check…</div>' : ""}${this._voiceCheckError ? `<div class="r bad" role="alert">${esc(this._voiceCheckError)}</div>` : ""}${voiceCheck?.heard_replies != null ? `<div class="r" role="status"><b>${voiceCheck.heard_replies ? "SAVED · You confirmed you heard the replies." : "SAVED · You reported that replies were not heard."}</b><small>This records playback feedback; the LISTEN / STOP results remain above.</small></div>` : ""}` : "";
    }

    _voiceCheckState(sensorValue) {
      // Use the acknowledged response immediately, until HA supplies a newer sensor value.
      if (this._voiceCheckReply && this._voiceCheckReply.sensor === sensorValue) return this._voiceCheckReply.value;
      this._voiceCheckReply = null;
      return sensorValue;
    }

    async _voiceCheckAction(action, runId) {
      if (this._voiceCheckSaving) return;
      this._voiceCheckSaving = true;
      this._voiceCheckError = "";
      this._render();
      try {
        const result = await this._hass.callWS({ type: "sentient/voice_check", action, ...(runId ? { run_id: runId } : {}) });
        this._voiceCheckReply = { value: result, sensor: this._st("mind")?.attributes?.voice_check };
      } catch (error) {
        this._voiceCheckError = error.message || String(error);
      } finally {
        this._voiceCheckSaving = false;
        this._render();
      }
    }

    _securityEventRows(mind) {
      const events = (mind.security_events || []).slice(0, 8);
      const feedbackOn = !!mind.security_event_feedback_enabled && !!this._hass.user?.is_admin;
      this._securityFeedbackReplies ||= new Map();
      for (const key of this._securityFeedbackReplies.keys()) {
        if (!events.some(e => e.id === key) || !feedbackOn) this._securityFeedbackReplies.delete(key);
      }
      const summaries = (mind.security_event_feedback_summary || []).map(c => `<div class="r dim"><b class="wrap">${esc(c.camera)} · ${esc(c.reviewed)} EVENTS REVIEWED<small>Useful: ${esc(c.useful)} · Not useful: ${esc(c.not_useful)} · False positives: ${esc(c.false_positive)} · Cats: ${esc(c.cat)}</small>${c.suggestion ? `<small>${esc(c.suggestion)}</small>` : ""}<small>${esc(c.limits)}</small></b></div>`).join("");
      return events.map(e => {
        const ack = this._securityFeedbackReplies.get(e.id);
        const feedback = ack && ack.sensor === e.feedback ? ack.value : e.feedback;
        if (ack && ack.sensor !== e.feedback) this._securityFeedbackReplies.delete(e.id);
        const pending = this._securityFeedbackSaving === e.id;
        const error = feedbackOn && this._securityFeedbackError?.id === e.id ? this._securityFeedbackError.message : "";
        const action = (value, label) => `<button class="btn sm ghost" ${pending ? "disabled" : ""} data-act="securityfeedback" data-id="${esc(e.id)}" data-value="${value}">${label}</button>`;
        return `<div class="r"><ha-icon icon="mdi:cctv"></ha-icon><b class="wrap">${esc(e.camera)} · FRIGATE REPORTED ${esc(e.label)}<small>EVENT · ${esc(e.id)}</small>${e.analysis ? `<small>AI INFERENCE · ${esc(e.analysis.summary)}</small>` : ""}<details><summary>Evidence and alert reasons</summary>${(e.alerts || []).map(a => `<small>${esc(a.reason)} · ${esc(a.text)}<br>${esc(a.link)}</small>`).join("")}<small>${esc(e.limits)}</small></details>${feedback ? `<small role="status">Saved feedback: ${esc(feedback.value.replaceAll("_", " "))} · household correction</small>` : ""}${e.snapshot ? `<a class="btn sm ghost" href="/api/frigate/notifications/${encodeURIComponent(e.id)}/thumbnail.jpg" target="_blank" rel="noopener">SNAPSHOT</a>` : ""}${e.clip ? `<a class="btn sm ghost" href="/api/frigate/notifications/${encodeURIComponent(e.id)}/clip.mp4" target="_blank" rel="noopener">CLIP</a>` : ""}${feedbackOn ? `<div>${action("useful", "USEFUL")}${action("not_useful", "NOT USEFUL")}${action("false_positive", "FALSE POSITIVE")}${action("cat", "CAT")}${feedback ? action("clear", "CLEAR") : ""}</div>` : ""}${pending ? '<small role="status">Saving event feedback…</small>' : ""}${error ? `<small role="alert">${esc(error)}</small>` : ""}</b></div>`;
      }).join("") + summaries;
    }

    async _securityFeedback(id, value) {
      if (this._securityFeedbackSaving || !this._hass.user?.is_admin || !this._st("mind")?.attributes?.security_event_feedback_enabled) return;
      this._securityFeedbackSaving = id;
      this._securityFeedbackError = null;
      this._render();
      try {
        const result = await this._hass.callWS({ type: "sentient/security/event/feedback", event_id: id, value });
        const source = this._st("mind")?.attributes?.security_events?.find(e => e.id === id)?.feedback;
        this._securityFeedbackReplies ||= new Map();
        this._securityFeedbackReplies.set(id, { value: result.feedback, sensor: source });
      } catch (error) {
        this._securityFeedbackError = { id, message: error.message || String(error) };
      } finally {
        this._securityFeedbackSaving = null;
        this._render();
      }
    }

    // ---------- events ----------
    _click(ev) {
      const el = ev.target.closest("[data-act]");
      if (!el) return;
      const { act, id, text } = el.dataset;
      if (act === "checkupdates") this._checkUpdates();
      else if (act === "securityfeedback") this._securityFeedback(id, el.dataset.value);
      else if (act === "refreshpanel") window.location.reload();
      else if (act === "voicecheck") {
        this._voiceCheckAction(id, el.dataset.run);
      }
      else if (act === "forget") this._call("forget", { what: id }, `Forget: ${text}?`);
      else if (act === "cancel") this._call("cancel_watch", { what: id }, `Stop: ${text}?`);
      else if (act === "wonderask") this._call("wonder", { ask_now: true });
      else if (act === "lasthour") this._lastHour();
      else if (act === "openmind") this._show("mind", true);
      else if (act === "mckeep") { const keep = el.dataset.keep || null; if (keep || window.confirm("Keep both as they are?")) this._hass.callWS({ type: "sentient/memory/resolve", pair: id, keep }).then(() => { this._innerAt = 0; this._loadInner(); }); }
      else if (act === "unwatch") this._hass.callWS({ type: "sentient/notice/unwatch", entity_id: id }).then(() => { this._innerAt = 0; this._loadInner(); });
      else if (act === "innerno") this._hass.callWS({ type: "sentient/inner/dismiss", key: id }).then(() => { this._innerAt = 0; this._loadInner(); });
      else if (act === "livecam") { this._liveCam = id; this._liveFollow = false; this._render(); }
      else if (act === "livesmooth") {
        try {
          const list = JSON.parse(localStorage.getItem("sentient-live-smooth") || "[]");
          localStorage.setItem("sentient-live-smooth", JSON.stringify(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]));
        } catch (e) { /* storage off: stays on the picture stream */ }
        this._render();
      }
      else if (act === "livefollow") { this._liveFollow = this._liveFollow === false ? true : false; this._render(); }
      else if (act === "sectlh") { this._tlHours = +id; this._render(); }
      else if (act === "secsnap") { this._secSnap = id || null; this._render(); }
      else if (act === "prognew") this._editProg(null);
      else if (act === "progsug") { const p = ((this._heat || {}).suggested || [])[+id]; if (p) this._hass.callWS({ type: "sentient/heating/programme/save", programme: { ...p, name: p.name.replace(" (suggested)", "") } }).then(() => { this._heatAt = 0; this._loadHeat(); }); }
      else if (act === "progsugedit") { const p = ((this._heat || {}).suggested || [])[+id]; if (p) this._editProg({ ...p, name: p.name.replace(" (suggested)", ""), id: undefined }); }
      else if (act === "progedit") this._editProg(((this._heat || {}).programmes || []).find((p) => p.id === id));
      else if (act === "progcancel") { this._heatEdit = false; this._drawHeat(); }
      else if (act === "progsave") this._saveProg(id);
      else if (act === "progaddper") { const ps = this._progDraft.periods, last = ps[ps.length - 1]; ps.push({ on: last ? last.off : "06:00", off: last ? (String((+last.off.slice(0, 2) + 2) % 24).padStart(2, "0") + last.off.slice(2)) : "09:00", temp: last ? last.temp : 21 }); this._progRedraw(); }
      else if (act === "progrmper") { this._progDraft.periods.splice(+id, 1); this._progRedraw(); }
      else if (act === "progdays") { const D = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]; this._progDraft.days = id === "wk" ? D.slice(0, 5) : id === "we" ? ["sat", "sun"] : D; this._progRedraw(); }
      else if (act === "progdel") { if (window.confirm("Delete this heating schedule?")) this._hass.callWS({ type: "sentient/heating/programme/delete", programme_id: id }).then(() => { this._heatAt = 0; this._loadHeat(); }); }
      else if (act === "progonoff") { const p = ((this._heat || {}).programmes || []).find((x) => x.id === id); if (p) this._hass.callWS({ type: "sentient/heating/programme/save", programme_id: id, programme: { ...p, enabled: !p.enabled } }).then(() => { this._heatAt = 0; this._loadHeat(); }); }
      else if (act === "heatzones") this._openHeatZones(false);
      else if (act === "roomlights") this._hass.callService("light", "turn_off", {}, { area_id: id }).then(() => { this._glAt = 0; setTimeout(() => this._loadGlance(), 800); }, (e) => window.alert(e.message || e));
      else if (act === "heatzsave") this._saveHeatZones();
      else if (act === "heatdel") { if (window.confirm("Delete this heating time?")) this._hass.callWS({ type: "sentient/items/delete", kind: "heating", item_id: id }).then(() => { this._heatAt = 0; this._loadHeat(); }); }
      else if (act === "heatonoff") {
        const x = ((this._heat || {}).schedules || []).find((i) => i.id === id);
        if (x) this._hass.callWS({ type: "sentient/heating/save", item_id: id, entity_id: x.entity_id, time: x.time, days: x.days, temperature: x.temperature, enabled: !x.enabled }).then(() => { this._heatAt = 0; this._loadHeat(); });
      }
      else if (act === "bksave") this._hass.callWS({ type: "sentient/backups/save" }).then((r) => { this._$("bkmsg").textContent = `Saved ${r.name}.`; this._loadBackups(); }, (e) => { this._$("bkmsg").textContent = e.message || String(e); });
      else if (act === "bkrestore") {
        if (!window.confirm(`Restore ${id}? It adds to what's here and doesn't delete anything.`)) return;
        this._hass.callWS({ type: "sentient/backups/restore", name: id, settings: this._$("bkset").checked }).then((res) => {
          this._$("bkmsg").textContent = "Restored: " + Object.entries(res).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(", ") + ".";
          this._loadMemories(); this._loadSpots(); this._loadMotion(); this._loadShortcuts();
        }, (e) => { this._$("bkmsg").textContent = e.message || String(e); });
      }
      else if (act === "newseen") {
        this._new = [];
        this._render();
        this._hass.callWS({ type: "sentient/whats_new/seen" }).catch(() => {});
      }
      else if (act === "propyes" || act === "propno") {
        (this._propDone = this._propDone || new Set()).add(id);
        this._render();
        this._hass.callWS({ type: "sentient/proposal", proposal: id, accept: act === "propyes" }).catch(() => {});
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
      else if (act === "unadopt") this._call("adopt_routine", { routine: id, adopt: false }, `Stop doing this for you?\n\n${text}`).then(() => this._loadItems("schedules"));
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
      else if (act === "wizdoctor") this._wizFinish().then(() => { this._doc = null; this._group = "doctor"; this._show("settings", true); });
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
      else if (act === "bkread") {
        this._hass.callWS({ type: "sentient/export/readable" }).then((r) => {
          const a = document.createElement("a");
          a.href = URL.createObjectURL(new Blob([r.text], { type: "text/markdown" }));
          a.download = `sentient-readable-${new Date().toISOString().slice(0, 10)}.md`;
          document.body.appendChild(a); a.click(); a.remove();
          setTimeout(() => URL.revokeObjectURL(a.href), 2000);
        }, (e) => { this._$("bkmsg").textContent = e.message || String(e); });
      }
      else if (act === "unmute") this._loadMutes(false, { remove: id });
      else if (act === "unmuteall") this._loadMutes(false, { clear: true });
      else if (act === "trytts") {
        const d = this._draft || {};
        this._status("PLAYING…", "warn");
        this._hass.callWS({ type: "sentient/reading/try", engine: d.reading_tts || null, voice: d.reading_voice || null }).then(
          (r) => this._status(r.ok ? `PLAYING ON ${String(r.speaker_name || "").toUpperCase()} ✓` : String(r.why || "COULDN'T PLAY IT").toUpperCase(), r.ok ? "ok" : "bad"),
          (e) => this._status(e.message || String(e), "bad"));
      }
      else if (act === "pin") {
        const m = (this._memories || []).find((x) => x.id === id);
        if (m) this._hass.callWS({ type: "sentient/memory/update", memory_id: m.id, text: m.text, pinned: !m.pinned })
          .then(() => { m.pinned = !m.pinned; this._drawMemories(); }, (e) => window.alert(e.message || e));
      }
      else if (act === "docrun") this._drawDoctor(true);
      else if (act === "privrun") this._drawPrivacy(true);
      else if (act === "userun") this._drawUsage(true);
      else if (act === "showall" || act === "showless") { this._showAll = act === "showall"; this._drawSide(); }
      else if (act === "privforget") {
        if (!window.confirm("Forget everything Sentient kept from the last 48 hours — the timeline, conversations, visitors, clip descriptions, faces seen, new memories (not pinned ones) and this record? It can't recall what was already sent to an AI service.")) return;
        this._hass.callWS({ type: "sentient/privacy/forget", hours: 48 }).then((r) => {
          const total = Object.values(r.forgot).reduce((a, b) => a + b, 0);
          this._status(`FORGOT ${total} THING${total === 1 ? "" : "S"} ✓`, "ok");
          this._drawPrivacy(true);
        }, (e) => this._status(e.message || String(e), "bad"));
      }
      else if (act === "docspeak") {
        if (!window.confirm("Say a short test line on every speaker, one after another?")) return;
        this._status("TESTING…", "warn");
        this._hass.callWS({ type: "sentient/doctor/speakers" }).then((r) => {
          const bad = r.results.filter((x) => !x.ok).map((x) => x.speaker);
          this._status(!r.results.length ? "NO SPEAKERS CONFIGURED" : bad.length ? `DIDN'T ACCEPT IT: ${bad.join(", ").toUpperCase()}` : `ALL ${r.results.length} SPEAKERS ACCEPTED IT ✓`, !r.results.length || bad.length ? "bad" : "ok");
        }, (e) => this._status(e.message || String(e), "bad"));
      }
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

    // the Mind page's top strip: how much it's thought today, the last week, and how it talks
    _drawBrain(mind, ai) {
      const R = (id) => this._$(id);
      const tk = mind.talking || {};
      const days = Object.entries(ai.last_7_days || {});
      if (ai.today != null) days.push(["today", ai.today]);
      const most = Math.max(1, ...days.map(([, n]) => n));
      const wd = (d) => d === "today" ? "TODAY" : new Date(d + "T12:00:00").toLocaleDateString(undefined, { weekday: "short" }).toUpperCase();
      const bars = days.slice(-8).map(([d, n]) => `<div class="wbar${d === "today" ? " now" : ""}" title="${esc(String(n))} AI calls"><b>${esc(String(n))}</b><i style="height:${Math.max(3, Math.round((n / most) * 64))}px"></i><span>${wd(d)}</span></div>`).join("");
      const tile = (n, l, cls) => `<div class="s${cls ? " " + cls : ""}"><div class="n">${esc(String(n))}</div><div class="l">${l}</div></div>`;
      const chip = (onNow, text) => `<span class="tchip${onNow ? " on" : ""}">${text}</span>`;
      const later = mind.ask_later || [];
      const used = ai.limit ? Math.min(100, Math.round(((ai.today || 0) / ai.limit) * 100)) : 0;
      R("mbrain").innerHTML = `<div class="brh"><div class="brl">${this._h("MIND", ai.failing ? "AI NOT ANSWERING" : ai.capped ? "AI LIMIT REACHED" : "THINKING NORMALLY")}
          <div class="st">${tile(ai.today ?? 0, ai.limit ? `AI TASKS OF ${ai.limit}` : "AI TASKS")}${tile(ai.failed_today || 0, "FAILED TODAY", ai.failed_today ? "warn" : "")}${tile(this._st("mind") ? this._st("mind").state : 0, "TALKS TODAY")}${tile(this._st("memories") ? this._st("memories").state : 0, "MEMORIES")}${tile((mind.follow_ups || []).length + later.length, "TO COME BACK TO")}</div>
          ${ai.limit ? `<div class="use"><i style="width:${used}%"></i></div>` : ""}
          <div class="tchips">${chip(tk.style === "natural", tk.style === "natural" ? "NATURAL TALK" : "BRIEF TALK")}${chip(tk.carry_on_minutes > 0, tk.carry_on_minutes > 0 ? `CARRIES ON ${esc(String(tk.carry_on_minutes))} MIN` : "NEW CHAT EACH TIME")}${chip(tk.focused_tools, "FOCUSED TOOLS")}${chip(tk.ask_later, "ASKS HOW THINGS WENT")}</div></div>
        <div class="week"><div class="wt">AI TASKS · LAST WEEK${ai.average_per_day != null ? ` · USUALLY ${esc(String(ai.average_per_day))} A DAY` : ""}</div><div class="wbars">${bars || '<div class="e">NO HISTORY YET</div>'}</div></div></div>`;
      // about itself: what works right now, and what its own records show
      const me = mind.self;
      R("mself").hidden = !me;
      if (me) {
        const ic = { ok: "mdi:check-circle-outline", limited: "mdi:alert-circle-outline", off: "mdi:minus-circle-outline" };
        const row = (c) => `<div class="r${c.status === "ok" ? " dim" : c.status === "limited" ? " warn" : ""}"><ha-icon icon="${esc(ic[c.status] || ic.off)}"></ha-icon><b class="wrap">${esc(c.name)}${c.why ? `<small>${esc(c.why)}</small>` : ""}</b><u>${esc(c.status.toUpperCase())}</u></div>`;
        const notOk = (me.status || []).filter((c) => c.status !== "ok").map(row).join("");
        const statusRows = (me.status || []).filter((c) => c.status === "ok").map(row).join("");
        const found = (me.findings || []).map((f) => `<div class="r"><ha-icon icon="mdi:head-lightbulb-outline"></ha-icon><b class="wrap">${esc(f.text)}</b><s>SELF-CHECK</s></div>`).join("");
        const limited = (me.status || []).filter((c) => c.status !== "ok").length;
        const gone = this._propDone || new Set();
        const props = (mind.proposals || []).filter((p) => !gone.has(p.id)).map((p) => `<div class="r prop"><ha-icon icon="mdi:tune-variant"></ha-icon>
          <b class="wrap">I'd change: ${esc(p.change.charAt(0).toLowerCase() + p.change.slice(1))}<small>Because ${esc(p.because)}</small></b>
          <button class="btn sm" data-act="propyes" data-id="${esc(p.id)}">YES</button><button class="btn sm ghost" data-act="propno" data-id="${esc(p.id)}">NO</button></div>`).join("");
        R("mself").innerHTML = this._h("ABOUT ITSELF", limited ? `${limited} LIMITED` : "ALL WORKING")
          + notOk + props + found + (me.enabled === false ? "" : (found ? "" : '<div class="hint">NOTHING STANDS OUT IN ITS OWN RECORDS THIS WEEK</div>'))
          + (me.adjusted || []).map((a) => `<div class="hint">LAST NIGHT · ${esc(a).toUpperCase()}</div>`).join("")
          + `${statusRows ? `<details class="caps"><summary>${notOk ? "Everything else is working" : "What it can do right now · all working"}</summary>${statusRows}</details>` : ""}`;
      }
      const goals = mind.goals || [];
      R("mgoals").hidden = !goals.length;
      R("mgoals").innerHTML = goals.length ? this._h("GOALS", `${goals.length}`) + goals.map((g) => `<div class="r"><ha-icon icon="mdi:flag-checkered"></ha-icon><b class="wrap">${esc(g.title)}<small>${g.progress ? esc(g.progress) : g.follows ? `following ${esc(g.follows)} · first check tonight` : "no sensor to follow"}</small></b><u>UNTIL ${esc(day(g.ends)).toUpperCase()}</u></div>`).join("") : "";
      R("mlater").hidden = !later.length;
      R("mlater").innerHTML = later.length ? this._h("WILL ASK HOW IT WENT", `${later.length}`) + later.map((t) => `<div class="r"><ha-icon icon="mdi:comment-question-outline"></ha-icon><b class="wrap">${esc(t.question)}<small>${esc(t.about)}</small></b><u>${new Date(t.due) > new Date() ? "FROM " + (Date.now() - new Date(t.due) > -864e5 ? hm(t.due) : day(t.due)) : "NEXT CHAT"}</u></div>`).join("") : "";
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
.page{max-width:2200px;margin:0 auto;padding:18px}
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
/* Overview and Mind: cards flow into balanced columns (no gaps under short columns), and the page itself
   scrolls when it must, without a visible scroll bar. Settings and the timeline keep theirs. */
/* Overview and Mind: fixed lanes, each with one job, the same height, the last card stretching to the bottom */
.lanes{display:grid;gap:16px;margin-top:16px;align-items:stretch}
.warnbar{display:flex;align-items:center;gap:12px;margin-top:14px;padding:11px 16px;border-radius:16px;border:1px solid rgba(255,190,85,.45);background:linear-gradient(90deg,rgba(255,180,60,.12),rgba(255,180,60,.03))}
.warnbar ha-icon{--mdc-icon-size:22px;color:#ffbe55;flex:none} .warnbar b{font-weight:500;font-size:13.5px;color:#ffe2b0}
.warnbar small{display:block;margin-top:2px;font-size:11px;color:rgba(255,226,176,.7)}
#mp>.warnbar{margin:0 0 14px}
.ov3{grid-template-columns:repeat(3,minmax(0,1fr))}
.mp2{grid-template-columns:minmax(0,1.15fr) minmax(0,1fr)}
.lane{display:flex;flex-direction:column;gap:14px;min-width:0}
.lane>.card{flex:none;display:flex;flex-direction:column}
.lane>.card>.hint.meter{margin-top:auto}
.lane>.card>.e:last-child{margin:auto 0}
.lt{display:flex;align-items:center;gap:10px;margin:2px 4px -4px;font-size:9.5px;font-weight:700;letter-spacing:.3em;color:rgba(127,239,255,.55)}
.lt::after{content:"";flex:1;height:1px;background:linear-gradient(90deg,rgba(0,255,247,.22),transparent)}
.caps{margin-top:6px} .caps>summary{cursor:pointer;font-size:10px;font-weight:700;letter-spacing:.16em;color:#7fefff;padding:6px 2px;list-style:none}
.caps>summary::-webkit-details-marker{display:none} .caps>summary::before{content:"▸ "} .caps[open]>summary::before{content:"▾ "}
.rtr{cursor:pointer} .rtr.on{border-color:rgba(0,255,247,.45);background:rgba(0,255,247,.09)} .rtr.off{opacity:.55}
.rtr s{text-decoration:none;font-size:8.5px;font-weight:700;letter-spacing:.14em;color:#7fefff;white-space:nowrap}
.brain{margin-top:0}
.brh{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(260px,1fr);gap:22px;align-items:stretch}
.brh .st{margin-top:4px}
.s.warn .n{color:#ffbe55;text-shadow:0 0 8px rgba(255,190,85,.5)}
.use{height:4px;margin-top:10px;border-radius:2px;background:rgba(0,255,247,.1);overflow:hidden}
.use i{display:block;height:100%;background:linear-gradient(90deg,#00a8ff,#00fff7);box-shadow:0 0 6px rgba(0,255,247,.6)}
.tchips{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px;justify-content:flex-start}
.tchip{font-size:8.5px;font-weight:700;letter-spacing:.14em;padding:4px 9px;border-radius:10px;color:rgba(150,220,235,.45);border:1px solid rgba(150,220,235,.18)}
.tchip.on{color:#7fefff;border-color:rgba(0,255,247,.45);background:rgba(0,255,247,.08)}
.week{display:flex;flex-direction:column;padding-left:22px;border-left:1px solid rgba(0,255,247,.12)}
.wt{font-size:9px;font-weight:700;letter-spacing:.18em;color:rgba(150,220,235,.65);margin-bottom:10px}
.wbars{flex:1;display:flex;align-items:flex-end;gap:8px;min-height:104px}
.wbar{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:5px;min-width:0}
.wbar i{display:block;width:100%;max-width:26px;border-radius:5px 5px 2px 2px;background:linear-gradient(180deg,rgba(0,255,247,.55),rgba(0,168,255,.25));flex:none}
.wbar.now i{background:linear-gradient(180deg,#00fff7,#00a8ff);box-shadow:0 0 10px rgba(0,255,247,.55)}
.wbar b{font-size:10px;font-weight:600;color:#bff8ff}
.wbar span{font-size:8px;font-weight:700;letter-spacing:.1em;color:rgba(150,220,235,.55)} .wbar.now span{color:#00fff7}
@media (max-width: 1100px){
  .ov3{grid-template-columns:repeat(2,minmax(0,1fr))}
  .ov3>.lane:nth-child(3){grid-column:1/-1;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-items:stretch}
  .ov3>.lane:nth-child(3)>.lt{grid-column:1/-1}
  .brh{grid-template-columns:1fr} .week{padding:14px 0 0;border-left:none;border-top:1px solid rgba(0,255,247,.12)}
}
@media (max-width: 720px){
  .lanes.ov3,.lanes.mp2{grid-template-columns:1fr}
  .ov3>.lane:nth-child(3){display:flex;align-items:stretch}
  .r.stackr{flex-wrap:wrap} .r.stackr b{flex:1 1 calc(100% - 30px)}
}
.flow{columns:3;column-gap:16px;margin-top:16px}
.flow>*{break-inside:avoid;margin:0 0 14px;width:100%}
.flow>.stack{break-inside:avoid;display:flex;gap:14px}
.flow>[hidden],.flow>.stack:empty{display:none}
:host([view="overview"]),:host([view="mind"]),:host([view="security"]),:host([view="house"]){scrollbar-width:none}
:host([view="overview"])::-webkit-scrollbar,:host([view="mind"])::-webkit-scrollbar,:host([view="security"])::-webkit-scrollbar,:host([view="house"])::-webkit-scrollbar{display:none}
#mp .flow{columns:2}
#scp .lanes,#hp .lanes{align-items:start}
.lv{position:relative;border-radius:12px;overflow:hidden;background:#000;aspect-ratio:16/9;border:1px solid rgba(0,255,247,.25)}
.lv img{display:block;width:100%;height:100%;object-fit:contain} .lvc{width:100%;height:100%} .lvc>*{display:block;width:100%;height:100%;--ha-card-border-radius:0;--ha-card-border-width:0}
.lvo{position:absolute;left:8px;top:8px;right:8px;display:flex;gap:6px}
.lbadge{font-size:9.5px;font-weight:700;letter-spacing:.14em;padding:4px 8px;border-radius:8px;color:#041014;background:#00fff7;box-shadow:0 0 10px rgba(0,255,247,.6)} .lbadge.dim{color:#cfeff5;background:rgba(0,0,0,.55);box-shadow:none}
.tchip[data-act]{cursor:pointer}
.tlw{display:flex;gap:8px;margin-top:10px} .tlnames{position:relative;flex:none;width:92px} .tlnames div{position:absolute;left:0;right:0;font-size:8.5px;font-weight:700;letter-spacing:.08em;color:rgba(150,220,235,.75);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tlplot{flex:1;min-width:0} .tlsvg{width:100%;height:100%;overflow:visible}
.tll{stroke:rgba(0,255,247,.12);stroke-width:1} .tlg{stroke:rgba(0,255,247,.08);stroke-dasharray:3 4} 
.tlk{stroke:rgba(0,255,247,.55);stroke-width:1.5;fill:none} .tlplot{position:relative} .tlsvg{position:absolute;inset:0}
.tld{position:absolute;width:10px;height:10px;margin:-5px 0 0 -5px;border-radius:50%;border:1.5px solid #041014;cursor:pointer;box-shadow:0 0 6px rgba(0,255,247,.4)} .tld:hover{border-color:#fff}
.tlticks{position:absolute;left:0;right:0;bottom:0;height:14px} .tlticks span{position:absolute;transform:translateX(-50%);font-size:8.5px;color:rgba(150,220,235,.5)}
.tlleg{display:flex;flex-wrap:wrap;gap:10px;margin-top:8px;font-size:8.5px;font-weight:700;letter-spacing:.1em;color:rgba(150,220,235,.6)} .tlleg i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px;vertical-align:-1px}
.snapbig{position:relative;margin-top:10px;border-radius:12px;overflow:hidden;border:1px solid rgba(0,255,247,.3)} .snapbig img{display:block;width:100%} .snapbig .btn{position:absolute;left:8px;bottom:8px} .snapbig .x{position:absolute;right:8px;top:6px}
.sgrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.sg{display:block;position:relative;border-radius:10px;overflow:hidden;border:1px solid rgba(0,255,247,.2);cursor:pointer;background:#000;aspect-ratio:16/10}
.sg img{display:block;width:100%;height:100%;object-fit:cover} .sg span{position:absolute;left:0;right:0;bottom:0;padding:3px 6px;font-size:8.5px;font-weight:700;letter-spacing:.08em;color:#dff6fa;background:linear-gradient(transparent,rgba(0,0,0,.8))}
.r b .btn{margin:6px 6px 0 0;display:inline-flex} .r b>div{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px} .r b>div .btn{margin:0}
.ttiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px}
.tt{display:flex;flex-direction:column;gap:2px;padding:8px 10px;border-radius:12px;border:1px solid rgba(0,255,247,.18);background:rgba(0,255,247,.05)} .tt ha-icon{--mdc-icon-size:20px;color:#00fff7} .tt b{font-size:20px;font-weight:300;color:#fff} .tt span{font-size:8.5px;font-weight:700;letter-spacing:.12em;color:rgba(150,220,235,.7)}
.tt.warm{border-color:rgba(255,170,80,.5)} .tt.warm ha-icon{color:#ffbe55}
.ppl{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px} .pp{font-size:9px;font-weight:700;letter-spacing:.12em;padding:4px 9px;border-radius:10px;color:rgba(150,220,235,.55);border:1px solid rgba(150,220,235,.18)} .pp.home{color:#7fefff;border-color:rgba(0,255,247,.45);background:rgba(0,255,247,.08)}
.mc{padding:8px 10px;margin-bottom:8px;border-radius:10px;border:1px solid rgba(255,190,85,.3);background:rgba(255,190,85,.04)} .mck{font-size:9px;font-weight:700;letter-spacing:.16em;color:#ffbe55;margin-bottom:6px}
.mct{padding:4px 0} .mct b{display:block;font-weight:400;font-size:12.5px} .mct small{font-size:8.5px;font-weight:700;letter-spacing:.12em;color:rgba(150,220,235,.6)}
.sub{font-size:9px;font-weight:700;letter-spacing:.2em;color:rgba(150,220,235,.7);margin:12px 2px 4px}
.hform{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:10px}
.hform select,.hform input[type=time],.hform input[type=number]{background:rgba(0,0,0,.25);border:1px solid rgba(0,255,247,.3);border-radius:8px;padding:6px 8px;color:#cfeff5;color-scheme:dark;font:inherit;font-size:12px}
.hform input[type=number]{width:70px}.offl{font-size:10px;letter-spacing:.12em;color:rgba(150,220,235,.8)}
.dchips{display:flex;gap:4px;flex-wrap:wrap;flex-basis:100%}.dchips label{cursor:pointer}.dchips input{position:absolute;opacity:0;pointer-events:none}
.dchips span{display:inline-block;font-size:9px;font-weight:700;letter-spacing:.1em;padding:4px 7px;border-radius:8px;border:1px solid rgba(0,255,247,.25);color:rgba(150,220,235,.6)}
.dchips input:checked+span{color:#041014;background:#7fefff;border-color:#7fefff}
.prog{padding:8px 10px;margin-bottom:6px;border-radius:10px;border:1px solid rgba(0,255,247,.12);background:rgba(0,255,247,.035)} .prog.dim{opacity:.55} .prog.sug{border-style:dashed;border-color:rgba(255,190,85,.4)}
.prog .ph{display:flex;align-items:center;gap:8px} .prog .ph b{font-weight:500;font-size:12.5px} .prog .ph u{flex:1;text-decoration:none;font-size:9px;font-weight:700;letter-spacing:.08em;color:rgba(150,220,235,.7)}
.pbar{position:relative;height:14px;margin:8px 0 4px;border-radius:4px;background:rgba(0,255,247,.06);overflow:hidden}
.pbar i{position:absolute;top:0;bottom:0;background:linear-gradient(90deg,#ff9e57,#ffbe55);box-shadow:0 0 6px rgba(255,170,80,.6)}
.pbar s{position:absolute;z-index:1;top:1px;transform:translateX(-50%);text-decoration:none;font-size:8px;color:rgba(200,235,245,.55)}
.pchips{display:flex;flex-wrap:wrap;gap:4px} .pchips span{font-size:9px;font-weight:700;letter-spacing:.06em;padding:3px 7px;border-radius:8px;color:#ffd9a8;border:1px solid rgba(255,190,85,.35)}
.pper{display:flex;align-items:center;gap:6px;flex-basis:100%} .pper span{font-size:9px;font-weight:700;letter-spacing:.12em;color:rgba(150,220,235,.7)}
.hform input[type=text]{background:rgba(0,0,0,.25);border:1px solid rgba(0,255,247,.3);border-radius:8px;padding:6px 8px;color:#cfeff5;font:inherit;font-size:12px}
.r b small{display:block;margin-top:3px;font-size:11px;line-height:1.35;color:rgba(150,220,235,.62);white-space:normal}
.r b details{display:block;margin-top:3px} .r b summary{cursor:pointer;font-size:11px;color:#7fefff;list-style:none}
.r b summary::-webkit-details-marker{display:none} .r b summary::before{content:"▸ "} .r b details[open] summary::before{content:"▾ "}
@media (min-width: 2200px){ .flow{columns:4} #mp .flow{columns:3} }
@media (max-width: 1100px){ .flow{columns:2} }
@media (max-width: 720px){ .flow{columns:1} }
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
.rgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:7px}
.rt{display:flex;flex-direction:column;gap:3px;min-width:0;padding:7px 9px;border-radius:12px;border:1px solid rgba(0,255,247,.14);background:rgba(0,255,247,.035)}
.rt.here{border-color:rgba(0,255,247,.6);box-shadow:inset 0 0 14px rgba(0,255,247,.14)} .rt.lit{background:linear-gradient(160deg,rgba(255,200,90,.09),rgba(0,255,247,.03))}
.rn{display:flex;align-items:center;gap:6px;min-width:0} .rn ha-icon{--mdc-icon-size:15px;color:#00fff7;flex:none} .rn b{font-size:10px;font-weight:700;letter-spacing:.12em;color:#e6f5fa;text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis} .rn s{margin-left:auto;text-decoration:none;font-size:7.5px;font-weight:700;letter-spacing:.14em;color:#00fff7}
.rv{display:flex;align-items:baseline;gap:8px} .rtemp{font-size:19px;font-weight:300;color:#fff;line-height:1.1} .rh{font-size:10px;letter-spacing:.06em;color:rgba(150,220,235,.7)} .rv .fl{--mdc-icon-size:15px;color:#ffa94d;align-self:center}
.rs{display:flex;flex-wrap:wrap;gap:3px} .rc{display:inline-flex;align-items:center;gap:3px;font:inherit;font-size:7.5px;font-weight:700;letter-spacing:.08em;padding:2px 6px;border-radius:9px;border:1px solid rgba(150,220,235,.16);color:rgba(150,220,235,.55);background:none;max-width:100%;overflow:hidden;white-space:nowrap;text-overflow:ellipsis} .rc ha-icon{--mdc-icon-size:12px;flex:none}
.rc.on{color:#ffd27f;border-color:rgba(255,200,90,.45);background:rgba(255,200,90,.08);cursor:pointer} .rc.on:hover{background:rgba(255,200,90,.18)} .rc.live{color:#7fefff;border-color:rgba(0,255,247,.4)} .rc.warn{color:#ffbe55;border-color:rgba(255,190,85,.5)}
.r.warn{border-color:rgba(255,190,85,.4)} .r.warn ha-icon{color:#ffbe55}
#hgraph{margin:2px 0 4px} .hg{position:relative;height:86px;margin-bottom:6px;border-radius:10px;background:rgba(0,0,0,.2);border:1px solid rgba(0,255,247,.1);overflow:hidden}
.hg svg{position:absolute;inset:0;width:100%;height:100%} .hg .heat rect{fill:rgba(255,169,77,.16)} .hg .set{fill:none;stroke:#ffa94d;stroke-width:1.5;stroke-dasharray:5 4;vector-effect:non-scaling-stroke} .hg .now{fill:none;stroke:#00fff7;stroke-width:2;vector-effect:non-scaling-stroke;filter:drop-shadow(0 0 3px rgba(0,255,247,.6))}
.hgl{position:absolute;font-size:8px;font-weight:700;letter-spacing:.1em;color:rgba(150,220,235,.6);pointer-events:none} .hgl.tk{bottom:3px;transform:translateX(-50%)} .hgl.nm{left:8px;top:4px;color:#7fefff}
.hgk{display:flex;align-items:center;gap:6px;font-size:7.5px;font-weight:700;letter-spacing:.12em;color:rgba(150,220,235,.55);margin:0 2px 4px} .hgk .k{display:inline-block;width:14px;height:3px;border-radius:2px;margin-left:6px} .hgk .k.now{background:#00fff7} .hgk .k.set{background:#ffa94d} .hgk .k.heat{height:8px;background:rgba(255,169,77,.3)}
.row.vc{margin:-2px 0 8px 30px}
.dchart{display:grid;grid-template-columns:repeat(24,1fr);gap:3px;align-items:end;height:84px;padding:4px 2px 0;border-bottom:1px solid rgba(0,255,247,.18)}
.dc{display:flex;flex-direction:column-reverse;height:100%;border-radius:3px 3px 0 0;background:rgba(0,255,247,.03);overflow:hidden} .dc.later{background:transparent;opacity:.35} .dc.nowc{box-shadow:0 0 0 1px rgba(0,255,247,.5)}
.dc i{display:block;flex:none;min-height:2px} .dc i.movement,.dkey .movement i{background:#00b8d9} .dc i.spoke,.dkey .spoke i{background:#b48cff} .dc i.outside,.dkey .outside i{background:#ffa94d} .dc i.you,.dkey .you i{background:#3dffa8}
.dticks{position:relative;height:12px;margin:2px 2px 0} .dticks span{position:absolute;font-size:7.5px;font-weight:700;letter-spacing:.1em;color:rgba(150,220,235,.5)}
.dkey{display:flex;flex-wrap:wrap;gap:4px 12px;margin-top:4px} .dkey span{display:inline-flex;align-items:center;gap:5px;font-size:8px;font-weight:700;letter-spacing:.12em;color:rgba(150,220,235,.7)} .dkey i{display:inline-block;width:8px;height:8px;border-radius:2px}
.pran{font-size:8px;font-weight:700;letter-spacing:.12em;color:rgba(61,255,168,.75);margin-top:5px} .pran.bad{color:#ff6b80}
.e{padding:10px 12px;text-align:center;font-size:11px;letter-spacing:.2em;color:rgba(150,200,215,.45)}
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
.nav span{flex:1}
.onoff{flex:none;font-style:normal;font-size:8.5px;font-weight:700;letter-spacing:.14em;padding:2px 7px;border-radius:8px;color:rgba(150,220,235,.45);border:1px solid rgba(150,220,235,.18)}
.onoff.on{color:#7fefff;border-color:rgba(0,255,247,.45);background:rgba(0,255,247,.08)}
.ssec{margin:12px 10px 4px;font-size:9px;font-weight:700;letter-spacing:.26em;color:rgba(127,239,255,.55)}
.sfind{display:flex;align-items:center;gap:8px;margin:2px 0 6px;padding:7px 11px;border-radius:12px;border:1px solid rgba(0,255,247,.25);background:rgba(0,255,247,.04)}
.sfind input{flex:1;min-width:0;background:none;border:none;outline:none} .sfind:focus-within{border-color:rgba(0,255,247,.6)}
#snav{display:flex;flex-direction:column;gap:4px}
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
.btn.sm{padding:6px 12px} .r.prop{flex-wrap:wrap;gap:8px} .r.prop .btn{flex:none} .btn[disabled]{opacity:.35;cursor:default} .btn.ghost{color:#7fefff;background:none;border:1px solid rgba(0,255,247,.35);box-shadow:none}
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
.ml{padding:12px 14px} .ml .r{margin:0;background:none;border:none;padding:0 0 6px} .ml.off{opacity:.55} .doc.bad{border-left:3px solid var(--bad,#ff6b80)} .doc.warn{border-left:3px solid var(--warn,#ffbe55)} .doc.ok ha-icon{color:var(--ok,#3dffa8)} .x.pin ha-icon{color:var(--accent,#00fff7)} .x ha-icon{--mdc-icon-size:16px}
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
.tli.focus{background:rgba(0,255,247,.08);outline:1px solid rgba(0,255,247,.45);border-radius:10px}
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
/* Tablet and up: Overview, House, Mind and Security fit the screen. The cards share the height with no gaps,
   and a long list scrolls inside its own card (no visible scroll bar). Small or short screens keep the flowing page. */
@media (min-width: 900px) and (min-height: 520px){
  :host([view="overview"]),:host([view="house"]),:host([view="mind"]),:host([view="security"]){overflow-y:hidden}
  #ov,#hp,#mp,#scp{height:calc(100vh - 56px);height:calc(100dvh - 56px);display:flex;flex-direction:column;gap:10px;padding:10px 12px}
  #ov>*,#mp>*{margin-top:0}
  #ov>.card.head,#ov>.warnbar,#mp>.warnbar,#mp>.brain,#hp>.card.rooms{flex:none;margin:0}
  .lanes{flex:1 1 auto;min-height:0;margin-top:0;gap:10px;align-items:stretch !important}
  .lanes{grid-template-columns:none !important;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr)}
  .ov3>.lane:nth-child(3){display:flex !important;grid-column:auto !important}
  .lane{gap:10px;min-height:0;overflow-y:auto;scrollbar-width:none} .lane::-webkit-scrollbar{display:none}
  .lanes>.lane:not(:has(>.card:not([hidden]))){display:none !important}
  .lane>.lt{flex:none;margin:0 4px -2px}
  .lane>.card{flex:none;padding:10px 12px}
  #ov .lane>.card.said{flex:1 1 auto;min-height:160px;overflow-y:auto;scrollbar-width:none} #ov .lane>.card.said::-webkit-scrollbar{display:none}
  .card .h{margin-bottom:7px}
  .r{padding:5px 9px;margin-bottom:4px}
  .head{gap:14px;padding:10px 14px}
  .head img{width:84px;height:84px;border-radius:14px}
  .ti{font-size:21px}
  .st{margin-top:7px}
  .s{padding:5px 8px}
  .s .n{font-size:17px}
  .brh{gap:16px}
  .wbars{min-height:70px}
  .tchips{margin-top:8px}
}
`;
  customElements.define("sentient-panel", SentientPanel);
})();
