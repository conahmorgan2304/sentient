"""OCR mode changes never destroy searchable manuals before successful reindex."""
import sqlite3
from types import SimpleNamespace

from custom_components.sentient import pages


class Database:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.fts = True
        for stmt in pages.SCHEMA:
            self.conn.execute(stmt)
        self.conn.execute(pages.FTS)
        self.conn.commit()

    def run(self, fn):
        try:
            result = fn(self.conn)
            self.conn.commit()
            return result
        except Exception:
            self.conn.rollback()
            raise


def _one_file(tmp_path, monkeypatch):
    file = tmp_path / "guide.pdf"
    file.write_bytes(b"pdf")
    monkeypatch.setattr(pages.os.path, "isfile", lambda _: True)
    return file


def test_failed_ocr_rebuild_retains_old_files_and_search_index(tmp_path, monkeypatch):
    db = Database()
    file = _one_file(tmp_path, monkeypatch)
    pages._store(db, "guide.pdf", file.stat().st_mtime, file.stat().st_size, 1, "page", None,
                 [(1, "1", "Boiler pressure instructions from old index")])
    db.run(lambda c: c.execute("INSERT INTO doc_index_meta VALUES ('local_ocr','0')"))
    monkeypatch.setattr("custom_components.sentient.local_ocr.available", lambda: True)
    monkeypatch.setattr(pages, "read_pdf", lambda *a, **kw: (_ for _ in ()).throw(ValueError("broken scan")))
    result = pages.sync(db, str(tmp_path), ["guide.pdf"], local_ocr=True)
    assert result["reindex_completed"] is False
    assert pages.search(db, "boiler pressure")[0]["body"].startswith("Boiler pressure")
    assert db.run(lambda c: c.execute("SELECT value FROM doc_index_meta").fetchone()[0]) == "0"


def test_successful_ocr_rebuild_swaps_documents_and_fts_together(tmp_path, monkeypatch):
    db = Database()
    file = _one_file(tmp_path, monkeypatch)
    pages._store(db, "guide.pdf", file.stat().st_mtime, file.stat().st_size, 1, "page", None,
                 [(1, "1", "Boiler pressure from prior index")])
    db.run(lambda c: c.execute("INSERT INTO doc_index_meta VALUES ('local_ocr','0')"))
    monkeypatch.setattr("custom_components.sentient.local_ocr.available", lambda: True)
    monkeypatch.setattr(pages, "read_pdf", lambda *a, **kw:
                        ([(1, "1", "Scanned thermostat calibration and warning text")], 1))
    result = pages.sync(db, str(tmp_path), ["guide.pdf"], local_ocr=True)
    assert result["reindex_completed"] is True
    assert pages.search(db, "thermostat calibration")[0]["body"].startswith("Scanned")
    assert pages.search(db, "boiler pressure") == []
    assert db.run(lambda c: c.execute("SELECT value FROM doc_index_meta").fetchone()[0]) == "1"


def test_ocr_reindex_publish_failure_rolls_back_prior_pages(tmp_path, monkeypatch):
    db = Database()
    file = _one_file(tmp_path, monkeypatch)
    pages._store(db, "guide.pdf", file.stat().st_mtime, file.stat().st_size, 1, "page", None,
                 [(1, "1", "Original boiler reference text")])
    db.run(lambda c: c.execute("INSERT INTO doc_index_meta VALUES ('local_ocr','0')"))
    monkeypatch.setattr("custom_components.sentient.local_ocr.available", lambda: True)
    monkeypatch.setattr(pages, "read_pdf", lambda *a, **kw:
                        ([(1, "1", "New scanned thermostat reference text")], 1))
    db.conn.execute("""CREATE TRIGGER reject_publish BEFORE DELETE ON doc_pages
                       BEGIN SELECT RAISE(ABORT, 'simulate full disk'); END""")
    db.conn.commit()
    result = pages.sync(db, str(tmp_path), ["guide.pdf"], local_ocr=True)
    assert result["reindex_completed"] is False
    assert pages.search(db, "original boiler")
    assert db.run(lambda c: c.execute("SELECT value FROM doc_index_meta").fetchone()[0]) == "0"
