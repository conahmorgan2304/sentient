"""Music by asking — only when Music Assistant is set up.

"Play something calm in the kitchen": the voice gets the household's own playlists and radio stations
(favourites first) with sentient_music, picks the one that fits, and Sentient plays it on the Music
Assistant player in that room. The same rules as speaking apply: in quiet hours it plays at the night
volume, a room in its own quiet time is only used if the person asking is in it, and Sentient's
announcements turn the music down while they speak.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.util import dt as dt_util

from .presence import area_of

DOMAIN_MA = "music_assistant"
CACHE = timedelta(hours=1)
KINDS = ("playlist", "radio")


def ma_entry(hass: HomeAssistant) -> str | None:
    entries = hass.config_entries.async_loaded_entries(DOMAIN_MA)
    return entries[0].entry_id if entries else None


def players(hass: HomeAssistant) -> list[str]:
    reg = er.async_get(hass)
    return [e.entity_id for e in reg.entities.values() if e.platform == DOMAIN_MA and e.domain == "media_player"
            and not e.disabled_by]


def player_in(hass: HomeAssistant, room: str | None = None, area_id: str | None = None) -> str | None:
    """The Music Assistant player in that room (by name or area), else None."""
    areas = ar.async_get(hass)
    if room and area_id is None:
        low = room.strip().lower().removeprefix("the ")
        area_id = next((a.id for a in areas.async_list_areas() if a.name.lower() == low or low in a.name.lower()), None)
        if area_id is None:  # maybe they named the player itself
            return next((p for p in players(hass) if (st := hass.states.get(p)) and low in st.name.lower()), None)
    if area_id is None:
        return None
    return next((p for p in players(hass) if area_of(hass, p) == area_id), None)


class Music:
    def __init__(self, hass: HomeAssistant, rt: Any) -> None:
        self.hass = hass
        self.rt = rt
        self._cache: tuple[Any, dict[str, list[dict[str, str]]]] | None = None

    async def async_choices(self) -> dict[str, Any]:
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
        player = player_in(self.hass, room) if room else player_in(self.hass, area_id=here)
        if player is None:
            names = [st.name for p in players(self.hass) if (st := self.hass.states.get(p))]
            return {"error": "No Music Assistant player in that room", "players": names}
        profiles = getattr(self.rt, "profiles", None)
        if profiles is not None and profiles.quiet(player) and area_of(self.hass, player) != here:
            return {"error": "That room is in its quiet time — only play there if the person asking is in it"}
        data: dict[str, Any] = {"media_id": media}
        if media_type:
            data["media_type"] = media_type
        if radio_mode:
            data["radio_mode"] = True
        if speech.quiet_now() and (night := speech.volume_now()) is not None:
            await self.hass.services.async_call("media_player", "volume_set",
                                                {"entity_id": player, "volume_level": night}, blocking=True)
        await self.hass.services.async_call(DOMAIN_MA, "play_media", data, target={"entity_id": player}, blocking=True)
        st = self.hass.states.get(player)
        return {"playing": media, "on": st.name if st else player,
                **({"quiet": "played at the night volume (quiet hours)"} if speech.quiet_now() else {})}
