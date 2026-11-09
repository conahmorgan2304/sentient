"""Memory 2.0: dated things expire, newer replaces older, links to the house, synonyms."""

from datetime import date

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar

from custom_components.sentient.memory import MemoryStore, expiry_for

MON = date(2026, 11, 16)  # a Monday


def test_expiry_from_the_words():
    assert expiry_for("The boiler engineer is coming on Thursday", MON) == "2026-11-20"
    assert expiry_for("Parcel arriving tomorrow", MON) == "2026-11-18"
    assert expiry_for("Bins are going out tonight", MON) == "2026-11-17"
    assert expiry_for("Dentist on 3rd December", MON) == "2026-12-04"
    assert expiry_for("Next Monday the window cleaner comes", MON) == "2026-11-24"
    assert expiry_for("The bins go out every Thursday", MON) is None  # a habit
    assert expiry_for("Football on Thursdays", MON) is None
    assert expiry_for("The rabbits live by the shed", MON) is None


async def test_expired_memories_go_unless_pinned(hass: HomeAssistant):
    store = MemoryStore(hass, "t")
    await store.async_load()
    a = store.add("The boiler engineer is coming on Thursday")
    b = store.add("The plumber is coming tomorrow")
    store.pin(b.id)
    keep = store.add("The rabbits live by the shed")
    a.expires = b.expires = "2026-11-01"
    assert not store.live(a, date(2026, 11, 2))
    gone = store.prune_expired(date(2026, 11, 2))
    assert gone == [a] and {m.id for m in store.items} == {b.id, keep.id}
    assert store.changed[-1]["kind"] == "expired"


async def test_newer_replaces_older_only_when_just_the_value_changed(hass: HomeAssistant):
    store = MemoryStore(hass, "t")
    await store.async_load()
    store.add("The heating is comfortable at 20 degrees")
    store.add("The heating is comfortable at 21 degrees")
    store.add("The living room cooler is in use")
    store.add("The living room cooler is not in use")
    store.add("The bins go out on Thursday")
    store.add("The recycling goes out on Thursday")  # different thing: both kept
    texts = [m.text for m in store.items]
    assert texts == ["The heating is comfortable at 21 degrees", "The living room cooler is not in use",
                     "The bins go out on Thursday", "The recycling goes out on Thursday"]
    assert [c["kind"] for c in store.changed] == ["replaced", "replaced"]


async def test_links_and_synonyms_help_recall(hass: HomeAssistant):
    area = ar.async_get(hass).async_create("Living Room")
    hass.states.async_set("light.landing_light", "off", {"friendly_name": "Landing light"})
    hass.states.async_set("person.kayleigh", "home", {"friendly_name": "Kayleigh"})
    store = MemoryStore(hass, "t")
    await store.async_load()
    m = store.add("Kayleigh likes the landing light dim at night")
    assert set(m.links) == {"light.landing_light", "person.kayleigh"}
    lamp = store.add("The lamp in the living room goes on at sunset")
    assert f"area:{area.id}" in lamp.links
    store.add("The rabbits live by the shed")
    assert store.search("living room light", count_hits=False)[0].id == lamp.id  # synonym: light ~ lamp
    assert store.for_prompt("what does Kayleigh like?", 2)[0].id == m.id



async def test_check_up_finds_conflicts_and_duplicates_and_resolves_them(hass: HomeAssistant):
    store = MemoryStore(hass, "t")
    await store.async_load()
    a = store.add("When asked to turn up the heater, turn it on first", "preference")
    store.pin(a.id)
    b = store.add("When asked to turn up the heater, don't turn it on first", "preference")  # pinned a survives
    c = store.add("By the kitchen speaker I mean the kitchen echo", "preference")
    d = store.add("By the kitchen speaker I mean the kitchen echo", "correction")
    store.add("The rabbits live by the shed")
    pairs = store.review()
    kinds = {(p["kind"], frozenset((p["newer"]["id"], p["older"]["id"]))) for p in pairs}
    assert ("conflict", frozenset((a.id, b.id))) in kinds and ("duplicate", frozenset((c.id, d.id))) in kinds
    dup = next(p for p in pairs if p["kind"] == "duplicate")
    assert store.resolve(dup["pair"], c.id) and store.get(d.id) is None
    con = next(p for p in pairs if p["kind"] == "conflict")
    assert store.resolve(con["pair"], None)  # both fine: not asked again
    assert store.review() == []
