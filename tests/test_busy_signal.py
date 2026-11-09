"""A chosen busy signal holds back speech that can wait; urgent speech still gets through."""

from homeassistant.core import HomeAssistant

from custom_components.sentient.speech import SpeechRouter


def _router(hass: HomeAssistant, entities) -> SpeechRouter:
    return SpeechRouter(hass, {"busy_entities": entities, "quiet_start": "00:00:00", "quiet_end": "00:00:00"})


async def test_nothing_chosen_means_never_busy(hass: HomeAssistant) -> None:
    hass.states.async_set("calendar.work", "on")
    assert not _router(hass, []).busy()
    assert not _router(hass, []).hushed()


async def test_busy_while_a_chosen_signal_is_on(hass: HomeAssistant) -> None:
    router = _router(hass, ["calendar.work", "sensor.phone_dnd"])
    hass.states.async_set("calendar.work", "off")
    hass.states.async_set("sensor.phone_dnd", "off")
    assert not router.hushed()
    hass.states.async_set("calendar.work", "on")
    assert router.busy() and router.hushed()
    hass.states.async_set("calendar.work", "off")
    hass.states.async_set("sensor.phone_dnd", "priority_only")
    assert router.hushed()


async def test_unavailable_signal_is_not_busy(hass: HomeAssistant) -> None:
    hass.states.async_set("switch.focus", "unavailable")
    assert not _router(hass, ["switch.focus", "switch.missing"]).busy()
