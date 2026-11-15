"""Where the voice went wrong: each voice turn checked with plain rules, for the Mind page."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.reliability import classify

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen"}


def _kinds(*args, **kw) -> list[str]:
    return [p["kind"] for p in classify(*args, **kw)]


def test_a_wrong_argument_put_right_on_a_retry_is_still_noted() -> None:
    """14 Nov: "Watch" failed with wrong arguments, then worked."""
    tools = [{"tool_name": "sentient_watch", "tool_result": {"error": "wrong arguments: not a valid option"}},
             {"tool_name": "sentient_watch", "tool_result": {"ok": True}}]
    found = classify("watch the rabbit pen for an hour", "I'll keep an eye on the rabbit pen.", tools, frozenset({"sentient_watch"}))
    assert [p["kind"] for p in found] == ["tool_failed"] and "put right on a retry" in found[0]["detail"]


def test_offering_to_feed_the_rabbits_is_noted() -> None:
    """14 Nov: it offered to feed the rabbits."""
    assert _kinds("are the rabbits ok?", "They look settled. Would you like me to feed the rabbits?",
                  [{"tool_name": "sentient_look", "tool_result": {"answer": "two rabbits"}}]) == ["offered_impossible"]
    assert _kinds("is the heating on?", "It is. Would you like me to turn it up?",
                  [{"tool_name": "GetLiveContext", "tool_result": {}}]) == []


def test_couldnt_answer_and_answering_the_past_without_looking() -> None:
    """14 Nov: "has anyone been to the rabbits?" — "no one" without looking at the camera history."""
    assert _kinds("has anyone been to the rabbits?", "No one has been to the rabbits today.", []) == ["no_evidence"]
    assert _kinds("has anyone been to the rabbits?", "I couldn't find any visits on the garden camera.",
                  [{"tool_name": "sentient_camera_activity", "tool_result": {"visits": []}}]) == ["unanswered"]
    assert _kinds("has anyone been to the rabbits?", "Yes, someone was there at 16:10.",
                  [{"tool_name": "sentient_camera_activity", "tool_result": {"visits": [1]}}]) == []
    assert _kinds("what's the weather like?", "Cold and clear.", []) == []  # now, not the past


def test_saying_its_done_when_nothing_was() -> None:
    assert _kinds("turn off the landing light", "Done, I've turned off the landing light.", []) == ["claimed_done"]
    done = [{"tool_name": "HassTurnOff", "tool_result": {"data": {"success": [{"name": "Landing light"}], "failed": []}}}]
    assert _kinds("turn off the landing light", "Done, I've turned off the landing light.", done) == []
    half = [{"tool_name": "HassTurnOff", "tool_result": {"data": {"success": [{"name": "Hall"}], "failed": [{"name": "Landing"}]}}}]
    assert _kinds("turn off the hall and landing", "I've turned them off.", half) == ["action_failed"]


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", data={**OPTIONS, **extra}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_voice_turns_are_checked_and_shown_on_the_mind_page(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    conv = rt.conversations
    conv.attach("c1", None)
    conv._chat("c1", "content_added", {"content": {"role": "user", "content": "has the parcel come?"}})
    conv._chat("c1", "content_added", {"content": {"role": "assistant", "content": "Yes, it came this morning."}})
    conv._chat("c1", "content_added", {"content": {"role": "user", "content": "turn on the kitchen light"}})
    conv._chat("c1", "content_added", {"content": {"role": "tool_result", "tool_name": "HassTurnOn", "tool_result": {
        "data": {"success": [{"name": "Kitchen light"}], "failed": []}}}})
    conv._chat("c1", "content_added", {"content": {"role": "assistant", "content": "I've turned it on."}})
    # a background AI job that doesn't use Sentient isn't counted
    conv._chat("bg", "content_added", {"content": {"role": "user", "content": "did anything happen?"}})
    conv._chat("bg", "content_added", {"content": {"role": "assistant", "content": "Done."}})
    summary = rt.reliability.summary()
    assert summary["turns"] == 2 and summary["clean_turns"] == 1
    assert summary["recent"][0]["asked"] == "has the parcel come?"
    assert summary["recent"][0]["problems"][0]["kind"] == "no_evidence"
    mind = next(s for s in hass.states.async_all("sensor") if "reliability" in s.attributes)
    assert mind.attributes["reliability"]["enabled"] is True


async def test_switching_it_off_stops_it_and_clears_what_was_kept(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    conv = rt.conversations
    conv.attach("c1", None)
    conv._chat("c1", "content_added", {"content": {"role": "user", "content": "did the bins go out?"}})
    conv._chat("c1", "content_added", {"content": {"role": "assistant", "content": "They did."}})
    assert rt.reliability.records
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "reliability_log": False})
    await hass.async_block_till_done()
    rt = entry.runtime_data
    assert not rt.reliability.records and rt.reliability.summary() == {"enabled": False}
    rt.conversations.attach("c2", None)
    rt.conversations._chat("c2", "content_added", {"content": {"role": "user", "content": "did the bins go out?"}})
    rt.conversations._chat("c2", "content_added", {"content": {"role": "assistant", "content": "They did."}})
    assert not rt.reliability.records


def test_a_script_that_reports_success_did_something() -> None:
    """15 Nov: "Show camera on Echo Show" ran and put the doorbell up, but it was logged as "said it was done, but
    nothing was done" — a script's {"success": true} wasn't counted as an action."""
    from custom_components.sentient.reliability import classify

    shown = {"tool_name": "jarvis_show_camera_on_echo_show", "tool_result": {"success": True, "result": None}}
    assert classify("the doorbell camera", "I've put the doorbell feed up on the screen.", [shown]) == []
    failed = {"tool_name": "jarvis_show_camera_on_echo_show", "tool_result": {"success": False, "message": "no"}}
    kinds = [p["kind"] for p in classify("the doorbell camera", "I've put it on the screen.", [failed])]
    assert "claimed_done" in kinds and "tool_failed" in kinds


async def test_a_relayed_answer_is_counted_once_keeping_the_tools(hass: HomeAssistant) -> None:
    """15 Nov: Sentient Voice Recognition passes the request to this agent, then relays the answer in its own
    conversation — every voice request was counted twice, the relayed copy with no tools."""
    entry = await _setup(hass)
    rt = entry.runtime_data
    conv = rt.conversations
    for cid, tools in (("inner", True), ("outer", False)):
        conv.attach(cid, None)
        conv._chat(cid, "content_added", {"content": {"role": "user", "content": "The doorbell camera."}})
        if tools:
            conv._chat(cid, "content_added", {"content": {"role": "tool_result", "tool_name": "jarvis_show_camera",
                                                          "tool_result": {"success": True}}})
        conv._chat(cid, "content_added", {"content": {"role": "assistant",
                                                      "content": "I've put the doorbell feed up on the screen."}})
    summary = rt.reliability.summary()
    assert summary["turns"] == 1 and summary["recent"] == []
    # the other way round: the relayed copy first, then the real one — the real one's tools are what's judged
    conv.attach("outer2", None)
    conv._chat("outer2", "content_added", {"content": {"role": "user", "content": "did anyone come?"}})
    conv._chat("outer2", "content_added", {"content": {"role": "assistant", "content": "Nobody came."}})
    assert rt.reliability.summary()["recent"][0]["problems"][0]["kind"] == "no_evidence"
    conv.attach("inner2", None)
    conv._chat("inner2", "content_added", {"content": {"role": "user", "content": "did anyone come?"}})
    conv._chat("inner2", "content_added", {"content": {"role": "tool_result", "tool_name": "sentient_visitors",
                                                       "tool_result": {"visitors": []}}})
    conv._chat("inner2", "content_added", {"content": {"role": "assistant", "content": "Nobody came."}})
    summary = rt.reliability.summary()
    assert summary["turns"] == 2 and summary["recent"] == []


async def test_the_voice_is_told_to_look_things_up_not_reuse_answers(hass: HomeAssistant) -> None:
    from custom_components.sentient.security_voice import CameraActivityTool

    assert "when did you last see the rabbits" in CameraActivityTool.description
    assert "not from an earlier answer" in CameraActivityTool.description


def test_action_tool_without_completion_evidence_is_not_a_success() -> None:
    pending = {"tool_name": "sentient_watch", "tool_result": {"status": "pending"}}
    assert "unverified" in _kinds(
        "watch the garden", "Done, I'm watching.", [pending],
        actions=frozenset({"sentient_watch"}),
    )
    no_targets = {"tool_name": "HassTurnOn", "tool_result": {"data": {"success": [], "failed": []}}}
    assert "claimed_done" in _kinds("turn on the light", "Done.", [no_targets])


def test_unrelated_tool_does_not_count_as_historical_evidence() -> None:
    unrelated = {"tool_name": "HassGetState", "tool_result": {"state": "on"}}
    assert "no_evidence" in _kinds(
        "when was the gate last opened?", "It was opened earlier.", [unrelated])
    supported = {"tool_name": "sentient_history", "tool_result": {"changes": []}}
    assert "no_evidence" not in _kinds(
        "when was the gate last opened?", "No history found.", [supported])


async def test_independent_identical_turns_are_not_deduplicated(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    for cid in ("separate1", "separate2"):
        rt.conversations.attach(cid, None)
        rt.conversations._chat(cid, "content_added", {"content": {"role": "user", "content": "is the light on?"}})
        rt.conversations._chat(cid, "content_added", {"content": {"role": "assistant", "content": "Yes."}})
    assert rt.reliability.summary()["turns"] == 2


async def test_unified_evidence_is_optional_and_preserves_source_gaps(hass: HomeAssistant) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch
    from custom_components.sentient.llm_api import SentientAPI, UnifiedEvidenceTool
    from homeassistant.core import Context
    from homeassistant.helpers import llm

    entry = await _setup(hass)
    rt = entry.runtime_data
    ctx = llm.LLMContext(platform="test", context=Context(), language="en",
                         assistant="conversation", device_id=None)
    api = await SentientAPI(hass).async_get_api_instance(ctx)
    assert "sentient_evidence" not in {t.name for t in api.tools}
    rt.options["unified_evidence"] = True
    api = await SentientAPI(hass).async_get_api_instance(ctx)
    assert "sentient_evidence" in {t.name for t in api.tools}

    tool = UnifiedEvidenceTool()
    with patch("custom_components.sentient.llm_api.TimelineTool.async_call",
               new_callable=AsyncMock, return_value={"events": [{"what": "door opened"}]}) as timeline, \
         patch("custom_components.sentient.security_voice.CameraActivityTool.async_call",
               new_callable=AsyncMock, return_value={"error": "not exposed"}) as camera, \
         patch.object(rt.brain, "on", return_value=False):
        got = await tool.async_call(
            hass, SimpleNamespace(tool_args={"camera": "garden", "hours": 2}), ctx)
    data = got.data if hasattr(got, "data") else got
    assert data["sources"]["timeline"]["available"] is True
    assert data["sources"]["ledger"]["available"] is False
    assert data["sources"]["camera_activity"]["available"] is False
    assert "not exposed" in data["sources"]["camera_activity"]["reason"]
    timeline.assert_awaited_once()
    camera.assert_awaited_once()


async def test_identical_turns_on_different_devices_remain_distinct(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    log = entry.runtime_data.reliability
    for cid, device, has_tools in (("first", "satellite.one", True),
                                   ("second", "satellite.two", False)):
        log.asked(cid, "did anyone visit?")
        if has_tools:
            log.tool(cid, {"tool_name": "sentient_visitors", "tool_result": {"visitors": []}})
        log.answered(cid, "No one visited.", room="Livingroom", device_id=device)
    assert log.summary()["turns"] == 2


def test_failed_historical_lookup_does_not_count_as_evidence() -> None:
    failed = {"tool_name": "sentient_history", "tool_result": {"error": "ledger unavailable"}}
    assert "no_evidence" in _kinds("when did the door open?", "It was at 2 am.", [failed])
