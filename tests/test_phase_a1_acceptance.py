"""Phase A1 acceptance tests — maps to workstation design § Phase A1 acceptance block."""

from __future__ import annotations

import json
from pathlib import Path

from pdf_translator import pipeline as pipeline_module
from pdf_translator.config import RunSettings
from pdf_translator.job_control import create_translation_job, load_progress, load_translation_events
from pdf_translator.jobs import BookJobRunner, JobRepository
from pdf_translator.models import NormalizedDocument, TranslationChunk
from pdf_translator.translate import BaseTranslator, MockTranslator, _chunk_cache_path, translate_markdown
from tests.test_pipeline import _Preflight
from tests.test_translate import FailingTranslator


def _patch_book_intake(monkeypatch, *, markdown: str, chapter_id: str = "ch-001", title: str = "Chapter") -> None:
    def fake_ingest(*args, **kwargs):
        source_pdf = args[0]
        return (
            NormalizedDocument(
                source_pdf=source_pdf,
                raw_markdown=markdown,
                reconstructed_markdown=markdown,
                structured={"pages": []},
                detected_language="en",
                images_dir=None,
            ),
            _Preflight(),
        )

    def fake_book(*args, **kwargs):
        return {
            "metadata": {"chapter_source": "test"},
            "render_policy": {"figures": "preserve"},
            "chapters": [
                {
                    "index": 1,
                    "chapter_id": chapter_id,
                    "title": title,
                    "markdown": markdown,
                    "source_pages": [1],
                    "page_start": 1,
                    "page_end": 1,
                    "toc": True,
                }
            ],
        }

    monkeypatch.setattr(pipeline_module, "ingest_pdf_guarded", fake_ingest)
    monkeypatch.setattr(pipeline_module, "build_document_profile", lambda *a, **k: {"profile": "book"})
    monkeypatch.setattr(pipeline_module, "build_book_reconstruction", fake_book)


def _english_settings(tmp_path: Path, **overrides) -> RunSettings:
    base = {
        "source_pdf": tmp_path / "english-book.epub",
        "output_dir": tmp_path / "runs",
        "target_language": "zh-CN",
        "source_language": "en",
        "translator": "mock",
        "max_chunk_chars": 9000,
        "profile_name": "book",
        "output_format": "none",
    }
    base.update(overrides)
    return RunSettings(**base)


def test_a1_run_directory_exposes_job_artifact_contract(tmp_path: Path, monkeypatch) -> None:
    _patch_book_intake(monkeypatch, markdown="# Chapter One\n\nBody paragraph for translation.\n")
    artifacts = pipeline_module.run_translation_pipeline(_english_settings(tmp_path))
    run_dir = artifacts.output_dir
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))

    for rel in ("jobs/translation-job.json", "jobs/progress.json", "jobs/translation-events.jsonl"):
        assert (run_dir / rel).is_file(), rel

    for key in ("translation_job", "translation_progress", "translation_events"):
        assert key in manifest["files"]
        assert Path(manifest["files"][key]).is_file()

    job = json.loads((run_dir / "jobs/translation-job.json").read_text(encoding="utf-8"))
    assert job["schema"] == "translation_job_v1"
    assert job["status"] == "completed"

    progress = json.loads((run_dir / "jobs/progress.json").read_text(encoding="utf-8"))
    assert progress["status"] == "completed"
    assert progress["completed_chunks"] == progress["total_chunks"] >= 1


def test_a1_resume_reuses_valid_cache_without_calling_translator_again(tmp_path: Path, monkeypatch) -> None:
    _patch_book_intake(monkeypatch, markdown="# Chapter One\n\nBody paragraph for translation.\n")
    build_calls: list[str] = []

    def build_translator(name: str):
        build_calls.append(name)
        return MockTranslator() if len(build_calls) == 1 else FailingTranslator()

    monkeypatch.setattr(pipeline_module, "build_translator", build_translator)

    settings = _english_settings(tmp_path)
    pipeline_module.run_translation_pipeline(settings)
    pipeline_module.run_translation_pipeline(
        _english_settings(tmp_path, resume_translation=True),
    )

    assert len(build_calls) == 2
    events = (tmp_path / "runs" / "english-book" / "jobs" / "translation-events.jsonl").read_text(encoding="utf-8")
    assert '"event": "cache_hit"' in events


def test_a1_ignore_cache_clears_stale_files_and_sets_manifest_flag(tmp_path: Path, monkeypatch) -> None:
    _patch_book_intake(monkeypatch, markdown="# Chapter One\n\nBody paragraph.\n")
    run_dir = tmp_path / "runs" / "english-book"
    stale = run_dir / "translation-cache" / "chunk-000000-stale.md"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("stale\n", encoding="utf-8")

    artifacts = pipeline_module.run_translation_pipeline(
        _english_settings(tmp_path, ignore_translation_cache=True),
    )
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))

    assert manifest["translation"]["ignore_cache"] is True
    assert not stale.exists()


def test_a1_bad_quality_cache_is_invalidated_not_reused(tmp_path: Path) -> None:
    class ValidChineseTranslator(BaseTranslator):
        name = "openai"

        def translate_chunk(self, chunk, source_language, target_language):
            return "# 标题\n\n" + ("这是用于通过质量检查的中文译文内容。" * 40) + "\n"

    long_english = " ".join(["English prose sentence with enough words for quality gates."] * 40)
    chunk = TranslationChunk(index=0, markdown=f"# Title\n\n{long_english}\n")
    cache_dir = tmp_path / "cache"
    settings = RunSettings(
        source_pdf=tmp_path / "book.epub",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="openai",
        max_chunk_chars=9000,
    )
    cache_path = _chunk_cache_path(cache_dir, chunk)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(f"# Title\n\n{long_english}\n", encoding="utf-8")

    observer = create_translation_job(
        run_dir=tmp_path / "run",
        translator="openai",
        source_language="en",
        target_language="zh-CN",
        total_chunks=1,
        concurrency=1,
        max_chunk_chars=9000,
        resume=False,
    )
    translate_markdown(
        chunks=[chunk],
        settings=settings,
        translator=ValidChineseTranslator(),
        cache_dir=cache_dir,
        observer=observer,
    )
    observer.finish(status="completed")

    events = load_translation_events(tmp_path / "run")
    assert any(event.get("event") == "cache_invalidated" for event in events)
    assert any(event.get("event") == "attempt_success" for event in events)
    assert "中文译文" in cache_path.read_text(encoding="utf-8")


def test_a1_translation_failure_is_inspectable_from_events_without_terminal_logs(tmp_path: Path) -> None:
    class EmptyThenOkTranslator(MockTranslator):
        name = "empty-then-ok"

        def __init__(self) -> None:
            self.calls = 0

        def translate_chunk(self, chunk, source_language, target_language):
            self.calls += 1
            if self.calls == 1:
                return ""
            return super().translate_chunk(chunk, source_language, target_language)

    observer = create_translation_job(
        run_dir=tmp_path,
        translator="empty-then-ok",
        source_language="en",
        target_language="zh-CN",
        total_chunks=1,
        concurrency=1,
        max_chunk_chars=9000,
        resume=False,
    )
    chunk = TranslationChunk(index=0, markdown="# Title\n\nBody.\n")
    settings = RunSettings(
        source_pdf=tmp_path / "book.epub",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="empty-then-ok",
        max_chunk_chars=9000,
    )
    translate_markdown(
        chunks=[chunk],
        settings=settings,
        translator=EmptyThenOkTranslator(),
        cache_dir=None,
        observer=observer,
        retry_count=2,
    )
    observer.finish(status="completed")

    events = load_translation_events(tmp_path)
    event_types = {event["event"] for event in events}
    assert "attempt_failure" in event_types
    assert "attempt_success" in event_types
    progress = load_progress(tmp_path)
    assert progress["completed_chunks"] == 1


def test_a1_book_job_runner_writes_chunk_fields_to_job_json(tmp_path: Path) -> None:
    source = tmp_path / "source.epub"
    source.write_bytes(b"epub")
    repository = JobRepository(tmp_path / "jobs")
    created = repository.create(source_path=source, processing_mode="translate")

    def fake_pipeline(settings, on_stage):
        assert settings.translation_progress_sink is not None
        on_stage("ingesting", {"stage_percent": 100, "source_language": "en"})
        on_stage("reconstructing", {"stage_percent": 100})
        on_stage("translating", {"stage_percent": 0, "text_operation": "translate"})
        settings.translation_progress_sink(
            {
                "total_chunks": 8,
                "completed_chunks": 3,
                "cache_hit_chunks": 1,
                "retrying_chunks": 0,
                "failed_chunks": 0,
            }
        )
        output_dir = settings.output_dir / source.stem
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = output_dir / "manifest.json"
        translated = output_dir / "translated.md"
        manifest.write_text("{}", encoding="utf-8")
        translated.write_text("ok", encoding="utf-8")
        on_stage("validating", {"stage_percent": 100})
        on_stage("pre_review", {"stage_percent": 100})
        from tests.test_jobs import _pipeline_artifacts

        return _pipeline_artifacts(output_dir, manifest, translated)

    BookJobRunner(repository, pipeline_runner=fake_pipeline).run(created["job_id"])
    snapshot = repository.load(created["job_id"])
    assert snapshot["progress"]["translation_chunks_total"] == 8
    assert snapshot["progress"]["translation_chunks_completed"] == 3
    assert snapshot["progress"]["translation_cache_hits"] == 1
