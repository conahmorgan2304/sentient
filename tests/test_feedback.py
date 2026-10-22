"""Learning from feedback: shushes, hushes after remarks and declined offers."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sentient.const import DOMAIN


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    hass.states.async_set("sensor.washer", "idle", {"friendly_name": "Washing machine"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": [], "quiet_start": "00:00:00", "quiet_end": "00:00:00",
        "notice_entities": ["sensor.washer"],
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_shush_and_hush_after_remark_make_it_quieter(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    rt = entry.runtime_data
    fb = rt.feedback
    rt.mutes.add("sensor.washer", 1)
    assert round(fb.score("sensor.washer"), 2) == 1.5 and fb.remark_level("sensor.washer") is None
    rt.mutes.remove("sensor.washer")
    assert fb.score("sensor.washer") == 0
    rt.mutes.add("sensor.washer", 1)
    rt.mutes.remove("sensor.washer")
    rt.mutes.add("sensor.washer")  # shushed for good this time: stays counted
    fb.remarked(["sensor.washer"])
    await hass.services.async_call(DOMAIN, "hush", {"minutes": 30}, blocking=True)
    assert fb.remark_level("sensor.washer") == "important_only"
    rt.mutes.remove("sensor.washer")  # unshushed, but the hush still counts
    assert fb.remark_level("sensor.washer") is None
    fb.record("sensor.washer", 2, "test")
    assert "don't want to hear about these unless it really matters" in rt.noticer._context()
    fb.record("sensor.washer", 1, "test")
    assert fb.remark_level("sensor.washer") == "quiet"
    hass.states.async_set("sensor.washer", "running")
    await hass.async_block_till_done()
    assert not rt.noticer._pending  # not noticed at all now
    listing = fb.listing()
    assert listing[0]["effect"] == "not mentioned"
    fb.reset("sensor.washer")
    assert fb.score("sensor.washer") == 0


async def test_hush_long_after_a_remark_is_not_feedback(hass: HomeAssistant, freezer) -> None:
    from datetime import timedelta

    entry = await _setup(hass)
    fb = entry.runtime_data.feedback
    fb.remarked(["sensor.washer"])
    freezer.tick(timedelta(minutes=5))
    await hass.services.async_call(DOMAIN, "hush", {"minutes": 30}, blocking=True)
    assert fb.score("sensor.washer") == 0


async def test_feedback_fades(hass: HomeAssistant, freezer) -> None:
    from datetime import timedelta

    entry = await _setup(hass)
    fb = entry.runtime_data.feedback
    fb.record("sensor.washer", 3, "test")
    freezer.tick(timedelta(days=30))
    assert round(fb.score("sensor.washer"), 1) == 1.5
