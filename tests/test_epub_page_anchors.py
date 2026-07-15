"""Tests for shared EPUB page anchor parsing."""

from __future__ import annotations

from pdf_translator.epub_page_anchors import (
    EPUB_PAGE_ANCHOR_RE,
    page_label_from_anchor,
)


def test_page_label_from_anchor_supports_underscore_and_compact_ids() -> None:
    assert page_label_from_anchor("page_1") == "1"
    assert page_label_from_anchor("page_i") == "i"
    assert page_label_from_anchor("page3") == "3"
    assert page_label_from_anchor("page49") == "49"


def test_page_anchor_regex_matches_span_pagebreak_without_underscore() -> None:
    xhtml = (
        b'<span aria-label="3" epub:type="pagebreak" id="page3" role="doc-pagebreak"/>'
        b'<span aria-label="4" epub:type="pagebreak" id="page4" role="doc-pagebreak"/>'
    )
    anchors = [m.group("anchor").decode() for m in EPUB_PAGE_ANCHOR_RE.finditer(xhtml)]
    assert anchors == ["page3", "page4"]
