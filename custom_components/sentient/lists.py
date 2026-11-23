"""Shopping and to-do lists by voice, done properly: tick off, put back, take off, add, read out.

Issue #206: "mark milk as purchased on the shopping list" was answered "Done" with nothing ticked. Home
Assistant's own list intent only finds an item spelt exactly as on the list ("milk" isn't "Milk"), and the voice
was free to say it had worked anyway. Here:

* items are matched the way people say them — any case, "the"/"some" ignored, milk/milks, "semi-skimmed milk"
  found from "milk" when it's the only milk — and two possible matches are asked about rather than guessed;
* every change is read back from the list afterwards, and only what's really changed comes back as ``done``;
* the list is the one named, else the shopping list, else the only list Assist can see;
* the house's own notes about lists (from memory) come back with the answer, so they're followed.

No AI. Only lists exposed to Assist (all lists if none are). Settings › Voice › Shopping and to-do lists.
"""

from __future__ import annotations

import re
from typing import Any

from homeassistant.core import HomeAssistant

CONF_VOICE_LISTS = "voice_lists"
FILLER = {"the", "a", "an", "some", "of", "my", "our", "more", "any"}


def _words(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", str(text).casefold())
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
    if len(exact) >= 1:
        return exact[0], []
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
    except Exception:  # noqa: BLE001 - exposure isn't available: every list
        shown = []
    return shown or lists


def _list_key(text: str) -> str:
    return " ".join(w for w in _words(text) if w not in ("list", "todo", "to", "do"))


def pick_list(hass: HomeAssistant, name: str | None, default: str | None = None) -> tuple[Any | None, list[str]]:
    lists = visible_lists(hass)
    if name:
        want = _list_key(name)
        for st in lists:
            if st.entity_id == name or (want and _list_key(st.name) == want):
                return st, []
        close = [st for st in lists if want and (want in _list_key(st.name) or want in _list_key(st.entity_id))]
        if len(close) == 1:
            return close[0], []
        return None, [st.name for st in (close or lists)]
    if default and (st := hass.states.get(default)) is not None:
        return st, []
    shopping = [st for st in lists if "shopping" in st.entity_id or "shopping" in st.name.casefold()]
    if len(shopping) == 1:
        return shopping[0], []
    if len(lists) == 1:
        return lists[0], []
    return None, [st.name for st in lists]


async def _items(hass: HomeAssistant, entity_id: str) -> list[dict[str, Any]]:
    resp = await hass.services.async_call("todo", "get_items", {"entity_id": entity_id}, blocking=True,
                                          return_response=True)
    return list(((resp or {}).get(entity_id) or {}).get("items") or [])


def _ref(item: dict[str, Any]) -> str:
    return item.get("uid") or item["summary"]


async def async_handle(hass: HomeAssistant, action: str, items: list[str], list_name: str | None,
                       default: str | None = None, notes: list[str] | None = None) -> dict[str, Any]:
    target, choices = pick_list(hass, list_name, default)
    if target is None:
        return {"error": "which list?", "lists": choices} if choices else {"error": "there are no to-do lists"}
    eid, name = target.entity_id, target.name
    before = await _items(hass, eid)
    out: dict[str, Any] = {"list": name}
    if notes:
        out["house_notes_about_lists"] = notes
    open_items = [i for i in before if i.get("status") == "needs_action"]
    done_items = [i for i in before if i.get("status") == "completed"]
    if action == "show":
        out["to_get"] = [i["summary"] for i in open_items]
        if done_items:
            out["ticked_off"] = [i["summary"] for i in done_items][:10]
        return out

    done: list[str] = []
    already: list[str] = []
    not_found: list[str] = []
    which: dict[str, list[str]] = {}
    changes: list[tuple[str, dict[str, Any] | None]] = []
    for said in [s for s in items if str(s).strip()]:
        if action == "add":
            hit, _ = match(said, open_items)
            if hit is not None and _key(hit["summary"]) == _key(said):
                already.append(hit["summary"])
                continue
            back, _ = match(said, done_items)
            if back is not None and _key(back["summary"]) == _key(said):
                await hass.services.async_call("todo", "update_item", {"entity_id": eid, "item": _ref(back),
                                                                     "status": "needs_action"}, blocking=True)
                changes.append((back["summary"], back))
                continue
            await hass.services.async_call("todo", "add_item", {"entity_id": eid, "item": said.strip()}, blocking=True)
            changes.append((said.strip(), None))
            continue
        pool = done_items if action == "reopen" else open_items
        hit, close = match(said, pool)
        if hit is None and not close and action in ("complete", "remove"):
            other, _ = match(said, done_items if action == "complete" else [])
            if other is not None:
                already.append(other["summary"])
                continue
            if action == "remove":
                hit, close = match(said, done_items)
        if hit is None:
            if close:
                which[said] = [i["summary"] for i in close]
            else:
                not_found.append(said)
            continue
        if action == "remove":
            await hass.services.async_call("todo", "remove_item", {"entity_id": eid, "item": [_ref(hit)]}, blocking=True)
        else:
            status = "needs_action" if action == "reopen" else "completed"
            await hass.services.async_call("todo", "update_item", {"entity_id": eid, "item": _ref(hit), "status": status},
                                           blocking=True)
        changes.append((hit["summary"], hit))

    after = await _items(hass, eid) if changes else before
    by_uid = {i.get("uid"): i for i in after if i.get("uid")}
    for summary, item in changes:  # only what the list now really shows
        now = by_uid.get(item.get("uid")) if item and item.get("uid") else next(
            (i for i in after if i.get("summary") == summary), None)
        ok = {
            "add": now is not None and now.get("status") == "needs_action",
            "complete": now is not None and now.get("status") == "completed",
            "reopen": now is not None and now.get("status") == "needs_action",
            "remove": now is None,
        }[action]
        (done if ok else not_found).append(summary)
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
