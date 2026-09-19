from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from pdf_translator.translation_quality import (
    RAW_TRANSLATION_QUALITY_REPORT,
    SOURCE_QUALITY_REPORT,
    TRANSLATION_QUALITY_INDEX,
    TranslationQualityBlockedError,
    assert_translation_quality_current,
    build_quality_signature,
    build_polished_output_quality_report,
    build_raw_translation_quality_report,
    build_source_quality_report,
    invalidate_stale_translation_postprocess,
    run_source_quality_gate_before_translation,
    sha256_text,
    write_translation_quality_bundle,
)
from pdf_translator.zh_markdown_cleanup import publish_translation_zh_cleanup


def _write_reading_units(run_dir: Path) -> None:
    from pdf_translator.reading_units import build_reading_units

    book = {
        "chapters": [
            {
                "index": 1,
                "chapter_id": "ch-1",
                "title": "Chapter",
                "markdown": "# Chapter\n\nHello world.\n",
                "source_pages": [1],
                "translate": True,
            }
        ]
    }
    payload = build_reading_units(book, source_path=Path("synthetic.pdf"), translation_authority=True)
    (run_dir / "reading-units.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = "# Chapter\n\nHello world.\n"
    segments = {
        "schema": "bookweaver_chapter_segments_v1",
        "reading_units_fingerprint": payload["document_fingerprint"],
        "segments": [
            {
                "segment_id": "seg-1",
                "chapter_id": "ch-1",
                "block_index": 0,
                "markdown": markdown,
            }
        ],
    }
    (run_dir / "chapter-segments.json").write_text(json.dumps(segments, indent=2), encoding="utf-8")
    (run_dir / "translation-input.md").write_text(markdown, encoding="utf-8")


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
    _write_reading_units(run_dir)
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
    _write_reading_units(run_dir)
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
    _write_reading_units(run_dir)
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
    _write_reading_units(run_dir)
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
    _write_reading_units(run_dir)
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
    _write_reading_units(run_dir)
    first = build_source_quality_report(run_dir, text_operation="translate")
    second = build_source_quality_report(run_dir, text_operation="translate")
    assert first["findings"] == second["findings"]
    assert first["status"] == second["status"]
