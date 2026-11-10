const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function load() {
  let Panel;
  const store = {};
  const context = {
    HTMLElement: class {}, customElements: { get: () => null, define: (_, cls) => { Panel = cls; } },
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } },
    setTimeout, clearTimeout,
  };
  context.window = { loadCardHelpers: () => new Promise(() => {}), matchMedia: () => ({ matches: false }) };
  vm.runInNewContext(fs.readFileSync('custom_components/sentient/frontend/sentient-panel.js', 'utf8'), context);
  return { Panel, store };
}

function setup(features) {
  const { Panel, store } = load();
  const img = { listeners: {}, addEventListener(n, f) { this.listeners[n] = f; }, removeAttribute() {} };
  const holder = { isConnected: true, innerHTML: '', querySelector: (s) => (s === 'img' ? img : null) };
  const el = { innerHTML: '', querySelector: (s) => (s === '.lvc' ? holder : null) };
  const panel = new Panel();
  panel._$ = () => el;
  panel._cams = { cameras: [{ entity_id: 'camera.nest' }] };
  panel._hass = { states: { 'camera.nest': { attributes: { entity_picture: '/api/camera_proxy/camera.nest?token=x', supported_features: features, friendly_name: 'Nest' } } } };
  panel.renders = 0;
  panel._render = () => { panel.renders++; };
  return { panel, img, holder, store };
}

test('a camera whose picture feed fails is moved to the Home Assistant player once and remembered', () => {
  const { panel, img, store } = setup(2);
  panel._drawLive({ security_events: [] });
  assert.ok(img.listeners.error && img.listeners.load);
  img.listeners.error();
  assert.deepEqual(JSON.parse(store['sentient-live-auto']), ['camera.nest']);
  assert.equal(panel.renders, 1);
  panel._clearLive();
});

test('a camera whose picture feed loads is left alone', () => {
  const { panel, img, store } = setup(2);
  panel._drawLive({ security_events: [] });
  img.listeners.load();
  assert.equal(store['sentient-live-auto'], undefined);
  assert.equal(panel.renders, 0);
  panel._clearLive();
});

test('a camera that cannot use the player never gets a fallback', () => {
  const { panel, img, store } = setup(0);
  panel._drawLive({ security_events: [] });
  assert.equal(img.listeners.error, undefined);
  assert.equal(store['sentient-live-auto'], undefined);
  panel._clearLive();
});
