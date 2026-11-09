"""Books: audiobooks played and ebooks read aloud, each picking up where it was left off.

Put books in a folder of Home Assistant's media (books by default):
* an ebook — an .epub or .txt file (DRM-free) — is read aloud by the house voice, a short passage at a time;
* an audiobook — an .mp3/.m4b/.m4a/.ogg file, or a folder of them for one book — is played on a speaker.

Every book keeps a bookmark per speaker, so a bedtime book in one room doesn't move another room's
place. A passage that was interrupted is started again rather than skipped. "Read for 20 minutes"
stops by itself and keeps the place. Saying the wake word, or "stop", stops it. No AI, except an
optional one-call recap of what was read last time. Echos cut long speech short and can't play the
house's own audio files, so through an Echo an ebook comes one passage per request instead.
"""

from __future__ import annotations

import asyncio
import logging
from html.parser import HTMLParser
import os
import posixpath
import re
from typing import Any
import zipfile

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import CONF_BOOKS, CONF_BOOKS_FOLDER, DEFAULT_BOOKS_FOLDER, DOMAIN

STORAGE_VERSION = 1
EBOOK = {".epub", ".txt"}
AUDIO = {".mp3": "audio/mpeg", ".m4b": "audio/mp4", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".opus": "audio/ogg"}
_LOGGER = logging.getLogger(__name__)

MAX_BOOK_BYTES = 30 * 1024 * 1024  # a book longer than this is refused rather than loaded into memory
MAX_PART_BYTES = 8 * 1024 * 1024  # one chapter file inside an epub
PASSAGE_CHARS = 700  # about 45 seconds of speech
_WORD = re.compile(r"[a-z0-9]+")


# ----- reading files -------------------------------------------------------------------------
class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "li", "tr", "section", "blockquote"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self._skip = max(0, self._skip - 1)
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _html_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    return "\n".join(" ".join(line.split()) for line in "".join(p.parts).splitlines() if line.strip())


def read_chapters(path: str) -> list[dict[str, Any]]:
    """[{title, passages: [str]}] for an .epub or .txt file; empty when it is too big or can't be read."""
    try:
        if os.path.getsize(path) > MAX_BOOK_BYTES:
            _LOGGER.warning("Book %s is over %d MB and was skipped", os.path.basename(path), MAX_BOOK_BYTES // 1024 // 1024)
            return []
        return _read_chapters(path)
    except (OSError, zipfile.BadZipFile, KeyError, AttributeError, ValueError) as err:
        _LOGGER.warning("Couldn't read book %s: %s", os.path.basename(path), err)
        return []


def _read_chapters(path: str) -> list[dict[str, Any]]:
    chapters: list[tuple[str, str]] = []
    if path.lower().endswith(".txt"):
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        parts = re.split(r"\n\s*(?=(?:chapter|part)\s+[\w ]{1,30}\n)", text, flags=re.I)
        for n, part in enumerate(parts, 1):
            first = part.strip().splitlines()[0] if part.strip() else ""
            chapters.append((first[:60] if re.match(r"(chapter|part)\b", first, re.I) else f"Part {n}", part))
    else:
        with zipfile.ZipFile(path) as z:
            container = z.read("META-INF/container.xml").decode("utf-8", "replace")
            found = re.search(r'full-path="([^"]+)"', container)
            if found is None:
                raise ValueError("no package file listed in the epub")
            opf_path = found.group(1)
            opf = z.read(opf_path).decode("utf-8", "replace")
            base = posixpath.dirname(opf_path)
            manifest = {m.group(1): m.group(2) for m in re.finditer(r'<item\b[^>]*\bid="([^"]+)"[^>]*\bhref="([^"]+)"', opf)}
            manifest.update({m.group(2): m.group(1) for m in re.finditer(r'<item\b[^>]*\bhref="([^"]+)"[^>]*\bid="([^"]+)"', opf)})
            for idref in re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', opf):
                href = manifest.get(idref)
                if not href:
                    continue
                member = posixpath.normpath(posixpath.join(base, href))
                try:
                    if z.getinfo(member).file_size > MAX_PART_BYTES:
                        continue  # a chapter that big is not text
                    html = z.read(member).decode("utf-8", "replace")
                except KeyError:
                    continue
                text = _html_text(html)
                if len(text) < 200:
                    continue  # covers, title pages, contents
                title = text.splitlines()[0][:60]
                chapters.append((title, text))
    out = []
    for title, text in chapters:
        passages, cur = [], ""
        for para in [p.strip() for p in re.split(r"\n+", text) if p.strip()]:
            if cur and len(cur) + len(para) > PASSAGE_CHARS:
                passages.append(cur)
                cur = ""
            cur = f"{cur} {para}".strip()
            while len(cur) > PASSAGE_CHARS * 2:  # one huge paragraph: split at a sentence
                cut = cur.rfind(". ", 0, PASSAGE_CHARS * 2) + 1 or PASSAGE_CHARS * 2
                passages.append(cur[:cut].strip())
                cur = cur[cut:].strip()
        if cur:
            passages.append(cur)
        if passages:
            out.append({"title": title, "passages": passages})
    return out


def scan(base: str) -> list[dict[str, Any]]:
    """Every book in the folder: [{name, kind: ebook|audio, files: [relative paths]}]."""
    if not os.path.isdir(base):
        return []
    out = []
    for entry in sorted(os.listdir(base)):
        full = os.path.join(base, entry)
        stem, ext = os.path.splitext(entry)
        if os.path.isdir(full):
            tracks = sorted(f for f in os.listdir(full) if os.path.splitext(f)[1].lower() in AUDIO)
            if tracks:
                out.append({"name": entry, "kind": "audio", "files": [f"{entry}/{t}" for t in tracks]})
        elif ext.lower() in EBOOK:
            out.append({"name": stem, "kind": "ebook", "files": [entry]})
        elif ext.lower() in AUDIO:
            out.append({"name": stem, "kind": "audio", "files": [entry]})
    return out


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 1 and w not in {"the", "book", "read", "my", "of", "a", "and"}}


def match(books: list[dict[str, Any]], title: str) -> dict[str, Any] | None:
    want = _words(title)
    best, score = None, 0
    for b in books:
        if b["name"].lower() == title.strip().lower():
            return b
        s = len(want & _words(b["name"]))
        if s > score:
            best, score = b, s
    return best


SPEECH_PIECE = 320  # characters: about 20 seconds of speech — speakers cope better than with a minute at once


def speech_pieces(passage: str, size: int = SPEECH_PIECE) -> list[str]:
    """A passage cut at sentence ends into pieces of about *size* characters (a long sentence stays whole)."""
    sentences = [m.strip() for m in re.findall(r"[^.!?…]+(?:[.!?…]+[\"'”’)]*|$)", passage) if m.strip()]
    pieces, current = [], ""
    for sentence in sentences:
        if current and len(current) + 1 + len(sentence) > size:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


# ----- the reader ----------------------------------------------------------------------------
CUT_SHORT_LIMIT = 3  # pieces in a row cut short before the story pauses itself
CUT_SHORT_RATIO = 0.35  # a piece over in under this share of its time was cut short
CUT_SHORT_MIN_SECONDS = 6.0  # shorter pieces are too quick to tell


class Books:
    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any], rt_getter) -> None:
        self.hass = hass
        self.options = options
        self._rt = rt_getter
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.books")
        self.marks: dict[str, dict[str, Any]] = {}  # "book|speaker" -> place
        self.last: dict[str, str] = {}  # speaker -> last book on it
        self._chapters: dict[str, list[dict[str, Any]]] = {}
        self.reading: dict[str, dict[str, Any]] = {}  # speaker -> {task, book, unsub}

    @property
    def enabled(self) -> bool:
        return bool(self.options.get(CONF_BOOKS))

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.marks, self.last = data.get("marks", {}), data.get("last", {})

    @callback
    def async_stop(self) -> None:
        for speaker in list(self.reading):
            self._stop(speaker, save=True)

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"marks": self.marks, "last": self.last}, 5)

    def folder(self) -> str:
        sub = str(self.options.get(CONF_BOOKS_FOLDER) or DEFAULT_BOOKS_FOLDER).strip().strip("/")
        if ".." in sub.split("/"):
            sub = DEFAULT_BOOKS_FOLDER  # only ever a folder inside Home Assistant's media
        base = (self.hass.config.media_dirs or {}).get("local") or self.hass.config.path("media")
        return os.path.join(base, sub)

    def media_id(self, rel: str) -> str:
        sub = str(self.options.get(CONF_BOOKS_FOLDER) or DEFAULT_BOOKS_FOLDER).strip().strip("/")
        if ".." in sub.split("/"):
            sub = DEFAULT_BOOKS_FOLDER
        return f"media-source://media_source/local/{sub}/{rel}"

    async def async_books(self) -> list[dict[str, Any]]:
        return await self.hass.async_add_executor_job(scan, self.folder())

    async def _chapters_of(self, book: dict[str, Any]) -> list[dict[str, Any]]:
        if book["name"] not in self._chapters:
            path = os.path.join(self.folder(), book["files"][0])
            self._chapters[book["name"]] = await self.hass.async_add_executor_job(read_chapters, path)
        return self._chapters[book["name"]]

    def is_echo(self, speaker: str) -> bool:
        entry = er.async_get(self.hass).async_get(speaker)
        return bool(entry and entry.platform == "alexa_media")

    def player_for(self, speaker: str) -> str | None:
        """A media player that can play audio files: the speaker itself, or the one on a satellite's device."""
        if speaker.startswith("media_player."):
            return None if self.is_echo(speaker) else speaker
        ents = er.async_get(self.hass)
        entry = ents.async_get(speaker)
        if entry and entry.device_id:
            for e in er.async_entries_for_device(ents, entry.device_id):
                if e.domain == "media_player":
                    return e.entity_id
        return None

    # ----- status -------------------------------------------------------------------------
    def place(self, book: str, speaker: str) -> dict[str, Any] | None:
        return self.marks.get(f"{book}|{speaker}")

    def listing(self) -> list[dict[str, Any]]:
        out = []
        for key, mark in self.marks.items():
            book, speaker = key.split("|", 1)
            st = self.hass.states.get(speaker)
            out.append({"book": book, "speaker": st.name if st else speaker, "where": mark.get("where", ""),
                        "when": mark.get("when"), "reading": speaker in self.reading and self.reading[speaker]["book"] == book})
        return sorted(out, key=lambda m: m.get("when") or "", reverse=True)

    # ----- reading and playing ----------------------------------------------------------------
    async def async_read(self, title: str | None, speaker: str, minutes: float | None = None,
                         via_reply: bool = False) -> dict[str, Any]:
        books = await self.async_books()
        if not books:
            return {"error": f"no books yet — put .epub, .txt or audiobook files in the media folder '{self.options.get(CONF_BOOKS_FOLDER) or DEFAULT_BOOKS_FOLDER}'"}
        name = title or self.last.get(speaker)
        book = match(books, name) if name else None
        if book is None:
            return {"error": "which book?", "books": [b["name"] for b in books]}
        self._stop(speaker, save=True)
        self.last[speaker] = book["name"]
        if book["kind"] == "audio":
            if via_reply or self.player_for(speaker) is None:
                return {"error": "audiobooks need a speaker that can play audio files — Echos can't play the house's own files; try a voice satellite or another speaker"}
            return await self._play_audio(book, speaker, minutes)
        chapters = await self._chapters_of(book)
        if not chapters:
            return {"error": f"couldn't find any text in {book['name']} (is it DRM-free?)"}
        mark = self.place(book["name"], speaker) or {"chapter": 0, "passage": 0}
        ch, ps = min(mark["chapter"], len(chapters) - 1), mark["passage"]
        if ps >= len(chapters[ch]["passages"]):
            ch, ps = ch + 1, 0
        if ch >= len(chapters):
            if not title:
                return {"book": book["name"], "finished": True, "note": "That's the end of the book. Say 'start it again' to begin from the start."}
            ch, ps = 0, 0  # asked for by name after finishing it: start it again
        where = f"chapter {ch + 1} of {len(chapters)}" + (f", {chapters[ch]['title']}" if chapters[ch]["title"] else "")
        if via_reply:  # an Echo through the Alexa skill: one passage as the answer, then "carry on"
            text = chapters[ch]["passages"][ps]
            self._mark(book["name"], speaker, ch, ps + 1, chapters)
            return {"book": book["name"], "where": where, "read_aloud": text,
                    "note": "Reply with exactly the read_aloud text, then a short 'say carry on for more'. Don't summarise it."}
        task = self.hass.async_create_background_task(self._read_loop(book, speaker, ch, ps, minutes), f"sentient book {book['name']}")
        self.reading[speaker] = {"task": task, "book": book["name"], "unsub": self._watch_wake(speaker)}
        start = "Starting" if (ch, ps) == (0, 0) else "Picking up in"
        return {"book": book["name"], "reading": True, "where": where, "for_minutes": minutes,
                "say_first": f"{start} {where}." if (ch, ps) != (0, 0) else f"Starting {book['name']}.",
                "note": "Say say_first in a few words; the reading starts on that speaker straight after."}

    def _mark(self, book: str, speaker: str, ch: int, ps: int, chapters: list[dict[str, Any]]) -> None:
        if ps >= len(chapters[min(ch, len(chapters) - 1)]["passages"]) and ch + 1 < len(chapters):
            ch, ps = ch + 1, 0
        title = chapters[min(ch, len(chapters) - 1)]["title"]
        self.marks[f"{book}|{speaker}"] = {"chapter": ch, "passage": ps, "when": dt_util.utcnow().isoformat(),
                                           "where": f"chapter {ch + 1} of {len(chapters)}" + (f" ({title})" if title else "")}
        self._save()

    async def _read_loop(self, book: dict[str, Any], speaker: str, ch: int, ps: int, minutes: float | None) -> None:
        chapters = await self._chapters_of(book)
        rt = self._rt()
        until = dt_util.utcnow().timestamp() + minutes * 60 if minutes else None
        loop = asyncio.get_running_loop()
        cuts, first = 0, (ch, ps)
        if ch < len(chapters) and ps < len(chapters[ch]["passages"]):
            rt.speech.prepare_reading(speech_pieces(chapters[ch]["passages"][ps])[0], speaker)  # ready by the time it's needed
        await asyncio.sleep(4)  # let the voice finish its "picking up in…"
        try:
            while ch < len(chapters):
                passages = chapters[ch]["passages"]
                while ps < len(passages):
                    if until and dt_util.utcnow().timestamp() >= until:
                        await rt.speech.async_read_aloud("I'll stop there and keep your place.", speaker, story=True)
                        return
                    pieces = speech_pieces(passages[ps])
                    following = speech_pieces(passages[ps + 1])[:1] if ps + 1 < len(passages) else []
                    upcoming = pieces + following
                    for i, piece in enumerate(pieces):
                        if i + 1 < len(upcoming):
                            rt.speech.prepare_reading(upcoming[i + 1], speaker)  # made while this one is read
                        began = loop.time()
                        if not await rt.speech.async_read_aloud(piece, speaker, story=True):
                            return  # couldn't speak: keep the place on this passage
                        if self._cut_short(piece, loop.time() - began):
                            if not cuts:
                                first = (ch, ps)
                            cuts += 1
                            if cuts >= CUT_SHORT_LIMIT:
                                # a tap on the screen ends the sound but not the story: stop rather than skip on,
                                # and keep the place where the first cut-off piece began
                                self._mark(book["name"], speaker, *first, chapters)
                                await rt.speech.async_read_aloud(
                                    "I've paused the story because it keeps being interrupted. Say carry on when you want it back.",
                                    speaker, story=True)
                                return
                        else:
                            cuts = 0
                    ps += 1
                    self._mark(book["name"], speaker, ch, ps, chapters)  # only once it has been read
                ch, ps = ch + 1, 0
                if ch < len(chapters):
                    await rt.speech.async_read_aloud(f"Chapter {ch + 1}.", speaker, story=True)
            await rt.speech.async_read_aloud(f"That's the end of {book['name']}.", speaker, story=True)
        finally:
            info = self.reading.get(speaker)
            if info and info.get("book") == book["name"]:
                if info.get("unsub"):
                    info["unsub"]()
                self.reading.pop(speaker, None)

    @staticmethod
    def _cut_short(piece: str, took: float) -> bool:
        """A piece that was over far sooner than it takes to say was stopped (a tap on the screen), not read."""
        from .speech import reading_seconds

        expected = reading_seconds(piece)
        return expected >= CUT_SHORT_MIN_SECONDS and took < expected * CUT_SHORT_RATIO

    async def async_silence(self, speaker: str | None = None) -> dict[str, Any]:
        """Stop whatever is being read, and the sound itself, without needing to be heard or understood.

        The way out when the voice can't be used: the wake word can't be heard over the story, and a tablet
        has no wake word to pause it with."""

        if speaker:
            speakers = [speaker]
        else:  # every reading, and any speaker it read on lately that is still making sound (a story that lost track)
            speakers = list(self.reading) + [
                sp for sp in self.last if sp not in self.reading
                and ((st := self.hass.states.get(sp)) is not None and st.state in ("playing", "responding"))
            ]
        stopped: list[str] = []
        for sp in speakers:
            info = self.reading.get(sp)
            if info and "player" in info:
                self._save_audio(sp)
            if (book := self._stop(sp, save=False)) is not None:
                stopped.append(book)
            player = sp if sp.startswith("media_player.") else self.player_for(sp)
            if player:
                await self.hass.services.async_call("media_player", "media_stop", {"entity_id": player}, blocking=False)
        return {"stopped": stopped}

    def _watch_wake(self, speaker: str):
        """Stop reading when someone says the wake word on that speaker (the passage is kept)."""
        if not speaker.startswith("assist_satellite."):
            return None

        @callback
        def changed(event: Event) -> None:
            new = event.data.get("new_state")
            if new is not None and new.state == "listening":
                self._stop(speaker, save=False)

        return async_track_state_change_event(self.hass, [speaker], changed)

    async def _play_audio(self, book: dict[str, Any], speaker: str, minutes: float | None) -> dict[str, Any]:
        player = self.player_for(speaker)
        mark = self.place(book["name"], speaker) or {"track": 0, "position": 0}
        track = min(mark.get("track", 0), len(book["files"]) - 1)
        position = float(mark.get("position") or 0)
        await self._play_track(book, speaker, player, track, position)
        unsub = async_track_state_change_event(self.hass, [player], lambda e: self._audio_changed(e, book, speaker, player))
        task = None
        if minutes:
            task = self.hass.async_create_background_task(self._sleep_timer(speaker, minutes), "sentient book timer")
        self.reading[speaker] = {"task": task, "book": book["name"], "unsub": unsub, "player": player, "track": track,
                                 "audio": book}
        where = f"part {track + 1} of {len(book['files'])}" if len(book["files"]) > 1 else ""
        mins = int(position // 60)
        return {"book": book["name"], "playing": True, "on": player,
                "say_first": (f"Picking up {mins} minutes in" + (f" to {where}" if where else "") + ".") if position > 30 else f"Starting {book['name']}."}

    async def _play_track(self, book, speaker, player, track, position) -> None:
        rel = book["files"][track]
        await self.hass.services.async_call("media_player", "play_media", {
            "entity_id": player, "media_content_id": self.media_id(rel),
            "media_content_type": AUDIO.get(os.path.splitext(rel)[1].lower(), "music")}, blocking=True)
        if position > 5:
            for _ in range(10):
                await asyncio.sleep(0.5)
                st = self.hass.states.get(player)
                if st and st.state == "playing":
                    break
            try:
                await self.hass.services.async_call("media_player", "media_seek",
                                                    {"entity_id": player, "seek_position": position}, blocking=True)
            except Exception:  # noqa: BLE001 - some players can't seek; it starts from the top of the part
                pass

    @callback
    def _audio_changed(self, event: Event, book, speaker, player) -> None:
        old, new = event.data.get("old_state"), event.data.get("new_state")
        info = self.reading.get(speaker)
        if not info or new is None:
            return
        if old is not None and old.state == "playing" and new.state in ("idle", "off"):
            dur, pos = _num(old.attributes.get("media_duration")), self._position(old)
            if dur and pos is not None and dur - pos < 15 and info["track"] + 1 < len(book["files"]):
                info["track"] += 1  # that part finished: on to the next
                self.marks[f"{book['name']}|{speaker}"] = {"track": info["track"], "position": 0, "when": dt_util.utcnow().isoformat(),
                                                          "where": f"part {info['track'] + 1} of {len(book['files'])}"}
                self._save()
                self.hass.async_create_task(self._play_track(book, speaker, player, info["track"], 0))
            else:
                self._save_audio(speaker, old)

    @staticmethod
    def _position(st) -> float | None:
        pos = _num(st.attributes.get("media_position"))
        at = st.attributes.get("media_position_updated_at")
        if pos is None:
            return None
        if st.state == "playing" and at:
            when = at if not isinstance(at, str) else dt_util.parse_datetime(at)
            if when:
                pos += (dt_util.utcnow() - when).total_seconds()
        return pos

    def _save_audio(self, speaker: str, st=None) -> None:
        info = self.reading.get(speaker)
        if not info or "audio" not in info:
            return
        st = st or self.hass.states.get(info["player"])
        pos = self._position(st) if st else None
        if pos is None:
            return
        n = len(info["audio"]["files"])
        self.marks[f"{info['book']}|{speaker}"] = {
            "track": info["track"], "position": max(0.0, pos - 10), "when": dt_util.utcnow().isoformat(),  # back 10 s
            "where": (f"part {info['track'] + 1} of {n}, " if n > 1 else "") + f"{int(pos // 60)} minutes in"}
        self._save()

    async def _sleep_timer(self, speaker: str, minutes: float) -> None:
        await asyncio.sleep(minutes * 60)
        info = self.reading.get(speaker)
        if info and "player" in info:
            self._save_audio(speaker)
            await self.hass.services.async_call("media_player", "media_pause", {"entity_id": info["player"]}, blocking=False)
            self._stop(speaker, save=False)

    @callback
    def _stop(self, speaker: str, save: bool) -> str | None:
        info = self.reading.pop(speaker, None)
        if not info:
            return None
        if save and "player" in info:
            self._save_audio(speaker)
        if info.get("unsub"):
            info["unsub"]()
        if (task := info.get("task")) is not None and not task.done():
            task.cancel()
        return info["book"]

    async def async_stop_reading(self, speaker: str) -> dict[str, Any]:
        info = self.reading.get(speaker)
        if info and "player" in info:
            self._save_audio(speaker)
            await self.hass.services.async_call("media_player", "media_pause", {"entity_id": info["player"]}, blocking=False)
        book = self._stop(speaker, save=False)
        if book is None:
            # the wake word already stopped it (that's how "pause" usually arrives): the place is kept either way
            if (book := self.last.get(speaker)) is None:
                return {"error": "nothing is being read there"}
        return {"stopped": book, "kept_place": (self.place(book, speaker) or {}).get("where"),
                "note": "Say it's paused and where; 'carry on' picks up from there."}

    async def async_move(self, speaker: str, how: str) -> dict[str, Any]:
        """next chapter / previous chapter / back a bit / restart, for an ebook."""
        name = (self.reading.get(speaker) or {}).get("book") or self.last.get(speaker)
        books = await self.async_books()
        book = match(books, name) if name else None
        if book is None or book["kind"] != "ebook":
            return {"error": "that only works for a book being read aloud"}
        chapters = await self._chapters_of(book)
        mark = self.place(book["name"], speaker) or {"chapter": 0, "passage": 0}
        ch, ps = mark["chapter"], mark["passage"]
        if how == "next_chapter":
            ch, ps = min(ch + 1, len(chapters) - 1), 0
        elif how == "previous_chapter":
            ch, ps = max(ch - (0 if ps > 1 else 1), 0), 0
        elif how == "back":
            ps = max(ps - 2, 0)
        elif how == "restart":
            ch, ps = 0, 0
        self._mark(book["name"], speaker, ch, ps, chapters)
        return await self.async_read(book["name"], speaker)

    async def recent_text(self, book_name: str, speaker: str, passages: int = 6) -> str:
        books = await self.async_books()
        book = match(books, book_name)
        if book is None or book["kind"] != "ebook":
            return ""
        chapters = await self._chapters_of(book)
        mark = self.place(book["name"], speaker) or {"chapter": 0, "passage": 0}
        flat = [p for c in chapters[: mark["chapter"] + 1] for p in c["passages"]]
        upto = sum(len(c["passages"]) for c in chapters[: mark["chapter"]]) + mark["passage"]
        return "\n".join(flat[max(0, upto - passages):upto])


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
