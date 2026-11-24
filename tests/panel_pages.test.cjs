// Page checks: open every tab, every Settings page and the main editors of the sidebar page in a real browser,
// against what the real integration answered (recorded by tests/test_panel_fixture.py into build/panel/).
// Any uncaught error or unhandled promise rejection on the page fails the check — a page that won't open,
// like "Doorbell & AI looks" in 1.5.12, fails here instead of in someone's house.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..");
const { openPage, settle, tap, act } = require("./panel_harness.cjs");
const FIXTURES = ["defaults", "all_on"].map((n) => path.join(ROOT, "build/panel", n + ".json"));

let chromium;
try { ({ chromium } = require("playwright")); } catch (_) { chromium = null; }

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
