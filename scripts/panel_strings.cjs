// Lists the words the sidebar page shows: opens every tab, Settings page, editor and the wizard in a browser
// (the same walk as tests/panel_pages.test.cjs, against build/panel/*.json) and collects every text node and
// title/placeholder/aria-label. With --all it prints everything seen; otherwise only text that is written in
// sentient-panel.js itself (what comes from the house — names, memories, timeline — isn't the page's to translate).
// Usage: node scripts/panel_strings.cjs [--all] > out.json
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const ROOT = path.join(__dirname, "..");
const PANEL = path.join(ROOT, "custom_components/sentient/frontend/sentient-panel.js");
const FIXTURES = ["defaults", "all_on"].map((n) => path.join(ROOT, "build/panel", n + ".json"));

const COLLECT = `(() => {
  const out = new Set();
  const walk = (root) => {
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    for (let n = tw.currentNode; n; n = tw.nextNode()) {
      if (n.nodeType === 3) {
        const p = n.parentElement;
        if (p && (p.tagName === "STYLE" || p.tagName === "SCRIPT")) continue;
        const t = n.nodeValue.trim(); if (t) out.add(t);
      } else {
        for (const a of ["title", "placeholder", "aria-label", "label"]) { const v = n.getAttribute && n.getAttribute(a); if (v && v.trim()) out.add(v.trim()); }
        if (n.shadowRoot && n !== window.__panel) walk(n.shadowRoot);
      }
    }
  };
  walk(window.__panel.shadowRoot);
  return [...out];
})()`;

(async () => {
  const all = process.argv.includes("--all");
  const src = fs.readFileSync(PANEL, "utf8").replace(/&amp;/g, "&").replace(/&middot;/g, "·").replace(/&rarr;/g, "→").replace(/&times;/g, "×");
  const browser = await chromium.launch();
  const seen = new Set();
  const t = require(path.join(ROOT, "tests/panel_harness.cjs"));
  for (const file of FIXTURES) {
    const fixture = JSON.parse(fs.readFileSync(file, "utf8"));
    for (const admin of [true, false]) {
      const { page } = await t.openPage(browser, fixture, admin);
      const grab = async () => { for (const s of await page.evaluate(COLLECT)) seen.add(s); };
      for (const view of ["overview", "house", "mind", "security", "timeline", "settings"]) {
        await page.evaluate((v) => window.__panel._show(v, true), view); await t.settle(page); await grab();
      }
      if (admin) {
        for (const id of await page.evaluate(() => Object.keys(window.__panel._pages()))) { await t.act(page, "goto", id); await grab(); }
        for (const kind of ["alarms", "schedules", "offers", "announce", "checks", "autooff", "rules", "rooms", "maintenance"]) {
          await t.act(page, "goto", kind); await t.tap(page, "itadd"); await grab();
        }
        await t.act(page, "goto", "motion"); await t.tap(page, "mladd"); await grab();
        await t.act(page, "goto", "shortcuts"); await t.tap(page, "scadd"); await grab();
        const wizard = fixture.answers.find((a) => a.request.type === "sentient/wizard");
        if (wizard && wizard.result) { await page.evaluate((w) => window.__panel._openWizard(w), wizard.result); await t.settle(page); await grab(); }
        await t.act(page, "layoutstart"); await grab();
      }
      await page.close();
    }
  }
  await browser.close();
  const words = [...seen].filter((s) => /[A-Za-z]{2}/.test(s) && (all || src.includes(s))).sort();
  process.stdout.write(JSON.stringify(words, null, 1) + "\n");
})();
