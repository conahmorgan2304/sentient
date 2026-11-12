"""Manuals read page by page: the AI gets only the pages that matter, and the answer says which page."""
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant

from custom_components.sentient import pages
from custom_components.sentient.ai import AIUnavailable
from custom_components.sentient.db import Database
from custom_components.sentient.manuals import async_answer

PAGES = [
    "Air purifier user guide. Safety instructions. Keep away from water and children.",
    "Contents. Getting started, cleaning, the filter, troubleshooting and the warranty.",
    "Replacing the filter. Use the HEPA filter model AP-200. Twist the base anticlockwise and lift the filter out.",
    "Warranty. The purifier is guaranteed for two years from the date of purchase. Keep your receipt.",
]


def make_pdf(path, texts: list[str]) -> None:
    """A small real PDF with one line of text per page (no PDF writer needed)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in texts:
        stream = f"BT /F1 10 Tf 40 700 Td ({text}) Tj ET".encode()
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {len(objs)} 0 R >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)


def _rt(hass, tmp_path, reply="It's the AP-200 filter, on page 3.", **options):
    db = Database(hass, "test", str(tmp_path / "brain.db"))
    ai = SimpleNamespace(spend=lambda *a, **k: None, async_call=AsyncMock(return_value={"data": reply}))
    return SimpleNamespace(options={"manuals": True, "manuals_folder": "manuals", **options}, ai=ai,
                           brain=SimpleNamespace(db=db, started=True))


def _folder(hass, tmp_path):
    folder = tmp_path / "manuals"
    folder.mkdir()
    make_pdf(folder / "Air purifier.pdf", PAGES)
    hass.config.media_dirs = {"local": str(tmp_path)}
    return folder


async def test_only_the_matching_pages_go_to_the_ai_and_the_page_is_named(hass: HomeAssistant, tmp_path) -> None:
    _folder(hass, tmp_path)
    rt = _rt(hass, tmp_path)
    result = await async_answer(hass, rt, "which filter does the air purifier need?")
    assert result["document"] == "Air purifier" and result["pages"][0] == "page 3"
    call = rt.ai.async_call.call_args.args[0]
    assert "attachments" not in call  # the pages, not the whole PDF
    assert "AP-200" in call["instructions"] and "Page 3" in call["instructions"]
    assert "Keep away from water" not in call["instructions"]


async def test_a_document_is_found_by_what_it_says_when_its_name_doesnt_match(hass: HomeAssistant, tmp_path) -> None:
    _folder(hass, tmp_path)
    rt = _rt(hass, tmp_path, reply="Two years from purchase, on page 4.")
    result = await async_answer(hass, rt, "how long is the guarantee?")
    assert result["document"] == "Air purifier" and result["pages"] == ["page 4"]


async def test_pages_that_dont_answer_fall_back_to_the_whole_document(hass: HomeAssistant, tmp_path) -> None:
    _folder(hass, tmp_path)
    rt = _rt(hass, tmp_path)
    rt.ai.async_call.side_effect = [{"data": "NOT_HERE"}, {"data": "Page 2 says so."}]
    result = await async_answer(hass, rt, "air purifier filter colour?")
    assert result["answer"] == "Page 2 says so."
    second = rt.ai.async_call.call_args_list[1].args[0]
    assert second["attachments"][0]["media_content_type"] == "application/pdf"
    assert "page 3" in second["instructions"]  # the search's best guess is passed on


async def test_with_the_ai_down_the_closest_passage_is_quoted_with_its_page(hass: HomeAssistant, tmp_path) -> None:
    _folder(hass, tmp_path)
    rt = _rt(hass, tmp_path)
    rt.ai.async_call.side_effect = AIUnavailable("down")
    result = await async_answer(hass, rt, "air purifier filter model")
    assert result["where"] == "page 3" and "AP-200" in result["passage"]


async def test_a_changed_file_is_read_again_and_a_removed_one_dropped(hass: HomeAssistant, tmp_path) -> None:
    folder = _folder(hass, tmp_path)
    rt = _rt(hass, tmp_path)
    db = rt.brain.db
    assert (await hass.async_add_executor_job(pages.sync, db, str(folder), ["Air purifier.pdf"]))["read"] == 1
    assert (await hass.async_add_executor_job(pages.sync, db, str(folder), ["Air purifier.pdf"]))["read"] == 0
    make_pdf(folder / "Air purifier.pdf", PAGES + ["Troubleshooting. A red light means the filter is blocked."])
    os.utime(folder / "Air purifier.pdf", (1, 1))
    assert (await hass.async_add_executor_job(pages.sync, db, str(folder), ["Air purifier.pdf"]))["read"] == 1
    assert (await hass.async_add_executor_job(pages.search, db, "red light"))[0]["page"] == 5
    assert (await hass.async_add_executor_job(pages.sync, db, str(folder), []))["removed"] == 1
    assert (await hass.async_add_executor_job(pages.status, db))["documents"] == 0


async def test_a_scanned_pdf_is_read_whole_and_shown_in_the_doctor_list(hass: HomeAssistant, tmp_path) -> None:
    folder = _folder(hass, tmp_path)
    make_pdf(folder / "Boiler.pdf", ["", ""])  # pictures of pages: no text
    rt = _rt(hass, tmp_path, reply="It's on page 2.")
    result = await async_answer(hass, rt, "boiler pressure?", "Boiler")
    assert "attachments" in rt.ai.async_call.call_args.args[0]
    assert result["answer"] == "It's on page 2."
    assert (await hass.async_add_executor_job(pages.status, rt.brain.db))["whole_only"] == ["Boiler"]


async def test_switched_off_reads_the_whole_document_as_before(hass: HomeAssistant, tmp_path) -> None:
    _folder(hass, tmp_path)
    rt = _rt(hass, tmp_path, manuals_pages=False)
    await async_answer(hass, rt, "which filter does the air purifier need?")
    assert "attachments" in rt.ai.async_call.call_args.args[0]
    assert (await hass.async_add_executor_job(pages.status, rt.brain.db))["documents"] == 0


async def test_text_documents_are_split_into_sections_named_by_their_headings(tmp_path) -> None:
    (tmp_path / "Kettle.md").write_text("# Cleaning\n\nWipe it.\n\n# Descaling\n\nUse citric acid once a month and "
                                        "rinse twice before boiling again.", encoding="utf-8")
    sections, total = pages.read_text(str(tmp_path / "Kettle.md"))
    assert total == 2 and sections[1][1] == "Descaling"
    assert pages.label({"unit": "section", "label": "Descaling"}) == 'the section "Descaling"'


async def test_switching_it_off_clears_what_was_read(hass: HomeAssistant, tmp_path) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.sentient.const import DOMAIN

    folder = tmp_path / "media" / "manuals"
    folder.mkdir(parents=True)
    make_pdf(folder / "Air purifier.pdf", PAGES)
    hass.config.media_dirs = {"local": str(tmp_path / "media")}
    entry = MockConfigEntry(domain=DOMAIN, title="Nova", unique_id=DOMAIN, data={"persona": "Nova"},
                            options={"manuals": True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    db = entry.runtime_data.brain.db
    assert (await hass.async_add_executor_job(pages.status, db))["pages"] == 4  # read in the background at start
    hass.config_entries.async_update_entry(entry, options={"manuals": True, "manuals_pages": False})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert (await hass.async_add_executor_job(pages.status, db))["documents"] == 0
