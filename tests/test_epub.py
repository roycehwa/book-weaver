from pathlib import Path
from zipfile import ZipFile

from pdf_translator.epub import render_epub_from_book
from pdf_translator.models import TranslatedChapter


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
        assert 'name="cover" content="image-1"' in opf
        assert 'id="image-1"' in opf and "cover-image" in opf

        nav = archive.read("OEBPS/nav.xhtml").decode("utf-8")
        assert nav.index("First Chapter") < nav.index("Second Chapter")
        chapter = archive.read("OEBPS/chapters/001-first-chapter.xhtml").decode("utf-8")
        assert 'href="../styles/book.css"' in chapter
        assert "../images/figure.png" in chapter
        assert "x &lt; y" in chapter or "x < y" in chapter
        css = archive.read("OEBPS/styles/book.css").decode("utf-8")
        assert "line-height: 1.82" in css
        assert "break-before: page" in css


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
        assert 'id="fnref-a"' in chapter
        assert 'epub:type="noteref"' in chapter
        assert 'href="#fn-footnote-a"' in chapter
        assert 'id="fn-footnote-a"' in chapter
        assert 'epub:type="footnote"' in chapter
        assert 'href="#fnref-a"' in chapter
        assert "说明文字。Book Title, p. 4." in chapter


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
