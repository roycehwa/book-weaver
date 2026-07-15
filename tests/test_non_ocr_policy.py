from __future__ import annotations

import pytest

from pdf_translator.guardrails import InputGateError, collect_non_ocr_policy_violations, enforce_non_ocr_translatable_policy


def test_enforce_non_ocr_policy_rejects_translatable_page_fallback() -> None:
    book = {
        "chapters": [
            {
                "title": "Body",
                "translate": True,
                "preserve_original": False,
                "markdown": "![Original page 31](original-page-p0031.png)\n\nText.",
            }
        ],
        "semantic_content": {"ocr_quarantine": []},
    }

    violations = collect_non_ocr_policy_violations(book)
    assert any("page-render fallback" in item for item in violations)

    with pytest.raises(InputGateError, match="Non-OCR input policy"):
        enforce_non_ocr_translatable_policy(book)


def test_enforce_non_ocr_policy_allows_preserve_original_page_images() -> None:
    book = {
        "chapters": [
            {
                "title": "Notes",
                "translate": False,
                "preserve_original": True,
                "markdown": "![Original page 3](original-page-p0003.png)",
            }
        ],
        "semantic_content": {"ocr_quarantine": []},
    }

    enforce_non_ocr_translatable_policy(book)


def test_enforce_non_ocr_policy_rejects_unresolved_ocr_quarantine() -> None:
    book = {
        "chapters": [],
        "semantic_content": {
            "ocr_quarantine": [
                {
                    "quarantine_id": "q-1",
                    "source_page": 12,
                    "disposition": "suspect_ocr",
                }
            ]
        },
    }

    with pytest.raises(InputGateError, match="suspect OCR"):
        enforce_non_ocr_translatable_policy(book)


def test_assert_translatable_pages_reject_empty_embedded_text() -> None:
    from pdf_translator.guardrails import assert_translatable_pages_have_extractable_text

    with pytest.raises(InputGateError, match="no extractable embedded text"):
        assert_translatable_pages_have_extractable_text(
            chapter_title="Body",
            pages=[4],
            page_markdown={4: "![Figure 1](figure.png)"},
        )
