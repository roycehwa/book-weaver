from __future__ import annotations

import re
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


def _split_markdown_blocks(markdown_text: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", markdown_text) if block.strip()]


def _normalize_pdf_text_block(block: str) -> str:
    block = block.replace("\u00ad", "")
    block = re.sub(r"(?<=[A-Za-z])[\x00-\x08\x0b-\x1f\x7f]\s*(?=[A-Za-z])", "", block)
    block = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", block)
    block = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", block)
    block = re.sub(r"(?<=[a-z.,;:])(?=\d{1,3}\s+[A-Z])", " ", block)
    return re.sub(r"[ \t]+", " ", block).strip()


def _normalize_footnote_reference_spacing(text: str) -> str:
    return re.sub(r"(?<=[A-Za-z).,;:])(?=\d{1,3}(?:\s+[A-Z]|$))", " ", text)


def _is_page_number_block(block: str) -> bool:
    text = " ".join(block.split())
    if re.fullmatch(r"(?:page\s*)?\d{1,4}", text, re.IGNORECASE):
        return True
    if re.fullmatch(r"[ivxlcdm]{1,8}", text, re.IGNORECASE):
        return True
    return False


def _is_structural_markdown_block(block: str) -> bool:
    stripped = block.lstrip()
    if stripped.startswith(("![", "|", "-", "*", ">", "```")):
        return True
    if re.match(r"\d+[.)]\s+", stripped):
        return True
    return False


def _is_markdown_heading_block(block: str) -> bool:
    return bool(re.match(r"#{1,6}\s+\S", block.lstrip()))


def _is_image_block(block: str) -> bool:
    return bool(re.fullmatch(r"!\[[^\]]*]\(.+\)", block.strip(), re.DOTALL))


def _is_table_block(block: str) -> bool:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    return bool(lines and all(line.startswith("|") and line.endswith("|") for line in lines))


def _is_list_block(block: str) -> bool:
    stripped = block.lstrip()
    return bool(re.match(r"(?:[-*+]\s+|\d+[.)]\s+)", stripped))


def _is_footnote_block(block: str) -> bool:
    stripped = block.lstrip()
    if _is_markdown_heading_block(stripped) or _is_image_block(stripped) or _is_table_block(stripped):
        return False
    if stripped.startswith((">", "```")) or _is_list_block(stripped):
        return False
    return bool(re.match(r"\d{1,3}\s+\S.{20,}", stripped, re.DOTALL))


def _format_footnote_block(block: str) -> str:
    if block.lstrip().startswith(">"):
        return block
    lines = block.splitlines() or [block]
    return "\n".join(f"> {line}" if line.strip() else ">" for line in lines)


def _split_footnote_block(block: str) -> list[str]:
    normalized = " ".join(block.split())
    matches = list(re.finditer(r"(?<!\S)\d{1,3}\s+(?=[A-Z])", normalized))
    if len(matches) <= 1:
        return [_format_footnote_block(normalized)]

    parts: list[str] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        note = normalized[start:end].strip()
        if note:
            parts.append(_format_footnote_block(note))
    return parts or [_format_footnote_block(normalized)]


def _is_plain_reflow_text_block(block: str) -> bool:
    stripped = block.lstrip()
    if not stripped:
        return False
    if _is_markdown_heading_block(block) or _is_image_block(block) or _is_table_block(block):
        return False
    if _is_footnote_block(block) or _is_list_block(block) or stripped.startswith((">", "```")):
        return False
    return True


def _is_repeatable_running_text(block: str) -> bool:
    if _is_structural_markdown_block(block):
        return False
    normalized = " ".join(block.split())
    if not normalized or len(normalized) > 90:
        return False
    if normalized.startswith("#"):
        normalized = normalized.lstrip("#").strip()
    words = normalized.split()
    if len(words) > 12:
        return False
    return any(char.isalpha() for char in normalized)


def _normalize_running_text(block: str) -> str:
    normalized = " ".join(block.split())
    normalized = normalized.lstrip("#").strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.casefold()


def _ends_like_complete_sentence(block: str) -> bool:
    text = block.rstrip()
    return bool(text and re.search(r"""[.!?。！？;；:：)"'\]”’]$""", text))


def _starts_like_new_paragraph(block: str) -> bool:
    text = block.lstrip()
    if not text:
        return False
    if re.match(r"(?:chapter|part|section|appendix)\b", text, re.IGNORECASE):
        return True
    return bool(re.match(r"[A-Z][A-Z0-9 ,:'\"()\-]{8,}$", text))


def _should_merge_plain_text(previous: str, current: str) -> bool:
    if not (_is_plain_reflow_text_block(previous) and _is_plain_reflow_text_block(current)):
        return False
    if _starts_like_new_paragraph(current):
        return False
    if _ends_like_complete_sentence(previous):
        return False
    return True


def _reflow_book_blocks(blocks: list[str]) -> list[str]:
    reflowed: list[str] = []
    for block in blocks:
        normalized = _normalize_pdf_text_block(block)
        if not normalized:
            continue
        if reflowed and _should_merge_plain_text(reflowed[-1], normalized):
            reflowed[-1] = f"{reflowed[-1]} {normalized}"
        else:
            reflowed.append(normalized)
    return reflowed


def clean_book_reflow_markdown(markdown_text: str) -> str:
    blocks = [
        block
        for block in (_normalize_pdf_text_block(block) for block in _split_markdown_blocks(markdown_text))
        if block and not _is_page_number_block(block)
    ]
    running_counts: dict[str, int] = {}
    for block in blocks:
        if _is_repeatable_running_text(block):
            key = _normalize_running_text(block)
            running_counts[key] = running_counts.get(key, 0) + 1

    decontaminated: list[str] = []
    seen_running_text: set[str] = set()
    for block in blocks:
        if _is_repeatable_running_text(block):
            key = _normalize_running_text(block)
            if running_counts.get(key, 0) >= 3:
                if not _is_markdown_heading_block(block):
                    continue
                if key in seen_running_text:
                    continue
                seen_running_text.add(key)
        if _is_footnote_block(block):
            decontaminated.extend(_split_footnote_block(block))
            continue
        decontaminated.append(block)

    cleaned = _reflow_book_blocks(decontaminated)
    return _normalize_footnote_reference_spacing("\n\n".join(cleaned).strip()) + "\n"


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
    is_book = profile in {"auto", "book", "magazine"}
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
        reconstructed_markdown = clean_book_reflow_markdown(raw_markdown)
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
