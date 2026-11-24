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
   English and anything not translated are left exactly as they are. */

const BASE = new URL("./i18n/", import.meta.url).href;
const files = new Map();   // language -> Promise<dict | null>
const ready = new Map();   // language -> dict | null, once loaded
const originals = new WeakMap();   // node -> its English
const ATTRS = ["title", "placeholder", "aria-label"];
const SKIP = new Set(["STYLE", "SCRIPT", "TEXTAREA", "CODE", "PRE"]);  // (inputs have no text; their placeholders are swapped)
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

function slotRe(parts, caps) {
  const names = [];
  const body = parts.map((part) => {
    const m = /^\{([a-z]+|\d+)\}$/.exec(part);
    if (!m) return (caps ? part.toUpperCase() : part).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    names.push(m[1]);
    return /^\d+$/.test(m[1]) ? "(\\d+(?:[.,:]\\d+)*)" : "(.+?)";
  }).join("");
  return { re: new RegExp("^" + body + "$"), names };
}

function compile(raw) {
  const exact = new Map(), upper = new Map(), slots = [];
  for (const [en, tr] of Object.entries(raw || {})) {
    if (!tr || typeof tr !== "string") continue;
    exact.set(en, tr);
    if (!upper.has(en.toUpperCase())) upper.set(en.toUpperCase(), tr);
    if (/\{[a-z]+\}/.test(en)) {
      const parts = en.split(/(\{[a-z]+\}|\{\d+\})/);
      const plain = slotRe(parts, false);
      slots.push({ re: plain.re, reUp: slotRe(parts, true).re, names: plain.names, tr });
    }
  }
  return { exact, upper, slots };
}

function load(lang) {
  if (lang === "en" || lang.startsWith("en-")) return Promise.resolve(null);
  if (!files.has(lang)) {
    const tryFile = (name) => fetch(BASE + name + ".json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null), () => null);
    files.set(lang, (async () => {
      let raw = await tryFile(lang);
      if (!raw && lang.includes("-")) raw = await tryFile(lang.split("-")[0]);
      const d = raw ? compile(raw) : null;
      ready.set(lang, d);
      return d;
    })());
  }
  return files.get(lang);
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

function slotted(text, d) {
  const caps = isCaps(text);
  for (const s of d.slots) {
    const m = (caps ? s.reUp : s.re).exec(text);
    if (!m) continue;
    let out = caps ? s.tr.toUpperCase() : s.tr;
    s.names.forEach((name, k) => {
      const got = m[k + 1];
      const inner = /^\d+$/.test(name) ? got : (one(got, d, false) || got);
      out = out.split(`{${caps ? name.toUpperCase() : name}}`).join(inner).split(`{${name}}`).join(inner);
    });
    return out;
  }
  return null;
}

function one(text, d, split = true) {
  const hit = whole(text, d) ?? numbered(text, d) ?? slotted(text, d);
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
          const p = n.parentElement;
          if (!(p && (SKIP.has(p.tagName) || p.closest("[data-no-i18n]")))) walk(n, dict);
        }
      }
    } finally {
      observer.takeRecords();
      busy = false;
    }
  });
  const update = async (h) => {
    const want = languageOf(h);
    if (want === lang) return;
    lang = want;
    const d = await load(want);
    if (lang !== want) return;
    dict = d || { exact: new Map(), upper: new Map(), slots: [] };
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
  return load(languageOf(hass));
}

/** For text that never reaches the page (confirm and alert boxes): English until the language has loaded. */
export function t(hass, english) {
  return translate(english, ready.get(languageOf(hass)) || null);
}
export { compile };  // for the tests
