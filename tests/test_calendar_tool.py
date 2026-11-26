"""The voice's calendar: days worked out in code, so an all-day event is never said a day early."""

from datetime import date, timedelta

from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN
from custom_components.sentient.focus import Focus
from custom_components.sentient.skills import describe_when

OPTIONS = {"persona": "Nova", "quiet_start": "00:00:00", "quiet_end": "00:00:00"}
MONDAY = date(2026, 11, 16)


def test_when_is_worked_out_in_words() -> None:
    # 16 Nov live: start 21st, end 22nd (exclusive) was said "Friday the twentieth to Saturday the twenty-first"
    assert describe_when({"start": "2026-11-21", "end": "2026-11-22"}, MONDAY) == "Saturday 21 November, all day"
    assert describe_when({"start": "2026-11-21", "end": "2026-11-23"}, MONDAY) == \
        "Saturday 21 November to Sunday 22 November, all day"
    assert describe_when({"start": "2026-11-17", "end": "2026-11-18"}, MONDAY) == "tomorrow, all day"
    begins = dt_util.as_local(dt_util.parse_datetime("2026-11-16T20:30:00+00:00"))
    timed = {"start": begins.isoformat(), "end": (begins + timedelta(minutes=45)).isoformat()}
    assert describe_when(timed, begins.date()) == f"today at {begins:%H:%M} until {begins + timedelta(minutes=45):%H:%M}"


async def _setup(hass: HomeAssistant, freezer):
    freezer.move_to("2026-11-16T08:00:00+00:00")
    await hass.config.async_set_time_zone("Europe/London")
    assert await async_setup_component(hass, "homeassistant", {})
    events = {"calendar.mine": [{"start": "2026-11-21", "end": "2026-11-22", "summary": "Work"},
                                {"start": "2026-11-17T18:00:00+00:00", "end": "2026-11-17T19:00:00+00:00",
                                 "summary": "Dentist", "location": "Ormskirk"}],
              "calendar.private": [{"start": "2026-11-18", "end": "2026-11-19", "summary": "Secret"}]}
    asked: list[dict] = []

    async def get_events(call) -> ServiceResponse:
        asked.append(dict(call.data))
        ids = call.data["entity_id"]
        return {e: {"events": events[e]} for e in (ids if isinstance(ids, list) else [ids])}

    hass.services.async_register("calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY)
    hass.states.async_set("calendar.mine", "off", {"friendly_name": "My calendar"})
    hass.states.async_set("calendar.private", "off", {"friendly_name": "Private"})
    async_expose_entity(hass, "conversation", "calendar.mine", True)
    async_expose_entity(hass, "conversation", "calendar.private", False)
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                               assistant=None, device_id=None))
    return api, asked


async def test_the_voice_reads_only_shared_calendars_with_days_worked_out(hass: HomeAssistant, freezer) -> None:
    api, asked = await _setup(hass, freezer)
    assert "sentient_calendar" in {t.name for t in api.tools}
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_calendar", tool_args={"days": 7}))
    assert asked[-1]["entity_id"] == ["calendar.mine"]  # the private one isn't shared with the voice
    assert out["today"] == "Monday 16 November"
    assert out["events"] == [
        {"calendar": "My calendar", "what": "Dentist", "when": "tomorrow at 18:00 until 19:00", "where": "Ormskirk"},
        {"calendar": "My calendar", "what": "Work", "when": "Saturday 21 November, all day"}]
    assert "say each event's 'when' exactly" in api.api_prompt


async def test_no_shared_calendar_no_tool(hass: HomeAssistant, freezer) -> None:
    api, _ = await _setup(hass, freezer)
    async_expose_entity(hass, "conversation", "calendar.mine", False)
    api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                               assistant=None, device_id=None))
    assert "sentient_calendar" not in {t.name for t in api.tools}


def test_calendar_questions_get_the_calendar_tool() -> None:
    focus = Focus()
    for asked in ("what's on my calendar this week", "what's on tomorrow", "any plans this weekend"):
        assert "sentient_calendar" in (focus.tools_for(asked, asked) or []), asked


async def test_calendar_tool_does_not_fetch_exposed_but_unreadable_calendars(hass: HomeAssistant, freezer) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from homeassistant.core import Context

    _, asked = await _setup(hass, freezer)
    user = SimpleNamespace(is_active=True, permissions=SimpleNamespace(check_entity=lambda *_: False))
    with patch.object(hass.auth, "async_get_user", AsyncMock(return_value=user)):
        api = await llm.async_get_api(hass, DOMAIN, llm.LLMContext(
            platform="t", context=Context(user_id="limited"), language="en", assistant=None, device_id=None,
        ))
        result = await api.async_call_tool(llm.ToolInput(tool_name="sentient_calendar", tool_args={}))
    assert result["events"] == []
    assert not asked
