from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any

from pdf_translator.models import TranslationChunk
from pdf_translator.chunking import split_markdown_into_chunks
from pdf_translator.translate import (
    _chapter_markdown_for_translation,
    _chunk_cache_path,
    _is_preserved_apparatus_block,
    _read_chunk_cache,
    _split_markdown_media_segments,
)


SCHEMA_SEGMENTS = "translation_review_segments_v1"
SCHEMA_TRANSLATED_SEGMENTS = "translation_review_translated_segments_v1"
SCHEMA_ITEMS = "translation_review_items_v1"
SCHEMA_STATE = "translation_review_state_v1"
SCHEMA_PRE_REVIEW = "translation_pre_review_v1"
SCHEMA_CHAPTER_MARKS = "translation_review_chapter_marks_v1"

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z'’-]{3,}\b")
URL_OR_EMAIL_RE = re.compile(r"(?:https?://|www\.)\S+|\S+@\S+")
MARKDOWN_LINK_DEST_RE = re.compile(r"\]\([^)]+\)")
ALLOWED_MIXED_LATIN_WORDS = {
    "appendix",
    "chapter",
    "copyright",
    "email",
    "figure",
    "index",
    "license",
    "notes",
    "press",
    "table",
}
MODEL_REFUSAL_PATTERNS = (
    re.compile(r"未在消息中提供"),
    re.compile(r"请提供.*(?:Markdown|markdown|文档)"),
    re.compile(r"please provide.*markdown", re.I),
    re.compile(r"no markdown (?:content|document)", re.I),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def split_review_blocks(markdown: str) -> list[str]:
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
    return [block for block in blocks if block]


def _chapter_key(chapter: dict[str, Any], fallback_index: int) -> str:
    chapter_id = str(chapter.get("chapter_id") or "").strip()
    if chapter_id:
        return chapter_id
    index = int(chapter.get("index") or fallback_index)
    return f"chapter-{index:03d}"


def _chapter_title(chapter: dict[str, Any], fallback_index: int) -> str:
    return str(chapter.get("title") or f"Chapter {chapter.get('index') or fallback_index}")


def _segment_location(chapter: dict[str, Any]) -> dict[str, Any]:
    return {
        "chapter_index": chapter.get("index"),
        "chapter_id": chapter.get("chapter_id"),
        "chapter_title": chapter.get("title"),
        "page_start": chapter.get("page_start"),
        "page_end": chapter.get("page_end"),
        "source_pages": [int(page) for page in chapter.get("source_pages", [])],
        "source_internal_path": chapter.get("source_internal_path"),
    }


def build_source_segments(book: dict[str, Any], *, source_path: Path) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    chapters = book.get("chapters") or []
    for fallback_index, chapter in enumerate(chapters, 1):
        key = _chapter_key(chapter, fallback_index)
        blocks = split_review_blocks(str(chapter.get("markdown") or ""))
        for block_index, block in enumerate(blocks, 1):
            segment_id = f"{key}:s{block_index:03d}"
            segments.append(
                {
                    "segment_id": segment_id,
                    "chapter_id": key,
                    "chapter_index": int(chapter.get("index") or fallback_index),
                    "chapter_title": _chapter_title(chapter, fallback_index),
                    "block_index": block_index,
                    "source_text": block,
                    "source_path": str(source_path),
                    "source_location": _segment_location(chapter),
                    "status": "pending",
                }
            )
    return segments


def build_translated_segments(
    source_segments: list[dict[str, Any]],
    translated_chapters: list[dict[str, Any]],
    *,
    target_language: str,
) -> list[dict[str, Any]]:
    translated_by_chapter: dict[str, list[str]] = {}
    for fallback_index, chapter in enumerate(translated_chapters, 1):
        key = _chapter_key(chapter, fallback_index)
        translated_by_chapter[key] = split_review_blocks(str(chapter.get("markdown") or ""))

    translated_segments: list[dict[str, Any]] = []
    for segment in source_segments:
        chapter_id = str(segment["chapter_id"])
        block_index = int(segment["block_index"])
        chapter_blocks = translated_by_chapter.get(chapter_id, [])
        translated_text = chapter_blocks[block_index - 1] if block_index <= len(chapter_blocks) else ""
        translated_segments.append(
            {
                "segment_id": segment["segment_id"],
                "chapter_id": chapter_id,
                "chapter_index": segment["chapter_index"],
                "chapter_title": segment["chapter_title"],
                "block_index": block_index,
                "translated_text": translated_text,
                "target_language": target_language,
                "source_location": segment.get("source_location", {}),
                "status": "needs_review",
            }
        )
    return translated_segments


def build_aligned_review_segments(
    book: dict[str, Any],
    *,
    source_path: Path,
    target_language: str,
    cache_dir: Path,
    max_chunk_chars: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pair source/translation segments using the same chunk boundaries as translate_book_chapters."""
    source_segments: list[dict[str, Any]] = []
    translated_segments: list[dict[str, Any]] = []
    global_chunk_index = 0

    for fallback_index, chapter in enumerate(book.get("chapters") or [], 1):
        key = _chapter_key(chapter, fallback_index)
        chapter_index = int(chapter.get("index") or fallback_index)
        title = _chapter_title(chapter, fallback_index)
        location = _segment_location(chapter)
        chapter_source_markdown = _chapter_markdown_for_translation(chapter)
        block_index = 0

        def append_segment(source_text: str, translated_text: str) -> None:
            nonlocal block_index
            block_index += 1
            segment_id = f"{key}:c{block_index:03d}"
            base = {
                "segment_id": segment_id,
                "chapter_id": key,
                "chapter_index": chapter_index,
                "chapter_title": title,
                "block_index": block_index,
                "source_location": location,
            }
            source_segments.append(
                {
                    **base,
                    "source_text": source_text,
                    "source_path": str(source_path),
                    "status": "pending",
                }
            )
            translated_segments.append(
                {
                    **base,
                    "translated_text": translated_text,
                    "target_language": target_language,
                    "status": "needs_review",
                }
            )

        if not bool(chapter.get("translate", True)):
            text = chapter_source_markdown.strip()
            if text:
                append_segment(text, text)
            continue

        media_segments = (
            [("media", chapter_source_markdown.strip())]
            if _is_preserved_apparatus_block(chapter_source_markdown)
            else _split_markdown_media_segments(chapter_source_markdown)
        )
        for segment_kind, segment_markdown in media_segments:
            text = segment_markdown.strip()
            if not text:
                continue
            if segment_kind == "media":
                continue
            for chunk in split_markdown_into_chunks(text, max_chunk_chars):
                translation_chunk = TranslationChunk(index=global_chunk_index, markdown=chunk.markdown)
                translated_text = _read_chunk_cache(cache_dir, translation_chunk)
                append_segment(chunk.markdown, translated_text)
                global_chunk_index += 1

    return _merge_reading_review_segments(source_segments, translated_segments)


def _merge_reading_review_segments(
    source_segments: list[dict[str, Any]],
    translated_segments: list[dict[str, Any]],
    *,
    min_chars: int = 1500,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge adjacent translation chunks into longer reading units for human review."""
    if not source_segments:
        return [], []

    translated_by_id = {segment["segment_id"]: segment for segment in translated_segments}
    merged_source: list[dict[str, Any]] = []
    merged_translated: list[dict[str, Any]] = []
    block_index_by_chapter: dict[str, int] = defaultdict(int)

    source_parts: list[str] = []
    translated_parts: list[str] = []
    part_ids: list[str] = []
    active_chapter: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal source_parts, translated_parts, part_ids, active_chapter
        if not active_chapter or not source_parts:
            return
        chapter_id = str(active_chapter["chapter_id"])
        block_index_by_chapter[chapter_id] += 1
        block_index = block_index_by_chapter[chapter_id]
        segment_id = f"{chapter_id}:r{block_index:03d}"
        base = {
            "segment_id": segment_id,
            "chapter_id": chapter_id,
            "chapter_index": active_chapter["chapter_index"],
            "chapter_title": active_chapter["chapter_title"],
            "block_index": block_index,
            "source_location": active_chapter["source_location"],
            "translation_part_ids": list(part_ids),
        }
        merged_source.append(
            {
                **base,
                "source_text": "\n\n".join(source_parts),
                "source_path": active_chapter["source_path"],
                "status": "pending",
            }
        )
        merged_translated.append(
            {
                **base,
                "translated_text": "\n\n".join(part for part in translated_parts if part.strip()),
                "target_language": active_chapter["target_language"],
                "status": "needs_review",
            }
        )
        source_parts = []
        translated_parts = []
        part_ids = []
        active_chapter = None

    for source in source_segments:
        chapter_id = str(source["chapter_id"])
        if active_chapter and chapter_id != str(active_chapter["chapter_id"]):
            flush()

        if active_chapter is None:
            translated = translated_by_id.get(str(source["segment_id"]), {})
            active_chapter = {
                "chapter_id": chapter_id,
                "chapter_index": source["chapter_index"],
                "chapter_title": source["chapter_title"],
                "source_location": source.get("source_location"),
                "source_path": source.get("source_path"),
                "target_language": translated.get("target_language"),
            }

        translated = translated_by_id.get(str(source["segment_id"]), {})
        source_parts.append(str(source.get("source_text") or ""))
        translated_parts.append(str(translated.get("translated_text") or ""))
        part_ids.append(str(source["segment_id"]))

        combined_len = sum(len(part) for part in source_parts)
        if combined_len >= min_chars:
            flush()

    flush()
    return merged_source, merged_translated


def _ascii_letter_count(text: str) -> int:
    return sum(1 for char in text if char.isascii() and char.isalpha())


def _cjk_count(text: str) -> int:
    return len(CJK_RE.findall(text))


def _mixed_english_signal(text: str) -> tuple[int, int]:
    suspect_words = 0
    mixed_lines = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "![", "|", ">", "```")):
            continue
        if not CJK_RE.search(line):
            continue
        cleaned = URL_OR_EMAIL_RE.sub(" ", line)
        cleaned = MARKDOWN_LINK_DEST_RE.sub("]", cleaned)
        line_suspects = 0
        for match in LATIN_WORD_RE.finditer(cleaned):
            original = match.group(0)
            word = original.strip("'’”-").lower()
            if not word or word in ALLOWED_MIXED_LATIN_WORDS:
                continue
            if original.isupper() and len(original) <= 8:
                continue
            if original[:1].isupper() and original[1:].islower():
                continue
            if any(char.islower() for char in original):
                line_suspects += 1
        if line_suspects:
            mixed_lines += 1
            suspect_words += line_suspects
    return suspect_words, mixed_lines


def detect_review_items(
    source_segments: list[dict[str, Any]],
    translated_segments: list[dict[str, Any]],
    *,
    target_language: str,
) -> list[dict[str, Any]]:
    translated_by_id = {segment["segment_id"]: segment for segment in translated_segments}
    items: list[dict[str, Any]] = []
    for source in source_segments:
        segment_id = str(source["segment_id"])
        translated = translated_by_id.get(segment_id, {})
        translated_text = str(translated.get("translated_text") or "").strip()
        issue_type: str | None = None
        severity = "medium"
        evidence: dict[str, Any] = {}

        if not translated_text:
            issue_type = "missing_translation"
            severity = "high"
        elif target_language.lower().startswith("zh"):
            source_ascii = _ascii_letter_count(str(source.get("source_text") or ""))
            translated_ascii = _ascii_letter_count(translated_text)
            translated_cjk = _cjk_count(translated_text)
            mixed_words, mixed_lines = _mixed_english_signal(translated_text)
            evidence = {
                "source_ascii": source_ascii,
                "translated_ascii": translated_ascii,
                "translated_cjk": translated_cjk,
                "mixed_english_words": mixed_words,
                "mixed_english_lines": mixed_lines,
            }
            if source_ascii >= 80 and translated_ascii > 80 and translated_cjk < 20:
                issue_type = "untranslated"
                severity = "high"
            elif source_ascii >= 500 and translated_cjk + int(translated_ascii * 0.35) < source_ascii * 0.16:
                issue_type = "possibly_incomplete"
                severity = "high"
            elif mixed_words >= 2 or mixed_lines >= 1:
                issue_type = "mixed_english"
                severity = "medium"

        if issue_type is None:
            continue
        items.append(
            {
                "item_id": f"review-{len(items) + 1:04d}",
                "segment_id": segment_id,
                "issue_type": issue_type,
                "severity": severity,
                "status": "open",
                "chapter_id": source.get("chapter_id"),
                "chapter_index": source.get("chapter_index"),
                "chapter_title": source.get("chapter_title"),
                "block_index": source.get("block_index"),
                "source_location": source.get("source_location", {}),
                "evidence": evidence,
            }
        )
    return items


def build_pre_review_report(
    segments: list[dict[str, Any]],
    review_items: list[dict[str, Any]],
    *,
    method: str = "rules_v1",
) -> dict[str, Any]:
    """Machine pre-review: audit source/translation pairs and flag questionable segments."""
    issue_counts: dict[str, int] = defaultdict(int)
    flagged_segment_ids: list[str] = []
    for item in review_items:
        issue_counts[str(item.get("issue_type") or "unknown")] += 1
        segment_id = str(item.get("segment_id") or "").strip()
        if segment_id:
            flagged_segment_ids.append(segment_id)
    total_segments = len(segments)
    flagged_segments = len(flagged_segment_ids)
    return {
        "schema": SCHEMA_PRE_REVIEW,
        "status": "completed",
        "completed_at": utc_now(),
        "method": method,
        "total_segments": total_segments,
        "flagged_segments": flagged_segments,
        "clean_segments": max(total_segments - flagged_segments, 0),
        "issue_counts": dict(sorted(issue_counts.items())),
        "flagged_segment_ids": flagged_segment_ids,
    }


def create_review_state(review_items: list[dict[str, Any]]) -> dict[str, Any]:
    flagged_count = len(review_items)
    return {
        "schema": SCHEMA_STATE,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "summary": {
            "total_items": flagged_count,
            "open_items": flagged_count,
            "approved_items": 0,
            "resolved_items": 0,
        },
        "workflow": {
            "pre_review_completed": True,
            "human_review_mode": "issues_only" if flagged_count else "full",
        },
        "decisions": {},
    }


def build_review_artifacts(
    *,
    source_path: Path,
    target_language: str,
    book: dict[str, Any],
    translated_chapters: list[dict[str, Any]],
    cache_dir: Path | None = None,
    max_chunk_chars: int = 9000,
) -> dict[str, Any]:
    if cache_dir is not None and cache_dir.exists():
        segments, translated_segments_list = build_aligned_review_segments(
            book,
            source_path=source_path,
            target_language=target_language,
            cache_dir=cache_dir,
            max_chunk_chars=max_chunk_chars,
        )
    else:
        segments = build_source_segments(book, source_path=source_path)
        translated_segments_list = build_translated_segments(
            segments,
            translated_chapters,
            target_language=target_language,
        )
    review_items = detect_review_items(segments, translated_segments_list, target_language=target_language)
    pre_review = build_pre_review_report(segments, review_items)
    return {
        "segments": {
            "schema": SCHEMA_SEGMENTS,
            "source_path": str(source_path),
            "alignment": "reading_units" if cache_dir is not None and cache_dir.exists() else "paragraph_blocks",
            "segments": segments,
        },
        "translated_segments": {
            "schema": SCHEMA_TRANSLATED_SEGMENTS,
            "target_language": target_language,
            "alignment": "reading_units" if cache_dir is not None and cache_dir.exists() else "paragraph_blocks",
            "segments": translated_segments_list,
        },
        "review_items": {
            "schema": SCHEMA_ITEMS,
            "target_language": target_language,
            "items": review_items,
        },
        "review_state": create_review_state(review_items),
        "pre_review": pre_review,
        "chapter_marks": {
            "schema": SCHEMA_CHAPTER_MARKS,
            "marks": [],
        },
    }


def _payload_segments(payload_or_segments: Any) -> list[dict[str, Any]]:
    if isinstance(payload_or_segments, dict):
        value = payload_or_segments.get("segments", [])
    else:
        value = payload_or_segments
    return [dict(item) for item in value]


def _payload_items(payload_or_items: Any) -> list[dict[str, Any]]:
    if isinstance(payload_or_items, dict):
        value = payload_or_items.get("items", [])
    else:
        value = payload_or_items
    return [dict(item) for item in value]


def apply_review_state(translated_segments_payload: Any, review_state: dict[str, Any]) -> list[dict[str, Any]]:
    segments = _payload_segments(translated_segments_payload)
    decisions = review_state.get("decisions", {})
    if not isinstance(decisions, dict):
        return segments
    for segment in segments:
        decision = decisions.get(segment["segment_id"])
        if not isinstance(decision, dict):
            continue
        approved_text = str(decision.get("approved_text") or "").strip()
        if approved_text and decision.get("status") in {"approved", "resolved"}:
            segment["translated_text"] = approved_text
        if decision.get("status"):
            segment["status"] = str(decision["status"])
        if decision.get("reviewer_comment"):
            segment["reviewer_comment"] = str(decision["reviewer_comment"])
    return segments


def _rewrite_prompt(source_text: str, current_translation: str, reviewer_comment: str) -> str:
    source = source_text.strip()
    comment = reviewer_comment.strip()
    current = current_translation.strip()
    current_section = current if current else "(none — translate the source text from scratch)"
    return (
        "Rewrite this translation according to the reviewer instruction.\n"
        "Return only the revised target-language text. Do not include explanations or placeholders.\n\n"
        "SOURCE TEXT:\n"
        f"{source}\n\n"
        "CURRENT TRANSLATION:\n"
        f"{current_section}\n\n"
        "REVIEWER INSTRUCTION:\n"
        f"{comment}"
    )


def _cjk_count(text: str) -> int:
    return sum(1 for char in text if "\u4e00" <= char <= "\u9fff")


def _looks_like_model_refusal(text: str) -> bool:
    return any(pattern.search(text) for pattern in MODEL_REFUSAL_PATTERNS)


def _translate_missing_segment(
    *,
    translator: Any,
    index: int,
    source_text: str,
    reviewer_comment: str,
    source_language: str | None,
    target_language: str,
) -> str:
    source = source_text.strip()
    if not source:
        return ""
    resolved_source_language = source_language or "en"
    chunk = TranslationChunk(index=index, markdown=source)
    candidate = translator.translate_chunk(
        chunk=chunk,
        source_language=resolved_source_language,
        target_language=target_language,
    ).strip()
    if _is_valid_rewrite_candidate(source, candidate, target_language):
        return candidate
    if not reviewer_comment:
        return candidate
    retry_prompt = _rewrite_prompt(source, "", reviewer_comment)
    return translator.translate_chunk(
        TranslationChunk(index=index, markdown=retry_prompt),
        source_language=resolved_source_language,
        target_language=target_language,
    ).strip()


def _is_valid_rewrite_candidate(source_text: str, candidate: str, target_language: str) -> bool:
    text = candidate.strip()
    if not text:
        return False
    if _looks_like_model_refusal(text):
        return False
    normalized = text.lower().strip("[]")
    if normalized in {"missing translation", "missing transalation"}:
        return False
    if target_language.lower().startswith("zh") and LATIN_WORD_RE.search(source_text):
        if not CJK_RE.search(text):
            return False
        source_letters = sum(1 for char in source_text if char.isascii() and char.isalpha())
        if source_letters >= 40 and _cjk_count(text) < max(12, source_letters // 8):
            return False
    return True


def rewrite_review_requests(
    *,
    run_dir: Path,
    translator: Any,
    source_language: str | None,
    target_language: str,
    segment_id: str | None = None,
) -> dict[str, Any]:
    project = review_project_from_run(run_dir)
    source_by_id = {segment["segment_id"]: segment for segment in project["segments"]}
    translated_by_id = {segment["segment_id"]: segment for segment in project["translated_segments"]}
    state = dict(project["review_state"])
    decisions = state.setdefault("decisions", {})
    rewritten_count = 0

    for current_segment_id, decision in list(decisions.items()):
        if segment_id is not None and current_segment_id != segment_id:
            continue
        if not isinstance(decision, dict):
            continue
        if decision.get("action") != "model_rewrite" or decision.get("status") not in {"open", "requested"}:
            continue
        source = source_by_id.get(current_segment_id)
        translated = translated_by_id.get(current_segment_id)
        if source is None or translated is None:
            continue
        reviewer_comment = str(decision.get("reviewer_comment") or "").strip()
        if not reviewer_comment:
            continue
        source_text = str(source.get("source_text") or "").strip()
        current_translation = str(translated.get("translated_text") or "").strip()
        if not source_text:
            decision["rewrite_error"] = "Source text is empty; cannot model-rewrite this segment."
            continue
        if not current_translation:
            candidate = _translate_missing_segment(
                translator=translator,
                index=rewritten_count,
                source_text=source_text,
                reviewer_comment=reviewer_comment,
                source_language=source_language,
                target_language=target_language,
            )
        else:
            prompt = _rewrite_prompt(source_text, current_translation, reviewer_comment)
            candidate = translator.translate_chunk(
                TranslationChunk(index=rewritten_count, markdown=prompt),
                source_language=source_language or "en",
                target_language=target_language,
            ).strip()
        if not _is_valid_rewrite_candidate(source_text, candidate, target_language):
            decision["rewrite_error"] = "Model returned an invalid rewrite candidate; segment remains open."
            continue
        decision.pop("rewrite_error", None)
        decision["status"] = "candidate"
        decision["approved_text"] = candidate
        decision["model_generated_at"] = utc_now()
        decision["model"] = getattr(translator, "name", "unknown")
        rewritten_count += 1

    state["updated_at"] = utc_now()
    (run_dir / "review_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"rewritten_count": rewritten_count, "review_state_path": str(run_dir / "review_state.json")}


def translated_segments_to_markdown(translated_segments_payload: Any) -> str:
    segments = _payload_segments(translated_segments_payload)
    grouped: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        key = (
            int(segment.get("chapter_index") or 0),
            str(segment.get("chapter_id") or ""),
            str(segment.get("chapter_title") or "Chapter"),
        )
        grouped[key].append(segment)

    chapter_parts: list[str] = []
    for (_, _, title), chapter_segments in sorted(grouped.items(), key=lambda item: item[0]):
        body = "\n\n".join(
            str(segment.get("translated_text") or "").strip()
            for segment in sorted(chapter_segments, key=lambda item: int(item.get("block_index") or 0))
            if str(segment.get("translated_text") or "").strip()
        ).strip()
        if title and not body.startswith("#"):
            chapter_parts.append(f"# {title}\n\n{body}".strip())
        else:
            chapter_parts.append(body)
    return "\n\n".join(part for part in chapter_parts if part).strip() + "\n"


def translated_segments_to_chapters(translated_segments_payload: Any) -> list[dict[str, Any]]:
    segments = _payload_segments(translated_segments_payload)
    grouped: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        key = (
            int(segment.get("chapter_index") or 0),
            str(segment.get("chapter_id") or ""),
            str(segment.get("chapter_title") or "Chapter"),
        )
        grouped[key].append(segment)

    chapters: list[dict[str, Any]] = []
    for (chapter_index, chapter_id, title), chapter_segments in sorted(grouped.items(), key=lambda item: item[0]):
        markdown = "\n\n".join(
            str(segment.get("translated_text") or "").strip()
            for segment in sorted(chapter_segments, key=lambda item: int(item.get("block_index") or 0))
            if str(segment.get("translated_text") or "").strip()
        ).strip()
        first = chapter_segments[0] if chapter_segments else {}
        chapters.append(
            {
                "index": chapter_index,
                "chapter_id": chapter_id or None,
                "title": title,
                "page_start": (first.get("source_location") or {}).get("page_start"),
                "page_end": (first.get("source_location") or {}).get("page_end"),
                "source_pages": (first.get("source_location") or {}).get("source_pages", []),
                "source_internal_path": (first.get("source_location") or {}).get("source_internal_path"),
                "markdown": markdown + "\n" if markdown else "",
                "toc": True,
            }
        )
    return chapters


def _segment_index_map(segments: list[dict[str, Any]]) -> dict[str, int]:
    return {str(segment["segment_id"]): index for index, segment in enumerate(segments)}


def empty_chapter_marks_payload() -> dict[str, Any]:
    return {"schema": SCHEMA_CHAPTER_MARKS, "marks": []}


def load_chapter_marks(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "review_chapter_marks.json"
    if not path.exists():
        return empty_chapter_marks_payload()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return empty_chapter_marks_payload()
    marks = payload.get("marks", [])
    if not isinstance(marks, list):
        marks = []
    return {"schema": SCHEMA_CHAPTER_MARKS, "marks": [dict(item) for item in marks]}


def save_chapter_marks(run_dir: Path, payload: dict[str, Any]) -> Path:
    path = run_dir / "review_chapter_marks.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def add_review_chapter_mark(
    *,
    run_dir: Path,
    segments: list[dict[str, Any]],
    segment_id: str,
    chapter_title: str,
    mark_id: str | None = None,
) -> dict[str, Any]:
    import uuid

    index_by_id = _segment_index_map(segments)
    if segment_id not in index_by_id:
        raise ValueError(f"Unknown review segment: {segment_id}")
    title = chapter_title.strip()
    if not title:
        raise ValueError("chapter_title is required")
    payload = load_chapter_marks(run_dir)
    marks = payload["marks"]
    for existing in marks:
        if existing.get("segment_id") == segment_id:
            existing["chapter_title"] = title
            existing["updated_at"] = utc_now()
            save_chapter_marks(run_dir, payload)
            return payload
    marks.append(
        {
            "mark_id": mark_id or str(uuid.uuid4()),
            "segment_id": segment_id,
            "chapter_title": title,
            "segment_index": index_by_id[segment_id],
            "created_at": utc_now(),
        }
    )
    marks.sort(key=lambda item: int(item.get("segment_index") or index_by_id.get(str(item.get("segment_id")), 0)))
    save_chapter_marks(run_dir, payload)
    return payload


def remove_review_chapter_mark(*, run_dir: Path, mark_id: str) -> dict[str, Any]:
    payload = load_chapter_marks(run_dir)
    marks = payload["marks"]
    filtered = [item for item in marks if str(item.get("mark_id")) != mark_id]
    if len(filtered) == len(marks):
        raise ValueError(f"Chapter mark not found: {mark_id}")
    payload["marks"] = filtered
    save_chapter_marks(run_dir, payload)
    return payload


def build_chapter_groups_from_marks(
    segments: list[dict[str, Any]],
    marks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build chapter ranges using user segment marks."""
    if not segments or not marks:
        return []

    index_by_id = _segment_index_map(segments)
    boundary_indexes: list[int] = []
    titles_by_index: dict[int, str] = {}
    for mark in marks:
        segment_id = str(mark.get("segment_id") or "").strip()
        if segment_id not in index_by_id:
            continue
        index = index_by_id[segment_id]
        if index in titles_by_index:
            titles_by_index[index] = str(mark.get("chapter_title") or titles_by_index[index]).strip()
            continue
        boundary_indexes.append(index)
        titles_by_index[index] = str(mark.get("chapter_title") or "").strip() or display_chapter_title_from_segment(segments[index])
    if not boundary_indexes:
        return []

    boundary_indexes = sorted(set(boundary_indexes))
    groups: list[dict[str, Any]] = []
    for idx, start in enumerate(boundary_indexes):
        end = boundary_indexes[idx + 1] if idx + 1 < len(boundary_indexes) else len(segments)
        groups.append(
            {
                "chapter_id": f"user-mark-{start:04d}",
                "display_title": titles_by_index[start],
                "first_segment_index": start,
                "segment_count": end - start,
                "is_user_mark": True,
                "mark_segment_id": segments[start]["segment_id"],
            }
        )
    return groups


def display_chapter_title_from_segment(segment: dict[str, Any]) -> str:
    raw = str(segment.get("chapter_title") or "").strip()
    if raw and not raw.lower().startswith("untitled section"):
        return raw
    source = str(segment.get("source_text") or "")
    heading = re.search(r"^#{1,3}\s+(.+)$", source, re.M)
    if heading:
        return heading.group(1).strip()
    return raw or f"章节 {segment.get('chapter_index') or ''}"


def update_review_workflow(run_dir: Path, *, human_review_mode: str) -> dict[str, Any]:
    if human_review_mode not in {"issues_only", "full"}:
        raise ValueError("human_review_mode must be issues_only or full")
    state_path = run_dir / "review_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    workflow = state.setdefault("workflow", {})
    if not isinstance(workflow, dict):
        workflow = {}
        state["workflow"] = workflow
    workflow["human_review_mode"] = human_review_mode
    workflow["pre_review_completed"] = True
    state["updated_at"] = utc_now()
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def write_review_artifacts(run_dir: Path, artifacts: dict[str, Any]) -> dict[str, str]:
    paths = {
        "segments": run_dir / "segments.json",
        "translated_segments": run_dir / "translated_segments.json",
        "review_items": run_dir / "review_items.json",
        "review_state": run_dir / "review_state.json",
        "pre_review": run_dir / "pre_review.json",
        "chapter_marks": run_dir / "review_chapter_marks.json",
    }
    for key, path in paths.items():
        if key not in artifacts:
            continue
        path.write_text(json.dumps(artifacts[key], ensure_ascii=False, indent=2), encoding="utf-8")
    return {key: str(path) for key, path in paths.items() if key in artifacts}


def review_project_from_run(run_dir: Path) -> dict[str, Any]:
    segments = json.loads((run_dir / "segments.json").read_text(encoding="utf-8"))
    translated_segments = json.loads((run_dir / "translated_segments.json").read_text(encoding="utf-8"))
    review_items = json.loads((run_dir / "review_items.json").read_text(encoding="utf-8"))
    review_state = json.loads((run_dir / "review_state.json").read_text(encoding="utf-8"))
    pre_review_path = run_dir / "pre_review.json"
    if pre_review_path.exists():
        pre_review = json.loads(pre_review_path.read_text(encoding="utf-8"))
    else:
        segment_list = _payload_segments(segments)
        item_list = _payload_items(review_items)
        pre_review = build_pre_review_report(segment_list, item_list)
    return {
        "run_dir": str(run_dir),
        "segments": _payload_segments(segments),
        "translated_segments": _payload_segments(translated_segments),
        "review_items": _payload_items(review_items),
        "review_state": review_state,
        "pre_review": pre_review,
        "chapter_marks": load_chapter_marks(run_dir),
    }


def write_versioned_outputs(
    *,
    run_dir: Path,
    version_name: str,
    target_language: str,
    translated_segments: Any,
    parent_version: str | None = None,
    approval_status: str = "draft",
) -> dict[str, str]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", version_name):
        raise ValueError("Unsafe review version name. Use letters, numbers, dots, underscores, or dashes.")
    if approval_status not in {"draft", "approved"}:
        raise ValueError("approval_status must be draft or approved")
    version_dir = run_dir / "versions" / version_name
    version_dir.mkdir(parents=True, exist_ok=True)
    translated_markdown = translated_segments_to_markdown(translated_segments)
    translated_markdown_path = version_dir / "translated.md"
    translated_segments_path = version_dir / "translated_segments.json"
    manifest_path = version_dir / "version-manifest.json"

    translated_markdown_path.write_text(translated_markdown, encoding="utf-8")
    translated_segments_path.write_text(
        json.dumps(
            {
                "schema": SCHEMA_TRANSLATED_SEGMENTS,
                "target_language": target_language,
                "segments": _payload_segments(translated_segments),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "translation_review_version_v2",
                "version": version_name,
                "parent_version": parent_version,
                "target_language": target_language,
                "created_at": utc_now(),
                "review": {
                    "status": approval_status,
                    "approved_at": utc_now() if approval_status == "approved" else None,
                },
                "files": {
                    "translated_markdown": str(translated_markdown_path.relative_to(run_dir)),
                    "translated_segments": str(translated_segments_path.relative_to(run_dir)),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "version_dir": str(version_dir),
        "translated_markdown_path": str(translated_markdown_path),
        "translated_segments_path": str(translated_segments_path),
        "manifest_path": str(manifest_path),
    }
