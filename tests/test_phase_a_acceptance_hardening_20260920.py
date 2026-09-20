"""Regression tests for 2026-09-20 synthetic whole-book acceptance blockers."""

from __future__ import annotations

import json
from pathlib import Path

from pdf_translator.book_rebuild import build_book_reconstruction
from pdf_translator.continuation_decisions import validate_continuation_decisions
from pdf_translator.source_workspace import inspect_page, page_blocks


def _prov(page_no: int, left: float, top: float, *, bottom: float | None = None, right: float = 500) -> list[dict]:
    return [{
        "page_no": page_no,
        "bbox": {"l": left, "t": top, "b": bottom if bottom is not None else top - 20, "r": right},
    }]


def _docling_pages(height: float = 792.0, width: float = 612.0, count: int = 2) -> dict[str, dict]:
    return {
        str(page_no): {"size": {"width": width, "height": height}}
        for page_no in range(1, count + 1)
    }


def _exact_seventy_six_char_footer_line() -> str:
    line = "Provincial agents extended authority toward the distant northern river fron-"
    assert len(line) == 76
    assert line.endswith("-")
    return line


def test_mislabeled_page_footer_prose_is_kept_and_hyphen_continues() -> None:
    footer_line = _exact_seventy_six_char_footer_line()
    structured = {
        "pages": _docling_pages(),
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
        "texts": [
            {
                "label": "page_footer",
                "text": footer_line,
                "prov": _prov(1, 72, 280, bottom=260, right=520),
            },
            {
                "label": "text",
                "text": "tier settlements during the following decade.",
                "prov": _prov(2, 72, 680, bottom=660, right=510),
            },
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)

    assert "river frontier settlements" in result["chapters"][0]["markdown"].replace("\n", " ")
    decision = result["continuation_decisions"]["decisions"][0]
    assert decision["status"] == "accepted"
    assert decision["evidence"]["repair"] == "page_break_hyphen_removed"


def test_ragged_right_edges_same_column_hyphen_continuation_is_accepted() -> None:
    structured = {
        "pages": _docling_pages(),
        "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
        "texts": [
            {
                "label": "text",
                "text": "The council extended its authority toward the distant northern fron-",
                "prov": _prov(1, 72.253, 280, bottom=260, right=520),
            },
            {
                "label": "text",
                "text": "tier settlements during the following decade.",
                "prov": _prov(2, 72, 680, bottom=660, right=468),
            },
        ],
        "pictures": [],
        "tables": [],
    }

    result = build_book_reconstruction(structured)
    decision = result["continuation_decisions"]["decisions"][0]
    validate_continuation_decisions(result["continuation_decisions"])

    assert decision["status"] == "accepted"
    assert "column_or_alignment_mismatch" not in decision["reasons"]
    assert decision["evidence"]["geometry"]["ragged_right_edges"] is True


def test_accepted_continuation_reconciles_open_source_workspace_issue(tmp_path: Path) -> None:
    page_one = "Opening paragraph on the first page ends with a broken hyphenated fron-"
    pages = {1: page_one, 2: "tier text continues here."}
    ledger = {
        "schema": "bookweaver_continuation_decisions_v1",
        "decisions": [{
            "decision_id": "decision-1",
            "status": "accepted",
            "from_page": 1,
            "to_page": 2,
            "evidence": {
                "syntax_reason": "hyphen_continuation",
                "repair": "page_break_hyphen_removed",
            },
        }],
    }
    (tmp_path / "continuation-decisions.json").write_text(json.dumps(ledger), encoding="utf-8")

    workspace = inspect_page(tmp_path, pages, 1)
    continuation_issues = [issue for issue in workspace["issues"] if issue["code"] == "possible_continuation"]

    assert continuation_issues
    assert continuation_issues[0]["status"] == "reconciled"
    assert not any(
        group["code"] == "possible_continuation"
        for group in workspace["issue_groups"]
    )


def test_unresolved_continuation_surfaces_in_confirmation_quality_summary(tmp_path: Path) -> None:
    ledger = {
        "schema": "bookweaver_continuation_decisions_v1",
        "decisions": [{
            "decision_id": "decision-2",
            "status": "uncertain",
            "from_page": 3,
            "to_page": 4,
            "evidence": {"syntax_reason": "hyphen_continuation"},
            "reasons": ["left_not_near_page_bottom"],
        }],
    }
    (tmp_path / "continuation-decisions.json").write_text(json.dumps(ledger), encoding="utf-8")

    workspace = inspect_page(
        tmp_path,
        {3: "Body ends with an unfinished hyphenated token fron-", 4: "tier continues."},
        3,
    )

    assert workspace["confirmation_quality_issues"]
    assert workspace["confirmation_quality_issues"][0]["code"] == "unresolved_continuation"
    assert "第 3 页到第 4 页" in workspace["confirmation_quality_issues"][0]["message"]
