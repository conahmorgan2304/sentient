"""Calendar responses must not announce dates outside the requested window."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.calendar_window import window_events
from custom_components.sentient.const import DOMAIN
from custom_components.sentient.skills import async_calendar_events, async_calendar_clashes
from custom_components.sentient.upkeep import gather_briefing, plain_briefing


async def test_window_dates_offsets_and_exclusive_end(hass: HomeAssistant):
    await hass.config.async_set_time_zone("Europe/London")
    start = datetime(2026, 7, 1, 8, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    events = [
        {"summary": "Next week", "start": "2026-07-08", "end": "2026-07-09"},
        {"summary": "Tomorrow early", "start": "2026-07-02T07:00:00+00:00", "end": "2026-07-02T07:30:00+00:00"},
        {"summary": "Yesterday", "start": "2026-06-30", "end": "2026-07-01"},
        {"summary": "Ongoing all day", "start": "2026-06-30", "end": "2026-07-02"},
        {"summary": "Today", "start": "2026-07-01T10:00:00+02:00", "end": "2026-07-01T11:00:00+02:00"},
        {"summary": "At boundary", "start": end.isoformat()},
        {"summary": "Malformed", "start": "invalid"},
    ]
    assert [e["summary"] for e in window_events(events, start, end)] == [
        "Ongoing all day", "Today", "Tomorrow early"
    ]


async def test_briefing_and_calendar_consumers_reject_next_week(hass: HomeAssistant, freezer):
    await hass.config.async_set_time_zone("Europe/London")
    freezer.move_to("2026-11-26T08:00:00+00:00")
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "briefing_sections": ["calendar"],
        "briefing_calendars": ["calendar.family"], "bins_calendar": "calendar.family",
        "calendar_clashes": True,
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    rt = entry.runtime_data
    events = [
        {"summary": "Recycling next week", "start": "2026-12-03", "end": "2026-12-04"},
        {"summary": "Appointment", "start": "2026-11-26T12:00:00+00:00", "end": "2026-11-26T13:00:00+00:00"},
        {"summary": "All day", "start": "2026-11-26", "end": "2026-11-27"},
        {"summary": "Tomorrow", "start": "2026-11-27T07:00:00+00:00", "end": "2026-11-27T07:30:00+00:00"},
        {"summary": "Future clash", "start": "2026-12-03T09:00:00+00:00", "end": "2026-12-03T10:00:00+00:00"},
    ]

    async def get_events(call):
        return {"calendar.family": {"events": events}}

    hass.services.async_register("calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY)
    facts = await gather_briefing(hass, rt)
    assert facts["calendar_next_24h"] == [
        "All day (today, all day)", "Appointment (today at 12:00 until 13:00)",
        "Tomorrow (tomorrow at 07:00 until 07:30)",
    ]
    assert "Appointment" in plain_briefing(facts)
    voice = await async_calendar_events(hass, ["calendar.family"], days=1)
    assert [e["what"] for e in voice["events"]] == ["All day", "Appointment"]
    clashes = await async_calendar_clashes(hass, ["calendar.family"])
    assert clashes["events"] == 2
    forecast = await rt.foresight.gather()
    assert forecast["calendar"] == ["07:00 Tomorrow"]
    with patch.object(type(rt.bins), "calendars", return_value=["calendar.family"]):
        assert await rt.bins.async_collection_tomorrow() is None
    assert await rt.alarms._fill_calendar("{tomorrow:calendar.family}", dt_util.now()) == "Tomorrow"
