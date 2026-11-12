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
  return { Panel, store, context };
}

function setup(features, platform = null) {
  const { Panel, store, context } = load();
  const img = { listeners: {}, addEventListener(n, f) { this.listeners[n] = f; }, removeAttribute() {} };
  const holder = { isConnected: true, innerHTML: '', querySelector: (s) => (s === 'img' ? img : null) };
  const el = { innerHTML: '', querySelector: (s) => (s === '.lvc' ? holder : null) };
  const panel = new Panel();
  panel._$ = () => el;
  panel._cams = { cameras: [{ entity_id: 'camera.nest' }] };
  panel._hass = { entities: platform ? { 'camera.nest': { platform } } : {}, states: { 'camera.nest': { attributes: { entity_picture: '/api/camera_proxy/camera.nest?token=x', supported_features: features, friendly_name: 'Nest' } } } };
  panel.renders = 0;
  panel._render = () => { panel.renders++; };
  return { panel, img, holder, store, context };
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

test('Nest defaults to native live player before a successful placeholder picture can suppress fallback', () => {
  const { panel, img, store } = setup(2, 'nest');
  panel._drawLive({ security_events: [] });
  assert.equal(panel._liveCamKey, 'camera.nest|smooth');
  assert.equal(img.listeners.load, undefined);
  assert.equal(store['sentient-live-auto'], undefined);
  panel._clearLive();
});

test('Nest manual picture preference overrides native live default', () => {
  const { panel, store } = setup(2, 'nest');
  store['sentient-live-picture'] = JSON.stringify(['camera.nest']);
  panel._drawLive({ security_events: [] });
  assert.equal(panel._liveCamKey, 'camera.nest|picture');
  panel._clearLive();
});

test('non-Nest cameras preserve the picture-first behaviour', () => {
  const { panel } = setup(2, 'wyzeapi');
  panel._drawLive({ security_events: [] });
  assert.equal(panel._liveCamKey, 'camera.nest|picture');
  panel._clearLive();
});


test('native video initialisation failure falls back to an image rather than an empty player', async () => {
  const { panel, holder, context } = setup(2, 'nest');
  context.window.loadCardHelpers = () => Promise.reject(new Error('player unavailable'));
  panel._drawLive({ security_events: [] });
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(holder.innerHTML, /<img /);
  panel._clearLive();
});

function fakeTimers(context) {
  const due = [];
  context.setTimeout = (f, ms) => { due.push({ f, ms }); return due.length; };
  context.clearTimeout = (id) => { if (due[id - 1]) due[id - 1].f = null; };
  return () => { for (const t of due.splice(0)) if (t.f) t.f(); };
}

test('a native player that loads but never shows a frame falls back to the picture', async () => {
  const { panel, holder, context } = setup(2, 'nest');
  const run = fakeTimers(context);
  const card = { localName: 'hui-picture-entity-card', querySelectorAll: () => [], shadowRoot: null };
  holder.appendChild = (c) => { holder.child = c; };
  context.window.loadCardHelpers = () => Promise.resolve({ createCardElement: () => card });
  panel._drawLive({ security_events: [] });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(holder.child, card);
  run();
  assert.match(holder.innerHTML, /<img /);
  panel._clearLive();
});

test('a native player showing video is kept', async () => {
  const { panel, holder, context } = setup(2, 'nest');
  const run = fakeTimers(context);
  const video = { readyState: 4 };
  const card = { localName: 'hui-picture-entity-card', shadowRoot: null,
                 querySelectorAll: (s) => (s === 'video' ? [video] : []) };
  holder.appendChild = (c) => { holder.child = c; };
  context.window.loadCardHelpers = () => Promise.resolve({ createCardElement: () => card });
  panel._drawLive({ security_events: [] });
  await new Promise((resolve) => setImmediate(resolve));
  run();
  assert.doesNotMatch(holder.innerHTML, /<img /);
  assert.equal(panel._liveCard, card);
  panel._clearLive();
});
