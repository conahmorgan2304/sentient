const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let Panel;
vm.runInNewContext(fs.readFileSync('custom_components/sentient/frontend/sentient-panel.js', 'utf8'), {
  HTMLElement: class {}, customElements: { get: () => null, define: (_, cls) => { Panel = cls; } },
});
function setup(callWS) {
  const panel = new Panel();
  panel.mind = { security_event_feedback_enabled: true, security_events: [
    { id: 'event-one', camera: 'garden', label: 'person', alerts: [], limits: 'Not live evidence',
      analysis: { summary: '<unsafe description>' }, snapshot: true, clip: true },
  ] };
  panel._hass = { user: { is_admin: true }, callWS };
  panel._st = () => ({ attributes: panel.mind });
  panel._render = () => { panel.html = panel._securityEventRows(panel.mind); };
  panel._render();
  return panel;
}

test('event feedback waits for acknowledgement, prevents repeat clicks and clears visibly', async () => {
  let finish, calls = 0;
  const panel = setup(() => { calls++; return new Promise(resolve => { finish = resolve; }); });
  const pending = panel._securityFeedback('event-one', 'cat');
  assert.match(panel.html, /Saving event feedback/);
  assert.match(panel.html, /disabled data-act="securityfeedback"/);
  assert.doesNotMatch(panel.html, /Saved feedback:/);
  await panel._securityFeedback('event-one', 'cat');
  assert.equal(calls, 1);
  finish({ feedback: { value: 'cat' } });
  await pending;
  assert.match(panel.html, /Saved feedback: cat/);
  panel._hass.callWS = async () => ({ feedback: null });
  await panel._securityFeedback('event-one', 'clear');
  assert.doesNotMatch(panel.html, /Saved feedback:|Saving event feedback/);
});

test('feedback errors are visible and escaped, with retry available', async () => {
  const panel = setup(async () => { throw new Error('<event expired>'); });
  await panel._securityFeedback('event-one', 'cat');
  assert.match(panel.html, /role="alert">&lt;event expired&gt;/);
  assert.doesNotMatch(panel.html, /Saved feedback:|disabled data-act/);
  assert.match(panel.html, /&lt;unsafe description&gt;/);
  assert.doesNotMatch(panel.html, /<unsafe description>/);
});

test('feedback controls disappear when disabled or non-admin and no request is sent', async () => {
  let calls = 0;
  const panel = setup(async () => { calls++; });
  panel.mind.security_event_feedback_enabled = false;
  panel._render();
  assert.doesNotMatch(panel.html, /data-act="securityfeedback"/);
  await panel._securityFeedback('event-one', 'cat');
  panel.mind.security_event_feedback_enabled = true;
  panel._hass.user.is_admin = false;
  panel._render();
  assert.doesNotMatch(panel.html, /data-act="securityfeedback"/);
  await panel._securityFeedback('event-one', 'cat');
  assert.equal(calls, 0);
});

test('motion cannot select an excluded camera and leaving Security removes the stream', () => {
  const panel = new Panel();
  panel._hass = { states: {
    'camera.garden': { attributes: { entity_picture: '/api/camera_proxy/garden' } },
    'camera.ignored': { attributes: { entity_picture: '/api/camera_proxy/ignored' } },
  } };
  panel._cams = { cameras: [{ entity_id: 'camera.garden' }] };
  panel._liveCam = 'camera.ignored';
  panel.shadowRoot = {};
  const holder = { innerHTML: '' };
  let aborted = false;
  const live = {
    innerHTML: '',
    querySelector(selector) {
      if (selector === '.lvc') return holder;
      if (selector === '.lvc img' && holder.innerHTML) return {
        removeAttribute: (attr) => { aborted = attr === 'src'; },
      };
      return null;
    },
    replaceChildren() { this.innerHTML = ''; holder.innerHTML = ''; },
  };
  panel._$ = () => live;
  panel._drawLive({ security_events: [{ camera: 'ignored', label: 'person', end: null }] });
  assert.equal(panel._liveCam, 'camera.garden');
  assert.match(holder.innerHTML, /camera_proxy_stream\/garden/);
  panel._clearLive();
  assert.equal(holder.innerHTML, '');
  assert.equal(aborted, true);
  assert.equal(panel._liveCamKey, null);
  assert.equal(panel._liveCard, null);
});

test('an offline gate is unconfirmed, and open doors and triggered alarms remain visible', () => {
  const panel = new Panel();
  const el = {};
  panel._hass = { states: {
    'binary_sensor.gate': { entity_id: 'binary_sensor.gate', state: 'unavailable', attributes: { device_class: 'door', friendly_name: 'Gate' } },
    'binary_sensor.back': { entity_id: 'binary_sensor.back', state: 'on', attributes: { device_class: 'door', friendly_name: 'Back' } },
    'alarm_control_panel.home': { entity_id: 'alarm_control_panel.home', state: 'triggered', attributes: {} },
  } };
  panel._drawSecurityInto(el);
  assert.match(el.innerHTML, /Gate.*STATUS UNKNOWN/);
  assert.match(el.innerHTML, /Back.*OPEN/);
  assert.match(el.innerHTML, /ALARM ACTIVE/);
  assert.doesNotMatch(el.innerHTML, /ALL SHUT/);
});

test('camera pipeline health remains visible on the Security page', () => {
  const panel = new Panel(), el = { querySelector: () => null };
  panel._$ = (id) => id === "frigatehealth" ? el : null;
  panel._drawFrigateHealth({ enabled: true, availability: "online", stats_fresh: true, cameras: [
    { name: "Shed", processing: "reporting", process_fps: 5, ha_report: "idle" },
    { name: "Garden", processing: "zero_rate", ha_report: "idle" },
  ] });
  assert.equal(el.hidden, false);
  assert.match(el.innerHTML, /Shed.*5.0 FPS PROCESSING/);
  assert.match(el.innerHTML, /Garden.*ZERO PROCESSING RATE/);
  assert.match(el.innerHTML, /none received this session/);
});
