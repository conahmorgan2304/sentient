"""Manuals read page by page, so an answer can say which page it's on and the AI reads only the pages that matter.

Each PDF in the manuals folder is read once (and again only when the file changes) with pypdf, a small
pure-Python reader. The text of every page goes into Sentient's own database with full-text search, the same
FTS5/BM25 search as the memories. Text documents (.txt, .md) are split into sections the same way.

Asked a question, Sentient searches the pages and gives the AI only the best few, numbered, instead of the
whole PDF: a much smaller AI call, and the answer can say "page 14". If those pages don't answer it, the whole
document is read as before. When the AI can't be reached, the closest passage is quoted with its page.

Scanned PDFs (pictures of pages, no text in them) can't be read this way; they're still read whole by the AI.
Nothing is sent anywhere to read them. Switch: "Read PDFs page by page" (Settings → Manuals & documents);
switching it off clears what was read.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from typing import Any

_LOGGER = logging.getLogger(__name__)

CONF_MANUALS_PAGES = "manuals_pages"
READ = (".pdf", ".txt", ".md")
MAX_PAGES = 600
MAX_PAGE_CHARS = 8000
MAX_FILE_BYTES = 80 * 1_048_576
SECTION_CHARS = 1500
MIN_TEXT = 40  # a page with less than this is treated as having no text (a picture, a blank page)

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS doc_files (file TEXT PRIMARY KEY, mtime REAL, size INTEGER, pages INTEGER,
        read_pages INTEGER, unit TEXT, error TEXT)""",
    """CREATE TABLE IF NOT EXISTS doc_pages (file TEXT NOT NULL, page INTEGER NOT NULL, label TEXT, body TEXT,
        PRIMARY KEY (file, page))""",
    """CREATE TABLE IF NOT EXISTS doc_index_meta (key TEXT PRIMARY KEY, value TEXT)""",
]
FTS = "CREATE VIRTUAL TABLE IF NOT EXISTS doc_pages_fts USING fts5(file UNINDEXED, page UNINDEXED, body)"
_SPACE = re.compile(r"[ \t\r\f\v]+")


def _tidy(text: str) -> str:
    lines = [_SPACE.sub(" ", line).strip() for line in (text or "").splitlines()]
    out = "\n".join(line for line in lines if line)
    return out[:MAX_PAGE_CHARS]


def _labels(reader: Any, count: int) -> list[str]:
    """The page numbers printed in the document where it has them (front matter i, ii…), else 1, 2, 3."""
    try:
        labels = list(reader.page_labels)[:count]
        if len(labels) == count and any(str(lab) != str(i + 1) for i, lab in enumerate(labels)):
            return [str(lab) for lab in labels]
    except Exception:  # noqa: BLE001 - labels are a nicety
        pass
    return [str(i + 1) for i in range(count)]


def read_pdf(path: str, *, local_ocr: bool = False) -> tuple[list[tuple[int, str, str]], int]:
    """([(page number, printed label, text)], total pages). Raises if it can't be read at all."""
    from pypdf import PdfReader

    reader = PdfReader(path)
    if reader.is_encrypted:
        reader.decrypt("")  # many manuals are "encrypted" with an empty password to stop editing
    total = len(reader.pages)
    count = min(total, MAX_PAGES)
    labels = _labels(reader, count)
    out = []
    ocr_pages = 0
    for i in range(count):
        try:
            text = _tidy(reader.pages[i].extract_text() or "")
        except Exception:  # noqa: BLE001 - one bad page doesn't lose the rest
            text = ""
        if local_ocr and len(text) < MIN_TEXT and ocr_pages < 20:
            from .local_ocr import extract_page
            text = _tidy(extract_page(reader.pages[i])) or text
            ocr_pages += 1
        out.append((i + 1, labels[i], text))
    return out, total


def quality_report(pages: list[tuple[int, str, str]]) -> dict[str, Any]:
    """Inspect text extraction only; never imply OCR or page-level proof."""
    from .document_quality import assess

    return assess([text for _, _, text in pages], min_chars=MIN_TEXT)


def read_text(path: str) -> tuple[list[tuple[int, str, str]], int]:
    """A text document as sections of a few paragraphs, each labelled by its heading if it has one."""
    with open(path, "rb") as handle:
        text = handle.read(4 * 1_048_576).decode("utf-8", errors="replace")
    sections: list[tuple[str, str]] = []
    heading, buf, size = "", [], 0

    def flush() -> None:
        nonlocal buf, size
        if buf:
            sections.append((heading, "\n\n".join(buf)))
            buf, size = [], 0

    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        first = block.splitlines()[0]
        if first.startswith("#"):
            flush()
            heading = first.lstrip("#").strip()[:60]
        # A single pasted paragraph can be much longer than a page. Split it
        # rather than silently truncating everything past MAX_PAGE_CHARS.
        while block:
            capacity = SECTION_CHARS - size - (2 if buf else 0)
            if capacity < 100:
                flush()
                capacity = SECTION_CHARS
            if len(block) <= capacity:
                buf.append(block)
                size += len(block) + (2 if len(buf) > 1 else 0)
                break
            cut = block.rfind(" ", max(0, capacity // 2), capacity + 1)
            if cut < capacity // 2:
                cut = capacity
            part = block[:cut].strip()
            if part:
                buf.append(part)
            flush()
            block = block[cut:].lstrip()
    flush()
    out = [(i + 1, head or str(i + 1), _tidy(body)) for i, (head, body) in enumerate(sections[:MAX_PAGES])]
    return out, len(sections)


def sync(db: Any, folder: str, files: list[str], *, local_ocr: bool = False) -> dict[str, Any]:
    """Read changed pages without clearing the previous index before successful extraction.

    When OCR changes, rebuild into SQLite TEMP staging tables. Only a complete
    extraction swaps the index and its mode marker inside one transaction.
    Crashes, unavailable PDFs or disk errors leave the old index usable.
    """
    wanted = {f for f in files if os.path.splitext(f)[1].lower() in READ and f == os.path.basename(f)
              and not f.startswith(".")}
    from .local_ocr import available as ocr_available

    ocr_installed = ocr_available()
    actual_ocr = bool(local_ocr and ocr_installed)
    mode = "1" if actual_ocr else "0"

    def _known(conn: sqlite3.Connection) -> tuple[dict[str, tuple[float, int]], bool]:
        previous = conn.execute("SELECT value FROM doc_index_meta WHERE key = ?", ("local_ocr",)).fetchone()
        changed = previous is not None and previous["value"] != mode
        # Existing installations without a marker need their first OCR-enabled pass.
        if previous is None and actual_ocr:
            changed = True
        known = {r["file"]: (r["mtime"], r["size"]) for r in
                 conn.execute("SELECT file, mtime, size FROM doc_files")}
        return known, changed

    known, changing_mode = db.run(_known)

    def _read(name: str) -> tuple[float, int, int, str, str | None, list[tuple[int, str, str]]] | None:
        path = os.path.join(folder, name)
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        stat = os.stat(path)
        if stat.st_size > MAX_FILE_BYTES:
            raise ValueError(f"{name}: too large to read page by page")
        data, total = (read_pdf(path, local_ocr=actual_ocr) if name.lower().endswith(".pdf")
                       else read_text(path))
        return stat.st_mtime, stat.st_size, total, ("page" if name.lower().endswith(".pdf")
                                                 else "section"), None, data

    if changing_mode:
        # TEMP tables are connection-local and are dropped automatically after
        # interruption; the live index is never emptied during extraction.
        def _prepare(conn: sqlite3.Connection) -> None:
            conn.execute("DROP TABLE IF EXISTS temp.doc_files_stage")
            conn.execute("DROP TABLE IF EXISTS temp.doc_pages_stage")
            conn.execute("CREATE TEMP TABLE doc_files_stage AS SELECT * FROM doc_files WHERE 0")
            conn.execute("CREATE TEMP TABLE doc_pages_stage AS SELECT * FROM doc_pages WHERE 0")
        db.run(_prepare)
        count = 0
        try:
            for name in sorted(wanted):
                result = _read(name)
                if result is None:
                    continue
                mtime, size, total, unit, error, data = result
                kept = [(n, label, body) for n, label, body in data if len(body) >= MIN_TEXT]
                def _stage(conn: sqlite3.Connection) -> None:
                    conn.execute("INSERT INTO doc_files_stage VALUES (?,?,?,?,?,?,?)",
                                 (name, mtime, size, total, len(kept), unit, error))
                    conn.executemany("INSERT INTO doc_pages_stage VALUES (?,?,?,?)",
                                     [(name, n, label, body) for n, label, body in kept])
                db.run(_stage)
                count += 1

            def _publish(conn: sqlite3.Connection) -> None:
                conn.execute("DELETE FROM doc_pages")
                conn.execute("DELETE FROM doc_files")
                if db.fts:
                    conn.execute("DELETE FROM doc_pages_fts")
                conn.execute("INSERT INTO doc_files SELECT * FROM doc_files_stage")
                conn.execute("INSERT INTO doc_pages SELECT * FROM doc_pages_stage")
                if db.fts:
                    conn.execute("INSERT INTO doc_pages_fts (file, page, body) "
                                 "SELECT file, page, body FROM doc_pages_stage")
                conn.execute("INSERT OR REPLACE INTO doc_index_meta (key,value) VALUES (?,?)",
                             ("local_ocr", mode))
                conn.execute("DROP TABLE doc_files_stage")
                conn.execute("DROP TABLE doc_pages_stage")
            db.run(_publish)
        except Exception as err:  # noqa: BLE001 - never destroy a prior searchable index
            _LOGGER.warning("Document OCR reindex failed; retaining the last working index: %s", err)
            return {"error": "OCR reindex could not complete; previous index was kept",
                    "read": 0, "removed": 0, "local_ocr_available": ocr_installed,
                    "local_ocr_active": actual_ocr, "reindex_completed": False}
        return {"read": count, "removed": len(set(known) - wanted),
                "local_ocr_available": ocr_installed, "local_ocr_active": actual_ocr,
                "reindex_completed": True}

    read = 0
    for name in sorted(wanted):
        try:
            result = _read(name) if name not in known else None
            if result is None and name in known:
                path = os.path.join(folder, name)
                if not os.path.islink(path) and os.path.isfile(path):
                    st = os.stat(path)
                    if known.get(name) != (st.st_mtime, st.st_size):
                        result = _read(name)
            if result is None:
                continue
            mtime, size, total, unit, error, data = result
            _store(db, name, mtime, size, total, unit, error, data)
            read += 1
        except Exception as err:  # noqa: BLE001 - preserve existing index of broken files
            _LOGGER.warning("Could not index %s; retaining its prior pages: %s", name, err)
    gone = [f for f in known if f not in wanted]
    if gone:
        db.run(lambda c: [_drop(db, c, f) for f in gone])
    # Mark initial text-only indexing after success; do not clear prior pages.
    if not actual_ocr:
        db.run(lambda c: c.execute("INSERT OR IGNORE INTO doc_index_meta (key,value) VALUES (?,?)",
                                   ("local_ocr", mode)))
    return {"read": read, "removed": len(gone), "local_ocr_available": ocr_installed,
            "local_ocr_active": actual_ocr, "reindex_completed": True}


def _drop(db: Any, conn: sqlite3.Connection, name: str) -> None:
    conn.execute("DELETE FROM doc_files WHERE file = ?", (name,))
    conn.execute("DELETE FROM doc_pages WHERE file = ?", (name,))
    if db.fts:
        conn.execute("DELETE FROM doc_pages_fts WHERE file = ?", (name,))


def _store(db: Any, name: str, mtime: float, size: int, total: int, unit: str, error: str | None,
           pages: list[tuple[int, str, str]]) -> None:
    kept = [p for p in pages if len(p[2]) >= MIN_TEXT]

    def _write(conn: sqlite3.Connection) -> None:
        _drop(db, conn, name)
        conn.execute("INSERT INTO doc_files (file, mtime, size, pages, read_pages, unit, error) VALUES (?,?,?,?,?,?,?)",
                     (name, mtime, size, total, len(kept), unit, error))
        conn.executemany("INSERT INTO doc_pages (file, page, label, body) VALUES (?,?,?,?)",
                         [(name, n, label, body) for n, label, body in kept])
        if db.fts:
            conn.executemany("INSERT INTO doc_pages_fts (file, page, body) VALUES (?,?,?)",
                             [(name, n, body) for n, _label, body in kept])

    db.run(_write)


def search(db: Any, question: str, file: str | None = None, limit: int = 3) -> list[dict[str, Any]]:
    """The pages that best match the question (BM25), optionally in one document. Runs in the executor."""
    from .db import _WORD, STOP, fts_query

    query = fts_query([question])
    if not query:
        return []

    def _find(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        where, args = ("", []) if file is None else (" AND f.file = ?", [file])
        if db.fts:
            try:
                return [dict(r) for r in conn.execute(
                    "SELECT f.file, f.page, p.label, p.body, bm25(doc_pages_fts) AS rank, d.unit FROM doc_pages_fts f "
                    "JOIN doc_pages p ON p.file = f.file AND p.page = f.page JOIN doc_files d ON d.file = f.file "
                    f"WHERE doc_pages_fts MATCH ?{where} ORDER BY rank LIMIT ?", [query, *args, limit])]
            except sqlite3.OperationalError:
                pass
        tokens = [t for t in _WORD.findall(question.casefold()) if t not in STOP]
        if not tokens:
            return []
        rows = conn.execute("SELECT p.file, p.page, p.label, p.body, d.unit FROM doc_pages p JOIN doc_files d "
                            f"ON d.file = p.file WHERE 1 = 1{where.replace('f.file', 'p.file')}", args).fetchall()
        scored = sorted(((sum(t in r["body"].casefold() for t in tokens), r) for r in rows), key=lambda x: -x[0])
        return [{**dict(r), "rank": -s} for s, r in scored[:limit] if s]

    return db.run(_find)


def status(db: Any) -> dict[str, Any]:
    """What has been read: documents, searchable pages, and which can't be read page by page."""
    def _status(conn: sqlite3.Connection) -> dict[str, Any]:
        rows = [dict(r) for r in conn.execute("SELECT file, pages, read_pages, unit, error FROM doc_files ORDER BY file")]
        mode = conn.execute("SELECT value FROM doc_index_meta WHERE key = 'local_ocr'").fetchone()
        from .local_ocr import available as ocr_available

        return {"local_ocr_available": ocr_available(),
                "local_ocr_indexed": bool(mode and mode["value"] == "1"),
                "documents": len(rows), "pages": sum(r["read_pages"] or 0 for r in rows),
                "whole_only": [os.path.splitext(r["file"])[0] for r in rows
                               if r["unit"] == "page" and not r["read_pages"]],
                "files": rows}

    return db.run(_status)


def clear(db: Any) -> None:
    def _clear(conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM doc_files")
        conn.execute("DELETE FROM doc_pages")
        if db.fts:
            conn.execute("DELETE FROM doc_pages_fts")

    db.run(_clear)


def label(hit: dict[str, Any]) -> str:
    """'page 14' or 'the section "Descaling"'."""
    if hit.get("unit") == "section":
        lab = str(hit.get("label") or hit.get("page"))
        return f'section {lab}' if lab.isdigit() else f'the section "{lab}"'
    return f"page {hit.get('label') or hit.get('page')}"
