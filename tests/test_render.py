from pathlib import Path

from PIL import Image as PILImage

from pdf_translator.render import render_pdf_from_markdown


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


def test_render_pdf_skips_missing_image_gracefully(tmp_path: Path) -> None:
    output = tmp_path / "missing_img.pdf"
    render_pdf_from_markdown(
        title="Missing image",
        markdown_text="# Chapter\n\n![alt](/nonexistent/path/image.png)\n\nText after.\n",
        output_path=output,
    )

    assert output.exists()
    assert output.stat().st_size > 0
