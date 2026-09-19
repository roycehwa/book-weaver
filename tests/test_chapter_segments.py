from pathlib import Path

import pytest

from pdf_translator.chapter_segments import (
    build_chapter_segments,
    build_chapter_segments_from_reading_units,
)
from pdf_translator.reading_units import build_reading_units
from pdf_translator.review import build_aligned_review_segments, detect_review_items


def test_media_stays_between_prose_and_outside_model_input():
    book = {"chapters": [{"id": "body", "markdown": "Opening prose.\n\n![Map](map.png)\n\nClosing prose.", "source_internal_path": "OPS/body.xhtml"}]}
    segments = build_chapter_segments(book, max_chars=1000)["segments"]
    assert [s["markdown"] for s in segments] == ["Opening prose.", "![Map](map.png)", "Closing prose."]
    assert [s["translate"] for s in segments] == [True, False, True]
    assert all(s["chapter_id"] == "body" for s in segments)
    assert all(s["source_internal_path"] == "OPS/body.xhtml" for s in segments)


def test_preserved_image_cannot_hide_missing_prose_translation(tmp_path: Path):
    book = {"chapters": [{"chapter_id": "body", "markdown": "![Map](map.png)\n\nThis paragraph has no translated cache entry."}]}
    source, translated = build_aligned_review_segments(book, source_path=Path("book.epub"), target_language="zh-CN", cache_dir=tmp_path, max_chunk_chars=1000)
    assert source[0]["translate"] is True
    issues = detect_review_items(source, translated, target_language="zh-CN")
    assert any(item["issue_type"] == "missing_translation" for item in issues)


def test_confirmed_reading_units_drive_transport_without_changing_paragraphs() -> None:
    book = {"chapters": [{
        "chapter_id": "body",
        "index": 1,
        "title": "Body",
        "markdown": "Alpha paragraph.\n\nBeta paragraph.\n\n![Map](map.png)",
        "translate": True,
        "translation_policy_confirmed": True,
    }]}
    units = build_reading_units(
        book,
        source_path=Path("book.epub"),
        translation_authority=True,
    )

    plan = build_chapter_segments_from_reading_units(units, max_chars=48)

    assert plan["source"] == "confirmed_reading_units"
    assert plan["reading_units_fingerprint"] == units["document_fingerprint"]
    assert "\n\n".join(segment["markdown"] for segment in plan["segments"]) == (
        "# Body\n\nAlpha paragraph.\n\nBeta paragraph.\n\n![Map](map.png)"
    )
    assert plan["segments"][-1]["translate"] is False
    assert plan["segments"][-1]["role"] == "figure"
    assert all(segment["unit_ids"] for segment in plan["segments"])


def test_unconfirmed_reading_units_cannot_drive_translation_transport() -> None:
    units = build_reading_units(
        {"chapters": [{"chapter_id": "body", "title": "Body", "markdown": "Text."}]},
        source_path=Path("book.pdf"),
    )

    with pytest.raises(ValueError, match="user-confirmed"):
        build_chapter_segments_from_reading_units(units, max_chars=100)


def test_oversized_reading_unit_keeps_one_unit_id_across_transport_parts() -> None:
    units = build_reading_units(
        {"chapters": [{
            "chapter_id": "body",
            "title": "Body",
            "markdown": "One complete sentence. Two complete sentences. Three complete sentences.",
        }]},
        source_path=Path("book.pdf"),
        translation_authority=True,
    )

    plan = build_chapter_segments_from_reading_units(units, max_chars=28)
    paragraph_parts = [segment for segment in plan["segments"] if segment.get("transport_part_of")]

    assert len(paragraph_parts) >= 2
    assert len({segment["transport_part_of"] for segment in paragraph_parts}) == 1
    assert all(segment["unit_ids"] == [segment["transport_part_of"]] for segment in paragraph_parts)
