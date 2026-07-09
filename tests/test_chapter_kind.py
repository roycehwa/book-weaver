"""Unit tests for chapter and block classification."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pdf_translator.chapter_kind import (  # noqa: E402
    BLOCK_KINDS,
    CHAPTER_KINDS,
    NON_TRANSLATABLE_BLOCK_KINDS,
    NON_TRANSLATABLE_CHAPTER_KINDS,
    classify_blocks,
    classify_chapter,
    should_translate_block,
    should_translate_chapter,
)


# --- chapter classification -------------------------------------------------


def test_explicit_kind_wins():
    ch = {"title": "Anything", "kind": "toc"}
    assert classify_chapter(ch) == "toc"


def test_title_table_of_contents_classified_as_toc():
    ch = {"title": "Table of Contents"}
    assert classify_chapter(ch) == "toc"


def test_title_bibliography_classified_as_bibliography():
    ch = {"title": "Bibliography"}
    assert classify_chapter(ch) == "bibliography"


def test_title_references_classified_as_bibliography():
    ch = {"title": "References"}
    assert classify_chapter(ch) == "bibliography"


def test_title_index_classified_as_index():
    ch = {"title": "Index"}
    assert classify_chapter(ch) == "index"


def test_title_notes_on_transcription_classified_as_apparatus():
    ch = {"title": "Notes on Transcription and Dates"}
    assert classify_chapter(ch) == "apparatus"


def test_title_appendix_classified_as_appendix():
    ch = {"title": "Appendix A: Chronology"}
    assert classify_chapter(ch) == "appendix"


def test_title_preface_classified_as_front_matter():
    ch = {"title": "Preface"}
    assert classify_chapter(ch) == "front_matter"


def test_title_introduction_classified_as_front_matter():
    ch = {"title": "Introduction"}
    assert classify_chapter(ch) == "front_matter"


def test_preserved_resource_only_chapter_uses_title_kind():
    ch = {"title": "Glossary", "preserve_original": True, "resource_only": True}
    assert classify_chapter(ch) == "apparatus"


def test_preserved_resource_only_without_title_uses_front_matter():
    ch = {"title": "Some resource", "preserve_original": True, "resource_only": True}
    assert classify_chapter(ch) == "front_matter"


def test_majority_page_kind_falls_back_to_narrative():
    ch = {"title": "Chapter 1", "source_pages": [1, 2]}
    pages = [
        {"page_no": 1, "page_kind": "body"},
        {"page_no": 2, "page_kind": "body"},
    ]
    assert classify_chapter(ch, pages=pages) == "narrative"


def test_majority_page_kind_toc():
    ch = {"title": "Whatever", "source_pages": [3, 4]}
    pages = [
        {"page_no": 1, "page_kind": "body"},
        {"page_no": 3, "page_kind": "toc"},
        {"page_no": 4, "page_kind": "toc"},
    ]
    assert classify_chapter(ch, pages=pages) == "toc"


def test_majority_page_kind_references_becomes_bibliography():
    ch = {"title": "Whatever", "source_pages": [10]}
    pages = [{"page_no": 10, "page_kind": "references"}]
    assert classify_chapter(ch, pages=pages) == "bibliography"


def test_default_is_narrative():
    ch = {"title": "Chapter 2: The Economy"}
    assert classify_chapter(ch) == "narrative"


# --- block classification --------------------------------------------------


def test_classify_blocks_fills_missing_kind_as_text():
    out = classify_blocks([{"text": "hello"}, {"text": "world", "kind": "table"}])
    assert out[0]["kind"] == "text"
    assert out[1]["kind"] == "table"


def test_classify_blocks_preserves_unknown_kind_as_text():
    out = classify_blocks([{"text": "x", "kind": "mystery"}])
    assert out[0]["kind"] == "text"


def test_classify_blocks_ignores_non_dict():
    out = classify_blocks([{"text": "ok"}, "string", None])  # type: ignore[list-item]
    assert out[0]["kind"] == "text"
    assert out[1] == "string"
    assert out[2] is None


# --- translation filter ----------------------------------------------------


def test_should_translate_chapter_blocks_apparatus():
    ch = {"title": "Notes on Transcription and Dates"}
    assert should_translate_chapter(ch) is False
    assert ch["title"] in NON_TRANSLATABLE_CHAPTER_KINDS or "apparatus" in NON_TRANSLATABLE_CHAPTER_KINDS


def test_should_translate_chapter_blocks_toc():
    assert should_translate_chapter({"title": "Table of Contents"}) is False


def test_should_translate_chapter_allows_narrative():
    assert should_translate_chapter({"title": "Chapter 1: Beginnings"}) is True


def test_should_translate_chapter_explicit_translate_false():
    ch = {"title": "Chapter 1", "translate": False}
    assert should_translate_chapter(ch) is False


def test_should_translate_chapter_preserved_resource_only():
    ch = {"title": "Glossary", "preserve_original": True, "resource_only": True}
    assert should_translate_chapter(ch) is False


def test_should_translate_block_skips_table():
    assert should_translate_block({"kind": "table", "text": "x"}) is False


def test_should_translate_block_skips_figure():
    assert should_translate_block({"kind": "figure", "text": "x"}) is False


def test_should_translate_block_allows_text():
    assert should_translate_block({"kind": "text", "text": "x"}) is True


def test_should_translate_block_allows_caption():
    assert should_translate_block({"kind": "caption", "text": "Figure 1"}) is True


def test_should_translate_block_defaults_to_text():
    assert should_translate_block({"text": "x"}) is True


# --- constants sanity -------------------------------------------------------


def test_kinds_have_no_unknown_values():
    for k in CHAPTER_KINDS:
        assert isinstance(k, str) and k.strip() == k
    for k in BLOCK_KINDS:
        assert isinstance(k, str) and k.strip() == k
    # the skip sets are subsets
    assert NON_TRANSLATABLE_CHAPTER_KINDS.issubset(CHAPTER_KINDS)
    assert NON_TRANSLATABLE_BLOCK_KINDS.issubset(BLOCK_KINDS)
