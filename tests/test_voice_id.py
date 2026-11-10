"""Sentient Voice Recognition link: options only when it is installed, and the speaker reaches the prompt."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers import llm
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient import voice_id
from custom_components.sentient.const import DOMAIN
from custom_components.sentient.speaker_identity import SPEAKER_FOR_TURN, VOICE_RECOGNITION_DOMAIN
from tests.test_books_recipes import _setup
from tests.test_voice_sessions_control import agent, request, response


def _install(hass: HomeAssistant, state=ConfigEntryState.LOADED) -> None:
    e = MockConfigEntry(domain=VOICE_RECOGNITION_DOMAIN)
    e.add_to_hass(hass)
    e.mock_state(hass, state)


def test_lines_name_the_speaker_or_say_it_is_unknown_or_cautious() -> None:
    alex = SimpleNamespace(speaker_id="a", display_name="Alex", share_name=True)
    nobody = SimpleNamespace(speaker_id=None, display_name=None)
    assert voice_id.prompt_lines({}, None) == []
    assert "Alex" in voice_id.prompt_lines({}, alex)[0] and "not proof" in voice_id.prompt_lines({}, alex)[0]
    assert "house rules say not to address people by name" in voice_id.prompt_lines({}, alex)[0]
    assert not voice_id.cautious({"voice_id_unknown": True}, alex)
    assert not voice_id.cautious({}, nobody)
    assert voice_id.cautious({"voice_id_unknown": True}, nobody)
    assert any("guest" in x for x in voice_id.prompt_lines({"voice_id_unknown": True}, nobody))
    assert not any("guest" in x for x in voice_id.prompt_lines({}, nobody))


async def test_lookup_needs_the_setting_and_a_running_integration(hass: HomeAssistant) -> None:
    find = Mock(return_value=SimpleNamespace(speaker_id="a", display_name="Alex", share_name=True))
    hass.data[SPEAKER_FOR_TURN] = find
    assert voice_id.lookup(hass, {"voice_id": True}, "req") is None  # not installed
    _install(hass, ConfigEntryState.NOT_LOADED)
    assert not voice_id.installed(hass) and voice_id.lookup(hass, {"voice_id": True}, "req") is None
    hass.config_entries.async_entries(VOICE_RECOGNITION_DOMAIN)[0].mock_state(hass, ConfigEntryState.LOADED)
    assert voice_id.lookup(hass, {}, "req") is None  # setting off
    find.assert_not_called()
    assert voice_id.name_of(voice_id.lookup(hass, {"voice_id": True}, "req")) == "Alex"
    assert hass.data[voice_id.LAST_KEY]["name"] == "Alex"
    find.side_effect = RuntimeError("add-on fault")
    assert voice_id.lookup(hass, {"voice_id": True}, "req") is None  # never stops an answer


async def test_conversation_binds_the_speaker_for_the_turn_only(hass: HomeAssistant) -> None:
    _install(hass)
    obj, _ = agent(hass, {"voice_id": True})
    hass.data[SPEAKER_FOR_TURN] = Mock(return_value=SimpleNamespace(speaker_id="a", display_name="Alex", share_name=True))
    seen = []

    async def turn(_request):
        seen.append(voice_id.name_of(voice_id.current_speaker.get()))
        return response()

    with patch.object(obj, "_recoverable_turn", side_effect=turn):
        await obj.async_process(request("turn on the lamp"))
    assert seen == ["Alex"] and voice_id.current_speaker.get() is None


async def test_prompt_carries_the_speaker_and_unknown_voices_can_be_guests(hass: HomeAssistant, tmp_path) -> None:
    entry, _ = await _setup(hass, tmp_path, voice_id=True, voice_id_unknown=True)
    api = llm.async_get_api  # noqa: F841
    from custom_components.sentient.llm_api import SentientAPI

    ctx = llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None)
    token = voice_id.current_speaker.set(SimpleNamespace(speaker_id="a", display_name="Alex", share_name=True))
    try:
        prompt = SentientAPI(hass)._prompt(ctx)
    finally:
        voice_id.current_speaker.reset(token)
    assert "probably Alex" in prompt and "This room is private" not in prompt
    token = voice_id.current_speaker.set(SimpleNamespace(speaker_id=None, display_name=None))
    try:
        prompt = SentientAPI(hass)._prompt(ctx)
    finally:
        voice_id.current_speaker.reset(token)
    assert "wasn't matched" in prompt and "Treat this voice as a guest" in prompt and "Things you know about" not in prompt
    assert "probably" not in SentientAPI(hass)._prompt(ctx)  # no result: nothing about a speaker


async def test_settings_tab_offers_download_until_installed(hass: HomeAssistant, tmp_path, hass_ws_client) -> None:
    await _setup(hass, tmp_path)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/settings"})
    tab = next(g for g in (await ws.receive_json())["result"]["groups"] if g["id"] == "voiceid")
    assert tab["schema"] == [] and tab["voice_id"]["installed"] is False
    assert "sentient-voice-recognition" in tab["voice_id"]["repo"] and "hacs_repository" in tab["voice_id"]["hacs"]
    _install(hass)
    await ws.send_json({"id": 2, "type": "sentient/settings"})
    tab = next(g for g in (await ws.receive_json())["result"]["groups"] if g["id"] == "voiceid")
    assert {f["name"] for f in tab["schema"]} == {"voice_id", "voice_id_unknown"} and tab["voice_id"]["installed"]
    await ws.send_json({"id": 3, "type": "sentient/voice_id"})
    assert (await ws.receive_json())["result"]["installed"] is True


async def test_latest_turn_report_says_why_and_reaches_the_page_status(hass: HomeAssistant, tmp_path) -> None:
    await _setup(hass, tmp_path, voice_id=True)
    _install(hass)
    hass.bus.async_fire("sentient_voice_recognition_turn", {"speaker_name": None, "reason": "below_threshold",
                                                            "similarity": 0.52, "runner_up_gap": None})
    await hass.async_block_till_done()
    heard = voice_id.status(hass, {})["heard"]
    assert heard["name"] is None and heard["reason"] == "below_threshold" and heard["similarity"] == 0.52
    assert "below the match level" in heard["why"]
    hass.bus.async_fire("sentient_voice_recognition_turn", {"speaker_name": "Alex", "reason": "matched", "similarity": 0.8})
    await hass.async_block_till_done()
    assert voice_id.status(hass, {})["heard"]["name"] == "Alex"


async def test_recent_turns_are_only_kept_while_the_setting_is_on_and_the_add_on_runs(hass: HomeAssistant) -> None:
    event = SimpleNamespace(data={"speaker_name": "Alex", "reason": "matched", "similarity": 0.8, "runner_up_gap": 0.3})
    voice_id.record_turn(hass, event, {"voice_id": True})  # add-on not installed: nothing kept
    assert voice_id.status(hass, {"voice_id": True})["recent"] == []
    _install(hass)
    voice_id.record_turn(hass, event, {"voice_id": False})  # setting off: nothing kept
    assert voice_id.status(hass, {"voice_id": True})["recent"] == []
    for _ in range(12):
        voice_id.record_turn(hass, event, {"voice_id": True})
    recent = voice_id.status(hass, {"voice_id": True})["recent"]
    assert len(recent) == voice_id.RECENT_MAX and recent[0]["name"] == "Alex"


def test_a_first_person_memory_is_filed_under_the_matched_speaker_only() -> None:
    alex = SimpleNamespace(speaker_id="a", display_name="Alex", share_name=True)
    nobody = SimpleNamespace(speaker_id=None, display_name=None)
    on = {"voice_id": True}
    assert voice_id.attribute(on, alex, "remember that I like tea", "likes tea") == "Alex: likes tea"
    assert voice_id.attribute(on, alex, "remember that my mum visits on Sunday", "mum visits on Sunday") == "Alex: mum visits on Sunday"
    assert voice_id.attribute(on, alex, "remember the bins go out on Tuesday", "bins go out on Tuesday") == "bins go out on Tuesday"
    assert voice_id.attribute(on, alex, "remember that I like tea", "Alex likes tea") == "Alex likes tea"
    assert voice_id.attribute(on, nobody, "remember that I like tea", "likes tea") == "likes tea"
    assert voice_id.attribute({}, alex, "remember that I like tea", "likes tea") == "likes tea"


async def test_enrol_commands_refuse_when_the_add_on_is_missing(hass: HomeAssistant) -> None:
    call = AsyncMock(return_value={"sentence": "x", "minutes": 10})
    hass.services.async_register(VOICE_RECOGNITION_DOMAIN, "start_speaker_enrolment", call, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(VOICE_RECOGNITION_DOMAIN, "cancel_speaker_enrolment", call)
    if True:
        try:
            await voice_id.start_enrolment(hass, "Alex", "assist_satellite.kitchen")
        except ValueError:
            pass
        else:
            raise AssertionError("should refuse when the add-on is not running")
        call.assert_not_called()
        await voice_id.cancel_enrolment(hass)
        call.assert_not_called()
        _install(hass)
        await voice_id.start_enrolment(hass, "Alex", "assist_satellite.kitchen")
        assert call.await_count == 1 and call.call_args.args[0].data["name"] == "Alex"


def _heard(sim, reason="below_threshold"):
    return {"reason": reason, "similarity": sim, "name": None}


def test_match_level_advice_only_when_several_recent_voices_just_missed_it() -> None:
    detail = {"threshold": 0.65}
    assert voice_id.advice(None, [_heard(0.6)] * 5) is None
    assert voice_id.advice(detail, [_heard(0.6)] * 2) is None  # too few to say anything
    assert voice_id.advice(detail, [_heard(0.2)] * 5) is None  # nowhere near: not a near miss
    assert voice_id.advice(detail, [_heard(0.6, "matched")] * 5) is None
    out = voice_id.advice(detail, [_heard(0.6), _heard(0.58), _heard(0.62)])
    assert out["value"] == 0.58 and out["turns"] == 3 and "wrong name" in out["text"]
    assert voice_id.advice({"threshold": 0.5}, [_heard(0.49)] * 3) is None  # never below the supported floor


def test_health_rows_say_what_is_wrong_and_stay_quiet_when_fine() -> None:
    assert voice_id.health_rows(None)[0][1] == "warn"
    down = voice_id.health_rows({"engine": False, "profiles": [{"name": "Alex", "nearest": None, "nearest_score": None}]})
    assert down[0][1] == "bad" and "engine" in down[0][2]
    assert any("No voices are saved" in r[2] for r in voice_id.health_rows({"engine": True, "profiles": []}))
    alike = voice_id.health_rows({"engine": True, "profiles": [{"name": "Alex", "nearest": "Sam", "nearest_score": 0.85}]})
    assert "Alex and Sam" in alike[0][2]
    fine = voice_id.health_rows({"engine": True, "profiles": [{"name": "Alex", "nearest": None, "nearest_score": None}]})
    assert [r[1] for r in fine] == ["ok"]


async def test_house_check_has_no_voice_rows_unless_the_add_on_is_running_and_switched_on(hass: HomeAssistant, tmp_path) -> None:
    from custom_components.sentient.doctor import async_check

    entry, _ = await _setup(hass, tmp_path, voice_id=True)
    rt = entry.runtime_data
    assert not any(r["area"] == "Voice" and "oice" in r["name"] and r["name"] != "Voice pipeline"
                   for r in (await async_check(hass, rt))["rows"])  # not installed
    _install(hass)
    hass.services.async_register(VOICE_RECOGNITION_DOMAIN, "status", AsyncMock(return_value={"engine": True, "profiles": []}),
                                 supports_response=SupportsResponse.ONLY)
    rows = (await async_check(hass, rt))["rows"]
    assert any(r["name"] == "Voice profiles" for r in rows)
    rt.options = {**rt.options, "voice_id": False}
    rows = (await async_check(hass, rt))["rows"]
    assert not any(r["name"] == "Voice profiles" for r in rows)  # switched off


async def test_matched_speaker_survives_remapping_and_unknown_voice_starts_fresh(hass):
    from custom_components.sentient.const import CONF_CARRY_ON_MINUTES

    _install(hass)
    obj, _ = agent(hass, {"voice_id": True, "voice_id_unknown": True, CONF_CARRY_ON_MINUTES: 2})
    current = None
    matched = SimpleNamespace(speaker_id="a", display_name="Alex")
    unknown = SimpleNamespace(speaker_id=None, display_name=None)
    seen = []

    def lookup(turn):
        if turn.conversation_id == current.conversation_id and turn.context is current.context:
            return unknown if turn.text == "guest question" else matched

    async def answer(turn):
        seen.append((turn.conversation_id, voice_id.current_speaker.get()))
        result = response()
        result.conversation_id = turn.conversation_id
        return result

    hass.data[SPEAKER_FOR_TURN] = lookup
    with patch.object(obj, "_recoverable_turn", side_effect=answer):
        for text, cid in (("first question", "first"), ("second question", "second"), ("guest question", "first")):
            current = request(text, cid=cid)
            await obj.async_process(current)
    assert seen == [("first", matched), ("first", matched), (None, unknown)]


async def test_unknown_voice_cannot_retrieve_household_history_or_memories(hass, tmp_path):
    from custom_components.sentient.llm_api import ConversationsTool, RecallTool

    entry, _ = await _setup(hass, tmp_path, voice_id=True, voice_id_unknown=True)
    entry.runtime_data.memory.add("Alex likes tea")
    ctx = llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None)
    token = voice_id.current_speaker.set(SimpleNamespace(speaker_id=None, display_name=None))
    try:
        history = await ConversationsTool().async_call(hass, llm.ToolInput(tool_name="sentient_conversations", tool_args={}), ctx)
        recall = await RecallTool().async_call(hass, llm.ToolInput(tool_name="sentient_recall", tool_args={"query": "tea"}), ctx)
    finally:
        voice_id.current_speaker.reset(token)
    assert "private" in history["error"]
    assert recall == {"memories": []}


async def test_local_only_speaker_stays_out_of_ai_prompt_and_memory_attribution(hass, tmp_path):
    from custom_components.sentient.focus import current_request
    from custom_components.sentient.llm_api import RememberTool, SentientAPI

    entry, _ = await _setup(hass, tmp_path, voice_id=True)
    ctx = llm.LLMContext(platform="t", context=None, language="en", assistant=None, device_id=None)
    request_token = current_request.set("remember that I like tea")
    token = voice_id.current_speaker.set(SimpleNamespace(speaker_id="a", display_name="Alex", share_name=False))
    tool = llm.ToolInput(tool_name="sentient_remember", tool_args={"text": "I like tea"})
    try:
        assert "probably Alex" not in SentientAPI(hass)._prompt(ctx)
        local = await RememberTool().async_call(hass, tool, ctx)
        assert local["saved"] == "I like tea"
        shared_token = voice_id.current_speaker.set(SimpleNamespace(speaker_id="a", display_name="Alex", share_name=True))
        try:
            assert "probably Alex" in SentientAPI(hass)._prompt(ctx)
            shared = await RememberTool().async_call(hass, tool, ctx)
        finally:
            voice_id.current_speaker.reset(shared_token)
        assert shared["saved"] == "Alex: I like tea"
        memory = next(item for item in entry.runtime_data.memory.items if item.id == shared["id"])
        assert memory.origin == "explicit"
    finally:
        current_request.reset(request_token)
        voice_id.current_speaker.reset(token)
