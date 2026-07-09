from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from job_service import BookJobService, JobServiceError
from translation_supervisor import _scan_and_resume_stalled


def _snapshot(job_id: str, *, state: str = "translating") -> dict:
    return {
        "schema": "book_job_v1",
        "job_id": job_id,
        "state": state,
        "request": {"processing_mode": "translate"},
        "resolved": {"text_operation": "translate"},
        "artifacts": {},
    }


def test_translation_activity_marks_recent_progress_as_active(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    job_id = "job-active"
    run_dir = jobs_dir / job_id / "artifacts" / "book-run" / "jobs"
    run_dir.mkdir(parents=True)
    (jobs_dir / job_id / "job.json").write_text(
        json.dumps(_snapshot(job_id), ensure_ascii=False),
        encoding="utf-8",
    )
    updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    (run_dir / "progress.json").write_text(
        json.dumps(
            {
                "status": "running",
                "updated_at": updated_at,
                "completed_chunks": 10,
                "total_chunks": 20,
                "running_chunks": 2,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    service = BookJobService(jobs_dir=jobs_dir, project_home=tmp_path)
    activity = service.translation_activity(_snapshot(job_id))

    assert activity is not None
    assert activity["status"] == "active"
    assert activity["completed_chunks"] == 10


def test_translation_activity_marks_old_progress_as_stalled(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    job_id = "job-stalled"
    run_dir = jobs_dir / job_id / "artifacts" / "book-run" / "jobs"
    run_dir.mkdir(parents=True)
    (jobs_dir / job_id / "job.json").write_text(
        json.dumps(_snapshot(job_id), ensure_ascii=False),
        encoding="utf-8",
    )
    updated_at = (datetime.now(timezone.utc) - timedelta(minutes=12)).isoformat().replace("+00:00", "Z")
    (run_dir / "progress.json").write_text(
        json.dumps(
            {
                "status": "running",
                "updated_at": updated_at,
                "completed_chunks": 92,
                "total_chunks": 108,
                "running_chunks": 5,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    service = BookJobService(jobs_dir=jobs_dir, project_home=tmp_path)
    activity = service.translation_activity(_snapshot(job_id))

    assert activity is not None
    assert activity["status"] == "stalled"
    assert activity["seconds_since_update"] >= 600


def test_get_reconciles_stale_translating_job_with_old_running_chunk(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    job_id = "job-stale"
    job_dir = jobs_dir / job_id
    run_dir = job_dir / "artifacts" / "book-run" / "jobs"
    run_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(
        json.dumps(_snapshot(job_id), ensure_ascii=False),
        encoding="utf-8",
    )
    updated_at = (datetime.now(timezone.utc) - timedelta(minutes=12)).isoformat().replace("+00:00", "Z")
    (run_dir / "progress.json").write_text(
        json.dumps(
            {
                "status": "running",
                "updated_at": updated_at,
                "completed_chunks": 86,
                "total_chunks": 231,
                "running_chunks": 1,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    service = BookJobService(jobs_dir=jobs_dir, project_home=tmp_path)
    snapshot = service.get(job_id)

    assert snapshot["state"] == "failed"
    assert snapshot["failed_stage"] == "translating"
    assert snapshot["error"]["retryable"] is True


def test_translation_resume_is_blocked_until_chapters_are_user_confirmed(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    job_id = "job-auto-chapters"
    job_dir = jobs_dir / job_id
    artifacts_dir = job_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "canonical-chapters.json").write_text(
        json.dumps(
            {
                "schema": "bookmate_canonical_chapters_v1",
                "job_id": job_id,
                "source_artifact": "book",
                "chapters": [{"index": 1, "chapter_id": "ch-1", "title": "Auto"}],
            }
        ),
        encoding="utf-8",
    )
    snapshot = _snapshot(job_id, state="failed")
    snapshot["failed_stage"] = "translating"
    snapshot["artifacts"] = {"canonical_chapters": {"href": "artifacts/canonical-chapters.json"}}
    snapshot["error"] = {
        "code": "job_stage_failed",
        "message": "翻译失败",
        "retryable": True,
    }
    (job_dir / "job.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    service = BookJobService(jobs_dir=jobs_dir, project_home=tmp_path)
    resume = service.translation_resume(service.get(job_id))

    assert resume is not None
    assert resume["available"] is False
    assert resume["reason"] == "human_gate_required"
    assert "章节" in resume["detail"]


def test_supervisor_marks_human_gate_resume_failure_instead_of_retrying() -> None:
    marked: list[tuple[str, str]] = []

    class Service:
        def list(self):
            return [{"job_id": "job-needs-human", "state": "translating"}]

        def get(self, job_id: str):
            assert job_id == "job-needs-human"
            return {
                "job_id": job_id,
                "state": "translating",
                "translation_resume": {"available": True},
            }

        def translation_worker_lock_held(self, job_id: str) -> bool:
            assert job_id == "job-needs-human"
            return False

        def resume(self, job_id: str) -> None:
            assert job_id == "job-needs-human"
            raise JobServiceError("请先人工确认章节目录，再开始全文翻译。")

        def mark_translation_resume_blocked(self, job_id: str, message: str) -> None:
            marked.append((job_id, message))

    _scan_and_resume_stalled(Service())

    assert marked == [("job-needs-human", "请先人工确认章节目录，再开始全文翻译。")]


def test_supervisor_auto_resumes_failed_translation_until_limit() -> None:
    calls: list[str] = []
    attempts = {"count": 0}
    exhausted: list[tuple[str, int]] = []

    class Service:
        def list(self):
            return [{"job_id": "job-failed", "state": "failed", "failed_stage": "translating"}]

        def get(self, job_id: str):
            assert job_id == "job-failed"
            return {
                "job_id": job_id,
                "state": "failed",
                "failed_stage": "translating",
                "translation_resume": {"available": True},
            }

        def translation_worker_lock_held(self, job_id: str) -> bool:
            assert job_id == "job-failed"
            return False

        def auto_resume_attempts(self, job_id: str) -> int:
            assert job_id == "job-failed"
            return attempts["count"]

        def record_auto_resume_attempt(self, job_id: str) -> None:
            assert job_id == "job-failed"
            attempts["count"] += 1

        def mark_translation_auto_resume_exhausted(self, job_id: str, max_attempts: int) -> None:
            exhausted.append((job_id, max_attempts))

        def resume(self, job_id: str) -> None:
            calls.append(job_id)

    _scan_and_resume_stalled(Service())

    assert calls == ["job-failed"]
    assert attempts["count"] == 1
    assert exhausted == []


def test_supervisor_marks_auto_resume_exhausted_at_limit(monkeypatch) -> None:
    marked: list[tuple[str, int]] = []

    class Service:
        def list(self):
            return [{"job_id": "job-failed", "state": "failed", "failed_stage": "translating"}]

        def get(self, job_id: str):
            return {
                "job_id": job_id,
                "state": "failed",
                "failed_stage": "translating",
                "translation_resume": {"available": True},
            }

        def translation_worker_lock_held(self, job_id: str) -> bool:
            return False

        def auto_resume_attempts(self, job_id: str) -> int:
            return 3

        def record_auto_resume_attempt(self, job_id: str) -> None:
            raise AssertionError("should not record past the limit")

        def mark_translation_auto_resume_exhausted(self, job_id: str, max_attempts: int) -> None:
            marked.append((job_id, max_attempts))

        def resume(self, job_id: str) -> None:
            raise AssertionError("should not resume past the limit")

    monkeypatch.setenv("BOOKMATE_AUTO_RESUME_MAX_ATTEMPTS", "3")

    _scan_and_resume_stalled(Service())

    assert marked == [("job-failed", 3)]
