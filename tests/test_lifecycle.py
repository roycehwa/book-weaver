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

    assert status["schema"] == "phase_a_status_v2"
    assert status["status"] == "accepted"
    assert status["translation_mode"] == "not_requested"
    assert status["final_markdown"] == "book.md"
    assert status["chapter_id_coverage"] == 1.0
    assert status["ready_for_phase_b"] is True
    assert status["phase_b_input"]["mode"] == "source_only"
    assert status["phase_b_input"]["reading_language"] == "zh-CN"
    assert (run_dir / "phase_a_status.json").exists()


def test_finalize_run_allows_english_source_to_enter_phase_b_directly(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_pdf": "/books/source.epub",
                "source_language": "en",
                "target_language": None,
                "translation": {"mode": "not_requested"},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "book.json").write_text(
        json.dumps({"chapters": [{"chapter_id": "chapter-001"}]}),
        encoding="utf-8",
    )
    (run_dir / "book.md").write_text("# Chapter One\n\nEnglish source text.\n", encoding="utf-8")
    (run_dir / "chapter-report.json").write_text("{}", encoding="utf-8")

    status = finalize_run(run_dir)["status"]

    assert status["ready_for_phase_b"] is True
    assert status["phase_b_input"]["mode"] == "source_only"
    assert status["phase_b_input"]["content_source"] == "source_book"
    assert status["phase_b_input"]["source_language"] == "en"
    assert status["phase_b_input"]["reading_language"] == "en"
    assert status["phase_b_input"]["translation_markdown"] is None


def test_finalize_run_prefers_approved_review_version(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    version_dir = run_dir / "versions" / "review-v2"
    version_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_pdf": "/books/source.epub",
                "source_language": "en",
                "target_language": "zh-CN",
                "translation": {"mode": "translated"},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "book.json").write_text(
        json.dumps({"chapters": [{"chapter_id": "chapter-001"}]}),
        encoding="utf-8",
    )
    (run_dir / "book.md").write_text("# Chapter One\n\nEnglish source text.\n", encoding="utf-8")
    (run_dir / "translated.md").write_text("# 第一章\n\n机器初译。\n", encoding="utf-8")
    (run_dir / "chapter-report.json").write_text("{}", encoding="utf-8")
    reviewed_path = version_dir / "translated.md"
    reviewed_path.write_text("# 第一章\n\n用户批准译文。\n", encoding="utf-8")
    (version_dir / "version-manifest.json").write_text(
        json.dumps(
            {
                "schema": "translation_review_version_v2",
                "version": "review-v2",
                "target_language": "zh-CN",
                "created_at": "2026-06-10T10:00:00+00:00",
                "review": {"status": "approved", "approved_at": "2026-06-10T10:00:00+00:00"},
                "files": {"translated_markdown": "versions/review-v2/translated.md"},
            }
        ),
        encoding="utf-8",
    )

    status = finalize_run(run_dir)["status"]

    assert status["final_markdown"] == "versions/review-v2/translated.md"
    assert status["phase_b_input"]["mode"] == "source_plus_translation"
    assert status["phase_b_input"]["content_source"] == "reviewed_translation"
    assert status["phase_b_input"]["review_status"] == "approved"
    assert status["phase_b_input"]["review_version"] == "review-v2"


def test_finalize_run_uses_source_while_translation_review_is_pending(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_pdf": "/books/source.epub",
                "source_language": "en",
                "target_language": "zh-CN",
                "translation": {"mode": "translated"},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "book.json").write_text(
        json.dumps({"chapters": [{"chapter_id": "chapter-001"}]}),
        encoding="utf-8",
    )
    (run_dir / "book.md").write_text("# Chapter One\n\nEnglish source text.\n", encoding="utf-8")
    (run_dir / "translated.md").write_text("# 第一章\n\n机器初译。\n", encoding="utf-8")
    (run_dir / "review_state.json").write_text(json.dumps({"decisions": {}}), encoding="utf-8")
    (run_dir / "chapter-report.json").write_text("{}", encoding="utf-8")

    status = finalize_run(run_dir)["status"]

    assert status["final_markdown"] == "book.md"
    assert status["phase_b_input"]["mode"] == "source_only"
    assert status["phase_b_input"]["content_source"] == "source_book_pending_translation_review"
    assert status["phase_b_input"]["review_status"] == "pending"


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
