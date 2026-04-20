from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat

from pdf_translator.ingest import build_pdf_converter


def test_build_pdf_converter_uses_fast_native_pdf_settings() -> None:
    converter = build_pdf_converter()
    pdf_option = converter.format_to_options[InputFormat.PDF]

    assert pdf_option.backend is PyPdfiumDocumentBackend
    assert pdf_option.pipeline_options.do_ocr is False
    assert pdf_option.pipeline_options.do_table_structure is False
    assert pdf_option.pipeline_options.force_backend_text is True
