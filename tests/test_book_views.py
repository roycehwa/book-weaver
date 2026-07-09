"""Verify that ``render_translation_input_markdown`` honours chapter and
block kinds, so the translator no longer sees apparatus / TOC / index
chapters or in-chapter tables / figures."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pdf_translator.book_views import render_translation_input_markdown  # noqa: E402
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
