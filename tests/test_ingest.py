from pathlib import Path

from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat

import pytest
import pypdfium2 as pdfium

from pdf_translator.guardrails import InputGateError, PdfPreflight, _enforce_text_layer, ingest_pdf_guarded
from pdf_translator.ingest import build_pdf_converter, clean_book_reflow_markdown
from pdf_translator.models import NormalizedDocument


def test_build_pdf_converter_uses_fast_native_pdf_settings() -> None:
    converter = build_pdf_converter()
    pdf_option = converter.format_to_options[InputFormat.PDF]

    assert pdf_option.backend is PyPdfiumDocumentBackend
    assert pdf_option.pipeline_options.do_ocr is False
    assert pdf_option.pipeline_options.do_table_structure is False
    assert pdf_option.pipeline_options.force_backend_text is True


def test_build_pdf_converter_book_enables_table_structure_and_images() -> None:
    converter = build_pdf_converter(enable_table_structure=True, generate_picture_images=True)
    pdf_option = converter.format_to_options[InputFormat.PDF]

    assert pdf_option.pipeline_options.do_table_structure is True
    assert pdf_option.pipeline_options.generate_picture_images is True
    assert pdf_option.pipeline_options.images_scale == 2.0


def test_clean_book_reflow_markdown_removes_pagination_noise() -> None:
    markdown = """
Running Book Header

1

Chapter opening paragraph.

![Image](/tmp/image.png)

Running Book Header

2

Another body paragraph.

| A | B |
| --- | --- |
| 1 | 2 |

Running Book Header

3

6. Nelson, N. C. (2018). Model Behavior.
"""

    cleaned = clean_book_reflow_markdown(markdown)

    assert "Running Book Header" not in cleaned
    assert "\n1\n" not in cleaned
    assert "\n2\n" not in cleaned
    assert "Chapter opening paragraph." in cleaned
    assert "![Image](/tmp/image.png)" in cleaned
    assert "| A | B |" in cleaned
    assert "6. Nelson" in cleaned


def test_clean_book_reflow_markdown_keeps_first_repeated_section_heading() -> None:
    markdown = """
## References

A. First reference.

## References

B. Second reference.

## References

C. Third reference.
"""

    cleaned = clean_book_reflow_markdown(markdown)

    assert cleaned.count("## References") == 1
    assert "A. First reference." in cleaned
    assert "B. Second reference." in cleaned
    assert "C. Third reference." in cleaned


def test_clean_book_reflow_markdown_reflows_split_prose_without_crossing_components() -> None:
    markdown = """
This paragraph was broken by

the source page layout

and should become one paragraph.

![Figure](figure.png)

Figure caption starts here

and continues after the image.

| A | B |
| --- | --- |
| 1 | 2 |

## New Section

Fresh paragraph.
"""

    cleaned = clean_book_reflow_markdown(markdown)

    assert "This paragraph was broken by the source page layout and should become one paragraph." in cleaned
    assert "![Figure](figure.png)" in cleaned
    assert "| A | B |" in cleaned
    assert "## New Section" in cleaned
    assert cleaned.index("![Figure](figure.png)") < cleaned.index("Figure caption starts here")
    assert cleaned.index("| A | B |") < cleaned.index("## New Section")


def test_clean_book_reflow_markdown_removes_pdf_control_artifacts() -> None:
    markdown = "A vari\x02 ous hyphen-\nated example."

    cleaned = clean_book_reflow_markdown(markdown)

    assert "various" in cleaned
    assert "hyphenated" in cleaned
    assert "\x02" not in cleaned


def test_clean_book_reflow_markdown_marks_bare_numbered_footnotes() -> None:
    markdown = """
Main paragraph ends here.

3 The transcription of the poem is quoted here as a long explanatory note.

Another paragraph starts here.
"""

    cleaned = clean_book_reflow_markdown(markdown)

    assert "> 3 The transcription of the poem" in cleaned
    assert "Main paragraph ends here.\n\n> 3" in cleaned
    assert "note.\n\nAnother paragraph starts here." in cleaned


def test_clean_book_reflow_markdown_splits_multiple_footnotes_in_one_block() -> None:
    markdown = """
3 First extracted note was merged with the following note. 4 Second extracted note should stand alone.

Body text has a sticky footnote marker.5 It should get spacing.
"""

    cleaned = clean_book_reflow_markdown(markdown)

    assert "> 3 First extracted note was merged with the following note." in cleaned
    assert "> 4 Second extracted note should stand alone." in cleaned
    assert "marker. 5 It should get spacing." in cleaned


def test_enforce_text_layer_rejects_scan_like_document() -> None:
    source_pdf = Path(__file__)
    normalized = NormalizedDocument(
        source_pdf=source_pdf,
        raw_markdown="<!-- image -->\n\n" * 30,
        reconstructed_markdown="<!-- image -->\n\n" * 30,
        structured={"texts": [], "pages": []},
        detected_language=None,
    )
    preflight = PdfPreflight(
        source_pdf=source_pdf,
        profile_name="magazine",
        page_count=24,
        file_size_bytes=1,
        warn_page_count=112,
        max_page_count=220,
        warn_file_size_mb=40.0,
        max_file_size_mb=100.0,
    )

    with pytest.raises(InputGateError):
        _enforce_text_layer(normalized, preflight)

    assert preflight.text_layer_chars is not None
    assert preflight.image_marker_count == 30


def _make_test_pdf(path: Path, page_count: int) -> None:
    document = pdfium.PdfDocument.new()
    try:
        for _ in range(page_count):
            page = document.new_page(200, 200)
            page.close()
        document.save(str(path))
    finally:
        document.close()


def test_ingest_pdf_guarded_soft_page_limit_accepts_over_limit_pdf(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_pdf = tmp_path / "sample.pdf"
    _make_test_pdf(source_pdf, page_count=4)

    captured_source: dict[str, Path] = {}

    def fake_ingest(path: Path, *, output_dir: Path | None = None, profile: str = "auto") -> NormalizedDocument:
        captured_source["path"] = path
        return NormalizedDocument(
            source_pdf=path,
            raw_markdown="Narrative text.\n\n" * 20,
            reconstructed_markdown="Narrative text.\n\n" * 20,
            structured={"body": {"children": []}, "texts": []},
            detected_language="en",
        )

    monkeypatch.setattr("pdf_translator.guardrails.ingest_pdf", fake_ingest)

    normalized, preflight = ingest_pdf_guarded(
        source_pdf,
        profile_name="magazine",
        timeout_seconds=0,
        max_page_count=2,
        soft_input_gate=True,
        soft_page_limit=2,
    )

    assert normalized.source_pdf == source_pdf
    assert preflight.page_count == 4
    assert preflight.ingest_page_count == 2
    assert any("Soft gate applied" in warning for warning in preflight.warnings)
    assert captured_source["path"] != source_pdf


def test_ingest_pdf_guarded_strict_gate_still_rejects(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_pdf = tmp_path / "sample.pdf"
    _make_test_pdf(source_pdf, page_count=4)

    def fake_ingest(path: Path, *, output_dir: Path | None = None, profile: str = "auto") -> NormalizedDocument:
        return NormalizedDocument(
            source_pdf=path,
            raw_markdown="Narrative text.\n\n" * 20,
            reconstructed_markdown="Narrative text.\n\n" * 20,
            structured={"body": {"children": []}, "texts": []},
            detected_language="en",
        )

    monkeypatch.setattr("pdf_translator.guardrails.ingest_pdf", fake_ingest)

    with pytest.raises(InputGateError):
        ingest_pdf_guarded(
            source_pdf,
            profile_name="magazine",
            timeout_seconds=0,
            max_page_count=2,
            soft_input_gate=False,
            soft_page_limit=2,
        )
