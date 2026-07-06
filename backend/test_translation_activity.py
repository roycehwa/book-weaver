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
