from __future__ import annotations

import math
import re
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


TOP_TITLE_BAND = 480.0
MAX_TITLE_LINE_WORDS = 12
BODY_TEXT_MIN_CHARS = 260
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
MATH_SYMBOL_RE = re.compile(r"[=+\-−·×÷*/^_√∂∑∫∞≈≠≤≥<>|]")
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
TOC_LINE_RE = re.compile(r"\.{2,}\s*\d+$|^\d+\s+[A-Z].+\d+$")
NOTE_LINE_RE = re.compile(r"^(?:\d+|\*|†|‡)\s+")
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
    r"^(?:front\s*matter|frontmatter|copyright|dedication|contents|table of contents|"
    r"tables|figures|text boxes?|glossary|abbreviations|notes|endnotes|bibliography|references|works cited|"
    r".*index|index of .*)$",
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


def _ordered_page_blocks(structured: dict[str, Any]) -> dict[int, list[LayoutBlock]]:
    blocks = [block for block in _extract_text_blocks(structured) if not _is_book_noise_block(block)]
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
        if table_markdown is None:
            if image_path is None:
                continue
            table_markdown = f"![Table {page_no}.{table_no}]({image_path.as_posix()})"
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
            from_page_footer=block.label == "page_footer",
        )
        for block in blocks
        if _format_book_block(block)
        and not (block.label == "caption" and _normalize_text(block.text) in suppressed_caption_texts)
    ]
    items.extend(figures)
    items.extend(tables)
    ordered = sorted(
        items,
        key=lambda item: (-item.top, item.left, _kind_reading_rank(item.kind)),
    )
    out: list[BookItem] = []
    footer_started = False
    for item in ordered:
        text = item.text
        if item.from_page_footer and out and not footer_started:
            text = "\n\n---\n\n" + text
            footer_started = True
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
    return markdown + "\n" if markdown else ""


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
    for page in pages:
        page_no = int(page["page_no"])
        if page_no in used_pages:
            continue
        if not page["content_lines"]:
            continue
        if page.get("figure_count", 0) <= 0 and page.get("table_count", 0) <= 0:
            continue
        markdown = _build_chapter_markdown([page], include_page_markers=False)
        if not markdown.strip():
            continue
        preserved.append(
            {
                "index": -1,
                "title": f"Original Visual Page {page_no}",
                "page_start": page_no,
                "page_end": page_no,
                "source_pages": [page_no],
                "markdown": markdown,
                "trace_markdown": _build_chapter_markdown([page], include_page_markers=True),
                "translate": False,
                "preserve_original": True,
                "resource_only": True,
            }
        )
    return preserved


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
            excluded_page_kinds = {"toc", "references", "index", "table_heavy", "visual_only"}
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
    chapters: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    for i, entry in enumerate(raw_chapters, 1):
        title = str(entry.get("title") or f"Section {i}")
        md = str(entry.get("markdown") or "").strip()
        if md and not md.endswith("\n"):
            md += "\n"
        tr = str(entry.get("trace_markdown") or md).strip()
        if tr and not tr.endswith("\n"):
            tr += "\n"
        chapters.append(
            {
                "index": i,
                "title": title,
                "page_start": i,
                "page_end": i,
                "source_pages": [i],
                "markdown": md,
                "trace_markdown": tr,
                "translate": True,
                "preserve_original": False,
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

    return {
        "metadata": {
            "schema": "book_ir",
            "schema_version": 1,
            "chapter_source": "epub_spine",
            "outline_entry_count": len(chapters),
            "outline_stop_entry_count": len(chapters),
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
        "assets": [],
        "pages": [
            {
                "page_no": page["page_no"],
                "page_kind": page["page_kind"],
                "chapter_title": page["chapter_title"],
                "figure_count": page["figure_count"],
                "table_count": page["table_count"],
            }
            for page in pages
        ],
        "full_markdown": full_markdown,
        "trace_markdown": trace_markdown,
    }


def build_book_reconstruction(
    structured: dict[str, Any],
    *,
    source_pdf: Path | None = None,
    images_dir: Path | None = None,
) -> dict[str, Any]:
    epub_meta = structured.get("_epub_meta") if isinstance(structured, dict) else None
    if isinstance(epub_meta, dict) and epub_meta.get("schema") == "epub_ingest_v1":
        return _build_book_from_epub_meta(epub_meta, source_pdf)

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
    ordered_pages = _ordered_page_blocks(structured)
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
        if not chapter_markdown.strip():
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

    if not chapters:
        for page in pages:
            if page["page_kind"] in {"toc", "references", "index"}:
                flush()
                continue
            if page["page_kind"] in {"front_matter", "back_matter", "notes_heavy", "visual_only"}:
                continue

            page_title = page["chapter_title"]
            if page_title and current_pages:
                flush()
            if page_title and current_title is None:
                current_title = page_title
            current_pages.append(page)

        flush()

    chapters.extend(_build_preserved_resource_chapters(pages, chapters))
    chapters.sort(key=lambda chapter: (int(chapter.get("page_start") or 0), int(chapter.get("index") or 0)))
    for index, chapter in enumerate(chapters, 1):
        chapter["index"] = index

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

    assets = [
        {
            "kind": item.kind,
            "page_no": item.page_no,
            "path": item.path,
            "text": item.text,
        }
        for page_items in picture_items.values()
        for item in page_items
        if item.path
    ]
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

    return {
        "metadata": {
            "schema": "book_ir",
            "schema_version": 1,
            "chapter_source": "pdf_outline" if outline_entries else "layout_heuristic",
            "outline_entry_count": len([entry for entry in outline_entries if not entry.get("skip")]),
            "outline_stop_entry_count": len(outline_entries),
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
            }
            for page in pages
        ],
        "full_markdown": full_markdown,
        "trace_markdown": trace_markdown,
    }
