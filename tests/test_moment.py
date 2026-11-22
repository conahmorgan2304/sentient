"""Awareness of the moment: who's about, where there's movement, what just happened, what's unusual today."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": [], "quiet_start": "00:00:00", "quiet_end": "00:00:00"}
CTX = llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None)


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _prompt(hass) -> str:
    return (await llm.async_get_api(hass, DOMAIN, CTX)).api_prompt


async def test_off_adds_nothing(hass: HomeAssistant) -> None:
    hass.states.async_set("person.sam", "home", {"friendly_name": "Sam"})
    await _setup(hass)
    assert "Who's about" not in await _prompt(hass)


async def test_the_moment_is_in_every_reply(hass: HomeAssistant, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/London")
    freezer.move_to("2026-11-22T18:00:00+00:00")
    hass.states.async_set("person.sam", "home", {"friendly_name": "Sam"})
    hass.states.async_set("person.alex", "not_home", {"friendly_name": "Alex"})
    entry = await _setup(hass, moment_awareness=True)
    rt = entry.runtime_data
    rt.timeline.add("drive", "A black SUV arrived on the drive")
    rt.timeline.add("mind", "Thinking out loud")  # the house talking to itself: not an event
    kitchen = ar.async_get(hass).async_get_or_create("Kitchen")
    rt.tracker.occupied_areas = lambda *a, **k: [kitchen.id]
    rt.brain.expect.scores[dt_util.now().date().isoformat()] = {"known": 5, "expected": 4, "surprises": [
        {"entity": "light.shed", "name": "Shed light", "new": "on", "ts": dt_util.utcnow().timestamp() - 600}]}
    prompt = await _prompt(hass)
    assert "Movement lately in" not in prompt  # not said twice
    assert "Sam home since 18:00" in prompt and "Alex out since 18:00" in prompt
    assert "Movement in the last few minutes, busiest first: Kitchen" in prompt
    assert "18:00 A black SUV arrived on the drive" in prompt and "Thinking out loud" not in prompt
    assert "Shed light went on at 17:50" in prompt


async def test_guests_only_get_the_movement(hass: HomeAssistant) -> None:
    hass.states.async_set("person.sam", "home", {"friendly_name": "Sam"})
    entry = await _setup(hass, moment_awareness=True)
    rt = entry.runtime_data
    rt.timeline.add("drive", "A black SUV arrived on the drive")
    kitchen = ar.async_get(hass).async_get_or_create("Kitchen")
    rt.tracker.occupied_areas = lambda *a, **k: [kitchen.id]
    rt.speech.guest_mode = True
    prompt = await _prompt(hass)
    assert "Sam" not in prompt and "black SUV" not in prompt
    assert "busiest first: Kitchen" in prompt


async def test_an_old_event_is_not_just_now(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass, moment_awareness=True)
    entry.runtime_data.timeline.add("drive", "A van arrived")
    freezer.tick(timedelta(hours=2))
    assert "A van arrived" not in await _prompt(hass)
