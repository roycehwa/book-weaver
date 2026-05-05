"""EPUB ingest: heading dedup, flow order (standalone images), OPF cover injection."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from pdf_translator.book_rebuild import build_book_reconstruction
from pdf_translator.ingest import ingest_epub


def _write_epub(
    path: Path,
    *,
    opf_extra_meta: str = "",
    opf_manifest_extra: str = "",
    chapter_xhtml: str,
    image_bytes: bytes | None = None,
    image_href: str = "OEBPS/images/pix.png",
) -> None:
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
        manifest_image = ""
        if image_bytes is not None:
            z.writestr(image_href, image_bytes)
            if not opf_manifest_extra.strip():
                manifest_image = '    <item id="pix" href="images/pix.png" media-type="image/png" />\n'
        z.writestr(
            "OEBPS/content.opf",
            f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:test</dc:identifier>
    <dc:title>Test</dc:title>
    <dc:language>en</dc:language>
{opf_extra_meta}
  </metadata>
  <manifest>
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml" />
{manifest_image}{opf_manifest_extra}
  </manifest>
  <spine>
    <itemref idref="chap1" />
  </spine>
</package>
""",
        )
        z.writestr("OEBPS/chapter1.xhtml", chapter_xhtml)
    path.write_bytes(buf.getvalue())


def test_ingest_epub_preserves_external_link_in_paragraph(tmp_path: Path) -> None:
    xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head><body>
<p>See <a href="https://example.org/doc">the doc</a> for details.</p>
</body></html>"""
    epub = tmp_path / "links.epub"
    _write_epub(epub, chapter_xhtml=xhtml)
    doc = ingest_epub(epub)
    md = doc.structured["_epub_meta"]["chapters"][0]["markdown"]
    assert "[the doc](https://example.org/doc)" in md


def test_ingest_epub_chapter_title_not_tripled_in_full_markdown(tmp_path: Path) -> None:
    xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Ignored</title></head><body>
<h1>Alpha Section</h1>
<p>Body line one.</p>
</body></html>"""
    epub = tmp_path / "t.epub"
    _write_epub(epub, chapter_xhtml=xhtml)
    doc = ingest_epub(epub)
    book = build_book_reconstruction(doc.structured, source_pdf=None)
    fm = book["full_markdown"]
    assert fm.count("Alpha Section") == 1
    assert "## Alpha Section" not in fm
    assert "Body line one." in fm


def test_ingest_epub_standalone_cover_image_in_flow(tmp_path: Path) -> None:
    xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head><body>
<div><img src="images/pix.png" alt="Hero"/></div>
<p>After image.</p>
</body></html>"""
    epub = tmp_path / "t.epub"
    _write_epub(epub, chapter_xhtml=xhtml, image_bytes=b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
    doc = ingest_epub(epub)
    md = doc.structured["_epub_meta"]["chapters"][0]["markdown"]
    assert "![Hero]" in md
    assert "After image." in md
    assert md.index("![Hero]") < md.index("After image.")


def test_ingest_epub_opf_cover_prepended_when_not_in_spine(tmp_path: Path) -> None:
    xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>X</title></head><body>
<p>Chapter only.</p>
</body></html>"""
    epub = tmp_path / "t.epub"
    tiny_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    _write_epub(
        epub,
        chapter_xhtml=xhtml,
        image_bytes=tiny_png,
        opf_manifest_extra='    <item id="cover-img" href="images/pix.png" media-type="image/png" properties="cover-image" />\n',
        opf_extra_meta='    <meta name="cover" content="cover-img" />\n',
    )
    doc = ingest_epub(epub)
    titles = [c["title"] for c in doc.structured["_epub_meta"]["chapters"]]
    assert titles[0] == "Cover"
    assert "![Cover]" in doc.structured["_epub_meta"]["chapters"][0]["markdown"]


def test_ingest_epub_resolves_parent_relative_image_paths(tmp_path: Path) -> None:
    """Images like src=\"../Images/a.png\" from xhtml/ must resolve inside the zip."""
    tiny_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head><body>
<p><img src="../Images/pix.png" alt="Fig"/></p>
</body></html>"""
    epub = tmp_path / "rel.epub"
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
        z.writestr("OEBPS/Images/pix.png", tiny_png)
        z.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="id" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="id">urn:t</dc:identifier>
    <dc:title>T</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="c1" href="xhtml/p.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine><itemref idref="c1" /></spine>
</package>
""",
        )
        z.writestr("OEBPS/xhtml/p.xhtml", xhtml)
    epub.write_bytes(buf.getvalue())
    doc = ingest_epub(epub)
    md = doc.structured["_epub_meta"]["chapters"][0]["markdown"]
    assert "![Fig]" in md


def test_ingest_epub_no_duplicate_cover_when_spine_references_file(tmp_path: Path) -> None:
    tiny_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>X</title></head><body>
<p><img src="images/pix.png" alt="inline"/></p>
</body></html>"""
    epub = tmp_path / "t.epub"
    _write_epub(
        epub,
        chapter_xhtml=xhtml,
        image_bytes=tiny_png,
        opf_manifest_extra='    <item id="cover-img" href="images/pix.png" media-type="image/png" properties="cover-image" />\n',
    )
    doc = ingest_epub(epub)
    assert all(c["title"] != "Cover" for c in doc.structured["_epub_meta"]["chapters"])
