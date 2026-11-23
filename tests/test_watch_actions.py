"""Watches and reminders that act: read back, a yes on a later turn, carried out, checked."""

from datetime import timedelta
from types import SimpleNamespace

from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.readback import Readbacks

OPTIONS = {
    "persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
    "quiet_start": "00:00:00", "quiet_end": "00:00:00",
}
STEP = [{"action": "light.turn_off", "target": "hall light"}]


async def _setup(hass: HomeAssistant, on: bool = True):
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", data={**OPTIONS, "watch_actions": on}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("assist_satellite.kitchen", "idle")
    hass.states.async_set("light.hall", "on", {"friendly_name": "Hall light"})
    hass.states.async_set("binary_sensor.back_door", "off", {"friendly_name": "Back door", "device_class": "door"})
    async_expose_entity(hass, "conversation", "light.hall", True)
    switched: list[ServiceCall] = []

    async def turn_off(call: ServiceCall) -> None:
        switched.append(call)
        for eid in call.data["entity_id"]:
            hass.states.async_set(eid, "off", {"friendly_name": "Hall light"})

    hass.services.async_register("light", "turn_off", turn_off)
    said = async_mock_service(hass, "assist_satellite", "announce")
    ctx = llm.LLMContext(platform="test", context=None, language="en", assistant="conversation", device_id=None)
    api = await llm.async_get_api(hass, "sentient", ctx)
    return entry, api, switched, said


async def test_off_by_default_watches_only_speak(hass: HomeAssistant) -> None:
    _, api, switched, _ = await _setup(hass, on=False)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_watch", tool_args={
        "what": "back door left open", "entity": "back door", "to_state": "on", "then_do": STEP}))
    assert "switched off" in out["error"] and "Watches and reminders can act" in out["error"]
    assert not switched


async def test_reads_back_then_acts_after_the_state_lasts(hass: HomeAssistant) -> None:
    entry, api, switched, said = await _setup(hass)
    args = {"what": "back door left open", "entity": "back door", "to_state": "on", "for_minutes": 10, "then_do": STEP}
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_watch", tool_args=args))
    assert out["read_back"] == "When the back door is open for 10 minutes: turn off the hall light, and tell you."
    assert not entry.runtime_data.watches.watches  # nothing set up until the yes
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_watch", tool_args={**args, "confirmed": True}))
    assert out["watching"]["then"] == "turn off the hall light"
    hass.states.async_set("binary_sensor.back_door", "on", {"friendly_name": "Back door"})
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5))
    await hass.async_block_till_done()
    assert not switched  # not yet ten minutes
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=11))
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in switched] == [["light.hall"]]
    assert said[-1].data["message"] == "I've turned off the hall light."
    assert not entry.runtime_data.watches.watches


async def test_a_repeating_watch_fires_once_per_stretch(hass: HomeAssistant) -> None:
    entry, api, switched, said = await _setup(hass)
    await api.async_call_tool(llm.ToolInput(tool_name="sentient_watch", tool_args={
        "what": "back door left open", "entity": "back door", "to_state": "on", "for_minutes": 10, "repeat": True,
        "message": "The back door's been open ten minutes.", "until": "23:59"}))
    hass.states.async_set("binary_sensor.back_door", "on", {"friendly_name": "Back door"})
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=11))
    await hass.async_block_till_done()
    for n in range(3):  # the sensor reports in again while still open (battery, signal)
        hass.states.async_set("binary_sensor.back_door", "on", {"friendly_name": "Back door", "battery": 90 - n})
        await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=30))
    await hass.async_block_till_done()
    assert len(said) == 1 and entry.runtime_data.watches.watches[0].fired == 1
    hass.states.async_set("binary_sensor.back_door", "off", {"friendly_name": "Back door"})
    hass.states.async_set("binary_sensor.back_door", "on", {"friendly_name": "Back door"})
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=45))
    await hass.async_block_till_done()
    assert entry.runtime_data.watches.watches[0].fired == 2  # opened again: a new stretch (speech drops the repeat)


async def test_closed_before_the_time_is_up_does_nothing(hass: HomeAssistant) -> None:
    _, api, switched, said = await _setup(hass)
    args = {"what": "back door left open", "entity": "back door", "to_state": "on", "for_minutes": 10, "then_do": STEP,
            "confirmed": True}
    await api.async_call_tool(llm.ToolInput(tool_name="sentient_watch", tool_args=args))
    hass.states.async_set("binary_sensor.back_door", "on", {"friendly_name": "Back door"})
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.back_door", "off", {"friendly_name": "Back door"})
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=11))
    await hass.async_block_till_done()
    assert not switched and not said


async def test_only_devices_the_voice_may_control(hass: HomeAssistant) -> None:
    _, api, switched, _ = await _setup(hass)
    hass.states.async_set("light.shed", "on", {"friendly_name": "Shed light"})
    async_expose_entity(hass, "conversation", "light.shed", False)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_watch", tool_args={
        "what": "x", "entity": "back door", "to_state": "on", "then_do": [{"action": "light.turn_off", "target": "shed light"}]}))
    assert "isn't one the voice is allowed to control" in out["error"]
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_watch", tool_args={
        "what": "x", "entity": "back door", "to_state": "on", "then_do": [{"action": "lock.unlock", "target": "hall light"}]}))
    assert "error" in out and not switched


async def test_reminder_checks_its_condition_first(hass: HomeAssistant) -> None:
    entry, api, switched, said = await _setup(hass)
    args = {"at": "23:00", "only_if": "back door", "only_if_is": "on", "then_do": STEP,
            "message": "The back door's still open.", "confirmed": True}
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_remind", tool_args=args))
    assert out["reminder"]["only_if"] == "the back door is open"
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_remind", tool_args={**args, "confirmed": False}))
    assert out["read_back"] == "At 23:00, if the back door is open: turn off the hall light, and say “The back door's still open”."
    watch = entry.runtime_data.watches.watches[0]
    await entry.runtime_data.watches._async_fire(watch, None, None)  # due, and the door is shut
    assert not switched and not said and not entry.runtime_data.watches.watches
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_remind", tool_args=args))
    hass.states.async_set("binary_sensor.back_door", "on", {"friendly_name": "Back door"})
    watch = entry.runtime_data.watches.watches[0]
    await entry.runtime_data.watches._async_fire(watch, None, None)
    await hass.async_block_till_done()
    assert said[-1].data["message"] == "The back door's still open. I've turned off the hall light."


async def test_a_watch_switched_off_later_does_not_act(hass: HomeAssistant) -> None:
    entry, api, switched, said = await _setup(hass)
    await api.async_call_tool(llm.ToolInput(tool_name="sentient_watch", tool_args={
        "what": "door", "entity": "back door", "to_state": "on", "then_do": STEP, "confirmed": True}))
    entry.runtime_data.watches.options = {**entry.runtime_data.watches.options, "watch_actions": False}
    hass.states.async_set("binary_sensor.back_door", "on", {"friendly_name": "Back door"})
    await hass.async_block_till_done()
    assert not switched
    assert "switched off in Settings" in said[-1].data["message"]


def test_a_yes_has_to_come_on_a_later_turn() -> None:
    books = Readbacks()
    session = SimpleNamespace(id="c1", turn=3)
    details = {"what": "door", "do": ["x"]}
    assert "Read it back first" in books.confirm("watch", details, session)
    books.propose("watch", details, session)
    assert "Wait for them" in books.confirm("watch", details, session)  # the model "heard" its own yes
    session.turn = 4
    assert "changed" in books.confirm("watch", {**details, "what": "other"}, session)
    assert books.confirm("watch", details, session) is None
    assert books.confirm("watch", details, session) is not None  # used up
    assert books.confirm("watch", details, None) is None  # no voice sessions: as rules always worked


def test_the_voice_is_offered_the_watch_tools_for_these() -> None:
    from custom_components.sentient.focus import Focus

    for asked in ("at 11 if the back door is still open tell me", "when the washing finishes, put the hallway light on",
                  "if the back door's open for ten minutes turn the hall light off"):
        offered = Focus().tools_for(asked, None)
        assert offered is None or {"sentient_watch", "sentient_remind"} <= offered, asked


async def test_grading_can_be_run_now(hass: HomeAssistant) -> None:
    entry, *_ = await _setup(hass)
    out = await hass.services.async_call(DOMAIN, "grade_answers", {}, blocking=True, return_response=True)
    assert "skipped" in out  # off unless switched on
