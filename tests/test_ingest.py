from pathlib import Path

from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat

import pytest

from pdf_translator.guardrails import InputGateError, PdfPreflight, _enforce_text_layer
from pdf_translator.ingest import build_pdf_converter
from pdf_translator.models import NormalizedDocument


def test_build_pdf_converter_uses_fast_native_pdf_settings() -> None:
    converter = build_pdf_converter()
    pdf_option = converter.format_to_options[InputFormat.PDF]

    assert pdf_option.backend is PyPdfiumDocumentBackend
    assert pdf_option.pipeline_options.do_ocr is False
    assert pdf_option.pipeline_options.do_table_structure is False
    assert pdf_option.pipeline_options.force_backend_text is True


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
