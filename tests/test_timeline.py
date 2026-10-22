"""Timeline: one log of what the house did."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN, EVENT_HEALTH_CHANGED, EVENT_HELD, EVENT_NOTICED, EVENT_SPOKE, EVENT_WATCH_FIRED
from custom_components.sentient.motion_lights import EVENT_MOTION_LIGHT


async def test_timeline_records_and_answers(hass: HomeAssistant, hass_ws_client) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", "speakers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen speaker"})
    bus = hass.bus
    bus.async_fire(EVENT_SPOKE, {"message": "The washing's done.", "speakers": ["assist_satellite.kitchen"]})
    bus.async_fire(EVENT_HELD, {"message": "It's raining.", "reason": "quiet_hours"})
    bus.async_fire(EVENT_NOTICED, {"changes": ["Weather: cloudy → rainy"], "result": "stayed quiet"})
    bus.async_fire(EVENT_NOTICED, {"changes": ["x"], "result": "skipped: quiet_hours"})
    bus.async_fire(EVENT_WATCH_FIRED, {"what": "washer", "message": "The washer has finished."})
    bus.async_fire(EVENT_MOTION_LIGHT, {"name": "Landing light", "what": "on with motion"})
    from datetime import timedelta
    from homeassistant.util import dt as dt_util

    entry.runtime_data.timeline._started = dt_util.utcnow() - timedelta(hours=1)
    bus.async_fire(EVENT_HEALTH_CHANGED, {"problems": {}})
    bus.async_fire(EVENT_HEALTH_CHANGED, {"problems": {"offline:Waxmelt": "Waxmelt is offline"}})
    bus.async_fire(EVENT_HEALTH_CHANGED, {"problems": {}})
    await hass.async_block_till_done()
    tl = entry.runtime_data.timeline
    texts = [i["text"] for i in tl.since(1)]
    assert texts[:3] == ["Waxmelt is back to normal", "Waxmelt is offline", "Landing light: on with motion"]
    assert "x" not in texts and "Weather: cloudy → rainy" in texts
    said = next(i for i in tl.items if i["kind"] == "said")
    assert said["detail"] == "Kitchen speaker"
    assert tl.since(1, ["held"])[0]["detail"] == "held back: quiet hours"

    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/timeline", "kinds": ["light"]})
    assert [i["text"] for i in (await ws.receive_json())["result"]["items"]] == ["Landing light: on with motion"]

    api = await llm.async_get_api(hass, "sentient", llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None))
    res = await api.async_call_tool(llm.ToolInput(tool_name="sentient_timeline", tool_args={"hours": 2}))
    data = getattr(res, "data", res)
    whats = [e["what"] for e in data["events"]]
    assert "The washer has finished." in whats and "It's raining." not in whats


async def test_restart_keeps_offline_baseline(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", "speakers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    tl = entry.runtime_data.timeline
    tl._problems = {"offline:Frigate": "Frigate is offline"}  # known before the restart
    hass.bus.async_fire(EVENT_HEALTH_CHANGED, {"problems": {}})  # first check after start
    hass.bus.async_fire(EVENT_HEALTH_CHANGED, {"problems": {"offline:Frigate": "Frigate is offline"}})
    await hass.async_block_till_done()
    assert not [i for i in tl.items if i["kind"] == "health"]


async def test_digest(hass: HomeAssistant, hass_ws_client) -> None:
    from homeassistant.core import ServiceCall, SupportsResponse

    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova", "speakers": []})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    seen = []

    async def generate(call: ServiceCall):
        seen.append(call.data["instructions"])
        return {"conversation_id": "x", "data": "A delivery came at noon and the washer finished."}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    hass.bus.async_fire(EVENT_WATCH_FIRED, {"what": "washer", "message": "The washer has finished."})
    await hass.async_block_till_done()
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/digest"})
    res = (await ws.receive_json())["result"]
    assert res["digest"].startswith("A delivery") and "The washer has finished." in seen[0]
    assert entry.runtime_data.timeline.items[0]["kind"] == "digest"
