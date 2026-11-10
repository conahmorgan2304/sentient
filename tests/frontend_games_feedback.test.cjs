const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const src = fs.readFileSync('custom_components/sentient/frontend/sentient-panel.js', 'utf8');

test('the Games page reports success and errors on the page, not in a pop-up', () => {
  const body = src.match(/async _gameDo\(msg\) \{.*?\n    \}\n/s)[0];
  assert.ok(!body.includes('window.alert'), 'a hidden pop-up must not be the only feedback');
  assert.ok(body.includes('GAME CREATED'));
  assert.ok(body.includes('No answer from Home Assistant'));
  assert.ok(src.includes('id="gmsg"'));
});
