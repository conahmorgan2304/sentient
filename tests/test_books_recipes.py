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

    from custom_components.sentient.books import read_chapters, speech_pieces

    first = len(speech_pieces(read_chapters(str(tmp_path / "books" / "Flump.epub"))[0]["passages"][0]))

    async def read_aloud(text, speaker, **_kw):
        said.append(text)
        if len(said) == first + 1:
            rt.books._stop(speaker, save=False)  # the wake word, during the second passage
            raise asyncio.CancelledError
        return True

    with patch.object(rt.speech, "async_read_aloud", side_effect=read_aloud), patch("custom_components.sentient.books.asyncio.sleep", AsyncMock()), \
            patch("custom_components.sentient.books.Books._cut_short", return_value=False):  # these reads are instant by design
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


async def test_reading_paces_a_satellite_that_returns_straight_away(hass: HomeAssistant, tmp_path) -> None:
    """A browser tablet accepts an announcement instantly: the reader still waits for the passage to be spoken."""
    from custom_components.sentient.speech import reading_seconds

    entry, _device = await _setup(hass, tmp_path, books=True)
    calls = async_mock_service(hass, "assist_satellite", "announce")
    passage = "word " * 100
    with patch("custom_components.sentient.speech.asyncio.sleep", AsyncMock()) as sleep:
        assert await entry.runtime_data.speech.async_read_aloud(passage, "assist_satellite.bedroom")
    assert calls[-1].data["preannounce"] is False  # no chime before every paragraph
    assert max(c.args[0] for c in sleep.await_args_list) >= reading_seconds(passage) - 1 >= 39


async def test_read_hands_off_and_pause_after_the_wake_word_keeps_the_place(hass: HomeAssistant, tmp_path) -> None:
    (tmp_path / "books").mkdir()
    _epub(tmp_path / "books" / "Flump.epub")
    entry, device = await _setup(hass, tmp_path, books=True)
    rt = entry.runtime_data
    from custom_components.sentient.focus import TOPICS, turn_flags

    assert TOPICS["books"][0].search("play the princess story") and TOPICS["books"][0].search("pause")
    flags: dict = {}
    token = turn_flags.set(flags)
    try:
        with patch.object(rt.speech, "async_read_aloud", AsyncMock(return_value=True)), \
                patch("custom_components.sentient.books.Books._read_loop", AsyncMock()):
            api = await _api(hass, device)
            out = await api.async_call_tool(llm.ToolInput(tool_name="sentient_book", tool_args={"action": "read", "title": "Flump"}))
            assert flags["hand_off"] == out["say_first"] == "Starting Flump."
            rt.books._stop("assist_satellite.bedroom", save=False)  # the wake word stops it first
            paused = await api.async_call_tool(llm.ToolInput(tool_name="sentient_book", tool_args={"action": "pause"}))
            assert paused["stopped"] == "Flump"
            # finished books start again when asked for by name
            rt.books.marks["Flump|assist_satellite.bedroom"] = {"chapter": 1, "passage": 999}
            again = await api.async_call_tool(llm.ToolInput(tool_name="sentient_book", tool_args={"action": "read", "title": "Flump"}))
            assert again["reading"] and again["say_first"] == "Starting Flump."
            rt.books._stop("assist_satellite.bedroom", save=False)
    finally:
        turn_flags.reset(token)


async def test_a_story_reads_in_the_chosen_reading_voice(hass: HomeAssistant, tmp_path) -> None:
    """Books can have their own text-to-speech voice; Echos and recipes keep the usual one."""
    entry, _device = await _setup(hass, tmp_path, books=True, reading_tts="tts.storyteller", reading_voice="Kore")
    hass.states.async_set("tts.storyteller", "unknown")
    speech = entry.runtime_data.speech
    announce = async_mock_service(hass, "assist_satellite", "announce")
    speak = async_mock_service(hass, "tts", "speak")
    with patch("custom_components.sentient.speech.asyncio.sleep", AsyncMock()), \
            patch("custom_components.sentient.speech.tts_media_id", return_value="media-source://tts/x") as gen:
        assert await speech.async_read_aloud("Once upon a time.", "assist_satellite.bedroom", story=True)
        assert announce[-1].data["media_id"] == "media-source://tts/x" and announce[-1].data["message"] == "Once upon a time."
        assert gen.call_args.args[2] == "tts.storyteller" and gen.call_args.args[4] == {"voice": "Kore"}
        assert await speech.async_read_aloud("Once upon a time.", "media_player.bedroom", story=True)
        assert speak[-1].data["options"] == {"voice": "Kore"} and speak[-1].data["entity_id"] in ("tts.storyteller", ["tts.storyteller"])
        # a recipe step (not a story) stays in the usual voice
        n = len(announce)
        assert await speech.async_read_aloud("Chop the onions.", "assist_satellite.bedroom")
        assert len(announce) == n + 1 and "media_id" not in announce[-1].data


async def test_reading_voice_picker_and_try_it(hass: HomeAssistant, tmp_path, hass_ws_client) -> None:
    entry, _device = await _setup(hass, tmp_path, books=True, default_speaker="assist_satellite.bedroom")
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "sentient/settings"})
    res = (await ws.receive_json())["result"]
    reading = next(g for g in res["groups"] if g["id"] == "reading")["schema"]
    voice = next(f for f in reading if f["name"] == "reading_voice")
    assert "tts_voice" in voice["selector"] and voice["context"] == {"engineId": "reading_tts"}
    announce = async_mock_service(hass, "assist_satellite", "announce")
    await ws.send_json({"id": 2, "type": "sentient/reading/try"})
    out = (await ws.receive_json())["result"]
    assert out["ok"] and out["speaker_name"] == "Bedroom" and "fox" in announce[-1].data["message"]


def test_passages_are_spoken_in_short_pieces_at_sentence_ends() -> None:
    from custom_components.sentient.books import speech_pieces

    passage = "She opened the door. \"Who is there?\" she asked! Nobody answered... " * 12
    pieces = speech_pieces(passage)
    assert len(pieces) == 3 and all(len(p) <= 330 for p in pieces)
    assert " ".join(pieces).split() == passage.split()  # nothing lost or repeated
    assert pieces[0].endswith(("!", ".", "?", '"'))


async def test_next_piece_made_while_this_one_is_read(hass: HomeAssistant, tmp_path) -> None:
    """With a reading voice, each next piece's audio is asked for before the current one is read."""
    (tmp_path / "books").mkdir()
    _epub(tmp_path / "books" / "Flump.epub")
    entry, device = await _setup(hass, tmp_path, books=True, reading_tts="tts.storyteller")
    hass.states.async_set("tts.storyteller", "unknown")
    rt = entry.runtime_data
    events: list[tuple[str, str]] = []
    made: list[str] = []

    async def read_aloud(text, speaker, **_kw):
        events.append(("read", text))
        if len([e for e in events if e[0] == "read"]) >= 3:
            rt.books._stop(speaker, save=False)
            raise asyncio.CancelledError
        return True

    async def make(media_id):
        made.append(media_id)

    with patch.object(rt.speech, "async_read_aloud", side_effect=read_aloud), \
            patch("custom_components.sentient.books.asyncio.sleep", AsyncMock()), \
            patch("custom_components.sentient.speech.tts_media_id", side_effect=lambda h, text, *a: f"tts:{text}"), \
            patch.object(rt.speech, "_make_audio", side_effect=make):
        orig = rt.speech.prepare_reading

        def prepare(text, speaker):
            events.append(("prepare", text))
            orig(text, speaker)

        rt.speech.prepare_reading = prepare
        api = await _api(hass, device)
        await api.async_call_tool(llm.ToolInput(tool_name="sentient_book", tool_args={"action": "read", "title": "flump"}))
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()
    reads = [t for k, t in events if k == "read"]
    prepared = [t for k, t in events if k == "prepare"]
    assert prepared[0] == reads[0]  # the first piece is made during "starting…"
    for i in range(1, len(reads)):  # each next piece was asked for before the one before it was read
        assert events.index(("prepare", reads[i])) < events.index(("read", reads[i - 1]))
    assert made and made[0].startswith("tts:")
    rt.speech.options = {**rt.speech.options, "read_ahead": False}
    n = len(made)
    orig("Another piece.", "assist_satellite.bedroom")
    await hass.async_block_till_done()
    assert len(made) == n  # off: nothing made ahead


def test_damaged_and_oversized_books_give_no_text_instead_of_crashing(tmp_path, monkeypatch):
    import zipfile

    from custom_components.sentient import books

    broken = tmp_path / "broken.epub"
    broken.write_bytes(b"not a zip file")
    assert books.read_chapters(str(broken)) == []

    no_package = tmp_path / "nopackage.epub"
    with zipfile.ZipFile(no_package, "w") as z:
        z.writestr("META-INF/container.xml", "<container></container>")
    assert books.read_chapters(str(no_package)) == []

    big = tmp_path / "big.txt"
    big.write_text("word " * 100)
    monkeypatch.setattr(books, "MAX_BOOK_BYTES", 10)
    assert books.read_chapters(str(big)) == []
    monkeypatch.setattr(books, "MAX_BOOK_BYTES", 10_000)
    assert books.read_chapters(str(big))


async def test_a_story_that_keeps_being_cut_short_pauses_and_keeps_the_place(hass: HomeAssistant, tmp_path) -> None:
    """Tapping a tablet's screen ends the passage being played; the reader used to carry on to the next one,
    so a child tapping skipped through the book with no way to stop it."""
    (tmp_path / "books").mkdir()
    _epub(tmp_path / "books" / "Flump.epub")
    entry, _device = await _setup(hass, tmp_path, books=True)
    books = entry.runtime_data.books
    spoken: list[str] = []
    real_sleep = asyncio.sleep

    async def quick_sleep(_seconds):
        await real_sleep(0)

    async def cut_short(text, speaker, story=False):
        spoken.append(text)  # accepted, and over in an instant, as if the screen had been tapped
        return True

    with patch.object(entry.runtime_data.speech, "async_read_aloud", cut_short), \
            patch.object(entry.runtime_data.speech, "prepare_reading"), \
            patch("custom_components.sentient.books.asyncio.sleep", quick_sleep):
        await books.async_read("Flump", "assist_satellite.bedroom")
        task = books.reading["assist_satellite.bedroom"]["task"]
        await asyncio.wait_for(task, 5)
    assert "assist_satellite.bedroom" not in books.reading  # it stopped by itself
    assert len([s for s in spoken if "Sentence" in s]) == 3  # after three cut-off pieces, not the whole book
    assert "paused" in spoken[-1].lower()
    mark = books.place("Flump", "assist_satellite.bedroom")
    assert (mark["chapter"], mark["passage"]) == (0, 0)  # nothing counts as heard


async def test_stop_reading_service_stops_even_without_the_voice(hass: HomeAssistant, tmp_path) -> None:
    entry, _device = await _setup(hass, tmp_path, books=True)
    books = entry.runtime_data.books
    stopped = async_mock_service(hass, "media_player", "media_stop")
    task = hass.async_create_background_task(asyncio.sleep(60), "story")
    books.reading["assist_satellite.bedroom"] = {"task": task, "book": "Flump", "unsub": None}
    out = await hass.services.async_call(DOMAIN, "stop_reading", {}, blocking=True, return_response=True)
    await hass.async_block_till_done()
    assert out["stopped"] == ["Flump"] and task.cancelled() and not books.reading
    assert stopped and stopped[-1].data["entity_id"] == "media_player.bedroom"  # the sound is stopped too
    # and a named speaker is silenced even when nothing was registered as reading there
    await hass.services.async_call(DOMAIN, "stop_reading", {"speaker": "assist_satellite.bedroom"}, blocking=True, return_response=True)
    assert len(stopped) == 2


async def test_reading_stop_command_from_the_panel(hass: HomeAssistant, tmp_path, hass_ws_client) -> None:
    entry, _device = await _setup(hass, tmp_path, books=True)
    books = entry.runtime_data.books
    task = hass.async_create_background_task(asyncio.sleep(60), "story")
    books.reading["assist_satellite.bedroom"] = {"task": task, "book": "Flump", "unsub": None}
    async_mock_service(hass, "media_player", "media_stop")
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "sentient/reading/stop"})
    assert (await client.receive_json())["result"] == {"stopped": ["Flump"]}
    await hass.async_block_till_done()
    assert task.cancelled() and not books.reading
