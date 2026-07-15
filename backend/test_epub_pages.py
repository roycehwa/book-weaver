"""Tests for EPUB page resolution: cover/roman/digit pages and labels."""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from epub_spine import EpubPage, render_epub_page_by_anchor, resolve_epub_pages  # noqa: E402


def _write_minimal_epub(
    path: Path,
    chapters: list[tuple[str, str, list[str]]],
    chapter_bodies: dict[str, str] | None = None,
) -> None:
    """Write a tiny EPUB with the given spine.

    chapters: list of (href, title, [anchor_ids])
    """
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        # META-INF/container.xml
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            '</rootfiles></container>',
        )
        # OPF
        manifest_items = "".join(
            f'<item id="c{i}" href="{href}" media-type="application/xhtml+xml"/>'
            for i, (href, _, _) in enumerate(chapters)
        )
        spine_refs = "".join(
            f'<itemref idref="c{i}"/>' for i, _ in enumerate(chapters)
        )
        zf.writestr(
            "OEBPS/content.opf",
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            f'<manifest>{manifest_items}</manifest>'
            f'<spine>{spine_refs}</spine>'
            f'</package>',
        )
        # Each chapter
        for href, title, anchor_ids in chapters:
            body = (chapter_bodies or {}).get(href)
            if body is None:
                anchors_html = "".join(f'<a id="{aid}"></a>' for aid in anchor_ids)
                body = f'<h1>{title}</h1>{anchors_html}<p>Lorem ipsum dolor sit amet.</p>'
            xhtml = (
                f'<?xml version="1.0" encoding="UTF-8"?>'
                f'<html xmlns="http://www.w3.org/1999/xhtml">'
                f'<head><title>{title}</title></head>'
                f'<body>{body}</body></html>'
            )
            zf.writestr(f"OEBPS/{href}", xhtml)


def test_resolve_epub_pages_includes_cover_roman_and_digit(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_minimal_epub(
        epub,
        [
            ("cover.xhtml", "cover", []),  # no anchors → 1 page, label=""
            ("ch01.xhtml", "ch01", ["page_i", "page_ii"]),  # roman
            ("ch02.xhtml", "ch02", ["page_1", "page_2"]),  # digit
        ],
    )
    pages = resolve_epub_pages(epub)
    assert [p.page_label for p in pages] == ["", "i", "ii", "1", "2"]
    # page_number is 0 for cover and roman, real value for digit
    assert pages[0].page_number == 0
    assert pages[1].page_number == 0
    assert pages[3].page_number == 1
    assert pages[4].page_number == 2
    # indices are 1-based, contiguous
    assert [p.index for p in pages] == [1, 2, 3, 4, 5]
    # chapter titles match
    assert pages[0].chapter_title == "cover"
    assert pages[1].chapter_title == "ch01"
    assert pages[3].chapter_title == "ch02"


def test_resolve_epub_pages_chapter_with_no_anchors_is_single_page(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_minimal_epub(
        epub,
        [
            ("plates.xhtml", "Plates", []),
            ("ch01.xhtml", "ch02", ["page_1"]),
        ],
    )
    pages = resolve_epub_pages(epub)
    assert len(pages) == 2
    assert pages[0].page_anchor == ""
    assert pages[0].page_label == ""
    assert pages[1].page_anchor == "page_1"
    assert pages[1].page_label == "1"


def test_resolve_epub_pages_accepts_span_pagebreak_anchors(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_minimal_epub(
        epub,
        [("ch01.xhtml", "ch01", [])],
        {
            "ch01.xhtml": (
                '<h1><span aria-label="page 1." epub:type="pagebreak" '
                'id="page_1" role="doc-pagebreak"/>Chapter</h1>'
                '<p>First page.</p>'
                '<p><span aria-label="page 2." epub:type="pagebreak" '
                'id="page_2" role="doc-pagebreak"/>Second page.</p>'
            )
        },
    )

    pages = resolve_epub_pages(epub)

    assert [p.page_anchor for p in pages] == ["page_1", "page_2"]
    assert [p.page_label for p in pages] == ["1", "2"]


def test_resolve_epub_pages_accepts_compact_page_ids_without_underscore(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_minimal_epub(
        epub,
        [("ch01.xhtml", "ch01", [])],
        {
            "ch01.xhtml": (
                '<p><span aria-label="3" epub:type="pagebreak" id="page3" role="doc-pagebreak"/>'
                "First page.</p>"
                '<p><span aria-label="4" epub:type="pagebreak" id="page4" role="doc-pagebreak"/>'
                "Second page.</p>"
            )
        },
    )

    pages = resolve_epub_pages(epub)

    assert [p.page_anchor for p in pages] == ["page3", "page4"]
    assert [p.page_label for p in pages] == ["3", "4"]
    assert [p.page_number for p in pages] == [3, 4]


def test_resolve_epub_pages_falls_back_to_spine_when_no_anchors_anywhere(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_minimal_epub(
        epub,
        [("a.xhtml", "A", []), ("b.xhtml", "B", [])],
    )
    pages = resolve_epub_pages(epub)

    assert len(pages) == 2
    assert [p.index for p in pages] == [1, 2]
    assert [p.chapter_title for p in pages] == ["A", "B"]
    assert [p.page_anchor for p in pages] == ["", ""]
    assert [p.page_label for p in pages] == ["", ""]


def test_render_page_converts_xhtml_page_anchor_to_inert_marker(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_minimal_epub(
        epub,
        [("chapter.xhtml", "Chapter", ["page_i", "page_ii"])],
        {
            "chapter.xhtml": '<p><a id="page_i"/>First page '
            '<a href="notes.xhtml#n1">1</a> tail '
            '<a id="page_ii"/>Second page</p>',
        },
    )

    html = render_epub_page_by_anchor(epub, "OEBPS/chapter.xhtml", "page_i", "job-1")

    assert '<span id="page_i"></span>' in html
    assert '<a id="page_i"' not in html
    assert '<a href="notes.xhtml#n1">1</a>' in html
    assert "First page" in html


def test_render_page_rewrites_svg_image_href_to_asset_endpoint(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_minimal_epub(
        epub,
        [("cover.xhtml", "Cover", [])],
        {
            "cover.xhtml": (
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'xmlns:xlink="http://www.w3.org/1999/xlink">'
                '<image xlink:href="../images/cover.jpg" width="600" height="900"/>'
                '</svg>'
            )
        },
    )
    with zipfile.ZipFile(epub, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("images/cover.jpg", b"fake")

    html = render_epub_page_by_anchor(epub, "OEBPS/cover.xhtml", "", "job-1")

    assert 'xlink:href="/api/jobs/job-1/epub/asset?path=images%2Fcover.jpg"' in html
    assert "../images/cover.jpg" not in html
