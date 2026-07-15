from __future__ import annotations

import re
from typing import Any

from pdf_translator.chapter_kind import classify_chapter, should_translate_chapter
from pdf_translator.chunking import split_markdown_into_chunks


SCHEMA = "bookweaver_chapter_segments_v1"
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def build_chapter_segments(book: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    pages = book.get("pages") if isinstance(book, dict) else []
    pages = pages if isinstance(pages, list) else []
    segments: list[dict[str, Any]] = []
    for fallback_index, chapter in enumerate(book.get("chapters") or [], 1):
        if not isinstance(chapter, dict):
            continue
        if not chapter.get("kind"):
            chapter["kind"] = classify_chapter(chapter, pages=pages)
        chapter_id = str(chapter.get("chapter_id") or f"chapter-{fallback_index:03d}")
        chapter_title = str(chapter.get("title") or f"Chapter {fallback_index}")
        chapter_translate = should_translate_chapter(chapter)
        chapter_markdown = _chapter_markdown(chapter).strip()
        if not chapter_markdown:
            continue
        units = _split_chapter_sections(chapter_markdown) if chapter_translate else [(None, chapter_markdown)]
        segment_in_chapter = 0
        for section_title, section_markdown in units:
            for part in _split_to_max_chars(section_markdown, max_chars):
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
                        "markdown": part.strip(),
                        "role": role,
                        "translate": chapter_translate,
                        "knowledge_eligible": chapter_translate and role not in {"table", "figure"},
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
    if isinstance(payload_or_book.get("chapter_segments"), list):
        return list(payload_or_book.get("chapter_segments") or [])
    return build_chapter_segments(payload_or_book, max_chars=max_chars)["segments"]


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
