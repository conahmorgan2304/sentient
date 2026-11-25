// The page's words in other languages (custom_components/sentient/frontend/i18n/).
// 1. Every language has every English piece from en.json — no more, no fewer — with the same {slots}, and
//    pieces in capitals stay in capitals.
// 2. In a browser, in Polish: every tab, Settings page and editor opens without errors, and every word the
//    page writes itself (text that's in sentient-panel.js) has a translation. A new English label without one
//    fails here: add it to i18n/en.json and to every language.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..");
const DIR = path.join(ROOT, "custom_components/sentient/frontend/i18n");
const EN = JSON.parse(fs.readFileSync(path.join(DIR, "en.json"), "utf8"));
const LANGS = fs.readdirSync(DIR).filter((f) => f.endsWith(".json") && f !== "en.json").map((f) => f.slice(0, -5));
const SLOTS = (s) => (s.match(/\{[a-z0-9]+\}/g) || []).sort().join(",");
const caps = (s) => /[A-Z]/.test(s) && s === s.toUpperCase();

test("en.json is a sorted list of unique English pieces", () => {
  assert.ok(Array.isArray(EN) && EN.length > 500);
  assert.equal(new Set(EN).size, EN.length, "duplicates in en.json");
  assert.deepEqual([...EN].sort(), EN, "keep en.json sorted");
});

for (const lang of LANGS) {
  test(`i18n/${lang}.json has every piece, with its slots`, () => {
    const tr = JSON.parse(fs.readFileSync(path.join(DIR, lang + ".json"), "utf8"));
    const missing = EN.filter((k) => !(k in tr));
    const extra = Object.keys(tr).filter((k) => !EN.includes(k));
    assert.deepEqual(missing, [], `${lang}: missing translations`);
    assert.deepEqual(extra, [], `${lang}: pieces that aren't in en.json any more`);
    for (const k of EN) {
      assert.ok(typeof tr[k] === "string" && tr[k].trim(), `${lang}: empty translation for ${JSON.stringify(k)}`);
      assert.equal(SLOTS(tr[k]), SLOTS(k), `${lang}: slots differ in ${JSON.stringify(k)}`);
      if (caps(k)) assert.equal(tr[k], tr[k].toUpperCase(), `${lang}: ${JSON.stringify(k)} is in capitals on the page`);
    }
  });
}

test("the translator: whole pieces, capitals, numbers, named slots and dotted lists", async () => {
  const m = await import(path.join(ROOT, "custom_components/sentient/frontend/sentient-i18n.js"));
  const d = m.compile({ "Set-up wizard": "Kreator", "{0} to look at": "{0} do sprawdzenia", "Warmest {name} {0}°": "Najcieplej: {name} {0}°",
    Kitchen: "Kuchnia", Awake: "Czuwa", "{0} on": "{0} wł.", "STEP {0} OF {1}": "KROK {0} Z {1}" });
  assert.equal(m.translate("Set-up wizard", d), "Kreator");
  assert.equal(m.translate("SET-UP WIZARD", d), "KREATOR");
  assert.equal(m.translate("3 TO LOOK AT", d), "3 DO SPRAWDZENIA");
  assert.equal(m.translate("Warmest Kitchen 21.2°", d), "Najcieplej: Kuchnia 21.2°");
  assert.equal(m.translate("AWAKE · 2 ON · ELSE", d), "CZUWA · 2 WŁ. · ELSE");
  assert.equal(m.translate("STEP 1 OF 11", d), "KROK 1 Z 11");
  assert.equal(m.translate("  Set-up wizard ", d), "  Kreator ");
  assert.equal(m.translate("Not in the list", d), "Not in the list");
  assert.equal(m.translate("Set-up wizard", null), "Set-up wizard");
});

let chromium;
try { ({ chromium } = require("playwright")); } catch (_) { chromium = null; }

test("in Polish, every page opens and every word the page writes is translated", { skip: !chromium && !process.env.CI && "playwright isn't installed" }, async () => {
  const { openPage, settle, tap, act, PANEL } = require("./panel_harness.cjs");
  const file = path.join(ROOT, "build/panel/all_on.json");
  assert.ok(fs.existsSync(file), `${file} is missing: run pytest tests/test_panel_fixture.py first`);
  const fixture = JSON.parse(fs.readFileSync(file, "utf8"));
  const src = fs.readFileSync(PANEL, "utf8").replace(/&amp;/g, "&").replace(/&middot;/g, "·").replace(/&rarr;/g, "→");
  const m = await import(path.join(ROOT, "custom_components/sentient/frontend/sentient-i18n.js"));
  const pl = m.compile(JSON.parse(fs.readFileSync(path.join(DIR, "pl.json"), "utf8")));
  const browser = await chromium.launch();
  try {
    const { page, errors } = await openPage(browser, fixture, true, "pl");
    const seen = new Set();
    const grab = async () => {
      for (const s of await page.evaluate(() => {
        const out = [];
        const tw = document.createTreeWalker(window.__panel.shadowRoot, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
        for (let n = tw.currentNode; n; n = tw.nextNode()) {
          if (n.nodeType === 3) { const p = n.parentElement; if (!(p && /^(STYLE|SCRIPT|TEXTAREA)$/.test(p.tagName))) out.push(n.nodeValue.trim()); }
          else for (const a of ["title", "placeholder", "aria-label"]) if (n.getAttribute && n.getAttribute(a)) out.push(n.getAttribute(a).trim());
        }
        return out;
      })) if (s) seen.add(s);
    };
    await page.waitForTimeout(500);
    for (const view of ["overview", "house", "mind", "security", "timeline", "settings"]) {
      await page.evaluate((v) => window.__panel._show(v, true), view); await settle(page); await grab();
    }
    for (const id of await page.evaluate(() => Object.keys(window.__panel._pages()))) { await act(page, "goto", id); await grab(); }
    for (const kind of ["alarms", "schedules", "offers", "announce", "checks", "autooff", "rules", "rooms", "maintenance"]) {
      await act(page, "goto", kind); await tap(page, "itadd"); await grab();
    }
    await page.evaluate(() => window.__panel._show("settings", true)); await settle(page); await grab();
    const tabs = await page.evaluate(() => [...window.__panel.shadowRoot.querySelectorAll("nav.tabs span")].map((s) => s.textContent));
    await page.close();
    // pictures (camera thumbnails, icons) point at Home Assistant's own addresses, which this test page doesn't serve
    assert.deepEqual(errors.filter((e) => !e.includes("Failed to load resource")), [], "errors on the page in Polish");
    assert.ok(tabs.includes("USTAWIENIA") && tabs.includes("PRZEGLĄD"), `tabs aren't in Polish: ${tabs.join(", ")}`);
    // anything still in English that the page wrote itself: text standing on its own in sentient-panel.js
    // (between tags or quotes), not already Polish, and not a name that reads the same everywhere
    const SAME = new Set(["OK", "AI", "SENTIENT", "HACS", "MQTT", "TTS", "Frigate", "Alexa", "Echo"]);
    const polish = new Set(Object.values(JSON.parse(fs.readFileSync(path.join(DIR, "pl.json"), "utf8"))));
    const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const standalone = (s) => new RegExp(`[>"'\`]\\s*${esc(s)}\\s*[<"'\`$]`).test(src);
    const english = [...seen].filter((s) => /[A-Za-z]{2}/.test(s) && !SAME.has(s) && !polish.has(s) && standalone(s)
      && !m.covered(s, pl));
    assert.deepEqual(english, [], "page text with no translation: add it to i18n/en.json and every language");
  } finally {
    await browser.close();
  }
});

test("Sentient's own sentences: openings, endings and a known ending kept at the end", async () => {
  const m = await import(path.join(ROOT, "custom_components/sentient/frontend/sentient-i18n.js"));
  const d = m.compile({ Awake: "Czuwa" }, {
    "Running now: ": "Teraz działa: ", " (someone was up)": " (ktoś nie spał)", "The AI": "AI",
    "{a} is offline": "{a} jest offline", "It's down to {a} degrees in {b}": "Spadło do {a} stopni w {b}",
    " — it's clear now": " — już czysto", Kitchen: "Kuchnia", "{a} thing{b} to look at": "{a} rzeczy do sprawdzenia" });
  assert.equal(m.translate("Running now: The AI is offline", d), "Teraz działa: AI jest offline");
  assert.equal(m.translate("Lights on (someone was up)", d), "Lights on (ktoś nie spał)");
  assert.equal(m.translate("It's down to 2 degrees in Kitchen — it's clear now", d), "Spadło do 2 stopni w Kuchnia — już czysto");
  assert.equal(m.translate("1 thing to look at", d), "1 rzeczy do sprawdzenia");
  assert.equal(m.translate("Awake · The AI is offline", d), "Czuwa · AI jest offline");
  assert.equal(m.translate("Not one of ours", d), "Not one of ours");
  // page pieces never become openings or endings, so " on" on the page doesn't eat sentence endings
  assert.equal(m.compile({ " on": " wł." }).endings.length, 0);
});

test("the house catalogue: every language has every sentence, with no slots of its own", () => {
  const H = path.join(DIR, "house");
  const en = JSON.parse(fs.readFileSync(path.join(H, "en.json"), "utf8"));
  assert.deepEqual([...en].sort(), en, "keep house/en.json sorted");
  for (const lang of LANGS) {
    const tr = JSON.parse(fs.readFileSync(path.join(H, lang + ".json"), "utf8"));
    assert.deepEqual(Object.keys(tr).sort(), [...en].sort(), `house/${lang}.json keys`);
    for (const k of en) for (const s of tr[k].match(/\{[a-z]+\}/g) || []) assert.ok(k.includes(s), `${lang}: ${s} in ${JSON.stringify(k)}`);
  }
});

test("houseText follows the Mind sensor's switch", async () => {
  const m = await import(path.join(ROOT, "custom_components/sentient/frontend/sentient-i18n.js"));
  const hass = (on) => ({ entities: { "sensor.mind": { entity_id: "sensor.mind", platform: "sentient", translation_key: "mind" } },
    states: { "sensor.mind": { attributes: on === undefined ? {} : { house_language_text: on } } } });
  assert.equal(m.houseText(hass(false)), false);
  assert.equal(m.houseText(hass(true)), true);
  assert.equal(m.houseText(hass()), true);
});
