from __future__ import annotations

from pdf_translator.ocr_quality import assess_ocr_block


def test_symbol_fragment_run_is_quarantined_with_evidence() -> None:
    result = assess_ocr_block(
        "1:79. 2- 80 - - 3291/. 32 / 82.0/ 2 /892: 8/99",
        page_no=6,
        bbox=[0, 0, 595, 90],
        overlaps={"footer", "scan_artifact"},
    )

    assert result.disposition == "suspect_ocr"
    assert {
        "symbol_density",
        "fragmented_tokens",
        "footer_overlap",
    } <= set(result.reason_codes)
    assert result.raw_text.startswith("1:79")
    assert result.page_no == 6
    assert result.bbox == (0.0, 0.0, 595.0, 90.0)


def test_clean_prose_stays_in_reading_content() -> None:
    result = assess_ocr_block(
        "Industrial planning shaped the institutional development of the region.",
        page_no=7,
        bbox=[72, 120, 520, 180],
    )

    assert result.disposition == "reading"
    assert result.score < 0.55


def test_ambiguous_join_pattern_is_reviewable_not_deleted() -> None:
    source = "Origins ofChinese Socialism andthe industrial region"
    result = assess_ocr_block(source, page_no=8)

    assert result.disposition == "review"
    assert "impossible_word_join" in result.reason_codes
    assert result.raw_text == source


def test_out_of_page_bbox_is_recorded_as_evidence() -> None:
    result = assess_ocr_block(
        "Possibly meaningful text",
        page_no=9,
        bbox=[-20, 50, 700, 90],
        page_size=(595, 842),
    )

    assert "out_of_page_bbox" in result.reason_codes
    assert result.disposition == "review"
