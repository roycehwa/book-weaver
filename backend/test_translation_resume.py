from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from job_service import BookJobService


def _snapshot(job_id: str, *, state: str = "translating") -> dict:
    return {
        "schema": "book_job_v1",
        "job_id": job_id,
        "state": state,
        "request": {"processing_mode": "translate"},
        "resolved": {"text_operation": "translate"},
        "artifacts": {},
    }


def _write_job(jobs_dir: Path, job_id: str, snapshot: dict) -> None:
    job_dir = jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")


def _write_translation_gates(jobs_dir: Path, job_id: str, snapshot: dict) -> None:
    job_dir = jobs_dir / job_id
    run_dir = job_dir / "artifacts" / "book-run"
    (run_dir / "glossary").mkdir(parents=True, exist_ok=True)
    (run_dir / "book.json").write_text(json.dumps({"chapters": []}), encoding="utf-8")
    (run_dir / "workflow.json").write_text(
        json.dumps(
            {
                "schema": "phase_a_workflow_v1",
                "stage": "glossary_ready",
                "glossary_finalized_by_user": True,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "glossary" / "active.json").write_text(
        json.dumps(
            {
                "schema": "phase_a_glossary_v1",
                "entries": [
                    {
                        "source": "Good Company",
                        "target": "好公司",
                        "type": "name_or_key_term",
                        "status": "active",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "artifacts" / "canonical-chapters.json").write_text(
        json.dumps(
            {
                "schema": "bookmate_canonical_chapters_v1",
                "job_id": job_id,
                "source_artifact": "user_confirmation",
                "chapters": [{"index": 1, "chapter_id": "ch-1", "title": "Manual"}],
            }
        ),
        encoding="utf-8",
    )
    snapshot["artifacts"] = {
        **(snapshot.get("artifacts") if isinstance(snapshot.get("artifacts"), dict) else {}),
        "book": {"href": "artifacts/book-run/book.json"},
        "canonical_chapters": {"href": "artifacts/canonical-chapters.json"},
        "glossary_active": {"href": "artifacts/book-run/glossary/active.json"},
    }
    _write_job(jobs_dir, job_id, snapshot)


def _write_progress(
    jobs_dir: Path,
    job_id: str,
    *,
    updated_at: str,
    running_chunks: int = 0,
) -> None:
    run_dir = jobs_dir / job_id / "artifacts" / "book-run" / "jobs"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "progress.json").write_text(
        json.dumps(
            {
                "status": "running",
                "updated_at": updated_at,
                "completed_chunks": 10,
                "total_chunks": 20,
                "running_chunks": running_chunks,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_translation_resume_unavailable_while_active(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    job_id = "job-active"
    _write_job(jobs_dir, job_id, _snapshot(job_id))
    updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_progress(jobs_dir, job_id, updated_at=updated_at, running_chunks=2)

    service = BookJobService(jobs_dir=jobs_dir, project_home=tmp_path)
    snapshot = service.get(job_id)

    assert snapshot["translation_resume"]["available"] is False
    assert snapshot["translation_resume"]["reason"] == "already_running"


def test_translation_resume_available_when_stalled(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    job_id = "job-stalled"
    snapshot = _snapshot(job_id)
    _write_translation_gates(jobs_dir, job_id, snapshot)
    updated_at = (datetime.now(timezone.utc) - timedelta(minutes=12)).isoformat().replace("+00:00", "Z")
    _write_progress(jobs_dir, job_id, updated_at=updated_at, running_chunks=3)

    service = BookJobService(jobs_dir=jobs_dir, project_home=tmp_path)
    snapshot = service.get(job_id)

    assert snapshot["translation_resume"]["available"] is True
    assert snapshot["translation_resume"]["label"] == "从检查点恢复"


def test_translation_resume_cooldown_after_recent_request(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    job_id = "job-cooldown"
    snapshot = _snapshot(job_id, state="failed")
    snapshot["failed_stage"] = "translating"
    snapshot["error"] = {"code": "job_stage_failed", "message": "failed", "retryable": True}
    _write_job(jobs_dir, job_id, snapshot)
    updated_at = (datetime.now(timezone.utc) - timedelta(minutes=12)).isoformat().replace("+00:00", "Z")
    _write_progress(jobs_dir, job_id, updated_at=updated_at)

    service = BookJobService(jobs_dir=jobs_dir, project_home=tmp_path)
    service.record_resume_request(job_id)
    enriched = service.get(job_id)

    assert enriched["translation_resume"]["available"] is False
    assert enriched["translation_resume"]["reason"] == "cooldown"


def test_translation_activity_marks_failed_when_progress_failed(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    job_id = "job-progress-failed"
    snapshot = _snapshot(job_id)
    _write_translation_gates(jobs_dir, job_id, snapshot)
    run_dir = jobs_dir / job_id / "artifacts" / "book-run" / "jobs"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "progress.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "completed_chunks": 2,
                "total_chunks": 138,
                "running_chunks": 0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (run_dir / "translation-events.jsonl").write_text(
        '{"event":"job_finished","status":"failed","timestamp":"2026-07-05T08:06:55Z"}\n',
        encoding="utf-8",
    )

    service = BookJobService(jobs_dir=jobs_dir, project_home=tmp_path)
    enriched = service.get(job_id)

    assert enriched["state"] == "failed"
    assert enriched["translation_activity"]["status"] == "failed"
    assert enriched["translation_resume"]["available"] is True


def test_translation_resume_available_when_failed(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    job_id = "job-failed"
    snapshot = _snapshot(job_id, state="failed")
    snapshot["failed_stage"] = "translating"
    snapshot["error"] = {
        "code": "job_stage_failed",
        "message": "Job failed during translating.",
        "retryable": True,
    }
    _write_translation_gates(jobs_dir, job_id, snapshot)
    updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _write_progress(jobs_dir, job_id, updated_at=updated_at)

    service = BookJobService(jobs_dir=jobs_dir, project_home=tmp_path)
    enriched = service.get(job_id)

    assert enriched["translation_resume"]["available"] is True
    assert enriched["translation_resume"]["label"] == "从检查点恢复"


def test_get_ignores_stale_persisted_derived_resume_fields(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    job_id = "job-stale-resume"
    snapshot = _snapshot(job_id, state="failed")
    snapshot["failed_stage"] = "translating"
    snapshot["error"] = {
        "code": "job_stage_failed",
        "message": "Job failed during translating.",
        "retryable": True,
    }
    snapshot["translation_resume"] = {
        "available": False,
        "reason": "not_retryable",
        "detail": "Job failed during created.",
    }
    snapshot["translation_activity"] = {"status": "unknown"}
    _write_translation_gates(jobs_dir, job_id, snapshot)

    service = BookJobService(jobs_dir=jobs_dir, project_home=tmp_path)
    enriched = service.get(job_id)

    assert enriched["translation_resume"]["available"] is True
    persisted = json.loads(
        (jobs_dir / job_id / "job.json").read_text(encoding="utf-8")
    )
    assert "translation_resume" not in persisted
    assert "translation_activity" not in persisted
