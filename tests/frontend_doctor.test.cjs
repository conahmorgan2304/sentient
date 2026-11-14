const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let Panel;
vm.runInNewContext(fs.readFileSync('custom_components/sentient/frontend/sentient-panel.js', 'utf8'), {
  HTMLElement: class {}, customElements: { get: () => null, define: (_, cls) => { Panel = cls; } },
  setTimeout, clearTimeout,
});

test('the setup doctor puts problems first and groups the rest, saying a shared detail once', async () => {
  const panel = new Panel(), body = { innerHTML: '' };
  panel._$ = (id) => id === 'body' ? body : null;
  panel._drawSide = () => {};
  panel._group = 'doctor';
  panel._doc = { status: 'bad', rows: [
    { area: 'Speakers', name: 'kitchen Assist satellite', status: 'ok', detail: 'Ready.', fix: '' },
    { area: 'Speakers', name: 'landing echo', status: 'ok', detail: 'Ready.', fix: '' },
    { area: 'Speakers', name: 'Reading voice', status: 'ok', detail: 'Some voice', fix: '' },
    { area: 'Cameras', name: 'Gate', status: 'bad', detail: 'Unavailable.', fix: 'Check its power.' },
  ] };
  await panel._drawDoctor();
  const html = body.innerHTML;
  assert.match(html, /1 to look at.*3 of 4 checks passed/s);
  assert.ok(html.indexOf('Check its power.') < html.indexOf('class="t">SPEAKERS'), 'problems come first');
  assert.match(html, /<div class="docshared">Ready\.<\/div>/);
  assert.match(html, /<b>Kitchen<\/b><\/span>/);  // "Assist satellite" dropped, detail said once above
  assert.match(html, /<b>Reading voice<\/b><i>Some voice<\/i>/);
  assert.match(html, /<small>Cameras<\/small>/);  // an area with only a problem is named on the problem card
  panel._doc = { status: 'ok', rows: [{ area: 'AI', name: 'Agent', status: 'ok', detail: 'Fine.', fix: '' }] };
  await panel._drawDoctor();
  assert.match(body.innerHTML, /Everything checks out.*All 1 checks passed/s);
});
