"""Away lights: replay last week while everyone's out after dark."""

from datetime import timedelta

from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.away import intervals
from custom_components.sentient.const import DOMAIN


def _state(entity_id: str, state: str, when) -> State:
    return State(entity_id, state, last_changed=when, last_updated=when)


def test_intervals_skip_flicks_and_cap_long_ones() -> None:
    t0 = dt_util.utcnow()
    states = [
        _state("light.a", "on", t0), _state("light.a", "off", t0 + timedelta(minutes=40)),
        _state("light.a", "on", t0 + timedelta(minutes=50)), _state("light.a", "off", t0 + timedelta(minutes=51)),
        _state("light.a", "on", t0 + timedelta(hours=2)),
    ]
    out = intervals(states, t0, t0 + timedelta(hours=12))
    assert out[0] == (t0, t0 + timedelta(minutes=40))
    assert len(out) == 2 and out[1][1] - out[1][0] == timedelta(hours=6)


async def test_away_lights_follow_last_week_and_stop_when_someone_is_home(hass: HomeAssistant) -> None:
    hass.states.async_set("person.a", "not_home")
    hass.states.async_set("person.b", "not_home")
    hass.states.async_set("sun.sun", "below_horizon")
    hass.states.async_set("light.lounge", "off")
    hass.states.async_set("light.landing", "on")  # left on by a person: never ours to switch off
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": [], "away_lights_on": True, "away_after": 0,
        "away_lights": ["light.lounge", "light.landing"],
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    away = entry.runtime_data.away
    now = dt_util.utcnow()

    async def history(start, end, lights):
        week = now - timedelta(days=7)
        return {"light.lounge": [_state("light.lounge", "off", week - timedelta(hours=2)),
                                 _state("light.lounge", "on", week - timedelta(minutes=30)),
                                 _state("light.lounge", "off", week + timedelta(minutes=60))],
                "light.landing": [_state("light.landing", "off", week - timedelta(hours=1))]}

    away._history = history
    on = async_mock_service(hass, "light", "turn_on")
    off = async_mock_service(hass, "light", "turn_off")
    away._check_people(now)
    await away.async_step(now)
    assert away.active and [c.data["entity_id"] for c in on] == ["light.lounge"]
    assert not off  # the landing was on already, and isn't ours
    assert away.times()["light.lounge"]  # learning will know this wasn't a person
    # someone comes home: what we switched on goes off, nothing else
    hass.states.async_set("light.lounge", "on")
    hass.states.async_set("person.a", "home")
    await hass.async_block_till_done()
    assert not away.active and [c.data["entity_id"] for c in off] == ["light.lounge"]
    assert any(i["text"].startswith("Away lights off") for i in entry.runtime_data.timeline.items)


async def test_away_lights_wait_for_dark(hass: HomeAssistant) -> None:
    hass.states.async_set("person.a", "not_home")
    hass.states.async_set("sun.sun", "above_horizon")
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": [], "away_lights_on": True, "away_after": 30,
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    away = entry.runtime_data.away
    now = dt_util.utcnow()
    assert not away.due(now + timedelta(hours=1))  # light outside
    hass.states.async_set("sun.sun", "below_horizon")
    assert not away.due(now + timedelta(minutes=10))  # only just gone out
    assert away.due(now + timedelta(minutes=31))


async def test_away_lights_defaults_and_movement_inside(hass: HomeAssistant) -> None:
    from homeassistant.helpers import area_registry as ar, entity_registry as er

    hass.states.async_set("person.a", "not_home")
    hass.states.async_set("sun.sun", "below_horizon")
    area = ar.async_get(hass).async_create("Lounge")
    ents = er.async_get(hass)
    for oid in ("lounge", "hallway_screen", "kitchen_speaker_led_ring", "loose"):
        ents.async_get_or_create("light", "test", oid, suggested_object_id=oid)
        hass.states.async_set(f"light.{oid}", "off")
    for oid in ("lounge", "hallway_screen", "kitchen_speaker_led_ring"):
        ents.async_update_entity(f"light.{oid}", area_id=area.id)
    hass.states.async_set("light.all_lights", "off", {"entity_id": ["light.lounge"]})
    hass.states.async_set("binary_sensor.lounge_motion", "off", {"device_class": "motion"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={
        "persona": "Nova", "speakers": [], "away_lights_on": True, "away_after": 0,
        "inside_sensors": ["binary_sensor.lounge_motion"],
    })
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    away = entry.runtime_data.away
    assert away.lights() == ["light.lounge"]  # no screens, LED rings, groups or lights without a room
    now = dt_util.utcnow() + timedelta(hours=1)
    assert away.due(now)
    hass.states.async_set("binary_sensor.lounge_motion", "on", {"device_class": "motion"})
    assert away.movement_inside(now) and not away.due(now)  # someone's in after all
