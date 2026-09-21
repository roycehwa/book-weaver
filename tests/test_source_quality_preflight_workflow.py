from __future__ import annotations

import json
from pathlib import Path

from pdf_translator.jobs import BookJobRunner
from pdf_translator.translation_quality import (
    SOURCE_QUALITY_REPORT,
    TranslationQualityBlockedError,
    confirmation_quality_issues_from_source_report,
    load_confirmation_quality_issues,
    write_source_quality_preflight,
)
from tests.synthetic_quality_fixtures import write_synthetic_reading_units as _write_synthetic


def test_confirmation_quality_issues_from_blocked_source_report() -> None:
    report = {
        "status": "blocked",
        "findings": [
            {
                "severity": "blocking",
                "code": "hyphenated_line_break",
                "evidence": {
                    "chapter": "Body",
                    "line": 12,
                    "excerpt": "transfor-\\nmation",
                },
            }
        ],
    }
    issues = confirmation_quality_issues_from_source_report(report)
    assert len(issues) == 1
    assert issues[0]["severity"] == "error"
    assert issues[0]["code"] == "hyphenated_line_break"
    assert "跨行断词" in issues[0]["message"]
    assert "Body" in issues[0]["message"]
    assert "12" in issues[0]["message"]


def test_preflight_writes_artifacts_and_blocks(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_synthetic(run_dir)
    (run_dir / "translation-input.md").write_text("bro\u00adken\n", encoding="utf-8")
    result = write_source_quality_preflight(run_dir, text_operation="translate")
    assert result["blocked"] is True
    assert (run_dir / SOURCE_QUALITY_REPORT).is_file()
    issues = load_confirmation_quality_issues(run_dir)
    assert any(item["code"] == "soft_hyphen" for item in issues)


def test_job_runner_classifies_source_quality_blocked_as_non_retryable() -> None:
    exc = TranslationQualityBlockedError(
        "blocked",
        reason_zh="源文含未修复断词，请先处理章节确认区的提示。",
    )
    code, retryable = BookJobRunner._classify_failure(exc, "translating")
    assert code == "source_quality_blocked"
    assert retryable is False
    reason = BookJobRunner._safe_failure_reason(exc, code)
    assert reason and "断词" in reason
