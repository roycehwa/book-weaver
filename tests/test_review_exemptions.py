"""Verify the review exemption rules reduce false-positive mixed_english flags."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pdf_translator.review_exemptions import (  # noqa: E402
    apply_review_exemptions,
    summarise_exemptions,
)


# --- individual rule coverage ---------------------------------------------


def _seg(**kw) -> dict:
    base = {
        "segment_id": "ch-001:s001",
        "chapter_id": "ch-001",
        "chapter_title": "Chapter 1",
        "chapter_kind": "narrative",
        "source_text": "Some text.",
        "translated_text": "Some text.",
    }
    base.update(kw)
    return base


def test_apparatus_chapter_exempt():
    seg = _seg(chapter_kind="apparatus", chapter_title="Notes on Transcription")
    exempt, reason = apply_review_exemptions(seg)
    assert exempt is True
    assert reason == "_is_apparatus_segment"


def test_bibliography_chapter_exempt():
    seg = _seg(chapter_kind="bibliography", chapter_title="Bibliography")
    exempt, reason = apply_review_exemptions(seg)
    assert exempt is True


def test_index_chapter_exempt():
    seg = _seg(chapter_kind="index", chapter_title="Index")
    exempt, reason = apply_review_exemptions(seg)
    assert exempt is True


def test_apparatus_title_hint_exempt_even_without_kind():
    seg = _seg(chapter_kind="", chapter_title="Notes on Transcription and Dates")
    exempt, reason = apply_review_exemptions(seg)
    assert exempt is True
    assert reason == "_is_apparatus_segment"


def test_quote_segment_exempt():
    seg = _seg(
        source_text="> This is a long quotation that should be left in the original.\n> Continued.",
        translated_text="> This is a long quotation that should be left in the original.\n> Continued.",
    )
    exempt, reason = apply_review_exemptions(seg)
    assert exempt is True
    assert reason == "_is_quote_segment"


def test_inline_code_exempt():
    seg = _seg(translated_text="Use the `process_data` helper to clean up.")
    exempt, reason = apply_review_exemptions(seg)
    assert exempt is True
    assert reason == "_is_quote_segment"


def test_arabic_script_exempt():
    seg = _seg(translated_text="Some ʿarabic text like ʿAbd al-Raḥmān remains.")
    exempt, reason = apply_review_exemptions(seg)
    assert exempt is True


def test_glossary_term_exempt_when_matches():
    seg = _seg(
        translated_text="Kennedy and Bessard edited the volume.",
        glossary_active=[
            {"source": "Kennedy"},
            {"source": "Bessard"},
        ],
    )
    exempt, reason = apply_review_exemptions(seg)
    assert exempt is True


def test_glossary_term_not_exempt_when_no_match():
    seg = _seg(
        translated_text="A complete and unannotated paragraph.",
        glossary_active=[{"source": "Kennedy"}],
    )
    exempt, reason = apply_review_exemptions(seg)
    assert exempt is False


def test_year_only_segment_exempt():
    seg = _seg(translated_text="2024", source_text="2024")
    exempt, reason = apply_review_exemptions(seg)
    assert exempt is True


def test_normal_narrative_not_exempt():
    seg = _seg(
        chapter_kind="narrative",
        translated_text="这是一段普通的中文翻译。",
        source_text="This is normal English prose.",
        glossary_active=[],
    )
    exempt, reason = apply_review_exemptions(seg)
    assert exempt is False
    assert reason is None


# --- summarise_exemptions -------------------------------------------------


def test_summarise_counts_per_rule():
    segments = [
        _seg(chapter_kind="apparatus", chapter_title="Notes"),
        _seg(chapter_kind="bibliography"),
        _seg(chapter_kind="narrative", translated_text="普普通通"),
        _seg(translated_text="Use `code` here"),
    ]
    counts = summarise_exemptions(segments)
    assert counts.get("_is_apparatus_segment") == 2
    assert counts.get("_is_quote_segment") == 1
    assert sum(counts.values()) == 3


def test_summarise_handles_empty_list():
    assert summarise_exemptions([]) == {}


# --- integration with detect_review_items --------------------------------


def test_detect_review_items_skips_exempt_segments():
    """End-to-end: a mixed_english flag in an apparatus chapter must
    not appear in the review items list."""
    from pdf_translator.review import detect_review_items

    # A Chinese translated segment with English proper nouns — without
    # exemption this would normally raise mixed_english.
    source_segments = [
        {
            "segment_id": "ch-001:s001",
            "chapter_id": "ch-001",
            "chapter_index": 1,
            "chapter_title": "Notes on Transcription and Dates",
            "chapter_kind": "apparatus",
            "block_index": 1,
            "source_text": "Kennedy (2004) discusses the use of dirhams in early Islamic trade.",
            "translate": True,
            "source_location": {
                "chapter_index": 1,
                "chapter_id": "ch-001",
                "chapter_title": "Notes on Transcription and Dates",
                "page_start": 24,
                "page_end": 24,
                "source_pages": [24],
                "source_internal_path": None,
            },
        }
    ]
    translated_segments = [
        {
            "segment_id": "ch-001:s001",
            "translated_text": (
                "Kennedy (2004) 讨论了 dirhams 在早期伊斯兰贸易中的使用。"
                "There are also references to al-Rashid and other figures in the text."
            ),
        }
    ]
    items = detect_review_items(
        source_segments,
        translated_segments,
        target_language="zh-CN",
        text_operation="translate",
    )
    # Exempted: no items should fire for this segment.
    assert items == []


def test_detect_review_items_still_flags_real_mixed_english():
    """A genuinely untranslated paragraph in a narrative chapter must
    still surface a mixed_english issue."""
    from pdf_translator.review import detect_review_items

    source_segments = [
        {
            "segment_id": "ch-002:s001",
            "chapter_id": "ch-002",
            "chapter_index": 2,
            "chapter_title": "Chapter 2",
            "chapter_kind": "narrative",
            "block_index": 1,
            "source_text": "Discusses economy and trade.",
            "translate": True,
            "source_location": {
                "chapter_index": 2,
                "chapter_id": "ch-002",
                "chapter_title": "Chapter 2",
                "page_start": 30,
                "page_end": 30,
                "source_pages": [30],
                "source_internal_path": None,
            },
        }
    ]
    # 4+ mixed-english words in a long Chinese segment with no CJK
    translated_segments = [
        {
            "segment_id": "ch-002:s001",
            "translated_text": (
                "翻译没做对，保留了 untranslated english words like "
                "caliphate trade economy Baghdad in the text without "
                "translating them properly. 这就是真正的 mixed english 问题。"
            ),
        }
    ]
    items = detect_review_items(
        source_segments,
        translated_segments,
        target_language="zh-CN",
        text_operation="translate",
    )
    assert any(it.get("issue_type") == "mixed_english" for it in items)
