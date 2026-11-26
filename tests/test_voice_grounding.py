"""Voice grounding through the agent and actual Sentient tools with synthetic household data."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, SupportsResponse
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.sentient.conversation import BackedUpAgent
from custom_components.sentient.focus import current_request
from tests.test_phase3 import _setup
from tests.test_voice_sessions_control import agent, request, response


def spoken(result):
    return result.response.speech["plain"]["speech"]


@pytest.mark.parametrize(("language", "first", "ambiguous", "question", "yes"), [
    ("en", "Check the gate", "Check the gath", "Did you mean Gate?", "Yes"),
    ("fr", "Regarde le portail", "Regarde le portain", "Tu voulais dire Portail ?", "Oui"),
])
async def test_camera_voice_clarifies_then_checks_again(hass, language, first, ambiguous, question, yes):
    assert await async_setup_component(hass, "homeassistant", {})
    obj, rt = agent(hass, {"conversation_sessions": True, "specific_clarification": True,
                           "conversation_style": "natural", "voice_agent": "conversation.primary"})
    from custom_components.sentient.speech import SpeechRouter
    from custom_components.sentient.watches import WatchManager

    rt.speech = SpeechRouter(hass, rt.options)
    rt.watches = WatchManager(hass, "grounding", rt.options, rt.speech)
    hass.states.async_set("camera.gate", "idle", {"friendly_name": "Gate" if language == "en" else "Portail"})
    async_expose_entity(hass, "conversation", "camera.gate", True)
    generate = AsyncMock(return_value={"data": "A parcel is visible."})
    with patch("custom_components.sentient.watches.async_generate", generate), \
            patch.object(obj, "_converse", AsyncMock(return_value=response("The weather is fine."))) as provider:
        result = await obj.async_process(replace(request(first), language=language))
        assert spoken(result) == "A parcel is visible." and not result.continue_conversation
        assert generate.await_count == 1
        assert "camera.gate" in str(generate.call_args.args[1]["attachments"])
        assert "do not infer contents" in generate.call_args.args[1]["instructions"]

        result = await obj.async_process(replace(request(ambiguous), language=language))
        assert spoken(result) == question and result.continue_conversation
        assert generate.await_count == 1
        assert rt.sessions.get(request(first)).clarification["request"] == ambiguous

        result = await obj.async_process(replace(request(yes), language=language))
        assert spoken(result) == "A parcel is visible." and not result.continue_conversation
        assert generate.await_count == 2
        provider.assert_not_awaited()

        result = await obj.async_process(replace(request(ambiguous, device="other"), language=language))
        assert spoken(result) != question and generate.await_count == 2
        assert provider.call_args.args[1].conversation_id != "one"

        result = await obj.async_process(replace(request("What's the weather?"), language=language))
        assert "The weather is fine." in spoken(result)
        assert generate.await_count == 2
        assert not (await obj.async_process(request("That's all"))).continue_conversation


async def test_expired_camera_topic_and_known_new_target_do_not_trigger_clarification(hass, freezer):
    assert await async_setup_component(hass, "homeassistant", {})
    obj, rt = agent(hass, {"conversation_sessions": True, "specific_clarification": True,
                           "session_seconds": 15, "conversation_style": "natural", "voice_agent": "conversation.primary"})
    hass.states.async_set("camera.gate", "idle", {"friendly_name": "Gate"})
    async_expose_entity(hass, "conversation", "camera.gate", True)
    look = AsyncMock(return_value={"answer": "A gate is visible."})
    rt.watches = SimpleNamespace(spots=None, options=rt.options, async_look=look)
    with patch.object(obj, "_converse", AsyncMock(return_value=response("A different subject."))) as provider:
        await obj.async_process(request("Check the gate"))
        hass.states.async_set("sensor.gate_battery", "42", {"friendly_name": "Gath"})
        assert spoken(await obj.async_process(request("Check Gath"))) == "A different subject."
        await obj.async_process(request("Check the gate"))
        hass.states.async_remove("sensor.gate_battery")
        freezer.tick(timedelta(seconds=16))
        assert spoken(await obj.async_process(request("Check Gath"))) == "A different subject."
        assert provider.await_count == 2
        assert look.await_count == 2


async def test_provider_prompt_keeps_notices_and_unrelated_memories_out(hass):
    entry = await _setup(hass, prompt_memories=12, focused_tools=True, moment_awareness=True,
                         relevant_memories=False)
    rt = entry.runtime_data
    rt.memory.add("The gate festival delivery is scheduled today.", source="reflection")
    rt.memory.add("The gate hinges are grey.", source="settings")
    rt.timeline.add("said", "Festival delivery today", source="briefing")
    ctx = llm.LLMContext(platform="test", context=Context(), language="en", assistant=None, device_id=None)
    obj = BackedUpAgent(SimpleNamespace(entry_id=entry.entry_id, runtime_data=rt, title="Sentient"))
    obj.hass, obj.entity_id = hass, "conversation.sentient"
    options = {**rt.options, "voice_agent": "conversation.primary"}
    rt.options = options

    async def provider(_, incoming):
        api = await llm.async_get_api(hass, "sentient", ctx)
        assert current_request.get() == incoming.text
        assert "Festival delivery today" not in api.api_prompt
        assert "festival delivery is scheduled" not in api.api_prompt
        assert "parcel's contents" in api.api_prompt
        return response("I can only tell you what the camera shows.")

    with patch.object(obj, "_converse", side_effect=provider):
        assert "camera shows" in spoken(await obj.async_process(request("Can you see what is at the gate?")))


async def test_calendar_challenge_reads_current_exposed_calendar(hass, freezer):
    assert await async_setup_component(hass, "homeassistant", {})
    await hass.config.async_set_time_zone("Europe/London")
    freezer.move_to("2026-10-24T23:30:00+00:00")  # Sunday 00:30, before the clocks go back
    hass.states.async_set("calendar.home", "off")
    async_expose_entity(hass, "conversation", "calendar.home", True)
    hass.states.async_set("calendar.private", "off")
    async_expose_entity(hass, "conversation", "calendar.private", False)
    asked = []

    async def events(call):
        asked.append(call.data)
        return {"calendar.home": {"events": [
            {"start": "2026-11-01", "end": "2026-11-02", "summary": "Craft supplies delivery"},
            {"start": "2026-10-25", "end": "2026-10-26", "summary": "Weekly recycling"},
            {"start": "2026-10-31", "end": "2026-11-02", "summary": "Workshop"},
        ]}, "calendar.private": {"events": [
            {"start": "2026-10-25", "end": "2026-10-26", "summary": "Private title"},
        ]}}

    hass.services.async_register("calendar", "get_events", events, supports_response=SupportsResponse.ONLY)
    entry = await _setup(hass, focused_tools=True, instant_answers=True)
    rt = entry.runtime_data
    rt.options = {**rt.options, "voice_agent": "conversation.primary"}
    rt.memory.pin(rt.memory.add("Craft supplies delivery today.", source="calendar").id)
    rt.timeline.add("said", "Craft supplies delivery today.", source="briefing")
    ctx = llm.LLMContext(platform="test", context=Context(), language="en", assistant=None, device_id=None)
    obj = BackedUpAgent(SimpleNamespace(entry_id=entry.entry_id, runtime_data=rt, title="Sentient"))
    obj.hass, obj.entity_id = hass, "conversation.sentient"

    async def provider(_, _incoming):
        api = await llm.async_get_api(hass, "sentient", ctx)
        assert "Craft supplies delivery today." not in api.api_prompt
        recalled = await api.async_call_tool(llm.ToolInput("sentient_recall", {"query": "Craft supplies delivery today"}))
        assert not getattr(recalled, "data", recalled)["memories"]
        current = await api.async_call_tool(llm.ToolInput("sentient_calendar", {"days": 8}))
        current = getattr(current, "data", current)
        assert current["today"] == "Sunday 25 October"
        delivery = next(event for event in current["events"] if event["what"] == "Craft supplies delivery")
        assert delivery["when"] == "Sunday 1 November, all day"
        assert any(event["what"] == "Workshop" for event in current["events"])
        assert not any(event["what"] == "Private title" for event in current["events"])
        return response("The calendar shows " + delivery["when"] + ".")

    with patch.object(obj, "_converse", side_effect=provider) as converse:
        today = await obj.async_process(request("What's on my calendar today?"))
        assert "Weekly recycling" in spoken(today) and "Craft supplies" not in spoken(today)
        assert asked[-1]["end_date_time"] == "2026-10-26T00:00:00+00:00"
        result = await obj.async_process(request("Why did you say the delivery was today?"))
        assert spoken(result) == "The current calendar shows Craft supplies delivery, Sunday 1 November, all day."
        converse.assert_not_called()
        result = await obj.async_process(request("Was the delivery scheduled for today or next week?"))
        assert spoken(result) == "The calendar shows Sunday 1 November, all day."
        assert converse.call_count == 1
    assert all(call["entity_id"] == ["calendar.home"] for call in asked)


@pytest.mark.parametrize("claim", [
    "Would you like me to keep an eye on the gate?",
    "I'm monitoring the gate now.",
    "I've changed your notification settings.",
    "I'll make sure to remind you later.",
    "I've remembered your preference.",
])
async def test_unrelated_action_does_not_verify_watch_or_notification_claim(hass, claim):
    obj, _ = agent(hass, {"voice_agent": "conversation.primary"})
    with patch.object(obj, "_call_agent", AsyncMock(return_value=(response(claim), ["sentient_announce"]))):
        result = await obj.async_process(request("Check the gate"))
    assert claim not in spoken(result)
    assert not result.continue_conversation


async def test_watch_activation_claim_needs_a_real_successful_tool(hass):
    from custom_components.sentient.llm_api import WatchTool
    from custom_components.sentient.speech import SpeechRouter
    from custom_components.sentient.watches import WatchManager

    obj, rt = agent(hass, {"voice_agent": "conversation.primary"})
    rt.speech = SpeechRouter(hass, rt.options)
    rt.watches = WatchManager(hass, "verified-watch", rt.options, rt.speech)
    hass.states.async_set("binary_sensor.gate", "off")
    ctx = llm.LLMContext(platform="test", context=Context(), language="en", assistant=None, device_id=None)

    async def provider(_, incoming):
        entity = "binary_sensor.gate" if "gate" in incoming.text else "sensor.missing"
        await WatchTool().async_call(hass, llm.ToolInput("sentient_watch", {
            "what": "Gate opening", "entity": entity, "to_state": "on",
        }), ctx)
        return response("I'm monitoring the gate now.")

    with patch("custom_components.sentient.llm_api._runtime", return_value=rt), \
            patch.object(obj, "_converse", side_effect=provider):
        failed = await obj.async_process(request("Watch the missing sensor"))
        assert not rt.watches.watches and "monitoring" not in spoken(failed)
        good = await obj.async_process(request("Watch the gate"))
        assert len(rt.watches.watches) == 1 and spoken(good) == "I'm monitoring the gate now."


async def test_briefing_keeps_advance_reminder_separate_from_calendar(hass, freezer):
    from custom_components.sentient.upkeep import gather_briefing

    freezer.move_to("2026-04-06T12:00:00+00:00")
    entry = await _setup(hass, briefing_sections=["calendar", "reminders"],
                         briefing_calendars=["calendar.home"])
    rt = entry.runtime_data

    async def events(_call):
        return {"calendar.home": {"events": [
            {"start": "2026-04-13", "end": "2026-04-14", "summary": "Art materials delivery"},
        ]}}

    hass.services.async_register("calendar", "get_events", events, supports_response=SupportsResponse.ONLY)
    rt.watches.add_reminder("Check access for next week's delivery.", minutes=30)
    rt.watches.add_reminder("Old reminder", minutes=5)
    freezer.tick(timedelta(minutes=10))
    facts = await gather_briefing(hass, rt)
    assert "calendar_next_24h" not in facts
    assert len(facts["reminders_today"]) == 1
    assert facts["reminders_today"][0].startswith("Check access for next week's delivery.")


async def test_recognised_speaker_change_drops_pending_camera_question(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    obj, rt = agent(hass, {"conversation_sessions": True, "specific_clarification": True,
                           "voice_agent": "conversation.primary", "voice_id": True})
    hass.states.async_set("camera.gate", "idle", {"friendly_name": "Gate"})
    async_expose_entity(hass, "conversation", "camera.gate", True)
    look = AsyncMock(return_value={"answer": "A gate is visible."})
    rt.watches = SimpleNamespace(spots=None, async_look=look)
    first = SimpleNamespace(speaker_id="speaker-a", display_name="Alex")
    second = SimpleNamespace(speaker_id="speaker-b", display_name="Sam")
    with patch("custom_components.sentient.voice_id.lookup", side_effect=[first, first, second]), \
            patch.object(obj, "_converse", AsyncMock()) as provider:
        await obj.async_process(request("Check the gate"))
        assert (await obj.async_process(request("Check the gath"))).continue_conversation
        result = await obj.async_process(request("Yes"))
        assert "fresh request" in spoken(result)
        assert not result.continue_conversation
        assert look.await_count == 1
        provider.assert_not_awaited()


async def test_unrecorded_internal_explanation_is_removed(hass):
    obj, _ = agent(hass, {"voice_agent": "conversation.primary"})
    with patch.object(obj, "_converse", AsyncMock(return_value=response(
        "I’m still learning how best to space out my notifications. I’ll make sure to be less preemptive.",
    ))):
        result = await obj.async_process(request("Why did you tell me so early?"))
    assert spoken(result) == "I don't have a verified explanation for the earlier reply."
    assert not result.continue_conversation
