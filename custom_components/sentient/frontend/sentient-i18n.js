/* Sentient's page words in the house's language. Shared by the sidebar page and the cards.

   The pages are written in English; i18n/<language>.json maps each English piece to its translation.
   attach(root, hass) watches a shadow root and swaps every text node and title/placeholder/aria-label that
   has a translation — whatever drew it — and keeps the English, so a language change swaps back.

   How a piece is matched, first hit wins:
     1. as it is                                  "Set-up wizard"
     2. in capitals, from the ordinary key          "SET-UP WIZARD" -> the "Set-up wizard" translation, in capitals
     3. with its numbers taken out as {0}, {1}…     "3 TO LOOK AT" -> "{0} to look at"
     4. a key with {name} slots                     "Warmest {name} {0}°"
     5. split at " · " and each part on its own     "AWAKE · 2 ON"
   Sentient's own sentences (timeline, House health, the Mind tab) come from i18n/house/<language>.json, the
   same templates the integration uses for speech and phones (phrases.py). They're loaded too unless "Sentient's
   own words in the house's language" is off (the Mind sensor's house_language_text attribute), and add:
     6. a known opening and the rest on its own     "Running now: Washing machine"
     7. a known ending and the rest on its own      "A car arrived on the drive — it's clear now"
   English and anything not translated are left exactly as they are. */

const BASE = new URL("./i18n/", import.meta.url).href;
const files = new Map();   // language -> Promise<dict | null>
const ready = new Map();   // language -> dict | null, once loaded
const originals = new WeakMap();   // node -> its English
const ATTRS = ["title", "placeholder", "aria-label"];
const SKIP = new Set(["STYLE", "SCRIPT", "TEXTAREA", "CODE", "PRE"]);  // (inputs have no text; their placeholders are swapped)
const CLOCK = /^(1[0-2]|[1-9])(?::([0-5][0-9]))?([ap])m$/i;
const NUM = /\d+(?:[.,:]\d+)*/g;

const norm = (lang) => String(lang || "en").toLowerCase().replace("_", "-");

export function languageOf(hass) {
  return norm((hass && ((hass.locale && hass.locale.language) || hass.language)) || "en");
}

/** Date and time formatting locale: British English for English, otherwise the house's language. */
export function localeOf(hass) {
  const lang = languageOf(hass);
  return lang === "en" ? "en-GB" : lang;
}

function slotRe(parts, caps, anchor = "whole") {
  const names = [];
  const stateChange = parts.join("") === "{a} {b} at {c}";
  // a slot doesn't reach across " · " (a list of separate pieces) unless the piece itself has one
  const dotted = parts.some((p) => p.includes(" · "));
  const any = dotted ? "." : "(?:(?! · ).)";
  const body = parts.map((part, i) => {
    const m = /^\{([a-z]+|\d+)\}$/.exec(part);
    if (!m) return (caps ? part.toUpperCase() : part).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    names.push(m[1]);
    if (/^\d+$/.test(m[1])) return "(\\d+(?:[.,:]\\d+)*)";
    // The house's state-change summary has a one-word raw state and a clock time.
    if (stateChange) return { a: "(" + any + "+)", b: "(\\S+)", c: "(\\d{2}:\\d{2})" }[m[1]];
    // a slot straight after a letter is a word ending ("thing{b}" for "s"), which may be empty
    return "(" + any + (/[A-Za-z]$/.test(parts[i - 1] || "") ? "*?)" : "+?)");
  }).join("");
  const rx = anchor === "start" ? "^" + body : anchor === "end" ? body + "$" : "^" + body + "$";
  return { re: new RegExp(rx, "s"), names };
}

const ENDING_START = [" ", ",", ";", ".", ":", "!", "?", "—", "("];
const OPENING_END = [" ", ":", "—"];
const literal = (en) => en.replace(/\{[a-z]+\}|\{\d+\}/g, "").length;

/** raw: { English: translation }. house: Sentient's own sentences, which may also be openings and endings. */
function compile(raw, house = null) {
  const exact = new Map(), upper = new Map(), slots = [], openings = [], endings = [];
  const add = (en, tr, fragments) => {
    if (!tr || typeof tr !== "string" || exact.has(en)) return;
    exact.set(en, tr);
    if (!upper.has(en.toUpperCase())) upper.set(en.toUpperCase(), tr);
    const parts = en.split(/(\{[a-z]+\}|\{\d+\})/);
    if (fragments && ENDING_START.includes(en[0])) {
      const r = slotRe(parts, false, "end");
      endings.push({ re: r.re, names: r.names, tr, n: en.length });
    } else if (fragments && OPENING_END.includes(en[en.length - 1])) {
      const r = slotRe(parts, false, "start");
      openings.push({ re: r.re, names: r.names, tr, n: en.length });
    } else if (/\{[a-z]+\}/.test(en)) {
      const plain = slotRe(parts, false);
      slots.push({ re: plain.re, reUp: slotRe(parts, true).re, names: plain.names, tr, n: literal(en) });
    }
  };
  for (const [en, tr] of Object.entries(raw || {})) add(en, tr, false);
  for (const [en, tr] of Object.entries(house || {})) add(en, tr, true);
  slots.sort((a, b) => b.n - a.n);
  openings.sort((a, b) => b.n - a.n);
  endings.sort((a, b) => b.n - a.n);
  return { exact, upper, slots, openings, endings };
}

function load(lang, house = true) {
  if (lang === "en" || lang.startsWith("en-")) return Promise.resolve(null);
  const key = lang + (house ? "+house" : "");
  if (!files.has(key)) {
    const tryFile = (name) => fetch(BASE + name + ".json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null), () => null);
    const either = async (dir) => (await tryFile(dir + lang)) || (lang.includes("-") ? await tryFile(dir + lang.split("-")[0]) : null);
    files.set(key, (async () => {
      const [raw, own] = await Promise.all([either(""), house ? either("house/") : null]);
      const d = raw || own ? compile(raw, own) : null;
      ready.set(key, d);
      return d;
    })());
  }
  return files.get(key);
}

/** Whether Sentient's own sentences are wanted in the house's language: the Mind sensor says (on if it can't be found). */
export function houseText(hass) {
  for (const e of Object.values((hass && hass.entities) || {})) {
    if (e.platform === "sentient" && e.translation_key === "mind") {
      const st = hass.states && hass.states[e.entity_id];
      return !(st && st.attributes && st.attributes.house_language_text === false);
    }
  }
  return true;
}

const isCaps = (s) => /[A-Z]/.test(s) && s === s.toUpperCase();

function whole(text, d) {
  if (d.exact.has(text)) return d.exact.get(text);
  if (isCaps(text) && d.upper.has(text)) return d.upper.get(text).toUpperCase();
  return null;
}

function numbered(text, d) {
  const nums = text.match(NUM);
  if (!nums) return null;
  let i = 0;
  const key = text.replace(NUM, () => `{${i++}}`);
  const tr = whole(key, d);
  return tr == null ? null : tr.replace(/\{(\d+)\}/g, (m, n) => (nums[+n] != null ? nums[+n] : m));
}

function slotValue(value, d) {
  const clock = CLOCK.exec(value);
  if (clock) {
    const hour = Number(clock[1]) % 12 + (clock[3].toLowerCase() === "p" ? 12 : 0);
    return `${String(hour).padStart(2, "0")}:${clock[2] || "00"}`;
  }
  return one(value, d, false) || value;
}

function slotted(text, d) {
  const caps = isCaps(text);
  for (const s of d.slots) {
    const m = (caps ? s.reUp : s.re).exec(text);
    if (!m) continue;
    let out = caps ? s.tr.toUpperCase() : s.tr;
    s.names.forEach((name, k) => {
      const got = m[k + 1];
      const inner = /^\d+$/.test(name) ? got : slotValue(got, d);
      out = out.split(`{${caps ? name.toUpperCase() : name}}`).join(inner).split(`{${name}}`).join(inner);
    });
    return out;
  }
  return null;
}

function filled(tr, names, m, d) {
  let out = tr;
  names.forEach((name, k) => {
    const got = m[k + 1] || "";
    const inner = /^\d+$/.test(name) || !/[A-Za-z]/.test(got) ? got : slotValue(got, d);
    out = out.split(`{${name}}`).join(inner);
  });
  return out;
}

function fragments(text, d, depth) {
  if (depth > 4) return null;
  for (const o of d.openings || []) {
    const m = o.re.exec(text);
    if (m && m[0].length < text.length) {
      const rest = text.slice(m[0].length);
      return filled(o.tr, o.names, m, d) + (one(rest, d, true, depth + 1) ?? rest);
    }
  }
  for (const e of d.endings || []) {
    const m = e.re.exec(text);
    if (m && m.index > 0) {
      const head = text.slice(0, m.index);
      return (one(head, d, true, depth + 1) ?? head) + filled(e.tr, e.names, m, d);
    }
  }
  return null;
}

// a known sentence with a known ending added: the ending stays at the end instead of inside the last slot
function ended(text, d, depth) {
  if (depth > 4) return null;
  for (const e of d.endings || []) {
    const m = e.re.exec(text);
    if (m && m.index > 0) {
      const head = one(text.slice(0, m.index), d, false, depth + 1);
      if (head != null) return head + filled(e.tr, e.names, m, d);
    }
  }
  return null;
}

function one(text, d, split = true, depth = 0) {
  const hit = whole(text, d) ?? numbered(text, d) ?? ended(text, d, depth) ?? slotted(text, d) ?? fragments(text, d, depth);
  if (hit != null || !split || !text.includes(" · ")) return hit;
  let changed = false;
  const parts = text.split(" · ").map((p) => { const t = one(p.trim(), d, false); if (t != null) { changed = true; return t; } return p; });
  return changed ? parts.join(" · ") : null;
}

/** One piece of English in the language of `dict` (from load); unchanged when there's no translation. */
export function translate(text, dict) {
  if (!dict || text == null) return text;
  const s = String(text), core = s.trim();
  if (!core || !/[A-Za-z]/.test(core)) return s;
  const tr = one(core, dict);
  return tr == null ? s : s.replace(core, tr);
}

function swapText(node, dict) {
  const was = originals.get(node);
  const now = node.nodeValue;
  // the page wrote new English since the last swap: that's the new original
  const english = was && was.shown === now ? was.en : now;
  const out = translate(english, dict);
  if (out !== now) node.nodeValue = out;
  originals.set(node, { en: english, shown: out });
}

function swapAttrs(el, dict) {
  for (const a of ATTRS) {
    if (!el.hasAttribute(a)) continue;
    const key = "__i18n_" + a, now = el.getAttribute(a), was = el[key];
    const english = was && was.shown === now ? was.en : now;
    const out = translate(english, dict);
    if (out !== now) el.setAttribute(a, out);
    el[key] = { en: english, shown: out };
  }
}

function walk(root, dict) {
  const tw = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.nodeType === 1 && (SKIP.has(n.tagName) || n.hasAttribute("data-no-i18n")) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  if (root.nodeType === 3) { swapText(root, dict); return; }
  if (root.nodeType === 1) swapAttrs(root, dict);
  for (let n = tw.nextNode(); n; n = tw.nextNode()) {
    if (n.nodeType === 3) swapText(n, dict);
    else swapAttrs(n, dict);
  }
}

/** Keep `root` (a shadow root) in the house's language from now on. Returns a function to call when hass changes. */
export function attach(root, hass) {
  let lang = null, dict = null, busy = false;
  const observer = new MutationObserver((records) => {
    if (!dict || busy) return;
    busy = true;
    try {
      for (const r of records) {
        if (r.type === "characterData") walk(r.target, dict);
        else if (r.type === "attributes") swapAttrs(r.target, dict);
        else for (const n of r.addedNodes) if (n.nodeType === 3 || n.nodeType === 1) {
          const el = n.nodeType === 1 ? n : n.parentElement;
          if (!(el && (SKIP.has(el.tagName) || el.closest("[data-no-i18n]")))) walk(n, dict);
        }
      }
    } finally {
      observer.takeRecords();
      busy = false;
    }
  });
  const update = async (h) => {
    const want = languageOf(h) + (houseText(h) ? "+house" : "");
    if (want === lang) return;
    lang = want;
    const d = await load(languageOf(h), houseText(h));
    if (lang !== want) return;
    dict = d || { exact: new Map(), upper: new Map(), slots: [], openings: [], endings: [] };
    busy = true;
    try { walk(root, dict); } finally { observer.takeRecords(); busy = false; }
    if (!d) dict = null;
  };
  observer.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  update(hass);
  return update;
}

/** Whether `text` has a translation in `dict` (it may read the same as the English). */
export function covered(text, dict) {
  const core = String(text == null ? "" : text).trim();
  return !!dict && !!core && one(core, dict) != null;
}

/** Start loading the house's language; resolves once t() can use it. */
export function prepare(hass) {
  return load(languageOf(hass), houseText(hass));
}

/** For text that never reaches the page (confirm and alert boxes): English until the language has loaded. */
export function t(hass, english) {
  return translate(english, ready.get(languageOf(hass) + (houseText(hass) ? "+house" : "")) || null);
}
export { compile };  // for the tests
