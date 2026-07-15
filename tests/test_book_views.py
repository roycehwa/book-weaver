"""Verify that ``render_translation_input_markdown`` honours chapter and
block kinds, so the translator no longer sees apparatus / TOC / index
chapters or in-chapter tables / figures."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pdf_translator.book_views import ensure_chapter_top_heading, join_chapter_delivery_markdown, render_translation_input_markdown  # noqa: E402
from pdf_translator.chapter_kind import (  # noqa: E402
    classify_chapter,
    classify_blocks,
    should_translate_chapter,
)


def _chapter(title, markdown, **extra):
    ch = {"title": title, "markdown": markdown, "index": 1}
    ch.update(extra)
    return ch


def test_skips_apparatus_chapter_by_title():
    book = {
        "chapters": [
            _chapter("Notes on Transcription and Dates", "Some intro text."),
            _chapter("Chapter 1: Beginnings", "Real prose."),
        ],
    }
    out = render_translation_input_markdown(book)
    assert "Some intro text." not in out
    assert "Real prose." in out
    assert "chapter skipped" in out.lower()


def test_skips_toc_chapter():
    book = {
        "chapters": [
            _chapter("Table of Contents", "1. Chapter 1\n2. Chapter 2"),
            _chapter("Chapter 1", "Body text."),
        ],
    }
    out = render_translation_input_markdown(book)
    assert "1. Chapter 1" not in out
    assert "Body text." in out


def test_skips_bibliography():
    book = {
        "chapters": [
            _chapter("Chapter 1", "Body."),
            _chapter("Bibliography", "Lots of refs."),
        ],
    }
    out = render_translation_input_markdown(book)
    assert "Lots of refs." not in out
    assert "Body." in out


def test_skips_index():
    book = {
        "chapters": [_chapter("Index", "A, B, C")],
    }
    out = render_translation_input_markdown(book)
    assert "A, B, C" not in out


def test_skips_appendix():
    book = {
        "chapters": [
            _chapter("Chapter 1", "Body."),
            _chapter("Appendix A: Chronology", "Date1\nDate2"),
        ],
    }
    out = render_translation_input_markdown(book)
    assert "Date1" not in out
    assert "Body." in out


def test_explicit_translate_false_overrides_kind():
    book = {
        "chapters": [_chapter("Chapter 1", "Body.", translate=False)],
    }
    out = render_translation_input_markdown(book)
    assert "Body." not in out


def test_drops_table_block_inside_narrative_chapter():
    book = {
        "chapters": [
            _chapter("Chapter 1", "Body text.", blocks=[
                {"kind": "text", "markdown": "Prose."},
                {"kind": "table", "markdown": "| a | b |\n| - | - |"},
            ]),
        ],
    }
    out = render_translation_input_markdown(book)
    assert "Prose." in out
    assert "| a | b |" not in out


def test_drops_figure_block_inside_narrative_chapter():
    book = {
        "chapters": [
            _chapter("Chapter 1", "Body.", blocks=[
                {"kind": "text", "markdown": "Prose."},
                {"kind": "figure", "markdown": "![Figure 1](fig1.png)"},
            ]),
        ],
    }
    out = render_translation_input_markdown(book)
    assert "Prose." in out
    assert "fig1.png" not in out


def test_keeps_caption_block():
    book = {
        "chapters": [
            _chapter("Chapter 1", "Body.", blocks=[
                {"kind": "text", "markdown": "Prose."},
                {"kind": "caption", "markdown": "Figure 1: Trade routes"},
            ]),
        ],
    }
    out = render_translation_input_markdown(book)
    assert "Trade routes" in out


def test_falls_back_to_full_markdown_when_no_chapters():
    book = {"full_markdown": "Free-form content."}
    out = render_translation_input_markdown(book)
    assert "Free-form content." in out


def test_legacy_markdown_with_image_lines_filtered():
    book = {
        "chapters": [
            _chapter("Chapter 1", "Prose.\n\n![Table 1](t1.png)\n\nMore prose."),
        ],
    }
    out = render_translation_input_markdown(book)
    assert "Prose." in out
    assert "More prose." in out
    assert "t1.png" not in out


def test_land_and_trade_classification_matches_expectation():
    """Replays the kind classification on the Land and trade chapters."""
    chapter_titles = [
        "Cover",
        "Half Title",
        "Title Page",
        "Copyright",
        "Contents",
        "List of Figures",
        "Notes on Transcription and Dates",
        "Part I: Economic Exceptionalism in the Early Islamic Middle East",
        "1 The Late Antique and Byzantine Context to Early Islam",
    ]
    book = {"chapters": [{"title": t, "markdown": ""} for t in chapter_titles]}
    for ch in book["chapters"]:
        ch["kind"] = classify_chapter(ch)
    kinds = {ch["title"]: ch["kind"] for ch in book["chapters"]}
    assert kinds["Cover"] == "cover"
    assert kinds["Title Page"] in {"cover", "front_matter"}
    assert kinds["Contents"] == "toc"
    assert kinds["List of Figures"] == "toc"
    assert kinds["Notes on Transcription and Dates"] == "apparatus"
    # narrative chapters stay narrative
    assert kinds["1 The Late Antique and Byzantine Context to Early Islam"] == "narrative"


def test_classify_blocks_returns_normalized():
    out = classify_blocks([{"text": "a"}, {"text": "b", "kind": "table"}])
    assert out[0]["kind"] == "text"
    assert out[1]["kind"] == "table"


def test_should_translate_chapter_decision_for_land_and_trade():
    assert should_translate_chapter({"title": "Notes on Transcription and Dates"}) is False
    assert should_translate_chapter({"title": "1 The Late Antique and Byzantine Context to Early Islam"}) is True


def test_ensure_chapter_top_heading_prepends_h1_when_body_starts_with_h2() -> None:
    result = ensure_chapter_top_heading("## 第一章\n\n正文。", "Chapter 1: Intro")
    assert result.startswith("# Chapter 1: Intro\n\n## 第一章")


def test_ensure_chapter_top_heading_normalizes_duplicate_h1() -> None:
    result = ensure_chapter_top_heading("# 引言\n\n正文。", "Introduction")
    assert result.startswith("# Introduction\n\n正文。")


def test_normalize_chapter_headings_demotes_body_h1() -> None:
    from pdf_translator.book_views import normalize_chapter_headings

    result = normalize_chapter_headings("# Old Title\n\n## Section\n\nBody.", "Chapter 1")
    assert result.startswith("# Chapter 1\n\n## Section")
    assert "# Old Title" not in result


def test_join_chapter_delivery_markdown_adds_chapter_headings() -> None:
    joined = join_chapter_delivery_markdown(
        [
            {"title": "Chapter 1", "markdown": "## 第一章\n\n正文。", "toc": True},
            {"title": "Chapter 2", "markdown": "# 第二章\n\n更多。", "toc": True},
        ]
    )
    assert joined.startswith("# Chapter 1\n\n## 第一章")
    assert "# Chapter 2\n\n更多。" in joined


def test_sanitize_cover_chapter_markdown_keeps_single_cover_image() -> None:
    from pdf_translator.book_views import sanitize_cover_chapter_markdown

    markdown = (
        "# Cover\n\n"
        "![Figure 1.1](book-images/figure-p0001-01.png)\n\n"
        "![Cover](book-images/cover-p0001.png)\n\n"
        "![Figure 1.1 duplicate](book-images/figure-p0001-01.png)"
    )

    cleaned = sanitize_cover_chapter_markdown(markdown)

    assert cleaned.count("![") == 1
    assert "cover-p0001.png" in cleaned


def test_sanitize_apparatus_chapter_markdown_drops_duplicate_crops() -> None:
    from pdf_translator.book_views import sanitize_apparatus_chapter_markdown

    markdown = (
        "![Original page 8](original-page-p0008.png)\n\n"
        "![Table 8.1](book-images/table-p0008-01.png)\n\n"
        "## Figures\n\n"
        "1.1 Example figure page 14"
    )

    cleaned = sanitize_apparatus_chapter_markdown(markdown)

    assert "original-page-p0008.png" in cleaned
    assert "table-p0008-01.png" not in cleaned
    assert "1.1 Example figure" not in cleaned


def test_strip_scrape_watermarks_removes_oceanpdf_and_stray_rules() -> None:
    from pdf_translator.book_views import strip_scrape_watermarks

    markdown = (
        "Body paragraph.\n\n"
        "## —\n\n"
        "## OceanofPDF.com\n\n"
        "OceanofPDF.com CHAPTER 7\n\n"
        "Real heading\n\n"
        "OceanofPDF. F. com"
    )
    cleaned = strip_scrape_watermarks(markdown)

    assert "OceanofPDF" not in cleaned
    assert "CHAPTER 7" in cleaned
    assert cleaned.startswith("Body paragraph.")


def test_split_chapter_sections_merges_orphan_concluding_heading() -> None:
    from pdf_translator.chapter_segments import _split_chapter_sections

    markdown = (
        "## Care versus Passion\n\n"
        "All in all, we should apply CT when we care.\n\n"
        "## Concluding Thoughts"
    )

    sections = _split_chapter_sections(markdown)

    assert len(sections) == 1
    assert sections[0][0] == "Care versus Passion"
    assert "All in all" in sections[0][1]
    assert "Concluding Thoughts" in sections[0][1]
