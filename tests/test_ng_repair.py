from __future__ import annotations

import json
from pathlib import Path

from pdf_translator.ng_repair import repair_ng_directory


def test_repair_ng_removes_ghost_dir_when_book_already_in_ok(tmp_path: Path) -> None:
    source_root = tmp_path / "文档"
    ng_dir = source_root / "NG" / "Finished Book"
    ok_dir = source_root / "OK" / "Finished Book"
    ng_dir.mkdir(parents=True)
    ok_dir.mkdir(parents=True)
    (ng_dir / "phase-a-status.json").write_text(
        json.dumps({"status": "ng", "source_lane": "EN", "attempt_count": 0}),
        encoding="utf-8",
    )
    (ok_dir / "Finished Book.pdf").write_bytes(b"%PDF")

    report = repair_ng_directory(source_root)

    assert not ng_dir.exists()
    assert report.removed_ghost_dirs == 1
    assert report.reset_retries == 0


def test_repair_ng_resets_retryable_translation_failure(tmp_path: Path) -> None:
    source_root = tmp_path / "文档"
    failed_dir = source_root / "NG" / "Retry Me"
    failed_dir.mkdir(parents=True)
    (failed_dir / "Retry Me.pdf").write_bytes(b"%PDF")
    (failed_dir / "phase-a-status.json").write_text(
        json.dumps(
            {
                "status": "ng",
                "source_lane": "EN",
                "attempt_count": 2,
                "retry_exhausted": True,
                "error_type": "RuntimeError",
                "error": "Translation failed for chunk 12 after 6 attempts: MiniMax translation failed",
            }
        ),
        encoding="utf-8",
    )

    report = repair_ng_directory(source_root)

    status = json.loads((failed_dir / "phase-a-status.json").read_text(encoding="utf-8"))
    assert report.reset_retries == 1
    assert status["attempt_count"] == 0
    assert "retry_exhausted" not in status
    assert status["auto_repair_count"] == 1
    assert (failed_dir / "Retry Me.pdf").exists()


def test_repair_ng_skips_quality_check_failures(tmp_path: Path) -> None:
    source_root = tmp_path / "文档"
    failed_dir = source_root / "NG" / "Bad Quality"
    failed_dir.mkdir(parents=True)
    (failed_dir / "Bad Quality.pdf").write_bytes(b"%PDF")
    (failed_dir / "phase-a-status.json").write_text(
        json.dumps(
            {
                "status": "ng",
                "source_lane": "EN",
                "attempt_count": 2,
                "retry_exhausted": True,
                "error_type": "ValueError",
                "error": "Translation for chunk 3 looks untranslated (ascii=300, cjk=10)",
            }
        ),
        encoding="utf-8",
    )

    report = repair_ng_directory(source_root)

    status = json.loads((failed_dir / "phase-a-status.json").read_text(encoding="utf-8"))
    assert report.reset_retries == 0
    assert status["attempt_count"] == 2
    assert status["retry_exhausted"] is True
