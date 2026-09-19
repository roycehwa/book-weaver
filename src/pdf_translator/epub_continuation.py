"""EPUB spine resource boundary continuation decisions and chapter merging."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pdf_translator.continuation_decisions import (
    SCHEMA,
    _syntax_continuation,
    logical_continuations_from_ledger,
    validate_continuation_decisions,
)

EPUB_BOUNDARY_PROVENANCE = "epub_spine_resource_boundary_v1"

HEADING_MARKDOWN_RE = re.compile(r"^#{1,6}\s+\S")
LIST_MARKDOWN_RE = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)")
FIGURE_MARKDOWN_RE = re.compile(r"^!\[")

WEAK_SPINE_TITLE_RE = re.compile(r"^(?:section|chapter|part)?\s*\d*\s*$", re.IGNORECASE)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _epub_endpoint_id(resource_path: str, dom_path: str, char_start: int, char_end: int) -> str:
    payload = f"{resource_path}:{dom_path}:{char_start}:{char_end}"
    return _sha256_text(payload)[:24]


def _decision_id(
    *,
    kind: str,
    from_page: int,
    to_page: int,
    left_id: str,
    right_id: str,
) -> str:
    payload = f"{kind}:{from_page}:{to_page}:{left_id}:{right_id}"
    return _sha256_text(payload)[:24]


def _unit_markdown(unit: dict[str, Any]) -> str:
    return str(unit.get("markdown") or "").strip()


def _is_heading_unit(unit: dict[str, Any]) -> bool:
    markdown = _unit_markdown(unit)
    dom_path = str(unit.get("dom_path") or "")
    if HEADING_MARKDOWN_RE.match(markdown):
        return True
    return bool(re.search(r"/h[1-4]\[\d+\]$", dom_path, flags=re.IGNORECASE))


def _is_paragraph_barrier_unit(unit: dict[str, Any]) -> bool:
    markdown = _unit_markdown(unit)
    if not markdown:
        return True
    if _is_heading_unit(unit):
        return True
    if LIST_MARKDOWN_RE.match(markdown):
        return True
    if FIGURE_MARKDOWN_RE.match(markdown):
        return True
    if markdown.startswith(">"):
        return True
    if markdown.startswith(("```", "~~~")):
        return True
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    if lines and all(line.startswith("|") and line.endswith("|") for line in lines):
        return True
    return False


def _body_units(chapter: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(unit)
        for unit in chapter.get("dom_units") or []
        if isinstance(unit, dict) and _unit_markdown(unit)
    ]


def _final_paragraph_unit(chapter: dict[str, Any]) -> dict[str, Any] | None:
    for unit in reversed(_body_units(chapter)):
        if not _is_paragraph_barrier_unit(unit):
            return unit
    return None


def _first_paragraph_unit(chapter: dict[str, Any]) -> dict[str, Any] | None:
    for unit in _body_units(chapter):
        if not _is_paragraph_barrier_unit(unit):
            return unit
    return None


def _weak_fallback_title(title: str, spine_id: str | None) -> bool:
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", title).strip()
    if not cleaned:
        return True
    if WEAK_SPINE_TITLE_RE.match(cleaned):
        return True
    if spine_id and cleaned.lower() == spine_id.replace("_", " ").lower():
        return True
    stem = spine_id or ""
    if stem and cleaned.lower() == stem.lower():
        return True
    return False


def _resource_policy_class(chapter: dict[str, Any]) -> str:
    title = str(chapter.get("title") or "").strip().lower()
    if chapter.get("preserve_original") or chapter.get("resource_only"):
        return "preserve"
    if title in {"notes", "bibliography", "references", "glossary", "index"}:
        return "apparatus"
    return "body"


def _nav_chapter_boundary(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_nav = str(left.get("nav_label") or "").strip()
    right_nav = str(right.get("nav_label") or "").strip()
    if not right_nav:
        return False
    if right_nav == left_nav:
        return False
    if _weak_fallback_title(right_nav, str(right.get("spine_id") or "")):
        return False
    return True


def _heading_boundary(right: dict[str, Any]) -> bool:
    units = _body_units(right)
    if not units:
        return False
    return _is_heading_unit(units[0])


def evaluate_epub_chapter_group(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    from_page: int,
    to_page: int,
) -> dict[str, Any]:
    left_path = str(left.get("source_internal_path") or "")
    right_path = str(right.get("source_internal_path") or "")
    decision: dict[str, Any] = {
        "decision_id": _decision_id(
            kind="epub_chapter_group",
            from_page=from_page,
            to_page=to_page,
            left_id=left_path,
            right_id=right_path,
        ),
        "status": "rejected",
        "confidence": "high_confidence_epub_spine",
        "kind": "epub_chapter_group",
        "from_page": from_page,
        "to_page": to_page,
        "left_resource_path": left_path,
        "right_resource_path": right_path,
        "provenance": EPUB_BOUNDARY_PROVENANCE,
        "reasons": [],
        "evidence": {"axis": "chapter_group"},
    }
    if not left_path or not right_path:
        decision["reasons"].append("missing_resource_path")
        return decision
    if _heading_boundary(right):
        decision["reasons"].append("heading_boundary")
        return decision
    if _nav_chapter_boundary(left, right):
        decision["reasons"].append("nav_chapter_boundary")
        return decision
    left_policy = _resource_policy_class(left)
    right_policy = _resource_policy_class(right)
    if left_policy != right_policy:
        decision["reasons"].append("policy_transition")
        return decision
    decision["status"] = "accepted"
    decision["reasons"] = ["spine_order_continuation"]
    return decision


def _link_targets_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_targets = {str(target) for target in left.get("link_targets") or []}
    right_targets = {str(target) for target in right.get("link_targets") or []}
    if not left_targets and not right_targets:
        return True
    return left_targets == right_targets


def evaluate_epub_paragraph_join(
    left: dict[str, Any],
    right: dict[str, Any],
    left_unit: dict[str, Any],
    right_unit: dict[str, Any],
    *,
    from_page: int,
    to_page: int,
    chapter_group_accepted: bool,
) -> dict[str, Any]:
    left_path = str(left.get("source_internal_path") or "")
    right_path = str(right.get("source_internal_path") or "")
    left_dom = str(left_unit.get("dom_path") or "")
    right_dom = str(right_unit.get("dom_path") or "")
    left_start = int(left_unit.get("char_start") or 0)
    left_end = int(left_unit.get("char_end") or 0)
    right_start = int(right_unit.get("char_start") or 0)
    right_end = int(right_unit.get("char_end") or 0)
    left_id = _epub_endpoint_id(left_path, left_dom, left_start, left_end)
    right_id = _epub_endpoint_id(right_path, right_dom, right_start, right_end)
    decision: dict[str, Any] = {
        "decision_id": _decision_id(
            kind="epub_paragraph_join",
            from_page=from_page,
            to_page=to_page,
            left_id=left_id,
            right_id=right_id,
        ),
        "status": "rejected",
        "confidence": "high_confidence_epub_spine",
        "kind": "epub_paragraph_join",
        "from_page": from_page,
        "to_page": to_page,
        "left_resource_path": left_path,
        "right_resource_path": right_path,
        "left_dom_path": left_dom,
        "right_dom_path": right_dom,
        "left_char_start": left_start,
        "left_char_end": left_end,
        "right_char_start": right_start,
        "right_char_end": right_end,
        "provenance": EPUB_BOUNDARY_PROVENANCE,
        "reasons": [],
        "evidence": {"axis": "paragraph_join"},
    }
    if not chapter_group_accepted:
        decision["reasons"].append("chapter_boundary")
        return decision
    if _is_paragraph_barrier_unit(left_unit) or _is_paragraph_barrier_unit(right_unit):
        decision["reasons"].append("structural_barrier")
        return decision
    if not _link_targets_compatible(left_unit, right_unit):
        decision["reasons"].append("incompatible_link_targets")
        return decision
    left_text = _unit_markdown(left_unit)
    right_text = _unit_markdown(right_unit)
    syntax_ok, syntax_reason, joined = _syntax_continuation(left_text, right_text)
    decision["evidence"]["syntax_reason"] = syntax_reason
    if not syntax_ok:
        decision["reasons"].append(syntax_reason)
        return decision
    if not joined:
        decision["reasons"].append("missing_joined_text")
        decision["status"] = "uncertain"
        return decision
    decision["status"] = "accepted"
    decision["reasons"] = ["cross_resource_syntax", syntax_reason]
    decision["evidence"]["joined_text"] = joined
    decision["evidence"]["left_text"] = left_text
    decision["evidence"]["right_text"] = right_text
    decision["evidence"]["repair"] = (
        "page_break_hyphen_removed" if syntax_reason == "hyphen_continuation" else "cross_resource_space_join"
    )
    return decision


def _decision_fingerprint_entry(decision: dict[str, Any]) -> dict[str, Any]:
    evidence = decision.get("evidence") if isinstance(decision.get("evidence"), dict) else {}
    return {
        "decision_id": decision["decision_id"],
        "status": decision["status"],
        "kind": decision.get("kind"),
        "from_page": decision["from_page"],
        "to_page": decision["to_page"],
        "reasons": list(decision.get("reasons") or []),
        "left_resource_path": decision.get("left_resource_path"),
        "right_resource_path": decision.get("right_resource_path"),
        "left_dom_path": decision.get("left_dom_path"),
        "right_dom_path": decision.get("right_dom_path"),
        "left_char_end": decision.get("left_char_end"),
        "right_char_start": decision.get("right_char_start"),
        "evidence": {
            "joined_text": evidence.get("joined_text"),
            "repair": evidence.get("repair"),
            "syntax_reason": evidence.get("syntax_reason"),
        },
    }


def build_epub_continuation_ledger(spine_chapters: list[dict[str, Any]]) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    accepted_groups: dict[tuple[int, int], bool] = {}
    for left, right in zip(spine_chapters, spine_chapters[1:]):
        if not isinstance(left, dict) or not isinstance(right, dict):
            continue
        from_page = int(left.get("page_no") or left.get("page_start") or 0)
        to_page = int(right.get("page_no") or right.get("page_start") or 0)
        if from_page <= 0 or to_page <= 0:
            continue
        group_decision = evaluate_epub_chapter_group(left, right, from_page=from_page, to_page=to_page)
        decisions.append(group_decision)
        accepted_groups[(from_page, to_page)] = group_decision.get("status") == "accepted"
        left_unit = _final_paragraph_unit(left)
        right_unit = _first_paragraph_unit(right)
        if left_unit and right_unit:
            decisions.append(
                evaluate_epub_paragraph_join(
                    left,
                    right,
                    left_unit,
                    right_unit,
                    from_page=from_page,
                    to_page=to_page,
                    chapter_group_accepted=accepted_groups[(from_page, to_page)],
                )
            )
    fingerprint_payload = [_decision_fingerprint_entry(item) for item in decisions]
    return {
        "schema": SCHEMA,
        "provenance": EPUB_BOUNDARY_PROVENANCE,
        "decision_count": len(decisions),
        "document_fingerprint": _sha256_text(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ),
        "decisions": decisions,
    }


def _merge_markdown_parts(left_md: str, right_md: str, paragraph_join: dict[str, Any] | None) -> str:
    left = left_md.rstrip()
    right = right_md.lstrip()
    if paragraph_join and paragraph_join.get("status") == "accepted":
        evidence = paragraph_join.get("evidence") if isinstance(paragraph_join.get("evidence"), dict) else {}
        left_text = str(evidence.get("left_text") or "")
        right_text = str(evidence.get("right_text") or "")
        joined = str(evidence.get("joined_text") or "")
        if left_text and right_text and joined and left.endswith(left_text) and right.startswith(right_text):
            remainder = right[len(right_text) :].lstrip()
            combined = left[: -len(left_text)] + joined
            if remainder:
                combined = combined.rstrip() + "\n\n" + remainder
            return combined + ("\n" if not combined.endswith("\n") else "")
    parts = [part for part in (left, right) if part.strip()]
    combined = "\n\n".join(parts)
    if combined and not combined.endswith("\n"):
        combined += "\n"
    return combined


def _merge_trace_markdown(left_trace: str, right_trace: str) -> str:
    left = left_trace.rstrip()
    right = right_trace.lstrip()
    if not left:
        return right
    if not right:
        return left
    return f"{left}\n\n{right}"


def _merge_dom_units(
    left_units: list[dict[str, Any]],
    right_units: list[dict[str, Any]],
    paragraph_join: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    merged = [dict(unit) for unit in left_units if isinstance(unit, dict)]
    if paragraph_join and paragraph_join.get("status") == "accepted":
        evidence = paragraph_join.get("evidence") if isinstance(paragraph_join.get("evidence"), dict) else {}
        joined = str(evidence.get("joined_text") or "")
        left_dom = str(paragraph_join.get("left_dom_path") or "")
        right_dom = str(paragraph_join.get("right_dom_path") or "")
        if joined and left_dom and right_dom:
            for unit in merged:
                if str(unit.get("dom_path") or "") != left_dom:
                    continue
                unit["markdown"] = joined
                unit["char_end"] = len(joined)
                left_targets = [str(target) for target in unit.get("link_targets") or []]
                right_unit = next(
                    (item for item in right_units if str(item.get("dom_path") or "") == right_dom),
                    None,
                )
                if isinstance(right_unit, dict):
                    for target in right_unit.get("link_targets") or []:
                        target_text = str(target)
                        if target_text and target_text not in left_targets:
                            left_targets.append(target_text)
                if left_targets:
                    unit["link_targets"] = left_targets
                unit["continuation"] = {
                    "decision_id": paragraph_join.get("decision_id"),
                    "right_resource_path": paragraph_join.get("right_resource_path"),
                    "right_dom_path": right_dom,
                    "right_char_start": paragraph_join.get("right_char_start"),
                    "right_char_end": paragraph_join.get("right_char_end"),
                }
                break
            skip_dom = {right_dom}
            merged.extend(
                dict(unit)
                for unit in right_units
                if isinstance(unit, dict) and str(unit.get("dom_path") or "") not in skip_dom
            )
            return merged
    merged.extend(dict(unit) for unit in right_units if isinstance(unit, dict))
    return merged


def apply_epub_spine_continuations(
    spine_chapters: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    ledger = build_epub_continuation_ledger(spine_chapters)
    validate_continuation_decisions(ledger)
    decisions_by_pair: dict[tuple[int, int], dict[str, dict[str, Any]]] = {}
    for decision in ledger.get("decisions") or []:
        if not isinstance(decision, dict):
            continue
        key = (int(decision.get("from_page") or 0), int(decision.get("to_page") or 0))
        bucket = decisions_by_pair.setdefault(key, {})
        kind = str(decision.get("kind") or "")
        bucket[kind] = decision

    merged: list[dict[str, Any]] = []
    index = 0
    while index < len(spine_chapters):
        current = dict(spine_chapters[index])
        page_no = int(current.get("page_no") or current.get("page_start") or index + 1)
        current["page_start"] = page_no
        current["page_end"] = page_no
        current["source_pages"] = [page_no]
        paths = [
            str(current.get("source_internal_path") or "").replace("\\", "/")
        ]
        paths = [path for path in paths if path]
        current["source_internal_paths"] = list(dict.fromkeys(paths))
        next_index = index + 1
        while next_index < len(spine_chapters):
            right = spine_chapters[next_index]
            right_page = int(right.get("page_no") or right.get("page_start") or next_index + 1)
            pair = decisions_by_pair.get((page_no, right_page), {})
            group = pair.get("epub_chapter_group")
            if not group or group.get("status") != "accepted":
                break
            paragraph_join = pair.get("epub_paragraph_join")
            current["markdown"] = _merge_markdown_parts(
                str(current.get("markdown") or ""),
                str(right.get("markdown") or ""),
                paragraph_join,
            )
            current["trace_markdown"] = _merge_trace_markdown(
                str(current.get("trace_markdown") or ""),
                str(right.get("trace_markdown") or ""),
            )
            current["dom_units"] = _merge_dom_units(
                list(current.get("dom_units") or []),
                list(right.get("dom_units") or []),
                paragraph_join,
            )
            right_path = str(right.get("source_internal_path") or "").replace("\\", "/")
            if right_path:
                current["source_internal_paths"] = list(
                    dict.fromkeys([*current.get("source_internal_paths") or [], right_path])
                )
            current["page_end"] = right_page
            current["source_pages"] = list(range(page_no, right_page + 1))
            page_no = right_page
            next_index += 1
        if len(current.get("source_internal_paths") or []) == 1:
            current["source_internal_path"] = current["source_internal_paths"][0]
        else:
            current["source_internal_path"] = current["source_internal_paths"][0] if current.get("source_internal_paths") else None
        merged.append(current)
        index = next_index

    logical_continuations = logical_continuations_from_ledger(ledger)
    return merged, logical_continuations, ledger
