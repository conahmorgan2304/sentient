"""Issue #206: shopping and to-do lists by voice — found however they're said, and only real changes reported."""

from homeassistant.core import SupportsResponse

from custom_components.sentient.lists import async_handle, match


def _fake_list(hass, items, entity="todo.shopping_list", name="Shopping List"):
    """A to-do list held in memory, served through the real todo service names."""
    hass.states.async_set(entity, str(len(items)), {"friendly_name": name})
    store = {"items": [dict(i, uid=i.get("uid") or str(n)) for n, i in enumerate(items)], "next": 100, "ignore": set()}

    async def get_items(call):
        return {entity: {"items": [dict(i) for i in store["items"]]}}

    async def update_item(call):
        if call.data["item"] in store["ignore"]:
            return  # a list that says yes and does nothing
        for i in store["items"]:
            if call.data["item"] in (i["uid"], i["summary"]):
                i["status"] = call.data["status"]

    async def add_item(call):
        store["next"] += 1
        store["items"].append({"uid": str(store["next"]), "summary": call.data["item"], "status": "needs_action"})

    async def remove_item(call):
        gone = set(call.data["item"])
        store["items"] = [i for i in store["items"] if i["uid"] not in gone and i["summary"] not in gone]

    hass.services.async_register("todo", "get_items", get_items, supports_response=SupportsResponse.ONLY)
    for svc, fn in (("update_item", update_item), ("add_item", add_item), ("remove_item", remove_item)):
        hass.services.async_register("todo", svc, fn)
    return store


def _status(store, summary):
    return next(i["status"] for i in store["items"] if i["summary"] == summary)


def test_matching_the_way_people_say_it():
    items = [{"summary": "Milk"}, {"summary": "Bin bags"}, {"summary": "Bananas"}]
    assert match("milk", items)[0]["summary"] == "Milk"
    assert match("the bin bag", items)[0]["summary"] == "Bin bags"
    assert match("banana", items)[0]["summary"] == "Bananas"
    assert match("bread", items) == (None, [])
    two = [{"summary": "Semi-skimmed milk"}, {"summary": "Oat milk"}]
    hit, choices = match("milk", two)
    assert hit is None and len(choices) == 2
    assert match("milk", [{"summary": "Semi-skimmed milk"}])[0]["summary"] == "Semi-skimmed milk"


async def test_mark_milk_as_purchased(hass):
    store = _fake_list(hass, [{"summary": "Milk", "status": "needs_action"}])
    out = await async_handle(hass, "complete", ["milk"], "shopping list")
    assert out["done"] == ["Milk"] and _status(store, "Milk") == "completed"


async def test_says_not_done_when_the_list_didnt_change(hass):
    store = _fake_list(hass, [{"summary": "Milk", "status": "needs_action", "uid": "m1"}])
    store["ignore"].add("m1")
    out = await async_handle(hass, "complete", ["milk"], None)
    assert out["done"] == [] and out["not_done"] == ["Milk"]


async def test_not_on_the_list_and_which_one(hass):
    _fake_list(hass, [{"summary": "Oat milk", "status": "needs_action"}, {"summary": "Whole milk", "status": "needs_action"}])
    out = await async_handle(hass, "complete", ["milk", "eggs"], None)
    assert out["done"] == [] and out["not_done"] == ["eggs"] and set(out["which_one"]["milk"]) == {"Oat milk", "Whole milk"}


async def test_add_doesnt_duplicate_and_puts_a_ticked_item_back(hass):
    store = _fake_list(hass, [{"summary": "Milk", "status": "needs_action"}, {"summary": "Eggs", "status": "completed"}])
    out = await async_handle(hass, "add", ["milk", "eggs", "bread"], None)
    assert out["already"] == ["Milk"] and set(out["done"]) == {"Eggs", "bread"}
    assert _status(store, "Eggs") == "needs_action" and len(store["items"]) == 3


async def test_remove_and_show(hass):
    store = _fake_list(hass, [{"summary": "Milk", "status": "needs_action"}, {"summary": "Eggs", "status": "completed"}])
    shown = await async_handle(hass, "show", [], None)
    assert shown["to_get"] == ["Milk"] and shown["ticked_off"] == ["Eggs"]
    out = await async_handle(hass, "remove", ["milk"], None)
    assert out["done"] == ["Milk"] and [i["summary"] for i in store["items"]] == ["Eggs"]


async def test_named_list_and_house_notes(hass):
    _fake_list(hass, [{"summary": "Milk", "status": "needs_action"}])
    _fake_list(hass, [{"summary": "Fix the gate", "status": "needs_action"}], "todo.jobs", "Jobs")
    out = await async_handle(hass, "show", [], "jobs list", notes=["Lidl list is the shopping list"])
    assert out["list"] == "Jobs" and out["to_get"] == ["Fix the gate"]
    assert out["house_notes_about_lists"] == ["Lidl list is the shopping list"]
    unsure = await async_handle(hass, "show", [], "garage")
    assert unsure["error"] == "which list?"


async def test_tool_is_offered_and_can_be_switched_off(hass):
    from custom_components.sentient.llm_api import ListsTool

    assert ListsTool.name == "sentient_lists" and ListsTool.acts


def test_printer_and_appliance_temperatures_arent_the_rooms():
    """Issue #197: a Bambu spool holder's chamber temperature showed as the room's."""
    from types import SimpleNamespace

    from custom_components.sentient.glance import _chip

    def st(eid, name):
        return SimpleNamespace(entity_id=eid, name=name)

    assert _chip(st("sensor.bambu_ams_1_temperature", "AMS 1 Temperature"))
    assert _chip(st("sensor.x1c_chamber_temperature", "X1C Chamber temperature"))
    assert _chip(st("sensor.fridge_temperature", "Fridge temperature"))
    assert not _chip(st("sensor.kitchen_temperature", "Kitchen Temperature"))
    assert not _chip(st("sensor.cupboard_temperature", "Cupboard temperature"))
