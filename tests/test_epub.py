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
                title="First Chapter",
                page_start=1,
                page_end=2,
                source_pages=[1, 2],
                markdown=f"# First Chapter\n\n![Figure]({image_path})\n\nBody with x < y.",
            ),
            TranslatedChapter(
                index=2,
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
        assert "../images/figure.png" in chapter
        assert "x &lt; y" in chapter or "x < y" in chapter
        css = archive.read("OEBPS/styles/book.css").decode("utf-8")
        assert "line-height: 1.82" in css
        assert "break-before: page" in css


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
