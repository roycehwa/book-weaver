from __future__ import annotations

import copy
from typing import Any

from pdf_translator.page_integrity import PageIntegrityError, build_page_ledger


class IntegrityGateError(ValueError):
    """Raised when an approved export violates the integrity contract."""


_FAILURE_KEYS = (
    "missing_pages",
    "missing_translations",
    "segment_order",
    "unresolved_ocr",
    "missing_assets",
    "broken_footnote_links",
    "absolute_paths",
    "pdf_body_flow_notes",
    "unresolved_review",
)


def _ratio(covered: int, total: int) -> float:
    return round(covered / total, 5) if total else 1.0


def build_integrity_ledger(
    book: dict[str, Any],
    *,
    epub_validation: dict[str, Any] | None = None,
    pdf_validation: dict[str, Any] | None = None,
    review_items: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    segment_conservation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failures = {key: [] for key in _FAILURE_KEYS}
    try:
        page_ledger = build_page_ledger(book)
        page_summary = page_ledger["summary"]
        required_pages = int(page_summary["required_pages"])
        covered_pages = required_pages
    except PageIntegrityError as exc:
        required_pages = sum(
            bool(page.get("has_content"))
            for page in book.get("pages", [])
            if isinstance(page, dict)
        )
        covered_pages = 0
        failures["missing_pages"].append(str(exc))

    semantic = book.get("semantic_content")
    semantic = semantic if isinstance(semantic, dict) else {}
    translatable_spans: list[dict[str, Any]] = []
    all_backlinks: list[str] = []
    semantic_notes: list[dict[str, Any]] = []
    for note in semantic.get("footnotes", []):
        if not isinstance(note, dict):
            continue
        semantic_notes.append(note)
        all_backlinks.extend(str(value) for value in note.get("backlinks", []))
        for span in note.get("spans", []):
            if isinstance(span, dict) and span.get("kind") == "prose":
                translatable_spans.append(span)
    missing_translations = [
        str(span.get("span_id") or "")
        for span in translatable_spans
        if not str(span.get("translated_text") or "").strip()
    ]
    failures["missing_translations"].extend(missing_translations)

    segment_conservation = segment_conservation if isinstance(segment_conservation, dict) else {}
    failures["segment_order"].extend(
        str(value) for value in segment_conservation.get("failures", []) if str(value).strip()
    )

    for record in semantic.get("ocr_quarantine", []):
        if not isinstance(record, dict):
            continue
        if record.get("resolution") not in {"confirmed_noise", "restored_to_reading"}:
            failures["unresolved_ocr"].append(
                str(record.get("quarantine_id") or record.get("block_id") or "unknown")
            )

    epub_validation = epub_validation or {}
    failures["missing_assets"].extend(
        str(value) for value in epub_validation.get("missing_assets", [])
    )
    failures["broken_footnote_links"].extend(
        str(value) for value in epub_validation.get("unresolved_hrefs", [])
    )
    failures["broken_footnote_links"].extend(
        str(note.get("footnote_id") or "unknown")
        for note in semantic_notes
        if not note.get("backlinks") and not bool(note.get("standalone"))
    )
    failures["absolute_paths"].extend(
        str(value) for value in epub_validation.get("absolute_paths", [])
    )
    pdf_validation = pdf_validation or {}
    failures["pdf_body_flow_notes"].extend(
        str(value) for value in pdf_validation.get("body_flow_notes", [])
    )
    failures["unresolved_review"].extend(
        str(item.get("item_id") or item.get("review_id") or "unknown")
        for item in review_items
        if item.get("status") not in {"approved", "resolved", "dismissed"}
    )

    asset_total = len(
        [asset for asset in book.get("assets", []) if isinstance(asset, dict)]
    )
    asset_missing = len(failures["missing_assets"])
    link_total = sum(
        max(1, len(note.get("backlinks", [])))
        for note in semantic_notes
        if not bool(note.get("standalone"))
    )
    link_missing = len(failures["broken_footnote_links"])
    dimensions = {
        "pages": {
            "covered": covered_pages,
            "total": required_pages,
            "ratio": _ratio(covered_pages, required_pages),
        },
        "semantic_spans": {
            "covered": len(translatable_spans) - len(missing_translations),
            "total": len(translatable_spans),
            "ratio": _ratio(
                len(translatable_spans) - len(missing_translations),
                len(translatable_spans),
            ),
        },
        "assets": {
            "covered": max(asset_total - asset_missing, 0),
            "total": asset_total,
            "ratio": _ratio(max(asset_total - asset_missing, 0), asset_total),
        },
        "footnote_links": {
            "covered": max(link_total - link_missing, 0),
            "total": link_total,
            "ratio": _ratio(max(link_total - link_missing, 0), link_total),
        },
    }
    segment_total = int(segment_conservation.get("translatable_segment_count") or 0)
    segment_failures = len(failures["segment_order"])
    if segment_total:
        dimensions["segment_order"] = {
            "covered": max(segment_total - segment_failures, 0),
            "total": segment_total,
            "ratio": _ratio(max(segment_total - segment_failures, 0), segment_total),
        }
    technical_failures = {
        key: values
        for key, values in failures.items()
        if key != "unresolved_review"
    }
    technical_ready = not any(technical_failures.values())
    approved_ready = technical_ready and not failures["unresolved_review"]
    return {
        "schema": "integrity_ledger_v1",
        "dimensions": dimensions,
        "failures": failures,
        "technical_ready": technical_ready,
        "approved_ready": approved_ready,
        # Backward-compatible alias: "ready" always means approved export ready.
        "ready": approved_ready,
    }


def assert_approved_export_ready(ledger: dict[str, Any]) -> None:
    failures = ledger.get("failures")
    failures = failures if isinstance(failures, dict) else {}
    blocking = {
        key: values
        for key, values in failures.items()
        if isinstance(values, list) and values
    }
    if blocking:
        details = "; ".join(
            f"{key}: {', '.join(str(value) for value in values[:8])}"
            for key, values in blocking.items()
        )
        raise IntegrityGateError(f"Approved export blocked by integrity failures: {details}")


def refresh_review_readiness(
    ledger: dict[str, Any],
    *,
    review_items: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    review_state: dict[str, Any],
) -> dict[str, Any]:
    """Project current review decisions into an existing technical ledger."""

    refreshed = copy.deepcopy(ledger)
    failures = refreshed.setdefault("failures", {})
    decisions = review_state.get("decisions")
    decisions = decisions if isinstance(decisions, dict) else {}
    unresolved: list[str] = []
    for item in review_items:
        segment_id = str(item.get("segment_id") or "")
        decision = decisions.get(segment_id)
        decision = decision if isinstance(decision, dict) else {}
        current_status = str(
            decision.get("status") or item.get("status") or "open"
        )
        if current_status not in {"approved", "resolved", "dismissed"}:
            unresolved.append(
                str(
                    item.get("item_id")
                    or item.get("review_id")
                    or segment_id
                    or "unknown"
                )
            )
    failures["unresolved_review"] = unresolved
    technical_ready = not any(
        values for key, values in failures.items() if key != "unresolved_review"
    )
    approved_ready = technical_ready and not unresolved
    refreshed["technical_ready"] = technical_ready
    refreshed["approved_ready"] = approved_ready
    refreshed["ready"] = approved_ready
    return refreshed
