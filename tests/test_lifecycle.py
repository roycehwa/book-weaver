from __future__ import annotations

import json
from pathlib import Path

from pdf_translator.lifecycle import cleanup_run, finalize_run


def test_finalize_run_writes_phase_a_status(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_pdf": "/books/source.epub",
                "source_language": "zh-CN",
                "target_language": None,
                "translation": {"mode": "not_requested"},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "book.json").write_text(
        json.dumps({"chapters": [{"chapter_id": "chapter-001"}, {"chapter_id": "chapter-002"}]}),
        encoding="utf-8",
    )
    (run_dir / "book.md").write_text("# 第一章\n", encoding="utf-8")
    (run_dir / "chapter-report.json").write_text("{}", encoding="utf-8")

    result = finalize_run(run_dir)
    status = result["status"]

    assert status["status"] == "accepted"
    assert status["translation_mode"] == "not_requested"
    assert status["final_markdown"] == "book.md"
    assert status["chapter_id_coverage"] == 1.0
    assert status["ready_for_phase_b"] is True
    assert (run_dir / "phase_a_status.json").exists()


def test_cleanup_run_dry_run_and_delete_temp_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "normalized.md").write_text("raw", encoding="utf-8")
    (run_dir / "normalized.json").write_text("{}", encoding="utf-8")
    chapters = run_dir / "chapters"
    chapters.mkdir()
    (chapters / "001.md").write_text("# one", encoding="utf-8")
    cache = run_dir / "translation-cache"
    cache.mkdir()
    (cache / "chunk-0001.md").write_text("cache", encoding="utf-8")

    dry = cleanup_run(run_dir, dry_run=True)
    assert "normalized.md" in dry["report"]["would_remove"]
    assert (run_dir / "normalized.md").exists()

    actual = cleanup_run(run_dir, dry_run=False)
    assert "normalized.md" in actual["report"]["removed"]
    assert not (run_dir / "normalized.md").exists()
    assert not cache.exists()
    assert not chapters.exists()
