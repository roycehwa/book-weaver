from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

import pypdfium2 as pdfium

from pdf_translator.reconstruct import (
    _cluster_columns,
    _column_index,
    _extract_text_blocks,
    _is_noise_block,
    _normalize_text,
)


BODY_LABELS = {"text", "list_item"}
HEADER_LABELS = {"section_header"}
CAPTION_LABELS = {"caption"}
SHORT_FRAGMENT_CHARS = 40
BODY_SEGMENT_GAP = 115.0


@dataclass(slots=True)
class ProfileSpec:
    name: str
    document_reject_ratio: float
    allow_skip_content: bool
    front_matter_window: int
    back_matter_window: int
    article_columns_max: int
    assist_columns_min: int
    assist_segments_min: int
    reject_columns_min: int
    reject_segments_min: int
    reject_segments_hard: int
    weak_flow_ratio: float


PROFILE_SPECS: dict[str, ProfileSpec] = {
    "magazine": ProfileSpec(
        name="magazine",
        document_reject_ratio=0.40,
        allow_skip_content=True,
        front_matter_window=10,
        back_matter_window=8,
        article_columns_max=4,
        assist_columns_min=3,
        assist_segments_min=5,
        reject_columns_min=5,
        reject_segments_min=8,
        reject_segments_hard=10,
        weak_flow_ratio=0.22,
    ),
    "book": ProfileSpec(
        name="book",
        document_reject_ratio=0.18,
        allow_skip_content=False,
        front_matter_window=4,
        back_matter_window=4,
        article_columns_max=2,
        assist_columns_min=2,
        assist_segments_min=4,
        reject_columns_min=4,
        reject_segments_min=8,
        reject_segments_hard=10,
        weak_flow_ratio=0.24,
    ),
    "newspaper": ProfileSpec(
        name="newspaper",
        document_reject_ratio=0.30,
        allow_skip_content=True,
        front_matter_window=6,
        back_matter_window=6,
        article_columns_max=5,
        assist_columns_min=3,
        assist_segments_min=6,
        reject_columns_min=6,
        reject_segments_min=10,
        reject_segments_hard=12,
        weak_flow_ratio=0.18,
    ),
}


def _page_sizes(source_pdf: Path) -> dict[int, tuple[float, float]]:
    document = pdfium.PdfDocument(str(source_pdf))
    sizes: dict[int, tuple[float, float]] = {}
    for index in range(len(document)):
        sizes[index + 1] = document[index].get_size()
    return sizes


def _extract_picture_areas(structured: dict[str, Any], page_sizes: dict[int, tuple[float, float]]) -> dict[int, float]:
    areas: dict[int, float] = defaultdict(float)
    for picture in structured.get("pictures", []):
        prov = picture.get("prov") or []
        if not prov:
            continue
        item = prov[0]
        page_no = int(item.get("page_no", 0))
        bbox = item.get("bbox") or {}
        width = max(0.0, float(bbox.get("r", 0.0)) - float(bbox.get("l", 0.0)))
        height = max(0.0, float(bbox.get("t", 0.0)) - float(bbox.get("b", 0.0)))
        page_size = page_sizes.get(page_no)
        if not page_size:
            continue
        page_area = page_size[0] * page_size[1]
        if page_area <= 0:
            continue
        areas[page_no] += (width * height) / page_area
    return dict(areas)


def _merge_flow_segments(page_blocks: list[Any], columns: list[float]) -> list[int]:
    per_column: dict[int, list[Any]] = defaultdict(list)
    for block in page_blocks:
        per_column[_column_index(block, columns)].append(block)

    segment_char_counts: list[int] = []
    for column_index in sorted(per_column):
        sorted_blocks = sorted(per_column[column_index], key=lambda block: -block.top)
        current_chars = 0
        previous_top: float | None = None
        for block in sorted_blocks:
            block_chars = len(_normalize_text(block.text))
            if previous_top is None:
                current_chars = block_chars
                previous_top = block.top
                continue

            gap = previous_top - block.top
            if gap > BODY_SEGMENT_GAP:
                segment_char_counts.append(current_chars)
                current_chars = block_chars
            else:
                current_chars += block_chars
            previous_top = block.top

        if current_chars:
            segment_char_counts.append(current_chars)

    return segment_char_counts


def _page_position_bucket(page_no: int, total_pages: int, spec: ProfileSpec) -> str:
    if page_no <= spec.front_matter_window:
        return "front"
    if page_no > total_pages - spec.back_matter_window:
        return "back"
    return "middle"


def _infer_profile_name(page_profiles: list[dict[str, Any]]) -> str:
    if not page_profiles:
        return "magazine"

    text_pages = [page for page in page_profiles if page["text_chars"] > 0]
    if not text_pages:
        return "magazine"

    median_columns = median(page["column_count"] for page in text_pages)
    avg_picture_ratio = sum(page["picture_area_ratio"] for page in page_profiles) / len(page_profiles)
    avg_headers = sum(page["header_block_count"] for page in page_profiles) / len(page_profiles)
    high_fragment_pages = sum(1 for page in text_pages if page["flow_segment_count"] >= 6)

    if median_columns <= 1 and avg_picture_ratio < 0.12 and avg_headers < 1.5:
        return "book"

    if median_columns >= 3 or (median_columns >= 2 and high_fragment_pages / len(text_pages) > 0.35 and avg_picture_ratio < 0.2):
        return "newspaper"

    return "magazine"


def _content_label(
    *,
    spec: ProfileSpec,
    page_no: int,
    total_pages: int,
    text_chars: int,
    picture_area_ratio: float,
    body_block_count: int,
    header_block_count: int,
    caption_block_count: int,
    flow_segment_count: int,
    main_text_block_count: int,
) -> str:
    position = _page_position_bucket(page_no, total_pages, spec)

    if text_chars == 0:
        return "visual_only"

    if spec.allow_skip_content and picture_area_ratio >= 0.78 and text_chars < 220 and body_block_count <= 3:
        return "ad_or_visual"

    if (
        spec.allow_skip_content
        and position == "front"
        and header_block_count >= 2
        and text_chars < 700
        and flow_segment_count <= 4
    ):
        return "front_matter"

    if (
        spec.allow_skip_content
        and position == "back"
        and text_chars < 900
        and (caption_block_count > 0 or picture_area_ratio >= 0.35)
    ):
        return "back_matter"

    if spec.allow_skip_content and header_block_count >= 3 and text_chars < 650 and main_text_block_count <= 4:
        return "toc_or_listing"

    if text_chars >= 250:
        return "article"

    return "short_text"


def _page_action(
    *,
    spec: ProfileSpec,
    content_label: str,
    text_chars: int,
    flow_segment_count: int,
    column_count: int,
    small_fragment_ratio: float,
    largest_flow_ratio: float,
) -> tuple[str, list[str]]:
    if content_label == "visual_only":
        if spec.allow_skip_content:
            return "skip_content", ["no_body_text"]
        return "assist", ["non_text_page_in_book"]

    if content_label in {"ad_or_visual", "front_matter", "back_matter", "toc_or_listing"}:
        if spec.allow_skip_content:
            return "skip_content", [content_label]
        return "assist", [f"book_{content_label}"]

    reasons: list[str] = []

    if flow_segment_count >= spec.reject_segments_hard:
        reasons.append("too_many_flow_segments")
    if column_count >= spec.reject_columns_min and flow_segment_count >= spec.assist_segments_min:
        reasons.append("too_many_columns")
    if small_fragment_ratio >= 0.75 and flow_segment_count >= spec.assist_segments_min:
        reasons.append("fragmented_short_blocks")
    if (
        largest_flow_ratio < spec.weak_flow_ratio
        and flow_segment_count >= spec.reject_segments_min
        and column_count >= spec.reject_columns_min - 1
        and text_chars >= 250
    ):
        reasons.append("weak_main_reading_flow")

    if reasons:
        return "reject_structure", reasons

    if (
        content_label == "article"
        and (column_count >= spec.assist_columns_min or flow_segment_count >= spec.assist_segments_min)
    ):
        assist_reasons: list[str] = []
        if column_count >= spec.assist_columns_min:
            assist_reasons.append("structured_multi_column_article")
        if flow_segment_count >= spec.assist_segments_min:
            assist_reasons.append("structured_multi_segment_article")
        if largest_flow_ratio < max(spec.weak_flow_ratio + 0.08, 0.3):
            assist_reasons.append("distributed_reading_flow")
        return "assist", assist_reasons or ["complex_article_layout"]

    if content_label == "short_text" and flow_segment_count >= spec.assist_segments_min:
        return "assist", ["short_but_fragmented"]

    return "accept", ["stable_reading_flow"]


def _build_page_profiles(
    *,
    structured: dict[str, Any],
    page_sizes: dict[int, tuple[float, float]],
    spec: ProfileSpec,
) -> list[dict[str, Any]]:
    picture_area_ratios = _extract_picture_areas(structured, page_sizes)
    filtered_blocks = [block for block in _extract_text_blocks(structured) if not _is_noise_block(block)]
    page_blocks: dict[int, list[Any]] = defaultdict(list)
    for block in filtered_blocks:
        page_blocks[block.page_no].append(block)

    total_pages = len(page_sizes)
    page_profiles: list[dict[str, Any]] = []
    for page_no in range(1, total_pages + 1):
        blocks = page_blocks.get(page_no, [])
        body_blocks = [block for block in blocks if block.label in BODY_LABELS]
        header_blocks = [block for block in blocks if block.label in HEADER_LABELS]
        caption_blocks = [block for block in blocks if block.label in CAPTION_LABELS]

        normalized_body_blocks = [block for block in body_blocks if len(_normalize_text(block.text)) > 0]
        text_chars = sum(len(_normalize_text(block.text)) for block in normalized_body_blocks)
        main_blocks = [
            block
            for block in normalized_body_blocks
            if len(_normalize_text(block.text)) >= SHORT_FRAGMENT_CHARS
        ] or normalized_body_blocks

        columns = _cluster_columns(main_blocks)
        flow_segments = _merge_flow_segments(main_blocks, columns) if main_blocks else []
        flow_segment_count = len(flow_segments)
        largest_flow_ratio = (max(flow_segments) / text_chars) if flow_segments and text_chars else 0.0
        short_fragments = [
            block for block in normalized_body_blocks if len(_normalize_text(block.text)) < SHORT_FRAGMENT_CHARS
        ]
        small_fragment_ratio = (
            len(short_fragments) / len(normalized_body_blocks) if normalized_body_blocks else 0.0
        )
        picture_area_ratio = min(1.0, picture_area_ratios.get(page_no, 0.0))

        content_label = _content_label(
            spec=spec,
            page_no=page_no,
            total_pages=total_pages,
            text_chars=text_chars,
            picture_area_ratio=picture_area_ratio,
            body_block_count=len(normalized_body_blocks),
            header_block_count=len(header_blocks),
            caption_block_count=len(caption_blocks),
            flow_segment_count=flow_segment_count,
            main_text_block_count=len(main_blocks),
        )
        action, reasons = _page_action(
            spec=spec,
            content_label=content_label,
            text_chars=text_chars,
            flow_segment_count=flow_segment_count,
            column_count=len(columns),
            small_fragment_ratio=small_fragment_ratio,
            largest_flow_ratio=largest_flow_ratio,
        )

        page_profiles.append(
            {
                "page_no": page_no,
                "content_label": content_label,
                "action": action,
                "reasons": reasons,
                "text_chars": text_chars,
                "body_block_count": len(normalized_body_blocks),
                "main_text_block_count": len(main_blocks),
                "flow_segment_count": flow_segment_count,
                "column_count": len(columns),
                "largest_flow_ratio": round(largest_flow_ratio, 3),
                "small_fragment_ratio": round(small_fragment_ratio, 3),
                "picture_area_ratio": round(picture_area_ratio, 3),
                "header_block_count": len(header_blocks),
                "caption_block_count": len(caption_blocks),
                "position": _page_position_bucket(page_no, total_pages, spec),
            }
        )
    return page_profiles


def build_document_profile(source_pdf: Path, structured: dict[str, Any], profile_name: str = "auto") -> dict[str, Any]:
    page_sizes = _page_sizes(source_pdf)
    provisional_spec = PROFILE_SPECS["magazine"]
    provisional_pages = _build_page_profiles(structured=structured, page_sizes=page_sizes, spec=provisional_spec)

    resolved_profile_name = _infer_profile_name(provisional_pages) if profile_name == "auto" else profile_name
    if resolved_profile_name not in PROFILE_SPECS:
        raise ValueError(f"Unsupported profile: {profile_name}")

    spec = PROFILE_SPECS[resolved_profile_name]
    page_profiles = _build_page_profiles(structured=structured, page_sizes=page_sizes, spec=spec)

    action_counter: Counter[str] = Counter(page["action"] for page in page_profiles)
    content_counter: Counter[str] = Counter(page["content_label"] for page in page_profiles)
    total_pages = len(page_profiles)
    reject_ratio = (action_counter["reject_structure"] / total_pages) if total_pages else 0.0

    page_numbers_by_action = {
        action: [page["page_no"] for page in page_profiles if page["action"] == action]
        for action in ("accept", "assist", "skip_content", "reject_structure")
    }

    document_action = "reject" if reject_ratio > spec.document_reject_ratio else "accept"
    if document_action == "accept" and action_counter["assist"] > total_pages * 0.2:
        document_action = "review"

    return {
        "source_pdf": str(source_pdf),
        "profile": resolved_profile_name,
        "total_pages": total_pages,
        "actions": {
            "accept": action_counter["accept"],
            "assist": action_counter["assist"],
            "skip_content": action_counter["skip_content"],
            "reject_structure": action_counter["reject_structure"],
        },
        "ratios": {
            "accept": round(action_counter["accept"] / total_pages, 3) if total_pages else 0.0,
            "assist": round(action_counter["assist"] / total_pages, 3) if total_pages else 0.0,
            "skip_content": round(action_counter["skip_content"] / total_pages, 3) if total_pages else 0.0,
            "reject_structure": round(reject_ratio, 3),
        },
        "document_action": document_action,
        "thresholds": {
            "document_reject_ratio": spec.document_reject_ratio,
            "short_fragment_chars": SHORT_FRAGMENT_CHARS,
            "body_segment_gap": BODY_SEGMENT_GAP,
            "assist_segments_min": spec.assist_segments_min,
            "reject_segments_min": spec.reject_segments_min,
        },
        "content_labels": dict(content_counter),
        "accepted_pages": page_numbers_by_action["accept"],
        "assist_pages": page_numbers_by_action["assist"],
        "skipped_pages": page_numbers_by_action["skip_content"],
        "rejected_pages": page_numbers_by_action["reject_structure"],
        "pages": page_profiles,
    }
