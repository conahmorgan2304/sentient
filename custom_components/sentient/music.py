"""Music by asking — through Music Assistant, or through the Echos (Alexa Media Player).

"Play something calm in the kitchen": with Music Assistant, the voice gets the household's own playlists and
radio stations (favourites first) with sentient_music, picks the one that fits, and Sentient plays it on the
Music Assistant player in that room. Without it, and with "Music through Alexa" on, the Echo in that room is
asked to play it from the chosen service (Spotify, Amazon Music…) — by name, as if someone had asked the Echo,
since Alexa can't list a library. No AI call either way beyond the voice's own. The same rules as speaking apply: in quiet hours it plays at the night
volume, a room in its own quiet time is only used if the person asking is in it, and Sentient's
announcements turn the music down while they speak.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.util import dt as dt_util

from .const import CONF_SPEAKERS, DOMAIN
from .presence import area_of

DOMAIN_MA = "music_assistant"
DOMAIN_ALEXA = "alexa_media"
CONF_MUSIC_ALEXA = "music_alexa"  # off: the Echos are never asked to play music
CONF_MUSIC_SERVICE = "music_alexa_service"
SERVICES = ("SPOTIFY", "AMAZON_MUSIC", "APPLE_MUSIC", "DEEZER", "TUNEIN", "IHEARTRADIO")
DEFAULT_SERVICE = "SPOTIFY"
CACHE = timedelta(hours=1)
KINDS = ("playlist", "radio")


def ma_entry(hass: HomeAssistant) -> str | None:
    entries = hass.config_entries.async_loaded_entries(DOMAIN_MA)
    return entries[0].entry_id if entries else None


def _platform_players(hass: HomeAssistant, platform: str) -> list[str]:
    reg = er.async_get(hass)
    return [e.entity_id for e in reg.entities.values() if e.platform == platform and e.domain == "media_player"
            and not e.disabled_by]


def players(hass: HomeAssistant) -> list[str]:
    return _platform_players(hass, DOMAIN_MA)


def alexa_players(hass: HomeAssistant) -> list[str]:
    """The Echos and speaker groups (Alexa Media Player) — a group like 'Upstairs' can be asked for by name —
    but not the 'This Device' entry for a phone or computer's Alexa app."""
    return [p for p in _platform_players(hass, DOMAIN_ALEXA) if "this_device" not in p]


def backend(hass: HomeAssistant, options: dict[str, Any]) -> str | None:
    """'ma' (Music Assistant, first choice), 'alexa' (the Echos, when switched on), or None."""
    if ma_entry(hass) is not None:
        return "ma"
    if options.get(CONF_MUSIC_ALEXA) and hass.config_entries.async_loaded_entries(DOMAIN_ALEXA) and alexa_players(hass):
        return "alexa"
    return None


def ask_alexa_for(media: str, media_type: str | None) -> str:
    """What the Echo is asked to find: 'playlist Chill Vibes', 'songs by Coldplay', or what they said."""
    media = " ".join(str(media).split())
    if media_type == "playlist" and "playlist" not in media.lower():
        return f"playlist {media}"
    if media_type == "artist" and not media.lower().startswith(("songs by", "music by")):
        return f"songs by {media}"
    if media_type == "album" and "album" not in media.lower():
        return f"the album {media}"
    return media


def _own_speakers(hass: HomeAssistant) -> tuple[set[str], set[str]]:
    """Unique ids and names of the speakers Sentient is set to talk through."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    options = getattr(entries[0].runtime_data, "options", None) if entries else None
    reg, ids, names = er.async_get(hass), set(), set()
    for speaker in (options or {}).get(CONF_SPEAKERS) or []:
        if (entry := reg.async_get(speaker)) and entry.unique_id:
            ids.add(entry.unique_id)
        if st := hass.states.get(speaker):
            names.add(st.name.lower())
    return ids, names


def _best(hass: HomeAssistant, candidates: list[str]) -> str | None:
    """Of several players: one that's on, then the house's own speaker, then a speaker rather than a TV."""
    ids, names = _own_speakers(hass)
    reg = er.async_get(hass)

    def rank(player: str) -> tuple[bool, bool, bool]:
        st = hass.states.get(player)
        entry = reg.async_get(player)
        own = (entry is not None and entry.unique_id in ids) or (st is not None and st.name.lower() in names)
        return (st is None or st.state in ("unavailable", "unknown"), not own,
                st is None or st.attributes.get("device_class") != "speaker")

    return min(candidates, key=rank, default=None)


def player_in(hass: HomeAssistant, room: str | None = None, area_id: str | None = None,
              pool: list[str] | None = None) -> str | None:
    """The music player in that room (by name or area), else None; pool defaults to Music Assistant's."""
    areas = ar.async_get(hass)
    pool = players(hass) if pool is None else pool
    if room and area_id is None:
        low = room.strip().lower().removeprefix("the ")
        area_id = next((a.id for a in areas.async_list_areas() if a.name.lower() == low or low in a.name.lower()), None)
        if area_id is None:  # maybe they named the player itself
            return _best(hass, [p for p in pool if (st := hass.states.get(p)) and low in st.name.lower()])
    if area_id is None:
        return None
    return _best(hass, [p for p in pool if area_of(hass, p) == area_id])


class Music:
    def __init__(self, hass: HomeAssistant, rt: Any) -> None:
        self.hass = hass
        self.rt = rt
        self._cache: tuple[Any, dict[str, list[dict[str, str]]]] | None = None

    def _pool(self) -> list[str]:
        return alexa_players(self.hass) if backend(self.hass, self.rt.options) == "alexa" else players(self.hass)

    def player(self, room: str | None, device_id: str | None) -> str | None:
        """The player for that room, or the room the person asking is in."""
        if room:
            return player_in(self.hass, room, pool=self._pool())
        if device_id:
            from homeassistant.helpers import device_registry as dr

            device = dr.async_get(self.hass).async_get(device_id)
            if device is not None and device.area_id:
                return player_in(self.hass, area_id=device.area_id, pool=self._pool())
        return None

    async def async_stop(self, room: str | None, device_id: str | None) -> dict[str, Any]:
        player = self.player(room, device_id)
        if player is None:
            return {"error": "Which room?"}
        # an Echo pauses (Alexa has no stop that leaves it ready to resume); Music Assistant stops
        service = "media_pause" if backend(self.hass, self.rt.options) == "alexa" else "media_stop"
        await self.hass.services.async_call("media_player", service, {"entity_id": player}, blocking=True)
        return {"stopped": player}

    async def async_choices(self) -> dict[str, Any]:
        if backend(self.hass, self.rt.options) == "alexa":
            service = str(self.rt.options.get(CONF_MUSIC_SERVICE) or DEFAULT_SERVICE).replace("_", " ").title()
            return {"service": service, "players": [st.name for p in alexa_players(self.hass) if (st := self.hass.states.get(p))],
                    "note": f"Through the Echos, {service} plays whatever is asked for by name — a playlist, an artist, "
                            "a song or a mood ('relaxing acoustic'). Alexa can't list the library, so use their words."}
        entry = ma_entry(self.hass)
        if entry is None:
            return {"error": "Music Assistant isn't set up"}
        now = dt_util.utcnow()
        if self._cache and now - self._cache[0] < CACHE:
            return self._cache[1]
        out: dict[str, Any] = {}
        for kind in KINDS:
            seen: dict[str, dict[str, str]] = {}
            for favourite in (True, False):
                try:
                    resp = await self.hass.services.async_call(
                        DOMAIN_MA, "get_library",
                        {"config_entry_id": entry, "media_type": kind, "favorite": favourite, "limit": 40},
                        blocking=True, return_response=True,
                    )
                except Exception:  # noqa: BLE001 - one kind failing still leaves the other
                    continue
                for item in (resp or {}).get("items", []):
                    if item.get("uri") and item.get("name") and item["uri"] not in seen:
                        seen[item["uri"]] = {"name": item["name"], "uri": item["uri"], **({"favourite": "yes"} if favourite else {})}
            out[kind + "s"] = list(seen.values())[:40]
        out["players"] = [st.name for p in players(self.hass) if (st := self.hass.states.get(p))]
        self._cache = (now, out)
        return out

    async def async_play(self, media: str, media_type: str | None, room: str | None, device_id: str | None,
                         radio_mode: bool = False) -> dict[str, Any]:
        speech = self.rt.speech
        here = None
        if device_id:
            from homeassistant.helpers import device_registry as dr

            device = dr.async_get(self.hass).async_get(device_id)
            here = device.area_id if device else None
        how = backend(self.hass, self.rt.options)
        pool = alexa_players(self.hass) if how == "alexa" else players(self.hass)
        player = player_in(self.hass, room, pool=pool) if room else player_in(self.hass, area_id=here, pool=pool)
        if player is None:
            names = [st.name for p in pool if (st := self.hass.states.get(p))]
            return {"error": "No Echo in that room" if how == "alexa" else "No Music Assistant player in that room",
                    "players": names}
        profiles = getattr(self.rt, "profiles", None)
        if profiles is not None and profiles.quiet(player) and area_of(self.hass, player) != here:
            return {"error": "That room is in its quiet time — only play there if the person asking is in it"}
        if speech.quiet_now() and (night := speech.volume_now()) is not None:
            await self.hass.services.async_call("media_player", "volume_set",
                                                {"entity_id": player, "volume_level": night}, blocking=True)
        if how == "alexa":
            service = str(self.rt.options.get(CONF_MUSIC_SERVICE) or DEFAULT_SERVICE)
            await self.hass.services.async_call("media_player", "play_media", {
                "entity_id": player, "media_content_type": service if service in SERVICES else DEFAULT_SERVICE,
                "media_content_id": ask_alexa_for(media, media_type)}, blocking=True)
        else:
            data: dict[str, Any] = {"media_id": media}
            if media_type:
                data["media_type"] = media_type
            if radio_mode:
                data["radio_mode"] = True
            await self.hass.services.async_call(DOMAIN_MA, "play_media", data, target={"entity_id": player}, blocking=True)
        st = self.hass.states.get(player)
        return {"playing": media, "on": st.name if st else player,
                **({"quiet": "played at the night volume (quiet hours)"} if speech.quiet_now() else {})}
