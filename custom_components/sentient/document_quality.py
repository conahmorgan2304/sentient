"""Measure document page extractability without installing OCR or cloud services."""
from __future__ import annotations
from typing import Any

def assess(pages: list[str | None], *, min_chars: int = 40) -> dict[str, Any]:
    total = len(pages)
    if not total:
        return {"pages": 0, "text_pages": 0, "likely_scanned_pages": 0,
                "text_coverage": None, "ocr_recommended": False}
    readable = sum(isinstance(page, str) and len(page.strip()) >= min_chars for page in pages)
    return {"pages": total, "text_pages": readable,
            "likely_scanned_pages": total - readable,
            "text_coverage": round(readable / total, 3),
            "ocr_recommended": readable < total,
            "note": "Short pages may be intentionally blank; OCR has not run."}

def citation_confidence(question: str, page_text: str) -> dict[str, Any]:
    from .retrieval_quality import tokens
    query = tokens(question)
    overlap = len(query & tokens(page_text))
    return {"lexical_support": round(overlap / len(query), 3) if query else None,
            "verified_answer": False, "page_is_evidence_only": True}
