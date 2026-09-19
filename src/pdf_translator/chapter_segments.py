from __future__ import annotations

import re
from typing import Any

from pdf_translator.chapter_kind import classify_chapter, should_translate_chapter
from pdf_translator.chunking import split_markdown_into_chunks


SCHEMA = "bookweaver_chapter_segments_v1"
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def build_chapter_segments_from_reading_units(
    reading_units: dict[str, Any],
    *,
    max_chars: int,
) -> dict[str, Any]:
    from pdf_translator.reading_units import SCHEMA as READING_UNITS_SCHEMA, validate_reading_units

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if reading_units.get("schema") != READING_UNITS_SCHEMA:
        raise ValueError("Expected a reading-units payload")
    validate_reading_units(reading_units)
    generation = reading_units.get("generation") or {}
    if generation.get("translation_authority") is not True:
        raise ValueError("Chapter transport segments require user-confirmed reading units")

    chapter_meta = {
        str(chapter.get("chapter_id") or ""): chapter
        for chapter in reading_units.get("chapters") or []
        if isinstance(chapter, dict)
    }
    segments: list[dict[str, Any]] = []
    per_chapter_count: dict[str, int] = {}
    bucket: list[dict[str, Any]] = []
    bucket_chars = 0

    def source_location(units: list[dict[str, Any]]) -> tuple[list[int], str | None]:
        pages: list[int] = []
        internal_path: str | None = None
        for unit in units:
            for span in unit.get("provenance") or []:
                if not isinstance(span, dict):
                    continue
                page = span.get("page_no")
                if isinstance(page, int) and page not in pages:
                    pages.append(page)
                if internal_path is None and isinstance(span.get("resource_path"), str):
                    internal_path = span["resource_path"]
        return pages, internal_path

    def emit(units: list[dict[str, Any]], markdown: str, *, separator_before: str = "\n\n", part: int | None = None) -> None:
        if not units or not markdown.strip():
            return
        first = units[0]
        chapter_id = str(first["chapter_id"])
        meta = chapter_meta.get(chapter_id, {})
        per_chapter_count[chapter_id] = per_chapter_count.get(chapter_id, 0) + 1
        sequence = per_chapter_count[chapter_id]
        pages, internal_path = source_location(units)
        kinds = {str(unit.get("kind") or "paragraph") for unit in units}
        policies = {str(unit.get("policy") or "translate") for unit in units}
        translate = policies == {"translate"} and not kinds.intersection({"figure", "table"})
        role = next(iter(kinds)) if len(kinds) == 1 and next(iter(kinds)) in {"heading", "figure", "table"} else "prose"
        section_title = next(
            (
                heading[1]
                for unit in units
                for heading in [_heading(str(unit.get("markdown") or ""))]
                if heading is not None and heading[0] >= 2
            ),
            None,
        )
        payload: dict[str, Any] = {
            "segment_id": f"{chapter_id}:seg{sequence:04d}",
            "chapter_id": chapter_id,
            "chapter_index": int(first.get("chapter_index") or meta.get("index") or 0),
            "chapter_title": str(meta.get("title") or chapter_id),
            "chapter_kind": str(meta.get("kind") or "narrative"),
            "segment_index": len(segments),
            "segment_index_in_chapter": sequence,
            "section_title": section_title,
            "source_pages": sorted(pages),
            "page_start": min(pages) if pages else None,
            "page_end": max(pages) if pages else None,
            "source_internal_path": internal_path,
            "markdown": markdown.strip(),
            "separator_before": separator_before,
            "preserve_block_structure": True,
            "role": role,
            "translate": translate,
            "knowledge_eligible": translate,
            "unit_ids": [str(unit["unit_id"]) for unit in units],
            "reading_units_fingerprint": reading_units.get("document_fingerprint"),
        }
        if part is not None:
            payload["transport_part"] = part
            payload["transport_part_of"] = str(first["unit_id"])
        segments.append(payload)

    def flush() -> None:
        nonlocal bucket, bucket_chars
        if bucket:
            emit(bucket, "\n\n".join(str(unit["markdown"]) for unit in bucket))
        bucket = []
        bucket_chars = 0

    for unit in reading_units.get("units") or []:
        if not isinstance(unit, dict):
            continue
        markdown = str(unit.get("markdown") or "").strip()
        if not markdown:
            continue
        if bucket and (
            unit.get("chapter_id") != bucket[-1].get("chapter_id")
            or unit.get("policy") != bucket[-1].get("policy")
        ):
            flush()
        if unit.get("kind") == "heading" and any(
            existing.get("kind") != "heading" for existing in bucket
        ):
            flush()
        atomic = unit.get("kind") in {"figure", "table", "code"}
        if atomic:
            flush()
            emit([unit], markdown)
            continue
        if len(markdown) > max_chars:
            flush()
            for part_index, part in enumerate(split_markdown_into_chunks(markdown, max_chars), 1):
                emit([unit], part.markdown, separator_before=part.separator_before, part=part_index)
            continue
        added = len(markdown) + (2 if bucket else 0)
        if bucket and bucket_chars + added > max_chars:
            flush()
            added = len(markdown)
        bucket.append(unit)
        bucket_chars += added
    flush()

    return {
        "schema": SCHEMA,
        "source": "confirmed_reading_units",
        "reading_units_fingerprint": reading_units.get("document_fingerprint"),
        "max_chunk_chars": max_chars,
        "chapter_count": len({segment["chapter_id"] for segment in segments}),
        "segment_count": len(segments),
        "segments": segments,
    }


def build_chapter_segments(book: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    pages = book.get("pages") if isinstance(book, dict) else []
    pages = pages if isinstance(pages, list) else []
    segments: list[dict[str, Any]] = []
    for fallback_index, chapter in enumerate(book.get("chapters") or [], 1):
        if not isinstance(chapter, dict):
            continue
        if not chapter.get("kind"):
            chapter["kind"] = classify_chapter(chapter, pages=pages)
        chapter_id = str(chapter.get("chapter_id") or chapter.get("id") or f"chapter-{fallback_index:03d}")
        chapter_title = str(chapter.get("title") or f"Chapter {fallback_index}")
        chapter_translate = should_translate_chapter(chapter)
        chapter_markdown = _chapter_markdown(chapter).strip()
        if not chapter_markdown:
            continue
        units = _split_chapter_sections(chapter_markdown) if chapter_translate else [(None, chapter_markdown)]
        segment_in_chapter = 0
        for section_title, section_markdown in units:
            parts = [
                (part.markdown, media, part.separator_before)
                for text, media in _separate_source_policies(section_markdown, chapter.get("source_decisions") or [])
                for part in split_markdown_into_chunks(text, max(len(text) + 2, max_chars) if media else max_chars)
            ]
            for part, media, separator_before in parts:
                if not part.strip():
                    continue
                segment_in_chapter += 1
                role = _segment_role(part)
                segments.append(
                    {
                        "segment_id": f"{chapter_id}:seg{segment_in_chapter:04d}",
                        "chapter_id": chapter_id,
                        "chapter_index": int(chapter.get("index") or fallback_index),
                        "chapter_title": chapter_title,
                        "chapter_kind": str(chapter.get("kind") or "narrative"),
                        "segment_index": len(segments),
                        "segment_index_in_chapter": segment_in_chapter,
                        "section_title": section_title,
                        "source_pages": [
                            int(page_no)
                            for page_no in (chapter.get("source_pages") or [])
                            if isinstance(page_no, int) or str(page_no).isdigit()
                        ],
                        "page_start": chapter.get("page_start"),
                        "page_end": chapter.get("page_end"),
                        "source_internal_path": chapter.get("source_internal_path"),
                        "markdown": part.strip(),
                        "separator_before": separator_before,
                        "preserve_block_structure": True,
                        "role": role,
                        "translate": chapter_translate and not media,
                        "knowledge_eligible": chapter_translate and not media,
                    }
                )
    return {
        "schema": SCHEMA,
        "source": "canonical_chapters" if _uses_canonical_chapters(book) else "book_chapters",
        "max_chunk_chars": max_chars,
        "chapter_count": len({segment["chapter_id"] for segment in segments}),
        "segment_count": len(segments),
        "segments": segments,
    }


def chapter_segments_for_translation(payload_or_book: dict[str, Any], *, max_chars: int) -> list[dict[str, Any]]:
    if payload_or_book.get("schema") == SCHEMA:
        return list(payload_or_book.get("segments") or [])
    if payload_or_book.get("schema") == "bookweaver_reading_units_v1":
        return build_chapter_segments_from_reading_units(
            payload_or_book,
            max_chars=max_chars,
        )["segments"]
    if isinstance(payload_or_book.get("chapter_segments"), list):
        return list(payload_or_book.get("chapter_segments") or [])
    # Kept as a pure construction path for reconstruction previews and unit
    # tests. The pipeline entry point rejects translation until confirmed
    # reading units have generated persisted chapter segments.
    return build_chapter_segments(payload_or_book, max_chars=max_chars)["segments"]


def _separate_source_policies(markdown: str, decisions: list[dict]) -> list[tuple[str, bool]]:
    preserved = {str(item["text"]).strip() for item in decisions if item.get("policy") == "preserve"}
    if not preserved:
        return _separate_media(markdown)
    # Match full block boundaries, never replace substrings within prose.
    pattern = re.compile(r"(?<!\S)(?:" + "|".join(re.escape(text) for text in sorted(preserved, key=len, reverse=True)) + r")(?!\S)")
    result, start = [], 0
    for match in pattern.finditer(markdown):
        before, after = markdown[:match.start()], markdown[match.end():]
        if before and not before.endswith("\n\n") or after and not after.startswith("\n\n"):
            continue
        result.extend(_separate_media(markdown[start:match.start()]))
        result.append((match.group(), True))
        start = match.end()
    result.extend(_separate_media(markdown[start:]))
    return result


def _uses_canonical_chapters(book: dict[str, Any]) -> bool:
    metadata = book.get("metadata")
    return isinstance(metadata, dict) and metadata.get("chapter_source") == "user_confirmed_canonical"


def _chapter_markdown(chapter: dict[str, Any]) -> str:
    markdown = str(chapter.get("markdown") or "").strip()
    title = str(chapter.get("title") or "").strip()
    if title and markdown and not markdown.lstrip().startswith("#"):
        return f"# {title}\n\n{markdown}"
    if title and not markdown:
        return f"# {title}"
    return markdown


def _separate_media(markdown: str) -> list[tuple[str, bool]]:
    """Keep image/table bytes out of model input without changing reading order."""
    blocks = _markdown_blocks(markdown)
    result: list[tuple[str, bool]] = []
    text: list[str] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        media = block.startswith(("![", "|"))
        if block.startswith("**Table ") and index + 1 < len(blocks) and blocks[index + 1].startswith("|"):
            block += "\n\n" + blocks[index + 1]
            index += 1
            media = True
        if media:
            if text:
                result.append(("\n\n".join(text), False))
                text = []
            result.append((block, True))
        else:
            text.append(block)
        index += 1
    if text:
        result.append(("\n\n".join(text), False))
    return result


def _split_chapter_sections(markdown: str) -> list[tuple[str | None, str]]:
    blocks = _markdown_blocks(markdown)
    sections: list[tuple[str | None, list[str]]] = []
    current_title: str | None = None
    current_blocks: list[str] = []
    for block in blocks:
        heading = _heading(block)
        if heading and heading[0] >= 2:
            if current_blocks:
                sections.append((current_title, current_blocks))
            current_title = heading[1]
            current_blocks = [block]
            continue
        current_blocks.append(block)
    if current_blocks:
        sections.append((current_title, current_blocks))
    merged: list[tuple[str | None, str]] = []
    for title, parts in sections:
        if not parts:
            continue
        content = "\n\n".join(parts).strip()
        if _is_heading_only_section(content) and merged:
            prev_title, prev_content = merged[-1]
            merged[-1] = (prev_title, f"{prev_content}\n\n{content}".strip())
            continue
        merged.append((title, content))
    return merged


def _is_heading_only_section(content: str) -> bool:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(lines) != 1:
        return False
    return bool(_HEADING_RE.match(lines[0]))


def _split_to_max_chars(markdown: str, max_chars: int) -> list[str]:
    if len(markdown) <= max_chars:
        return [markdown]
    return [chunk.markdown for chunk in split_markdown_into_chunks(markdown, max_chars)]


def _markdown_blocks(markdown: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            current.append(line)
            continue
        if not in_fence and not line.strip():
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return blocks


def _heading(block: str) -> tuple[int, str] | None:
    first_line = block.splitlines()[0].strip() if block.strip() else ""
    match = _HEADING_RE.match(first_line)
    if not match:
        return None
    return len(match.group(1)), match.group(2).strip()


def _segment_role(markdown: str) -> str:
    stripped = markdown.strip()
    if stripped.startswith("!["):
        return "figure"
    if stripped.startswith("|"):
        return "table"
    heading = _heading(markdown)
    if heading and len(_markdown_blocks(markdown)) == 1:
        return "heading"
    return "prose"
