"""Phase 1: the last hour in the voice's context, camera answer cache, picture pre-filter, per-feature
AI allowances and routine evidence — each working when on, and doing nothing when off."""

from datetime import timedelta

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN

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


def _ai(hass: HomeAssistant, reply):
    calls: list[ServiceCall] = []

    async def generate(call: ServiceCall):
        calls.append(call)
        return {"conversation_id": "x", "data": reply}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    return calls


async def test_last_hour_in_the_voice_context(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    entry.runtime_data.timeline.add("said", "Someone has opened the gate.", "hallway")
    entry.runtime_data.timeline.add("held", "Kept quiet about the kettle.", "quiet hours")
    prompt = await _prompt(hass)
    assert "In the last hour" in prompt and "Someone has opened the gate." in prompt
    assert "Kept quiet about the kettle." not in prompt  # what it held back isn't news


async def test_last_hour_off(hass: HomeAssistant) -> None:
    entry = await _setup(hass, prompt_recent=False)
    entry.runtime_data.timeline.add("said", "Someone has opened the gate.", "hallway")
    prompt = await _prompt(hass)
    assert "In the last hour" not in prompt and "Movement lately" not in prompt


async def test_camera_answer_reused_then_off(hass: HomeAssistant, freezer) -> None:
    hass.states.async_set("camera.garden", "idle", {"friendly_name": "Garden"})
    entry = await _setup(hass)
    ai = _ai(hass, "The garden's empty.")
    watches = entry.runtime_data.watches
    await watches.async_look("garden", "Is anyone in the garden?")
    out = await watches.async_look("garden", "is anyone in the garden")
    assert len(ai) == 1 and out["answer"] == "The garden's empty." and "seen_minutes_ago" in out
    freezer.tick(timedelta(minutes=4))  # older than the 3-minute default: look again
    await watches.async_look("garden", "Is anyone in the garden?")
    assert len(ai) == 2
    watches.options = {**watches.options, "look_cache_minutes": 0}  # off: always look
    await watches.async_look("garden", "Is anyone in the garden?")
    assert len(ai) == 3


async def test_camera_check_skipped_when_the_picture_hasnt_changed(hass: HomeAssistant) -> None:
    hass.states.async_set("camera.front", "idle", {"friendly_name": "Front"})
    entry = await _setup(hass)
    rt = entry.runtime_data
    ai = _ai(hass, {"match": False, "description": "an empty road"})
    same = [10] * 300 + [200] * 468

    async def fingerprint(camera, key, whole=False):
        return same

    rt.spots.async_fingerprint = fingerprint
    check = {"id": "c1", "name": "Delivery van", "camera": "camera.front", "question": "Is there a delivery van?"}
    first = await rt.checks.async_check(check)
    second = await rt.checks.async_check(check)
    assert len(ai) == 1 and "skipped" in second and "match" in first
    assert rt.watches.skipped == 1
    rt.watches.options = {**rt.watches.options, "camera_prefilter": False}  # off: always looks
    await rt.checks.async_check(check)
    assert len(ai) == 2


async def test_each_feature_can_have_its_own_allowance(hass: HomeAssistant) -> None:
    entry = await _setup(hass, budget_drive=2)
    gate = entry.runtime_data.ai
    assert gate.spend("drive") and gate.spend("drive")
    assert not gate.spend("drive")  # its own allowance is used up…
    assert gate.spend("noticing")  # …the rest carry on
    assert gate.spend("drive", force=True)  # someone asked: always
    assert gate.describe()["budgets"]["drive"] == {"used": 3, "cap": 2}
    gate.options = {**gate.options, "budget_drive": 0}  # off: shares the overall limit again
    assert gate.spend("drive")


async def test_routine_offers_say_how_often(hass: HomeAssistant) -> None:
    from custom_components.sentient.learning import Routine

    r = Routine("light.landing", "landing light", "on", 19 * 60, "at weekends", 1.0, 4, 3.0)
    assert r.evidence() == "4 of the last 4 weekend days"
    entry = await _setup(hass)
    learner = entry.runtime_data.learner
    learner.routines = {r.id: r}
    asked: list[str] = []

    async def ask(question, *args, **kwargs):
        asked.append(question)
        from custom_components.sentient.speech import SpeechResult

        return SpeechResult(True, "spoken", question, [])

    learner.speech.async_ask = ask
    learner.tracker = None
    await learner._async_offer(dt_util.now())
    assert "4 of the last 4 weekend days" in asked[-1]
    r.suggested = None
    learner.options = {**learner.options, "routine_evidence": False}
    await learner._async_offer(dt_util.now())
    assert "of the last" not in asked[-1]


def test_clock_words_and_self_test_hears_them() -> None:
    from datetime import datetime

    from custom_components.sentient.llm_api import clock_words
    from custom_components.sentient.selftest import spoken_times

    for hh, mm, words in [(11, 49, "eleven forty-nine"), (9, 5, "nine oh five"), (15, 0, "three o'clock"), (0, 12, "twelve twelve")]:
        said = clock_words(datetime(2026, 10, 28, hh, mm))
        assert said == words
        assert (hh % 12) * 60 + mm in spoken_times(f"It's {said}.")
