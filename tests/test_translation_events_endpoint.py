from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class FakeJobService:
    def __init__(self, job_dir: Path) -> None:
        self._dir = job_dir

    def _job_dir(self, job_id: str) -> Path:
        assert job_id == "job-1"
        return self._dir


def test_translation_events_endpoint_reads_artifact_run_events(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import main

    job_dir = tmp_path / "job-1"
    run_jobs_dir = job_dir / "artifacts" / "book-run" / "jobs"
    run_jobs_dir.mkdir(parents=True)
    (run_jobs_dir / "translation-events.jsonl").write_text(
        json.dumps({"event": "attempt_success", "chunk_index": 7}) + "\n",
        encoding="utf-8",
    )
    (run_jobs_dir / "progress.json").write_text(
        json.dumps({"status": "completed"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_job_service", lambda: FakeJobService(job_dir))

    response = asyncio.run(main.get_translation_events("job-1"))

    assert response["events"] == [{"event": "attempt_success", "chunk_index": 7}]
    assert response["completed"] is True
    assert response["size"] > 0


def test_translation_events_endpoint_limit_returns_consumed_offset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import main

    job_dir = tmp_path / "job-1"
    run_jobs_dir = job_dir / "artifacts" / "book-run" / "jobs"
    run_jobs_dir.mkdir(parents=True)
    first = json.dumps({"event": "attempt_start", "chunk_index": 1}) + "\n"
    second = json.dumps({"event": "attempt_success", "chunk_index": 1}) + "\n"
    events_path = run_jobs_dir / "translation-events.jsonl"
    events_path.write_text(first + second, encoding="utf-8")
    (run_jobs_dir / "progress.json").write_text(
        json.dumps({"status": "running"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "get_job_service", lambda: FakeJobService(job_dir))

    first_response = asyncio.run(main.get_translation_events("job-1", limit=1))
    second_response = asyncio.run(
        main.get_translation_events("job-1", since_offset=first_response["offset"], limit=1)
    )

    assert first_response["events"] == [{"event": "attempt_start", "chunk_index": 1}]
    assert first_response["offset"] == len(first.encode("utf-8"))
    assert first_response["offset"] < first_response["size"]
    assert second_response["events"] == [{"event": "attempt_success", "chunk_index": 1}]
