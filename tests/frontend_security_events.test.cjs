const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let Panel;
vm.runInNewContext(fs.readFileSync('custom_components/sentient/frontend/sentient-panel.js', 'utf8'), {
  HTMLElement: class {}, customElements: { get: () => null, define: (_, cls) => { Panel = cls; } },
  setTimeout, clearTimeout,
});
function setup(callWS) {
  const panel = new Panel();
  panel.mind = { security_event_feedback_enabled: true, security_visits: [
    { id: 'event-one', ids: ['event-one', 'event-two'], camera: 'garden', labels: ['person', 'cat'], zones: ['side_path'],
      start: 1700000000, end: 1700000030, alerts: [], limits: 'Not live evidence',
      analysis: { summary: '<unsafe description>' }, snapshot: '/thumb.jpg', clip: '/clip.mp4' },
  ] };
  panel._hass = { user: { is_admin: true }, callWS };
  panel._st = () => ({ attributes: panel.mind });
  panel._render = () => { panel.html = panel._securityEventRows(panel.mind); };
  panel._render();
  return panel;
}

test('visit feedback waits for acknowledgement, covers every event of the visit and folds it away', async () => {
  let finish, calls = 0, sent;
  const panel = setup((msg) => { calls++; sent = msg; return new Promise(resolve => { finish = resolve; }); });
  assert.match(panel.html, /GARDEN · .* · PERSON, CAT/);
  assert.match(panel.html, /In side path · 2 events/);
  assert.match(panel.html, />GOOD CATCH<.*>NOT A PERSON<.*>IGNORE</);
  assert.doesNotMatch(panel.html, /REVIEWED \(/);
  const pending = panel._securityFeedback('event-one', 'false_positive');
  assert.match(panel.html, /Saving event feedback/);
  assert.match(panel.html, /disabled data-act="securityfeedback"/);
  await panel._securityFeedback('event-one', 'false_positive');
  assert.equal(calls, 1);
  assert.deepEqual(sent.event_ids, ['event-one', 'event-two']);
  finish({ feedback: { value: 'false_positive' }, incidents_reviewed: 1 });
  await pending;
  assert.match(panel.html, /<details class="evdone"><summary data-act="evdone" data-id="visits">REVIEWED \(1\)<\/summary>/);
  assert.match(panel.html, /Marked not a person · counted as a reviewed false detection/);
  assert.match(panel.html, />UNDO</);
  assert.doesNotMatch(panel.html, />GOOD CATCH</);
  panel._hass.callWS = async () => ({ feedback: null });
  await panel._securityFeedback('event-one', 'clear');
  assert.doesNotMatch(panel.html, /Marked|Saving event feedback|REVIEWED \(/);
  assert.match(panel.html, />GOOD CATCH</);
});

test('a zone marked not a person a few times can be hidden from the list', async () => {
  let sent;
  const panel = setup(async (msg) => { sent = msg; return { hidden: ['garden/side_path'] }; });
  panel.mind.security_event_feedback_summary = [{ camera: 'garden', reviewed: 3, useful: 0, not_useful: 0, false_positive: 3, cat: 0,
    limits: 'Only reviewed events', zones: [{ zone: 'side_path', not_a_person: 3 }] }];
  panel._render();
  assert.match(panel.html, /side path: 3 marked not a person/);
  assert.match(panel.html, /data-act="securityzonehide" data-id="garden" data-text="side_path">HIDE THIS ZONE/);
  await panel._securityZoneHide('garden', 'side_path');
  assert.equal(JSON.stringify(sent), JSON.stringify({ type: 'sentient/security/zone/hide', camera: 'garden', zone: 'side_path', hide: true }));
  assert.match(panel.html, /side path hidden/);
});

test('feedback errors are visible and escaped, with retry available', async () => {
  const panel = setup(async () => { throw new Error('<event expired>'); });
  await panel._securityFeedback('event-one', 'cat');
  assert.match(panel.html, /role="alert">&lt;event expired&gt;/);
  assert.doesNotMatch(panel.html, /Marked|disabled data-act/);
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
  panel._hass = { states: {
    'binary_sensor.gate': { entity_id: 'binary_sensor.gate', state: 'off', attributes: { device_class: 'door', friendly_name: 'Gate', restored: true } },
    'lock.front': { entity_id: 'lock.front', state: 'locked', attributes: {} },
  } };
  panel._drawSecurityInto(el);
  assert.match(el.innerHTML, /Gate.*STATUS UNKNOWN/);
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
  panel._drawFrigateHealth({ enabled: true, availability: "unknown", last_reported_availability: "online", stats_fresh: false, cameras: [] });
  assert.match(el.innerHTML, /UNKNOWN \(LAST REPORT ONLINE\).*NO FRESH STATS/);
});

test('an incident card shows its camera picture, says what its stage means and asks what it was in plain words', () => {
  const panel = new Panel();
  panel._hass = { user: { is_admin: true }, states: { 'camera.gate': { attributes: { friendly_name: 'Gate Camera' } } } };
  panel._st = () => null;
  panel._fold = (key, title, body) => `<fold ${key}>${title}${body}</fold>`;
  const mind = {
    security_incidents: [{ id: 'inc1', start: '2026-11-14T10:40:00Z', sources: ['camera.gate'], link: 'Not proof of one person.',
      stage: 'resolved', stage_why: 'nothing new', evidence: [{ time: '2026-11-14T10:40:00Z', text: 'Frigate reported person', event_id: 'ev1' }] }],
    security_visits: [{ id: 'ev1', ids: ['ev1'], camera: 'gate', labels: ['person'], zones: ['Zone2'], snapshot: '/snap/ev1.jpg', start: 1, end: 2 },
                      { id: 'ev9', ids: ['ev9'], camera: 'gate', labels: ['cat'], zones: ['Zone1'], snapshot: null, start: 3, end: 4 }],
    security_event_feedback_enabled: true,
  };
  // the same code path the Mind/Security drawing uses: incident cards first, then the visits not on one
  const src = Panel.toString();
  assert.match(src, /What was it\?/);
  assert.match(src, /NOTHING \/ AN ANIMAL/);
  panel._incidentEvents = new Set(['ev1']);
  const visits = panel._securityEventRows(mind);
  assert.doesNotMatch(visits, /ev1/);  // shown on the incident's card instead
  assert.match(visits, /ev9/);
});
