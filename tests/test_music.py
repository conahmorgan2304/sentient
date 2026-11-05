"""Music by asking through Music Assistant: choices from their library, played in the right room."""

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import area_registry as ar, entity_registry as er, llm
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.const import DOMAIN

OPTIONS = {"persona": "Nova", "quiet_start": "00:00:00", "quiet_end": "00:00:00"}


async def _setup(hass: HomeAssistant, monkeypatch, with_ma: bool = True):
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
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data=OPTIONS)
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
