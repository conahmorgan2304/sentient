"""Music by asking through Music Assistant: choices from their library, played in the right room."""

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import area_registry as ar, entity_registry as er, llm
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant, monkeypatch, with_ma: bool = True, extra: dict | None = None):
    import custom_components.sentient.music as music

    monkeypatch.setattr(music, "ma_entry", lambda hass: "ma1" if with_ma else None)
    if with_ma:
        kitchen = ar.async_get(hass).async_create("Kitchen")
        er.async_get(hass).async_get_or_create("media_player", "music_assistant", "k1", suggested_object_id="kitchen_ma")
        er.async_get(hass).async_update_entity("media_player.kitchen_ma", area_id=kitchen.id)
        hass.states.async_set("media_player.kitchen_ma", "idle", {"friendly_name": "Kitchen speaker"})

        async def library(call: ServiceCall):
            if call.data["media_type"] == "playlist":
                return {"items": [{"name": "Sunday Chill", "uri": "library://playlist/1"}]}
            return {"items": [{"name": "Classic FM", "uri": "library://radio/2"}]}

        hass.services.async_register("music_assistant", "get_library", library, supports_response=SupportsResponse.ONLY)
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **(extra or {})})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                                assistant=None, device_id=None))


async def test_play_something_calm_in_the_kitchen(hass: HomeAssistant, monkeypatch) -> None:
    api = await _setup(hass, monkeypatch)
    played = async_mock_service(hass, "music_assistant", "play_media")
    choices = await api.async_call_tool(llm.ToolInput(tool_name="sentient_music", tool_args={"action": "choices"}))
    choices = getattr(choices, "data", choices)
    assert choices["playlists"][0]["name"] == "Sunday Chill" and choices["radios"][0]["name"] == "Classic FM"
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_music", tool_args={
        "action": "play", "media": "library://playlist/1", "media_type": "playlist", "room": "kitchen"}))
    out = getattr(out, "data", out)
    assert out["on"] == "Kitchen speaker"
    assert played[0].data["media_id"] == "library://playlist/1" and played[0].data["entity_id"] in ("media_player.kitchen_ma", ["media_player.kitchen_ma"])


async def test_no_music_assistant_no_tool(hass: HomeAssistant, monkeypatch) -> None:
    api = await _setup(hass, monkeypatch, with_ma=False)
    assert "sentient_music" not in [t.name for t in api.tools]


async def test_room_with_several_players_picks_the_right_one(hass: HomeAssistant, monkeypatch) -> None:
    reg = er.async_get(hass)
    # The house talks through a Sonos; Music Assistant has its own player for the same device (same unique id).
    reg.async_get_or_create("media_player", "sonos", "RINCON_1", suggested_object_id="lounge_sonos")
    hass.states.async_set("media_player.lounge_sonos", "idle", {"friendly_name": "Lounge Sonos"})
    api = await _setup(hass, monkeypatch, extra={"speakers": ["media_player.lounge_sonos"]})
    areas = ar.async_get(hass)
    kitchen, lounge = areas.async_get_area_by_name("Kitchen"), areas.async_create("Lounge")
    reg.async_remove("media_player.kitchen_ma")
    # Music Assistant calls every player a speaker, so the TV box only differs by not being the house's speaker.
    for uid, obj, name, cls, state, area in (
        ("t1", "lounge_tv", "Lounge TV", "tv", "unavailable", lounge),
        ("b1", "lounge_box", "Lounge box", "speaker", "idle", lounge),
        ("RINCON_1", "lounge_ma", "Lounge", "speaker", "idle", lounge),
        ("t2", "kitchen_tv", "Kitchen TV", "tv", "idle", kitchen),
        ("s2", "kitchen_radio", "Kitchen radio", "speaker", "idle", kitchen),
        ("t3", "den_tv", "Den TV", "tv", "unavailable", areas.async_create("Den")),
    ):
        reg.async_get_or_create("media_player", "music_assistant", uid, suggested_object_id=obj)
        reg.async_update_entity(f"media_player.{obj}", area_id=area.id)
        hass.states.async_set(f"media_player.{obj}", state, {"friendly_name": name, "device_class": cls})
    played = async_mock_service(hass, "music_assistant", "play_media")

    async def play(room: str) -> str:
        out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_music", tool_args={
            "action": "play", "media": "Metallica", "media_type": "artist", "room": room}))
        return getattr(out, "data", out)["on"]

    assert await play("lounge") == "Lounge"  # the house's own speaker, though the TV and the box come first
    assert await play("lounge box") == "Lounge box"  # still reachable by its own name
    assert await play("kitchen") == "Kitchen radio"  # no house speaker there: a speaker rather than a TV
    assert await play("den") == "Den TV"  # the only player there is still used, even off
    assert len(played) == 4


async def _alexa(hass: HomeAssistant, monkeypatch, on: bool = True, service: str | None = None):
    """No Music Assistant; two Echos through Alexa Media Player and a 'This Device' entry that can't play."""
    alexa = MockConfigEntry(domain="alexa_media", title="Alexa")
    alexa.add_to_hass(hass)
    monkeypatch.setattr(hass.config_entries, "async_loaded_entries",
                        lambda domain, _real=hass.config_entries.async_loaded_entries:
                        [alexa] if domain == "alexa_media" else _real(domain))
    reg, areas = er.async_get(hass), ar.async_get(hass)
    lounge, kitchen = areas.async_create("Living Room"), areas.async_create("Kitchen")
    for uid, oid, area, name in (("e1", "livingroom", lounge.id, "livingroom echo"), ("e2", "kitchen_echo", kitchen.id, "kitchen echo"),
                                 ("e3", "this_device_2", None, "This Device")):
        reg.async_get_or_create("media_player", "alexa_media", uid, suggested_object_id=oid)
        reg.async_update_entity(f"media_player.{oid}", area_id=area)
        hass.states.async_set(f"media_player.{oid}", "idle", {"friendly_name": name})
    extra = {"music_alexa": on, **({"music_alexa_service": service} if service else {})}
    return await _setup(hass, monkeypatch, with_ma=False, extra=extra)


async def test_spotify_through_the_living_room_echo(hass: HomeAssistant, monkeypatch) -> None:
    """Tested live 13 Nov: play_media with type SPOTIFY on the living room Echo played it."""
    api = await _alexa(hass, monkeypatch)
    played = async_mock_service(hass, "media_player", "play_media")
    choices = await api.async_call_tool(llm.ToolInput(tool_name="sentient_music", tool_args={"action": "choices"}))
    choices = getattr(choices, "data", choices)
    assert choices["service"] == "Spotify" and "This Device" not in choices["players"]
    out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_music", tool_args={
        "action": "play", "media": "Chill Vibes", "media_type": "playlist", "room": "living room"}))
    out = getattr(out, "data", out)
    assert out["on"] == "livingroom echo"
    assert played[-1].data == {"entity_id": "media_player.livingroom", "media_content_type": "SPOTIFY",
                               "media_content_id": "playlist Chill Vibes"}


async def test_the_chosen_service_and_stopping_pauses_the_echo(hass: HomeAssistant, monkeypatch) -> None:
    api = await _alexa(hass, monkeypatch, service="AMAZON_MUSIC")
    played = async_mock_service(hass, "media_player", "play_media")
    paused = async_mock_service(hass, "media_player", "media_pause")
    await api.async_call_tool(llm.ToolInput(tool_name="sentient_music", tool_args={
        "action": "play", "media": "Coldplay", "media_type": "artist", "room": "kitchen"}))
    assert played[-1].data["media_content_type"] == "AMAZON_MUSIC"
    assert played[-1].data["media_content_id"] == "songs by Coldplay"
    await api.async_call_tool(llm.ToolInput(tool_name="sentient_music", tool_args={"action": "stop", "room": "kitchen"}))
    assert paused[-1].data["entity_id"] == "media_player.kitchen_echo"


async def test_music_through_alexa_is_off_until_switched_on(hass: HomeAssistant, monkeypatch) -> None:
    api = await _alexa(hass, monkeypatch, on=False)
    assert "sentient_music" not in [t.name for t in api.tools]


def test_what_the_echo_is_asked_for() -> None:
    from custom_components.sentient.music import ask_alexa_for

    assert ask_alexa_for("relaxing acoustic", None) == "relaxing acoustic"
    assert ask_alexa_for("my Discover Weekly playlist", "playlist") == "my Discover Weekly playlist"
    assert ask_alexa_for("Abbey Road", "album") == "the album Abbey Road"
