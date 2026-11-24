// The browser walk shared by the page checks (panel_pages.test.cjs) and scripts/panel_strings.cjs:
// a Home Assistant object with the recorded answers, the panel opened on a page, and taps on its own buttons.
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..");
const PANEL = path.join(ROOT, "custom_components/sentient/frontend/sentient-panel.js");

function fakeHass(fixture, admin, lang = "en") {
  // Runs in the browser: a Home Assistant object with the recorded answers. Writes succeed and change nothing.
  return `(() => {
    const fx = ${JSON.stringify(fixture)};
    const same = (a, b) => Object.keys(a).every((k) => k === "id" || JSON.stringify(a[k]) === JSON.stringify(b[k]));
    const find = (msg) => fx.answers.find((x) => same(x.request, msg))
      || fx.answers.find((x) => x.request.type === msg.type && (msg.kind === undefined || x.request.kind === msg.kind));
    window.__unanswered = [];
    const hass = {
      states: fx.states, entities: fx.entities, devices: {}, language: ${JSON.stringify(lang)},
      locale: { language: ${JSON.stringify(lang)}, number_format: "language", time_format: "24" },
      user: { id: "u1", name: "Tester", is_admin: ${admin ? "true" : "false"} },
      themes: { darkMode: true }, config: { time_zone: "Europe/London", unit_system: { temperature: "°C" } },
      callWS(msg) {
        const hit = find(msg);
        if (hit) return hit.error ? Promise.reject(hit.error) : Promise.resolve(JSON.parse(JSON.stringify(hit.result)));
        const write = /\\/(save|delete|set|forget|update|enable|act|seen|finish|test|clear|dismiss|thumb|style|review|feedback|resolve|learn|aliases|restore|import|stop|try|ignore|disable_copy|unwatch|enrol|enrol_cancel|threshold|calibrate|energy_comparison)$/.test(msg.type)
          || ["save", "reset", "enable"].includes(msg.action);
        if (!write && msg.type.startsWith("sentient/")) window.__unanswered.push(msg.type);  // Home Assistant's own (history) get {}
        return Promise.resolve({});
      },
      callService() { return Promise.resolve(); },
      connection: { subscribeEvents() { return Promise.resolve(() => {}); }, subscribeMessage() { return Promise.resolve(() => {}); } },
    };
    window.confirm = () => false; window.alert = () => {}; window.prompt = () => null;
    return hass;
  })()`;
}

async function openPage(browser, fixture, admin, lang = "en") {
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  const errors = [];
  page.on("pageerror", (err) => errors.push(String(err && err.stack || err)));
  page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });
  if (lang === "en") await page.setContent("<!doctype html><html><body></body></html>");
  else {
    // another language: serve the frontend folder as Home Assistant does, so the page can fetch its words
    const dir = path.dirname(PANEL);
    await page.route("http://sentient.test/**", (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/") return route.fulfill({ contentType: "text/html", body: "<!doctype html><html><body></body></html>" });
      const file = path.join(dir, url.pathname.replace(/^\/sentient_frontend\//, ""));
      if (!url.pathname.startsWith("/sentient_frontend/") || !file.startsWith(dir) || !fs.existsSync(file)) return route.fulfill({ status: 404, body: "" });
      return route.fulfill({ contentType: file.endsWith(".js") ? "text/javascript" : "application/json", body: fs.readFileSync(file) });
    });
    await page.goto("http://sentient.test/");
  }
  await page.addScriptTag({ path: PANEL });
  await page.evaluate(`window.__hass = ${fakeHass(fixture, admin, lang)};
    window.addEventListener("unhandledrejection", (e) => { throw new Error("unhandled rejection: " + (e.reason && (e.reason.stack || e.reason.message) || JSON.stringify(e.reason))); });
    const el = document.createElement("sentient-panel");
    el.narrow = false; el.panel = { config: {} };
    document.body.appendChild(el);
    el.hass = window.__hass;
    window.__panel = el;`);
  await page.waitForTimeout(300);
  return { page, errors };
}

const settle = (page) => page.waitForTimeout(120);

async function tap(page, action) {
  // the page's own button, once it's there (an editor's "add" appears after its list has loaded)
  for (let i = 0; i < 20; i++) {
    const done = await page.evaluate((a) => {
      const b = window.__panel.shadowRoot.querySelector(`[data-act="${a}"]`);
      if (b) b.click();
      return !!b;
    }, action);
    if (done) { await settle(page); return true; }
    await page.waitForTimeout(50);
  }
  return false;
}

async function act(page, action, id, extra = {}) {
  // goes through the page's own click handler, as a person tapping a button would
  await page.evaluate(([a, i, x]) => {
    const p = window.__panel, b = document.createElement("button");
    b.dataset.act = a; if (i != null) b.dataset.id = i;
    for (const [k, v] of Object.entries(x)) b.dataset[k] = v;
    b.style.display = "none"; p.shadowRoot.appendChild(b); b.click(); b.remove();
  }, [action, id, extra]);
  await settle(page);
}

module.exports = { fakeHass, openPage, settle, tap, act, PANEL };
