"""Format-neutral logical reading units used between ingest and translation.

Intake writes a preview projection from BookIR.  User confirmation freezes the
same schema as the authority for transport segmentation.  PDF nodes and EPUB
DOM blocks add precise spans where the adapter can prove them; other units keep
coarser chapter or resource provenance instead of overstating precision.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pdf_translator.chapter_kind import classify_chapter, should_translate_chapter


SCHEMA = "bookweaver_reading_units_v1"
UNIT_KINDS = frozenset({"paragraph", "heading", "list", "quote", "figure", "table", "code", "note"})
BOUNDARY_KINDS = frozenset({"chapter", "paragraph", "continuation", "uncertain"})
POLICIES = frozenset({"translate", "preserve", "exclude"})
PROVENANCE_PRECISIONS = frozenset({"chapter", "resource", "dom", "span"})


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def split_logical_markdown_blocks(markdown: str) -> list[str]:
    """Split reconstructed Markdown without breaking fenced blocks."""
    blocks: list[str] = []
    current: list[str] = []
    fence: str | None = None
    for line in markdown.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            value = marker.group(1)
            if fence and value[0] == fence[0] and len(value) >= len(fence):
                fence = None
            elif fence is None:
                fence = value
        if not line.strip() and fence is None:
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def _unit_kind(markdown: str) -> str:
    stripped = markdown.lstrip()
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if re.match(r"^#{1,6}\s+\S", stripped):
        return "heading"
    if stripped.startswith("!["):
        return "figure"
    if stripped.startswith(("```", "~~~")):
        return "code"
    if lines and all(line.startswith("|") and line.endswith("|") for line in lines):
        return "table"
    if stripped.startswith(">"):
        return "quote"
    if re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)", stripped):
        return "list"
    return "paragraph"


def _chapter_policy(chapter: dict[str, Any]) -> str:
    explicit = str(chapter.get("action") or chapter.get("policy") or "").strip().lower()
    if explicit in POLICIES:
        return explicit
    if bool(chapter.get("preserve_original")) and bool(chapter.get("resource_only")):
        return "preserve"
    if chapter.get("translate") is False:
        return "preserve" if bool(chapter.get("preserve_original")) else "exclude"
    return "translate" if should_translate_chapter(chapter) else "preserve"


def _unit_policy(chapter: dict[str, Any], block: str, chapter_policy: str) -> str:
    for decision in chapter.get("source_decisions") or []:
        if not isinstance(decision, dict):
            continue
        if str(decision.get("text") or "").strip() != block.strip():
            continue
        policy = str(decision.get("policy") or "").strip().lower()
        if policy in POLICIES:
            return policy
    return chapter_policy


def _source_spans(chapter: dict[str, Any], source_format: str) -> list[dict[str, Any]]:
    if source_format == "epub":
        internal_paths = [
            str(path).replace("\\", "/")
            for path in (
                chapter.get("source_internal_paths")
                or [chapter.get("source_internal_path")]
            )
            if isinstance(path, str) and path.strip()
        ]
        if internal_paths:
            return [{
                "source_format": "epub",
                "resource_path": internal_path,
                "precision": "resource",
            } for internal_path in dict.fromkeys(internal_paths)]
        return [{"source_format": "epub", "precision": "chapter"}]

    pages: list[int] = []
    for raw_page in chapter.get("source_pages") or []:
        try:
            page = int(raw_page)
        except (TypeError, ValueError):
            continue
        if page > 0 and page not in pages:
            pages.append(page)
    if pages:
        return [
            {"source_format": "pdf", "page_no": page, "precision": "chapter"}
            for page in pages
        ]
    return [{"source_format": "pdf", "precision": "chapter"}]


def _pdf_item_span(item: dict[str, Any]) -> dict[str, Any] | None:
    page_no = item.get("page_no")
    node_id = item.get("source_node_id")
    char_start = item.get("source_char_start")
    char_end = item.get("source_char_end")
    if (
        not isinstance(page_no, int)
        or not isinstance(node_id, str)
        or not node_id
        or not isinstance(char_start, int)
        or not isinstance(char_end, int)
        or char_start < 0
        or char_end <= char_start
    ):
        return None
    return {
        "source_format": "pdf",
        "page_no": page_no,
        "source_node_id": node_id,
        "char_start": char_start,
        "char_end": char_end,
        "precision": "span",
    }


def _pdf_block_span_candidates(
    book: dict[str, Any],
    chapter: dict[str, Any],
) -> dict[str, list[list[dict[str, Any]]]]:
    chapter_pages = {
        int(page)
        for page in chapter.get("source_pages") or []
        if isinstance(page, int) or (isinstance(page, str) and page.isdigit())
    }
    by_text: dict[str, list[list[dict[str, Any]]]] = {}
    pages_by_no = {
        int(page["page_no"]): page
        for page in book.get("pages") or []
        if isinstance(page, dict) and isinstance(page.get("page_no"), int)
    }
    item_by_location: dict[tuple[int, str, int, int], dict[str, Any]] = {}
    for page_no in sorted(chapter_pages):
        for item in pages_by_no.get(page_no, {}).get("content_items") or []:
            if not isinstance(item, dict):
                continue
            span = _pdf_item_span(item)
            text = str(item.get("text") or "").strip()
            if span is None or not text:
                continue
            by_text.setdefault(text, []).append([span])
            item_by_location[(page_no, span["source_node_id"], span["char_start"], span["char_end"])] = item

    for continuation in book.get("logical_continuations") or []:
        if not isinstance(continuation, dict):
            continue
        from_page = continuation.get("from_page")
        to_page = continuation.get("to_page")
        if from_page not in chapter_pages or to_page not in chapter_pages:
            continue
        node_id = continuation.get("source_node_id")
        left_node = str(continuation.get("left_source_node_id") or node_id or "")
        right_node = str(continuation.get("right_source_node_id") or node_id or "")
        left_end = continuation.get("source_char_end")
        right_start = continuation.get("next_source_char_start")
        if not left_node or not right_node or not isinstance(left_end, int) or not isinstance(right_start, int):
            continue
        left = next(
            (
                item
                for (page_no, item_node, _start, end), item in item_by_location.items()
                if page_no == from_page and item_node == left_node and end == left_end
            ),
            None,
        )
        right = next(
            (
                item
                for (page_no, item_node, start, _end), item in item_by_location.items()
                if page_no == to_page and item_node == right_node and start == right_start
            ),
            None,
        )
        spans = [span for item in (left, right) if item is not None for span in [_pdf_item_span(item)] if span]
        joined = str(continuation.get("joined_text") or "").strip()
        if joined and len(spans) == 2:
            by_text.setdefault(joined, []).insert(0, spans)
    return by_text


def _epub_block_span_candidates(chapter: dict[str, Any]) -> dict[str, list[list[dict[str, Any]]]]:
    by_text: dict[str, list[list[dict[str, Any]]]] = {}
    fallback_resource = str(chapter.get("source_internal_path") or "").replace("\\", "/")
    for unit in chapter.get("dom_units") or []:
        if not isinstance(unit, dict):
            continue
        markdown = str(unit.get("markdown") or "").strip()
        resource_path = str(unit.get("resource_path") or fallback_resource).replace("\\", "/")
        dom_path = str(unit.get("dom_path") or "")
        char_start = unit.get("char_start")
        char_end = unit.get("char_end")
        if (
            not markdown
            or not resource_path
            or not dom_path
            or not isinstance(char_start, int)
            or not isinstance(char_end, int)
            or char_start < 0
            or char_end < char_start
        ):
            continue
        span: dict[str, Any] = {
            "source_format": "epub",
            "resource_path": resource_path,
            "dom_path": dom_path,
            "char_start": char_start,
            "char_end": char_end,
            "precision": "dom",
        }
        if unit.get("element_id"):
            span["element_id"] = str(unit["element_id"])
        link_targets = [str(target) for target in unit.get("link_targets") or [] if str(target)]
        if link_targets:
            span["link_targets"] = link_targets
        by_text.setdefault(markdown, []).append([span])
    return by_text


def _chapter_id(chapter: dict[str, Any], fallback_index: int) -> str:
    value = str(chapter.get("chapter_id") or chapter.get("id") or "").strip()
    return value or f"chapter-{fallback_index:03d}"


def logical_chapter_markdown(chapter: dict[str, Any], fallback_index: int = 1) -> str:
    """Return the chapter text as it appears in the logical reading flow."""
    markdown = str(chapter.get("markdown") or "").strip()
    title = str(chapter.get("title") or f"Chapter {fallback_index}").strip()
    if title and markdown and not markdown.lstrip().startswith("#"):
        return f"# {title}\n\n{markdown}"
    if title and not markdown:
        return f"# {title}"
    return markdown


def build_reading_units(
    book: dict[str, Any],
    *,
    source_path: Path,
    translation_authority: bool = False,
) -> dict[str, Any]:
    """Project reconstructed BookIR into the stable reading-unit contract.

    Intake projections remain non-authoritative.  After the user confirms the
    reconstructed source, chapters, and content policies, callers may freeze
    the same schema with ``translation_authority=True`` for transport planning.
    """
    source_format = source_path.suffix.lower().lstrip(".")
    if source_format not in {"pdf", "epub"}:
        raise ValueError(f"Unsupported reading-unit source format: {source_format!r}")

    units: list[dict[str, Any]] = []
    chapters: list[dict[str, Any]] = []
    for fallback_index, chapter in enumerate(book.get("chapters") or [], 1):
        if not isinstance(chapter, dict):
            continue
        chapter_id = _chapter_id(chapter, fallback_index)
        chapter_index = int(chapter.get("index") or fallback_index)
        chapter_title = str(chapter.get("title") or f"Chapter {fallback_index}")
        markdown = logical_chapter_markdown(chapter, fallback_index)
        blocks = split_logical_markdown_blocks(markdown)
        policy = _chapter_policy(chapter)
        chapter_kind = classify_chapter(chapter, pages=book.get("pages") or [])
        pdf_span_candidates = (
            _pdf_block_span_candidates(book, chapter)
            if source_format == "pdf"
            else {}
        )
        epub_span_candidates = (
            _epub_block_span_candidates(chapter)
            if source_format == "epub"
            else {}
        )
        continuation_boundaries = {
            str(continuation.get("joined_text") or "").strip(): continuation
            for continuation in book.get("logical_continuations") or []
            if isinstance(continuation, dict) and str(continuation.get("joined_text") or "").strip()
        }
        unit_ids: list[str] = []
        for block_index, block in enumerate(blocks, 1):
            unit_id = f"{chapter_id}:unit{block_index:05d}"
            unit_ids.append(unit_id)
            exact_candidates = (
                pdf_span_candidates.get(block)
                or epub_span_candidates.get(block)
                or []
            )
            provenance = exact_candidates.pop(0) if exact_candidates else _source_spans(chapter, source_format)
            boundary_before = "chapter" if block_index == 1 else "paragraph"
            if block_index > 1 and block.strip() in continuation_boundaries:
                boundary_before = "continuation"
            units.append({
                "unit_id": unit_id,
                "chapter_id": chapter_id,
                "chapter_index": chapter_index,
                "order": len(units),
                "kind": _unit_kind(block),
                "markdown": block,
                "boundary_before": boundary_before,
                "policy": _unit_policy(chapter, block, policy),
                "policy_confirmed": bool(chapter.get("translation_policy_confirmed")),
                "provenance": provenance,
                "source_fingerprint": _sha256_text(block),
            })
        chapters.append({
            "chapter_id": chapter_id,
            "index": chapter_index,
            "title": chapter_title,
            "kind": chapter_kind,
            "policy": policy,
            "policy_confirmed": bool(chapter.get("translation_policy_confirmed")),
            "unit_ids": unit_ids,
            "source_fingerprint": _sha256_text(markdown),
        })

    fingerprint_payload = [
        {
            "unit_id": unit["unit_id"],
            "markdown": unit["markdown"],
            "policy": unit["policy"],
            "provenance": unit["provenance"],
        }
        for unit in units
    ]
    precisions = {
        span["precision"]
        for unit in units
        for span in unit["provenance"]
        if isinstance(span, dict) and span.get("precision") in PROVENANCE_PRECISIONS
    }
    precision_rank = {"chapter": 0, "resource": 1, "dom": 2, "span": 3}
    document_precision = min(precisions, key=precision_rank.__getitem__) if precisions else "chapter"
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "source_format": source_format,
        "generation": {
            "adapter": "book_projection_v1",
            "translation_authority": bool(translation_authority),
            "provenance_precision": document_precision,
        },
        "document_fingerprint": _sha256_text(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ),
        "chapter_count": len(chapters),
        "unit_count": len(units),
        "chapters": chapters,
        "units": units,
    }
    validate_reading_units(payload)
    return payload


def validate_reading_units(payload: dict[str, Any]) -> None:
    if payload.get("schema") != SCHEMA:
        raise ValueError("Unsupported reading-units schema")
    if payload.get("source_format") not in {"pdf", "epub"}:
        raise ValueError("Reading units require a supported source format")
    units = payload.get("units")
    chapters = payload.get("chapters")
    if not isinstance(units, list) or not isinstance(chapters, list):
        raise ValueError("Reading units require chapter and unit lists")
    generation = payload.get("generation")
    if not isinstance(generation, dict) or not isinstance(generation.get("translation_authority"), bool):
        raise ValueError("Reading units require an explicit translation-authority flag")
    if generation.get("provenance_precision") not in PROVENANCE_PRECISIONS:
        raise ValueError("Reading-unit document provenance precision is invalid")
    seen: set[str] = set()
    for expected_order, unit in enumerate(units):
        if not isinstance(unit, dict):
            raise ValueError("Every reading unit must be an object")
        unit_id = str(unit.get("unit_id") or "")
        if not unit_id or unit_id in seen:
            raise ValueError("Reading unit ids must be non-empty and unique")
        seen.add(unit_id)
        if unit.get("order") != expected_order:
            raise ValueError("Reading unit order must be contiguous")
        if unit.get("kind") not in UNIT_KINDS:
            raise ValueError(f"Unsupported reading unit kind: {unit.get('kind')!r}")
        if unit.get("boundary_before") not in BOUNDARY_KINDS:
            raise ValueError("Reading unit boundary is invalid")
        if unit.get("policy") not in POLICIES:
            raise ValueError("Reading unit policy is invalid")
        markdown = unit.get("markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            raise ValueError("Reading unit Markdown cannot be empty")
        if unit.get("source_fingerprint") != _sha256_text(markdown):
            raise ValueError("Reading unit source fingerprint does not match its content")
        provenance = unit.get("provenance")
        if not isinstance(provenance, list) or not provenance:
            raise ValueError("Every reading unit requires source provenance")
        for span in provenance:
            if not isinstance(span, dict) or span.get("source_format") != payload["source_format"]:
                raise ValueError("Reading unit provenance format does not match the document")
            if span.get("precision") not in PROVENANCE_PRECISIONS:
                raise ValueError("Reading unit provenance precision is invalid")
            if span.get("precision") == "span" and payload["source_format"] == "pdf":
                if not isinstance(span.get("page_no"), int) or not str(span.get("source_node_id") or ""):
                    raise ValueError("Precise PDF provenance requires a page and source node")
                if not isinstance(span.get("char_start"), int) or not isinstance(span.get("char_end"), int):
                    raise ValueError("Precise PDF provenance requires a character range")
                if span["char_start"] < 0 or span["char_end"] <= span["char_start"]:
                    raise ValueError("Precise PDF provenance character range is invalid")
            if span.get("precision") in {"resource", "dom", "span"} and payload["source_format"] == "epub":
                if not str(span.get("resource_path") or "").strip():
                    raise ValueError("Precise EPUB provenance requires a resource path")
            if span.get("precision") == "dom" and payload["source_format"] == "epub":
                if not str(span.get("dom_path") or "").strip():
                    raise ValueError("DOM EPUB provenance requires a DOM path")
                if not isinstance(span.get("char_start"), int) or not isinstance(span.get("char_end"), int):
                    raise ValueError("DOM EPUB provenance requires a character range")
                if span["char_start"] < 0 or span["char_end"] < span["char_start"]:
                    raise ValueError("DOM EPUB provenance character range is invalid")

    referenced: list[str] = []
    chapter_ids: set[str] = set()
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise ValueError("Every reading-unit chapter must be an object")
        chapter_id = str(chapter.get("chapter_id") or "")
        if not chapter_id or chapter_id in chapter_ids:
            raise ValueError("Reading-unit chapter ids must be non-empty and unique")
        chapter_ids.add(chapter_id)
        chapter_unit_ids = chapter.get("unit_ids")
        if not isinstance(chapter_unit_ids, list):
            raise ValueError("Reading-unit chapters require unit ids")
        referenced.extend(str(unit_id) for unit_id in chapter_unit_ids)
    if referenced != [unit["unit_id"] for unit in units]:
        raise ValueError("Chapter unit order must cover every reading unit exactly once")
    if any(unit.get("chapter_id") not in chapter_ids for unit in units):
        raise ValueError("Every reading unit must belong to a declared chapter")
    if payload.get("unit_count") != len(units) or payload.get("chapter_count") != len(chapters):
        raise ValueError("Reading-unit summary counts are inconsistent")


def chapter_markdown_from_units(payload: dict[str, Any], chapter_id: str) -> str:
    validate_reading_units(payload)
    blocks = [unit["markdown"] for unit in payload["units"] if unit["chapter_id"] == chapter_id]
    return "\n\n".join(blocks)


def write_reading_units(
    path: Path,
    book: dict[str, Any],
    *,
    source_path: Path,
    translation_authority: bool = False,
) -> dict[str, Any]:
    payload = build_reading_units(
        book,
        source_path=source_path,
        translation_authority=translation_authority,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload
