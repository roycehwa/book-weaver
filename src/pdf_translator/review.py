from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_SEGMENTS = "translation_review_segments_v1"
SCHEMA_TRANSLATED_SEGMENTS = "translation_review_translated_segments_v1"
SCHEMA_ITEMS = "translation_review_items_v1"
SCHEMA_STATE = "translation_review_state_v1"

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


def create_review_state(review_items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": SCHEMA_STATE,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "summary": {
            "total_items": len(review_items),
            "open_items": len(review_items),
            "approved_items": 0,
            "resolved_items": 0,
        },
        "decisions": {},
    }


def build_review_artifacts(
    *,
    source_path: Path,
    target_language: str,
    book: dict[str, Any],
    translated_chapters: list[dict[str, Any]],
) -> dict[str, Any]:
    segments = build_source_segments(book, source_path=source_path)
    translated_segments = build_translated_segments(
        segments,
        translated_chapters,
        target_language=target_language,
    )
    review_items = detect_review_items(segments, translated_segments, target_language=target_language)
    return {
        "segments": {
            "schema": SCHEMA_SEGMENTS,
            "source_path": str(source_path),
            "segments": segments,
        },
        "translated_segments": {
            "schema": SCHEMA_TRANSLATED_SEGMENTS,
            "target_language": target_language,
            "segments": translated_segments,
        },
        "review_items": {
            "schema": SCHEMA_ITEMS,
            "target_language": target_language,
            "items": review_items,
        },
        "review_state": create_review_state(review_items),
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
        if approved_text:
            segment["translated_text"] = approved_text
        if decision.get("status"):
            segment["status"] = str(decision["status"])
        if decision.get("reviewer_comment"):
            segment["reviewer_comment"] = str(decision["reviewer_comment"])
    return segments


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


def write_review_artifacts(run_dir: Path, artifacts: dict[str, Any]) -> dict[str, str]:
    paths = {
        "segments": run_dir / "segments.json",
        "translated_segments": run_dir / "translated_segments.json",
        "review_items": run_dir / "review_items.json",
        "review_state": run_dir / "review_state.json",
    }
    for key, path in paths.items():
        path.write_text(json.dumps(artifacts[key], ensure_ascii=False, indent=2), encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}


def review_project_from_run(run_dir: Path) -> dict[str, Any]:
    segments = json.loads((run_dir / "segments.json").read_text(encoding="utf-8"))
    translated_segments = json.loads((run_dir / "translated_segments.json").read_text(encoding="utf-8"))
    review_items = json.loads((run_dir / "review_items.json").read_text(encoding="utf-8"))
    review_state = json.loads((run_dir / "review_state.json").read_text(encoding="utf-8"))
    return {
        "run_dir": str(run_dir),
        "segments": _payload_segments(segments),
        "translated_segments": _payload_segments(translated_segments),
        "review_items": _payload_items(review_items),
        "review_state": review_state,
    }


def write_versioned_outputs(
    *,
    run_dir: Path,
    version_name: str,
    target_language: str,
    translated_segments: Any,
    parent_version: str | None = None,
) -> dict[str, str]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", version_name):
        raise ValueError("Unsafe review version name. Use letters, numbers, dots, underscores, or dashes.")
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
                "schema": "translation_review_version_v1",
                "version": version_name,
                "parent_version": parent_version,
                "target_language": target_language,
                "created_at": utc_now(),
                "files": {
                    "translated_markdown": str(translated_markdown_path),
                    "translated_segments": str(translated_segments_path),
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
