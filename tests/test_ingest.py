from pathlib import Path

from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat

import pytest
import pypdfium2 as pdfium

from pdf_translator.guardrails import InputGateError, PdfPreflight, _enforce_text_layer, ingest_pdf_guarded
from pdf_translator.ingest import build_pdf_converter
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
        profile_name="newspaper",
        page_count=24,
        file_size_bytes=1,
        warn_page_count=96,
        max_page_count=160,
        warn_file_size_mb=35.0,
        max_file_size_mb=80.0,
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
        profile_name="newspaper",
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
            profile_name="newspaper",
            timeout_seconds=0,
            max_page_count=2,
            soft_input_gate=False,
            soft_page_limit=2,
        )
