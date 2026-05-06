"""EPUB ingest: heading dedup, flow order (standalone images), OPF cover injection."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from pdf_translator.book_rebuild import build_book_reconstruction
from pdf_translator.ingest import _epub_maybe_repair_staccato_toc_lines, ingest_epub


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


def test_ingest_epub_nav_epub_type_toc_becomes_linked_lines(tmp_path: Path) -> None:
    xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head><body>
<nav epub:type="toc" id="contents">
  <p class="toc_title">Contents</p>
  <a href="chapter1.xhtml#a">First Part</a>
  <a href="chapter1.xhtml#b">Second Part</a>
</nav>
<p>Body after.</p>
</body></html>"""
    epub = tmp_path / "toc.epub"
    _write_epub(epub, chapter_xhtml=xhtml)
    doc = ingest_epub(epub)
    md = doc.structured["_epub_meta"]["chapters"][0]["markdown"]
    assert "[First Part](OEBPS/chapter1.xhtml#a)" in md
    assert "[Second Part](OEBPS/chapter1.xhtml#b)" in md
    assert "Body after." in md


def test_ingest_epub_drops_hidden_page_list_and_landmarks(tmp_path: Path) -> None:
    xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head><body>
<nav epub:type="toc" id="toc"><ol><li><a href="chapter1.xhtml#a">Main Chapter</a></li></ol></nav>
<nav epub:type="landmarks" hidden="hidden"><h1>Navigation</h1><ol><li><a href="cover.xhtml">Cover</a></li></ol></nav>
<nav epub:type="page-list" hidden="hidden" role="doc-pagelist"><h1>Page List</h1><ol><li><a href="chapter1.xhtml#page_1">1</a></li><li><a href="chapter1.xhtml#page_2">2</a></li></ol></nav>
</body></html>"""
    epub = tmp_path / "pagelist.epub"
    _write_epub(epub, chapter_xhtml=xhtml)
    doc = ingest_epub(epub)
    md = doc.structured["_epub_meta"]["chapters"][0]["markdown"]
    assert "Main Chapter" in md
    assert "Page List" not in md
    assert "Navigation" not in md
    assert "page_1" not in md


def test_epub_staccato_line_repair_merges_fragmented_column_flow() -> None:
    # Simulates two-column TOC where each glyph became its own line (many single-char rows)
    frag = "\n".join(list("ABCDEFGHIJ") * 3)
    assert len(frag.split("\n")) >= 24
    fixed = _epub_maybe_repair_staccato_toc_lines(frag + "\n\nNormal paragraph line here.")
    assert len(fixed.split("\n")) < len(frag.split("\n"))
    assert "".join(frag.split()) in "".join(fixed.split())


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


def test_ingest_epub_cleans_malformed_html_wrapper_lines(tmp_path: Path) -> None:
    xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head><body>
<p>p class="CRTF"&gt;Printed on acid-free paper. &lt;span class="crt-symb"&gt;&amp;#x221E;&lt;/span&gt;&lt;/p</p>
<p>Body text.</p>
</body></html>"""
    epub = tmp_path / "malformed.epub"
    _write_epub(epub, chapter_xhtml=xhtml)
    doc = ingest_epub(epub)
    md = doc.structured["_epub_meta"]["chapters"][0]["markdown"]
    assert 'p class="CRTF"' not in md
    assert "Printed on acid-free paper. ∞" in md
    assert "Body text." in md


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
    book = build_book_reconstruction(doc.structured, source_pdf=None)
    assert book["metadata"]["cover_image_path"]
    assert any(asset["kind"] == "cover" for asset in book["assets"])


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
