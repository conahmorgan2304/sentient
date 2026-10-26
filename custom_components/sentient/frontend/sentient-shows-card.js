/* Sentient light shows card — buttons for the shows chosen in Sentient's settings
   ("Buttons on the overview"). Loads with the integration; add it to any dashboard as
   `type: custom:sentient-shows-card`. Shows nothing at all when no show is chosen and none is on,
   so it never clutters a dashboard. Optional: `entity` (the Light show switch), `title`. */
(() => {
  if (customElements.get("sentient-shows-card")) return;
  const SHOWS = {
    disco: ["mdi:party-popper", "Disco"], party: ["mdi:glass-cocktail", "Party"], rainbow: ["mdi:looks", "Rainbow"],
    chill: ["mdi:weather-night", "Chill"], halloween: ["mdi:halloween", "Halloween"], christmas: ["mdi:pine-tree", "Christmas"],
  };
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  class SentientShowsCard extends HTMLElement {
    setConfig(config) { this._config = config || {}; this._sig = null; }
    static getStubConfig() { return {}; }
    getCardSize() { return 1; }
    getGridOptions() { return { columns: 12, rows: "auto" }; }

    _switch(hass) {
      if (this._config.entity) return hass.states[this._config.entity];
      return Object.values(hass.states).find((s) => s.entity_id.startsWith("switch.") && Array.isArray(s.attributes.buttons)
        && "following_sound" in s.attributes);
    }

    set hass(hass) {
      this._hass = hass;
      const st = this._switch(hass);
      const sig = st ? `${st.state}|${st.attributes.style}|${(st.attributes.buttons || []).join(",")}|${st.attributes.until}|${st.attributes.following_sound}` : "none";
      if (sig === this._sig) return;
      this._sig = sig;
      this._draw(st);
    }

    _draw(st) {
      if (!this.shadowRoot) this.attachShadow({ mode: "open" });
      const on = !!st && st.state === "on", style = st && st.attributes.style;
      const list = st ? (st.attributes.buttons || []).filter((s) => SHOWS[s]) : [];
      if (on && style && SHOWS[style] && !list.includes(style)) list.push(style);
      this.style.display = list.length ? "" : "none"; // nothing chosen: take no space at all
      if (!list.length) { this.shadowRoot.innerHTML = ""; return; }
      const until = on && st.attributes.until ? new Date(st.attributes.until).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "";
      this.shadowRoot.innerHTML = `<style>
        :host{display:block}
        ha-card{padding:12px 14px;background:rgba(10,15,25,.9);border:1px solid rgba(0,255,247,.3);border-radius:18px;
          box-shadow:0 0 14px rgba(0,255,247,.15),inset 0 0 16px rgba(0,255,247,.05)}
        .h{display:flex;align-items:center;gap:8px;margin-bottom:10px;font:700 11px/1 system-ui,sans-serif;letter-spacing:.24em;color:#00fff7;
          text-shadow:0 0 6px rgba(0,255,247,.5)} .h span{margin-left:auto;letter-spacing:.08em;color:rgba(150,220,235,.7);font-weight:600}
        .b{display:flex;flex-wrap:wrap;gap:8px}
        button{all:unset;cursor:pointer;display:flex;align-items:center;gap:7px;padding:9px 14px;border-radius:14px;
          font:700 11px/1 system-ui,sans-serif;letter-spacing:.14em;text-transform:uppercase;color:#bfefff;
          border:1px solid rgba(0,255,247,.3);background:rgba(0,255,247,.04);-webkit-tap-highlight-color:transparent}
        button ha-icon{--mdc-icon-size:18px;color:#5fd8e6}
        button:hover{background:rgba(0,255,247,.09)}
        button.on{color:#041016;background:#00fff7;border-color:#00fff7;box-shadow:0 0 16px rgba(0,255,247,.55)} button.on ha-icon{color:#041016}
        button.halloween.on{background:#ff8a1f;border-color:#ff8a1f;box-shadow:0 0 16px rgba(255,138,31,.55)}
        button.christmas.on{background:#ff4d5e;border-color:#ff4d5e;box-shadow:0 0 16px rgba(255,77,94,.55)}
      </style><ha-card>
        <div class="h"><ha-icon icon="mdi:party-popper"></ha-icon>${esc(this._config.title || "LIGHT SHOWS")}
          <span>${on ? `${st.attributes.following_sound ? "♪ " : ""}${until ? "TILL " + until : "ON"}` : ""}</span></div>
        <div class="b">${list.map((s) => {
          const lit = on && style === s;
          return `<button class="${s}${lit ? " on" : ""}" data-s="${s}"><ha-icon icon="${SHOWS[s][0]}"></ha-icon>${SHOWS[s][1]}${lit ? " · stop" : ""}</button>`;
        }).join("")}</div></ha-card>`;
      this.shadowRoot.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
        const s = b.dataset.s, cur = this._switch(this._hass);
        const stop = cur && cur.state === "on" && cur.attributes.style === s;
        this._hass.callService("sentient", "party", stop ? { on: false } : { style: s }).catch((e) => window.alert(e.message || e));
      }));
    }
  }

  customElements.define("sentient-shows-card", SentientShowsCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "sentient-shows-card", name: "Sentient light shows",
    description: "Buttons for the light shows chosen in Sentient's settings; hidden when none are." });
})();
