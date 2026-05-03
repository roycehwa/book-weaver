from pathlib import Path

from PIL import Image as PILImage
import pypdfium2 as pdfium

from pdf_translator.render import _estimate_column_widths, render_pdf_from_markdown


def test_render_pdf_from_markdown_creates_output(tmp_path: Path) -> None:
    output = tmp_path / "sample.pdf"
    render_pdf_from_markdown(
        title="Sample",
        markdown_text="# Heading\n\nA paragraph with **bold** text.\n\n- one\n- two\n",
        output_path=output,
    )

    assert output.exists()
    assert output.stat().st_size > 0


def test_render_pdf_embeds_image_when_path_exists(tmp_path: Path) -> None:
    img_path = tmp_path / "figure.png"
    PILImage.new("RGB", (200, 100), color=(128, 128, 128)).save(img_path)

    output = tmp_path / "with_image.pdf"
    render_pdf_from_markdown(
        title="Book with image",
        markdown_text=f"# Chapter\n\n![A grey box]({img_path})\n\nSome text after.\n",
        output_path=output,
    )

    assert output.exists()
    assert output.stat().st_size > 0


def test_render_pdf_resolves_relative_image_from_images_dir(tmp_path: Path) -> None:
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    img_path = images_dir / "figure.png"
    PILImage.new("RGB", (120, 80), color=(64, 64, 64)).save(img_path)

    output = tmp_path / "with_relative_image.pdf"
    render_pdf_from_markdown(
        title="Book with relative image",
        markdown_text="# Chapter\n\n![A grey box](figure.png)\n\nSome text after.\n",
        output_path=output,
        images_dir=images_dir,
    )

    assert output.exists()
    assert output.stat().st_size > 0


def test_render_pdf_fits_wide_table_to_page(tmp_path: Path) -> None:
    output = tmp_path / "wide_table.pdf"
    markdown_text = (
        "| A | B | C | D | E | F |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| very long cell value | another long cell value | c | d | e | f |\n"
    )
    render_pdf_from_markdown(
        title="Wide table",
        markdown_text=markdown_text,
        output_path=output,
    )

    assert output.exists()
    assert output.stat().st_size > 0


def test_render_pdf_skips_missing_image_gracefully(tmp_path: Path) -> None:
    output = tmp_path / "missing_img.pdf"
    render_pdf_from_markdown(
        title="Missing image",
        markdown_text="# Chapter\n\n![alt](/nonexistent/path/image.png)\n\nText after.\n",
        output_path=output,
    )

    assert output.exists()
    assert output.stat().st_size > 0


def test_render_pdf_ignores_html_comments(tmp_path: Path) -> None:
    output = tmp_path / "comment.pdf"
    render_pdf_from_markdown(
        title="Comment",
        markdown_text="<!-- mock translation chunk=16 target=zh-CN -->\n\nVisible text.\n",
        output_path=output,
    )

    assert output.exists()
    assert output.stat().st_size > 0


def test_render_pdf_handles_footnote_blockquotes(tmp_path: Path) -> None:
    output = tmp_path / "footnote.pdf"
    render_pdf_from_markdown(
        title="Footnote",
        markdown_text="Body text.\n\n> 3 A long footnote should render as a separate note block.\n\nNext body text.\n",
        output_path=output,
    )

    assert output.exists()
    assert output.stat().st_size > 0


def test_render_pdf_adds_page_break_before_top_level_heading(tmp_path: Path) -> None:
    output = tmp_path / "chapters.pdf"
    render_pdf_from_markdown(
        title="Chapters",
        markdown_text="# One\n\nOpening.\n\n# Two\n\nNext.\n",
        output_path=output,
    )

    doc = pdfium.PdfDocument(output)
    assert len(doc) >= 2


def test_estimate_column_widths_uses_content_weight() -> None:
    widths = _estimate_column_widths(
        [
            ["ID", "Description"],
            ["1", "A much longer descriptive cell that needs more horizontal space"],
        ],
        500,
    )

    assert widths[1] > widths[0]
    assert sum(widths) <= 500
