"""Recipes and a hands-free cooking mode that remembers which step you're on.

Recipes come from any of:
* a folder of Home Assistant's media (recipes by default) — PDFs, photos of pages, text or Markdown;
  each is turned into tidy ingredients and steps by one AI call the first time it's opened, then kept;
* Mealie, through Home Assistant's Mealie integration (no AI);
* Tandoor, with its address and an API token (no AI).

"Let's make the lasagne" reads the ingredients, then one step at a time: "next", "repeat that",
"go back", "what's after this?". The place is kept per speaker, so you can wander off and come back.
"Make it for 6" scales the amounts; the ingredients can go on the shopping list. A step that says
"simmer for 20 minutes" comes with the minutes, so the voice can offer the speaker's own timer.
Each step is the voice's reply, so it works on any speaker, Echos through the Alexa skill included.
"""

from __future__ import annotations

from fractions import Fraction
import os
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_RECIPES, CONF_RECIPES_FOLDER, CONF_RECIPES_LIST, CONF_TANDOOR_TOKEN, CONF_TANDOOR_URL, DEFAULT_RECIPES_FOLDER, DOMAIN,
)

STORAGE_VERSION = 1
FILES = {".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/plain", ".jpg": "image/jpeg",
         ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
_WORD = re.compile(r"[a-z0-9]+")
_TIME = re.compile(r"(\d+)(?:\s*(?:-|to)\s*(\d+))?\s*(minutes?|mins?|hours?|hrs?)\b", re.I)
_QTY = re.compile(r"^\s*(\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)")
_SKIP = {"the", "a", "an", "and", "with", "recipe", "make", "lets", "let", "cook", "my", "some", "for"}


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 1 and w not in _SKIP}


def scale(line: str, factor: float) -> str:
    """'200g flour' x1.5 -> '300g flour'; leaves lines without a leading amount alone."""
    if factor == 1:
        return line
    m = _QTY.match(line)
    if not m:
        return line
    raw = m.group(1).strip()
    try:
        value = sum(Fraction(p) for p in raw.split()) * Fraction(factor).limit_denominator(8)
    except (ValueError, ZeroDivisionError):
        return line
    whole, rest = divmod(value.limit_denominator(4), 1)
    text = (f"{whole} " if whole else "") + (f"{rest.numerator}/{rest.denominator}" if rest else "")
    return text.strip() + line[m.end():] if text.strip() else line


def timer_minutes(step: str) -> int | None:
    m = _TIME.search(step)
    if not m:
        return None
    n = int(m.group(2) or m.group(1))
    return n * 60 if m.group(3).lower().startswith("h") else n


class Recipes:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.recipes")
        self.parsed: dict[str, dict[str, Any]] = {}  # file+mtime -> structured recipe
        self.cooking: dict[str, dict[str, Any]] = {}  # speaker -> {recipe, step, factor}

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_RECIPES))

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.parsed, self.cooking = data.get("parsed", {}), data.get("cooking", {})

    def async_stop(self) -> None:
        return None

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"parsed": dict(list(self.parsed.items())[-60:]), "cooking": self.cooking}, 5)

    # ----- sources -------------------------------------------------------------------------
    def _folder(self) -> tuple[str, str]:
        sub = str(self.options.get(CONF_RECIPES_FOLDER) or DEFAULT_RECIPES_FOLDER).strip().strip("/")
        base = (self.hass.config.media_dirs or {}).get("local") or self.hass.config.path("media")
        return os.path.join(base, sub), f"media-source://media_source/local/{sub}"

    def _files(self) -> list[tuple[str, float]]:
        path, _ = self._folder()
        if not os.path.isdir(path):
            return []
        return sorted((f, os.path.getmtime(os.path.join(path, f))) for f in os.listdir(path)
                      if os.path.splitext(f)[1].lower() in FILES)

    def _mealie_entry(self):
        entries = [e for e in self.hass.config_entries.async_entries("mealie") if e.state.value == "loaded"]
        return entries[0] if entries else None

    async def async_search(self, query: str) -> list[dict[str, str]]:
        """[{name, source, ref}] best first."""
        want = _words(query)
        found: list[tuple[int, dict[str, str]]] = []
        for f, _m in await self.hass.async_add_executor_job(self._files):
            name = os.path.splitext(f)[0]
            found.append((len(want & _words(name)), {"name": name, "source": "folder", "ref": f}))
        if (entry := self._mealie_entry()) is not None and self.hass.services.has_service("mealie", "get_recipes"):
            try:
                resp = await self.hass.services.async_call("mealie", "get_recipes", {
                    "config_entry_id": entry.entry_id, "search_terms": query, "result_limit": 10},
                    blocking=True, return_response=True)
                for r in (resp or {}).get("recipes", {}).get("items", []) if isinstance((resp or {}).get("recipes"), dict) else (resp or {}).get("recipes", []):
                    found.append((len(want & _words(r.get("name", ""))) + 1, {"name": r.get("name", ""), "source": "mealie", "ref": r.get("slug") or r.get("recipe_id") or ""}))
            except Exception:  # noqa: BLE001 - one source failing mustn't stop the others
                pass
        if self.options.get(CONF_TANDOOR_URL) and self.options.get(CONF_TANDOOR_TOKEN):
            try:
                data = await self._tandoor(f"/api/recipe/?query={query}&page_size=10")
                for r in data.get("results", []):
                    found.append((len(want & _words(r.get("name", ""))) + 1, {"name": r.get("name", ""), "source": "tandoor", "ref": str(r.get("id"))}))
            except Exception:  # noqa: BLE001
                pass
        found.sort(key=lambda x: -x[0])
        if want:
            found = [f for f in found if f[0] > 0] or found
        return [f for _, f in found][:10]

    async def _tandoor(self, path: str) -> dict[str, Any]:
        base = str(self.options.get(CONF_TANDOOR_URL)).rstrip("/")
        async with async_get_clientsession(self.hass).get(
            base + path, headers={"Authorization": f"Bearer {self.options.get(CONF_TANDOOR_TOKEN)}"}, timeout=15) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def async_load_recipe(self, item: dict[str, str]) -> dict[str, Any] | None:
        """{name, servings, ingredients: [str], steps: [str]}"""
        if item["source"] == "tandoor":
            r = await self._tandoor(f"/api/recipe/{item['ref']}/")
            ingredients, steps = [], []
            for st in r.get("steps", []):
                for ing in st.get("ingredients", []):
                    amount = ing.get("amount") or ""
                    unit = (ing.get("unit") or {}).get("name") or ""
                    food = (ing.get("food") or {}).get("name") or ""
                    line = " ".join(str(x) for x in (amount if amount else "", unit, food) if x)
                    if ing.get("note"):
                        line += f" ({ing['note']})"
                    if line.strip():
                        ingredients.append(line.strip())
                if st.get("instruction"):
                    steps += [s.strip() for s in re.split(r"\n\s*\n|\n(?=\d+\.)", st["instruction"]) if s.strip()]
            return {"name": r.get("name") or item["name"], "servings": r.get("servings"), "ingredients": ingredients, "steps": steps}
        if item["source"] == "mealie":
            entry = self._mealie_entry()
            if entry is None:
                return None
            resp = await self.hass.services.async_call("mealie", "get_recipe", {
                "config_entry_id": entry.entry_id, "recipe_id": item["ref"]}, blocking=True, return_response=True)
            r = (resp or {}).get("recipe") or {}
            ingredients = [i.get("original_text") or i.get("display") or " ".join(
                str(x) for x in (i.get("quantity") or "", (i.get("unit") or {}).get("name") if isinstance(i.get("unit"), dict) else i.get("unit") or "",
                                 (i.get("food") or {}).get("name") if isinstance(i.get("food"), dict) else i.get("food") or "", i.get("note") or "") if x)
                           for i in r.get("recipe_ingredient") or r.get("ingredients") or []]
            steps = [s.get("text", "").strip() for s in r.get("recipe_instructions") or r.get("instructions") or [] if s.get("text")]
            return {"name": r.get("name") or item["name"], "servings": r.get("recipe_yield") or r.get("servings"),
                    "ingredients": [i for i in ingredients if i], "steps": steps}
        # a file: tidied by the AI once, then kept
        path, prefix = self._folder()
        files = dict(await self.hass.async_add_executor_job(self._files))
        if item["ref"] not in files:
            return None
        key = f"{item['ref']}|{files[item['ref']]}"
        if key in self.parsed:
            return self.parsed[key]
        rt = self._rt()
        rt.ai.spend("recipe", force=True)
        from .ai import AIUnavailable
        import voluptuous as vol
        from homeassistant.helpers import selector

        try:
            resp = await rt.ai.async_call({
                "task_name": "sentient recipe",
                "instructions": ("Read this recipe. Give its name, how many it serves (a number, or empty), every ingredient "
                                 "as one line with its amount, and the method as short separate steps in order, each one "
                                 "thing to do. Keep times and temperatures. British English. Don't add anything that isn't there."),
                "structure": vol.Schema({
                    vol.Required("name"): selector.TextSelector(),
                    vol.Optional("servings"): selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=50)),
                    vol.Required("ingredients"): selector.TextSelector(selector.TextSelectorConfig(multiple=True)),
                    vol.Required("steps"): selector.TextSelector(selector.TextSelectorConfig(multiple=True)),
                }),
                "attachments": [{"media_content_id": f"{prefix}/{item['ref']}", "media_content_type": FILES[os.path.splitext(item['ref'])[1].lower()]}],
            })
        except AIUnavailable:
            return None
        data = (resp or {}).get("data") or {}
        recipe = {"name": data.get("name") or item["name"], "servings": data.get("servings"),
                  "ingredients": list(data.get("ingredients") or []), "steps": list(data.get("steps") or [])}
        if recipe["steps"]:
            self.parsed[key] = recipe
            self._save()
        return recipe

    # ----- cooking ----------------------------------------------------------------------------
    def _step(self, speaker: str) -> dict[str, Any]:
        c = self.cooking[speaker]
        steps = c["recipe"]["steps"]
        n = c["step"]
        text = steps[n]
        out = {"recipe": c["recipe"]["name"], "step": n + 1, "of": len(steps), "say": text}
        if (mins := timer_minutes(text)) is not None:
            out["timer_minutes"] = mins
            out["note"] = f"Offer to set a {mins}-minute timer on this speaker (its own timer)."
        if n + 1 == len(steps):
            out["last_step"] = True
        return out

    async def async_cook(self, query: str, speaker: str, servings: float | None = None) -> dict[str, Any]:
        matches = await self.async_search(query)
        if not matches:
            return {"error": f"I couldn't find a recipe for {query}"}
        best = matches[0]
        if len(matches) > 1 and not (_words(query) <= _words(best["name"])) and (_words(query) & _words(matches[1]["name"])):
            return {"which": [m["name"] for m in matches[:4]], "note": "Ask which one they mean."}
        recipe = await self.async_load_recipe(best)
        if not recipe or not recipe["steps"]:
            return {"error": f"I couldn't read the {best['name']} recipe"}
        base = _num(recipe.get("servings"))
        factor = (servings / base) if servings and base else 1.0
        self.cooking[speaker] = {"recipe": recipe, "step": -1, "factor": factor, "when": dt_util.utcnow().isoformat()}
        self._save()
        return {"recipe": recipe["name"], "serves": servings or base, "steps": len(recipe["steps"]),
                "ingredients": [scale(i, factor) for i in recipe["ingredients"]],
                "note": "Read the ingredients briefly, then ask if they're ready for the first step."}

    def move(self, speaker: str, how: str, number: int | None = None) -> dict[str, Any]:
        c = self.cooking.get(speaker)
        if not c:
            return {"error": "nothing is being cooked on this speaker — say which recipe"}
        last = len(c["recipe"]["steps"]) - 1
        if how == "next":
            if c["step"] >= last:
                return {"recipe": c["recipe"]["name"], "finished": True, "say": "That was the last step. Enjoy!"}
            c["step"] += 1
        elif how == "previous":
            c["step"] = max(0, c["step"] - 1)
        elif how == "goto" and number:
            c["step"] = max(0, min(last, number - 1))
        elif how == "repeat":
            c["step"] = max(0, c["step"])
        elif how == "peek":
            if c["step"] >= last:
                return {"say": "There's nothing after this — it's the last step."}
            return {"next_step": c["recipe"]["steps"][c["step"] + 1], "note": "Say what's coming next, without moving on."}
        c["when"] = dt_util.utcnow().isoformat()
        self._save()
        return self._step(speaker)

    async def async_shopping(self, speaker: str) -> dict[str, Any]:
        c = self.cooking.get(speaker)
        if not c:
            return {"error": "which recipe?"}
        todo = self.options.get(CONF_RECIPES_LIST) or next(
            (s.entity_id for s in self.hass.states.async_all("todo") if "shopping" in s.entity_id), None)
        if not todo:
            return {"error": "no shopping list to add to"}
        items = [scale(i, c["factor"]) for i in c["recipe"]["ingredients"]]
        for item in items:
            await self.hass.services.async_call("todo", "add_item", {"entity_id": todo, "item": item}, blocking=True)
        st = self.hass.states.get(todo)
        return {"added": len(items), "to": st.name if st else todo}

    def stop(self, speaker: str) -> dict[str, Any]:
        c = self.cooking.pop(speaker, None)
        self._save()
        return {"stopped": c["recipe"]["name"]} if c else {"error": "nothing was being cooked"}

    def listing(self) -> list[dict[str, Any]]:
        out = []
        for speaker, c in self.cooking.items():
            st = self.hass.states.get(speaker)
            out.append({"recipe": c["recipe"]["name"], "speaker": st.name if st else speaker,
                        "step": c["step"] + 1, "of": len(c["recipe"]["steps"]), "when": c.get("when")})
        return out


def _num(v: Any) -> float | None:
    try:
        return float(str(v).split()[0])
    except (TypeError, ValueError, IndexError):
        return None
