from pathlib import Path

import pytest

from pdf_translator.epub import render_epub_from_book
from pdf_translator.render import render_pdf_from_markdown


def test_epub_render_failure_preserves_previous_output_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pdf_translator.epub as epub_module

    output = tmp_path / "book.epub"
    report = tmp_path / "book.resources.json"
    output.write_bytes(b"previous-epub")
    report.write_text("previous-report", encoding="utf-8")

    class ExplodingArchive:
        def __init__(self, path, *_args, **_kwargs):
            self.path = Path(path)

        def __enter__(self):
            self.path.write_bytes(b"partial-epub")
            return self

        def __exit__(self, *_args):
            return False

        def writestr(self, *_args, **_kwargs):
            raise OSError("injected zip failure")

    monkeypatch.setattr(epub_module, "ZipFile", ExplodingArchive)

    with pytest.raises(OSError, match="injected zip failure"):
        render_epub_from_book(
            book={},
            translated_chapters=[{"index": 1, "title": "Body", "markdown": "Text"}],
            output_path=output,
            title="Synthetic",
        )

    assert output.read_bytes() == b"previous-epub"
    assert report.read_text(encoding="utf-8") == "previous-report"
    assert not list(tmp_path.glob(".*.tmp.epub"))
    assert not list(tmp_path.glob(".*.tmp"))


def test_pdf_render_failure_preserves_previous_output_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pdf_translator.render as render_module

    output = tmp_path / "book.pdf"
    output.write_bytes(b"previous-pdf")

    class ExplodingDocument:
        def __init__(self, path, **_kwargs):
            self.path = Path(path)

        def addPageTemplates(self, _templates):
            return None

        def build(self, _story):
            self.path.write_bytes(b"partial-pdf")
            raise OSError("injected pdf failure")

    monkeypatch.setattr(render_module, "BaseDocTemplate", ExplodingDocument)

    with pytest.raises(OSError, match="injected pdf failure"):
        render_pdf_from_markdown("Synthetic", "Body text.", output)

    assert output.read_bytes() == b"previous-pdf"
    assert not list(tmp_path.glob(".*.tmp.pdf"))
