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
