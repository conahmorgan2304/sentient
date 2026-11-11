"""Local document lookup is bounded and never follows symlinks."""
from custom_components.sentient.document_retrieval import search_local_text


def test_plain_text_can_be_found_by_contents(tmp_path):
    (tmp_path / "kettle.txt").write_text("Replacement filter model KF42", encoding="utf-8")
    (tmp_path / "dryer.md").write_text("Clean the lint trap regularly", encoding="utf-8")
    assert search_local_text(str(tmp_path), ["kettle.txt", "dryer.md"], "Which one uses KF42 filter?") == "kettle.txt"


def test_ambiguous_document_query_does_not_guess(tmp_path):
    (tmp_path / "one.txt").write_text("Warranty covers motor", encoding="utf-8")
    (tmp_path / "two.txt").write_text("Warranty covers motor", encoding="utf-8")
    assert search_local_text(str(tmp_path), ["one.txt", "two.txt"], "motor warranty") is None


def test_symlinks_and_parent_paths_are_not_read(tmp_path):
    outside = tmp_path.parent / "sensitive_test_document.txt"
    outside.write_text("secret keyword", encoding="utf-8")
    try:
        (tmp_path / "link.txt").symlink_to(outside)
        assert search_local_text(str(tmp_path), ["../sensitive_test_document.txt", "link.txt"], "secret keyword") is None
    finally:
        outside.unlink()


def test_unrelated_document_not_selected(tmp_path):
    (tmp_path / "garden.txt").write_text("The rabbit enclosure", encoding="utf-8")
    assert search_local_text(str(tmp_path), ["garden.txt"], "dishwasher serial number") is None
