from pathlib import Path

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
