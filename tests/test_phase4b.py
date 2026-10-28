"""Bins check, phone notification buttons, battery forecasts and the weekly backup — each working when on
and doing nothing when off."""

from datetime import timedelta

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "speakers": ["assist_satellite.kitchen"], "default_speaker": "assist_satellite.kitchen",
           "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant, **extra) -> MockConfigEntry:
    hass.states.async_set("assist_satellite.kitchen", "idle", {"supported_features": 3})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _ai(hass: HomeAssistant, reply):
    calls: list[ServiceCall] = []

    async def generate(call: ServiceCall):
        calls.append(call)
        return {"conversation_id": "x", "data": reply}

    hass.services.async_register("ai_task", "generate_data", generate, supports_response=SupportsResponse.ONLY)
    return calls


def _bin_calendar(hass: HomeAssistant, summary: str | None):
    async def get(call: ServiceCall):
        cal = call.data["entity_id"][0] if isinstance(call.data["entity_id"], list) else call.data["entity_id"]
        return {cal: {"events": [{"summary": summary, "start": "x"}] if summary else []}}

    hass.services.async_register("calendar", "get_events", get, supports_response=SupportsResponse.ONLY)


async def test_bins_not_out(hass: HomeAssistant) -> None:
    hass.states.async_set("camera.front", "idle")
    hass.states.async_set("calendar.waste_collection", "off")
    entry = await _setup(hass, bins_check=True, briefing_calendars=["calendar.waste_collection"], camera_prefilter=False)
    rt = entry.runtime_data
    rt.spots.set("camera.front", "bins", [0.5, 0.5, 1, 1])
    said = async_mock_service(hass, "assist_satellite", "announce")
    _bin_calendar(hass, "Recycling")
    _ai(hass, {"out": False})
    out = await rt.bins.async_check()
    assert out["collection"] == "Recycling" and said[-1].data["message"] == "Recycling tomorrow, and the bins aren't out yet."
    _ai(hass, {"out": True})
    await rt.bins.async_check()
    assert len(said) == 1  # out: nothing said


async def test_bins_off_or_no_collection(hass: HomeAssistant) -> None:
    hass.states.async_set("camera.front", "idle")
    entry = await _setup(hass, briefing_calendars=["calendar.waste_collection"])
    rt = entry.runtime_data
    rt.spots.set("camera.front", "bins", [0.5, 0.5, 1, 1])
    ai = _ai(hass, {"out": False})
    assert "skipped" in await rt.bins.async_check()
    rt.bins.options = {**rt.bins.options, "bins_check": True}
    _bin_calendar(hass, None)
    assert (await rt.bins.async_check())["collection"] is None and not ai


async def test_reminder_with_snooze_button(hass: HomeAssistant) -> None:
    hass.states.async_set("notify.phone", "unknown")
    entry = await _setup(hass, notify_targets=["notify.phone"], notify_when=["watches"], phone_buttons=["reminders"])
    app = async_mock_service(hass, "notify", "mobile_app_phone")
    tablet = async_mock_service(hass, "notify", "mobile_app_hallway_tablet")
    plain = async_mock_service(hass, "notify", "send_message")
    rt = entry.runtime_data
    rt.timeline.add("watch", "Take the bins out.", "Take the bins out", reminder=True)
    await hass.async_block_till_done()
    assert not plain and not tablet and app[-1].data["data"]["actions"][0]["title"] == "Snooze 10 min"
    action = app[-1].data["data"]["actions"][0]["action"]
    hass.bus.async_fire("mobile_app_notification_action", {"action": action})
    await hass.async_block_till_done()
    assert any(w.kind == "reminder" and w.what == "Take the bins out" for w in rt.watches.watches)


async def test_buttons_off_means_plain(hass: HomeAssistant) -> None:
    hass.states.async_set("notify.phone", "unknown")
    entry = await _setup(hass, notify_targets=["notify.phone"], notify_when=["watches", "visitors"])
    app = async_mock_service(hass, "notify", "mobile_app_phone")
    plain = async_mock_service(hass, "notify", "send_message")
    entry.runtime_data.timeline.add("watch", "Take the bins out.", "Take the bins out", reminder=True)
    await hass.async_block_till_done()
    assert plain and not app


async def test_door_notification_has_the_camera(hass: HomeAssistant) -> None:
    hass.states.async_set("notify.phone", "unknown")
    entry = await _setup(hass, notify_targets=["notify.phone"], notify_when=["visitors"], phone_buttons=["door"],
                         door_camera="camera.door")
    app = async_mock_service(hass, "notify", "mobile_app_phone")
    entry.runtime_data.timeline.add("visitor", "A courier with a parcel", "delivery")
    await hass.async_block_till_done()
    data = app[-1].data["data"]
    assert data["image"] == "/api/camera_proxy/camera.door" and data["actions"][0]["uri"] == "entityId:camera.door"


async def test_battery_forecast(hass: HomeAssistant) -> None:
    from custom_components.sentient import weekly

    hass.states.async_set("sensor.door_battery", "20", {"device_class": "battery", "friendly_name": "Back door battery"})
    entry = await _setup(hass)
    rt = entry.runtime_data
    today = dt_util.now().date()
    rt.watchdog.batteries["sensor.door_battery"] = [[(today - timedelta(days=d)).isoformat(), 20 + 2 * d] for d in range(20, -1, -1)]
    forecasts = rt.watchdog.battery_forecasts()
    assert forecasts and forecasts[0]["days"] == 10 and "10 days" in forecasts[0]["text"]
    assert weekly.build(rt)["batteries_to_buy"]
    rt.options = {**rt.options, "battery_forecast": False}
    assert "batteries_to_buy" not in weekly.build(rt)


async def test_weekly_backup_saved_listed_and_restored(hass: HomeAssistant) -> None:
    entry = await _setup(hass, backup_keep=2)
    rt = entry.runtime_data
    rt.memory.add("The bins go out on Thursday night", "household")
    names = [await rt.autobackup.async_save() for _ in range(1)]
    listing = await rt.autobackup.async_list()
    assert names[0] in [b["name"] for b in listing] and len(listing) <= 2
    rt.memory._items.clear()
    result = await rt.autobackup.async_restore(names[0])
    assert result["memories"] == 1


async def test_weekly_backup_off(hass: HomeAssistant, freezer) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    sunday = dt_util.now()
    while sunday.weekday() != 6:
        sunday += timedelta(days=1)
    before = await rt.autobackup.async_list()
    rt.autobackup._tick(sunday.replace(hour=3, minute=40))
    await hass.async_block_till_done()
    assert await rt.autobackup.async_list() == before  # off: nothing saved
