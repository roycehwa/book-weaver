from pathlib import Path
from zipfile import ZipFile

from pdf_translator.epub import render_epub_from_book
from pdf_translator.models import TranslatedChapter
from pdf_translator.review import translated_segments_to_chapters


def test_review_export_keeps_body_titles_without_injecting_source_toc_label(tmp_path: Path) -> None:
    chapters = translated_segments_to_chapters(
        [
            {
                "segment_id": "chapter-two:seg0001",
                "chapter_id": "chapter-two",
                "chapter_index": 2,
                "chapter_title": '2. Using "Good" Business to Fight "Bad" Business',
                "chapter_kind": "narrative",
                "block_index": 1,
                "translated_text": "## Two",
                "role": "heading",
                "is_chapter_title": False,
            },
            {
                "segment_id": "chapter-two:seg0002",
                "chapter_id": "chapter-two",
                "chapter_index": 2,
                "chapter_title": '2. Using "Good" Business to Fight "Bad" Business',
                "chapter_kind": "narrative",
                "block_index": 2,
                "translated_text": '## 用“良善”商业对抗“不良”商业',
                "role": "heading",
                "is_chapter_title": False,
            },
            {
                "segment_id": "chapter-two:seg0003",
                "chapter_id": "chapter-two",
                "chapter_index": 2,
                "chapter_title": '2. Using "Good" Business to Fight "Bad" Business',
                "chapter_kind": "narrative",
                "block_index": 3,
                "translated_text": "正文。",
                "role": "prose",
                "is_chapter_title": False,
            },
        ]
    )
    output = tmp_path / "reviewed.epub"
    render_epub_from_book(
        book={}, translated_chapters=chapters, output_path=output,
        title="Synthetic", language="zh-CN",
    )
    with ZipFile(output) as archive:
        chapter_path = next(name for name in archive.namelist() if name.startswith("OEBPS/chapters/"))
        chapter = archive.read(chapter_path).decode("utf-8")
        nav = archive.read("OEBPS/nav.xhtml").decode("utf-8")
    body = chapter.split("<body", 1)[1]
    assert '<h2>Two</h2>' in body
    assert '<h2>用“良善”商业对抗“不良”商业</h2>' in body
    assert "正文。" in body
    assert "<h1>" not in body
    assert 'Using &quot;Good&quot; Business' not in body
    assert 'Using &quot;Good&quot; Business' in nav


def test_export_has_resource_budget_and_no_automatic_fonts(tmp_path, monkeypatch):
    import json
    for key in ("BOOKWEAVER_EPUB_FONT", "BOOKWEAVER_EPUB_FONT_CJK", "BOOKWEAVER_EPUB_FONT_LATIN"):
        monkeypatch.delenv(key, raising=False)
    output = tmp_path / "small.epub"
    render_epub_from_book(book={}, translated_chapters=[{"index": 1, "title": "正文", "markdown": "# 正文\n\n" + "中文正文。" * 10000}], output_path=output, title="测试", language="zh-CN")
    report = json.loads(output.with_suffix(".resources.json").read_text())
    assert report["font_count"] == 0
    assert report["total_bytes"] < 100000
    with ZipFile(output) as archive:
        assert not any("/fonts/" in name for name in archive.namelist())


def test_image_optimization_is_lossless_and_keeps_source(tmp_path):
    from PIL import Image
    from io import BytesIO
    from pdf_translator.epub import _export_image_bytes
    image = Image.new("RGB", (800, 1000), "white")
    image.putpixel((200, 300), (20, 40, 60))
    path = tmp_path / "page.png"
    image.save(path, compress_level=0)
    original = path.read_bytes()
    optimized = _export_image_bytes(path)
    assert len(optimized) < len(original)
    assert path.read_bytes() == original
    with Image.open(BytesIO(optimized)) as decoded:
        assert decoded.size == image.size
        assert decoded.tobytes() == image.tobytes()


def test_gray_rgb_export_preserves_every_pixel(tmp_path):
    from PIL import Image
    from io import BytesIO
    from pdf_translator.epub import _export_image_bytes
    image = Image.new("RGB", (128, 128))
    image.putdata([(x % 256,) * 3 for x in range(128 * 128)])
    path = tmp_path / "gray.png"
    image.save(path)
    original = path.read_bytes()
    decoded = Image.open(BytesIO(_export_image_bytes(path)))
    assert decoded.size == image.size
    assert decoded.convert("RGB").tobytes() == image.tobytes()
    assert path.read_bytes() == original


def test_render_epub_from_book_writes_epub_structure_and_chapters(tmp_path: Path) -> None:
    image_path = tmp_path / "figure.png"
    image_path.write_bytes(b"fake-png")
    output_path = tmp_path / "book.epub"

    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            TranslatedChapter(
                index=1,
                chapter_id="ch-001-first-chapter",
                title="First Chapter",
                page_start=1,
                page_end=2,
                source_pages=[1, 2],
                markdown=f"# First Chapter\n\n![Figure]({image_path})\n\nBody with x < y.",
            ),
            TranslatedChapter(
                index=2,
                chapter_id="ch-002-second-chapter",
                title="Second Chapter",
                page_start=3,
                page_end=4,
                source_pages=[3, 4],
                markdown="# Second Chapter\n\nMore body.",
            ),
        ],
        output_path=output_path,
        title="Sample Book",
        language="en",
        source_language="en",
        content_is_translated=False,
    )

    with ZipFile(output_path) as archive:
        names = archive.namelist()
        assert names[0] == "mimetype"
        assert archive.read("mimetype") == b"application/epub+zip"
        assert "META-INF/container.xml" in names
        assert "OEBPS/content.opf" in names
        assert "OEBPS/nav.xhtml" in names
        assert "OEBPS/styles/book.css" in names
        assert "OEBPS/chapters/001-first-chapter.xhtml" in names
        assert "OEBPS/chapters/002-second-chapter.xhtml" in names
        assert "OEBPS/images/figure.png" in names

        opf = archive.read("OEBPS/content.opf").decode("utf-8")
        assert 'name="cover"' not in opf
        assert 'id="image-1"' in opf and "cover-image" not in opf

        nav = archive.read("OEBPS/nav.xhtml").decode("utf-8")
        assert nav.index("First Chapter") < nav.index("Second Chapter")
        chapter = archive.read("OEBPS/chapters/001-first-chapter.xhtml").decode("utf-8")
        assert 'href="../styles/book.css"' in chapter
        assert "../images/figure.png" in chapter
        assert "x &lt; y" in chapter or "x < y" in chapter
        css = archive.read("OEBPS/styles/book.css").decode("utf-8")
        assert "line-height: 1.82" in css
        assert "bookweaver-chapter-start" in css
        assert "Songti SC" not in css or "@font-face" in css
        second = archive.read("OEBPS/chapters/002-second-chapter.xhtml").decode("utf-8")
        assert 'class="bookweaver-chapter-start"' in second


def test_render_epub_dedupes_identical_image_bytes(tmp_path: Path) -> None:
    image_a = tmp_path / "book-images" / "figure-a.png"
    image_b = tmp_path / "images" / "figure-b.png"
    image_a.parent.mkdir(parents=True)
    image_b.parent.mkdir(parents=True)
    payload = b"same-image-bytes"
    image_a.write_bytes(payload)
    image_b.write_bytes(payload)
    output_path = tmp_path / "book.epub"

    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            {
                "index": 1,
                "title": "Chapter",
                "markdown": (
                    f"![Figure A]({image_a})\n\n"
                    f"![Figure B]({image_b})"
                ),
            }
        ],
        output_path=output_path,
        title="Dedup Book",
        image_roots=[tmp_path],
    )

    with ZipFile(output_path) as archive:
        image_names = [name for name in archive.namelist() if name.startswith("OEBPS/images/")]
        assert len(image_names) == 1


def test_render_epub_strips_oceanpdf_watermarks(tmp_path: Path) -> None:
    output_path = tmp_path / "book.epub"

    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            {
                "index": 1,
                "title": "Chapter 1",
                "markdown": "# Chapter 1\n\nBody text.\n\n## —\n\n## OceanofPDF.com",
            }
        ],
        output_path=output_path,
        title="Sample Book",
        language="en",
        source_language="en",
        content_is_translated=False,
    )

    with ZipFile(output_path) as archive:
        chapter = archive.read("OEBPS/chapters/001-chapter-1.xhtml").decode("utf-8")
        assert "OceanofPDF" not in chapter
        assert "Body text." in chapter


def test_render_epub_resolves_relative_original_page_from_book_images(
    tmp_path: Path,
) -> None:
    images_dir = tmp_path / "book-images"
    images_dir.mkdir()
    page_image = images_dir / "original-page-p0006.png"
    page_image.write_bytes(b"png")
    output_path = tmp_path / "book.epub"

    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            {
                "index": 1,
                "title": "Resource",
                "markdown": "![Original page 6](original-page-p0006.png)",
            }
        ],
        output_path=output_path,
        title="Resource Book",
    )

    with ZipFile(output_path) as archive:
        assert any(
            name.endswith("/original-page-p0006.png")
            for name in archive.namelist()
        )


def test_render_epub_from_book_renders_markdown_footnotes_as_notes(tmp_path: Path) -> None:
    output_path = tmp_path / "footnotes.epub"

    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            {
                "index": 1,
                "title": "Chapter",
                "markdown": (
                    "# Chapter\n\n正文脚注。[^3]\n\n"
                    "[^3]: 《内部参考》54期（1951年3月31日），第153–160页。"
                ),
            }
        ],
        output_path=output_path,
        title="Footnote Book",
    )

    with ZipFile(output_path) as archive:
        chapter = archive.read("OEBPS/chapters/001-chapter.xhtml").decode("utf-8")
        assert 'href="《内部参考》54期（1951年3月31日），第153–160页。"' not in chapter
        assert "《内部参考》54期（1951年3月31日），第153–160页。" in chapter
        assert 'class="footnote"' in chapter
        assert 'class="footnote-ref"' in chapter


def test_render_epub_from_book_renders_semantic_footnote_with_backlink(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "semantic-footnote.epub"
    render_epub_from_book(
        book={
            "semantic_content": {
                "footnotes": [
                    {
                        "footnote_id": "footnote-a",
                        "marker": "1",
                        "source_page": 1,
                        "backlinks": [
                            {
                                "reference_id": "fnref-a",
                                "chapter_id": "ch-001",
                                "marker": "1",
                            }
                        ],
                        "spans": [
                            {
                                "kind": "prose",
                                "source_text": "Explanation.",
                                "translated_text": "说明文字。",
                            },
                            {
                                "kind": "citation",
                                "source_text": "Book Title, p. 4.",
                                "translated_text": "Book Title, p. 4.",
                            },
                        ],
                    }
                ]
            }
        },
        translated_chapters=[
            {
                "index": 1,
                "chapter_id": "ch-001",
                "title": "Chapter",
                "source_pages": [1],
                "markdown": "# Chapter\n\n正文。",
            }
        ],
        output_path=output_path,
        title="Semantic Notes",
    )

    with ZipFile(output_path) as archive:
        chapter = archive.read("OEBPS/chapters/001-chapter.xhtml").decode("utf-8")
        assert 'id="fn-footnote-a"' in chapter
        assert 'epub:type="noteref"' not in chapter
        assert 'role="doc-footnote"' not in chapter
        assert 'class="semantic-footnote-refs"' not in chapter
        assert "说明文字。Book Title, p. 4." in chapter


def test_render_epub_removes_marker_only_trailing_note_cluster(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "marker-only-notes.epub"
    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            {
                "index": 1,
                "chapter_id": "ch-001",
                "title": "Chapter",
                "source_pages": [1],
                "markdown": (
                    "# Chapter\n\n"
                    + "\n\n".join(f"正文段落 {index}。" for index in range(1, 8))
                    + "\n\n30\n\n31\n\n32"
                ),
            }
        ],
        output_path=output_path,
        title="Marker Notes",
    )

    with ZipFile(output_path) as archive:
        chapter = archive.read("OEBPS/chapters/001-chapter.xhtml").decode("utf-8")
        assert "<p>30</p>" not in chapter
        assert "<p>31</p>" not in chapter
        assert "<p>32</p>" not in chapter
        assert "本章注释" not in chapter


def test_render_epub_from_book_removes_orphan_footnote_backlink(tmp_path: Path) -> None:
    output_path = tmp_path / "orphan-footnote.epub"

    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            {
                "index": 1,
                "title": "Chapter",
                "markdown": "# Chapter\n\n正文中的旧引用已变成上标。⁹⁶\n\n[^96]: 保留的脚注内容。",
            }
        ],
        output_path=output_path,
        title="Footnote Book",
    )

    with ZipFile(output_path) as archive:
        chapter = archive.read("OEBPS/chapters/001-chapter.xhtml").decode("utf-8")
        assert "保留的脚注内容。" in chapter
        assert 'href="#fnref:96"' not in chapter


def test_render_epub_from_book_handles_control_chars(tmp_path: Path) -> None:
    output_path = tmp_path / "book.epub"

    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            {
                "index": 1,
                "title": "Unsafe",
                "markdown": "# Unsafe\n\nA map <T> and a control char \x05 should not break EPUB.",
            }
        ],
        output_path=output_path,
        title="Unsafe Book",
    )

    with ZipFile(output_path) as archive:
        chapter = archive.read("OEBPS/chapters/001-unsafe.xhtml").decode("utf-8")
        assert "A map" in chapter
        assert "\x05" not in chapter


def test_image_paths_with_title_punctuation_still_resolve(tmp_path: Path) -> None:
    from bs4 import BeautifulSoup

    from pdf_translator.epub import _markdown_to_body_html, _resolve_image_source_path

    image_dir = tmp_path / "book-images"
    image_dir.mkdir()
    (image_dir / "photo-83.jpg").write_bytes(b"jpg")
    stored = tmp_path / "Title's Work (Press)" / "book-images" / "photo-83.jpg"
    markdown = f"![图]({stored})\n"
    html = _markdown_to_body_html(markdown)
    src = BeautifulSoup(html, "html.parser").find("img").get("src")
    assert _resolve_image_source_path(str(src), [image_dir]) == (image_dir / "photo-83.jpg").resolve()


def test_unresolved_image_file_is_omitted_and_a_note_marker_stays(tmp_path: Path) -> None:
    from pdf_translator.epub import validate_epub_internal_hrefs

    image_dir = tmp_path / "book-images"
    image_dir.mkdir()
    (image_dir / "kept.jpg").write_bytes(b"jpg")
    missing = tmp_path / "Title's Work (Press)" / "book-images" / "gone.jpg"
    output_path = tmp_path / "book.epub"

    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            {
                "index": 1,
                "title": "Chapter",
                "markdown": (
                    f"![Kept]({image_dir / 'kept.jpg'})\n\n"
                    f"![Gone]({missing})\n\n"
                    "See ![50](OEBPS/part.xhtml#note).\n"
                ),
            }
        ],
        output_path=output_path,
        title="Images",
        image_roots=[image_dir],
    )

    with ZipFile(output_path) as archive:
        names = archive.namelist()
        assert any(name.endswith("kept.jpg") for name in names)
        assert not any("gone.jpg" in name for name in names)
        chapter = archive.read("OEBPS/chapters/001-chapter.xhtml").decode("utf-8")
        assert "gone.jpg" not in chapter
        assert "kept.jpg" in chapter
        assert "50" in chapter
        assert "part.xhtml" not in chapter
    validation = validate_epub_internal_hrefs(output_path)
    assert validation["missing_assets"] == []
    assert validation["absolute_paths"] == []


def test_render_epub_from_book_recovers_moved_absolute_image_paths(tmp_path: Path) -> None:
    output_dir = tmp_path / "OK" / "book"
    image_dir = output_dir / "book-images"
    image_dir.mkdir(parents=True)
    (image_dir / "figure-p0001-01.png").write_bytes(b"fake-png")
    stale_path = tmp_path / "NG" / "book" / "book-images" / "figure-p0001-01.png"
    output_path = output_dir / "book.epub"

    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            {
                "index": 1,
                "title": "Chapter",
                "markdown": f"# Chapter\n\n![Figure]({stale_path})\n\nBody.",
            }
        ],
        output_path=output_path,
        title="Moved Book",
    )

    with ZipFile(output_path) as archive:
        names = archive.namelist()
        assert "OEBPS/images/figure-p0001-01.png" in names
        chapter = archive.read("OEBPS/chapters/001-chapter.xhtml").decode("utf-8")
        assert "../images/figure-p0001-01.png" in chapter


def test_render_epub_uses_explicit_image_roots_for_versioned_output(tmp_path: Path) -> None:
    image_dir = tmp_path / "run" / "book-images"
    image_dir.mkdir(parents=True)
    (image_dir / "figure.png").write_bytes(b"fake-png")
    stale_path = tmp_path / "stale run" / "book-images" / "figure.png"
    output_path = tmp_path / "run" / "versions" / "final" / "book.epub"

    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            {
                "index": 1,
                "title": "Chapter",
                "markdown": f"![Figure]({stale_path})",
            }
        ],
        output_path=output_path,
        title="Reviewed Book",
        image_roots=[image_dir],
    )

    with ZipFile(output_path) as archive:
        assert "OEBPS/images/figure.png" in archive.namelist()


def test_render_epub_from_book_hides_non_toc_chapters_from_nav(tmp_path: Path) -> None:
    cover_path = tmp_path / "cover.png"
    cover_path.write_bytes(b"fake-png")
    output_path = tmp_path / "book.epub"

    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            {
                "index": 1,
                "title": "Cover",
                "markdown": f"![Cover]({cover_path})",
                "toc": False,
            },
            {
                "index": 2,
                "title": "Chapter 1",
                "markdown": "# Chapter 1\n\nBody text.",
            },
        ],
        output_path=output_path,
        title="Book With Cover",
    )

    with ZipFile(output_path) as archive:
        names = archive.namelist()
        assert "OEBPS/chapters/001-cover.xhtml" in names
        assert "OEBPS/chapters/002-chapter-1.xhtml" in names
        nav = archive.read("OEBPS/nav.xhtml").decode("utf-8")
        assert 'href="chapters/001-cover.xhtml"' not in nav
        assert ">Cover</a>" not in nav
        assert "Chapter 1" in nav


def test_render_epub_from_book_escapes_raw_html_examples(tmp_path: Path) -> None:
    output_path = tmp_path / "book.epub"

    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            {
                "index": 1,
                "title": "Notes",
                "markdown": 'Example code: <a href="https://example.test"><img src="https://example.test/x.png" /></a><br />Done.',
            }
        ],
        output_path=output_path,
        title="Raw HTML",
    )

    with ZipFile(output_path) as archive:
        chapter = archive.read("OEBPS/chapters/001-notes.xhtml").decode("utf-8")
        assert "<img" not in chapter
        assert "&lt;img" in chapter
        assert "Done." in chapter


def test_render_epub_from_book_compacts_trailing_chapter_notes(tmp_path: Path) -> None:
    output_path = tmp_path / "book.epub"
    body = "\n\n".join(f"Paragraph {index} with normal body text." for index in range(1, 9))
    notes = "\n\n".join(
        [
            "1",
            "First chapter-end note.",
            "2",
            "Second chapter-end note.",
            "3",
            "Third chapter-end note.",
        ]
    )

    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            {
                "index": 1,
                "title": "Chapter With Notes",
                "markdown": f"# Chapter With Notes\n\n{body}\n\n{notes}",
            }
        ],
        output_path=output_path,
        title="Notes Book",
    )

    with ZipFile(output_path) as archive:
        chapter = archive.read("OEBPS/chapters/001-chapter-with-notes.xhtml").decode("utf-8")
        css = archive.read("OEBPS/styles/book.css").decode("utf-8")
        assert 'class="chapter-notes"' in chapter
        assert "本章注释" in chapter
        assert chapter.index("Paragraph 8") < chapter.index('class="chapter-notes"')
        assert "First chapter-end note." in chapter
        assert ".chapter-notes" in css


def test_render_epub_from_book_wraps_preserved_back_matter(tmp_path: Path) -> None:
    output_path = tmp_path / "book.epub"

    render_epub_from_book(
        book={"chapters": []},
        translated_chapters=[
            {
                "index": 1,
                "title": "Notes",
                "markdown": "# Notes\n\n- 1. A preserved note.",
                "translate": False,
                "preserve_original": True,
                "toc": False,
            }
        ],
        output_path=output_path,
        title="Back Matter",
    )

    with ZipFile(output_path) as archive:
        chapter = archive.read("OEBPS/chapters/001-notes.xhtml").decode("utf-8")
        nav = archive.read("OEBPS/nav.xhtml").decode("utf-8")
        css = archive.read("OEBPS/styles/book.css").decode("utf-8")
        assert 'class="preserved-apparatus"' in chapter
        assert "A preserved note." in chapter
        assert ">Notes</a>" not in nav
        assert ".preserved-apparatus" in css
