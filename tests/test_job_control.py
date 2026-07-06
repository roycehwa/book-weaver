from __future__ import annotations

import json
from pathlib import Path

from pdf_translator.job_control import (
    TranslationJobObserver,
    create_translation_job,
    load_progress,
)


def test_create_translation_job_writes_state_and_progress(tmp_path: Path) -> None:
    observer = create_translation_job(
        run_dir=tmp_path,
        translator="mock",
        source_language="en",
        target_language="zh-CN",
        total_chunks=3,
        concurrency=2,
        max_chunk_chars=9000,
        resume=False,
    )

    job_path = tmp_path / "jobs" / "translation-job.json"
    progress_path = tmp_path / "jobs" / "progress.json"
    events_path = tmp_path / "jobs" / "translation-events.jsonl"

    assert isinstance(observer, TranslationJobObserver)
    assert job_path.exists()
    assert progress_path.exists()
    assert events_path.exists()

    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["schema"] == "translation_job_v1"
    assert job["status"] == "running"
    assert job["translator"] == "mock"
    assert job["total_chunks"] == 3

    progress = load_progress(tmp_path)
    assert progress["total_chunks"] == 3
    assert progress["completed_chunks"] == 0
    assert progress["failed_chunks"] == 0
    assert progress["cache_hit_chunks"] == 0


def test_resume_does_not_precount_unvalidated_cache_files(tmp_path: Path) -> None:
    cache_dir = tmp_path / "translation-cache"
    cache_dir.mkdir()
    (cache_dir / "chunk-000000-stalehash.md").write_text(
        "stale translation",
        encoding="utf-8",
    )

    observer = create_translation_job(
        run_dir=tmp_path,
        translator="mock",
        source_language="en",
        target_language="zh-CN",
        total_chunks=1,
        concurrency=1,
        max_chunk_chars=9000,
        resume=True,
    )

    progress = load_progress(tmp_path)
    assert progress["completed_chunks"] == 0
    assert progress["cache_hit_chunks"] == 0

    observer.cache_hit(
        chunk_index=0,
        input_hash="currenthash",
        cache_path=cache_dir / "chunk-000000-currenthash.md",
    )
    assert load_progress(tmp_path)["cache_hit_chunks"] == 1


def test_observer_records_chunk_success_and_progress(tmp_path: Path) -> None:
    observer = create_translation_job(
        run_dir=tmp_path,
        translator="mock",
        source_language="en",
        target_language="zh-CN",
        total_chunks=2,
        concurrency=1,
        max_chunk_chars=9000,
        resume=False,
    )

    observer.attempt_start(chunk_index=0, input_hash="abc", attempt=1)
    observer.attempt_success(chunk_index=0, input_hash="abc", cache_path=tmp_path / "cache" / "c0.md")
    observer.cache_hit(chunk_index=1, input_hash="def", cache_path=tmp_path / "cache" / "c1.md")
    observer.finish(status="completed")

    progress = load_progress(tmp_path)
    assert progress["completed_chunks"] == 2
    assert progress["cache_hit_chunks"] == 1
    assert progress["running_chunks"] == 0
    assert progress["status"] == "completed"

    events = (tmp_path / "jobs" / "translation-events.jsonl").read_text(encoding="utf-8").splitlines()
    assert any('"event": "attempt_start"' in line for line in events)
    assert any('"event": "attempt_success"' in line for line in events)
    assert any('"event": "cache_hit"' in line for line in events)


def test_observer_expands_total_for_dynamic_fallback_chunk(tmp_path: Path) -> None:
    observer = create_translation_job(
        run_dir=tmp_path,
        translator="mock",
        source_language="en",
        target_language="zh-CN",
        total_chunks=2,
        concurrency=1,
        max_chunk_chars=9000,
        resume=False,
    )

    observer.attempt_start(chunk_index=4, input_hash="fallback", attempt=1)
    observer.attempt_success(
        chunk_index=4,
        input_hash="fallback",
        cache_path=tmp_path / "translation-cache" / "fallback.md",
    )

    progress = load_progress(tmp_path)
    assert progress["total_chunks"] == 5
    assert progress["completed_chunks"] == 1
    assert progress["remaining_chunks"] == 4


def test_completed_job_reconciles_estimate_to_actual_chunk_count(
    tmp_path: Path,
) -> None:
    observer = create_translation_job(
        run_dir=tmp_path,
        translator="mock",
        source_language="en",
        target_language="zh-CN",
        total_chunks=3,
        concurrency=1,
        max_chunk_chars=9000,
        resume=False,
    )
    observer.cache_hit(
        chunk_index=0,
        input_hash="a",
        cache_path=tmp_path / "translation-cache" / "a.md",
    )
    observer.cache_hit(
        chunk_index=1,
        input_hash="b",
        cache_path=tmp_path / "translation-cache" / "b.md",
    )

    observer.finish(status="completed")

    progress = load_progress(tmp_path)
    assert progress["total_chunks"] == 2
    assert progress["completed_chunks"] == 2
    assert progress["remaining_chunks"] == 0


def test_observer_records_failure_without_losing_prior_success(tmp_path: Path) -> None:
    observer = create_translation_job(
        run_dir=tmp_path,
        translator="mock",
        source_language="en",
        target_language="zh-CN",
        total_chunks=2,
        concurrency=1,
        max_chunk_chars=9000,
        resume=False,
    )

    observer.attempt_success(chunk_index=0, input_hash="abc", cache_path=tmp_path / "cache" / "c0.md")
    observer.attempt_failure(
        chunk_index=1,
        input_hash="def",
        attempt=1,
        error_type="ValueError",
        message="empty translation",
        retryable=True,
    )
    observer.attempt_failure(
        chunk_index=2,
        input_hash="ghi",
        attempt=3,
        error_type="ValueError",
        message="final failure",
        retryable=False,
    )
    observer.finish(status="failed")

    progress = load_progress(tmp_path)
    assert progress["completed_chunks"] == 1
    assert progress["failed_chunks"] == 1
    assert progress["retrying_chunks"] == 0


def test_retrying_chunk_is_cleared_after_success(tmp_path: Path) -> None:
    observer = create_translation_job(
        run_dir=tmp_path,
        translator="mock",
        source_language="en",
        target_language="zh-CN",
        total_chunks=1,
        concurrency=1,
        max_chunk_chars=9000,
        resume=False,
    )

    observer.attempt_start(chunk_index=0, input_hash="abc", attempt=1)
    observer.attempt_failure(
        chunk_index=0,
        input_hash="abc",
        attempt=1,
        error_type="ValueError",
        message="retry",
        retryable=True,
    )
    observer.attempt_start(chunk_index=0, input_hash="abc", attempt=2)
    observer.attempt_failure(
        chunk_index=0,
        input_hash="abc",
        attempt=2,
        error_type="ValueError",
        message="retry again",
        retryable=True,
    )
    observer.attempt_start(chunk_index=0, input_hash="abc", attempt=3)
    observer.attempt_success(
        chunk_index=0,
        input_hash="abc",
        cache_path=tmp_path / "cache" / "c0.md",
    )

    progress = load_progress(tmp_path)
    assert progress["running_chunks"] == 0
    assert progress["retrying_chunks"] == 0
    assert progress["failed_chunks"] == 0


def test_observer_does_not_double_count_same_chunk(tmp_path: Path) -> None:
    observer = create_translation_job(
        run_dir=tmp_path,
        translator="mock",
        source_language="en",
        target_language="zh-CN",
        total_chunks=2,
        concurrency=1,
        max_chunk_chars=9000,
        resume=False,
    )

    observer.cache_hit(chunk_index=0, input_hash="abc", cache_path=tmp_path / "cache" / "c0.md")
    observer.attempt_success(chunk_index=0, input_hash="abc", cache_path=tmp_path / "cache" / "c0.md")
    observer.attempt_success(chunk_index=0, input_hash="abc", cache_path=tmp_path / "cache" / "c0.md")

    progress = load_progress(tmp_path)
    assert progress["completed_chunks"] == 1
    assert progress["cache_hit_chunks"] == 1


def test_format_progress_line_shows_bar_and_counts() -> None:
    from pdf_translator.job_control import format_progress_bar, format_progress_line

    assert format_progress_bar(5, 10).startswith("[")
    line = format_progress_line(
        {
            "status": "running",
            "completed_chunks": 5,
            "total_chunks": 10,
            "running_chunks": 1,
            "cache_hit_chunks": 2,
            "failed_chunks": 0,
            "estimated_remaining_seconds": 90,
        }
    )
    assert "5/10" in line
    assert "cache=2" in line
