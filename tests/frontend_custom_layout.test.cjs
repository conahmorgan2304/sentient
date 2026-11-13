const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");

const panel = fs.readFileSync("custom_components/sentient/frontend/sentient-panel.js", "utf8");
const ws = fs.readFileSync("custom_components/sentient/websocket.py", "utf8");

test("optional layout editor is connected to HA-backed persistence, not local storage", () => {
  assert.match(panel, /_initLayouts\(\)/);
  assert.match(panel, /_applyLayouts\(force = false\)/);
  assert.match(panel, /type: "sentient\/layout", action: "save"/);
  assert.match(panel, /data-act="layoutreset"/);
  assert.match(panel, /data-act="layoutshowall"/);
  assert.match(panel, /this\._hass\.user\?\.is_admin/);
});

test("layout saves require authenticated administrators at server boundary", () => {
  assert.match(ws, /async def ws_layout/);
  assert.match(ws, /getattr\(connection\.user, "is_admin", False\)/);
  assert.match(ws, /"unauthorized", "Administrator access is required"/);
});


test("card drag-and-drop is editor-only and persists through admin websocket", () => {
  assert.match(panel, /_layoutDragStart\(event\)/);
  assert.match(panel, /_layoutDragOver\(event\)/);
  assert.match(panel, /_layoutDrop\(event\)/);
  assert.match(panel, /this\._layoutEnabled && this\._layoutEdit/);
  assert.match(panel, /action: "save", layout: this\._layoutData/);
});

test("the layout toolbar only appears once switched on in Settings", () => {
  assert.doesNotMatch(panel, /ENABLE CUSTOM PAGES/);
  assert.match(panel, /const editing = admin && this\._layoutEnabled && this\._layoutEdit/);
  assert.match(panel, /h\.energy_comparison \?/);  // no kWh button without a heating meter chosen
});


test("AI routing calibration is explicitly approved and visible only to admin", () => {
  assert.match(panel, /data-act="routecal"/);
  assert.match(panel, /g\.id === "door" && this\._hass\.user\?\.is_admin/);
  assert.match(panel, /window\.confirm\("Run ONE potentially billable/);
  assert.match(panel, /type: "sentient\/ai\/routing\/calibrate"/);
  assert.match(ws, /async def ws_ai_routing_calibrate/);
  assert.match(ws, /@websocket_api\.require_admin/);
});

test("the tabs show no edit button except while editing, which starts from Settings", () => {
  assert.doesNotMatch(panel, /"EDIT LAYOUT"/);
  assert.match(panel, /bar\.hidden = !editing;/);
  assert.match(panel, /data-act="layoutstart">EDIT PAGE LAYOUT/);
  assert.match(panel, /g\.id === "page" && this\._hass\.user\?\.is_admin && this\._settings\.data\.custom_pages === true/);
});


test("a Settings group page only uses names that exist where it's drawn", () => {
  // 1.5.12/1.5.13: the Doorbell & AI looks and This page groups wouldn't open for admins, because their extra
  // cards read `data`, which isn't defined in _drawBody (ReferenceError).
  const start = panel.indexOf("    _drawBody() {");
  const end = panel.indexOf("\n    _", start + 20);
  const body = panel.slice(start, end);
  const declared = /\b(?:const|let|var)\s+(?:[^;=]*[,{\s])?data\b/.test(body);
  if (!declared) assert.doesNotMatch(body, /[^.\w]data\.\w/);
});
