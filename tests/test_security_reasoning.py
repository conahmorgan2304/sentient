"""Camera-security alerts weighed against the rest of the house before they're said (setting off by default)."""
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

from custom_components.sentient.security_reasoning import weigh
from tests.test_camsecurity import _see, _setup, _t


def _w(**kw):
    base = dict(reason="night", alarm_state="disarmed", entries_opened=[], indoor_activity=False, everyone_away=False,
                repeats=0, frigate_labels=[])
    return weigh(**{**base, **kw})


def test_someone_going_out_is_kept_quiet_but_the_alarm_always_wins():
    quiet = _w(entries_opened=[("Back door", 40)])
    assert quiet["announce"] is False and "entry_opened_just_before" in quiet["reasons"]
    assert "Back door opened 1 minute before" in quiet["explanation"]
    assert _w(entries_opened=[("Back door", 40)], alarm_state="armed_night")["announce"] is True
    assert _w(entries_opened=[("Back door", 40)], everyone_away=True)["announce"] is True


def test_an_entry_opened_long_before_or_after_does_not_explain_it():
    assert _w(entries_opened=[("Back door", 900)])["announce"] is True
    assert _w(entries_opened=[("Back door", -20)])["announce"] is True  # opened after the sighting: suspicious, say it


def test_repeats_are_said_once_then_kept_quiet_and_never_claim_identity():
    assert _w(repeats=1)["announce"] is True
    held = _w(repeats=2, frigate_labels=["person"])
    assert held["announce"] is False and "frigate_event" in held["reasons"]
    assert held["identity_confirmed"] is False and held["route_confirmed"] is False


def test_a_fence_alert_is_never_explained_away_by_a_door():
    assert _w(reason="fence", entries_opened=[("Back door", 30)])["announce"] is True


async def _night(hass, freezer, reasoning=True):
    freezer.move_to(dt_util.as_utc(_t(21, 2, 0)))
    entry = await _setup(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.data, "security_reasoning": reasoning})
    await hass.async_block_till_done()
    return entry


async def test_door_opened_just_before_a_night_sighting_is_logged_not_said(hass: HomeAssistant, freezer) -> None:
    entry = await _night(hass, freezer)
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("binary_sensor.back_gate", "on", {"device_class": "door", "friendly_name": "Back gate"})
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=30))
    await _see(hass, "gate_person")
    # night watch may still mention the gate itself; the camera alert about the person is what's kept quiet
    assert not [c for c in said if "person" in c.data["message"]]
    item = next(i for i in entry.runtime_data.timeline.items if i["kind"] == "security")
    assert item["reasoning"]["announce"] is False and "kept quiet" in item["detail"]


async def test_the_same_camera_is_said_twice_then_kept_quiet(hass: HomeAssistant, freezer) -> None:
    await _night(hass, freezer)
    said = async_mock_service(hass, "assist_satellite", "announce")
    for _ in range(3):
        await _see(hass, "gate_person")
        await _see(hass, "gate_person", on=False)
        freezer.tick(timedelta(minutes=11))  # past the plain repeat limit, so each one is a new alert
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert len([c for c in said if "person" in c.data["message"]]) == 2


async def test_off_nothing_changes(hass: HomeAssistant, freezer) -> None:
    entry = await _night(hass, freezer, reasoning=False)
    said = async_mock_service(hass, "assist_satellite", "announce")
    hass.states.async_set("binary_sensor.back_gate", "on", {"device_class": "door", "friendly_name": "Back gate"})
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=30))
    await _see(hass, "gate_person")
    assert [c for c in said if "person" in c.data["message"]]
    item = next(i for i in entry.runtime_data.timeline.items if i["kind"] == "security")
    assert "reasoning" not in item and item["detail"] == "camera security"
