from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_service import BookJobService, JobNotFound, JobServiceError


def test_default_project_home_uses_unified_repo_root(tmp_path: Path, monkeypatch) -> None:
    import job_service
    from engine_home import _REPO_ROOT

    monkeypatch.setattr(
        job_service,
        "get_settings",
        lambda: SimpleNamespace(
            BOOK_WEAVER_HOME="",
            PDF_TRANSLATOR_HOME="",
            BOOKMATE_JOBS_DIR=str(tmp_path / "jobs"),
        ),
    )

    service = BookJobService()

    assert service.project_home == _REPO_ROOT.resolve()


def test_glossary_suggest_status_is_read_without_importing_phase_a(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "glossary").mkdir(parents=True)

    assert BookJobService._glossary_suggest_status(run_dir) == {"status": "idle"}


def test_start_translation_requires_confirmed_canonical_chapters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = BookJobService(project_home=tmp_path, jobs_dir=tmp_path / "jobs")
    monkeypatch.setattr(
        service,
        "get",
        lambda job_id: {
            "job_id": job_id,
            "state": "awaiting_glossary",
            "artifacts": {"glossary_candidates": {"href": "artifacts/glossary/candidates.json"}},
        },
    )

    with pytest.raises(JobServiceError, match="章节"):
        service.start_translation("job-1")


def _snapshot(job_id: str, updated_at: str = "2026-06-12T10:00:00Z") -> dict:
    return {
        "schema": "book_job_v1",
        "job_id": job_id,
        "updated_at": updated_at,
        "state": "created",
        "artifacts": {},
    }


def test_create_invokes_stable_cli_and_parses_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "pdf-translator"
    project.mkdir()
    (project / "pyproject.toml").touch()
    source = tmp_path / "book.epub"
    source.write_bytes(b"epub")
    service = BookJobService(project_home=project, jobs_dir=tmp_path / "jobs")
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        return SimpleNamespace(
            returncode=0,
            stdout="Job ID: job-1\n" + json.dumps(_snapshot("job-1")),
            stderr="",
        )

    monkeypatch.setattr("job_service.subprocess.run", fake_run)

    result = service.create(
        source_path=source,
        processing_mode="preserve",
        source_language="en",
        target_language="zh-CN",
        translator="mock",
        output_format="epub",
        ingest_timeout_seconds=900,
    )

    assert result["job_id"] == "job-1"
    # 启动 launchd 环境下没有 uv 在 PATH 中，_run 会解析到 ~/.local/bin/uv 等绝对路径
    cmd = observed["command"]
    # 必须由 uv 触发（路径或 PATH 解析）
    runner = cmd[0]
    assert Path(runner).name == "uv", f"unexpected runner: {runner}"
    assert cmd[1] == "run"
    assert cmd[2] == "pdf-translator"
    assert "job" in cmd
    assert "create" in cmd
    assert "create" in observed["command"]
    assert "--source-lang" in observed["command"]
    assert "--ingest-timeout-seconds" in observed["command"]
    assert "900" in observed["command"]


def test_list_sorts_snapshots_and_events_are_validated(tmp_path: Path) -> None:
    service = BookJobService(project_home=tmp_path, jobs_dir=tmp_path / "jobs")
    for job_id, updated_at in [
        ("older", "2026-06-12T09:00:00Z"),
        ("newer", "2026-06-12T11:00:00Z"),
    ]:
        job_dir = service.jobs_dir / job_id
        job_dir.mkdir()
        (job_dir / "job.json").write_text(
            json.dumps(_snapshot(job_id, updated_at)),
            encoding="utf-8",
        )
        (job_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "schema": "book_job_event_v1",
                    "sequence": 1,
                    "job_id": job_id,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    assert [item["job_id"] for item in service.list()] == ["newer", "older"]
    assert service.events("newer")[0]["sequence"] == 1


def test_artifact_path_rejects_traversal(tmp_path: Path) -> None:
    service = BookJobService(project_home=tmp_path, jobs_dir=tmp_path / "jobs")
    job_dir = service.jobs_dir / "job-1"
    job_dir.mkdir()
    snapshot = _snapshot("job-1")
    snapshot["artifacts"] = {"bad": {"href": "../../secret.txt"}}
    (job_dir / "job.json").write_text(json.dumps(snapshot), encoding="utf-8")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

    with pytest.raises(JobNotFound):
        service.artifact_path("job-1", "bad")


def test_source_path_returns_stored_source_file(tmp_path: Path) -> None:
    service = BookJobService(project_home=tmp_path, jobs_dir=tmp_path / "jobs")
    job_dir = service.jobs_dir / "job-1"
    source_dir = job_dir / "source"
    source_dir.mkdir(parents=True)
    source = source_dir / "book.pdf"
    source.write_bytes(b"%PDF")
    snapshot = _snapshot("job-1")
    snapshot["source"] = {"filename": "book.pdf"}
    (job_dir / "job.json").write_text(json.dumps(snapshot), encoding="utf-8")

    assert service.source_path("job-1") == source.resolve()


def test_source_path_rejects_traversal(tmp_path: Path) -> None:
    service = BookJobService(project_home=tmp_path, jobs_dir=tmp_path / "jobs")
    job_dir = service.jobs_dir / "job-1"
    job_dir.mkdir(parents=True)
    snapshot = _snapshot("job-1")
    snapshot["source"] = {"filename": "../secret.pdf"}
    (job_dir / "job.json").write_text(json.dumps(snapshot), encoding="utf-8")
    (job_dir / "secret.pdf").write_bytes(b"%PDF")

    with pytest.raises(JobNotFound):
        service.source_path("job-1")


def test_review_run_dir_requires_complete_review_artifacts(tmp_path: Path) -> None:
    service = BookJobService(project_home=tmp_path, jobs_dir=tmp_path / "jobs")
    job_dir = service.jobs_dir / "job-1"
    run_dir = job_dir / "artifacts" / "book"
    run_dir.mkdir(parents=True)
    for name in [
        "segments.json",
        "translated_segments.json",
        "review_items.json",
        "review_state.json",
    ]:
        (run_dir / name).write_text("{}", encoding="utf-8")
    snapshot = _snapshot("job-1")
    snapshot["state"] = "awaiting_human_review"
    snapshot["artifacts"] = {
        "review_items": {"href": "artifacts/book/review_items.json"}
    }
    (job_dir / "job.json").write_text(json.dumps(snapshot), encoding="utf-8")

    assert service.review_run_dir("job-1") == run_dir


def test_review_run_dir_aliases_trailing_whitespace_directory(tmp_path: Path) -> None:
    service = BookJobService(project_home=tmp_path, jobs_dir=tmp_path / "jobs")
    job_dir = service.jobs_dir / "job-1"
    run_dir = job_dir / "artifacts" / "book "
    run_dir.mkdir(parents=True)
    for name in [
        "segments.json",
        "translated_segments.json",
        "review_items.json",
        "review_state.json",
    ]:
        (run_dir / name).write_text("{}", encoding="utf-8")
    snapshot = _snapshot("job-1")
    snapshot["state"] = "awaiting_human_review"
    snapshot["artifacts"] = {
        "review_items": {"href": "artifacts/book /review_items.json"}
    }
    (job_dir / "job.json").write_text(json.dumps(snapshot), encoding="utf-8")

    alias = service.review_run_dir("job-1")

    assert alias == job_dir / "review"
    assert alias.is_symlink()
    assert alias.resolve() == run_dir.resolve()


def test_review_run_dir_allows_translation_jobs_without_confirmed_chapters(tmp_path: Path) -> None:
    service = BookJobService(project_home=tmp_path, jobs_dir=tmp_path / "jobs")
    job_dir = service.jobs_dir / "job-1"
    run_dir = job_dir / "artifacts" / "book"
    run_dir.mkdir(parents=True)
    for name in [
        "segments.json",
        "translated_segments.json",
        "review_items.json",
        "review_state.json",
    ]:
        (run_dir / name).write_text("{}", encoding="utf-8")
    snapshot = _snapshot("job-1")
    snapshot["state"] = "awaiting_human_review"
    snapshot["resolved"] = {"text_operation": "translate"}
    snapshot["artifacts"] = {
        "review_items": {"href": "artifacts/book/review_items.json"}
    }
    (job_dir / "job.json").write_text(json.dumps(snapshot), encoding="utf-8")

    assert service.review_run_dir("job-1").resolve() == run_dir.resolve()


def test_confirm_chapters_writes_canonical_artifact_and_updates_snapshot(tmp_path: Path) -> None:
    service = BookJobService(project_home=tmp_path, jobs_dir=tmp_path / "jobs")
    job_dir = service.jobs_dir / "job-1"
    artifacts_dir = job_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)
    book = {
        "metadata": {"title": "Example"},
        "chapters": [
            {
                "index": 1,
                "chapter_id": "ch-1",
                "title": "Intro",
                "page_start": 1,
                "page_end": 3,
                "source_pages": [1, 2, 3],
            }
        ],
    }
    (artifacts_dir / "book.json").write_text(json.dumps(book), encoding="utf-8")
    snapshot = _snapshot("job-1")
    snapshot["state"] = "awaiting_human_review"
    snapshot["artifacts"] = {"book": {"href": "artifacts/book.json"}}
    (job_dir / "job.json").write_text(json.dumps(snapshot), encoding="utf-8")

    result = service.confirm_chapters("job-1")

    canonical_path = artifacts_dir / "canonical-chapters.json"
    assert canonical_path.is_file()
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    assert canonical["schema"] == "bookmate_canonical_chapters_v1"
    assert canonical["source_artifact"] == "book"
    assert canonical["chapters"][0]["chapter_id"] == "ch-1"
    assert result["artifacts"]["canonical_chapters"]["href"] == "artifacts/canonical-chapters.json"
    assert service.get("job-1")["artifacts"]["canonical_chapters"]["href"] == "artifacts/canonical-chapters.json"


def test_confirm_chapters_accepts_user_edited_chapters(tmp_path: Path) -> None:
    service = BookJobService(project_home=tmp_path, jobs_dir=tmp_path / "jobs")
    job_dir = service.jobs_dir / "job-1"
    artifacts_dir = job_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "book.json").write_text(
        json.dumps({"chapters": [{"index": 1, "chapter_id": "old", "title": "Old"}]}),
        encoding="utf-8",
    )
    snapshot = _snapshot("job-1")
    snapshot["state"] = "awaiting_human_review"
    snapshot["artifacts"] = {"book": {"href": "artifacts/book.json"}}
    (job_dir / "job.json").write_text(json.dumps(snapshot), encoding="utf-8")

    service.confirm_chapters(
        "job-1",
        chapters=[
            {
                "index": 1,
                "chapter_id": "manual-1",
                "title": "用户修正章节",
                "page_start": 10,
                "page_end": 20,
                "source_pages": [10, 11],
            }
        ],
    )

    canonical = json.loads((artifacts_dir / "canonical-chapters.json").read_text(encoding="utf-8"))
    assert canonical["source_artifact"] == "user_confirmation"
    assert canonical["chapters"][0]["chapter_id"] == "manual-1"
    assert canonical["chapters"][0]["title"] == "用户修正章节"


def test_draft_chapters_prefers_saved_canonical_chapters(tmp_path: Path) -> None:
    service = BookJobService(project_home=tmp_path, jobs_dir=tmp_path / "jobs")
    job_dir = service.jobs_dir / "job-1"
    artifacts_dir = job_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "book.json").write_text(
        json.dumps({"chapters": [{"index": 1, "chapter_id": "old", "title": "Old"}]}),
        encoding="utf-8",
    )
    (artifacts_dir / "canonical-chapters.json").write_text(
        json.dumps(
            {
                "schema": "bookmate_canonical_chapters_v1",
                "job_id": "job-1",
                "source_artifact": "user_confirmation",
                "created_at": "2026-06-20T00:00:00Z",
                "chapters": [{"index": 1, "chapter_id": "manual-1", "title": "Saved"}],
            }
        ),
        encoding="utf-8",
    )
    snapshot = _snapshot("job-1")
    snapshot["artifacts"] = {
        "book": {"href": "artifacts/book.json"},
        "canonical_chapters": {"href": "artifacts/canonical-chapters.json"},
    }
    (job_dir / "job.json").write_text(json.dumps(snapshot), encoding="utf-8")

    chapters = service.draft_chapters("job-1")

    assert chapters[0]["chapter_id"] == "manual-1"
    assert chapters[0]["title"] == "Saved"


def test_delete_removes_job_directory(tmp_path: Path) -> None:
    service = BookJobService(project_home=tmp_path, jobs_dir=tmp_path / "jobs")
    job_dir = service.jobs_dir / "job-1"
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps(_snapshot("job-1")), encoding="utf-8")

    service.delete("job-1")

    assert not job_dir.exists()
