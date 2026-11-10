"""1.3.1: remarks remembered across restarts, personal-device batteries, Echo playback signs."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed  # noqa: F401

from custom_components.sentient.speech import SpeechRouter


async def test_a_remark_isnt_repeated_after_a_restart(hass, hass_storage, freezer):
    freezer.move_to(dt_util.as_utc(dt_util.now().replace(hour=12, minute=0, second=0, microsecond=0)))  # not in quiet hours
    first = SpeechRouter(hass, {"speakers": []})
    await first.async_load_remarks("abc")
    first._remember_said("The kettle has finished boiling.")
    await first._remark_store._async_handle_write_data()  # the delayed save, now
    again = SpeechRouter(hass, {"speakers": []})
    await again.async_load_remarks("abc")
    assert again._gate("The kettle has finished boiling.", "normal", "notice").reason == "said_recently"
    later = dt_util.utcnow() + timedelta(hours=4)
    with patch("custom_components.sentient.speech.dt_util.utcnow", return_value=later):
        assert again._gate("The kettle has finished boiling.", "normal", "notice", check_duplicate=False) is None


async def test_old_remarks_are_not_restored(hass, hass_storage):
    old = (dt_util.utcnow() - timedelta(hours=30)).isoformat()
    hass_storage["sentient.abc.remarks"] = {"version": 1, "key": "sentient.abc.remarks", "data": {"the kettle": old}}
    router = SpeechRouter(hass, {"speakers": []})
    await router.async_load_remarks("abc")
    assert router._remarks == {}


async def test_an_echo_that_shows_no_sign_of_playing_is_counted(hass):
    hass.states.async_set("media_player.kitchen_echo", "idle")
    router = SpeechRouter(hass, {"speakers": ["media_player.kitchen_echo"], "tts_entity": "tts.x"})
    hass.services.async_register("tts", "speak", lambda call: None)
    with patch("custom_components.sentient.speech.asyncio.sleep", AsyncMock()):
        rows = await router._speak_timed("Hello", ["media_player.kitchen_echo"])
    assert rows[0]["playback"] == "no_change" and router.silent_runs["media_player.kitchen_echo"] == 1
    hass.services.async_register("tts", "speak", lambda call: hass.states.async_set("media_player.kitchen_echo", "playing"))
    with patch("custom_components.sentient.speech.asyncio.sleep", AsyncMock()):
        rows = await router._speak_timed("Hello again", ["media_player.kitchen_echo"])
    assert rows[0]["playback"] == "state_changed" and router.silent_runs["media_player.kitchen_echo"] == 0


async def test_a_phone_battery_isnt_a_house_problem(hass):
    from custom_components.sentient.health import _personal_device

    ent_reg = er.async_get(hass)
    hass.states.async_set("sensor.my_phone_battery_plus", "2", {"device_class": "battery"})
    hass.states.async_set("sensor.door_sensor_battery", "2", {"device_class": "battery"})
    assert _personal_device(hass, hass.states.get("sensor.my_phone_battery_plus"), None, ent_reg)
    assert not _personal_device(hass, hass.states.get("sensor.door_sensor_battery"), None, ent_reg)
