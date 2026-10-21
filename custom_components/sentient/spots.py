"""Spots: named areas inside a camera picture ("the rabbit pen").

A spot is found once by the AI task ("where is the rabbit pen in this
picture?") and remembered as a box. Camera watches and looks that mention a
spot then send the AI only that part of the picture — sharper answers for
less. The cropped picture is written to Home Assistant's local media folder
so the AI task can read it like any other media.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import CONF_AI_TASK_ENTITY, DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
MARGIN = 0.12  # extra context around a spot, as a share of its size
MIN_SIZE = 0.08  # never crop smaller than this share of the frame


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower().removeprefix("the ")).strip()


class Spots:
    """Named boxes per camera, and cropped pictures of them."""

    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any]) -> None:
        self.hass = hass
        self.options = options
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.spots")
        self.spots: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.spots = data.get("spots", {})

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"spots": self.spots}, 2)

    def listing(self) -> list[dict[str, Any]]:
        return [{"name": s["name"], "camera": s["camera"]} for s in self.spots.values()]

    def find_in(self, camera: str, text: str | None) -> str | None:
        """A spot on this camera whose name appears in the text, if any."""
        if not text:
            return None
        hay = f" {_key(text)} "
        best = None
        for key, spot in self.spots.items():
            if spot["camera"] == camera and f" {key} " in hay and (best is None or len(key) > len(best)):
                best = key
        return best

    def get(self, camera: str, name: str | None) -> dict[str, Any] | None:
        if not name:
            return None
        spot = self.spots.get(_key(name))
        return spot if spot and spot["camera"] == camera else None

    def forget(self, name: str) -> bool:
        if self.spots.pop(_key(name), None) is None:
            return False
        self._save()
        return True

    async def async_learn(self, camera: str, name: str) -> dict[str, Any]:
        """Ask the AI where the spot is in the current picture, and remember it."""
        data: dict[str, Any] = {
            "task_name": f"Sentient: find {name}"[:100],
            "instructions": (
                f"Find '{name}' in this camera picture. Give its bounding box as percentages of the "
                "image width and height, measured from the top-left corner (0 to 100). If it isn't "
                "clearly visible, set found to false."
            ),
            "structure": {
                "found": {"required": True, "selector": {"boolean": {}}},
                "left": {"required": False, "selector": {"number": {"min": 0, "max": 100}}},
                "top": {"required": False, "selector": {"number": {"min": 0, "max": 100}}},
                "right": {"required": False, "selector": {"number": {"min": 0, "max": 100}}},
                "bottom": {"required": False, "selector": {"number": {"min": 0, "max": 100}}},
            },
            "attachments": [{"media_content_id": f"media-source://camera/{camera}", "media_content_type": "image/jpeg"}],
        }
        if ai_entity := self.options.get(CONF_AI_TASK_ENTITY):
            data["entity_id"] = ai_entity
        response = await self.hass.services.async_call(
            "ai_task", "generate_data", data, blocking=True, return_response=True
        )
        result = (response or {}).get("data") or {}
        if not isinstance(result, dict) or not result.get("found"):
            raise HomeAssistantError(f"I couldn't see {name} on that camera")
        try:
            box = [float(result[k]) / 100 for k in ("left", "top", "right", "bottom")]
        except (KeyError, TypeError, ValueError) as err:
            raise HomeAssistantError(f"I couldn't work out where {name} is") from err
        left, top, right, bottom = (min(1.0, max(0.0, v)) for v in box)
        if right - left < 0.02 or bottom - top < 0.02:
            raise HomeAssistantError(f"I couldn't work out where {name} is")
        spot = {"name": name.strip(), "camera": camera, "box": [left, top, right, bottom]}
        self.spots[_key(name)] = spot
        self._save()
        return spot

    # ----- cropping ------------------------------------------------------------
    def _media_dir(self) -> Path | None:
        local = (self.hass.config.media_dirs or {}).get("local")
        return Path(local) if local else None

    async def async_attachment(self, camera: str, spot_key: str | None) -> dict[str, str]:
        """An AI-task attachment: the whole camera picture, or just the spot."""
        whole = {"media_content_id": f"media-source://camera/{camera}", "media_content_type": "image/jpeg"}
        spot = self.spots.get(spot_key or "")
        media = self._media_dir()
        if spot is None or spot["camera"] != camera or media is None:
            return whole
        try:
            from homeassistant.components.camera import async_get_image

            image = await async_get_image(self.hass, camera)
            filename = f"{re.sub(r'[^a-z0-9]+', '_', spot_key)}_{camera.split('.', 1)[1]}.jpg"
            await self.hass.async_add_executor_job(self._crop_to, image.content, spot["box"], media / "sentient" / filename)
        except Exception as err:  # noqa: BLE001 - a whole picture is a fine fallback
            _LOGGER.debug("Could not crop %s to %s: %s", camera, spot_key, err)
            return whole
        return {
            "media_content_id": f"media-source://media_source/local/sentient/{filename}",
            "media_content_type": "image/jpeg",
        }

    @staticmethod
    def _crop_to(content: bytes, box: list[float], path: Path) -> None:
        from PIL import Image

        img = Image.open(io.BytesIO(content)).convert("RGB")
        w, h = img.size
        left, top, right, bottom = box
        mw, mh = (right - left) * MARGIN, (bottom - top) * MARGIN
        left, top = max(0.0, left - mw), max(0.0, top - mh)
        right, bottom = min(1.0, right + mw), min(1.0, bottom + mh)
        if right - left < MIN_SIZE:
            centre = (left + right) / 2
            left, right = max(0.0, centre - MIN_SIZE / 2), min(1.0, centre + MIN_SIZE / 2)
        if bottom - top < MIN_SIZE:
            centre = (top + bottom) / 2
            top, bottom = max(0.0, centre - MIN_SIZE / 2), min(1.0, centre + MIN_SIZE / 2)
        crop = img.crop((int(left * w), int(top * h), int(right * w), int(bottom * h)))
        path.parent.mkdir(parents=True, exist_ok=True)
        crop.save(path, "JPEG", quality=90)
