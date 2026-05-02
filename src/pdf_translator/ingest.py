from __future__ import annotations

from pathlib import Path
from typing import Any

from langdetect import LangDetectException, detect

from pdf_translator.models import NormalizedDocument
from pdf_translator.reconstruct import reconstruct_markdown


def build_pdf_converter(
    *,
    enable_table_structure: bool = False,
    generate_picture_images: bool = False,
) -> Any:
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_options = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=enable_table_structure,
        force_backend_text=True,
        generate_picture_images=generate_picture_images,
        images_scale=2.0 if generate_picture_images else 1.0,
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


def _export_book_markdown(document: Any, images_dir: Path) -> str:
    from docling_core.types.doc import ImageRefMode

    images_dir.mkdir(parents=True, exist_ok=True)
    # Save images to disk and get a document copy with absolute URI references.
    doc_with_refs = document._with_pictures_refs(
        image_dir=images_dir,
        page_no=None,
        reference_path=None,  # None → absolute paths in the markdown
    )
    return doc_with_refs.export_to_markdown(image_mode=ImageRefMode.REFERENCED)


def detect_language(text: str) -> str | None:
    sample = " ".join(text.split())[:4000]
    if not sample:
        return None
    try:
        return detect(sample)
    except LangDetectException:
        return None


def ingest_pdf(
    source_pdf: Path,
    *,
    output_dir: Path | None = None,
    profile: str = "auto",
) -> NormalizedDocument:
    is_book = profile == "book"
    converter = build_pdf_converter(
        enable_table_structure=is_book,
        generate_picture_images=is_book and output_dir is not None,
    )
    result = converter.convert(source_pdf)
    document = result.document

    # structured dict is always needed (profile analysis, artifact storage)
    _, structured = _export_docling_document(document)

    images_dir: Path | None = None
    if is_book and output_dir is not None:
        images_dir = output_dir / "images"
        # Docling handles reading order, tables, and image refs natively for books.
        raw_markdown = _export_book_markdown(document, images_dir)
        reconstructed_markdown = raw_markdown
    else:
        raw_markdown = document.export_to_markdown()
        reconstructed_markdown = reconstruct_markdown(structured, raw_markdown)

    return NormalizedDocument(
        source_pdf=source_pdf,
        raw_markdown=raw_markdown,
        reconstructed_markdown=reconstructed_markdown,
        structured=structured,
        detected_language=detect_language(reconstructed_markdown),
        images_dir=images_dir,
    )
