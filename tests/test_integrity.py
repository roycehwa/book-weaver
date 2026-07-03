from __future__ import annotations

import pytest

from pdf_translator.integrity import (
    IntegrityGateError,
    assert_approved_export_ready,
    build_integrity_ledger,
)


def _complete_book() -> dict:
    return {
        "chapters": [
            {
                "chapter_id": "ch-001",
                "source_pages": [1],
                "resource_only": False,
                "preserve_original": False,
            }
        ],
        "pages": [{"page_no": 1, "has_content": True}],
        "assets": [{"asset_id": "asset-a", "path": "assets/a.png"}],
        "semantic_content": {
            "footnotes": [
                {
                    "footnote_id": "footnote-a",
                    "backlinks": ["fnref-a"],
                    "spans": [
                        {
                            "span_id": "prose-a",
                            "kind": "prose",
                            "source_text": "Explanation.",
                            "translated_text": "说明。",
                        },
                        {
                            "span_id": "citation-a",
                            "kind": "citation",
                            "source_text": "Book Title.",
                            "translated_text": "Book Title.",
                        },
                    ],
                }
            ],
            "ocr_quarantine": [],
            "evidence_assets": [],
        },
    }


def test_complete_ledger_reports_each_dimension() -> None:
    ledger = build_integrity_ledger(
        _complete_book(),
        epub_validation={
            "unresolved_hrefs": [],
            "missing_assets": [],
            "absolute_paths": [],
        },
        pdf_validation={"body_flow_notes": []},
        review_items=[],
    )

    assert ledger["schema"] == "integrity_ledger_v1"
    assert ledger["ready"] is True
    assert ledger["dimensions"]["pages"]["ratio"] == 1.0
    assert ledger["dimensions"]["semantic_spans"]["ratio"] == 1.0
    assert ledger["dimensions"]["assets"]["ratio"] == 1.0
    assert ledger["dimensions"]["footnote_links"]["ratio"] == 1.0


@pytest.mark.parametrize(
    "failure_key",
    [
        "missing_pages",
        "missing_translations",
        "unresolved_ocr",
        "missing_assets",
        "broken_footnote_links",
        "absolute_paths",
        "pdf_body_flow_notes",
        "unresolved_review",
    ],
)
def test_approved_export_rejects_each_integrity_failure(failure_key: str) -> None:
    ledger = build_integrity_ledger(_complete_book())
    ledger["failures"][failure_key] = ["fixture"]
    ledger["ready"] = False

    with pytest.raises(IntegrityGateError, match=failure_key):
        assert_approved_export_ready(ledger)


def test_missing_explanatory_translation_is_blocking_but_citation_is_not() -> None:
    book = _complete_book()
    book["semantic_content"]["footnotes"][0]["spans"][0]["translated_text"] = ""

    ledger = build_integrity_ledger(book)

    assert ledger["failures"]["missing_translations"] == ["prose-a"]
    assert "citation-a" not in ledger["failures"]["missing_translations"]


def test_footnote_without_backlink_is_blocking() -> None:
    book = _complete_book()
    book["semantic_content"]["footnotes"][0]["backlinks"] = []

    ledger = build_integrity_ledger(book)

    assert ledger["failures"]["broken_footnote_links"] == ["footnote-a"]
    assert ledger["dimensions"]["footnote_links"]["total"] == 1
    assert ledger["dimensions"]["footnote_links"]["ratio"] == 0.0
    assert ledger["ready"] is False


def test_standalone_table_note_does_not_require_backlink() -> None:
    book = _complete_book()
    note = book["semantic_content"]["footnotes"][0]
    note["backlinks"] = []
    note["standalone"] = True

    ledger = build_integrity_ledger(book)

    assert ledger["failures"]["broken_footnote_links"] == []
    assert ledger["dimensions"]["footnote_links"]["ratio"] == 1.0


def test_open_review_blocks_approval_but_not_technical_readiness() -> None:
    ledger = build_integrity_ledger(
        _complete_book(),
        review_items=[{"item_id": "review-a", "status": "open"}],
    )

    assert ledger["technical_ready"] is True
    assert ledger["approved_ready"] is False
    assert ledger["ready"] is False
    assert ledger["failures"]["unresolved_review"] == ["review-a"]
