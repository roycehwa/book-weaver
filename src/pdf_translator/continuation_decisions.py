"""Format-neutral cross-page continuation decision ledger.

PDF adapters record every evaluated page boundary.  Only high-confidence
evidence may auto-merge different Docling source nodes; same-node spans keep
the existing deterministic merge path.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pdf_translator.reconstruct import LayoutBlock, _cluster_columns, _column_index

SCHEMA = "bookweaver_continuation_decisions_v1"
STATUSES = frozenset({"accepted", "rejected", "uncertain"})
PROVENANCE = "pdf_docling_page_boundary_v1"

# Reference geometry on a ~792×612pt page; decisions use page-relative ratios.
PAGE_BOTTOM_MAX_RATIO = 320.0 / 792.0
PAGE_TOP_MIN_RATIO = 520.0 / 792.0
COLUMN_ALIGN_TOLERANCE_RATIO = 14.0 / 612.0
INDENT_TOLERANCE_RATIO = 22.0 / 612.0

BODY_TEXT_LABELS = frozenset({"text"})
BOUNDARY_NOISE_LABELS = frozenset({"page_header", "page_footer"})
STRUCTURAL_TEXT_LABELS = frozenset({
    "section_header",
    "title",
    "document_title",
    "list_item",
    "caption",
    "code",
    "formula",
    "form",
    "checkbox",
    "radio",
    "page_number",
})
STRUCTURAL_KINDS = frozenset({"figure", "table"})
SECTION_HEADING_LABELS = frozenset({"section_header", "title", "document_title"})

TERMINAL_PUNCT_RE = re.compile(r'[.!?]["\'\)\]]*\s*$')
HEADING_LINE_RE = re.compile(r"^#{1,6}\s+\S")
LIST_LINE_RE = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)")
LOWercase_CONTINUATION_RE = re.compile(r"^[a-z]")
HYPHEN_BREAK_RE = re.compile(r"[A-Za-z]-$")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decision_id(
    *,
    from_page: int,
    to_page: int,
    left_node: str,
    right_node: str,
    left_end: int,
    right_start: int,
) -> str:
    payload = f"{from_page}:{to_page}:{left_node}:{right_node}:{left_end}:{right_start}"
    return _sha256_text(payload)[:24]


def page_dimensions_from_structured(structured: dict[str, Any] | None) -> dict[int, tuple[float, float]]:
    pages = (structured or {}).get("pages")
    dimensions: dict[int, tuple[float, float]] = {}
    if isinstance(pages, dict):
        for key, page in pages.items():
            if not isinstance(page, dict):
                continue
            try:
                page_no = int(key)
            except (TypeError, ValueError):
                continue
            size = page.get("size") if isinstance(page.get("size"), dict) else {}
            width = float(size.get("width") or 0.0)
            height = float(size.get("height") or 0.0)
            if width > 0 and height > 0:
                dimensions[page_no] = (width, height)
    elif isinstance(pages, list):
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_no = page.get("page_no")
            if not isinstance(page_no, int):
                continue
            size = page.get("size") if isinstance(page.get("size"), dict) else {}
            width = float(size.get("width") or page.get("page_width") or 0.0)
            height = float(size.get("height") or page.get("page_height") or 0.0)
            if width > 0 and height > 0:
                dimensions[page_no] = (width, height)
    return dimensions


def attach_page_dimensions(
    page_payloads: list[dict[str, Any]],
    dimensions_by_page: dict[int, tuple[float, float]],
) -> None:
    for page in page_payloads:
        if not isinstance(page, dict):
            continue
        page_no = int(page.get("page_no") or 0)
        size = dimensions_by_page.get(page_no)
        if not size:
            continue
        width, height = size
        page["page_width"] = width
        page["page_height"] = height
        page["page_size"] = {"width": width, "height": height}


def _page_size_from_payload(page: dict[str, Any] | None) -> tuple[float, float] | None:
    if not isinstance(page, dict):
        return None
    size = page.get("page_size")
    if isinstance(size, dict):
        width = float(size.get("width") or 0.0)
        height = float(size.get("height") or 0.0)
        if width > 0 and height > 0:
            return width, height
    width = float(page.get("page_width") or 0.0)
    height = float(page.get("page_height") or 0.0)
    if width > 0 and height > 0:
        return width, height
    return None


def _item_geometry(item: dict[str, Any]) -> dict[str, float]:
    return {
        "left": float(item.get("left") or 0.0),
        "top": float(item.get("top") or 0.0),
        "right": float(item.get("right") or 0.0),
        "bottom": float(item.get("bottom") or item.get("top") or 0.0),
    }


def _layout_block_from_item(item: dict[str, Any]) -> LayoutBlock:
    geo = _item_geometry(item)
    return LayoutBlock(
        label=str(item.get("source_label") or "text"),
        text=str(item.get("text") or ""),
        page_no=int(item.get("page_no") or 0),
        left=geo["left"],
        top=geo["top"],
        bottom=geo["bottom"],
        right=geo["right"],
    )


def _alignment_tolerance(*widths: float) -> float:
    positive = [width for width in widths if width > 0]
    if not positive:
        return 0.0
    return max(positive) * COLUMN_ALIGN_TOLERANCE_RATIO


def _indent_tolerance(page_width: float) -> float:
    if page_width <= 0:
        return 0.0
    return page_width * INDENT_TOLERANCE_RATIO


def _columns_compatible(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    left_page_width: float,
    right_page_width: float,
) -> tuple[bool, dict[str, Any]]:
    if left_page_width <= 0 or right_page_width <= 0:
        return False, {"available": False}
    left_geo = _item_geometry(left)
    right_geo = _item_geometry(right)
    left_block = _layout_block_from_item(left)
    right_block = _layout_block_from_item(right)
    columns = _cluster_columns([left_block, right_block])
    left_col = _column_index(left_block, columns)
    right_col = _column_index(right_block, columns)
    same_column = left_col == right_col
    align_tol = _alignment_tolerance(left_page_width, right_page_width)
    aligned = (
        abs(left_geo["left"] - right_geo["left"]) <= align_tol
        and abs(left_geo["right"] - right_geo["right"]) <= align_tol
    )
    return same_column and aligned, {
        "left_column": left_col,
        "right_column": right_col,
        "left_bbox": left_geo,
        "right_bbox": right_geo,
        "alignment_tolerance": round(align_tol, 6),
        "left_page_width": left_page_width,
        "right_page_width": right_page_width,
    }


def _style_compatible(
    structured: dict[str, Any] | None,
    left_node: str,
    right_node: str,
) -> tuple[bool, dict[str, Any]]:
    if not structured:
        return True, {"available": False}
    texts = structured.get("texts") or []
    index_by_ref = {f"#/texts/{index}": item for index, item in enumerate(texts) if isinstance(item, dict)}
    left_item = index_by_ref.get(left_node)
    right_item = index_by_ref.get(right_node)
    if not left_item or not right_item:
        return True, {"available": False}

    def style_keys(item: dict[str, Any]) -> tuple[Any, ...]:
        keys = ("font", "font_size", "style", "weight")
        return tuple(item.get(key) for key in keys if item.get(key) is not None)

    left_style = style_keys(left_item)
    right_style = style_keys(right_item)
    if not left_style and not right_style:
        return True, {"available": False}
    if not left_style or not right_style:
        return True, {"available": True, "partial": True}
    return left_style == right_style, {
        "available": True,
        "left": left_style,
        "right": right_style,
    }


def _syntax_continuation(left_text: str, right_text: str) -> tuple[bool, str, str | None]:
    left = left_text.rstrip()
    right = right_text.lstrip()
    if not left or not right:
        return False, "empty_endpoint", None
    if HEADING_LINE_RE.match(right) or HEADING_LINE_RE.match(left):
        return False, "heading_boundary", None
    if LIST_LINE_RE.match(right) or LIST_LINE_RE.match(left):
        return False, "list_boundary", None
    if TERMINAL_PUNCT_RE.search(left):
        return False, "terminal_punctuation", None
    if HYPHEN_BREAK_RE.search(left) and LOWercase_CONTINUATION_RE.match(right):
        joined = left[:-1] + right
        return True, "hyphen_continuation", joined
    if LOWercase_CONTINUATION_RE.match(right) and re.search(r"[A-Za-z,;:(]$", left):
        return True, "lowercase_continuation", left + " " + right
    if LOWercase_CONTINUATION_RE.match(right) and not TERMINAL_PUNCT_RE.search(left):
        return True, "lowercase_continuation", left + " " + right
    return False, "syntax_not_continuation", None


def _join_source_node_fragments(left: str, right: str, separator: str) -> tuple[str, str]:
    left_text = left.rstrip()
    right_text = right.lstrip()
    if separator.isspace() and left_text.endswith("-") and re.match(r"^[a-z]", right_text):
        return left_text[:-1] + right_text, "page_break_hyphen_removed"
    normalized_separator = " " if separator and separator.isspace() else separator
    return left_text + normalized_separator + right_text, "source_separator_restored"


def _body_text_items(page: dict[str, Any]) -> list[dict[str, Any]]:
    allowed_labels = {"text"}
    return [
        item
        for item in page.get("content_items") or []
        if isinstance(item, dict)
        and item.get("kind") == "text"
        and not item.get("from_page_footer")
        and item.get("source_node_id")
        and isinstance(item.get("source_char_start"), int)
        and isinstance(item.get("source_char_end"), int)
        and str(item.get("source_label") or "text") in allowed_labels
    ]


def evaluate_same_source_node_boundary(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    from_page: int,
    to_page: int,
) -> dict[str, Any]:
    left_end = int(left["source_char_end"])
    right_start = int(right["source_char_start"])
    separator = str(right.get("source_separator_before") or "")
    node_id = str(left["source_node_id"])
    decision = {
        "decision_id": _decision_id(
            from_page=from_page,
            to_page=to_page,
            left_node=node_id,
            right_node=node_id,
            left_end=left_end,
            right_start=right_start,
        ),
        "status": "rejected",
        "confidence": "deterministic_same_source_node",
        "kind": "same_source_node",
        "from_page": from_page,
        "to_page": to_page,
        "left_source_node_id": node_id,
        "right_source_node_id": node_id,
        "left_char_start": int(left["source_char_start"]),
        "left_char_end": left_end,
        "right_char_start": right_start,
        "right_char_end": int(right["source_char_end"]),
        "provenance": PROVENANCE,
        "reasons": [],
        "evidence": {},
    }
    if right_start < left_end or right_start - left_end != len(separator):
        decision["reasons"].append("source_char_gap")
        return decision
    if separator and not separator.isspace():
        decision["reasons"].append("non_whitespace_source_separator")
        return decision
    joined, repair = _join_source_node_fragments(
        str(left.get("text") or ""),
        str(right.get("text") or ""),
        separator,
    )
    decision["status"] = "accepted"
    decision["reasons"] = ["same_source_node_provenance"]
    decision["evidence"] = {
        "repair": repair,
        "joined_text": joined,
        "source_separator": separator,
    }
    return decision


def _page_items_in_order(page: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(page, dict):
        return []
    items = [item for item in page.get("content_items") or [] if isinstance(item, dict)]
    return sorted(
        items,
        key=lambda item: (
            bool(item.get("from_page_footer")),
            -float(item.get("top") or 0.0),
            float(item.get("left") or 0.0),
            str(item.get("source_node_id") or ""),
        ),
    )


def _matches_left_endpoint(item: dict[str, Any], left: dict[str, Any]) -> bool:
    return (
        str(item.get("source_node_id") or "") == str(left.get("source_node_id") or "")
        and int(item.get("source_char_end") or -1) == int(left.get("source_char_end") or -1)
    )


def _matches_right_endpoint(item: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        str(item.get("source_node_id") or "") == str(right.get("source_node_id") or "")
        and int(item.get("source_char_start") or -1) == int(right.get("source_char_start") or -1)
    )


def _is_boundary_noise_item(item: dict[str, Any]) -> bool:
    return str(item.get("source_label") or "") in BOUNDARY_NOISE_LABELS


def _is_structural_barrier_item(item: dict[str, Any]) -> bool:
    if _is_boundary_noise_item(item):
        return False
    kind = str(item.get("kind") or "")
    if kind in STRUCTURAL_KINDS or item.get("path"):
        return True
    label = str(item.get("source_label") or "text")
    if label in STRUCTURAL_TEXT_LABELS:
        return True
    if kind != "text":
        return True
    if label not in BODY_TEXT_LABELS:
        return True
    return False


def _barrier_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": item.get("kind"),
        "source_label": item.get("source_label"),
        "source_node_id": item.get("source_node_id"),
        "text": str(item.get("text") or "")[:120],
        "top": item.get("top"),
        "left": item.get("left"),
    }


def _boundary_noise_record(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_label": item.get("source_label"),
        "source_node_id": item.get("source_node_id"),
        "text": str(item.get("text") or "")[:120],
        "top": item.get("top"),
    }


def _structured_text_ref(index: int) -> str:
    return f"#/texts/{index}"


def _structured_noise_between_endpoints(
    structured: dict[str, Any] | None,
    *,
    left_page_no: int,
    right_page_no: int,
    left: dict[str, Any],
    right: dict[str, Any],
    recorded_noise: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not structured:
        return []
    recorded_ids = {str(item.get("source_node_id") or "") for item in recorded_noise}
    left_bottom = float(_item_geometry(left)["bottom"])
    right_top = float(_item_geometry(right)["top"])
    extra: list[dict[str, Any]] = []
    for index, item in enumerate(structured.get("texts") or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "")
        if label not in BOUNDARY_NOISE_LABELS:
            continue
        node_id = _structured_text_ref(index)
        if node_id in recorded_ids:
            continue
        for prov in item.get("prov") or []:
            if not isinstance(prov, dict):
                continue
            page_no = int(prov.get("page_no") or 0)
            bbox = prov.get("bbox") or {}
            top = float(bbox.get("t") or 0.0)
            if page_no == right_page_no and top >= right_top:
                extra.append(
                    {
                        "source_label": label,
                        "source_node_id": node_id,
                        "text": str(item.get("text") or "")[:120],
                        "top": top,
                        "from_structured": True,
                    }
                )
            elif page_no == left_page_no and top < left_bottom:
                extra.append(
                    {
                        "source_label": label,
                        "source_node_id": node_id,
                        "text": str(item.get("text") or "")[:120],
                        "top": top,
                        "from_structured": True,
                    }
                )
    return extra


def _structural_barriers_between_endpoints(
    left_page: dict[str, Any] | None,
    right_page: dict[str, Any] | None,
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    structured: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    barriers: list[dict[str, Any]] = []
    noise: list[dict[str, Any]] = []
    reasons: list[str] = []
    left_items = _page_items_in_order(left_page)
    right_items = _page_items_in_order(right_page)
    left_index = next((index for index, item in enumerate(left_items) if _matches_left_endpoint(item, left)), None)
    right_index = next((index for index, item in enumerate(right_items) if _matches_right_endpoint(item, right)), None)
    if left_index is not None:
        for item in left_items[left_index + 1 :]:
            if _is_boundary_noise_item(item):
                noise.append(_boundary_noise_record(item))
                continue
            if _is_structural_barrier_item(item):
                barriers.append(_barrier_record(item))
    if right_index is not None:
        for item in right_items[:right_index]:
            if _is_boundary_noise_item(item):
                noise.append(_boundary_noise_record(item))
                continue
            if _is_structural_barrier_item(item):
                barriers.append(_barrier_record(item))
    if structured and (left_page or right_page):
        noise.extend(
            _structured_noise_between_endpoints(
                structured,
                left_page_no=int((left_page or {}).get("page_no") or left.get("page_no") or 0),
                right_page_no=int((right_page or {}).get("page_no") or right.get("page_no") or 0),
                left=left,
                right=right,
                recorded_noise=noise,
            )
        )
    if barriers:
        reasons.append("structural_content_between_endpoints")
        if any(str(item.get("source_label") or "") in SECTION_HEADING_LABELS for item in barriers):
            reasons.append("section_heading_boundary")
    return barriers, noise, reasons


def evaluate_cross_source_node_boundary(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    from_page: int,
    to_page: int,
    structured: dict[str, Any] | None = None,
    left_page: dict[str, Any] | None = None,
    right_page: dict[str, Any] | None = None,
) -> dict[str, Any]:
    left_node = str(left["source_node_id"])
    right_node = str(right["source_node_id"])
    left_end = int(left["source_char_end"])
    right_start = int(right["source_char_start"])
    decision: dict[str, Any] = {
        "decision_id": _decision_id(
            from_page=from_page,
            to_page=to_page,
            left_node=left_node,
            right_node=right_node,
            left_end=left_end,
            right_start=right_start,
        ),
        "status": "rejected",
        "confidence": "high_confidence_cross_source_node",
        "kind": "cross_source_node",
        "from_page": from_page,
        "to_page": to_page,
        "left_source_node_id": left_node,
        "right_source_node_id": right_node,
        "left_char_start": int(left["source_char_start"]),
        "left_char_end": left_end,
        "right_char_start": right_start,
        "right_char_end": int(right["source_char_end"]),
        "provenance": PROVENANCE,
        "reasons": [],
        "evidence": {},
    }
    left_geo = _item_geometry(left)
    right_geo = _item_geometry(right)
    structural_barriers, boundary_noise, structural_reasons = _structural_barriers_between_endpoints(
        left_page,
        right_page,
        left,
        right,
        structured=structured,
    )
    if boundary_noise:
        decision["evidence"]["ignored_boundary_noise"] = boundary_noise
    if structural_barriers:
        decision["evidence"]["structural_barriers"] = structural_barriers
        decision["reasons"].extend(structural_reasons)
    left_page_size = _page_size_from_payload(left_page)
    right_page_size = _page_size_from_payload(right_page)
    page_geometry_ok = False
    page_geometry_evidence: dict[str, Any] = {
        "left_page_size": (
            {"width": left_page_size[0], "height": left_page_size[1]} if left_page_size else None
        ),
        "right_page_size": (
            {"width": right_page_size[0], "height": right_page_size[1]} if right_page_size else None
        ),
    }
    if not left_page_size or not right_page_size:
        decision["reasons"].append("missing_page_dimensions")
    else:
        left_height = left_page_size[1]
        right_height = right_page_size[1]
        left_bottom_ratio = left_geo["bottom"] / left_height
        right_top_ratio = right_geo["top"] / right_height
        page_geometry_evidence["left_bottom_ratio"] = round(left_bottom_ratio, 6)
        page_geometry_evidence["right_top_ratio"] = round(right_top_ratio, 6)
        page_geometry_ok = (
            left_bottom_ratio <= PAGE_BOTTOM_MAX_RATIO
            and right_top_ratio >= PAGE_TOP_MIN_RATIO
        )
        if left_bottom_ratio > PAGE_BOTTOM_MAX_RATIO:
            decision["reasons"].append("left_not_near_page_bottom")
        if right_top_ratio < PAGE_TOP_MIN_RATIO:
            decision["reasons"].append("right_not_near_page_top")
    columns_ok = False
    column_evidence: dict[str, Any] = {"available": False}
    if left_page_size and right_page_size:
        columns_ok, column_evidence = _columns_compatible(
            left,
            right,
            left_page_width=left_page_size[0],
            right_page_width=right_page_size[0],
        )
    decision["evidence"]["geometry"] = {**column_evidence, **page_geometry_evidence}
    if left_page_size and right_page_size:
        indent_tol = _indent_tolerance(left_page_size[0])
        decision["evidence"]["geometry"]["indent_tolerance"] = round(indent_tol, 6)
        if right_geo["left"] > left_geo["left"] + indent_tol:
            decision["reasons"].append("new_paragraph_indent")
    if not columns_ok and left_page_size and right_page_size:
        decision["reasons"].append("column_or_alignment_mismatch")
    style_ok, style_evidence = _style_compatible(structured, left_node, right_node)
    decision["evidence"]["style"] = style_evidence
    if not style_ok:
        decision["reasons"].append("incompatible_style")
    left_text = str(left.get("text") or "")
    right_text = str(right.get("text") or "")
    syntax_ok, syntax_reason, joined = _syntax_continuation(left_text, right_text)
    decision["evidence"]["syntax_reason"] = syntax_reason
    if not syntax_ok:
        decision["reasons"].append(syntax_reason)
    if decision["reasons"]:
        decision["status"] = "rejected"
        if (
            page_geometry_ok is False
            and left_page_size
            and right_page_size
            and syntax_ok
            and columns_ok
            and style_ok
            and "missing_page_dimensions" not in decision["reasons"]
            and "structural_content_between_endpoints" not in decision["reasons"]
            and set(decision["reasons"]).issubset(
                {"left_not_near_page_bottom", "right_not_near_page_top"}
            )
        ):
            decision["status"] = "uncertain"
        return decision
    if not joined:
        decision["reasons"].append("missing_joined_text")
        decision["status"] = "uncertain"
        return decision
    decision["status"] = "accepted"
    decision["reasons"] = ["cross_page_geometry", syntax_reason]
    decision["evidence"]["joined_text"] = joined
    decision["evidence"]["repair"] = (
        "page_break_hyphen_removed" if syntax_reason == "hyphen_continuation" else "cross_node_space_join"
    )
    return decision


def build_continuation_decisions_ledger(
    page_payloads: list[dict[str, Any]],
    *,
    structured: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    ordered_pages = sorted(page_payloads, key=lambda page: int(page.get("page_no") or 0))
    for left_page, right_page in zip(ordered_pages, ordered_pages[1:]):
        left_page_no = int(left_page.get("page_no") or 0)
        right_page_no = int(right_page.get("page_no") or 0)
        if right_page_no != left_page_no + 1:
            continue
        left_items = _body_text_items(left_page)
        right_items = _body_text_items(right_page)
        if not left_items or not right_items:
            continue
        left = left_items[-1]
        right = right_items[0]
        if left["source_node_id"] == right["source_node_id"]:
            decisions.append(
                evaluate_same_source_node_boundary(
                    left,
                    right,
                    from_page=left_page_no,
                    to_page=right_page_no,
                )
            )
        else:
            decisions.append(
                evaluate_cross_source_node_boundary(
                    left,
                    right,
                    from_page=left_page_no,
                    to_page=right_page_no,
                    structured=structured,
                    left_page=left_page,
                    right_page=right_page,
                )
            )
    fingerprint_payload = [_decision_fingerprint_entry(item) for item in decisions]
    return {
        "schema": SCHEMA,
        "provenance": PROVENANCE,
        "decision_count": len(decisions),
        "document_fingerprint": _sha256_text(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ),
        "decisions": decisions,
    }


def _decision_fingerprint_entry(decision: dict[str, Any]) -> dict[str, Any]:
    evidence = decision.get("evidence") if isinstance(decision.get("evidence"), dict) else {}
    return {
        "decision_id": decision["decision_id"],
        "status": decision["status"],
        "kind": decision.get("kind"),
        "from_page": decision["from_page"],
        "to_page": decision["to_page"],
        "left_source_node_id": decision["left_source_node_id"],
        "right_source_node_id": decision["right_source_node_id"],
        "left_char_end": decision.get("left_char_end"),
        "right_char_start": decision.get("right_char_start"),
        "reasons": list(decision.get("reasons") or []),
        "evidence": {
            "joined_text": evidence.get("joined_text"),
            "repair": evidence.get("repair"),
            "source_separator": evidence.get("source_separator"),
            "syntax_reason": evidence.get("syntax_reason"),
            "style": evidence.get("style"),
            "geometry": evidence.get("geometry"),
        },
    }


def _match_body_item(
    page: dict[str, Any],
    *,
    node_id: str,
    char_start: int | None = None,
    char_end: int | None = None,
) -> dict[str, Any] | None:
    for item in _body_text_items(page):
        if str(item.get("source_node_id")) != node_id:
            continue
        if char_start is not None and int(item.get("source_char_start") or -1) != char_start:
            continue
        if char_end is not None and int(item.get("source_char_end") or -1) != char_end:
            continue
        return item
    return None


def enrich_logical_continuations(
    page_payloads: list[dict[str, Any]],
    continuations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pages_by_no = {
        int(page.get("page_no") or 0): page
        for page in page_payloads
        if isinstance(page, dict)
    }
    enriched: list[dict[str, Any]] = []
    for continuation in continuations:
        if not isinstance(continuation, dict):
            continue
        from_page = int(continuation.get("from_page") or 0)
        to_page = int(continuation.get("to_page") or 0)
        left_page = pages_by_no.get(from_page)
        right_page = pages_by_no.get(to_page)
        if not left_page or not right_page:
            continue
        left = _match_body_item(
            left_page,
            node_id=str(continuation.get("left_source_node_id") or continuation.get("source_node_id") or ""),
            char_end=int(continuation.get("source_char_end") or continuation.get("left_char_end") or -1),
        )
        right = _match_body_item(
            right_page,
            node_id=str(continuation.get("right_source_node_id") or continuation.get("source_node_id") or ""),
            char_start=int(continuation.get("next_source_char_start") or continuation.get("right_char_start") or -1),
        )
        if not left or not right:
            continue
        payload = dict(continuation)
        payload["left_text"] = str(left.get("text") or "").strip()
        payload["right_text"] = str(right.get("text") or "").strip()
        if continuation.get("kind") == "same_source_node":
            payload["source_separator"] = str(right.get("source_separator_before") or "")
        enriched.append(payload)
    return enriched


def logical_continuations_from_ledger(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    continuations: list[dict[str, Any]] = []
    for decision in ledger.get("decisions") or []:
        if not isinstance(decision, dict) or decision.get("status") != "accepted":
            continue
        evidence = decision.get("evidence") if isinstance(decision.get("evidence"), dict) else {}
        joined = str(evidence.get("joined_text") or "").strip()
        if not joined:
            continue
        entry: dict[str, Any] = {
            "decision_id": decision.get("decision_id"),
            "from_page": decision.get("from_page"),
            "to_page": decision.get("to_page"),
            "source_node_id": decision.get("left_source_node_id"),
            "left_source_node_id": decision.get("left_source_node_id"),
            "right_source_node_id": decision.get("right_source_node_id"),
            "source_char_end": decision.get("left_char_end"),
            "next_source_char_start": decision.get("right_char_start"),
            "left_char_start": decision.get("left_char_start"),
            "right_char_end": decision.get("right_char_end"),
            "joined_text": joined,
            "repair": evidence.get("repair"),
            "confidence": decision.get("confidence"),
            "kind": decision.get("kind"),
        }
        style_evidence = evidence.get("style")
        if isinstance(style_evidence, dict):
            entry["style_evidence"] = style_evidence
        if decision.get("kind") == "same_source_node":
            entry["source_separator"] = evidence.get("source_separator", "")
        continuations.append(entry)
    return continuations


def validate_continuation_decisions(payload: dict[str, Any]) -> None:
    if payload.get("schema") != SCHEMA:
        raise ValueError("Unsupported continuation-decisions schema")
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("Continuation decisions require a decision list")
    seen: set[str] = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("Every continuation decision must be an object")
        decision_id = str(decision.get("decision_id") or "")
        if not decision_id or decision_id in seen:
            raise ValueError("Continuation decision ids must be non-empty and unique")
        seen.add(decision_id)
        if decision.get("status") not in STATUSES:
            raise ValueError("Continuation decision status is invalid")


def write_continuation_decisions(
    path,
    page_payloads: list[dict[str, Any]],
    *,
    structured: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = build_continuation_decisions_ledger(page_payloads, structured=structured)
    validate_continuation_decisions(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload
