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
