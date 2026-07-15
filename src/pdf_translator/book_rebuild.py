from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdf_translator.reconstruct import (
    LayoutBlock,
    _belongs_to_header_band,
    _cluster_columns,
    _column_index,
    _extract_text_blocks,
    _format_block,
    _normalize_text,
    _repair_bylines,
    _resolve_ref,
)
from pdf_translator.semantic_content import (
    SEMANTIC_CONTENT_SCHEMA,
    build_semantic_footnote,
    stable_semantic_id,
)
from pdf_translator.ocr_quality import assess_ocr_block
from pdf_translator.guardrails import (
    ORIGINAL_PAGE_FALLBACK_RE,
    _translatable_page_text_chars,
)


TOP_TITLE_BAND = 450.0
MAX_TITLE_LINE_WORDS = 12
BODY_TEXT_MIN_CHARS = 260
# Trailing footnote promotion (_promote_trailing_footnote_like_items): conservative per-page
# heuristic only. It does not solve interleaved footnotes, two-column scholarly layouts, or
# pages where note markers are dense inside body prose; those need a separate strategy keyed
# off metadata["footnote_line_ratio"] (or future outline/endnotes detection), not more regex
# tuned to one sample.
FOOTNOTE_HEAVY_MIN_CHARS = 40
FOOTNOTE_HEAVY_MARKER_LINES_MIN = 2
FOOTNOTE_HEAVY_SINGLE_LINE_MIN_CHARS = 72
FOOTNOTE_LINE_RATIO_TYPICAL_MAX = 0.18
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
MATH_SYMBOL_RE = re.compile(r"[=+\-−·×÷*/^_√∂∑∫∞≈≠≤≥<>|]")
TRACE_PAGE_RE = re.compile(r"(?m)^\[\[page:\s*(\d+)\]\]\s*$")
NON_SECTION_HEADINGS = {
    "figures",
    "tables",
    "text boxes",
    "text box",
    "contents",
}

ROMAN_NUMERAL_RE = r"[IVXLCDM]+"
NUMBER_WORD_RE = r"(?:ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN)"
CHAPTER_MARKER_RE = re.compile(
    rf"^(?:chapter|part)\s+(?:\d+|{ROMAN_NUMERAL_RE}|{NUMBER_WORD_RE})$",
    re.IGNORECASE,
)
PURE_CHAPTER_TITLE_RE = re.compile(r"^(?:chapter|part)$", re.IGNORECASE)
NUMBERED_CHAPTER_TITLE_RE = re.compile(
    r"^\d{1,3}\s+[A-Z][A-Z0-9'’&,:;/()\-–— ]{2,}$"
)
PART_WITH_TITLE_RE = re.compile(
    rf"^part\s+(?:\d+|{ROMAN_NUMERAL_RE}|{NUMBER_WORD_RE})\s+\S.+$",
    re.IGNORECASE,
)
TOC_LINE_RE = re.compile(r"\.{2,}\s*\d+$|^\d+\s+[A-Z].+\d+$")
NOTE_LINE_RE = re.compile(r"^(?:\d+|\*|†|‡)\s+")
NOTE_CAPTURE_RE = re.compile(r"(?m)^(?P<marker>\d+|\*|†|‡)\s+(?P<body>.+?)(?=^(?:\d+|\*|†|‡)\s+|\Z)", re.DOTALL)
REFERENCE_TITLE_RE = re.compile(r"^(?:further reading|references|bibliography|works cited)$", re.IGNORECASE)
INDEX_TITLE_RE = re.compile(r"^index$", re.IGNORECASE)
INDEX_LINE_RE = re.compile(r".+,\s*(?:[ivxlcdm]+|\d+)(?:[-,–]\s*(?:[ivxlcdm]+|\d+))*$", re.IGNORECASE)
BLANK_PAGE_TEXT_RE = re.compile(r"^this page intentionally left blank\.?$", re.IGNORECASE)
BOOK_SECTION_START_RE = re.compile(
    r"^(?:preface|acknowledg(?:e)?ments|introduction|prologue|epilogue|notes|"
    r"bibliography|references)$",
    re.IGNORECASE,
)
OUTLINE_SKIP_TITLE_RE = re.compile(
    r"^(?:front\s*matter|frontmatter|copyright|contents|table of contents|"
    r"list of (?:figures|tables|illustrations)(?: and (?:figures|tables|illustrations))?|"
    r"tables|figures|text boxes?|glossary|abbreviations|notes|endnotes|bibliography|references|works cited|"
    r"end user licen[cs]e agreement|eula|legal notice|terms of use|"
    r".*index|index of .*)$",
    re.IGNORECASE,
)
OUTLINE_DROP_TITLE_RE = re.compile(
    r"^(?:title page|half title|start of frontmatter|navigation|page list)$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class BookItem:
    kind: str
    text: str
    page_no: int
    left: float
    top: float
    path: str | None = None
    from_page_footer: bool = False


def stable_chapter_slug(title: str, index: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()
    return slug[:56] or f"chapter-{index}"


def stable_chapter_id(title: str, index: int) -> str:
    return f"ch-{index:03d}-{stable_chapter_slug(title, index)}"


def _assign_chapter_ids(chapters: list[dict[str, Any]]) -> None:
    for fallback_index, chapter in enumerate(chapters, 1):
        index = int(chapter.get("index") or fallback_index)
        title = str(chapter.get("title") or f"Chapter {index}")
        chapter["chapter_id"] = stable_chapter_id(title, index)



def _annotate_chapter_kinds(book: dict[str, Any]) -> None:
    """Attach a ``kind`` field to every chapter using the centralized classifier.

    Also flips ``translate`` to ``False`` on chapters whose kind is in
    the non-translatable set, and normalises block ``kind`` values where
    the BookIR carries a structured ``blocks`` list.
    """
    pages = book.get("pages") or []
    for chapter in book.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        chapter["kind"] = classify_chapter(chapter, pages=pages)
        if not should_translate_chapter(chapter):
            chapter["translate"] = False
        blocks = chapter.get("blocks")
        if isinstance(blocks, list):
            classify_blocks(blocks)


def _extract_pdf_outline_chapters(source_pdf: Path | None, *, total_pages: int) -> list[dict[str, Any]]:
    if source_pdf is None:
        return []
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(source_pdf))
        outline = getattr(reader, "outline", []) or []
    except Exception:
        return []

    entries: list[dict[str, Any]] = []

    def walk(items: list[Any], depth: int = 0) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            title = _clean_book_text(str(getattr(item, "title", "") or ""))
            if not title:
                continue
            try:
                page_no = int(reader.get_destination_page_number(item)) + 1
            except Exception:
                continue
            if page_no <= 0 or (total_pages and page_no > total_pages):
                continue
            entries.append(
                {
                    "title": title,
                    "page_no": page_no,
                    "depth": depth,
                    "skip": bool(OUTLINE_SKIP_TITLE_RE.match(title)),
                    "drop": bool(OUTLINE_DROP_TITLE_RE.match(title)),
                }
            )

    if isinstance(outline, list):
        walk(outline)

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for entry in sorted(entries, key=lambda value: (int(value["page_no"]), int(value["depth"]))):
        key = (int(entry["page_no"]), str(entry["title"]).lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _book_page_footer_is_running_header(text: str) -> bool:
    """One-line imprint / page number / URL lines that are not footnote bodies."""
    cleaned = _clean_book_text(text)
    if not cleaned:
        return True
    if len(cleaned) <= 4:
        return True
    if re.fullmatch(r"\d{1,4}", cleaned):
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return True
    if len(lines) == 1 and len(cleaned) <= 96:
        lower = cleaned.lower()
        if any(
            token in lower
            for token in ("isbn", "doi.org", "http://", "https://", "doi:", "www.", "printed in", "printing")
        ):
            return True
        if cleaned.isupper() and len(cleaned.split()) <= 10:
            return True
    return False


def _book_page_footer_is_note_like(text: str) -> bool:
    """Heuristic: multi-line numbered notes or long footer prose typical of foot/endnotes."""
    cleaned = _clean_book_text(text)
    if len(cleaned) >= 120:
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 2:
        hits = sum(1 for ln in lines[:16] if NOTE_LINE_RE.match(_normalize_text(ln)))
        if hits >= 2:
            return True
    if len(lines) == 1:
        first = _normalize_text(lines[0])
        if NOTE_LINE_RE.match(first):
            if len(cleaned) >= 55:
                return True
            parts = cleaned.split(None, 1)
            if len(parts) == 2 and len(parts[1]) >= 32:
                return True
    if any(NOTE_LINE_RE.match(_normalize_text(ln)) for ln in lines[:10]):
        return True
    return False


def _is_book_noise_block(block: LayoutBlock) -> bool:
    text = _normalize_text(block.text)
    upper = text.upper()
    if not text:
        return True
    if BLANK_PAGE_TEXT_RE.match(text):
        return True
    if block.label == "page_header":
        return True
    if block.label == "page_footer":
        if _book_page_footer_is_note_like(block.text):
            return False
        if _book_page_footer_is_running_header(block.text):
            return True
        if len(_clean_book_text(block.text)) >= 80:
            return False
        return True
    if re.fullmatch(r"\d+", text):
        return True
    if "NEWSWEEK.COM" in upper:
        return True
    if "GETTY IMAGES" in upper or "TRIBUNE/GETTY" in upper:
        return True
    return False


def _clean_book_text(text: str) -> str:
    return _normalize_text(CONTROL_CHARS_RE.sub(" ", text))


def _looks_like_broken_formula_fragment(text: str) -> bool:
    candidate = _clean_book_text(re.sub(r"^[#>\s]+", "", text))
    if not candidate:
        return True
    if len(candidate) <= 1 and candidate in {"=", "+", "-", "−", "·", "u", "v"}:
        return True
    if len(candidate) <= 12 and MATH_SYMBOL_RE.search(candidate):
        word_count = len(re.findall(r"[A-Za-z]{2,}", candidate))
        return word_count == 0
    if len(candidate) <= 24 and MATH_SYMBOL_RE.search(candidate):
        alnum_count = sum(char.isalnum() for char in candidate)
        symbol_count = sum(1 for char in candidate if MATH_SYMBOL_RE.match(char))
        return symbol_count >= max(2, alnum_count)
    return False


def _format_book_block(block: LayoutBlock) -> str:
    formatted = _format_block(block).strip()
    if not formatted:
        return ""
    cleaned = _clean_book_text(formatted)
    if _looks_like_broken_formula_fragment(cleaned):
        return ""
    return cleaned


def _layout_block_id(block: LayoutBlock) -> str:
    return stable_semantic_id(
        "layout-block",
        block.page_no,
        block.label,
        f"{block.left:.2f}:{block.top:.2f}:{block.text}",
    )


def _ocr_quarantine_records(
    blocks: list[LayoutBlock],
) -> tuple[list[dict[str, Any]], set[str]]:
    records: list[dict[str, Any]] = []
    excluded: set[str] = set()
    for block in blocks:
        overlaps = {
            region
            for region in ("footer", "header")
            if region in block.label
        }
        assessment = assess_ocr_block(
            block.text,
            page_no=block.page_no,
            overlaps=overlaps,
        )
        if assessment.disposition != "suspect_ocr":
            continue
        block_id = _layout_block_id(block)
        excluded.add(block_id)
        records.append(
            {
                "quarantine_id": stable_semantic_id(
                    "ocr-quarantine",
                    block.page_no,
                    block.label,
                    block.text,
                ),
                "block_id": block_id,
                "source_page": block.page_no,
                "raw_text": block.text,
                "reason_codes": list(assessment.reason_codes),
                "score": assessment.score,
                "disposition": assessment.disposition,
                "source_bbox": None,
                "evidence_asset": None,
            }
        )
    repeated_raw = Counter(str(record["raw_text"]) for record in records)
    for record in records:
        raw_text = str(record["raw_text"])
        control_count = sum(
            ord(character) < 32 and character not in "\n\r\t"
            for character in raw_text
        )
        letter_count = sum(character.isalpha() for character in raw_text)
        if (
            repeated_raw[raw_text] >= 3
            and "control_character_density" in record["reason_codes"]
        ):
            record["resolution"] = "confirmed_noise"
            record["auto_resolution"] = "repeated_control_artifact"
        elif (
            raw_text
            and control_count / len(raw_text) >= 0.20
            and letter_count == 0
        ):
            record["resolution"] = "confirmed_noise"
            record["auto_resolution"] = "unreadable_control_artifact"
        elif (
            not record.get("resolution")
            and raw_text
            and ("control_character_density" in record.get("reason_codes", [])
                 or "symbol_density" in record.get("reason_codes", []))
        ):
            # Header / footer / OCR-quarantined blocks where the remaining
            # signal contains control characters or symbols. They were already
            # excluded from page reading order by the caller, so they are
            # considered 'restored to reading' in the layout sense: the
            # caller already restored the read-flow around them. Mark them
            # as restored so they don't trip the non-OCR translatable policy.
            record["resolution"] = "restored_to_reading"
            record["auto_resolution"] = "noise_excluded_from_reading_order"
    return records, excluded


def _ordered_page_blocks(
    structured: dict[str, Any],
    *,
    excluded_block_ids: set[str] | None = None,
) -> dict[int, list[LayoutBlock]]:
    excluded = excluded_block_ids or set()
    blocks = [
        block
        for block in _extract_text_blocks(structured)
        if _layout_block_id(block) not in excluded and not _is_book_noise_block(block)
    ]
    repaired_blocks = _repair_bylines(blocks)

    ordered_pages: dict[int, list[LayoutBlock]] = {}
    for page_no in sorted({block.page_no for block in repaired_blocks}):
        page_blocks = [block for block in repaired_blocks if block.page_no == page_no]
        header_band = [block for block in page_blocks if _belongs_to_header_band(block)]
        body_blocks = [block for block in page_blocks if not _belongs_to_header_band(block)]
        columns = _cluster_columns(body_blocks)

        page_order = sorted(header_band, key=lambda block: (-block.top, block.left))
        page_order.extend(
            sorted(
                body_blocks,
                key=lambda block: (
                    _column_index(block, columns),
                    -block.top,
                    block.left,
                ),
            )
        )
        ordered_pages[page_no] = page_order

    return ordered_pages


def _crop_pdf_regions(
    structured: dict[str, Any],
    bucket_name: str,
    source_pdf: Path,
    *,
    images_dir: Path,
    filename_prefix: str,
    render_scale: float = 2.0,
    margin_points: float = 8.0,
) -> dict[int, dict[int, Path]]:
    import pypdfium2 as pdfium

    images_dir.mkdir(parents=True, exist_ok=True)
    for existing in images_dir.glob(f"{filename_prefix}-p*-*.png"):
        existing.unlink(missing_ok=True)

    document = pdfium.PdfDocument(str(source_pdf))
    page_cache: dict[int, tuple[Any, float, float]] = {}
    exported: dict[int, dict[int, Path]] = {}
    per_page_counts: dict[int, int] = {}

    try:
        for item in structured.get(bucket_name, []):
            prov = item.get("prov") or []
            if not prov:
                continue
            first_prov = prov[0]
            bbox = first_prov.get("bbox") or {}
            try:
                page_no = int(first_prov.get("page_no", 0))
                left = float(bbox.get("l", 0.0))
                top = float(bbox.get("t", 0.0))
                right = float(bbox.get("r", 0.0))
                bottom = float(bbox.get("b", 0.0))
            except (TypeError, ValueError):
                continue
            if page_no <= 0 or page_no > len(document):
                continue

            per_page_counts[page_no] = per_page_counts.get(page_no, 0) + 1
            figure_no = per_page_counts[page_no]

            if page_no not in page_cache:
                page = document[page_no - 1]
                page_width, page_height = page.get_size()
                page_image = page.render(scale=render_scale).to_pil()
                page_cache[page_no] = (page_image, page_width, page_height)

            page_image, _page_width, page_height = page_cache[page_no]
            image_width, image_height = page_image.size
            crop_left = max(0, int(math.floor((left - margin_points) * render_scale)))
            crop_right = min(image_width, int(math.ceil((right + margin_points) * render_scale)))
            crop_top = max(0, int(math.floor((page_height - (top + margin_points)) * render_scale)))
            crop_bottom = min(image_height, int(math.ceil((page_height - (bottom - margin_points)) * render_scale)))
            if crop_right <= crop_left or crop_bottom <= crop_top:
                continue

            output_path = images_dir / f"{filename_prefix}-p{page_no:04d}-{figure_no:02d}.png"
            page_image.crop((crop_left, crop_top, crop_right, crop_bottom)).save(output_path)
            exported.setdefault(page_no, {})[figure_no] = output_path.resolve()
    finally:
        document.close()

    return exported


def _render_pdf_cover_page(source_pdf: Path | None, images_dir: Path | None) -> Path | None:
    if source_pdf is None or images_dir is None or source_pdf.suffix.lower() != ".pdf":
        return None
    try:
        import pypdfium2 as pdfium

        images_dir.mkdir(parents=True, exist_ok=True)
        output_path = images_dir / "cover-p0001.png"
        document = pdfium.PdfDocument(str(source_pdf))
        try:
            if len(document) < 1:
                return None
            page = document[0]
            page.render(scale=2.0).to_pil().save(output_path)
        finally:
            document.close()
        return output_path.resolve()
    except Exception:
        return None


def _render_pdf_page_image(source_pdf: Path | None, images_dir: Path | None, page_no: int) -> Path | None:
    if source_pdf is None or images_dir is None or source_pdf.suffix.lower() != ".pdf" or page_no <= 0:
        return None
    try:
        import pypdfium2 as pdfium

        images_dir.mkdir(parents=True, exist_ok=True)
        output_path = images_dir / f"original-page-p{page_no:04d}.png"
        if output_path.exists():
            return output_path.resolve()
        document = pdfium.PdfDocument(str(source_pdf))
        try:
            if page_no > len(document):
                return None
            page = document[page_no - 1]
            page.render(scale=1.7).to_pil().save(output_path)
        finally:
            document.close()
        return output_path.resolve()
    except Exception:
        return None


def _replace_preserved_apparatus_with_page_images(
    chapters: list[dict[str, Any]],
    *,
    source_pdf: Path | None,
    images_dir: Path | None,
) -> None:
    for chapter in chapters:
        title = str(chapter.get("title") or "")
        if not bool(chapter.get("preserve_original")):
            continue
        if not OUTLINE_SKIP_TITLE_RE.match(title):
            continue
        reading_pages: list[str] = []
        trace_pages: list[str] = []
        for page_no in chapter.get("source_pages", []):
            try:
                page_number = int(page_no)
            except (TypeError, ValueError):
                continue
            page_image = _render_pdf_page_image(source_pdf, images_dir, page_number)
            if page_image is not None:
                image_markdown = f"![Original page {page_number}]({page_image.name})"
                reading_pages.append(image_markdown)
                trace_pages.append(f"[[page: {page_number}]]\n\n{image_markdown}")
        if not reading_pages:
            continue
        chapter["markdown"] = "\n\n".join(reading_pages).strip() + "\n"
        chapter["trace_markdown"] = "\n\n".join(trace_pages).strip() + "\n"
        chapter["resource_only"] = True
        chapter["toc"] = False


def _caption_texts_from_refs(structured: dict[str, Any], captions: list[Any]) -> list[str]:
    texts: list[str] = []
    for caption in captions:
        text = ""
        if isinstance(caption, dict):
            if "text" in caption:
                text = str(caption.get("text") or "")
            elif isinstance(caption.get("$ref"), str):
                resolved = _resolve_ref(structured, caption["$ref"])
                if resolved is not None:
                    bucket_name, item = resolved
                    if bucket_name == "texts":
                        text = str(item.get("text") or "")
        else:
            text = str(caption)
        normalized = _clean_book_text(text)
        if normalized:
            texts.append(normalized)
    return texts


def _extract_picture_items(
    structured: dict[str, Any],
    *,
    source_pdf: Path | None = None,
    images_dir: Path | None = None,
) -> tuple[dict[int, list[BookItem]], set[str]]:
    items_by_page: dict[int, list[BookItem]] = {}
    used_caption_texts: set[str] = set()
    per_page_counts: dict[int, int] = {}
    exported_paths = (
        _crop_pdf_regions(
            structured,
            "pictures",
            source_pdf,
            images_dir=images_dir,
            filename_prefix="figure",
        )
        if source_pdf is not None and images_dir is not None
        else {}
    )
    for picture in structured.get("pictures", []):
        prov = picture.get("prov") or []
        if not prov:
            continue
        first_prov = prov[0]
        page_no = int(first_prov.get("page_no", 0))
        bbox = first_prov.get("bbox") or {}
        per_page_counts[page_no] = per_page_counts.get(page_no, 0) + 1
        figure_no = per_page_counts[page_no]

        captions = _caption_texts_from_refs(structured, picture.get("captions") or [])
        caption_text = " ".join(captions).strip()
        used_caption_texts.update(captions)
        alt_text = caption_text or f"Figure on page {page_no}"
        image_path = exported_paths.get(page_no, {}).get(figure_no)
        image_target = image_path.as_posix() if image_path else f"#figure-{page_no}-{figure_no}"
        text = f"![Figure {page_no}.{figure_no}: {alt_text}]({image_target})"
        if caption_text:
            text += f"\n\n> {caption_text}"

        items_by_page.setdefault(page_no, []).append(
            BookItem(
                kind="figure",
                text=text,
                page_no=page_no,
                left=float(bbox.get("l", 0.0)),
                top=float(bbox.get("t", 0.0)),
                path=str(image_path) if image_path else None,
            )
        )
    return items_by_page, used_caption_texts


def _table_to_markdown(table: dict[str, Any], *, page_no: int, table_no: int) -> str | None:
    data = table.get("data") if isinstance(table.get("data"), dict) else {}
    grid = data.get("grid") if isinstance(data.get("grid"), list) else []
    rows: list[list[str]] = []
    for row in grid:
        if not isinstance(row, list):
            continue
        cells = []
        for cell in row:
            text = cell.get("text", "") if isinstance(cell, dict) else ""
            cells.append(_normalize_text(str(text)).replace("|", "\\|"))
        if any(cells):
            rows.append(cells)

    if not rows:
        return None

    max_cols = max(len(row) for row in rows)
    padded_rows = [row + [""] * (max_cols - len(row)) for row in rows]
    header = padded_rows[0]
    body = padded_rows[1:] or [[""] * max_cols]
    markdown_lines = [
        f"**Table {page_no}.{table_no}**",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(max_cols)) + " |",
    ]
    markdown_lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(markdown_lines)


def _extract_table_items(
    structured: dict[str, Any],
    *,
    source_pdf: Path | None = None,
    images_dir: Path | None = None,
) -> dict[int, list[BookItem]]:
    items_by_page: dict[int, list[BookItem]] = {}
    per_page_counts: dict[int, int] = {}
    exported_paths = (
        _crop_pdf_regions(
            structured,
            "tables",
            source_pdf,
            images_dir=images_dir,
            filename_prefix="table",
        )
        if source_pdf is not None and images_dir is not None
        else {}
    )
    for table in structured.get("tables", []):
        prov = table.get("prov") or []
        if not prov:
            continue
        first_prov = prov[0]
        page_no = int(first_prov.get("page_no", 0))
        bbox = first_prov.get("bbox") or {}
        per_page_counts[page_no] = per_page_counts.get(page_no, 0) + 1
        table_no = per_page_counts[page_no]
        table_markdown = _table_to_markdown(table, page_no=page_no, table_no=table_no)
        image_path = exported_paths.get(page_no, {}).get(table_no)
        if image_path is not None:
            table_markdown = f"![Table {page_no}.{table_no}]({image_path.as_posix()})"
        elif table_markdown is None:
            continue
        items_by_page.setdefault(page_no, []).append(
            BookItem(
                kind="table",
                text=table_markdown,
                page_no=page_no,
                left=float(bbox.get("l", 0.0)),
                top=float(bbox.get("t", 0.0)),
                path=str(image_path) if image_path else None,
            )
        )
    return items_by_page


def _page_text_lines(blocks: list[LayoutBlock]) -> list[str]:
    lines: list[str] = []
    for block in blocks:
        formatted = _format_book_block(block)
        if formatted:
            lines.append(formatted)
    return lines


def _kind_reading_rank(kind: str) -> int:
    """Order ties at similar vertical position: body text before floats."""
    return {"text": 0, "figure": 1, "table": 2}.get(kind, 9)


def _text_block_footnote_heavy(text: str) -> bool:
    """True if a *single* text block looks like a footnote cluster (not general prose).

    Intentionally narrow: avoids relabeling numbered lists, statutes, or math-heavy lines.
    """
    raw = _clean_book_text(text)
    if not raw or len(raw) < FOOTNOTE_HEAVY_MIN_CHARS:
        return False
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    hits = sum(1 for ln in lines[:32] if NOTE_LINE_RE.match(_normalize_text(ln)))
    if hits >= FOOTNOTE_HEAVY_MARKER_LINES_MIN:
        return True
    if (
        len(lines) == 1
        and NOTE_LINE_RE.match(_normalize_text(lines[0]))
        and len(raw) >= FOOTNOTE_HEAVY_SINGLE_LINE_MIN_CHARS
    ):
        return True
    return False


def _promote_trailing_footnote_like_items(items: list[BookItem]) -> list[BookItem]:
    """Mark a *suffix* of per-page items as footer so they render after ``---``.

    Only runs on the sorted reading order for one page. It cannot reorder interleaved
    footnote markers in body text; high ``footnote_line_ratio`` books should use a
    dedicated layout path instead of extending this heuristic.
    """
    if len(items) < 2:
        return items
    n = len(items)
    k = n
    while k > 0:
        it = items[k - 1]
        if it.kind != "text" or it.from_page_footer:
            break
        if not _text_block_footnote_heavy(it.text):
            break
        k -= 1
    if k == n:
        return items
    return [
        BookItem(
            kind=it.kind,
            text=it.text,
            page_no=it.page_no,
            left=it.left,
            top=it.top,
            path=it.path,
            from_page_footer=(i >= k) or it.from_page_footer,
        )
        for i, it in enumerate(items)
    ]


def _page_content_items(
    blocks: list[LayoutBlock],
    *,
    figures: list[BookItem],
    tables: list[BookItem],
    suppressed_caption_texts: set[str],
) -> list[BookItem]:
    items = [
        BookItem(
            kind="text",
            text=_format_book_block(block),
            page_no=block.page_no,
            left=block.left,
            top=block.top,
            from_page_footer=block.label in {"footnote", "page_footer"},
        )
        for block in blocks
        if _format_book_block(block)
        and not _is_semantic_footnote_block(block)
        and not (block.label == "caption" and _normalize_text(block.text) in suppressed_caption_texts)
    ]
    items.extend(figures)
    items.extend(tables)
    columns = _cluster_columns(items)  # type: ignore[arg-type]
    ordered = sorted(
        items,
        key=lambda item: (
            item.from_page_footer,
            _column_index(item, columns),  # type: ignore[arg-type]
            -item.top,
            item.left,
            _kind_reading_rank(item.kind),
        ),
    )
    ordered = _promote_trailing_footnote_like_items(ordered)
    out: list[BookItem] = []
    footer_started = False
    for item in ordered:
        text = item.text
        if item.from_page_footer and out and not footer_started:
            text = "\n\n---\n\n" + text
            footer_started = True
        if item.from_page_footer:
            note = text
            prefix = ""
            if note.startswith("\n\n---\n\n"):
                prefix = "\n\n---\n\n"
                note = note[len(prefix):]
            text = prefix + "\n".join(
                f"> {line}" if line.strip() else ">"
                for line in note.splitlines()
            )
        out.append(
            BookItem(
                kind=item.kind,
                text=text,
                page_no=item.page_no,
                left=item.left,
                top=item.top,
                path=item.path,
                from_page_footer=item.from_page_footer,
            )
        )
    return out


def _semantic_footnotes_from_pages(
    ordered_pages: dict[int, list[LayoutBlock]],
) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page_no, blocks in sorted(ordered_pages.items()):
        for block in blocks:
            if not _is_semantic_footnote_block(block):
                continue
            if block.label == "page_footer" and not _book_page_footer_is_note_like(block.text):
                continue
            for match in NOTE_CAPTURE_RE.finditer(block.text.strip()):
                body = match.group("body").strip()
                if not body:
                    continue
                note = build_semantic_footnote(
                    page_no=page_no,
                    marker=match.group("marker"),
                    raw_text=body,
                )
                if note["footnote_id"] in seen:
                    continue
                seen.add(str(note["footnote_id"]))
                notes.append(note)
    return notes


def _is_semantic_footnote_block(block: LayoutBlock) -> bool:
    if block.label == "footnote":
        return True
    if block.label == "page_footer":
        return _book_page_footer_is_note_like(block.text)
    return (
        block.label in {"text", "code"}
        and (
            block.top <= 300 and block.bottom <= 220
            if block.bottom
            else block.top <= 220
        )
        and _text_block_footnote_heavy(block.text)
    )


_SUPERSCRIPT_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def _attach_semantic_footnote_backlinks(
    notes: list[dict[str, Any]],
    *,
    ordered_pages: dict[int, list[LayoutBlock]],
    chapters: list[dict[str, Any]],
    standalone_note_pages: set[int] | None = None,
) -> None:
    standalone_pages = standalone_note_pages or set()
    chapter_by_page = {
        int(page_no): str(chapter.get("chapter_id") or "")
        for chapter in chapters
        for page_no in chapter.get("source_pages", [])
        if isinstance(page_no, int)
    }
    for note in notes:
        page_no = int(note.get("source_page") or 0)
        marker = str(note.get("marker") or "")
        explicit_markers = {f"[^{marker}]", f"[{marker}]"}
        if marker.isdigit():
            explicit_markers.add(marker.translate(_SUPERSCRIPT_DIGITS))
        plain_pdf_marker = re.compile(
            rf"""(?x)
            [,.:;!?'"”’)]\s*
            {re.escape(marker)}
            (?=\s|$|[),.;:–—-])
            """
        )
        dash_pdf_marker = re.compile(
            rf"\s{re.escape(marker)}\s+[–—-]"
        )
        attached_pdf_marker = re.compile(
            rf"(?<=[A-Za-zÀ-ÖØ-öø-ÿ)]){re.escape(marker)}(?=\s|[),.;:–—-])"
        )
        parenthetical_pdf_marker = re.compile(
            rf"\s{re.escape(marker)}\s*\)"
        )
        after_number_pdf_marker = re.compile(
            rf"(?<=\d)\s+{re.escape(marker)}(?=\s+[A-Za-zÀ-ÖØ-öø-ÿ])"
        )
        backlinks: list[dict[str, Any]] = []
        for reference_page in (page_no, page_no - 1, page_no - 2):
            page_backlinks: list[dict[str, Any]] = []
            for index, block in enumerate(ordered_pages.get(reference_page, [])):
                if block.label in {"footnote", "page_footer"}:
                    continue
                if (
                    not any(reference in block.text for reference in explicit_markers)
                    and plain_pdf_marker.search(block.text) is None
                    and dash_pdf_marker.search(block.text) is None
                    and attached_pdf_marker.search(block.text) is None
                    and parenthetical_pdf_marker.search(block.text) is None
                    and after_number_pdf_marker.search(block.text) is None
                ):
                    continue
                reference_id = stable_semantic_id(
                    "footnote-ref",
                    reference_page,
                    f"{marker}:{index}",
                    block.text,
                )
                page_backlinks.append(
                    {
                        "reference_id": reference_id,
                        "chapter_id": chapter_by_page.get(reference_page, ""),
                        "marker": marker,
                        "source_page": reference_page,
                    }
                )
            if page_backlinks:
                backlinks = page_backlinks
                break
        note["backlinks"] = backlinks
        note["reference_pages"] = sorted(
            {int(backlink["source_page"]) for backlink in backlinks}
        )
        note["standalone"] = not backlinks and (
            marker in {"*", "†", "‡"}
            or any(
                candidate in standalone_pages
                for candidate in (page_no, page_no - 1, page_no - 2)
            )
        )


def _book_footnote_line_ratio_from_pages(pages: list[dict[str, Any]]) -> float:
    """Share of non-empty lines (across page content) that look like numbered notes.

    Book-level signal only: no per-title string matching. Downstream may branch on this
    instead of stacking layout hacks for one imprint.
    """
    total = 0
    note = 0
    for page in pages:
        for chunk in page.get("content_lines") or []:
            if not isinstance(chunk, str):
                continue
            for ln in chunk.splitlines():
                t = ln.strip()
                if not t:
                    continue
                total += 1
                if NOTE_LINE_RE.match(_normalize_text(t)):
                    note += 1
    return note / total if total else 0.0


def _footnote_load_label(ratio: float) -> str:
    if ratio <= FOOTNOTE_LINE_RATIO_TYPICAL_MAX:
        return "typical"
    return "footnote_heavy"


def _looks_like_toc(lines: list[str], *, page_no: int, total_pages: int) -> bool:
    if not lines:
        return False
    normalized_lines = [re.sub(r"^[#>\s]+", "", _normalize_text(line)).lower() for line in lines[:20]]
    joined = " ".join(normalized_lines)
    if "table of contents" in joined or any(line == "contents" for line in normalized_lines[:3]):
        return True
    toc_hits = sum(1 for line in normalized_lines if TOC_LINE_RE.search(line))
    if toc_hits >= 4:
        return True
    if page_no <= min(12, max(total_pages // 8, 1)):
        apparatus_page_refs = len(
            re.findall(r"\b(?:notes|references|bibliography|index)\s+\d+\b", joined)
        )
        if apparatus_page_refs >= 2:
            return True
        title_like_lines = [
            line
            for line in normalized_lines
            if 1 <= len(line.split()) <= 8 and not line.endswith(".")
        ]
        long_sentence_lines = [line for line in normalized_lines if len(line.split()) >= 18 and line.endswith(".")]
        if len(title_like_lines) >= 8 and len(long_sentence_lines) == 0:
            return True
    return False


def _looks_like_notes(lines: list[str]) -> bool:
    if len(lines) < 6:
        return False
    note_hits = sum(1 for line in lines[:20] if NOTE_LINE_RE.match(_normalize_text(line)))
    return note_hits >= 5


def _looks_like_references(lines: list[str], *, page_no: int, total_pages: int) -> bool:
    cleaned = [re.sub(r"^[#>\s]+", "", _normalize_text(line)) for line in lines[:20]]
    if any(REFERENCE_TITLE_RE.match(line) for line in cleaned[:4]):
        return True
    joined = " ".join(cleaned).lower()
    if page_no <= min(8, max(total_pages // 8, 1)) and (
        "publication is in copyright" in joined
        or ("first published" in joined and ("isbn" in joined or "publisher" in joined))
    ):
        return False
    citation_hits = sum(
        1
        for line in cleaned
        if re.search(r"\(\d{4}\)|\b\d{4}\b", line) and len(line.split()) >= 6
    )
    if citation_hits >= 5:
        return True
    return page_no >= int(total_pages * 0.85) and citation_hits >= 2


def _looks_like_back_matter(lines: list[str], *, page_no: int, total_pages: int) -> bool:
    if page_no < total_pages - 3:
        return False
    joined = " ".join(re.sub(r"^[#>\s]+", "", _normalize_text(line)).lower() for line in lines[:20])
    if "also in the" in joined and "series" in joined:
        return True
    written_by_count = len(re.findall(r"\bwritten by\b", joined))
    return written_by_count >= 2


def _looks_like_index(lines: list[str]) -> bool:
    cleaned = [re.sub(r"^[#>\s]+", "", _normalize_text(line)) for line in lines[:25]]
    if any(INDEX_TITLE_RE.match(line) for line in cleaned[:4]):
        return True
    index_hits = sum(1 for line in cleaned if INDEX_LINE_RE.match(line))
    dense_index_refs = sum(len(re.findall(r",\s*(?:[ivxlcdm]+|\d+)", line, flags=re.IGNORECASE)) for line in cleaned)
    if len(cleaned) <= 4:
        return index_hits >= 1 and dense_index_refs >= 4
    return index_hits >= 8 or dense_index_refs >= 14


def _looks_like_table_heavy(lines: list[str]) -> bool:
    if len(lines) < 4:
        return False
    dense_numeric = 0
    for line in lines[:20]:
        normalized = _normalize_text(line)
        digits = sum(char.isdigit() for char in normalized)
        if digits >= 6 or "|" in normalized or "\t" in normalized:
            dense_numeric += 1
    return dense_numeric >= 4


def _classify_page_kind(page_no: int, total_pages: int, blocks: list[LayoutBlock]) -> str:
    lines = _page_text_lines(blocks)
    text_chars = sum(len(_normalize_text(line)) for line in lines)
    chapter_title = _detect_chapter_title(blocks)
    if text_chars == 0:
        return "visual_only"
    if _looks_like_toc(lines, page_no=page_no, total_pages=total_pages):
        return "toc"
    if _looks_like_references(lines, page_no=page_no, total_pages=total_pages):
        return "references"
    if _looks_like_index(lines):
        return "index"
    if _looks_like_table_heavy(lines):
        return "table_heavy"
    if _looks_like_notes(lines):
        return "notes_heavy"
    if _looks_like_back_matter(lines, page_no=page_no, total_pages=total_pages):
        return "back_matter"
    if chapter_title:
        return "body"
    if total_pages >= 12 and page_no <= min(6, max(total_pages // 8, 1)) and text_chars < BODY_TEXT_MIN_CHARS:
        return "front_matter"
    if total_pages >= 12 and page_no >= total_pages - 3 and text_chars < BODY_TEXT_MIN_CHARS:
        return "back_matter"
    return "body"


def _top_title_candidates(blocks: list[LayoutBlock]) -> list[str]:
    candidates: list[str] = []
    for block in blocks:
        if block.top < TOP_TITLE_BAND:
            continue
        text = _clean_book_text(block.text)
        if not text:
            continue
        if _looks_like_broken_formula_fragment(text):
            continue
        if block.label == "caption":
            continue
        if len(text.split()) > MAX_TITLE_LINE_WORDS:
            continue
        candidates.append(text)
    return candidates[:4]


def _detect_chapter_title(blocks: list[LayoutBlock]) -> str | None:
    candidates = _top_title_candidates(blocks)
    if not candidates:
        return None

    first = candidates[0]
    if BOOK_SECTION_START_RE.match(first):
        return first

    if CHAPTER_MARKER_RE.match(first):
        if len(candidates) > 1 and not CHAPTER_MARKER_RE.match(candidates[1]):
            return f"{first}: {candidates[1]}"
        return first

    if PURE_CHAPTER_TITLE_RE.match(first) and len(candidates) > 1:
        return f"{first} {candidates[1]}"

    if NUMBERED_CHAPTER_TITLE_RE.match(first):
        return first

    if PART_WITH_TITLE_RE.match(first):
        return first

    if len(candidates) >= 2 and CHAPTER_MARKER_RE.match(candidates[0]):
        return f"{candidates[0]}: {candidates[1]}"

    for candidate in candidates:
        if candidate.startswith("## "):
            candidate = candidate[3:].strip()
        if len(candidate.split()) < 3:
            continue
        if candidate.endswith("."):
            continue
        if candidate.isupper() or candidate.istitle():
            return candidate
    return None


def _build_chapter_markdown(page_payloads: list[dict[str, Any]], *, include_page_markers: bool) -> str:
    parts: list[str] = []
    for payload in page_payloads:
        content_lines = payload["content_lines"]
        if not content_lines:
            continue
        if parts:
            parts.append("")
        if include_page_markers:
            parts.append(f"[[page: {payload['page_no']}]]")
        parts.extend(content_lines)
    markdown = "\n\n".join(line.strip() for line in parts if line.strip())
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    markdown = _dedupe_adjacent_duplicate_headings(markdown)
    return markdown + "\n" if markdown else ""


def _dedupe_adjacent_duplicate_headings(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    out: list[str] = []
    last_heading_key: str | None = None
    only_blank_since_heading = False
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            heading_key = _clean_book_text(match.group(2)).casefold()
            if heading_key and heading_key == last_heading_key and only_blank_since_heading:
                continue
            last_heading_key = heading_key
            only_blank_since_heading = True
            out.append(line)
            continue
        if line.strip():
            last_heading_key = None
            only_blank_since_heading = False
        elif last_heading_key is not None:
            only_blank_since_heading = True
        out.append(line)
    return "\n".join(out).strip()


def _strip_duplicate_leading_heading(markdown_text: str, chapter_title: str) -> str:
    if _is_placeholder_title(chapter_title):
        return markdown_text

    lines = markdown_text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return markdown_text

    first_heading = _clean_heading_text(lines[0])
    normalized_heading = _normalize_text(first_heading).lower()
    normalized_title = _normalize_text(chapter_title).lower()
    title_tail = normalized_title.split(":", 1)[-1].strip()
    if normalized_heading in {normalized_title, title_tail}:
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
        return ("\n".join(lines).strip() + "\n") if lines else ""
    return markdown_text


def _clean_heading_text(line: str) -> str:
    return re.sub(r"^[#>\s]+", "", _normalize_text(line)).strip()


def _infer_section_title(page_payloads: list[dict[str, Any]], fallback: str) -> str:
    headings: list[str] = []
    for payload in page_payloads:
        for line in payload["content_lines"]:
            if not line.startswith("## "):
                continue
            heading = _clean_heading_text(line)
            if not heading:
                continue
            if len(heading.split()) > MAX_TITLE_LINE_WORDS:
                continue
            if heading.lower() in NON_SECTION_HEADINGS:
                continue
            headings.append(heading)
            if len(headings) >= 3:
                break
        if headings:
            break

    if not headings:
        return fallback

    first = headings[0]
    if len(headings) > 1 and (
        re.fullmatch(r"part\s+(?:\d+|[ivxlcdm]+)", first, flags=re.IGNORECASE)
        or CHAPTER_MARKER_RE.match(first)
    ):
        return f"{first}: {headings[1]}"
    return first


def _is_placeholder_title(title: str) -> bool:
    return title.startswith("Untitled Section")


def _is_cover_chapter_title(title: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(title or "").lower()).strip()
    return normalized in {
        "cover",
        "half title",
        "title page",
        "title pages",
        "front matter",
        "frontmatter",
    }


def _is_title_pages_chapter_title(title: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(title or "").lower()).strip()
    return "title page" in normalized


def _resolve_canonical_chapter_pages(
    *,
    chapter_title: str,
    pages: list[int],
    page_markdown: dict[int, str],
    base_preserve_original: bool,
) -> tuple[list[int], bool, dict[str, list[int]]]:
    """Pick canonical pages and decide whether the chapter stays translatable."""

    exclusions = {"excluded_blank_pages": [], "excluded_fallback_pages": []}
    if base_preserve_original:
        return pages, True, exclusions

    fallback_pages = [
        page
        for page in pages
        if ORIGINAL_PAGE_FALLBACK_RE.search(str(page_markdown.get(page) or ""))
    ]
    candidate_pages = [page for page in pages if page not in fallback_pages]
    if fallback_pages:
        exclusions["excluded_fallback_pages"] = fallback_pages

    text_pages = [
        page
        for page in candidate_pages
        if _translatable_page_text_chars(page_markdown, page) >= 1
    ]
    blank_pages = [page for page in candidate_pages if page not in text_pages]
    if blank_pages:
        exclusions["excluded_blank_pages"] = blank_pages

    if text_pages:
        return text_pages, False, exclusions

    if (
        _is_placeholder_title(chapter_title)
        or _is_cover_chapter_title(chapter_title)
        or _is_resource_chapter_title(chapter_title)
        or _is_title_pages_chapter_title(chapter_title)
    ):
        return pages, True, exclusions

    # Blank or image-only chapters (e.g. dedication pages) should not block confirmation.
    return pages, True, exclusions


def _mark_excluded_canonical_pages(
    pages: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Record pages dropped from translatable chapters so page integrity can pass."""

    excluded_blank: dict[int, str] = {}
    excluded_fallback: dict[int, str] = {}
    for chapter in chapters:
        exclusions = chapter.get("page_exclusions")
        if not isinstance(exclusions, dict):
            continue
        title = str(chapter.get("title") or "chapter")
        for page_no in exclusions.get("excluded_blank_pages", []):
            try:
                excluded_blank[int(page_no)] = title
            except (TypeError, ValueError):
                continue
        for page_no in exclusions.get("excluded_fallback_pages", []):
            try:
                excluded_fallback[int(page_no)] = title
            except (TypeError, ValueError):
                continue

    marked: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        payload = dict(page)
        try:
            page_no = int(payload.get("page_no") or 0)
        except (TypeError, ValueError):
            marked.append(payload)
            continue
        if page_no in excluded_fallback:
            payload["disposition"] = "skipped"
            payload["skip_reason"] = (
                f"page_render_fallback_excluded_from:{excluded_fallback[page_no]}"
            )
        elif page_no in excluded_blank:
            payload["disposition"] = "skipped"
            payload["skip_reason"] = (
                f"no_embedded_text_excluded_from:{excluded_blank[page_no]}"
            )
        marked.append(payload)
    return marked


def _is_resource_chapter_title(title: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(title or "").lower()).strip()
    return normalized in {
        "cover",
        "dedication",
        "acknowledgments",
        "acknowledgements",
        "epigraph",
        "copyright",
        "colophon",
        "contents",
        "table of contents",
        "maps",
        "images",
        "maps images",
        "maps and images",
        "figures",
        "list of figures",
        "list of maps",
        "list of tables",
        "illustrations",
        "notes",
        "endnotes",
        "bibliography",
        "reference",
        "references",
        "works cited",
        "index",
    }


def _canonical_chapter_preserve_original(
    *,
    title: str,
    pages: list[int],
    page_policy: dict[int, dict[str, bool]],
    user_confirmed: bool = False,
) -> bool:
    if _is_resource_chapter_title(title):
        return True
    if user_confirmed:
        return False
    policies = [page_policy.get(page, {}) for page in pages if page in page_policy]
    return bool(policies) and all(
        policy.get("preserve_original") or policy.get("resource_only") or policy.get("translate") is False
        for policy in policies
    )


def apply_canonical_chapter_plan(
    book: dict[str, Any],
    canonical: dict[str, Any],
    *,
    source_path: Path | None = None,
    asset_dir: Path | None = None,
) -> dict[str, Any]:
    canonical_source = str(canonical.get("source_artifact") or "")
    user_confirmed = canonical_source == "user_confirmation"
    use_epub_reader_pages = bool(
        user_confirmed
        and source_path is not None
        and source_path.suffix.lower() == ".epub"
    )

    page_markdown: dict[int, str] = {}
    page_policy: dict[int, dict[str, bool]] = {}

    if use_epub_reader_pages:
        from pdf_translator.epub_reader_pages import build_epub_reader_page_markdown

        page_markdown = build_epub_reader_page_markdown(
            source_path,
            asset_dir=asset_dir,
        )
    else:
        for chapter in book.get("chapters", []):
            for page_no in chapter.get("source_pages", []):
                try:
                    page_number = int(page_no)
                except (TypeError, ValueError):
                    continue
                page_policy[page_number] = {
                    "preserve_original": bool(chapter.get("preserve_original")),
                    "resource_only": bool(chapter.get("resource_only")),
                    "translate": bool(chapter.get("translate", True)),
                }
            trace = str(chapter.get("trace_markdown") or "")
            matches = list(TRACE_PAGE_RE.finditer(trace))
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(trace)
                page_markdown[int(match.group(1))] = trace[match.end():end].strip()

    if not use_epub_reader_pages:
        for asset in book.get("assets", []):
            if not isinstance(asset, dict) or not isinstance(asset.get("page_no"), int):
                continue
            page_no = int(asset["page_no"])
            policy = page_policy.get(page_no, {})
            if policy.get("preserve_original") or policy.get("resource_only"):
                continue
            if "original-page-p" in page_markdown.get(page_no, ""):
                continue
            asset_markdown = str(asset.get("text") or "").strip()
            if not asset_markdown and asset.get("path"):
                label = "Table" if asset.get("kind") == "table" else "Figure"
                asset_markdown = f"![{label}]({asset['path']})"
            if asset_markdown and asset_markdown not in page_markdown.get(asset["page_no"], ""):
                page_markdown[asset["page_no"]] = "\n\n".join(
                    part for part in (page_markdown.get(asset["page_no"], ""), asset_markdown) if part
                )

    canonical_chapters = [
        chapter
        for chapter in canonical.get("chapters", [])
        if isinstance(chapter, dict)
    ]
    if not canonical_chapters:
        raise ValueError("Canonical chapter plan contains no chapters.")

    if use_epub_reader_pages:
        available_pages = sorted(page_markdown)
    else:
        available_pages = sorted(
            int(page["page_no"])
            for page in book.get("pages", [])
            if isinstance(page, dict)
            and isinstance(page.get("page_no"), int)
            and (
                page.get("has_content") is True
                or (
                    "has_content" not in page
                    and (
                        int(page["page_no"]) in page_markdown
                        or int(page.get("figure_count") or 0) > 0
                        or int(page.get("table_count") or 0) > 0
                    )
                )
            )
        )
    planned_pages = {
        int(page_no)
        for chapter in canonical_chapters
        for page_no in (
            chapter.get("source_pages")
            or range(int(chapter.get("page_start") or 0), int(chapter.get("page_end") or 0) + 1)
        )
    }
    chapters: list[dict[str, Any]] = []

    if not user_confirmed:
        uncovered = [page_no for page_no in available_pages if page_no not in planned_pages]
        if uncovered:
            groups: list[list[int]] = []
            for page_no in uncovered:
                if (
                    groups
                    and page_no == groups[-1][-1] + 1
                    and page_policy.get(page_no, {}) == page_policy.get(groups[-1][-1], {})
                ):
                    groups[-1].append(page_no)
                else:
                    groups.append([page_no])
            first_planned = min(planned_pages) if planned_pages else 1
            for group in groups:
                title = "Front Matter" if group[-1] < first_planned else "Supplementary Material"
                policies = [page_policy.get(page, {}) for page in group]
                preserve_original = bool(policies) and all(
                    policy.get("preserve_original", False) for policy in policies
                )
                chapters.append(
                    {
                        "title": title,
                        "page_start": group[0],
                        "page_end": group[-1],
                        "source_pages": group,
                        "markdown": "\n\n".join(
                            page_markdown.get(page, "")
                            for page in group
                            if page_markdown.get(page, "")
                        ).strip(),
                        "trace_markdown": "\n\n".join(
                            f"[[page: {page}]]\n\n{page_markdown.get(page, '')}" for page in group
                        ).strip(),
                        "translate": not preserve_original,
                        "preserve_original": preserve_original,
                        "resource_only": True,
                        "toc": False,
                    }
                )

    for canonical_chapter in canonical_chapters:
        pages = [
            int(page_no)
            for page_no in (
                canonical_chapter.get("source_pages")
                or range(
                    int(canonical_chapter.get("page_start") or 0),
                    int(canonical_chapter.get("page_end") or 0) + 1,
                )
            )
        ]
        chapter_title = str(canonical_chapter.get("title") or f"Chapter {len(chapters) + 1}")
        base_preserve_original = _canonical_chapter_preserve_original(
            title=chapter_title,
            pages=pages,
            page_policy=page_policy,
            user_confirmed=user_confirmed,
        )
        if not pages:
            continue
        pages, preserve_original, page_exclusions = _resolve_canonical_chapter_pages(
            chapter_title=chapter_title,
            pages=pages,
            page_markdown=page_markdown,
            base_preserve_original=base_preserve_original,
        )
        if not pages:
            continue
        markdown = "\n\n".join(page_markdown.get(page, "") for page in pages).strip()
        trace_markdown = "\n\n".join(
            f"[[page: {page}]]\n\n{page_markdown.get(page, '')}"
            for page in pages
        ).strip()
        chapters.append(
            {
                "title": chapter_title,
                "page_start": pages[0],
                "page_end": pages[-1],
                "source_pages": pages,
                "markdown": markdown,
                "trace_markdown": trace_markdown,
                "translate": not preserve_original,
                "preserve_original": preserve_original,
                "resource_only": preserve_original,
                "toc": True,
                "page_exclusions": page_exclusions,
            }
        )

    chapters.sort(key=lambda chapter: int(chapter["page_start"]))
    for index, chapter in enumerate(chapters, 1):
        chapter["index"] = index
    _assign_chapter_ids(chapters)
    chapter_by_page = {
        int(page_no): str(chapter.get("chapter_id") or "")
        for chapter in chapters
        for page_no in chapter.get("source_pages", [])
        if isinstance(page_no, int)
    }
    semantic_content = book.get("semantic_content")
    if isinstance(semantic_content, dict):
        for note in semantic_content.get("footnotes", []):
            if not isinstance(note, dict):
                continue
            chapter_id = chapter_by_page.get(int(note.get("source_page") or 0), "")
            for backlink in note.get("backlinks", []):
                if isinstance(backlink, dict):
                    backlink["chapter_id"] = chapter_id

    full_parts: list[str] = []
    trace_parts: list[str] = []
    for chapter in chapters:
        if chapter.get("toc", True):
            full_parts.append(f"# {chapter['title']}")
        full_parts.append(str(chapter.get("markdown") or "").strip())
        trace_parts.append(f"# {chapter['title']}")
        trace_parts.append(str(chapter.get("trace_markdown") or "").strip())

    result = dict(book)
    result["metadata"] = {
        **dict(book.get("metadata") or {}),
        "chapter_source": "user_confirmed_canonical",
        "canonical_chapter_count": len(canonical_chapters),
        "page_coordinate_system": "epub_reader" if use_epub_reader_pages else "book_ir",
    }
    result["chapters"] = chapters
    result["chapter_count"] = len(chapters)
    raw_pages = [
        {
            **page,
            "has_content": (
                page.get("has_content") is True
                if "has_content" in page
                else int(page.get("page_no") or 0) in available_pages
            ),
        }
        for page in book.get("pages", [])
        if isinstance(page, dict)
    ]
    result["pages"] = _mark_excluded_canonical_pages(raw_pages, chapters)
    result["full_markdown"] = "\n\n".join(part for part in full_parts if part).strip()
    result["trace_markdown"] = "\n\n".join(part for part in trace_parts if part).strip()
    return result


def _build_preserved_resource_chapters(
    pages: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    used_pages = {
        int(page_no)
        for chapter in chapters
        for page_no in chapter.get("source_pages", [])
    }
    preserved: list[dict[str, Any]] = []
    uncovered = [
        page
        for page in pages
        if int(page["page_no"]) not in used_pages and page["content_lines"]
    ]
    groups: list[list[dict[str, Any]]] = []
    for page in uncovered:
        if (
            groups
            and int(page["page_no"]) == int(groups[-1][-1]["page_no"]) + 1
            and page["page_kind"] == groups[-1][-1]["page_kind"]
        ):
            groups[-1].append(page)
        else:
            groups.append([page])

    for group in groups:
        page = group[0]
        page_no = int(page["page_no"])
        markdown = _build_chapter_markdown(group, include_page_markers=False)
        if not markdown.strip():
            continue
        title = _preserved_resource_title(page)
        apparatus = page["page_kind"] in {"toc", "references", "index"}
        preserved.append(
            {
                "index": -1,
                "title": title,
                "page_start": page_no,
                "page_end": int(group[-1]["page_no"]),
                "source_pages": [int(item["page_no"]) for item in group],
                "markdown": markdown,
                "trace_markdown": _build_chapter_markdown(group, include_page_markers=True),
                "translate": not apparatus,
                "preserve_original": apparatus,
                "resource_only": True,
                "toc": False,
            }
        )
    return preserved


def _layout_apparatus_title(page: dict[str, Any]) -> str | None:
    title = _clean_book_text(str(page.get("chapter_title") or ""))
    if not title:
        for line in page.get("content_lines") or []:
            candidate = _clean_heading_text(str(line))
            if candidate:
                title = candidate
                break
    if re.fullmatch(r"(?:notes|endnotes)", title, flags=re.IGNORECASE):
        return "Notes"
    if REFERENCE_TITLE_RE.match(title):
        return "References"
    if INDEX_TITLE_RE.match(title):
        return "Index"
    return None


def _build_layout_apparatus_chapters(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    current_pages: list[dict[str, Any]] = []
    current_title: str | None = None

    def flush(end_page: int | None = None) -> None:
        nonlocal current_pages, current_title
        if not current_pages or current_title is None:
            return
        markdown = _build_chapter_markdown(current_pages, include_page_markers=False)
        page_start = int(current_pages[0]["page_no"])
        page_end = max(page_start, int(end_page or current_pages[-1]["page_no"]))
        chapters.append(
            {
                "index": -1,
                "title": current_title,
                "page_start": page_start,
                "page_end": page_end,
                "source_pages": list(range(page_start, page_end + 1)),
                "markdown": markdown,
                "trace_markdown": _build_chapter_markdown(current_pages, include_page_markers=True),
                "translate": False,
                "preserve_original": True,
                "resource_only": True,
                "toc": False,
            }
        )
        current_pages = []
        current_title = None

    for page in pages:
        apparatus_title = _layout_apparatus_title(page)
        if apparatus_title is not None:
            if current_title != apparatus_title:
                flush(int(page["page_no"]) - 1)
                current_title = apparatus_title
            current_pages.append(page)
            continue
        if current_title is None:
            continue
        if page.get("chapter_title"):
            flush(int(page["page_no"]) - 1)
            continue
        current_pages.append(page)
    flush(max((int(page["page_no"]) for page in pages), default=0))
    return chapters


def _preserved_resource_title(page: dict[str, Any]) -> str:
    page_no = int(page["page_no"])
    page_kind = str(page.get("page_kind") or "")
    if page_kind == "toc":
        return "Contents"
    if page_kind == "references":
        return "References"
    if page_kind == "index":
        return "Index"
    title = _infer_section_title([page], f"Visual Material Page {page_no}")
    if _is_placeholder_title(title):
        return f"Visual Material Page {page_no}"
    return title


def _build_cover_chapter(cover_path: Path | None) -> dict[str, Any] | None:
    if cover_path is None:
        return None
    markdown = f"![Cover]({cover_path.as_posix()})\n"
    return {
        "index": 0,
        "title": "Cover",
        "page_start": 1,
        "page_end": 1,
        "source_pages": [1],
        "markdown": markdown,
        "trace_markdown": "[[page: 1]]\n\n" + markdown,
        "translate": False,
        "preserve_original": True,
        "resource_only": True,
        "cover": True,
        "toc": False,
    }


def _chapter_pages_from_outline(
    pages: list[dict[str, Any]],
    outline_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not outline_entries:
        return []

    pages_by_no = {int(page["page_no"]): page for page in pages}
    page_numbers = sorted(pages_by_no)
    chapters: list[dict[str, Any]] = []
    for entry in outline_entries:
        if entry.get("drop"):
            continue
        start_page = int(entry["page_no"])
        later_starts = (
            int(candidate["page_no"])
            for candidate in outline_entries
            if int(candidate["page_no"]) > start_page
        )
        next_start = min(later_starts, default=(page_numbers[-1] + 1 if page_numbers else start_page + 1))
        if next_start <= start_page:
            continue

        if entry.get("skip"):
            excluded_page_kinds = {"visual_only"}
        else:
            # Keep table_heavy pages: they are often charts/tables/outline-style grids; excluding them
            # dropped dense figure/table pages that Docling did not flag with structured table_count.
            excluded_page_kinds = {"toc", "references", "index", "visual_only"}
        selected_pages = [
            pages_by_no[page_no]
            for page_no in page_numbers
            if start_page <= page_no < next_start
            and (
                pages_by_no[page_no]["page_kind"] not in excluded_page_kinds
                or pages_by_no[page_no].get("table_count", 0) > 0
                or pages_by_no[page_no].get("figure_count", 0) > 0
            )
            and pages_by_no[page_no]["content_lines"]
        ]
        if not selected_pages:
            continue

        title = str(entry["title"])
        chapter_markdown = _build_chapter_markdown(selected_pages, include_page_markers=False)
        chapter_markdown = _strip_duplicate_leading_heading(chapter_markdown, title)
        trace_markdown = _build_chapter_markdown(selected_pages, include_page_markers=True)
        if not chapter_markdown.strip():
            continue

        chapters.append(
            {
                "index": len(chapters) + 1,
                "title": title,
                "page_start": selected_pages[0]["page_no"],
                "page_end": selected_pages[-1]["page_no"],
                "source_pages": [page["page_no"] for page in selected_pages],
                "markdown": chapter_markdown,
                "trace_markdown": trace_markdown,
                "outline_depth": entry.get("depth", 0),
                "translate": not bool(entry.get("skip")),
                "preserve_original": bool(entry.get("skip")),
            }
        )

    return chapters


def _build_book_from_epub_meta(meta: dict[str, Any], source_path: Path | None) -> dict[str, Any]:
    raw_chapters = meta.get("chapters") or []
    raw_assets = meta.get("assets") or []
    chapters: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    in_outline_index = False
    for entry in raw_chapters:
        title = str(entry.get("title") or f"Section {len(chapters) + 1}")
        md = str(entry.get("markdown") or "").strip()
        title_clean = _clean_book_text(title).lower()
        md_no_links = re.sub(r"\[[^\]]+\]\([^)]+\)", "", md)
        md_text_chars = len(re.sub(r"[\W_]+", "", md_no_links, flags=re.UNICODE))
        has_image = "![" in md
        if OUTLINE_DROP_TITLE_RE.match(title) and not has_image:
            continue
        if title_clean in {"title", "title page", "half title"} and not has_image and md_text_chars < 160:
            continue
        if title_clean in {"navigation", "page list"}:
            continue
        if "## page list" in md.lower() and md.count("\n- [") > 25:
            continue

        i = len(chapters) + 1
        if md and not md.endswith("\n"):
            md += "\n"
        tr = str(entry.get("trace_markdown") or md).strip()
        if tr and not tr.endswith("\n"):
            tr += "\n"
        sip = entry.get("source_internal_path")
        is_cover_title = title_clean in {"cover", "cover page"}
        is_index_continuation = bool(
            in_outline_index
            and re.fullmatch(r"[A-Z]", title.strip(), flags=re.IGNORECASE)
        )
        is_preserved_resource = bool(
            is_cover_title
            or OUTLINE_SKIP_TITLE_RE.match(title)
            or is_index_continuation
        )
        if INDEX_TITLE_RE.match(title):
            in_outline_index = True
        chapters.append(
            {
                "index": i,
                "title": title,
                "page_start": i,
                "page_end": i,
                "source_pages": [i],
                "markdown": md,
                "trace_markdown": tr,
                "translate": not is_preserved_resource,
                "preserve_original": is_preserved_resource,
                "resource_only": is_preserved_resource,
                "source_internal_path": sip if isinstance(sip, str) else None,
                "toc": not is_preserved_resource,
            }
        )
        pages.append(
            {
                "page_no": i,
                "page_kind": "body",
                "chapter_title": None,
                "figure_count": 0,
                "table_count": 0,
            }
        )

    full_markdown_parts: list[str] = []
    trace_markdown_parts: list[str] = []
    for chapter in chapters:
        if not _is_placeholder_title(chapter["title"]):
            full_markdown_parts.append(f"# {chapter['title']}\n")
        full_markdown_parts.append(chapter["markdown"].strip())
        full_markdown_parts.append("")
        trace_markdown_parts.append(f"# {chapter['title']}\n")
        trace_markdown_parts.append(chapter["trace_markdown"].strip())
        trace_markdown_parts.append("")

    full_markdown = "\n\n".join(part for part in full_markdown_parts if part).strip()
    if full_markdown:
        full_markdown += "\n"
    trace_markdown = "\n\n".join(part for part in trace_markdown_parts if part).strip()
    if trace_markdown:
        trace_markdown += "\n"

    _assign_chapter_ids(chapters)
    epub_footnote_ratio_pages = [{"content_lines": [ch["markdown"]]} for ch in chapters]
    epub_fn_ratio = _book_footnote_line_ratio_from_pages(epub_footnote_ratio_pages)
    assets: list[dict[str, Any]] = []
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, dict):
            continue
        path = raw_asset.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        kind = str(raw_asset.get("kind") or "picture")
        assets.append(
            {
                "kind": "cover" if kind == "cover" else "picture",
                "page_no": None,
                "path": path,
                "source_internal_path": raw_asset.get("source_internal_path"),
                "text": f"![{('Cover' if kind == 'cover' else 'Image')}]({path})",
            }
        )
    if not any(asset["kind"] == "cover" for asset in assets):
        for chapter in chapters:
            if "![Cover" not in chapter.get("markdown", ""):
                continue
            match = re.search(r"!\[[^\]]*]\(([^)]+)\)", chapter["markdown"])
            if match:
                assets.insert(
                    0,
                    {
                        "kind": "cover",
                        "page_no": chapter.get("page_start"),
                        "path": match.group(1),
                        "source_internal_path": chapter.get("source_internal_path"),
                        "text": match.group(0),
                    },
                )
                break
    cover_image_path = next((asset["path"] for asset in assets if asset["kind"] == "cover"), None)

    return {
        "metadata": {
            "schema": "book_ir",
            "schema_version": 1,
            "chapter_source": "epub_spine",
            "outline_entry_count": len(chapters),
            "outline_stop_entry_count": len(chapters),
            "footnote_line_ratio": round(epub_fn_ratio, 5),
            "footnote_load": _footnote_load_label(epub_fn_ratio),
            "cover_image_path": cover_image_path,
        },
        "render_policy": {
            "reading_output": "single_language_epub",
            "figures": "embed_crops_translate_alt_optional_caption_skipped_when_structured_table",
            "footnotes": "note_like_page_footer_blocks_end_of_page_reading_order",
            "apparatus": "references_bibliography_index_sections_classified_separately",
            "math": "weak_support_clean_broken_fragments",
        },
        "chapter_count": len(chapters),
        "chapters": chapters,
        "assets": assets,
        "pages": [
            {
                "page_no": page["page_no"],
                "page_kind": page["page_kind"],
                "chapter_title": page["chapter_title"],
                "figure_count": page["figure_count"],
                "table_count": page["table_count"],
                "has_content": bool(page.get("content_lines")),
            }
            for page in pages
        ],
        "full_markdown": full_markdown,
        "trace_markdown": trace_markdown,
    }
    _annotate_chapter_kinds(book)
    return book


def build_book_reconstruction(
    structured: dict[str, Any],
    *,
    source_pdf: Path | None = None,
    images_dir: Path | None = None,
) -> dict[str, Any]:
    epub_meta = structured.get("_epub_meta") if isinstance(structured, dict) else None
    if isinstance(epub_meta, dict) and epub_meta.get("schema") == "epub_ingest_v1":
        return _build_book_from_epub_meta(epub_meta, source_pdf)

    cover_path = _render_pdf_cover_page(source_pdf, images_dir)
    picture_items, used_caption_texts = _extract_picture_items(
        structured,
        source_pdf=source_pdf,
        images_dir=images_dir,
    )
    table_items = _extract_table_items(
        structured,
        source_pdf=source_pdf,
        images_dir=images_dir,
    )
    raw_text_blocks = _extract_text_blocks(structured)
    ocr_quarantine, quarantined_block_ids = _ocr_quarantine_records(raw_text_blocks)
    ordered_pages = _ordered_page_blocks(
        structured,
        excluded_block_ids=quarantined_block_ids,
    )
    semantic_footnotes = _semantic_footnotes_from_pages(ordered_pages)
    all_page_numbers = set(ordered_pages) | set(picture_items) | set(table_items)
    total_pages = max(all_page_numbers) if all_page_numbers else 0

    pages: list[dict[str, Any]] = []
    for page_no in sorted(all_page_numbers):
        blocks = ordered_pages.get(page_no, [])
        page_kind = _classify_page_kind(page_no, total_pages, blocks)
        chapter_title = _detect_chapter_title(blocks) if page_kind == "body" else None
        content_items = _page_content_items(
            blocks,
            figures=picture_items.get(page_no, []),
            tables=table_items.get(page_no, []),
            suppressed_caption_texts=used_caption_texts,
        )
        if page_kind == "visual_only" and content_items:
            page_kind = "body"
        content_lines = [item.text for item in content_items if item.text]
        pages.append(
            {
                "page_no": page_no,
                "page_kind": page_kind,
                "chapter_title": chapter_title,
                "content_lines": content_lines,
                "figure_count": len(picture_items.get(page_no, [])),
                "table_count": len(table_items.get(page_no, [])),
            }
        )

    outline_entries = _extract_pdf_outline_chapters(source_pdf, total_pages=total_pages)
    chapters: list[dict[str, Any]] = _chapter_pages_from_outline(pages, outline_entries)
    cover_chapter = _build_cover_chapter(cover_path)
    if cover_chapter is not None:
        chapters.insert(0, cover_chapter)
    current_pages: list[dict[str, Any]] = []
    current_title: str | None = None

    def flush() -> None:
        nonlocal current_pages, current_title
        if not current_pages:
            return
        fallback_title = f"Untitled Section {len(chapters) + 1}"
        chapter_title = current_title or _infer_section_title(current_pages, fallback_title)
        chapter_markdown = _build_chapter_markdown(current_pages, include_page_markers=False)
        chapter_markdown = _strip_duplicate_leading_heading(chapter_markdown, chapter_title)
        trace_markdown = _build_chapter_markdown(current_pages, include_page_markers=True)
        is_part_divider = bool(current_title and PART_WITH_TITLE_RE.match(current_title))
        if not chapter_markdown.strip() and not is_part_divider:
            current_pages = []
            current_title = None
            return
        chapters.append(
            {
                "index": len(chapters) + 1,
                "title": chapter_title,
                "page_start": current_pages[0]["page_no"],
                "page_end": current_pages[-1]["page_no"],
                "source_pages": [page["page_no"] for page in current_pages],
                "markdown": chapter_markdown,
                "trace_markdown": trace_markdown,
                "translate": True,
                "preserve_original": False,
            }
        )
        current_pages = []
        current_title = None

    has_content_chapters = any(not bool(chapter.get("resource_only")) for chapter in chapters)
    if not has_content_chapters:
        layout_apparatus = (
            _build_layout_apparatus_chapters(pages)
            if source_pdf is not None and images_dir is not None
            else []
        )
        apparatus_page_numbers = {
            int(page_no)
            for chapter in layout_apparatus
            for page_no in chapter.get("source_pages", [])
        }
        for page in pages:
            if int(page["page_no"]) in apparatus_page_numbers:
                flush()
                continue
            if page["page_kind"] in {"toc", "references", "index"}:
                flush()
                continue
            _skippable_misc = {"front_matter", "back_matter", "notes_heavy", "visual_only"}
            if page["page_kind"] in _skippable_misc:
                has_assets = (page.get("figure_count", 0) > 0) or (page.get("table_count", 0) > 0)
                has_text = bool(page.get("content_lines"))
                if not has_assets and not has_text:
                    continue

            page_title = page["chapter_title"]
            if page_title and current_pages:
                flush()
            if page_title and current_title is None:
                current_title = page_title
            current_pages.append(page)

        flush()
        chapters.extend(layout_apparatus)

    chapters.extend(_build_preserved_resource_chapters(pages, chapters))
    chapters.sort(key=lambda chapter: (int(chapter.get("page_start") or 0), int(chapter.get("index") or 0)))
    _replace_preserved_apparatus_with_page_images(chapters, source_pdf=source_pdf, images_dir=images_dir)
    for index, chapter in enumerate(chapters, 1):
        chapter["index"] = index
    _assign_chapter_ids(chapters)
    _attach_semantic_footnote_backlinks(
        semantic_footnotes,
        ordered_pages=ordered_pages,
        chapters=chapters,
        standalone_note_pages={
            int(page["page_no"])
            for page in pages
            if int(page.get("table_count") or 0) > 0
            or page.get("page_kind") == "table_heavy"
        },
    )

    full_markdown_parts: list[str] = []
    trace_markdown_parts: list[str] = []
    for chapter in chapters:
        if not _is_placeholder_title(chapter["title"]):
            full_markdown_parts.append(f"# {chapter['title']}\n")
        full_markdown_parts.append(chapter["markdown"].strip())
        full_markdown_parts.append("")
        trace_markdown_parts.append(f"# {chapter['title']}\n")
        trace_markdown_parts.append(chapter["trace_markdown"].strip())
        trace_markdown_parts.append("")

    full_markdown = "\n\n".join(part for part in full_markdown_parts if part).strip()
    if full_markdown:
        full_markdown += "\n"
    trace_markdown = "\n\n".join(part for part in trace_markdown_parts if part).strip()
    if trace_markdown:
        trace_markdown += "\n"

    assets = []
    if cover_path is not None:
        assets.append(
            {
                "kind": "cover",
                "page_no": 1,
                "path": str(cover_path),
                "text": f"![Cover]({cover_path.as_posix()})",
            }
        )
    assets.extend(
        {
            "kind": item.kind,
            "page_no": item.page_no,
            "path": item.path,
            "text": item.text,
        }
        for page_items in picture_items.values()
        for item in page_items
        if item.path
    )
    assets.extend(
        {
            "kind": item.kind,
            "page_no": item.page_no,
            "path": item.path,
            "text": item.text,
        }
        for page_items in table_items.values()
        for item in page_items
        if item.path
    )

    footnote_line_ratio = _book_footnote_line_ratio_from_pages(pages)
    content_page_numbers = {
        int(page["page_no"])
        for page in pages
        if page.get("content_lines")
    }
    covered_page_numbers = {
        int(page_no)
        for chapter in chapters
        for page_no in chapter.get("source_pages", [])
    }
    uncovered_content_pages = sorted(content_page_numbers - covered_page_numbers)
    coverage_ratio = (
        (len(content_page_numbers) - len(uncovered_content_pages)) / len(content_page_numbers)
        if content_page_numbers
        else 1.0
    )
    return {
        "metadata": {
            "schema": "book_ir",
            "schema_version": 1,
            "chapter_source": "pdf_outline" if outline_entries else "layout_heuristic",
            "outline_entry_count": len(
                [entry for entry in outline_entries if not entry.get("skip") and not entry.get("drop")]
            ),
            "outline_stop_entry_count": len(outline_entries),
            "footnote_line_ratio": round(footnote_line_ratio, 5),
            "footnote_load": _footnote_load_label(footnote_line_ratio),
            "content_page_coverage_ratio": round(coverage_ratio, 5),
            "uncovered_content_pages": uncovered_content_pages,
            "cover_image_path": str(cover_path) if cover_path is not None else None,
        },
        "render_policy": {
            "reading_output": "single_language_epub",
            "figures": "embed_crops_translate_alt_optional_caption_skipped_when_structured_table",
            "footnotes": "note_like_page_footer_blocks_end_of_page_reading_order",
            "apparatus": "references_bibliography_index_sections_classified_separately",
            "math": "weak_support_clean_broken_fragments",
        },
        "chapter_count": len(chapters),
        "chapters": chapters,
        "assets": assets,
        "semantic_content": {
            "schema": SEMANTIC_CONTENT_SCHEMA,
            "footnotes": semantic_footnotes,
            "ocr_quarantine": ocr_quarantine,
            "evidence_assets": [],
        },
        "pages": [
            {
                "page_no": page["page_no"],
                "page_kind": page["page_kind"],
                "chapter_title": page["chapter_title"],
                "figure_count": page["figure_count"],
                "table_count": page["table_count"],
                "has_content": bool(
                    page.get("content_lines")
                    or int(page.get("figure_count") or 0) > 0
                    or int(page.get("table_count") or 0) > 0
                ),
            }
            for page in pages
        ],
        "full_markdown": full_markdown,
        "trace_markdown": trace_markdown,
    }
