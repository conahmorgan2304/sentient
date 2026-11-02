"""Search: questions about the world outside the house.

Two ways, both optional:
* The companion integration Voice Satellite LLM Tools adds web, image, YouTube, Wikipedia and weather tools
  to whichever AI agent selects them — Sentient's voice already uses them that way, and its tablet card shows
  the pictures and videos. When it's installed, Sentient tells the voice the tools are there and when to use
  them (words on a speaker, pictures only on a tablet). When it isn't, the Sentient page suggests it.
* Without it, Sentient's own voice-only web search can use your own SearXNG (for example the SearXNG add-on):
  no keys, nothing leaves the house except the search itself. Off by default.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_SEARCH_HINTS, CONF_SEARXNG_URL, CONF_WEB_SEARCH, SEARCH_COMPANION

_LOGGER = logging.getLogger(__name__)
MAX_RESULTS = 5


def companion_tools(hass: HomeAssistant) -> list[str]:
    """The companion's tools that are set up, by their titles ('Web Search - SearXNG', 'Video Search - YouTube'…)."""
    return [e.title for e in hass.config_entries.async_entries(SEARCH_COMPANION) if not e.disabled_by]


def companion_has_web(hass: HomeAssistant) -> bool:
    return any("web search" in t.lower() for t in companion_tools(hass))


def own_search_on(hass: HomeAssistant, options: dict[str, Any]) -> bool:
    """Sentient's own search: chosen, a SearXNG address given, and not doubling up on the companion's."""
    return bool(options.get(CONF_WEB_SEARCH) and options.get(CONF_SEARXNG_URL)) and not companion_has_web(hass)


def prompt_line(hass: HomeAssistant, options: dict[str, Any]) -> str | None:
    if not options.get(CONF_SEARCH_HINTS, True):
        return None
    tools = companion_tools(hass)
    if tools:
        return ("Search tools are available (" + ", ".join(tools) + "). Use them for anything about the world "
                "outside the house — news, facts, opening times, how-tos, films, people — rather than guessing. "
                "On a speaker without a screen, answer in a sentence or two of words; only show images or videos "
                "when you're talking through a tablet.")
    if own_search_on(hass, options):
        return ("For anything about the world outside the house — news, facts, opening times, how-tos — call "
                "sentient_search rather than guessing, then answer in a sentence or two.")
    return None


async def async_search(hass: HomeAssistant, options: dict[str, Any], query: str) -> dict[str, Any]:
    base = str(options.get(CONF_SEARXNG_URL) or "").rstrip("/")
    if not base:
        return {"error": "No SearXNG address is set in Sentient's settings"}
    params = {"q": query, "format": "json", "safesearch": "1", "language": hass.config.language or "en"}
    try:
        async with asyncio.timeout(10):
            resp = await async_get_clientsession(hass).get(f"{base}/search", params=params)
            if resp.status == 403:
                return {"error": "SearXNG refused JSON — turn on the json format in its settings (search: formats)"}
            if resp.status != 200:
                return {"error": f"SearXNG answered {resp.status}"}
            data = await resp.json(content_type=None)
    except Exception as err:  # noqa: BLE001
        return {"error": f"couldn't reach SearXNG: {err or type(err).__name__}"}
    results = [{"title": r.get("title"), "snippet": (r.get("content") or "")[:300], "source": r.get("url")}
               for r in (data.get("results") or [])[:MAX_RESULTS]]
    answers = [a if isinstance(a, str) else a.get("answer") for a in data.get("answers") or []]
    return {"query": query, "answers": [a for a in answers if a][:2], "results": results,
            "note": "Summarise what matters in a sentence or two; don't read out web addresses."}
