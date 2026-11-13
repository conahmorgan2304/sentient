"""Opt-in offline OCR is resource-bounded, never installs a model or uses the network."""
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.sentient.local_ocr import extract_page


def _fake_page(raw=b"image", width=120, height=80):
    picture = SimpleNamespace(width=width, height=height)
    return SimpleNamespace(images=[SimpleNamespace(image=picture, data=raw)])


def test_ocr_unavailable_means_empty_without_process_call():
    with patch("custom_components.sentient.local_ocr.shutil.which", return_value=None), patch(
        "custom_components.sentient.local_ocr.subprocess.run"
    ) as run:
        assert extract_page(_fake_page()) == ""
        run.assert_not_called()


def test_opted_in_ocr_reads_embedded_image_locally_with_timeout():
    result = SimpleNamespace(returncode=0, stdout=b"Boiler pressure 1.5 bar")
    with patch("custom_components.sentient.local_ocr.shutil.which", return_value="/usr/bin/tesseract"), patch(
        "custom_components.sentient.local_ocr.subprocess.run", return_value=result
    ) as run:
        assert "Boiler pressure" in extract_page(_fake_page())
        args, kwargs = run.call_args
        assert args[0] == ["/usr/bin/tesseract", "stdin", "stdout", "--psm", "3"]
        assert kwargs["timeout"] <= 6
        assert kwargs["input"] == b"image"


def test_oversized_image_is_rejected_without_external_process():
    with patch("custom_components.sentient.local_ocr.shutil.which", return_value="/usr/bin/tesseract"), patch(
        "custom_components.sentient.local_ocr.subprocess.run"
    ) as run:
        assert extract_page(_fake_page(width=5000, height=5000)) == ""
        run.assert_not_called()


def test_pdf_pages_preserve_text_and_only_ocr_scans(monkeypatch):
    from custom_components.sentient import pages
    import sys

    page1 = SimpleNamespace(extract_text=lambda: "Text " * 25)
    page2 = SimpleNamespace(extract_text=lambda: "")
    reader = SimpleNamespace(pages=[page1, page2], is_encrypted=False, page_labels=["1", "2"])
    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=lambda _: reader))
    with patch("custom_components.sentient.local_ocr.extract_page", return_value="Pressure settings " * 10) as ocr:
        output, total = pages.read_pdf("/unused.pdf", local_ocr=True)
    assert total == 2
    assert "Text" in output[0][2] and "Pressure" in output[1][2]
    ocr.assert_called_once_with(page2)
