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
  panel.sensor = { id: 'run-one', status: 'passed_agent_checks', steps: [{}, {}], heard_replies: null };
  panel._hass = { user: { is_admin: true }, callWS };
  panel._st = () => ({ attributes: { voice_check: panel.sensor } });
  panel._render = () => { panel.html = panel._voiceCheckRow(panel.sensor); };
  panel._render();
  return panel;
}

test('YES acknowledges immediately without waiting for a sensor update and prevents duplicate clicks', async () => {
  let finish, calls = 0;
  const panel = setup((msg) => {
    calls++;
    assert.equal(msg.action, 'heard');
    assert.equal(msg.run_id, 'run-one');
    return new Promise(resolve => { finish = resolve; });
  });
  const pending = panel._voiceCheckAction('heard', 'run-one');
  assert.match(panel.html, /Saving voice check/);
  assert.match(panel.html, /disabled data-act="voicecheck"/);
  await panel._voiceCheckAction('not_heard', 'run-one');
  assert.equal(calls, 1);
  finish({ ...panel.sensor, heard_replies: true });
  await pending;
  assert.match(panel.html, /SAVED · You confirmed/);
  assert.doesNotMatch(panel.html, /Did you hear the replies/);
  assert.doesNotMatch(panel.html, /Saving voice check/);
  panel.sensor = { ...panel.sensor, id: 'new-run', steps: [] };
  panel._render();
  assert.doesNotMatch(panel.html, /SAVED ·/);
});

test('NO is saved explicitly and clear uses the returned null immediately', async () => {
  const panel = setup(async msg => msg.action === 'clear' ? null : { ...panel.sensor, heard_replies: false });
  await panel._voiceCheckAction('not_heard', 'run-one');
  assert.match(panel.html, /SAVED · You reported that replies were not heard/);
  assert.doesNotMatch(panel.html, /Did you hear the replies/);
  await panel._voiceCheckAction('clear');
  assert.doesNotMatch(panel.html, /SAVED ·|passed agent checks/);
});

test('rejection is visible in the live check and retry remains possible', async () => {
  const panel = setup(async () => { throw new Error('Check expired <retry>'); });
  await panel._voiceCheckAction('heard', 'run-one');
  assert.match(panel.html, /role="alert">Check expired &lt;retry&gt;/);
  assert.match(panel.html, /Did you hear the replies/);
  assert.doesNotMatch(panel.html, /SAVED ·|disabled data-act/);
  panel._hass.callWS = async () => ({ ...panel.sensor, heard_replies: true });
  await panel._voiceCheckAction('heard', 'run-one');
  assert.match(panel.html, /SAVED · You confirmed/);
  assert.doesNotMatch(panel.html, /role="alert"/);
});

test('update checker UI is optional, escapes errors and prevents repeated requests', async () => {
  let finish, calls = 0;
  const panel = setup(() => { calls++; return new Promise(resolve => { finish = resolve; }); });
  const info = { enabled: true, installed_version: '0.67.0', latest_version: null };
  assert.equal(panel._updateRow({ enabled: false }), '');
  panel._render = () => { panel.html = panel._updateRow(info); };
  const pending = panel._checkUpdates();
  assert.match(panel.html, /CHECKING…/);
  await panel._checkUpdates();
  assert.equal(calls, 1);
  finish({ error: 'Offline <retry>' });
  await pending;
  assert.match(panel.html, /Check failed: Offline &lt;retry&gt;/);
  panel._hass.user.is_admin = false;
  assert.equal(panel._updateRow(info), '');
});

test('a successful update check also asks HACS to look again', async () => {
  const sent = [];
  const panel = setup(async (msg) => {
    sent.push(msg);
    if (msg.type === 'sentient/update_check') return { latest_version: '9.0.0', update_available: true };
    if (msg.type === 'hacs/repositories/list') return [{ id: 42, full_name: 'conahmorgan2304/sentient' }];
    return null;
  });
  panel._render = () => {};
  await panel._checkUpdates();
  assert.deepEqual(sent.map(m => m.type), ['sentient/update_check', 'hacs/repositories/list', 'hacs/repository/refresh']);
  assert.equal(sent[2].repository, '42');
  assert.match(panel._updatesFeedback, /HACS has been told to look again/);
});

test('single-turn check exposes playback confirmation and separate check modes', async () => {
  const panel = setup(async () => ({ ...panel.sensor, heard_replies: true }));
  panel.sensor = { ...panel.sensor, mode: 'single', steps: [{}] };
  panel._render();
  assert.match(panel.html, /Did you hear the replies/);
  for (const mode of ['start_single', 'start_question', 'start_cancel']) assert.ok(panel.html.includes(`data-id="${mode}"`));
  await panel._voiceCheckAction('heard', 'run-one');
  assert.match(panel.html, /SAVED · You confirmed/);
});
