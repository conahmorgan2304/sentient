"""Motion lights: dark only, quiet hours per light, late-night dimming, hands off."""

from datetime import datetime, timedelta

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": [], "quiet_start": "21:00:00", "quiet_end": "08:00:00"}


async def _setup(hass: HomeAssistant, freezer, hour: int, minute: int = 0):
    freezer.move_to(dt_util.as_utc(datetime(2026, 10, 22, hour, minute, tzinfo=dt_util.get_default_time_zone())))
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("sun.sun", "below_horizon")
    hass.states.async_set("light.landing", "off")
    hass.states.async_set("binary_sensor.landing_motion", "off")
    return entry


async def _motion(hass: HomeAssistant, on: bool) -> None:
    hass.states.async_set("binary_sensor.landing_motion", "on" if on else "off")
    await hass.async_block_till_done()


async def test_on_after_dark_and_off_when_still(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass, freezer, 19)
    on = async_mock_service(hass, "light", "turn_on")
    off = async_mock_service(hass, "light", "turn_off")
    ml = entry.runtime_data.motion_lights
    ml.upsert({"light": "light.landing", "motion": ["binary_sensor.landing_motion"], "off_after": 2, "brightness": 80})
    await _motion(hass, True)
    assert on[-1].data == {"entity_id": "light.landing", "brightness_pct": 80}
    hass.states.async_set("light.landing", "on", context=on[-1].context)  # our own change
    await _motion(hass, False)
    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not off
    freezer.tick(timedelta(minutes=1, seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(off) == 1


async def test_daytime_and_quiet_hours(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass, freezer, 22)
    on = async_mock_service(hass, "light", "turn_on")
    ml = entry.runtime_data.motion_lights
    rule = ml.upsert({"light": "light.landing", "motion": ["binary_sensor.landing_motion"]})
    await _motion(hass, True)
    assert not on and rule.last == "stayed off: quiet hours"
    await _motion(hass, False)
    # this light ignores quiet hours; before 23:00 it's normal brightness
    ml.upsert({"id": rule.id, "respect_quiet": False, "night_brightness": 15})
    await _motion(hass, True)
    assert on[-1].data["brightness_pct"] == 100
    hass.states.async_set("light.landing", "off")
    await _motion(hass, False)
    # late at night: dimmed
    freezer.tick(timedelta(hours=2))
    await _motion(hass, True)
    assert on[-1].data["brightness_pct"] == 15
    hass.states.async_set("light.landing", "off")
    await _motion(hass, False)
    # in daylight nothing happens unless "only after dark" is off
    hass.states.async_set("sun.sun", "above_horizon")
    count = len(on)
    await _motion(hass, True)
    assert len(on) == count


async def test_hands_off_a_light_someone_controls(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass, freezer, 19)
    on = async_mock_service(hass, "light", "turn_on")
    off = async_mock_service(hass, "light", "turn_off")
    ml = entry.runtime_data.motion_lights
    ml.upsert({"light": "light.landing", "motion": ["binary_sensor.landing_motion"], "off_after": 1})
    # already on by hand: not touched, and never switched off
    hass.states.async_set("light.landing", "on")
    await _motion(hass, True)
    assert not on
    hass.states.async_set("light.landing", "off")
    await _motion(hass, False)
    # ours, then someone changes it by hand: no longer switched off
    await _motion(hass, True)
    hass.states.async_set("light.landing", "on", {"brightness": 255}, context=on[-1].context)
    await hass.async_block_till_done()
    # the light reporting back a moment later, and reconnecting: still ours
    hass.states.async_set("light.landing", "on", {"brightness": 250, "color_temp_kelvin": 3000}, context=Context())
    hass.states.async_set("light.landing", "unavailable", context=Context())
    await hass.async_block_till_done()
    assert ml.listing()["rules"][0]["last"] != "taken over by hand"
    hass.states.async_set("light.landing", "off")
    await _motion(hass, False)
    await _motion(hass, True)
    hass.states.async_set("light.landing", "on", {"brightness": 255}, context=on[-1].context)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=40))
    hass.states.async_set("light.landing", "on", {"brightness": 252, "color_temp_kelvin": 2700}, context=Context())
    await hass.async_block_till_done()
    assert ml.listing()["rules"][0]["last"] != "taken over by hand"
    hass.states.async_set("light.landing", "on", {"brightness": 40}, context=Context())
    await hass.async_block_till_done()
    await _motion(hass, False)
    freezer.tick(timedelta(minutes=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not off
    assert ml.listing()["rules"][0]["last"] == "taken over by hand"


async def test_websocket_crud(hass: HomeAssistant, hass_ws_client) -> None:
    ws = await hass_ws_client(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("sun.sun", "below_horizon")
    await ws.send_json({"id": 1, "type": "sentient/motion_lights/save",
                        "rule": {"light": "light.landing", "motion": "binary_sensor.landing_motion", "night_brightness": 20,
                                 "quiet_start": "20:30:00", "quiet_end": "07:00"}})
    res = (await ws.receive_json())["result"]
    rule_id = res["rules"][0]["id"]
    assert res["rules"][0]["motion"] == ["binary_sensor.landing_motion"] and res["dark"] is True
    assert res["rules"][0]["quiet_start"] == "20:30" and res["rules"][0]["quiet_end"] == "07:00"
    await ws.send_json({"id": 2, "type": "sentient/motion_lights/save",
                        "rule": {"light": "light.landing", "motion": ["binary_sensor.landing_motion"]}})
    assert (await ws.receive_json())["error"]["code"] == "invalid"  # one rule per light
    await ws.send_json({"id": 3, "type": "sentient/motion_lights/night", "start": "23:30", "end": "05:45"})
    assert (await ws.receive_json())["result"]["night_start"] == "23:30"
    await ws.send_json({"id": 4, "type": "sentient/motion_lights/save",
                        "rule": {"id": rule_id, "light": "light.landing", "motion": ["binary_sensor.landing_motion"],
                                 "quiet_start": None, "quiet_end": None}})
    assert (await ws.receive_json())["result"]["rules"][0]["quiet_start"] is None
    await ws.send_json({"id": 5, "type": "sentient/motion_lights/delete", "rule_id": rule_id})
    assert (await ws.receive_json())["result"]["rules"] == []
    assert entry.runtime_data.motion_lights.managed_lights == set()


async def test_lux_sensor_and_voice_tool(hass: HomeAssistant, freezer) -> None:
    from homeassistant.helpers import llm

    entry = await _setup(hass, freezer, 13)
    hass.states.async_set("sun.sun", "above_horizon")
    hass.states.async_set("sensor.landing_lux", "80")
    on = async_mock_service(hass, "light", "turn_on")
    ml = entry.runtime_data.motion_lights
    ml.upsert({"light": "light.landing", "motion": ["binary_sensor.landing_motion"], "lux_sensor": "sensor.landing_lux", "lux_below": 40})
    hass.states.async_set("light.landing", "off", {"friendly_name": "Landing light"})
    await _motion(hass, True)
    assert not on  # bright enough
    await _motion(hass, False)
    hass.states.async_set("sensor.landing_lux", "12")
    await _motion(hass, True)
    assert len(on) == 1  # dark on the landing even in daytime
    await _motion(hass, False)

    api = await llm.async_get_api(hass, "sentient", llm.LLMContext(platform="test", context=None, language="en", assistant=None, device_id=None))
    res = await api.async_call_tool(llm.ToolInput(tool_name="sentient_motion_lights", tool_args={"light": "landing", "on": False}))
    data = getattr(res, "data", res)
    assert data["follows_motion"] is False and not ml.find("landing").enabled
    hass.states.async_set("light.landing", "off", {"friendly_name": "Landing light"})
    await _motion(hass, True)
    assert len(on) == 1


async def test_stayed_off_logged_once_not_every_time(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass, freezer, 23, 30)
    async_mock_service(hass, "light", "turn_on")
    seen = []
    hass.bus.async_listen("sentient_motion_light", lambda e: seen.append(e.data["what"]))
    rule = entry.runtime_data.motion_lights.upsert({"light": "light.landing", "motion": ["binary_sensor.landing_motion"]})
    for _ in range(3):
        await _motion(hass, True)
        await _motion(hass, False)
        freezer.tick(timedelta(minutes=5))
    assert rule.last == "stayed off: quiet hours"
    assert seen.count("stayed off: quiet hours") == 1


async def test_custom_quiet_cutoff_stops_only_its_own_motion_light(hass, freezer):
    entry = await _setup(hass, freezer, 20, 29)
    ml = entry.runtime_data.motion_lights
    on = async_mock_service(hass, "light", "turn_on")
    off = async_mock_service(hass, "light", "turn_off")
    hass.states.async_set("light.bedroom", "off")
    motion = ["binary_sensor.landing_motion"]
    bedroom = ml.upsert({"light": "light.bedroom", "motion": motion, "quiet_start": "20:30", "quiet_end": "07:00"})
    ml.upsert({"light": "light.landing", "motion": motion})

    await _motion(hass, True)
    assert {call.data["entity_id"] for call in on} == {"light.bedroom", "light.landing"}
    for call in on:
        hass.states.async_set(call.data["entity_id"], "on", context=call.context)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [call.data["entity_id"] for call in off] == ["light.bedroom"]
    assert bedroom.last == "off: this light's quiet hours"

    hass.states.async_set("light.bedroom", "off", context=off[0].context)
    hass.states.async_set("light.landing", "off")
    await _motion(hass, False)
    await _motion(hass, True)
    assert [call.data["entity_id"] for call in on[2:]] == ["light.landing"]

    freezer.tick(timedelta(hours=10, minutes=30))  # 07:00, before the house quiet period ends
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    await _motion(hass, False)
    await _motion(hass, True)
    assert [call.data["entity_id"] for call in on[3:]] == ["light.bedroom"]


@pytest.mark.parametrize("manual_takeover", [False, True])
async def test_cutoff_respects_manual_control_and_delayed_own_reports(hass, freezer, manual_takeover):
    entry = await _setup(hass, freezer, 20, 29)
    ml = entry.runtime_data.motion_lights
    on = async_mock_service(hass, "light", "turn_on")
    off = async_mock_service(hass, "light", "turn_off")
    rule = ml.upsert({"light": "light.landing", "motion": ["binary_sensor.landing_motion"],
                      "quiet_start": "20:30", "quiet_end": "07:00"})
    freezer.tick(timedelta(seconds=45))
    await _motion(hass, True)
    if manual_takeover:
        hass.states.async_set(rule.light, "on", {"brightness": 255}, context=on[0].context)
        await hass.async_block_till_done()
        hass.states.async_set(rule.light, "on", {"brightness": 200}, context=Context(user_id="manual"))
        await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=15))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not off
    if manual_takeover:
        assert rule.last == "taken over by hand"
    else:
        hass.states.async_set(rule.light, "on", context=on[0].context)
        await hass.async_block_till_done()
        assert [call.data["entity_id"] for call in off] == [rule.light]


def test_quiet_window_needs_a_pair_of_different_local_times():
    from custom_components.sentient.motion_lights import MotionRule

    rule = MotionRule(light="light.landing", motion=["binary_sensor.landing_motion"])
    assert rule.quiet_window() is None
    rule.quiet_start = "20:30"
    with pytest.raises(ValueError, match="both quiet times"):
        rule.quiet_window()
    rule.quiet_end = "20:30"
    with pytest.raises(ValueError, match="different local times"):
        rule.quiet_window()
