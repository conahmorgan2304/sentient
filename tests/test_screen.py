"""The tablet screen: alerts and notices from what's said, the morning card, and on/off."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.sentient.const import DOMAIN, EVENT_SPOKE

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_what_is_said_goes_on_the_screen_with_the_camera(hass: HomeAssistant) -> None:
    hass.states.async_set("camera.doorbell", "idle")
    entry = await _setup(hass, door_camera="camera.doorbell")
    hass.bus.async_fire(EVENT_SPOKE, {"message": "Someone's at the door.", "source": "visitors", "priority": "normal"})
    hass.bus.async_fire(EVENT_SPOKE, {"message": "Nice day for it.", "source": "notice", "priority": "normal"})  # chatter: not shown
    await hass.async_block_till_done()
    screen = entry.runtime_data.screen
    assert screen.mode() == "notice"
    assert [(c["text"], c["camera"]) for c in screen.cards] == [("Someone's at the door.", "camera.doorbell")]
    state = hass.states.get("sensor.nova_screen")
    assert state.state == "notice" and state.attributes["cards"][0]["text"] == "Someone's at the door."
    hass.bus.async_fire(EVENT_SPOKE, {"message": "Smoke in the kitchen!", "source": "critical", "priority": "urgent"})
    await hass.async_block_till_done()
    assert screen.mode() == "alert" and screen.cards[0]["until"] is None  # safety stays until dismissed


async def test_notices_clear_themselves_and_can_be_dismissed(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    screen = entry.runtime_data.screen
    card = screen.show("Washing machine's finished.", "appliance:finished")
    other = screen.show("Test", "service")
    await hass.services.async_call(DOMAIN, "clear_screen", {"card_id": other["id"]}, blocking=True)
    assert [c["id"] for c in screen.cards] == [card["id"]]
    screen.cards[0]["until"] = (dt_util.utcnow() - timedelta(seconds=1)).isoformat()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=1))
    await hass.async_block_till_done()
    assert screen.mode() == "idle"


async def test_morning_card_when_the_house_wakes(hass: HomeAssistant) -> None:
    hass.states.async_set("weather.home", "sunny", {"temperature": 12, "temperature_unit": "°C"})
    entry = await _setup(hass)
    morning = await entry.runtime_data.screen.async_morning()
    assert morning["weather"] == "sunny, 12°C" and morning["day"]


async def test_screen_actions_and_switching_it_off(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "input_boolean", "turn_on")
    entry = await _setup(hass, screen_actions=[{"action": "input_boolean.turn_on", "target": {"entity_id": "input_boolean.x"}}])
    entry.runtime_data.screen.show("Gate opened.", "announce:Gate")
    await hass.async_block_till_done()
    assert len(calls) == 1
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.nova_tablet_screen"}, blocking=True)
    await hass.async_block_till_done()
    screen = entry.runtime_data.screen
    assert not screen.enabled and screen.mode() == "idle" and not screen.cards
    assert screen.show("Gate opened.", "announce:Gate")["shown"] is False
    hass.bus.async_fire(EVENT_SPOKE, {"message": "Someone's at the door.", "source": "visitors"})
    await hass.async_block_till_done()
    assert not screen.cards and len(calls) == 1
