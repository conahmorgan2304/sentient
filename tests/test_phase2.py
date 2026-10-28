"""Phase 2: 'say it differently', suggestions, 'coming up' on the screen and asking before big actions —
each working when on, and doing nothing when off."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN, personality_line

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _prompt(hass: HomeAssistant) -> str:
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                               assistant=None, device_id=None))
    return api.api_prompt


async def test_say_it_differently(hass: HomeAssistant, hass_ws_client) -> None:
    entry = await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/feedback/style", "text": "The kettle is all boiled.", "note": "Shorter"})
    assert (await ws.receive_json())["success"]
    await ws.send_json({"id": 2, "type": "sentient/feedback/style", "text": "x", "note": "shorter"})  # same note: once
    assert (await ws.receive_json())["result"]["notes"] == ["shorter"]
    opts = entry.runtime_data.options
    assert "How the household has asked for things to be said: shorter." in personality_line(opts)
    assert "shorter" in await _prompt(hass)
    # off: the notes aren't used and nothing new is taken
    hass.config_entries.async_update_entry(entry, options={**entry.data, **entry.options, "style_feedback": False})
    await hass.async_block_till_done()
    assert "How the household" not in personality_line(entry.runtime_data.options)
    await ws.send_json({"id": 3, "type": "sentient/feedback/style", "text": "x", "note": "Plainer"})
    assert not (await ws.receive_json())["success"]


async def test_suggestions_offer_then_use_or_dismiss(hass: HomeAssistant, hass_ws_client) -> None:
    hass.states.async_set("sensor.washing_machine_power", "3", {"device_class": "power", "friendly_name": "Washing machine power"})
    hass.states.async_set("sensor.hutch_temperature", "9", {"device_class": "temperature", "friendly_name": "Hutch temperature"})
    entry = await _setup(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/suggestions"})
    items = (await ws.receive_json())["result"]["items"]
    ids = {i["id"]: i for i in items}
    assert "appliances|sensor.washing_machine_power" in ids and "animal_sensors|sensor.hutch_temperature" in ids
    await ws.send_json({"id": 2, "type": "sentient/suggestions/act", "suggestion": "appliances|sensor.washing_machine_power", "accept": True})
    reply = await ws.receive_json()
    assert reply["success"], reply
    await hass.async_block_till_done()
    assert "sensor.washing_machine_power" in entry.runtime_data.options["appliances"]
    await ws.send_json({"id": 3, "type": "sentient/suggestions/act", "suggestion": "animal_sensors|sensor.hutch_temperature", "accept": False})
    await ws.receive_json()
    await ws.send_json({"id": 4, "type": "sentient/suggestions"})
    assert (await ws.receive_json())["result"]["items"] == []  # used, and dismissed for good
    hass.states.async_set("sensor.dryer_power", "1", {"device_class": "power", "friendly_name": "Dryer power"})
    hass.config_entries.async_update_entry(entry, options={**entry.data, **entry.options, "suggestions": False})
    await hass.async_block_till_done()
    await ws.send_json({"id": 5, "type": "sentient/suggestions"})
    assert (await ws.receive_json())["result"]["items"] == []  # off: nothing offered


async def test_coming_up_on_the_screen(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    soon = dt_util.as_local(dt_util.utcnow() + timedelta(hours=2))
    rt.alarms.set("Bins out", soon.strftime("%H:%M"))
    rt.watches.add_reminder("Ring the dentist", minutes=30)
    rt.alarms.set("Far off", (soon + timedelta(hours=3)).strftime("%H:%M"), on="2099-01-01")
    items = rt.screen.coming_up()
    assert [i["what"] for i in items] == ["Ring the dentist", "Bins out"]
    await hass.async_block_till_done()
    sensor = next(s for s in hass.states.async_all("sensor") if "coming_up" in s.attributes)
    assert [i["what"] for i in sensor.attributes["coming_up"]][:1] == ["Ring the dentist"]


async def test_ask_before_big_actions(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert "Ask first" not in await _prompt(hass)  # off by default
    hass.config_entries.async_update_entry(entry, options={**entry.data, **entry.options, "confirm_kinds": ["locks", "garage"]})
    await hass.async_block_till_done()
    prompt = await _prompt(hass)
    assert "Ask first: before you unlock or lock a door; or open or close a garage door or gate" in prompt
    assert "blinds" not in prompt.split("Ask first")[1].split("\n")[0]
