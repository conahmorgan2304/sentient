"""Memory picks what matters: pinned, then relevant, then recently useful; contradictions tidied."""

from homeassistant.core import HomeAssistant

from custom_components.sentient.memory import MemoryStore


async def test_pinned_then_relevant_then_used_then_newest(hass: HomeAssistant) -> None:
    store = MemoryStore(hass, "x")
    for n in range(30):
        store.add(f"Filler fact number {n} about topic{n}")
    pinned = store.add("The alarm code is never said out loud")
    store.pin(pinned.id)
    rabbit = store.add("The rabbits are called Sophia and Marshy")
    store.search("topic3")  # used recently
    chosen = store.for_prompt("how are the rabbits?", 6)
    assert chosen[0] is pinned and rabbit in chosen[:3]
    assert any(m.text.endswith("topic3") for m in chosen)
    assert len(chosen) == 6


async def test_pin_survives_reload(hass: HomeAssistant) -> None:
    store = MemoryStore(hass, "y")
    m = store.add("Bins go out on Thursday")
    store.pin(m.id)
    await hass.async_block_till_done()
    data = {"items": [x.as_dict() for x in store.items] + [{"text": "old style", "category": "fact", "id": "abc12345",
                                                           "created": "2026-01-01T00:00:00", "source": None, "hits": 0}]}
    store._store.async_load = lambda: _async(data)
    await store.async_load()
    assert store.get(m.id).pinned and store.get("abc12345").pinned is False


async def _async(value):
    return value
