"""Shopping and to-do lists by voice, done properly: tick off, put back, take off, add, read out.

Issue #206: "mark milk as purchased on the shopping list" was answered "Done" with nothing ticked. Home
Assistant's own list intent only finds an item spelt exactly as on the list ("milk" isn't "Milk"), and the voice
was free to say it had worked anyway. Here:

* items are matched the way people say them — any case, "the"/"some" ignored, milk/milks, "semi-skimmed milk"
  found from "milk" when it's the only milk — and two possible matches are asked about rather than guessed;
* every change is read back from the list afterwards, and only what's really changed comes back as ``done``;
* the list is the one named, else the shopping list, else the only list Assist can see;
* the house's own notes about lists (from memory) come back with the answer, so they're followed.

Only lists exposed to Assist. Settings › Voice › Shopping and to-do lists.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import re
from typing import Any
import unicodedata

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

CONF_VOICE_LISTS = "voice_lists"
FILLER = {"the", "a", "an", "some", "of", "my", "our", "more", "any"}
LIST_WORDS = re.compile(r"shopping list|to[- ]do list|list[ayę] zakup(?:ów|owa)|liście zakupów|"
                        r"lista zadań|liście zadań|liste (?:de courses|des tâches)|einkaufsliste", re.I)
SHOW = re.compile(r"(?:what(?:'s| is) on (?:my |the |our )?(?:shopping|to[- ]do) list|"
                  r"co (?:jest |mam )?na (?:mojej )?liście (?:zakupów|zadań)|"
                  r"pokaż (?:mi )?listę (?:zakupów|zadań)|"
                  r"qu['’]est-ce qu['’]il y a sur (?:ma |la )?liste de courses)[? .]*", re.I)


def _words(text: str) -> list[str]:
    words = re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", str(text)).casefold())
    out = []
    for w in words:
        if w in FILLER:
            continue
        if len(w) > 3 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("es") and w[-3] in "sxz":
            w = w[:-2]
        elif len(w) > 2 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def _key(text: str) -> str:
    return " ".join(_words(text))


def match(said: str, items: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The item meant by `said`: (item, []) when sure, (None, choices) when not, (None, []) when not there."""
    want = _key(said)
    if not want:
        return None, []
    exact = [i for i in items if _key(i.get("summary", "")) == want]
    if len(exact) == 1:
        return exact[0], []
    if exact:
        return None, exact
    wanted = set(want.split())
    close = [i for i in items if wanted <= set(_key(i.get("summary", "")).split())]
    if not close:
        close = [i for i in items if want in _key(i.get("summary", "")) or (_key(i.get("summary", "")) and _key(i.get("summary", "")) in want)]
    if len(close) == 1:
        return close[0], []
    return None, close


def visible_lists(hass: HomeAssistant) -> list[Any]:
    lists = hass.states.async_all("todo")
    try:
        from homeassistant.components.homeassistant.exposed_entities import async_should_expose

        shown = [s for s in lists if async_should_expose(hass, "conversation", s.entity_id)]
    except (ImportError, KeyError):  # Assistant exposure hasn't been initialised.
        return lists
    return shown


def _list_key(text: str) -> str:
    return " ".join(w for w in _words(text) if w not in ("list", "todo", "to", "do"))


def pick_list(hass: HomeAssistant, name: str | None, default: str | None = None,
              allowed: Callable[[str], bool] = lambda _: True) -> tuple[Any | None, list[str]]:
    lists = [state for state in visible_lists(hass) if allowed(state.entity_id)]
    if name:
        want = _list_key(name)
        if exact_id := next((st for st in lists if st.entity_id == name), None):
            return exact_id, []
        exact = [st for st in lists if want and _list_key(st.name) == want]
        if exact:
            return (exact[0], []) if len(exact) == 1 else (None, [st.name for st in exact])
        close = [st for st in lists if want and (want in _list_key(st.name) or want in _list_key(st.entity_id))]
        if len(close) == 1:
            return close[0], []
        return None, [st.name for st in (close or lists)]
    if default and (st := next((state for state in lists if state.entity_id == default), None)) is not None:
        return st, []
    shopping = [st for st in lists if "shopping" in st.entity_id or "shopping" in st.name.casefold()]
    if len(shopping) == 1:
        return shopping[0], []
    if len(lists) == 1:
        return lists[0], []
    return None, [st.name for st in lists]


async def _items(hass: HomeAssistant, entity_id: str, context: Context | None) -> list[dict[str, Any]]:
    resp = await hass.services.async_call("todo", "get_items", {"entity_id": entity_id}, blocking=True,
                                          return_response=True, context=context)
    items = ((resp or {}).get(entity_id) or {}).get("items")
    if not isinstance(items, list) or any(not isinstance(i, dict) or not isinstance(i.get("summary"), str)
                                         or i.get("status") not in ("needs_action", "completed") for i in items):
        raise HomeAssistantError("The list didn't report its contents.")
    return items


def _ref(item: dict[str, Any]) -> str:
    return item.get("uid") or item["summary"]


async def async_handle(hass: HomeAssistant, action: str, items: list[str], list_name: str | None,
                       default: str | None = None, notes: list[str] | None = None, *,
                       context: Context | None = None, replacement: str | None = None) -> dict[str, Any]:
    from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ

    user_id = getattr(context, "user_id", None)
    user = await hass.auth.async_get_user(user_id) if user_id else None
    if user_id and (user is None or not user.is_active):
        return {"error": "The list is unavailable."}
    target, choices = pick_list(hass, list_name, default, lambda eid: not user or (
        user.permissions.check_entity(eid, POLICY_READ)
        and (action == "show" or user.permissions.check_entity(eid, POLICY_CONTROL))))
    if target is None:
        return {"error": "which list?", "lists": choices} if choices else {"error": "there are no to-do lists"}
    if target.state in ("unknown", "unavailable"):
        return {"error": "The list is unavailable."}
    locks = hass.data.setdefault("sentient_todo_locks", {})
    async with locks.setdefault(target.entity_id, asyncio.Lock()):
        return await _handle_list(hass, target, action, items, notes, context, replacement)


async def _handle_list(hass, target, action, items, notes, context, replacement):
    eid, name = target.entity_id, target.name
    try:
        before = await _items(hass, eid, context)
    except HomeAssistantError:
        return {"error": "The list is unavailable."}
    out: dict[str, Any] = {"list": name}
    if notes:
        out["house_notes_about_lists"] = notes
    open_items = [i for i in before if i.get("status") == "needs_action"]
    done_items = [i for i in before if i.get("status") == "completed"]
    if action == "show":
        out["to_get"] = [i["summary"] for i in open_items]
        if done_items:
            out["ticked_off"] = [i["summary"] for i in done_items]
        return out

    done: list[str] = []
    already: list[str] = []
    not_found: list[str] = []
    which: dict[str, list[str]] = {}
    changes: list[tuple[str, dict[str, Any] | None]] = []
    for said in dict.fromkeys(s.strip() for s in items if s.strip()):
        if action == "add":
            hit, duplicates = match(said, open_items)
            if hit is not None and _key(hit["summary"]) == _key(said):
                already.append(hit["summary"])
                continue
            back, completed_duplicates = match(said, done_items)
            duplicates = [i for i in duplicates + completed_duplicates if _key(i["summary"]) == _key(said)]
            if duplicates:
                which[said] = [i["summary"] for i in duplicates]
                continue
            if back is not None and _key(back["summary"]) == _key(said):
                await hass.services.async_call("todo", "update_item", {"entity_id": eid, "item": _ref(back),
                                                                     "status": "needs_action"}, blocking=True, context=context)
                changes.append((back["summary"], back))
                continue
            await hass.services.async_call("todo", "add_item", {"entity_id": eid, "item": said}, blocking=True, context=context)
            changes.append((said.strip(), None))
            continue
        pool = {"reopen": done_items, "remove": before, "rename": before}.get(action, open_items)
        hit, close = match(said, pool)
        if hit is None and not close and action == "complete":
            other, close = match(said, done_items)
            if other is not None:
                already.append(other["summary"])
                continue
        if hit is None:
            if close:
                which[said] = [i["summary"] for i in close]
            else:
                not_found.append(said)
            continue
        if action == "remove":
            await hass.services.async_call("todo", "remove_item", {"entity_id": eid, "item": [_ref(hit)]}, blocking=True,
                                           context=context)
        elif action == "rename":
            if not replacement or not hit.get("uid"):
                not_found.append(said)
                continue
            if hit["summary"] == replacement:
                already.append(replacement)
                continue
            await hass.services.async_call("todo", "update_item", {"entity_id": eid, "item": _ref(hit),
                                           "rename": replacement}, blocking=True, context=context)
        else:
            status = "needs_action" if action == "reopen" else "completed"
            await hass.services.async_call("todo", "update_item", {"entity_id": eid, "item": _ref(hit), "status": status},
                                           blocking=True, context=context)
        changes.append((hit["summary"], hit))

    try:
        after = await _items(hass, eid, context) if changes else before
    except HomeAssistantError:
        return {"list": name, "done": [], "unverified": [summary for summary, _ in changes]}
    by_uid = {i.get("uid"): i for i in after if i.get("uid")}
    before_refs = {_ref(i) for i in before}
    for summary, item in changes:  # only what the list now really shows
        now = by_uid.get(item.get("uid")) if item and item.get("uid") else next(
            (i for i in after if i.get("summary") == summary and (item is not None or _ref(i) not in before_refs)), None)
        ok = {
            "add": now is not None and now.get("status") == "needs_action",
            "complete": now is not None and now.get("status") == "completed",
            "reopen": now is not None and now.get("status") == "needs_action",
            "remove": now is None,
            "rename": (now is not None and item is not None and now.get("summary") == replacement
                       and now.get("status") == item.get("status")),
        }[action]
        (done if ok else not_found).append(replacement if ok and action == "rename" else summary)
    out["done"] = done
    if already:
        out["already"] = already
    if not_found:
        out["not_done"] = not_found
        out["on_the_list"] = [i["summary"] for i in after if i.get("status") == "needs_action"][:15]
    if which:
        out["which_one"] = which
    out["say"] = ("Say only what's in done as done. Anything in not_done didn't happen — say so. For which_one, "
                  "ask which they meant.")
    return out


async def async_reply(hass: HomeAssistant, result: dict, language: str | None) -> str:
    """Read from the exact service result, preserving list and item text."""
    from . import phrases

    await phrases.async_load(hass, language)
    if "error" in result:
        if result.get("lists"):
            return f"{phrases.label('Which list?', language)} ({', '.join(result['lists'])})"
        return phrases.label("The list is unavailable.", language)
    parts = [result["list"]]
    fields = (
        ("to_get", "To do"), ("ticked_off", "Completed"), ("done", "Updated"),
        ("already", "Already up to date"), ("not_done", "No matching change"),
        ("unverified", "Unverified"),
    )
    for key, label in fields:
        if result.get(key):
            parts.append(f"{phrases.label(label, language)}: {', '.join(result[key])}")
    if choices := result.get("which_one"):
        parts.append(phrases.label("Which item?", language) + " " +
                     ", ".join(name for group in choices.values() for name in group))
    if len(parts) == 1 and "to_get" in result:
        parts.append(phrases.label("The list is empty.", language))
    return ". ".join(parts)
