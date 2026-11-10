const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

test('Security tab puts the Cameras list at the top of the left column, above the live view', () => {
  const src = fs.readFileSync('custom_components/sentient/frontend/sentient-panel.js', 'utf8');
  const lane = src.match(/<section class="lane" aria-label="Live">.*?<\/section>/s)[0];
  const ids = [...lane.matchAll(/id="(\w+)"/g)].map((m) => m[1]);
  assert.deepEqual(ids, ['camc', 'live', 'sec2']);
  assert.equal(src.split('id="camc"').length - 1, 1);
});
