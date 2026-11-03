"""Self-awareness from records, manuals and goals — evidence-backed, optional, nothing changed in the house."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen", "assist_satellite.lounge"],
           "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"friendly_name": "Kitchen speaker"})
    hass.states.async_set("assist_satellite.lounge", "idle", {"friendly_name": "Lounge speaker"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _api(hass):
    return await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                               assistant=None, device_id=None))


async def test_self_check_finds_what_the_records_show(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass)
    tl = entry.runtime_data.timeline
    for n in range(4):  # the kettle remark, hushed straight after twice; the lounge speaker failing
        tl.add("said", "The kettle has finished boiling.", "Kitchen", source="notice",
               deliveries=[{"entity_id": "assist_satellite.kitchen", "status": "accepted"},
                           {"entity_id": "assist_satellite.lounge", "status": "failed" if n else "accepted"}])
        if n < 2:
            freezer.tick(timedelta(minutes=1))
            tl.add("mode", "Hushed until 18:00")
        freezer.tick(timedelta(hours=2))
    report = entry.runtime_data.selfaware.check()
    kinds = {f["kind"] for f in report["findings"]}
    assert {"chatty", "repeat", "speaker"} <= kinds
    speaker = next(f for f in report["findings"] if f["kind"] == "speaker")
    assert speaker["evidence"] == {"tries": 4, "failed": 3} and "Lounge speaker" in speaker["text"]
    api = await _api(hass)
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_self", tool_args={}))
    assert any(c["name"] == "AI" and c["status"] == "ok" for c in out["right_now"])
    assert any("Lounge speaker" in f["finding"] for f in out["noticed_about_itself"])


async def test_self_adjust_quietens_what_keeps_being_hushed_only_when_on(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass)
    tl, fb = entry.runtime_data.timeline, entry.runtime_data.feedback
    for _ in range(3):
        tl.add("said", "Fancy a brew?", "Kitchen", source="offer:Brew")
        freezer.tick(timedelta(minutes=1))
        tl.add("mode", "Hushed until 18:00")
        freezer.tick(timedelta(hours=3))
    entry.runtime_data.selfaware._nightly(None)
    assert fb.score("source:offer:Brew") == 0  # off by default: only noted
    hass.config_entries.async_update_entry(entry, options={**OPTIONS, "self_adjust": True})
    await hass.async_block_till_done()
    entry.runtime_data.selfaware._nightly(None)
    assert fb.score("source:offer:Brew") > 0 and entry.runtime_data.selfaware.last["adjusted"]


async def test_status_when_the_ai_is_down(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    hass.states.async_set("camera.garden", "idle", {"friendly_name": "Garden"})
    for _ in range(5):
        entry.runtime_data.ai.failed(RuntimeError("503"))
    status = {c["name"]: c for c in entry.runtime_data.selfaware.status()}
    assert status["AI"]["status"] == "limited" and status["Cameras"]["status"] == "limited"
    assert "can't describe pictures" in status["Cameras"]["why"]


async def test_manuals_pick_the_right_document(hass: HomeAssistant, tmp_path) -> None:
    from custom_components.sentient.manuals import pick

    assert pick(["Air purifier.pdf", "Boiler manual.pdf"], "what filter does the air purifier need") == "Air purifier.pdf"
    (tmp_path / "manuals").mkdir()
    (tmp_path / "manuals" / "Air purifier.pdf").write_bytes(b"%PDF-1.4")
    hass.config.media_dirs = {"local": str(tmp_path)}
    entry = await _setup(hass, manuals=True)
    seen = {}

    async def call(data, timeout=90):
        seen.update(data)
        return {"data": "It takes the H13 HEPA filter, page 12."}

    with patch.object(entry.runtime_data.ai, "async_call", side_effect=call):
        api = await _api(hass)
        out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_manual", tool_args={"question": "what filter does the air purifier need?"}))
    assert out == {"document": "Air purifier", "answer": "It takes the H13 HEPA filter, page 12."}
    assert seen["attachments"][0]["media_content_id"] == "media-source://media_source/local/manuals/Air purifier.pdf"


async def test_manuals_and_goals_off_by_default(hass: HomeAssistant) -> None:
    await _setup(hass)
    names = {t.name for t in (await _api(hass)).tools}
    assert "sentient_manual" not in names and "sentient_goal" not in names and "sentient_self" in names


async def test_goal_follows_a_sensor(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.heating_hours", "5", {"friendly_name": "Heating hours", "unit_of_measurement": "h"})
    entry = await _setup(hass, goals=True)
    with patch("custom_components.sentient.goals.daily_values", AsyncMock(return_value=[("2026-11-01", 6.0), ("2026-10-31", 5.0)])):
        api = await _api(hass)
        out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_goal", tool_args={
            "action": "set", "title": "Use less heating this month", "entity": "sensor.heating_hours", "aim": "lower", "days": 30}))
        assert out["usual_per_day"] == 5.5
    with patch("custom_components.sentient.goals.daily_values", AsyncMock(return_value=[("2026-11-02", 4.4)])):
        await entry.runtime_data.goals.async_check()
    listing = entry.runtime_data.goals.listing()
    assert listing[0]["progress"].startswith("20% lower than usual") and "right way" in listing[0]["progress"]
    assert "Use less heating this month (20% lower" in (await _api(hass)).api_prompt
    closed = await api.async_call_tool(llm.ToolInput(tool_name="sentient_goal", tool_args={"action": "close", "title": "heating"}))
    assert closed["closed"] == "Use less heating this month"


def test_asking_the_time_is_not_a_finding() -> None:
    from custom_components.sentient.selfaware import TRIVIAL

    assert TRIVIAL.search("what time is it") and TRIVIAL.search("What's the time?")
    assert not TRIVIAL.search("what's the temperature outside?")


async def test_once_a_day_and_a_fresh_restart_are_not_problems(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    for _ in range(7):  # the kettle each morning for a week
        rt.timeline.add("said", "The kettle has finished boiling.", "Kitchen", source="notice")
        freezer.tick(timedelta(days=1))
    assert not [f for f in rt.selfaware.check()["findings"] if f["kind"] == "repeat"]
    hass.states.async_set("camera.tablet", "unavailable", {"friendly_name": "Tablet Camera"})
    cams = next(c for c in rt.selfaware.status() if c["name"] == "Cameras")
    assert "Tablet Camera" not in cams["why"]  # it has only just dropped (a restart, a tablet waking)
    freezer.tick(timedelta(minutes=11))
    cams = next(c for c in rt.selfaware.status() if c["name"] == "Cameras")
    assert cams["status"] == "limited" and "Tablet Camera" in cams["why"]


def test_self_test_understands_every_way_of_saying_none() -> None:
    from custom_components.sentient.selftest import NONE_SAID

    for said in ("You don't have any reminders set right now.", "You do not have any reminders.",
                 "There aren't any reminders.", "You’ve not got any reminders.", "No reminders today.", "All clear."):
        assert NONE_SAID.search(said), said
    for said in ("You have two reminders: the bins and the dentist.", "Your reminder is the dentist at three."):
        assert not NONE_SAID.search(said), said
