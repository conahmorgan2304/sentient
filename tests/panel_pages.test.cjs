// Page checks: open every tab, every Settings page and the main editors of the sidebar page in a real browser,
// against what the real integration answered (recorded by tests/test_panel_fixture.py into build/panel/).
// Any uncaught error or unhandled promise rejection on the page fails the check — a page that won't open,
// like "Doorbell & AI looks" in 1.5.12, fails here instead of in someone's house.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..");
const PANEL = path.join(ROOT, "custom_components/sentient/frontend/sentient-panel.js");
const FIXTURES = ["defaults", "all_on"].map((n) => path.join(ROOT, "build/panel", n + ".json"));

let chromium;
try { ({ chromium } = require("playwright")); } catch (_) { chromium = null; }

function fakeHass(fixture, admin) {
  // Runs in the browser: a Home Assistant object with the recorded answers. Writes succeed and change nothing.
  return `(() => {
    const fx = ${JSON.stringify(fixture)};
    const same = (a, b) => Object.keys(a).every((k) => k === "id" || JSON.stringify(a[k]) === JSON.stringify(b[k]));
    const find = (msg) => fx.answers.find((x) => same(x.request, msg))
      || fx.answers.find((x) => x.request.type === msg.type && (msg.kind === undefined || x.request.kind === msg.kind));
    window.__unanswered = [];
    const hass = {
      states: fx.states, entities: fx.entities, devices: {}, language: "en",
      locale: { language: "en", number_format: "language", time_format: "24" },
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

async function openPage(browser, fixture, admin) {
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  const errors = [];
  page.on("pageerror", (err) => errors.push(String(err && err.stack || err)));
  page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });
  await page.setContent("<!doctype html><html><body></body></html>");
  await page.addScriptTag({ path: PANEL });
  await page.evaluate(`window.__hass = ${fakeHass(fixture, admin)};
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

for (const file of FIXTURES) {
  const name = path.basename(file, ".json");
  test(`every tab, Settings page and editor opens (${name})`, { skip: !chromium && !process.env.CI && "playwright isn't installed" }, async () => {
    assert.ok(fs.existsSync(file), `${file} is missing: run pytest tests/test_panel_fixture.py first`);
    const fixture = JSON.parse(fs.readFileSync(file, "utf8"));
    const browser = await chromium.launch();
    try {
      for (const admin of [true, false]) {
        const { page, errors } = await openPage(browser, fixture, admin);
        const opened = [];
        for (const view of ["overview", "house", "mind", "security", "timeline", "settings"]) {
          await page.evaluate((v) => window.__panel._show(v, true), view);
          await settle(page);
          opened.push(view);
        }
        if (admin) {
          const pages = await page.evaluate(() => Object.keys(window.__panel._pages()));
          assert.ok(pages.length > 30, `only ${pages.length} Settings pages found`);
          for (const id of pages) {
            await act(page, "goto", id);
            opened.push("settings:" + id);
          }
          // the editors and dialogs
          for (const kind of ["alarms", "schedules", "offers", "announce", "checks", "autooff", "rules", "rooms", "maintenance"]) {
            await act(page, "goto", kind);
            assert.ok(await tap(page, "itadd"), `no add button on ${kind}`);
            opened.push("editor:" + kind);
          }
          await act(page, "goto", "motion"); assert.ok(await tap(page, "mladd"), "no add button on motion lights"); opened.push("editor:motion");
          await act(page, "goto", "shortcuts"); assert.ok(await tap(page, "scadd"), "no add button on voice routines"); opened.push("editor:shortcuts");
          const wizard = fixture.answers.find((a) => a.request.type === "sentient/wizard");
          if (wizard && wizard.result) {
            await page.evaluate((w) => window.__panel._openWizard(w), wizard.result);
            await settle(page); opened.push("wizard");
          }
          await act(page, "layoutstart"); opened.push("layout editing");
          await page.evaluate(() => window.__panel._show("mind", true)); await settle(page);
          await act(page, "layouttoggle"); opened.push("layout done");
        }
        const missing = await page.evaluate(() => [...new Set(window.__unanswered)]);
        await page.close();
        assert.deepEqual(errors, [], `${name}, ${admin ? "admin" : "not admin"}: errors after opening ${opened.length} pages`);
        assert.deepEqual(missing, [], `${name}: the page asked for something the fixture doesn't record — add it to READS in tests/test_panel_fixture.py`);
      }
    } finally {
      await browser.close();
    }
  });
}
