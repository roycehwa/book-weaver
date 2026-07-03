from __future__ import annotations

import json
from pathlib import Path

from scripts.verify_scale_readiness import verify_run


def test_verify_run_accepts_complete_integrity_ledger(tmp_path: Path) -> None:
    (tmp_path / "integrity-ledger.json").write_text(
        json.dumps(
            {
                "ready": True,
                "dimensions": {
                    "pages": {"ratio": 1.0},
                    "semantic_spans": {"ratio": 1.0},
                    "assets": {"ratio": 1.0},
                    "footnote_links": {"ratio": 1.0},
                },
                "failures": {},
            }
        ),
        encoding="utf-8",
    )

    assert verify_run(tmp_path) == []


def test_verify_run_reports_missing_ledger_and_incomplete_dimensions(
    tmp_path: Path,
) -> None:
    assert verify_run(tmp_path) == ["missing integrity-ledger.json"]
    (tmp_path / "integrity-ledger.json").write_text(
        json.dumps(
            {
                "ready": False,
                "dimensions": {
                    "pages": {"ratio": 1.0},
                    "semantic_spans": {"ratio": 0.5},
                    "assets": {"ratio": 1.0},
                    "footnote_links": {"ratio": 0.0},
                },
                "failures": {"missing_translations": ["span-a"]},
            }
        ),
        encoding="utf-8",
    )

    errors = verify_run(tmp_path)

    assert "semantic_spans coverage is 0.5" in errors
    assert "footnote_links coverage is 0.0" in errors
    assert "missing_translations: span-a" in errors


def test_verify_run_allows_open_human_review_when_technical_gate_passes(
    tmp_path: Path,
) -> None:
    (tmp_path / "integrity-ledger.json").write_text(
        json.dumps(
            {
                "technical_ready": True,
                "approved_ready": False,
                "ready": False,
                "dimensions": {
                    "pages": {"ratio": 1.0},
                    "semantic_spans": {"ratio": 1.0},
                    "assets": {"ratio": 1.0},
                    "footnote_links": {"ratio": 1.0},
                },
                "failures": {"unresolved_review": ["review-a"]},
            }
        ),
        encoding="utf-8",
    )

    assert verify_run(tmp_path) == []
