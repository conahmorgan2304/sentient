// Behavioral tests for Sentient layout drag-drop without a browser dependency.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("custom_components/sentient/frontend/sentient-panel.js", "utf8");
const begin = source.indexOf("    async _layoutDrop(event) {");
const end = source.indexOf("    async _layoutAction(", begin);
assert.ok(begin >= 0 && end > begin, "layout implementation must be present");
const snippet = source.slice(begin, end);
const { _layoutDrop } = vm.runInNewContext("({" + snippet + "})");

function setup(admin) {
  const lane0 = { children: [] };
  const lane1 = { children: [] };
  function attach(lane, card) {
    const old = card.parentElement;
    if (old) old.children.splice(old.children.indexOf(card), 1);
    lane.children.push(card);
    card.parentElement = lane;
  }
  for (const lane of [lane0, lane1]) lane.appendChild = (card) => attach(lane, card);
  const cardA = { id: "a" };
  const cardB = { id: "b" };
  cardA.before = cardB.before = function (other) {
    const lane = this.parentElement;
    attach(lane, other);
    lane.children.splice(lane.children.indexOf(other), 1);
    lane.children.splice(lane.children.indexOf(this), 0, other);
  };
  attach(lane0, cardA);
  attach(lane1, cardB);
  const root = {
    contains: (target) => target === lane0 || target === lane1,
    querySelectorAll: () => [...lane0.children, ...lane1.children],
    querySelector: () => null,
  };
  let saved;
  let redrawn = false;
  const panel = {
    _dragCard: { page: "overview", id: "a" },
    _layoutEnabled: true,
    _layoutEdit: true,
    _hass: { user: { is_admin: admin }, callWS: async (payload) => {
      saved = payload;
      return { layout: payload.layout };
    } },
    _layoutData: {},
    _layoutRoots: { overview: "overview" },
    _layoutCurrent: () => "overview",
    _$: () => root,
    _layoutRows: () => [lane0, lane1].flatMap((lane, index) =>
      lane.children.map((card) => ({ id: card.id, visible: true, lane: index }))),
    _applyLayouts: () => { redrawn = true; },
  };
  const event = {
    prevented: false,
    target: { closest: (selector) => selector === ".card" ? cardB : lane1 },
    preventDefault() { this.prevented = true; },
  };
  return { panel, event, lane0, lane1, get saved() { return saved; },
           get redrawn() { return redrawn; } };
}

test("administrator drop moves built-in card and saves the complete lane order", async () => {
  const env = setup(true);
  await _layoutDrop.call(env.panel, env.event);
  assert.equal(env.event.prevented, true);
  assert.equal(env.lane1.children.map((c) => c.id).join(","), "a,b");
  assert.equal(env.saved.type, "sentient/layout");
  assert.equal(env.saved.action, "save");
  assert.deepEqual(Array.from(env.saved.layout.overview, (x) => x.id), ["a", "b"]);
  assert.equal(env.redrawn, true);
});

test("nonadministrator drop never changes cards or calls the save API", async () => {
  const env = setup(false);
  await _layoutDrop.call(env.panel, env.event);
  assert.equal(env.lane0.children.map((x) => x.id).join(","), "a");
  assert.equal(env.lane1.children.map((x) => x.id).join(","), "b");
  assert.equal(env.saved, undefined);
  assert.equal(env.event.prevented, false);
});

test("opted-out layout cannot drag even for administrators", async () => {
  const env = setup(true);
  env.panel._layoutEnabled = false;
  await _layoutDrop.call(env.panel, env.event);
  assert.equal(env.saved, undefined);
});
