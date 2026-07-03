import pytest

from pdf_translator.page_integrity import PageIntegrityError, build_page_ledger


def test_page_ledger_classifies_content_resource_and_blank_pages() -> None:
    book = {
        "pages": [
            {"page_no": 1, "has_content": True},
            {"page_no": 2, "has_content": True},
            {"page_no": 3, "has_content": False},
        ],
        "chapters": [
            {"chapter_id": "body", "source_pages": [1]},
            {
                "chapter_id": "index",
                "source_pages": [2],
                "resource_only": True,
                "preserve_original": True,
            },
        ],
    }

    ledger = build_page_ledger(book)

    assert [page["disposition"] for page in ledger["pages"]] == [
        "content",
        "resource",
        "blank",
    ]
    assert ledger["summary"]["required_coverage_ratio"] == 1.0


def test_page_ledger_rejects_missing_and_duplicate_ownership() -> None:
    missing = {
        "pages": [{"page_no": 1, "has_content": True}],
        "chapters": [],
    }
    with pytest.raises(PageIntegrityError, match="missing ownership"):
        build_page_ledger(missing)

    duplicate = {
        "pages": [{"page_no": 1, "has_content": True}],
        "chapters": [
            {"chapter_id": "a", "source_pages": [1]},
            {"chapter_id": "b", "source_pages": [1]},
        ],
    }
    with pytest.raises(PageIntegrityError, match="duplicate ownership"):
        build_page_ledger(duplicate)


def test_page_ledger_allows_explicit_skip_with_reason() -> None:
    book = {
        "pages": [
            {
                "page_no": 1,
                "has_content": True,
                "disposition": "skipped",
                "skip_reason": "publisher advertisement",
            }
        ],
        "chapters": [],
    }

    ledger = build_page_ledger(book)

    assert ledger["pages"][0] == {
        "page_no": 1,
        "disposition": "skipped",
        "chapter_id": None,
        "reason": "publisher advertisement",
    }
