"""Cross-chapter EPUB ingest links and output EPUB href rewriting."""

from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from pdf_translator.book_rebuild import build_book_reconstruction
from pdf_translator.epub import render_epub_from_book, validate_epub_internal_hrefs
from pdf_translator.ingest import ingest_epub


def _write_two_spine_epub(path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />
  </rootfiles>
</container>
""",
        )
        z.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:test</dc:identifier>
    <dc:title>TwoChap</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml" />
    <item id="c2" href="chapter2.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine>
    <itemref idref="c1" />
    <itemref idref="c2" />
  </spine>
</package>
""",
        )
        z.writestr(
            "OEBPS/chapter1.xhtml",
            """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>One</title></head><body>
<h1>Chapter One</h1>
<p>Jump to <a href="chapter2.xhtml#note">chapter two</a>.</p>
</body></html>""",
        )
        z.writestr(
            "OEBPS/chapter2.xhtml",
            """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Two</title></head><body>
<h1>Chapter Two</h1>
<p id="note">Note target.</p>
</body></html>""",
        )
    path.write_bytes(buf.getvalue())


def test_ingest_epub_resolves_cross_chapter_href_to_zip_internal_path(tmp_path: Path) -> None:
    epub = tmp_path / "two.epub"
    _write_two_spine_epub(epub)
    doc = ingest_epub(epub)
    chapters = doc.structured["_epub_meta"]["chapters"]
    assert chapters[0]["source_internal_path"] == "OEBPS/chapter1.xhtml"
    assert chapters[1]["source_internal_path"] == "OEBPS/chapter2.xhtml"
    md0 = chapters[0]["markdown"]
    assert "chapter two" in md0
    assert "OEBPS/chapter2.xhtml#note" in md0


def test_render_epub_rewrites_internal_href_to_output_chapter_basename(tmp_path: Path) -> None:
    epub = tmp_path / "two.epub"
    _write_two_spine_epub(epub)
    doc = ingest_epub(epub)
    book = build_book_reconstruction(doc.structured, source_pdf=None)
    out = tmp_path / "out.epub"
    payload = [
        {
            "index": c["index"],
            "title": c["title"],
            "markdown": c["markdown"],
            "source_pages": c.get("source_pages", []),
            "source_internal_path": c.get("source_internal_path"),
        }
        for c in book["chapters"]
    ]
    render_epub_from_book(
        book=book,
        translated_chapters=payload,
        output_path=out,
        title="T",
        language="en",
    )
    with ZipFile(out) as zf:
        names = [n for n in zf.namelist() if n.startswith("OEBPS/chapters/") and n.endswith(".xhtml")]
        assert len(names) == 2
        c1 = zf.read(sorted(names)[0]).decode("utf-8")
        assert "OEBPS/chapter2.xhtml" not in c1
        assert "002-chapter-two.xhtml" in c1
        assert "002-chapter-two.xhtml" in c1
        assert "#note" not in c1

    validation = validate_epub_internal_hrefs(out)
    assert validation["total_internal_hrefs"] >= 1
    assert validation["unresolved_internal_hrefs"] == 0
    assert validation["resolved_ratio"] == 1.0


def test_render_epub_restores_note_ids_and_backlinks(tmp_path: Path) -> None:
    out = tmp_path / "notes.epub"
    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            {
                "index": 1,
                "title": "Chapter",
                "source_internal_path": "OPS/c01.xhtml",
                "markdown": (
                    "# Chapter\n\n"
                    "Body text.[1](OPS/c01.xhtml#c01-note-0001)"
                    "[2](OPS/c01.xhtml#c01-note-0002)\n\n"
                    "## Notes\n\n"
                    "- [**1.**](OPS/c01.xhtml#R_c01-note-0001) Note text. "
                    "[**2.**](OPS/c01.xhtml#R_c01-note-0002) More note text."
                ),
            }
        ],
        output_path=out,
        title="Notes",
    )

    with ZipFile(out) as zf:
        chapter_name = next(
            name for name in zf.namelist()
            if name.startswith("OEBPS/chapters/") and name.endswith(".xhtml")
        )
        chapter = zf.read(chapter_name).decode("utf-8")
        ET.fromstring(chapter)
        assert 'xmlns:epub="http://www.idpf.org/2007/ops"' in chapter
        assert 'id="R_c01-note-0001"' in chapter
        assert 'epub:type="noteref"' in chapter
        assert 'id="c01-note-0001"' in chapter
        assert 'id="c01-note-0002"' in chapter
        assert 'epub:type="footnote"' in chapter
        assert "#c01-note-0001" in chapter
        assert "#c01-note-0002" in chapter
        assert "#R_c01-note-0001" in chapter
        assert "#R_c01-note-0002" in chapter

    validation = validate_epub_internal_hrefs(out)
    assert validation["unresolved_internal_hrefs"] == 0


def test_render_epub_keeps_uncited_note_without_broken_backlink(tmp_path: Path) -> None:
    out = tmp_path / "uncited-note.epub"
    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            {
                "index": 1,
                "title": "Chapter",
                "source_internal_path": "OPS/c01.xhtml",
                "markdown": (
                    "# Chapter\n\nBody text.\n\n## Notes\n\n"
                    "- [**1.**](OPS/c01.xhtml#R_c01-note-0001) Uncited note."
                ),
            }
        ],
        output_path=out,
        title="Notes",
    )

    with ZipFile(out) as zf:
        chapter_name = next(
            name for name in zf.namelist()
            if name.startswith("OEBPS/chapters/") and name.endswith(".xhtml")
        )
        chapter = zf.read(chapter_name).decode("utf-8")
        assert "Uncited note" in chapter
        assert "#R_c01-note-0001" not in chapter
        assert 'id="c01-note-0001"' in chapter

    validation = validate_epub_internal_hrefs(out)
    assert validation["unresolved_internal_hrefs"] == 0
