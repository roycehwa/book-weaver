from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from pdf_translator.agent_runner import run_agent_once
from pdf_translator.guardrails import IngestExecutionError
from pdf_translator.pipeline import build_artifacts


def test_agent_once_moves_successful_book_to_ok(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "文档"
    source_dir = source_root / "EN"
    source_dir.mkdir(parents=True)
    source = source_dir / "Sample Book.pdf"
    source.write_bytes(b"%PDF")
    calls: list[str] = []

    def fake_pipeline(settings):
        calls.append(settings.source_language)
        output_dir = settings.output_dir / settings.source_pdf.stem
        output_dir.mkdir(parents=True)
        artifacts = build_artifacts(output_dir, settings.source_pdf, settings.target_language)
        artifacts.manifest_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
        artifacts.translated_markdown_path.write_text("translated", encoding="utf-8")
        artifacts.translated_epub_path.write_bytes(b"epub")
        return artifacts

    @dataclass(slots=True)
    class FakePolishResult:
        report_path: Path

    def fake_polish(*, run_dir: Path, **kwargs):
        report_path = run_dir / "polish-report.json"
        report_path.write_text(json.dumps({"polished": True}), encoding="utf-8")
        return FakePolishResult(report_path=report_path)

    monkeypatch.setattr("pdf_translator.agent_runner.run_translation_pipeline", fake_pipeline)
    monkeypatch.setattr("pdf_translator.agent_runner.run_polish", fake_polish)

    result = run_agent_once(source_root=source_root, translator="mock")

    assert result.status == "ok"
    assert calls == ["en"]
    assert not source.exists()
    assert result.destination_dir is not None
    assert result.destination_dir.parent == source_root / "OK"
    assert (result.destination_dir / "Sample Book.pdf").exists()
    assert (result.destination_dir / "manifest.json").exists()
    status = json.loads((result.destination_dir / "phase-a-status.json").read_text(encoding="utf-8"))
    assert status["status"] == "ok"
    assert status["source_lane"] == "EN"
    assert status["polish_report"].endswith("polish-report.json")


def test_agent_once_moves_failed_book_to_ng(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "文档"
    source_dir = source_root / "CN"
    source_dir.mkdir(parents=True)
    source = source_dir / "坏书.pdf"
    source.write_bytes(b"%PDF")

    def fake_pipeline(settings):
        run_dir = settings.output_dir / settings.source_pdf.stem
        run_dir.mkdir(parents=True)
        (run_dir / "partial.txt").write_text("partial", encoding="utf-8")
        raise RuntimeError("boom")

    monkeypatch.setattr("pdf_translator.agent_runner.run_translation_pipeline", fake_pipeline)

    result = run_agent_once(source_root=source_root, translator="mock", polish_english=False)

    assert result.status == "ng"
    assert not source.exists()
    assert result.destination_dir is not None
    assert result.destination_dir.parent == source_root / "NG"
    assert (result.destination_dir / "坏书.pdf").exists()
    assert (result.destination_dir / "坏书" / "partial.txt").exists()
    status = json.loads((result.destination_dir / "phase-a-status.json").read_text(encoding="utf-8"))
    assert status["status"] == "ng"
    assert status["source_lane"] == "CN"
    assert status["error_type"] == "RuntimeError"


def test_agent_once_can_restrict_source_lane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "文档"
    en_dir = source_root / "EN"
    cn_dir = source_root / "CN"
    en_dir.mkdir(parents=True)
    cn_dir.mkdir(parents=True)
    en_source = en_dir / "Old English.pdf"
    cn_source = cn_dir / "中文.pdf"
    en_source.write_bytes(b"%PDF")
    cn_source.write_bytes(b"%PDF")
    en_mtime = 100
    cn_mtime = 200
    __import__("os").utime(en_source, (en_mtime, en_mtime))
    __import__("os").utime(cn_source, (cn_mtime, cn_mtime))

    def fake_pipeline(settings):
        output_dir = settings.output_dir / settings.source_pdf.stem
        output_dir.mkdir(parents=True)
        artifacts = build_artifacts(output_dir, settings.source_pdf, settings.target_language)
        artifacts.manifest_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
        return artifacts

    monkeypatch.setattr("pdf_translator.agent_runner.run_translation_pipeline", fake_pipeline)

    result = run_agent_once(source_root=source_root, translator="mock", source_lanes=("CN",))

    assert result.status == "ok"
    assert en_source.exists()
    assert not cn_source.exists()
    assert result.destination_dir is not None
    assert (result.destination_dir / "中文.pdf").exists()


def test_agent_once_resumes_failed_ng_before_new_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "文档"
    failed_dir = source_root / "NG" / "Failed Book"
    failed_dir.mkdir(parents=True)
    failed_source = failed_dir / "Failed Book.pdf"
    failed_source.write_bytes(b"%PDF")
    (failed_dir / "phase-a-status.json").write_text(
        json.dumps({"status": "ng", "source_lane": "EN"}),
        encoding="utf-8",
    )
    old_run_dir = failed_dir / "Failed Book"
    old_cache = old_run_dir / "translation-cache"
    old_cache.mkdir(parents=True)
    (old_cache / "chunk-000001-old.md").write_text("cached", encoding="utf-8")

    new_dir = source_root / "EN"
    new_dir.mkdir(parents=True)
    new_source = new_dir / "New Book.pdf"
    new_source.write_bytes(b"%PDF")
    seen_output_dirs: list[Path] = []

    def fake_pipeline(settings):
        seen_output_dirs.append(settings.output_dir)
        output_dir = settings.output_dir / settings.source_pdf.stem
        output_dir.mkdir(parents=True, exist_ok=True)
        assert (output_dir / "translation-cache" / "chunk-000001-old.md").exists()
        artifacts = build_artifacts(output_dir, settings.source_pdf, settings.target_language)
        artifacts.manifest_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
        return artifacts

    monkeypatch.setattr("pdf_translator.agent_runner.run_translation_pipeline", fake_pipeline)

    result = run_agent_once(source_root=source_root, translator="mock", polish_english=False)

    assert result.status == "ok"
    assert seen_output_dirs == [failed_dir]
    assert new_source.exists()
    assert result.destination_dir is not None
    assert result.destination_dir.parent == source_root / "OK"
    assert (result.destination_dir / "Failed Book.pdf").exists()
    assert not failed_dir.exists()
    status = json.loads((result.destination_dir / "phase-a-status.json").read_text(encoding="utf-8"))
    assert status["resume_from_ng"] is True


def test_agent_once_skips_exhausted_ng_retries_for_new_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "文档"
    failed_dir = source_root / "NG" / "Failed Book"
    failed_dir.mkdir(parents=True)
    failed_source = failed_dir / "Failed Book.pdf"
    failed_source.write_bytes(b"%PDF")
    (failed_dir / "phase-a-status.json").write_text(
        json.dumps({"status": "ng", "source_lane": "EN", "attempt_count": 2}),
        encoding="utf-8",
    )

    new_dir = source_root / "EN"
    new_dir.mkdir(parents=True)
    new_source = new_dir / "New Book.pdf"
    new_source.write_bytes(b"%PDF")
    processed_sources: list[Path] = []

    def fake_pipeline(settings):
        processed_sources.append(settings.source_pdf)
        output_dir = settings.output_dir / settings.source_pdf.stem
        output_dir.mkdir(parents=True, exist_ok=True)
        artifacts = build_artifacts(output_dir, settings.source_pdf, settings.target_language)
        artifacts.manifest_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
        return artifacts

    monkeypatch.setattr("pdf_translator.agent_runner.run_translation_pipeline", fake_pipeline)

    result = run_agent_once(source_root=source_root, translator="mock", polish_english=False)

    assert result.status == "ok"
    assert processed_sources == [new_source]
    assert failed_source.exists()
    assert result.destination_dir is not None
    assert (result.destination_dir / "New Book.pdf").exists()


def test_agent_once_marks_ingest_failures_non_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "文档"
    source_dir = source_root / "EN"
    source_dir.mkdir(parents=True)
    source = source_dir / "Broken.epub"
    source.write_bytes(b"Too many downloads at the same time")

    def fake_pipeline(settings):
        raise IngestExecutionError("Unable to inspect page count for Broken.epub: File is not a zip file")

    monkeypatch.setattr("pdf_translator.agent_runner.run_translation_pipeline", fake_pipeline)

    result = run_agent_once(source_root=source_root, translator="mock", polish_english=False, max_ng_retries=2)

    assert result.status == "ng"
    assert result.destination_dir is not None
    status = json.loads((result.destination_dir / "phase-a-status.json").read_text(encoding="utf-8"))
    assert status["attempt_count"] == 2
    assert status["retry_exhausted"] is True
    assert status["non_retryable"] is True


def test_agent_once_reuses_stable_working_dir_after_timeout_like_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "文档"
    source_dir = source_root / "EN"
    source_dir.mkdir(parents=True)
    source = source_dir / "Timed Out.pdf"
    source.write_bytes(b"%PDF")
    stale_cache = source_root / ".hermes-working" / "Timed Out" / "translation-cache"
    stale_cache.mkdir(parents=True)
    (stale_cache / "chunk-000001-old.md").write_text("cached", encoding="utf-8")

    def fake_pipeline(settings):
        assert settings.output_dir == source_root / ".hermes-working"
        output_dir = settings.output_dir / settings.source_pdf.stem
        assert (output_dir / "translation-cache" / "chunk-000001-old.md").exists()
        output_dir.mkdir(parents=True, exist_ok=True)
        artifacts = build_artifacts(output_dir, settings.source_pdf, settings.target_language)
        artifacts.manifest_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
        return artifacts

    monkeypatch.setattr("pdf_translator.agent_runner.run_translation_pipeline", fake_pipeline)

    result = run_agent_once(source_root=source_root, translator="mock", polish_english=False)

    assert result.status == "ok"
    assert result.destination_dir is not None
    assert (result.destination_dir / "translation-cache" / "chunk-000001-old.md").exists()
    assert not (source_root / ".hermes-working" / "Timed Out").exists()
