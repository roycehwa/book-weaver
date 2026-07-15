from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf_translator.jobs import BookJobRunner, JobRepository, resolve_text_operation
from pdf_translator.models import PipelineArtifacts
from pdf_translator.pipeline import build_artifacts


def test_existing_translation_resume_does_not_replay_intake_stages() -> None:
    from pdf_translator.pipeline import _pre_translation_stages

    assert _pre_translation_stages(using_existing_run=True) == ()
    assert _pre_translation_stages(using_existing_run=False) == (
        "ingesting",
        "reconstructing",
    )


@pytest.mark.parametrize(
    ("mode", "source_language", "target_language", "expected"),
    [
        ("preserve", "en", "zh-CN", "preserve"),
        ("translate", "zh-CN", "zh-CN", "translate"),
        ("auto", "English", "en-US", "preserve"),
        ("auto", "Chinese", "zh-CN", "preserve"),
        ("auto", "en", "zh-CN", "translate"),
        ("auto", None, "zh-CN", "translate"),
    ],
)
def test_resolve_text_operation(
    mode: str,
    source_language: str | None,
    target_language: str,
    expected: str,
) -> None:
    assert resolve_text_operation(mode, source_language, target_language) == expected


def test_resolve_text_operation_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError, match="processing mode"):
        resolve_text_operation("sometimes", "en", "zh-CN")


def test_translate_phase_can_resume_from_translating_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.epub"
    source.write_bytes(b"epub")
    repository = JobRepository(tmp_path / "jobs")
    created = repository.create(source_path=source, processing_mode="translate")
    repository.update(created["job_id"], state="translating")
    runner = BookJobRunner(repository)
    monkeypatch.setattr("pdf_translator.workflow.require_glossary_ready", lambda run_dir: None)

    def fake_full_pipeline(job_id: str, **kwargs):
        assert job_id == created["job_id"]
        assert kwargs["existing_run_dir"] == runner._run_output_dir(created["job_id"])
        assert kwargs["require_glossary_ready"] is True
        return repository.update(job_id, state="awaiting_human_review")

    monkeypatch.setattr(runner, "_run_full_pipeline", fake_full_pipeline)

    completed = runner.run_translate_phase(created["job_id"])

    assert completed["state"] == "awaiting_human_review"


def test_artifact_map_exposes_chapter_segments(tmp_path: Path) -> None:
    source = tmp_path / "source.epub"
    source.write_bytes(b"epub")
    repository = JobRepository(tmp_path / "jobs")
    created = repository.create(source_path=source, processing_mode="translate")
    job_dir = repository.job_dir(created["job_id"])
    run_dir = job_dir / "artifacts" / "run"
    run_dir.mkdir(parents=True)
    artifacts = build_artifacts(run_dir, source, "zh-CN")
    artifacts.manifest_path.write_text("{}", encoding="utf-8")
    artifacts.normalized_markdown_path.write_text("", encoding="utf-8")
    artifacts.normalized_json_path.write_text("{}", encoding="utf-8")
    artifacts.profile_json_path.write_text("{}", encoding="utf-8")
    artifacts.reconstructed_markdown_path.write_text("", encoding="utf-8")
    artifacts.translation_input_markdown_path.write_text("", encoding="utf-8")
    artifacts.translated_markdown_path.write_text("", encoding="utf-8")
    artifacts.book_json_path.write_text("{}", encoding="utf-8")
    (run_dir / "chapter-segments.json").write_text(
        json.dumps({"schema": "bookweaver_chapter_segments_v1", "segments": []}),
        encoding="utf-8",
    )

    mapped = BookJobRunner(repository)._artifact_map(created["job_id"], artifacts)

    assert mapped["chapter_segments"]["href"] == "artifacts/run/chapter-segments.json"


def test_repository_creates_durable_job_snapshot_and_initial_event(tmp_path: Path) -> None:
    source = tmp_path / "source.epub"
    source.write_bytes(b"example epub")
    repository = JobRepository(tmp_path / "jobs")

    snapshot = repository.create(
        source_path=source,
        processing_mode="auto",
        target_language="zh-CN",
        translator="mock",
        output_format="epub",
        ingest_timeout_seconds=900,
    )

    loaded = repository.load(snapshot["job_id"])
    assert loaded == snapshot
    assert loaded["schema"] == "book_job_v1"
    assert loaded["revision"] == 1
    assert loaded["state"] == "created"
    assert loaded["source"]["filename"] == "source.epub"
    assert loaded["source"]["size_bytes"] == len(b"example epub")
    assert len(loaded["source"]["sha256"]) == 64
    assert loaded["request"]["processing_mode"] == "auto"
    assert loaded["request"]["ingest_timeout_seconds"] == 900
    assert loaded["resolved"]["text_operation"] is None
    assert loaded["artifacts"] == {}
    assert loaded["error"] is None

    events = repository.list_events(snapshot["job_id"])
    assert events == [
        {
            "schema": "book_job_event_v1",
            "sequence": 1,
            "time": events[0]["time"],
            "job_id": snapshot["job_id"],
            "type": "job_created",
            "stage": "created",
            "data": {},
        }
    ]


def test_repository_updates_snapshot_atomically_and_increments_revision(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF")
    repository = JobRepository(tmp_path / "jobs")
    created = repository.create(source_path=source)

    updated = repository.update(
        created["job_id"],
        state="ingesting",
        progress={"stage_percent": 25, "overall_percent": 5},
    )

    assert updated["revision"] == 2
    assert updated["state"] == "ingesting"
    assert updated["progress"]["stage_percent"] == 25
    assert repository.load(created["job_id"]) == updated
    assert not list(repository.job_dir(created["job_id"]).glob("job.json.*.tmp"))


def test_repository_appends_events_with_monotonic_sequence(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF")
    repository = JobRepository(tmp_path / "jobs")
    created = repository.create(source_path=source)

    second = repository.append_event(
        created["job_id"],
        event_type="stage_started",
        stage="ingesting",
        data={"attempt": 1},
    )
    third = repository.append_event(
        created["job_id"],
        event_type="language_detected",
        stage="ingesting",
        data={"language": "en"},
    )

    assert second["sequence"] == 2
    assert third["sequence"] == 3
    assert [event["sequence"] for event in repository.list_events(created["job_id"])] == [1, 2, 3]


def test_repository_rejects_corrupt_event_history(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF")
    repository = JobRepository(tmp_path / "jobs")
    created = repository.create(source_path=source)
    events_path = repository.job_dir(created["job_id"]) / "events.jsonl"
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write("{not-json}\n")

    with pytest.raises(ValueError, match="event history"):
        repository.list_events(created["job_id"])


def test_snapshot_is_valid_json_after_each_update(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF")
    repository = JobRepository(tmp_path / "jobs")
    created = repository.create(source_path=source)

    for percent in range(10):
        repository.update(
            created["job_id"],
            progress={"stage_percent": percent, "overall_percent": percent},
        )
        json.loads(
            (repository.job_dir(created["job_id"]) / "job.json").read_text(encoding="utf-8")
        )


def test_job_runner_reaches_review_ready_and_maps_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "source.epub"
    source.write_bytes(b"epub")
    repository = JobRepository(tmp_path / "jobs")
    created = repository.create(
        source_path=source,
        processing_mode="preserve",
        source_language="en",
        output_format="epub",
        ingest_timeout_seconds=900,
    )

    def fake_pipeline(settings, on_stage):
        assert settings.processing_mode == "preserve"
        assert settings.ingest_timeout_seconds == 900
        assert settings.source_pdf == repository.job_dir(created["job_id"]) / "source" / source.name
        output_dir = settings.output_dir / source.stem
        output_dir.mkdir(parents=True)
        manifest = output_dir / "manifest.json"
        translated = output_dir / "translated.md"
        review_items = output_dir / "review_items.json"
        page_ledger = output_dir / "page-ledger.json"
        manifest.write_text("{}", encoding="utf-8")
        translated.write_text("translated", encoding="utf-8")
        review_items.write_text("{}", encoding="utf-8")
        page_ledger.write_text("{}", encoding="utf-8")
        on_stage("ingesting", {"stage_percent": 100, "source_language": "en"})
        on_stage("reconstructing", {"stage_percent": 100})
        on_stage("preserving", {"stage_percent": 100, "text_operation": "preserve"})
        on_stage("validating", {"stage_percent": 100})
        on_stage("pre_review", {"stage_percent": 100})
        return _pipeline_artifacts(output_dir, manifest, translated)

    completed = BookJobRunner(repository, pipeline_runner=fake_pipeline).run(created["job_id"])

    assert completed["state"] == "awaiting_human_review"
    assert completed["resolved"] == {
        "source_language": "en",
        "text_operation": "preserve",
    }
    assert completed["progress"]["overall_percent"] == 90
    assert completed["artifacts"]["manifest"]["href"] == "artifacts/source/manifest.json"
    assert completed["artifacts"]["translated_markdown"]["href"] == "artifacts/source/translated.md"
    assert completed["artifacts"]["review_items"]["href"] == "artifacts/source/review_items.json"
    assert completed["artifacts"]["page_ledger"]["href"] == "artifacts/source/page-ledger.json"
    assert [event["type"] for event in repository.list_events(created["job_id"])] == [
        "job_created",
        "stage_started",
        "stage_completed",
        "stage_started",
        "stage_completed",
        "stage_started",
        "stage_completed",
        "stage_started",
        "stage_completed",
        "stage_started",
        "stage_completed",
        "review_ready",
    ]


def test_job_runner_records_failure_and_resume_uses_existing_job(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF")
    repository = JobRepository(tmp_path / "jobs")
    created = repository.create(source_path=source, translator="mock")
    attempts = 0

    def flaky_pipeline(settings, on_stage):
        nonlocal attempts
        attempts += 1
        on_stage("ingesting", {"stage_percent": 100, "source_language": "en"})
        if attempts == 2:
            resumed_snapshot = repository.load(created["job_id"])
            assert resumed_snapshot["state"] == "ingesting"
            assert resumed_snapshot["error"] is None
            assert resumed_snapshot["failed_stage"] is None
        on_stage("translating", {"stage_percent": 20, "text_operation": "translate"})
        if attempts == 1:
            raise RuntimeError("provider token expired: secret-value")
        output_dir = settings.output_dir / source.stem
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = output_dir / "manifest.json"
        translated = output_dir / "translated.md"
        manifest.write_text("{}", encoding="utf-8")
        translated.write_text("translated", encoding="utf-8")
        on_stage("validating", {"stage_percent": 100})
        on_stage("pre_review", {"stage_percent": 100})
        return _pipeline_artifacts(output_dir, manifest, translated)

    runner = BookJobRunner(repository, pipeline_runner=flaky_pipeline)
    with pytest.raises(RuntimeError, match="provider token expired"):
        runner.run(created["job_id"])

    failed = repository.load(created["job_id"])
    assert failed["state"] == "failed"
    assert failed["failed_stage"] == "translating"
    assert failed["error"]["code"] == "job_stage_failed"
    assert failed["error"]["retryable"] is True
    assert "secret-value" not in failed["error"]["message"]

    resumed = runner.resume(created["job_id"])

    assert attempts == 2
    assert resumed["state"] == "awaiting_human_review"
    assert resumed["error"] is None
    assert resumed["failed_stage"] is None
    assert "job_resumed" in [
        event["type"] for event in repository.list_events(created["job_id"])
    ]


def test_job_runner_persists_canonical_polish_outcome(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF")
    repository = JobRepository(tmp_path / "jobs")
    created = repository.create(source_path=source, translator="mock")

    def fake_pipeline(settings, on_stage):
        on_stage("translating", {"stage_percent": 100, "text_operation": "translate"})
        on_stage("polishing", {"stage_percent": 0})
        on_stage("polishing", {"stage_percent": 100, "polish_outcome": "no_candidates"})
        on_stage("pre_review", {"stage_percent": 100})
        output_dir = settings.output_dir / source.stem
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = output_dir / "manifest.json"
        translated = output_dir / "translated.md"
        manifest.write_text("{}", encoding="utf-8")
        translated.write_text("translated", encoding="utf-8")
        return _pipeline_artifacts(output_dir, manifest, translated)

    completed = BookJobRunner(repository, pipeline_runner=fake_pipeline).run(created["job_id"])

    assert completed["progress"]["polish_outcome"] == "no_candidates"
    polish_events = [
        event
        for event in repository.list_events(created["job_id"])
        if event["stage"] == "polishing"
    ]
    assert polish_events[-1]["type"] == "stage_completed"
    assert polish_events[-1]["data"]["polish_outcome"] == "no_candidates"


def test_job_runner_records_polish_failure_as_failed_stage(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF")
    repository = JobRepository(tmp_path / "jobs")
    created = repository.create(source_path=source, translator="mock")

    def fake_pipeline(_settings, on_stage):
        on_stage("polishing", {"stage_percent": 0})
        raise RuntimeError("polish provider unavailable")

    runner = BookJobRunner(repository, pipeline_runner=fake_pipeline)
    with pytest.raises(RuntimeError, match="polish provider unavailable"):
        runner.run(created["job_id"])

    failed = repository.load(created["job_id"])
    assert failed["state"] == "failed"
    assert failed["failed_stage"] == "polishing"
    assert failed["progress"]["polish_outcome"] == "failed"


def test_job_runner_resumes_polish_failure_from_existing_run(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF")
    repository = JobRepository(tmp_path / "jobs")
    created = repository.create(source_path=source, translator="mock")
    repository.update(
        created["job_id"],
        state="failed",
        failed_stage="polishing",
        progress={"polish_outcome": "failed"},
    )
    runner = BookJobRunner(repository, pipeline_runner=lambda *_args: None)
    runner._uses_default_pipeline = True
    run_dir = tmp_path / "existing-run"
    monkeypatch.setattr(runner, "_run_output_dir", lambda _job_id: run_dir)
    calls: list[tuple[str, Path | None, bool]] = []

    def fake_full_pipeline(job_id, *, existing_run_dir=None, require_glossary_ready=False):
        calls.append((job_id, existing_run_dir, require_glossary_ready))
        return {"state": "awaiting_human_review"}

    monkeypatch.setattr(runner, "_run_full_pipeline", fake_full_pipeline)

    result = runner.resume(created["job_id"])

    assert result["state"] == "awaiting_human_review"
    assert calls == [(created["job_id"], run_dir, True)]


def test_job_runner_marks_invalid_source_non_retryable(tmp_path: Path) -> None:
    source = tmp_path / "source.epub"
    source.write_bytes(b"invalid epub")
    repository = JobRepository(tmp_path / "jobs")
    created = repository.create(source_path=source)

    def invalid_pipeline(settings, on_stage):
        on_stage("ingesting", {"stage_percent": 0})
        try:
            raise __import__("zipfile").BadZipFile("invalid archive")
        except __import__("zipfile").BadZipFile as cause:
            raise RuntimeError("Unable to inspect source") from cause

    with pytest.raises(RuntimeError, match="Unable to inspect source"):
        BookJobRunner(repository, pipeline_runner=invalid_pipeline).run(created["job_id"])

    failed = repository.load(created["job_id"])
    assert failed["error"]["code"] == "invalid_source"
    assert failed["error"]["retryable"] is False


def test_run_export_phase_completes_after_validating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF")
    repository = JobRepository(tmp_path / "jobs")
    created = repository.create(
        source_path=source,
        processing_mode="convert",
        output_format="epub",
    )
    job_id = created["job_id"]
    repository.update(job_id, state="awaiting_glossary")

    job_dir = repository.job_dir(job_id)
    run_dir = job_dir / "artifacts" / "source"
    run_dir.mkdir(parents=True)
    book_json = run_dir / "book.json"
    book_json.write_text(
        json.dumps(
            {
                "metadata": {},
                "pages": [],
                "chapters": [{"index": 1, "title": "Intro", "markdown": "Hello"}],
            }
        ),
        encoding="utf-8",
    )
    manifest = run_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_pdf": str(job_dir / "source" / source.name),
                "files": {"book_json": str(book_json)},
            }
        ),
        encoding="utf-8",
    )
    canonical = job_dir / "artifacts" / "canonical-chapters.json"
    canonical.write_text("{}", encoding="utf-8")

    runner = BookJobRunner(repository)
    monkeypatch.setattr(runner, "_run_output_dir", lambda _job_id: run_dir)

    def fake_export_pipeline(settings, on_stage):
        on_stage("exporting", {"stage_percent": 100})
        on_stage("validating", {"stage_percent": 100})
        return _pipeline_artifacts(run_dir, manifest, run_dir / "translated.md")

    monkeypatch.setattr("pdf_translator.pipeline.run_export_pipeline", fake_export_pipeline)

    completed = runner.run_export_phase(job_id)

    assert completed["state"] == "completed"
    assert completed["error"] is None
    assert "export_completed" in [event["type"] for event in repository.list_events(job_id)]


    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF")
    repository = JobRepository(tmp_path / "jobs")
    created = repository.create(source_path=source, translator="minimax")

    def missing_config_pipeline(settings, on_stage):
        on_stage("ingesting", {"stage_percent": 100, "source_language": "en"})
        on_stage("translating", {"stage_percent": 0, "text_operation": "translate"})
        raise ValueError("MINIMAX_API_KEY or LLM_API_KEY is required when translator='minimax'.")

    with pytest.raises(ValueError, match="MINIMAX_API_KEY"):
        BookJobRunner(repository, pipeline_runner=missing_config_pipeline).run(created["job_id"])

    failed = repository.load(created["job_id"])
    assert failed["state"] == "failed"
    assert failed["failed_stage"] == "translating"
    assert failed["error"]["code"] == "configuration_error"
    assert failed["error"]["retryable"] is True
    assert failed["error"]["details"] == {
        "stage": "translating",
        "reason": "MINIMAX_API_KEY or LLM_API_KEY is required when translator='minimax'.",
    }


def _pipeline_artifacts(
    output_dir: Path,
    manifest: Path,
    translated: Path,
) -> PipelineArtifacts:
    return PipelineArtifacts(
        output_dir=output_dir,
        normalized_markdown_path=output_dir / "normalized.md",
        normalized_json_path=output_dir / "normalized.json",
        profile_json_path=output_dir / "profile.json",
        reconstructed_markdown_path=output_dir / "reconstructed.md",
        translation_input_markdown_path=output_dir / "translation-input.md",
        translated_markdown_path=translated,
        translated_pdf_path=output_dir / "translated.pdf",
        translated_epub_path=output_dir / "translated.epub",
        manifest_path=manifest,
        book_json_path=output_dir / "book.json",
        book_markdown_path=output_dir / "book.md",
        book_trace_markdown_path=output_dir / "book-trace.md",
    )
