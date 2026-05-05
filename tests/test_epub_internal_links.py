"""Cross-chapter EPUB ingest links and output EPUB href rewriting."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from zipfile import ZipFile

from pdf_translator.book_rebuild import build_book_reconstruction
from pdf_translator.epub import render_epub_from_book
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
        assert "002-chapter-two.xhtml#note" in c1 or "chapter-two.xhtml#note" in c1
