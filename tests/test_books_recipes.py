"""Books (ebooks read aloud, audiobooks played, bookmarks per speaker) and recipes (cooking mode)."""

import asyncio
from unittest.mock import AsyncMock, patch
import zipfile

from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse
from homeassistant.helpers import device_registry as dr, entity_registry as er, llm
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sentient.books import read_chapters, scan
from custom_components.sentient.const import DOMAIN
from custom_components.sentient.recipes import scale, timer_minutes

OPTIONS = {"persona": "Nova", "speakers": [], "quiet_start": "00:00:00", "quiet_end": "00:00:00"}
CHAPTER = "<html><body><h1>Chapter One</h1>" + "".join(f"<p>Sentence {n} of a long story about a rabbit called Flump.</p>" for n in range(40)) + "</body></html>"


def _epub(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>')
        z.writestr("OEBPS/book.opf", '<package><manifest><item id="c1" href="c1.xhtml"/><item id="c2" href="c2.xhtml"/></manifest>'
                                     '<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
        z.writestr("OEBPS/c1.xhtml", CHAPTER)
        z.writestr("OEBPS/c2.xhtml", CHAPTER.replace("One", "Two"))


async def _setup(hass, tmp_path, **extra):
    hass.config.media_dirs = {"local": str(tmp_path)}
    other = MockConfigEntry(domain="esphome"); other.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(config_entry_id=other.entry_id, identifiers={("esphome", "bed")}, name="Bedroom")
    er.async_get(hass).async_get_or_create("assist_satellite", "esphome", "bed_sat", suggested_object_id="bedroom", device_id=dev.id)
    er.async_get(hass).async_get_or_create("media_player", "esphome", "bed_mp", suggested_object_id="bedroom", device_id=dev.id)
    hass.states.async_set("assist_satellite.bedroom", "idle", {"friendly_name": "Bedroom"})
    hass.states.async_set("media_player.bedroom", "idle", {"friendly_name": "Bedroom"})
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={**OPTIONS, **extra})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, dev.id


async def _api(hass, device_id=None):
    return await llm.async_get_api(hass, DOMAIN, llm.LLMContext(platform="t", context=None, language="en",
                                                               assistant=None, device_id=device_id))


def test_epub_and_txt_become_chapters_and_passages(tmp_path) -> None:
    _epub(tmp_path / "Flump.epub")
    chapters = read_chapters(str(tmp_path / "Flump.epub"))
    assert len(chapters) == 2 and chapters[0]["title"] == "Chapter One"
    assert all(len(p) <= 1400 for p in chapters[0]["passages"]) and len(chapters[0]["passages"]) >= 3
    (tmp_path / "Tale.txt").write_text("Chapter 1\n" + "Once upon a time. " * 80 + "\nChapter 2\n" + "The end. " * 50)
    assert [c["title"] for c in read_chapters(str(tmp_path / "Tale.txt"))] == ["Chapter 1", "Chapter 2"]
    (tmp_path / "Narnia").mkdir()
    (tmp_path / "Narnia" / "02.mp3").write_bytes(b"x"); (tmp_path / "Narnia" / "01.mp3").write_bytes(b"x")
    books = {b["name"]: b for b in scan(str(tmp_path))}
    assert books["Flump"]["kind"] == "ebook" and books["Narnia"]["files"] == ["Narnia/01.mp3", "Narnia/02.mp3"]


async def test_reading_keeps_the_place_and_picks_up(hass: HomeAssistant, tmp_path) -> None:
    (tmp_path / "books").mkdir()
    _epub(tmp_path / "books" / "Flump.epub")
    entry, device = await _setup(hass, tmp_path, books=True)
    rt = entry.runtime_data
    said = []

    async def read_aloud(text, speaker):
        said.append(text)
        if len(said) == 2:
            rt.books._stop(speaker, save=False)  # the wake word, during the second passage
            raise asyncio.CancelledError
        return True

    with patch.object(rt.speech, "async_read_aloud", side_effect=read_aloud), patch("custom_components.sentient.books.asyncio.sleep", AsyncMock()):
        api = await _api(hass, device)
        out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_book", tool_args={"action": "read", "title": "flump"}))
        assert out["reading"] and out["book"] == "Flump"
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()
        mark = rt.books.place("Flump", "assist_satellite.bedroom")
        assert mark["chapter"] == 0 and mark["passage"] == 1  # the interrupted passage isn't skipped
        said.clear()
        out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_book", tool_args={"action": "read"}))
        assert out["book"] == "Flump" and "Picking up" in out["say_first"]
        await asyncio.sleep(0.05)
        rt.books._stop("assist_satellite.bedroom", save=False)
    assert said and "Sentence" in said[0]


async def test_through_alexa_one_passage_per_request(hass: HomeAssistant, tmp_path) -> None:
    (tmp_path / "books").mkdir()
    _epub(tmp_path / "books" / "Flump.epub")
    entry, device = await _setup(hass, tmp_path, books=True)
    from custom_components.sentient.focus import via_alexa

    token = via_alexa.set(True)
    try:
        api = await _api(hass, device)
        first = await api.async_call_tool(llm.ToolInput(tool_name="sentient_book", tool_args={"action": "read", "title": "Flump"}))
        second = await api.async_call_tool(llm.ToolInput(tool_name="sentient_book", tool_args={"action": "read"}))
    finally:
        via_alexa.reset(token)
    assert first["read_aloud"] != second["read_aloud"] and "Sentence" in first["read_aloud"]


async def test_audiobook_plays_and_resumes(hass: HomeAssistant, tmp_path) -> None:
    (tmp_path / "books").mkdir()
    (tmp_path / "books" / "Narnia.mp3").write_bytes(b"x")
    entry, device = await _setup(hass, tmp_path, books=True)
    play = async_mock_service(hass, "media_player", "play_media")
    seek = async_mock_service(hass, "media_player", "media_seek")
    async_mock_service(hass, "media_player", "media_pause")
    rt = entry.runtime_data
    rt.books.marks["Narnia|assist_satellite.bedroom"] = {"track": 0, "position": 600.0}
    with patch("custom_components.sentient.books.asyncio.sleep", AsyncMock()):
        out = await rt.books.async_read("Narnia", "assist_satellite.bedroom")
    assert out["on"] == "media_player.bedroom" and "10 minutes in" in out["say_first"]
    assert play[-1].data["media_content_id"] == "media-source://media_source/local/books/Narnia.mp3"
    assert seek[-1].data["seek_position"] == 600.0
    hass.states.async_set("media_player.bedroom", "playing", {"media_position": 900, "media_duration": 3600})
    await rt.books.async_stop_reading("assist_satellite.bedroom")
    assert rt.books.place("Narnia", "assist_satellite.bedroom")["position"] >= 889  # kept, a few seconds back


async def test_books_off_by_default(hass: HomeAssistant, tmp_path) -> None:
    await _setup(hass, tmp_path)
    names = {t.name for t in (await _api(hass)).tools}
    assert "sentient_book" not in names and "sentient_recipe" not in names


def test_scaling_and_timers() -> None:
    assert scale("200 g flour", 1.5) == "300 g flour"
    assert scale("1/2 tsp salt", 2) == "1 tsp salt"
    assert scale("a pinch of salt", 2) == "a pinch of salt"
    assert timer_minutes("Simmer for 20 minutes, stirring") == 20 and timer_minutes("Bake for 1 hour") == 60
    assert timer_minutes("Season to taste") is None


async def test_cooking_from_a_file(hass: HomeAssistant, tmp_path) -> None:
    (tmp_path / "recipes").mkdir()
    (tmp_path / "recipes" / "Lasagne.pdf").write_bytes(b"%PDF")
    hass.states.async_set("todo.shopping_list", "0", {"friendly_name": "Shopping list"})
    entry, device = await _setup(hass, tmp_path, recipes=True)
    rt = entry.runtime_data
    ai = AsyncMock(return_value={"data": {"name": "Lasagne", "servings": 4, "ingredients": ["500 g beef mince", "1 onion"],
                                          "steps": ["Brown the mince.", "Simmer the sauce for 20 minutes.", "Layer and bake."]}})
    added = async_mock_service(hass, "todo", "add_item")
    with patch.object(rt.ai, "async_call", ai):
        api = await _api(hass, device)
        out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_recipe", tool_args={"action": "cook", "query": "lasagne", "servings": 6}))
        assert out["ingredients"] == ["750 g beef mince", "1 1/2 onion"] and out["steps"] == 3
        step = await api.async_call_tool(llm.ToolInput(tool_name="sentient_recipe", tool_args={"action": "next"}))
        assert step["say"] == "Brown the mince." and step["step"] == 1
        step = await api.async_call_tool(llm.ToolInput(tool_name="sentient_recipe", tool_args={"action": "next"}))
        assert step["timer_minutes"] == 20
        again = await api.async_call_tool(llm.ToolInput(tool_name="sentient_recipe", tool_args={"action": "repeat"}))
        assert again["step"] == 2
        await api.async_call_tool(llm.ToolInput(tool_name="sentient_recipe", tool_args={"action": "shopping"}))
        await rt.recipes.async_cook("lasagne", "other")  # opened again: no second AI call
    assert ai.await_count == 1 and len(added) == 2


async def test_mealie_recipe_needs_no_ai(hass: HomeAssistant, tmp_path) -> None:
    entry, device = await _setup(hass, tmp_path, recipes=True)
    from types import SimpleNamespace

    mealie = SimpleNamespace(entry_id="m1")

    async def get_recipes(call) -> ServiceResponse:
        return {"recipes": {"items": [{"name": "Chicken curry", "slug": "chicken-curry"}]}}

    async def get_recipe(call) -> ServiceResponse:
        return {"recipe": {"name": "Chicken curry", "recipe_yield": "4", "ingredients": [{"original_text": "2 chicken breasts"}],
                           "instructions": [{"text": "Fry the onions."}, {"text": "Add the chicken."}]}}

    hass.services.async_register("mealie", "get_recipes", get_recipes, supports_response=SupportsResponse.ONLY)
    hass.services.async_register("mealie", "get_recipe", get_recipe, supports_response=SupportsResponse.ONLY)
    with patch.object(entry.runtime_data.ai, "async_call", AsyncMock()) as ai, \
         patch.object(type(entry.runtime_data.recipes), "_mealie_entry", lambda self: mealie):
        out = await entry.runtime_data.recipes.async_cook("chicken curry", "s1")
    assert out["recipe"] == "Chicken curry" and out["ingredients"] == ["2 chicken breasts"] and not ai.called
