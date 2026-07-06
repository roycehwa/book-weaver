"""Extract table-of-contents entries from PDF text layers (no OCR)."""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Any

import fitz

TOC_HEADING_RE = re.compile(
    r"^(?:table\s+of\s+contents|contents|目录|目\s*录)$",
    re.IGNORECASE,
)
ROMAN_PAGE_RE = re.compile(r"^[ivxlcdm]+$", re.IGNORECASE)
SECTION_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)*$")
INLINE_TOC_LINE_RE = re.compile(
    r"^(?P<prefix>\d+(?:\.\d+)*\s+)?(?P<title>[A-Za-z][^0-9]{2,80}?)"
    r"[\s\.·…]{1,}\s*(?P<page>\d{1,4})\s*$"
)
SINGLE_LINE_BLOB_RE = re.compile(
    r"(?:Preface|Introduction|Appendix|"
    r"\d+\s+(?!\d)(?:[A-Z][A-Za-z0-9 ,'\-–—:()]{2,70}?))"
    r"\s+(\d{1,4})(?=\s|$)"
)


@dataclass(frozen=True)
class TocTextEntry:
    title: str
    printed_page: int
    section: str | None = None

    @property
    def depth(self) -> int:
        if not self.section or not SECTION_NUMBER_RE.fullmatch(self.section):
            return 0
        return self.section.count(".") + 1


def normalize_line(value: str) -> str:
    return " ".join(value.replace("\x00", "").split())


def is_page_number_line(value: str) -> bool:
    if not value.isdigit():
        return False
    page = int(value)
    return 1 <= page <= 3000


def is_section_number_line(value: str) -> bool:
    return bool(SECTION_NUMBER_RE.fullmatch(value))


def is_heading_line(value: str) -> bool:
    return bool(TOC_HEADING_RE.match(value)) or value.lower() in {"contents", "table of contents"}


def is_noise_line(value: str) -> bool:
    lowered = value.lower()
    if ROMAN_PAGE_RE.fullmatch(lowered):
        return True
    if lowered in {"contents", "table of contents", "目录"}:
        return True
    return len(value) < 2


def parse_inline_toc_lines(text: str) -> list[TocTextEntry]:
    entries: list[TocTextEntry] = []
    for raw in text.splitlines():
        line = normalize_line(raw)
        if not line or is_noise_line(line):
            continue
        match = INLINE_TOC_LINE_RE.match(line)
        if not match:
            continue
        prefix = (match.group("prefix") or "").strip()
        title = normalize_line(match.group("title"))
        page = int(match.group("page"))
        section = prefix.split()[0] if prefix else None
        if title and page > 0:
            entries.append(TocTextEntry(title=title, printed_page=page, section=section))
    return entries


def parse_multiline_toc_lines(text: str) -> list[TocTextEntry]:
    lines = [normalize_line(line) for line in text.splitlines() if normalize_line(line)]
    entries: list[TocTextEntry] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if is_heading_line(line):
            index += 1
            continue
        if is_noise_line(line):
            index += 1
            continue

        section: str | None = None
        title_parts: list[str] = []
        printed_page: int | None = None

        if is_section_number_line(line):
            section = line
            index += 1
            if index < len(lines) and lines[index] == section:
                index += 1
            if index >= len(lines):
                break
            if not is_page_number_line(lines[index]) and not is_section_number_line(lines[index]):
                title_parts.append(lines[index])
                index += 1
        elif is_page_number_line(line) and not title_parts and not section:
            # Rare: page before title; skip.
            index += 1
            continue
        else:
            title_parts.append(line)
            index += 1

        while index < len(lines) and not is_page_number_line(lines[index]):
            if is_section_number_line(lines[index]):
                break
            if is_heading_line(lines[index]) or is_noise_line(lines[index]):
                break
            title_parts.append(lines[index])
            index += 1

        if index < len(lines) and is_page_number_line(lines[index]):
            printed_page = int(lines[index])
            index += 1

        title = normalize_line(" ".join(title_parts))
        if title and printed_page:
            if section and not title.startswith(section):
                display_title = normalize_line(f"{section} {title}")
            else:
                display_title = title
            entries.append(
                TocTextEntry(
                    title=display_title,
                    printed_page=printed_page,
                    section=section,
                )
            )
            continue

        if title_parts and index < len(lines):
            index += 1
    return entries


def parse_blob_toc_text(text: str) -> list[TocTextEntry]:
    compact = normalize_line(text)
    if "contents" in compact.lower():
        compact = re.sub(r"(?i)contents\s*", " ", compact, count=1)
    entries: list[TocTextEntry] = []
    for match in SINGLE_LINE_BLOB_RE.finditer(compact):
        fragment = normalize_line(match.group(0))
        page_match = re.search(r"(\d{1,4})$", fragment)
        if not page_match:
            continue
        page = int(page_match.group(1))
        title = normalize_line(fragment[: page_match.start()])
        section_match = re.match(r"^(\d+(?:\.\d+)*)\s+", title)
        section = section_match.group(1) if section_match else None
        if section:
            title = normalize_line(title[len(section_match.group(0)) :])
        if title and page > 0:
            entries.append(TocTextEntry(title=title, printed_page=page, section=section))
    return entries


def parse_toc_text(text: str) -> list[TocTextEntry]:
    multiline = parse_multiline_toc_lines(text)
    if len(multiline) >= 3:
        return _dedupe_entries(multiline)
    inline = parse_inline_toc_lines(text)
    if len(inline) >= 3:
        return _dedupe_entries(inline)
    blob = parse_blob_toc_text(text)
    if len(blob) >= 3:
        return _dedupe_entries(blob)
    best = max([multiline, inline, blob], key=len)
    return _dedupe_entries(best)


def _dedupe_entries(entries: list[TocTextEntry]) -> list[TocTextEntry]:
    seen: set[tuple[str, int]] = set()
    deduped: list[TocTextEntry] = []
    for entry in entries:
        key = (entry.title.lower(), entry.printed_page)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def entry_depth(entry: TocTextEntry) -> int:
    if entry.section and SECTION_NUMBER_RE.fullmatch(entry.section):
        return entry.section.count(".") + 1
    head = entry.title.split()[0] if entry.title else ""
    if re.fullmatch(r"\d+(?:\.\d+)*", head):
        return head.count(".") + 1
    return 0


def filter_toc_entries(
    entries: list[TocTextEntry],
    *,
    max_depth: int | None = 1,
) -> list[TocTextEntry]:
    if max_depth is None:
        return entries
    filtered = [
        entry
        for entry in entries
        if entry_depth(entry) == 0 or entry_depth(entry) <= max_depth
    ]
    return filtered or entries


def score_toc_page_text(text: str) -> float:
    lines = [normalize_line(line) for line in text.splitlines() if normalize_line(line)]
    if len(lines) < 4:
        compact = normalize_line(text)
        if "contents" not in compact.lower():
            return 0.0
        blob_entries = parse_blob_toc_text(text)
        return min(1.0, len(blob_entries) / 8.0)

    hits = 0
    for line in lines:
        if INLINE_TOC_LINE_RE.match(line):
            hits += 1
            continue
        if is_page_number_line(line):
            hits += 0.35
    heading_bonus = 0.25 if any(is_heading_line(line) for line in lines[:3]) else 0.0
    return min(1.0, hits / max(len(lines), 1) + heading_bonus)


def detect_toc_page_range(
    doc: fitz.Document,
    *,
    max_scan_pages: int = 35,
) -> tuple[int, int] | None:
    scores: list[tuple[int, float]] = []
    scan_until = min(max_scan_pages, doc.page_count)
    for page_index in range(scan_until):
        text = doc[page_index].get_text("text")
        score = score_toc_page_text(text)
        if score >= 0.18:
            scores.append((page_index + 1, score))

    if not scores:
        return None

    pages = [page for page, _ in scores]
    start = min(pages)
    end = start
    for page, score in scores:
        if page >= start and page <= start + 6:
            end = max(end, page)
    if end - start > 4:
        end = start + 3
    return start, end


def infer_page_offset(
    doc: fitz.Document,
    entries: list[TocTextEntry],
    *,
    toc_page_start: int | None = None,
    toc_page_end: int | None = None,
    max_checks: int = 6,
) -> int:
    votes: list[int] = []
    search_start = 0
    if toc_page_end is not None:
        search_start = max(0, toc_page_end)
    for entry in entries[:max_checks]:
        needle = entry.title.strip()
        if len(needle) < 4:
            continue
        probe = needle.lower()
        if entry.section and not probe.startswith(entry.section.lower()):
            probe = f"{entry.section.lower()} {probe}"
        fragment = probe[: min(len(probe), 48)]
        for page_index in range(search_start, doc.page_count):
            page_text = doc[page_index].get_text("text").lower()
            if fragment not in page_text:
                continue
            if toc_page_start is not None and toc_page_end is not None:
                page_number = page_index + 1
                if toc_page_start <= page_number <= toc_page_end:
                    continue
            pdf_page = page_index + 1
            votes.append(pdf_page - entry.printed_page)
            break
    if not votes:
        return 0
    return int(statistics.median(votes))


def _entry_depth(entry: TocTextEntry) -> int:
    return entry.depth


def _pdf_end_for_entry(
    entries: list[TocTextEntry],
    index: int,
    *,
    page_offset: int,
    total_pages: int,
) -> int:
    entry = entries[index]
    pdf_start = entry.printed_page + page_offset
    depth = _entry_depth(entry)
    for next_index in range(index + 1, len(entries)):
        next_entry = entries[next_index]
        if _entry_depth(next_entry) <= depth:
            return max(
                pdf_start,
                min(next_entry.printed_page + page_offset - 1, total_pages),
            )
    return total_pages


def entries_to_chapters(
    entries: list[TocTextEntry],
    *,
    page_offset: int,
    total_pages: int,
) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        pdf_start = entry.printed_page + page_offset
        pdf_end = _pdf_end_for_entry(
            entries,
            index,
            page_offset=page_offset,
            total_pages=total_pages,
        )
        pdf_start = max(1, min(pdf_start, total_pages))
        pdf_end = max(pdf_start, min(pdf_end, total_pages))
        source_pages = list(range(pdf_start, pdf_end + 1))
        chapters.append(
            {
                "index": index,
                "title": entry.title,
                "start_page": pdf_start,
                "end_page": pdf_end,
                "printed_page": entry.printed_page,
                "source_pages": source_pages,
                "section": entry.section,
            }
        )
    return chapters


def extract_text_toc_from_pdf(
    pdf_path: str,
    *,
    page_start: int | None = None,
    page_end: int | None = None,
    page_offset: int | None = None,
    max_depth: int | None = 1,
) -> dict[str, Any] | None:
    doc = fitz.open(pdf_path)
    try:
        detected_range = None
        if page_start is None or page_end is None:
            detected_range = detect_toc_page_range(doc)
            if detected_range is None:
                return None
            page_start, page_end = detected_range
        page_start = max(1, min(page_start, doc.page_count))
        page_end = max(page_start, min(page_end, doc.page_count))

        text_parts: list[str] = []
        for page_index in range(page_start - 1, page_end):
            text_parts.append(doc[page_index].get_text("text"))
        text = "\n".join(text_parts)
        entries = filter_toc_entries(parse_toc_text(text), max_depth=max_depth)
        if len(entries) < 2:
            return None

        resolved_offset = page_offset
        if resolved_offset is None:
            resolved_offset = infer_page_offset(
                doc,
                entries,
                toc_page_start=page_start,
                toc_page_end=page_end,
            )
        chapters = entries_to_chapters(
            entries,
            page_offset=resolved_offset,
            total_pages=doc.page_count,
        )
        if not chapters:
            return None
        return {
            "chapters": chapters,
            "toc_page_start": page_start,
            "toc_page_end": page_end,
            "page_offset": resolved_offset,
            "entry_count": len(entries),
        }
    finally:
        doc.close()
