from __future__ import annotations

from pathlib import Path
from typing import Any

from langdetect import LangDetectException, detect

from pdf_translator.models import NormalizedDocument
from pdf_translator.reconstruct import reconstruct_markdown


def build_pdf_converter() -> Any:
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_options = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=False,
        force_backend_text=True,
    )
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options,
                backend=PyPdfiumDocumentBackend,
            )
        }
    )


def _export_docling_document(document: Any) -> tuple[str, dict[str, Any]]:
    raw_markdown = document.export_to_markdown()

    if hasattr(document, "export_to_dict"):
        structured = document.export_to_dict()
    elif hasattr(document, "model_dump"):
        structured = document.model_dump()
    else:
        structured = {"raw": str(document)}

    return raw_markdown, structured


def detect_language(text: str) -> str | None:
    sample = " ".join(text.split())[:4000]
    if not sample:
        return None
    try:
        return detect(sample)
    except LangDetectException:
        return None


def ingest_pdf(source_pdf: Path) -> NormalizedDocument:
    converter = build_pdf_converter()
    result = converter.convert(source_pdf)
    raw_markdown, structured = _export_docling_document(result.document)
    reconstructed_markdown = reconstruct_markdown(structured, raw_markdown)

    return NormalizedDocument(
        source_pdf=source_pdf,
        raw_markdown=raw_markdown,
        reconstructed_markdown=reconstructed_markdown,
        structured=structured,
        detected_language=detect_language(reconstructed_markdown),
    )
