"""Holiday mode: quiet (safety alerts aside), phones get what would have been said, heating to frost and back."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00", "notify_targets": ["notify.phone"],
           "holiday_thermostats": ["climate.lounge"], "away_people": ["person.sam"]}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen"})
    hass.states.async_set("notify.phone", "unknown")
    hass.states.async_set("climate.lounge", "heat", {"temperature": 20, "friendly_name": "Lounge"})
    hass.states.async_set("person.sam", "home", {"friendly_name": "Sam"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_holiday_quiet_phones_and_heating(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    temps = async_mock_service(hass, "climate", "set_temperature")
    said = async_mock_service(hass, "assist_satellite", "announce")
    phoned = async_mock_service(hass, "notify", "send_message")
    out = await rt.holiday.async_begin(dt_util.now() + timedelta(days=7))
    assert out["holiday"] and temps[-1].data == {"entity_id": "climate.lounge", "temperature": 7.0}
    assert hass.states.get("switch.nova_holiday_mode").state == "on"
    held = await rt.speech.async_say("The washing's done.", "normal", None, source="appliance:washer")
    await hass.async_block_till_done()
    assert held.reason == "holiday" and not said and phoned  # to the phones, not out loud
    alert = await rt.speech.async_say("Smoke in the kitchen!", "urgent", None, source="critical")
    assert alert.spoken  # safety alerts still sound
    # everyone goes out, then Sam comes home: back to normal, heating put back
    hass.states.async_set("person.sam", "not_home")
    await hass.async_block_till_done()
    assert rt.holiday.left and rt.holiday.active
    hass.states.async_set("person.sam", "home")
    await hass.async_block_till_done()
    assert not rt.holiday.active and temps[-1].data == {"entity_id": "climate.lounge", "temperature": 20}


async def test_by_voice_and_the_end_date(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass, holiday_thermostats=[])
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                               assistant=None, device_id=None))
    back = (dt_util.now() + timedelta(days=2)).date().isoformat()
    result = await api.async_call_tool(llm.ToolInput(tool_name="sentient_holiday", tool_args={"action": "on", "until": back}))
    out = getattr(result, "data", result)
    assert out["holiday"] and out["thermostats"] == []
    freezer.tick(timedelta(days=3))
    entry.runtime_data.holiday._tick(dt_util.utcnow())
    await hass.async_block_till_done()
    assert not entry.runtime_data.holiday.active


async def test_off_in_settings(hass: HomeAssistant) -> None:
    entry = await _setup(hass, holiday_mode=False)
    assert (await entry.runtime_data.holiday.async_begin())["error"]
    assert hass.states.get("switch.nova_holiday_mode").state == "unavailable"
