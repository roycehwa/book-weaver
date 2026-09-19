from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from pdf_translator.translation_quality import (
    POLISHED_OUTPUT_QUALITY_REPORT,
    RAW_TRANSLATION_QUALITY_REPORT,
    SOURCE_QUALITY_REPORT,
    TRANSLATION_QUALITY_INDEX,
    TranslationQualityBlockedError,
    assert_translation_quality_current,
    build_quality_signature,
    build_polished_output_quality_report,
    build_raw_translation_quality_report,
    build_source_quality_report,
    build_translation_quality_index,
    invalidate_stale_translation_postprocess,
    run_source_quality_gate_before_translation,
    sha256_text,
    write_translation_quality_bundle,
)
from pdf_translator.zh_markdown_cleanup import publish_translation_zh_cleanup
from tests.synthetic_quality_fixtures import write_synthetic_reading_units


def _write_passing_translation_outputs(run_dir: Path, *, source: str, raw: str | None = None) -> None:
    raw_text = raw if raw is not None else source
    cleaned = raw_text
    final = cleaned
    (run_dir / "translated.raw.md").write_text(raw_text, encoding="utf-8")
    (run_dir / "translated.cleaned.md").write_text(cleaned, encoding="utf-8")
    (run_dir / "translated.md").write_text(final, encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps({"text_operation": "translate", "translation": {"mode": "translated"}}),
        encoding="utf-8",
    )
    write_translation_quality_bundle(
        run_dir,
        text_operation="translate",
        source_markdown=source,
        review_items=[],
    )


def test_source_gate_blocks_before_translator(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    (run_dir / "translation-input.md").write_text("bro\u00adken\n", encoding="utf-8")
    with pytest.raises(TranslationQualityBlockedError):
        run_source_quality_gate_before_translation(run_dir, text_operation="translate")
    assert (run_dir / SOURCE_QUALITY_REPORT).exists()
    assert (run_dir / TRANSLATION_QUALITY_INDEX).exists()

    translator = mock.Mock()
    with mock.patch("pdf_translator.pipeline.build_translator", translator):
        with pytest.raises(TranslationQualityBlockedError):
            run_source_quality_gate_before_translation(run_dir, text_operation="translate")
    translator.assert_not_called()


def test_non_zh_cleanup_writes_skipped_report_and_matching_raw_cleaned(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    text, files = publish_translation_zh_cleanup(
        run_dir,
        "Hello world.",
        target_language="en",
        text_operation="translate",
    )
    assert text == "Hello world."
    assert files is not None
    report = json.loads((run_dir / "translation-cleanup-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "skipped"
    assert report["reason"] == "target_not_zh"
    assert (run_dir / "translated.raw.md").read_text(encoding="utf-8") == (run_dir / "translated.cleaned.md").read_text(
        encoding="utf-8"
    )


def test_raw_report_flags_structure_and_link_regressions(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    (run_dir / "translated.raw.md").write_text("# Chapter\n\nHello world.\n\nSecond block.\n", encoding="utf-8")
    report = build_raw_translation_quality_report(
        run_dir,
        text_operation="translate",
        source_markdown=source,
        review_items=[],
    )
    codes = {item["code"] for item in report["findings"]}
    assert "markdown_block_structure_loss" in codes

    (run_dir / "translated.raw.md").write_text("[x](missing.html)\n", encoding="utf-8")
    report = build_raw_translation_quality_report(
        run_dir,
        text_operation="translate",
        source_markdown=source,
        review_items=[],
    )
    codes = {item["code"] for item in report["findings"]}
    assert "invented_link_targets" in codes


def test_polished_report_flags_protected_regression(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    cleaned = "Value 42\n"
    final = "Value 43\n"
    (run_dir / "translated.cleaned.md").write_text(cleaned, encoding="utf-8")
    (run_dir / "translated.md").write_text(final, encoding="utf-8")
    report = build_polished_output_quality_report(run_dir, text_operation="translate")
    codes = {item["code"] for item in report["findings"]}
    assert "numeric_literal_regression" in codes


def test_stale_postprocess_invalidation_keeps_translation_cache(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    cache_dir = run_dir / "translation-cache"
    cache_dir.mkdir()
    (cache_dir / "chunk-0.json").write_text("{}", encoding="utf-8")
    (run_dir / "polish-report.json").write_text("{}", encoding="utf-8")
    (run_dir / "translated.polished.md").write_text("polished", encoding="utf-8")
    index = {
        "schema": "bookweaver_translation_quality_index_v1",
        "signature": {"translation_input_sha256": "old"},
    }
    (run_dir / TRANSLATION_QUALITY_INDEX).write_text(json.dumps(index), encoding="utf-8")
    removed = invalidate_stale_translation_postprocess(run_dir, text_operation="translate")
    assert "polish-report.json" in removed
    assert (cache_dir / "chunk-0.json").exists()


def test_quality_index_signature_changes_with_glossary_fingerprint(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    first = build_quality_signature(run_dir, text_operation="translate")
    monkeypatch.setattr(
        "pdf_translator.translation_quality.glossary_fingerprint",
        lambda _run: "glossary-a",
    )
    second = build_quality_signature(run_dir, text_operation="translate")
    assert first != second


def test_assert_translation_quality_current_passes_for_synthetic_bundle(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(run_dir, source=source)
    assert_translation_quality_current(run_dir)


def test_assert_translation_quality_current_fails_when_artifacts_missing(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"text_operation": "translate", "translation": {"mode": "translated"}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing"):
        assert_translation_quality_current(run_dir)


def test_source_report_is_deterministic(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    first = build_source_quality_report(run_dir, text_operation="translate")
    second = build_source_quality_report(run_dir, text_operation="translate")
    assert first["findings"] == second["findings"]
    assert first["status"] == second["status"]


def test_source_gate_blocks_missing_chapter_segments_fingerprint(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    segments = json.loads((run_dir / "chapter-segments.json").read_text(encoding="utf-8"))
    segments.pop("reading_units_fingerprint", None)
    (run_dir / "chapter-segments.json").write_text(json.dumps(segments), encoding="utf-8")
    report = build_source_quality_report(run_dir, text_operation="translate")
    assert any(item["code"] == "missing_reading_units_fingerprint" for item in report["findings"])


def test_quality_signature_ignores_review_state_but_tracks_translation_inputs(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    base = build_quality_signature(run_dir, text_operation="translate")
    (run_dir / "review_state.json").write_text(
        json.dumps({"decisions": {"seg-1": {"status": "approved", "approved_text": "人工"}}}),
        encoding="utf-8",
    )
    after_review = build_quality_signature(run_dir, text_operation="translate")
    assert base == after_review
    (run_dir / "translated.raw.md").write_text("# Chapter\n\nChanged.\n", encoding="utf-8")
    assert build_quality_signature(run_dir, text_operation="translate") != base


def test_stub_quality_index_does_not_hash_absent_raw_or_polished_reports(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    stale_raw = "stale raw payload"
    (run_dir / RAW_TRANSLATION_QUALITY_REPORT).write_text(stale_raw, encoding="utf-8")
    context = {"reading_units_document_fingerprint": "fp"}
    index = build_translation_quality_index(
        run_dir,
        reports={"source": {"findings": []}, "raw_translation": None, "polished_output": None},
        context=context,
        text_operation="translate",
    )
    assert index["reports"]["raw_translation"]["sha256"] is None
    assert index["reports"]["polished_output"]["sha256"] is None


def test_raw_report_filters_polish_review_items(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    (run_dir / "translated.raw.md").write_text(source, encoding="utf-8")
    report = build_raw_translation_quality_report(
        run_dir,
        text_operation="translate",
        source_markdown=source,
        review_items=[{"issue_type": "polish_unresolved", "segment_id": "seg-1"}],
    )
    assert not any(item["code"] == "polish_unresolved" for item in report["findings"])


def test_polished_report_flags_url_email_anchor_and_code_losses(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    cleaned = "See <https://example.com> and `code` and [^1].\n"
    final = "See <https://other.example> and `code` and [^1].\n"
    (run_dir / "translated.cleaned.md").write_text(cleaned, encoding="utf-8")
    (run_dir / "translated.md").write_text(final, encoding="utf-8")
    report = build_polished_output_quality_report(run_dir, text_operation="translate")
    codes = {item["code"] for item in report["findings"]}
    assert "polished_urls_regression" in codes or "protected_literal_regression" in codes


def test_assert_translation_quality_current_rejects_malformed_report_metadata(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(run_dir, source=source)
    index = json.loads((run_dir / TRANSLATION_QUALITY_INDEX).read_text(encoding="utf-8"))
    index["reports"]["raw_translation"] = {"path": RAW_TRANSLATION_QUALITY_REPORT}
    (run_dir / TRANSLATION_QUALITY_INDEX).write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        assert_translation_quality_current(run_dir)


def test_stale_invalidation_does_not_follow_epub_outside_run_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    outside_epub = tmp_path / "outside.epub"
    outside_epub.write_text("epub", encoding="utf-8")
    (run_dir / "polish-report.json").write_text(
        json.dumps({"outputs": {"translated_polished_epub": str(outside_epub)}}),
        encoding="utf-8",
    )
    (run_dir / TRANSLATION_QUALITY_INDEX).write_text(
        json.dumps({"signature": {"translation_input_sha256": "old"}}),
        encoding="utf-8",
    )
    invalidate_stale_translation_postprocess(run_dir, text_operation="translate")
    assert outside_epub.exists()


def test_translation_source_revision_written_before_raw_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    (run_dir / "translation-source-revision.json").write_text(
        json.dumps({"glossary_fingerprint": "stale", "reading_units_fingerprint": "stale"}),
        encoding="utf-8",
    )
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    (run_dir / "translated.raw.md").write_text(source, encoding="utf-8")
    write_translation_quality_bundle(
        run_dir,
        text_operation="translate",
        source_markdown=source,
        review_items=[],
    )
    binding = json.loads((run_dir / "translation-source-revision.json").read_text(encoding="utf-8"))
    report = json.loads((run_dir / RAW_TRANSLATION_QUALITY_REPORT).read_text(encoding="utf-8"))
    assert binding.get("reading_units_fingerprint")
    assert not any(code.startswith("stale_") for code in (item["code"] for item in report["findings"]))
