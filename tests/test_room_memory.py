"""Short-term memory per room."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.room_memory import lines

OPTIONS = {"persona": "Nova", "speakers": [], "quiet_start": "00:00:00", "quiet_end": "00:00:00", "room_memory": True}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _talk(rt, cid, room, asked, said, minutes_ago=0):
    when = (dt_util.utcnow() - timedelta(minutes=minutes_ago)).isoformat()
    rt.conversations.episodes.insert(0, {"id": cid[-10:], "start": when, "last": when, "room": room, "turns": [
        {"who": "them", "text": asked}, {"who": "me", "text": said}]})
    rt.conversations._ours[cid] = {"room": room}


async def test_what_went_on_in_this_room(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.conversations.episodes = []
    _talk(rt, "conv-kitchen-1", "Kitchen", "Put the kettle on", "The kettle's on.", 20)
    _talk(rt, "conv-lounge-01", "Living Room", "What's on TV?", "Nothing much.", 15)
    now = dt_util.utcnow().timestamp()
    rt.brain.ledger.recent.append({"ts": now - 600, "entity": "light.kitchen", "name": "Kitchen light", "area": "Kitchen",
                                   "old": "off", "new": "on", "cause": "voice"})
    rt.brain.ledger.recent.append({"ts": now - 500, "entity": "light.hall", "name": "Hall light", "area": "Hallway",
                                   "old": "off", "new": "on", "cause": "voice"})
    out = "\n".join(lines(rt, rt.options, "Kitchen", "conv-now", private=False, guest=False))
    assert "they: \"Put the kettle on\" / you: \"The kettle's on.\"" in out
    assert "Kitchen light off → on" in out
    assert "What's on TV" not in out and "Hall light" not in out


async def test_a_conversation_follows_someone_into_the_next_room(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    rt.conversations.episodes = []
    _talk(rt, "conv-kitchen-1", "Kitchen", "Is the back gate shut?", "Yes, it's shut.", 3)
    out = "\n".join(lines(rt, rt.options, "Living Room", "conv-now", private=False, guest=False))
    assert "just talking to you in the Kitchen" in out and "back gate" in out
    # not carried into a private room, or once it's old, or when something's been said here since
    assert not lines(rt, rt.options, "Living Room", "conv-now", private=True, guest=False)
    _talk(rt, "conv-lounge-01", "Living Room", "Lights on", "Done.", 1)
    assert "just talking" not in "\n".join(lines(rt, rt.options, "Living Room", "conv-now", private=False, guest=False))


async def test_off_and_in_the_prompt(hass: HomeAssistant) -> None:
    entry = await _setup(hass, room_memory=False)
    rt = entry.runtime_data
    _talk(rt, "conv-kitchen-1", "Kitchen", "Put the kettle on", "The kettle's on.", 20)
    assert lines(rt, rt.options, "Kitchen", None, private=False, guest=False) == []
    ctx = llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None)
    assert "Earlier in the" not in (await llm.async_get_api(hass, DOMAIN, ctx)).api_prompt
