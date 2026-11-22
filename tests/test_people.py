"""Knowing each person: who's probably talking without voice recognition."""

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, llm
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": [], "quiet_start": "00:00:00", "quiet_end": "00:00:00", "know_people": True}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _prompt(hass, user_id=None, device_id=None) -> str:
    ctx = llm.LLMContext(platform="t", context=Context(user_id=user_id), language="en", assistant=None, device_id=device_id)
    return (await llm.async_get_api(hass, DOMAIN, ctx)).api_prompt


async def test_typed_in_the_app_is_its_user(hass: HomeAssistant) -> None:
    user = await hass.auth.async_create_user("Sam")
    hass.states.async_set("person.sam", "home", {"friendly_name": "Sam", "user_id": user.id})
    entry = await _setup(hass)
    mine = entry.runtime_data.memory.add("Prefers the lamp to the big light", owner="sam")
    for n in range(60):
        entry.runtime_data.memory.add(f"House fact number {n} about the shed {n}.")
    prompt = await _prompt(hass, user.id)
    assert "Probably Sam talking (they're signed in to Home Assistant as them)" in prompt
    assert mine.text in prompt  # their own things first, even with the list full


async def test_their_phone_alone_in_the_room(hass: HomeAssistant) -> None:
    lounge = ar.async_get(hass).async_get_or_create("Lounge")
    hass.states.async_set("person.sam", "home", {"friendly_name": "Sam"})
    hass.states.async_set("sensor.sams_phone_area", "Lounge", {"friendly_name": "Sam's phone Area"})
    other = MockConfigEntry(domain="esphome"); other.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=other.entry_id, identifiers={("esphome", "sat")})
    dr.async_get(hass).async_update_device(dev.id, area_id=lounge.id)
    await _setup(hass, room_tracking=True, room_trackers=["sensor.sams_phone_area"])
    assert "Probably Sam talking (their phone is the only tracked phone in this room)" in await _prompt(hass, device_id=dev.id)
    assert "Probably Sam" not in await _prompt(hass)  # no room to go on


async def test_off_guest_and_unknown_users_guess_nothing(hass: HomeAssistant) -> None:
    user = await hass.auth.async_create_user("Sam")
    hass.states.async_set("person.sam", "home", {"friendly_name": "Sam", "user_id": user.id})
    entry = await _setup(hass, know_people=False)
    assert "Probably Sam" not in await _prompt(hass, user.id)
    hass.config_entries.async_update_entry(entry, options={**entry.data, "know_people": True})
    await hass.async_block_till_done()
    entry.runtime_data.speech.guest_mode = True
    assert "Probably Sam" not in await _prompt(hass, user.id)
    entry.runtime_data.speech.guest_mode = False
    assert "Probably" not in await _prompt(hass, "someone-else")
