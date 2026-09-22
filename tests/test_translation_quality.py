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
    TRANSLATION_QUALITY_SOURCE,
    TranslationQualityBlockedError,
    assert_translation_quality_current,
    build_quality_signature,
    build_polished_output_quality_report,
    build_raw_translation_quality_report,
    build_source_quality_report,
    build_translation_quality_index,
    build_quality_context,
    effective_translation_quality_evaluation,
    invalidate_stale_translation_postprocess,
    revalidate_translation_quality,
    run_source_quality_gate_before_translation,
    sha256_file,
    sha256_text,
    stable_finding_id,
    translation_quality_summary,
    write_translation_quality_bundle,
    _finding,
)
from pdf_translator.zh_markdown_cleanup import publish_translation_zh_cleanup
from tests.synthetic_quality_fixtures import write_synthetic_reading_units


def _write_passing_translation_outputs(
    run_dir: Path,
    *,
    source: str,
    raw: str | None = None,
    review_items: list[dict] | None = None,
) -> None:
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
        review_items=review_items or [],
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
    findings = {item["code"]: item for item in report["findings"]}
    assert findings["markdown_prose_block_shape_changed"]["severity"] == "review"
    assert "markdown_block_structure_loss" not in findings

    (run_dir / "translated.raw.md").write_text("Hello world.\n", encoding="utf-8")
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


def test_raw_report_compares_delivery_shape_separately_from_transport_input(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    transport = (
        "<!-- translation input: generated -->\n\n"
        "## Chapter\n\nRead the [source](https://example.com/source).\n"
    )
    comparison = (
        "<!-- internal chapter boundary -->\n\n<!DOCTYPE html>\n\n"
        "# Cover\n\n![Cover](book-images/cover.jpg)\n\n"
        "# Chapter\n\nRead the [source](https://example.com/source).\n"
    )
    raw = (
        "# Cover\n\n![封面](book-images/cover.jpg)\n\n"
        "# Chapter\n\n请阅读[来源](https://example.com/source)。\n"
    )
    (run_dir / "translation-input.md").write_text(transport, encoding="utf-8")
    (run_dir / "translated.raw.md").write_text(raw, encoding="utf-8")
    report = build_raw_translation_quality_report(
        run_dir,
        text_operation="translate",
        source_markdown=transport,
        comparison_source_markdown=comparison,
        review_items=[],
    )
    codes = {item["code"] for item in report["findings"]}
    assert not codes.intersection(
        {
            "markdown_block_structure_loss",
            "invented_link_targets",
            "lost_link_targets",
            "markdown_link_structure_loss",
            "urls_regression",
            "link_destinations_regression",
            "html_tags_regression",
        }
    )


def test_raw_report_advises_on_real_external_link_change_with_delivery_shape(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    transport = "## Chapter\n\nRead the source.\n"
    comparison = "# Chapter\n\n[Source](https://example.com/source).\n"
    raw = "# Chapter\n\n[来源](https://evil.example/changed)。\n"
    (run_dir / "translation-input.md").write_text(transport, encoding="utf-8")
    (run_dir / "translated.raw.md").write_text(raw, encoding="utf-8")
    report = build_raw_translation_quality_report(
        run_dir,
        text_operation="translate",
        source_markdown=transport,
        comparison_source_markdown=comparison,
        review_items=[],
    )
    codes = {item["code"] for item in report["findings"]}
    assert "invented_link_targets" in codes
    assert "lost_link_targets" in codes
    assert "urls_regression" in codes
    assert all(item["severity"] == "review" for item in report["findings"] if item["code"] in codes)


def test_raw_report_accepts_existing_body_headings_and_preserved_link(tmp_path: Path) -> None:
    from pdf_translator.translate import render_translation_quality_source

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    transport = "## Two\n\n## Alternative Heading\n\nRead [source](https://example.org/source).\n"
    book = {
        "chapters": [{
            "index": 1, "chapter_id": "chapter-two", "title": "2. Source TOC Label",
            "kind": "narrative", "translate": True, "toc": True,
            "markdown": transport,
        }],
        "chapter_segments": [
            {"chapter_id": "chapter-two", "chapter_title": "2. Source TOC Label",
             "markdown": "## Two", "role": "heading", "separator_before": "\n\n"},
            {"chapter_id": "chapter-two", "chapter_title": "2. Source TOC Label",
             "markdown": "## Alternative Heading", "role": "heading", "separator_before": "\n\n"},
            {"chapter_id": "chapter-two", "chapter_title": "2. Source TOC Label",
             "markdown": "Read [source](https://example.org/source).", "role": "prose",
             "separator_before": "\n\n"},
        ],
    }
    raw = "## Two\n\n## 另一标题\n\n请阅读[来源](https://example.org/source)。\n"
    (run_dir / "translation-input.md").write_text(transport, encoding="utf-8")
    (run_dir / "translated.raw.md").write_text(raw, encoding="utf-8")

    report = build_raw_translation_quality_report(
        run_dir,
        text_operation="translate",
        source_markdown=transport,
        comparison_source_markdown=render_translation_quality_source(book),
    )

    blocking = {item["code"] for item in report["findings"] if item["severity"] == "blocking"}
    assert not blocking


def test_title_only_chapter_keeps_delivery_heading_in_quality_source(tmp_path: Path) -> None:
    from pdf_translator.translate import render_translation_quality_source

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    book = {
        "chapters": [
            {"index": 1, "chapter_id": "part-title", "title": "Part Two",
             "kind": "narrative", "translate": True, "toc": True,
             "markdown": "## Part Two"},
            {"index": 2, "chapter_id": "chapter-body", "title": "Chapter",
             "kind": "narrative", "translate": True, "toc": True,
             "markdown": "## Chapter\n\nBody text."},
        ],
        "chapter_segments": [
            {"chapter_id": "part-title", "chapter_title": "Part Two",
             "markdown": "## Part Two", "role": "heading", "is_chapter_title": True},
            {"chapter_id": "chapter-body", "chapter_title": "Chapter",
             "markdown": "## Chapter", "role": "heading", "is_chapter_title": True},
            {"chapter_id": "chapter-body", "chapter_title": "Chapter",
             "markdown": "Body text.", "role": "prose", "separator_before": "\n\n"},
        ],
    }
    comparison = render_translation_quality_source(book)
    assert comparison == "# Part Two\n\n# Chapter\n\nBody text.\n"
    transport = "## Part Two\n\n## Chapter\n\nBody text.\n"
    (run_dir / "translation-input.md").write_text(transport, encoding="utf-8")
    (run_dir / "translated.raw.md").write_text(
        "# 第二部分\n\n# 章节\n\n正文。\n", encoding="utf-8",
    )
    report = build_raw_translation_quality_report(
        run_dir, text_operation="translate", source_markdown=transport,
        comparison_source_markdown=comparison, review_items=[],
    )
    assert not [finding for finding in report["findings"] if finding["severity"] == "blocking"]


def test_lost_link_finding_points_to_review_segment(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = "# Chapter\n\nRead [source](chapter.xhtml#note-2).\n"
    raw = "# 章节\n\n请阅读来源。\n"
    (run_dir / "translation-input.md").write_text(source, encoding="utf-8")
    (run_dir / "translated.raw.md").write_text(raw, encoding="utf-8")
    (run_dir / "segments.json").write_text(json.dumps({"segments": [{
        "segment_id": "s2", "source_text": "Read [source](chapter.xhtml#note-2).",
        "source_location": {"page_start": 12},
    }]}), encoding="utf-8")
    (run_dir / "translated_segments.json").write_text(json.dumps({"segments": [{
        "segment_id": "s2", "translated_text": "请阅读来源。",
        "source_location": {"page_start": 12},
    }]}), encoding="utf-8")

    report = build_raw_translation_quality_report(
        run_dir, text_operation="translate", source_markdown=source,
    )
    finding = next(item for item in report["findings"] if item["code"] == "lost_link_targets")
    assert finding["segment_ids"] == ["s2"]
    assert finding["source_location"] == {"page_start": 12}


def test_link_difference_does_not_require_manual_repair(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = "# Chapter\n\nRead [source](chapter.xhtml#note-2).\n"
    raw = "# 章节\n\n请阅读来源。\n"
    (run_dir / "translation-input.md").write_text(source, encoding="utf-8")
    (run_dir / "segments.json").write_text(json.dumps({"segments": [{
        "segment_id": "s2", "source_text": "Read [source](chapter.xhtml#note-2).",
        "source_location": {"page_start": 12},
    }]}), encoding="utf-8")
    (run_dir / "translated_segments.json").write_text(json.dumps({"segments": [{
        "segment_id": "s2", "translated_text": "请阅读来源。",
        "source_location": {"page_start": 12},
    }]}), encoding="utf-8")
    _write_passing_translation_outputs(run_dir, source=source, raw=raw)

    def decide(value: str) -> None:
        (run_dir / "review_state.json").write_text(json.dumps({"decisions": {
            "s2": {"status": "approved", "action": "manual_edit", "approved_text": value},
        }}), encoding="utf-8")

    summary = translation_quality_summary(run_dir)
    assert summary["translation_quality_blocking"] is False
    assert any(item["code"] == "lost_link_targets" for item in summary["navigation_hints"])
    decide("请阅读来源。")
    assert translation_quality_summary(run_dir)["translation_quality_blocking"] is False
    decide("请阅读[来源](chapter.xhtml#wrong)。")
    assert translation_quality_summary(run_dir)["translation_quality_blocking"] is False
    decide("请阅读[来源](chapter.xhtml#note-2)。")
    assert translation_quality_summary(run_dir)["translation_quality_blocking"] is False
    assert_translation_quality_current(run_dir)


def test_raw_report_still_blocks_semantic_html_tag_change(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = "# Chapter\n\n<span id=\"source-note\">Text</span>\n"
    raw = "# Chapter\n\n<span id=\"changed-note\">文字</span>\n"
    (run_dir / "translation-input.md").write_text(source, encoding="utf-8")
    (run_dir / "translated.raw.md").write_text(raw, encoding="utf-8")
    report = build_raw_translation_quality_report(
        run_dir,
        text_operation="translate",
        source_markdown=source,
        comparison_source_markdown=source,
        review_items=[],
    )
    assert any(item["code"] == "html_tags_regression" for item in report["findings"])


def test_raw_link_changes_do_not_mask_required_image_loss(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = "# Chapter\n\n![Map](assets/map.png)\n\n[Source](old.xhtml#note).\n"
    raw = "# 章\n\n![图](assets/other.png)\n\n[来源](new.xhtml#note)。\n"
    (run_dir / "translation-input.md").write_text(source, encoding="utf-8")
    (run_dir / "translated.raw.md").write_text(raw, encoding="utf-8")
    report = build_raw_translation_quality_report(
        run_dir, text_operation="translate", source_markdown=source, review_items=[],
    )
    findings = {item["code"]: item for item in report["findings"]}
    assert findings["markdown_link_structure_loss"]["severity"] == "blocking"
    assert findings["lost_link_targets"]["severity"] == "review"


def test_polished_link_digits_are_advisory_but_prose_digits_stay_strict(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    cleaned = "# Chapter\n\n第 12 页请看[资料](https://old.example/123)。\n"
    (run_dir / "translated.cleaned.md").write_text(cleaned, encoding="utf-8")
    (run_dir / "translated.md").write_text(
        "# Chapter\n\n第 12 页请看[资料](https://new.example/456)。\n", encoding="utf-8",
    )
    report = build_polished_output_quality_report(run_dir, text_operation="translate")
    assert any(item["severity"] == "review" for item in report["findings"])
    assert not any(item["severity"] == "blocking" for item in report["findings"])
    (run_dir / "translated.md").write_text(
        "# Chapter\n\n第 13 页请看[资料](https://new.example/456)。\n", encoding="utf-8",
    )
    report = build_polished_output_quality_report(run_dir, text_operation="translate")
    assert any(item["code"] == "numeric_literal_regression" and item["severity"] == "blocking"
               for item in report["findings"])


def test_revalidate_translation_quality_rebuilds_only_derived_reports(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "chapter_id": "ch-1",
                        "title": "Chapter",
                        "kind": "narrative",
                        "markdown": source,
                        "translate": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    raw = "# Chapter\n\n你好，世界。\n"
    for name in ("translated.raw.md", "translated.cleaned.md", "translated.md"):
        (run_dir / name).write_text(raw, encoding="utf-8")
    (run_dir / "review_items.json").write_text('{"items": []}', encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps({"text_operation": "translate", "translation": {"mode": "translated"}}),
        encoding="utf-8",
    )

    summary = revalidate_translation_quality(run_dir)

    assert summary["translation_quality_blocking"] is False
    assert (run_dir / TRANSLATION_QUALITY_SOURCE).read_text(encoding="utf-8") == source


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


def test_polished_report_inspects_review_buckets_when_outcome_applied(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    text = "Same line.\n"
    (run_dir / "translated.cleaned.md").write_text(text, encoding="utf-8")
    (run_dir / "translated.md").write_text(text, encoding="utf-8")
    (run_dir / "polish-report.json").write_text(
        json.dumps(
            {
                "outcome": "applied",
                "rejected": [{"decision": "reject", "line": 1, "before": "Same line."}],
            }
        ),
        encoding="utf-8",
    )
    report = build_polished_output_quality_report(run_dir, text_operation="translate")
    assert any(item["code"] == "polish_rejected" for item in report["findings"])


def test_raw_report_input_sha_matches_translation_input_file(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    (run_dir / "translated.raw.md").write_text(source, encoding="utf-8")
    report = build_raw_translation_quality_report(
        run_dir,
        text_operation="translate",
        source_markdown=source,
        review_items=[],
    )
    assert report["input_sha256"] == sha256_file(run_dir / "translation-input.md")
    wrong = build_raw_translation_quality_report(
        run_dir,
        text_operation="translate",
        source_markdown=source + "\n",
        review_items=[],
    )
    assert any(item["code"] == "translation_input_sha_mismatch" for item in wrong["findings"])


def test_review_findings_include_unit_ids_and_source_location(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    (run_dir / "translated.raw.md").write_text(source, encoding="utf-8")
    segments = json.loads((run_dir / "chapter-segments.json").read_text(encoding="utf-8"))
    unit_ids = segments["segments"][0]["unit_ids"]
    report = build_raw_translation_quality_report(
        run_dir,
        text_operation="translate",
        source_markdown=source,
        review_items=[{"issue_type": "mixed_english", "segment_id": "seg-1", "evidence": {"token": "API"}}],
    )
    finding = next(item for item in report["findings"] if item["code"] == "mixed_english")
    assert finding["unit_ids"] == unit_ids
    assert finding["source_location"]["resource_path"] == "synthetic.pdf"
    assert "evidence" not in finding["evidence"]


def test_stable_finding_id_differs_for_same_evidence_in_different_segments() -> None:
    evidence = {"token": "API"}
    first = stable_finding_id(
        stage="raw_translation",
        code="mixed_english",
        evidence=evidence,
        segment_ids=["seg-1"],
    )
    second = stable_finding_id(
        stage="raw_translation",
        code="mixed_english",
        evidence=evidence,
        segment_ids=["seg-2"],
    )
    assert first != second


def test_assert_translation_quality_current_unchanged_after_review_state(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(run_dir, source=source)
    (run_dir / "review_state.json").write_text(
        json.dumps({"decisions": {"seg-1": {"status": "approved", "approved_text": "人工"}}}),
        encoding="utf-8",
    )
    assert_translation_quality_current(run_dir)


def test_assert_translation_quality_current_fails_on_stale_signature(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(run_dir, source=source)
    (run_dir / "translated.raw.md").write_text("# changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="stale"):
        assert_translation_quality_current(run_dir)


def test_assert_translation_quality_current_fails_on_stale_raw_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(run_dir, source=source)
    (run_dir / RAW_TRANSLATION_QUALITY_REPORT).write_text('{"stale": true}', encoding="utf-8")
    with pytest.raises(ValueError, match="stale"):
        assert_translation_quality_current(run_dir)


def test_assert_translation_quality_current_fails_on_outside_report_path(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(run_dir, source=source)
    outside = tmp_path / "outside-report.json"
    outside.write_text((run_dir / RAW_TRANSLATION_QUALITY_REPORT).read_text(encoding="utf-8"), encoding="utf-8")
    index = json.loads((run_dir / TRANSLATION_QUALITY_INDEX).read_text(encoding="utf-8"))
    index["reports"]["raw_translation"] = {
        "path": str(outside),
        "sha256": sha256_file(outside),
    }
    (run_dir / TRANSLATION_QUALITY_INDEX).write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(ValueError, match="outside"):
        assert_translation_quality_current(run_dir)


def test_assert_translation_quality_current_fails_on_blocking_index(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(run_dir, source=source)
    raw_path = run_dir / RAW_TRANSLATION_QUALITY_REPORT
    raw_report = json.loads(raw_path.read_text(encoding="utf-8"))
    raw_report["findings"].append(
        _finding(
            stage="raw_translation",
            code="untranslated",
            severity="blocking",
            message="Synthetic blocking segment finding.",
            segment_ids=["seg-1"],
        )
    )
    raw_path.write_text(json.dumps(raw_report), encoding="utf-8")
    index = build_translation_quality_index(
        run_dir,
        reports={
            "source": json.loads((run_dir / SOURCE_QUALITY_REPORT).read_text(encoding="utf-8")),
            "raw_translation": raw_report,
            "polished_output": json.loads((run_dir / POLISHED_OUTPUT_QUALITY_REPORT).read_text(encoding="utf-8")),
        },
        context=build_quality_context(run_dir),
        text_operation="translate",
    )
    (run_dir / TRANSLATION_QUALITY_INDEX).write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(ValueError, match="blocking"):
        assert_translation_quality_current(run_dir)


def test_effective_quality_blocks_when_index_count_disagrees_with_reports(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(run_dir, source=source)
    index_path = run_dir / TRANSLATION_QUALITY_INDEX
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["blocking_count"] = 1
    index["aggregate_status"] = "blocked"
    index["acceptable"] = False
    index_path.write_text(json.dumps(index), encoding="utf-8")

    summary = translation_quality_summary(run_dir)
    assert summary["translation_quality_blocking"] is True
    assert "translation_quality_index_count_mismatch" in summary["artifact_errors"]
    with pytest.raises(ValueError, match="blocking"):
        assert_translation_quality_current(run_dir)


def test_effective_quality_allows_adjudicated_segment_blocking_finding(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(
        run_dir,
        source=source,
        review_items=[{"issue_type": "untranslated", "segment_id": "seg-1", "evidence": {}}],
    )
    (run_dir / "review_state.json").write_text(
        json.dumps({"decisions": {"seg-1": {"status": "approved", "approved_text": "人工"}}}),
        encoding="utf-8",
    )
    summary = translation_quality_summary(run_dir)
    assert summary["translation_quality_blocking"] is False
    assert_translation_quality_current(run_dir)


def test_effective_quality_allows_adjudicated_missing_translation_finding(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(
        run_dir,
        source=source,
        review_items=[{"issue_type": "missing_translation", "segment_id": "seg-1", "evidence": {}}],
    )
    (run_dir / "review_state.json").write_text(
        json.dumps({"decisions": {"seg-1": {"status": "resolved", "approved_text": "人工补译"}}}),
        encoding="utf-8",
    )
    assert effective_translation_quality_evaluation(run_dir)["translation_quality_blocking"] is False
    assert_translation_quality_current(run_dir)


def test_deferred_provider_refusal_blocks_export_until_review_translation(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(
        run_dir,
        source=source,
        review_items=[{"issue_type": "translation_failed_open", "segment_id": "seg-1", "evidence": {}}],
    )
    (run_dir / "review_state.json").write_text(json.dumps({"decisions": {}}), encoding="utf-8")
    assert effective_translation_quality_evaluation(run_dir)["translation_quality_blocking"] is True
    with pytest.raises(ValueError, match="blocking"):
        assert_translation_quality_current(run_dir)
    (run_dir / "review_state.json").write_text(
        json.dumps({"decisions": {"seg-1": {"status": "approved", "approved_text": "人工补译"}}}),
        encoding="utf-8",
    )
    assert effective_translation_quality_evaluation(run_dir)["translation_quality_blocking"] is False


def test_effective_quality_blocks_unresolved_segment_finding(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(
        run_dir,
        source=source,
        review_items=[{"issue_type": "untranslated", "segment_id": "seg-1", "evidence": {}}],
    )
    summary = translation_quality_summary(run_dir)
    assert summary["translation_quality_blocking"] is True
    with pytest.raises(ValueError, match="blocking"):
        assert_translation_quality_current(run_dir)


def test_quality_summary_blocks_when_indexed_report_is_missing(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(run_dir, source=source)
    (run_dir / RAW_TRANSLATION_QUALITY_REPORT).unlink()

    summary = translation_quality_summary(run_dir)
    assert summary["translation_quality_blocking"] is True
    assert summary["effective_blocking_count"] >= 1
    with pytest.raises(ValueError, match="artifacts are missing"):
        assert_translation_quality_current(run_dir)


def test_effective_quality_keeps_global_blocking_finding(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(run_dir, source=source)
    raw_path = run_dir / RAW_TRANSLATION_QUALITY_REPORT
    raw_report = json.loads(raw_path.read_text(encoding="utf-8"))
    raw_report["findings"].append(
        _finding(
            stage="raw_translation",
            code="markdown_block_structure_loss",
            severity="blocking",
            message="Global structure regression must remain blocking.",
            segment_ids=["seg-1"],
        )
    )
    raw_path.write_text(json.dumps(raw_report), encoding="utf-8")
    index = build_translation_quality_index(
        run_dir,
        reports={
            "source": json.loads((run_dir / SOURCE_QUALITY_REPORT).read_text(encoding="utf-8")),
            "raw_translation": raw_report,
            "polished_output": json.loads((run_dir / POLISHED_OUTPUT_QUALITY_REPORT).read_text(encoding="utf-8")),
        },
        context=build_quality_context(run_dir),
        text_operation="translate",
    )
    (run_dir / TRANSLATION_QUALITY_INDEX).write_text(json.dumps(index), encoding="utf-8")
    (run_dir / "review_state.json").write_text(
        json.dumps({"decisions": {"seg-1": {"status": "approved"}}}),
        encoding="utf-8",
    )
    assert effective_translation_quality_evaluation(run_dir)["translation_quality_blocking"] is True
    with pytest.raises(ValueError, match="blocking"):
        assert_translation_quality_current(run_dir)


def test_assert_translation_quality_current_allows_review_only_index(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(run_dir, source=source)
    index = json.loads((run_dir / TRANSLATION_QUALITY_INDEX).read_text(encoding="utf-8"))
    index["blocking_count"] = 0
    index["review_count"] = 3
    index["aggregate_status"] = "review"
    index["acceptable"] = True
    (run_dir / TRANSLATION_QUALITY_INDEX).write_text(json.dumps(index), encoding="utf-8")
    assert_translation_quality_current(run_dir)


def test_assert_translation_quality_current_skips_preserve_mode(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"text_operation": "preserve", "translation": {"mode": "preserved"}}),
        encoding="utf-8",
    )
    assert_translation_quality_current(run_dir)


def test_stale_invalidation_keeps_translation_and_polish_cache(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    translation_cache = run_dir / "translation-cache"
    polish_cache = run_dir / "polish-cache"
    translation_cache.mkdir()
    polish_cache.mkdir()
    (translation_cache / "chunk-0.json").write_text("{}", encoding="utf-8")
    (polish_cache / "line-1.json").write_text("{}", encoding="utf-8")
    (run_dir / "polish-report.json").write_text("{}", encoding="utf-8")
    (run_dir / TRANSLATION_QUALITY_INDEX).write_text(
        json.dumps({"signature": {"translation_input_sha256": "old"}}),
        encoding="utf-8",
    )
    invalidate_stale_translation_postprocess(run_dir, text_operation="translate")
    assert (translation_cache / "chunk-0.json").exists()
    assert (polish_cache / "line-1.json").exists()
    assert not (run_dir / "polish-report.json").exists()


def test_assert_translation_quality_current_fails_on_stale_final_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(run_dir, source=source)
    (run_dir / POLISHED_OUTPUT_QUALITY_REPORT).write_text('{"stale": true}', encoding="utf-8")
    with pytest.raises(ValueError, match="stale"):
        assert_translation_quality_current(run_dir)


def test_assert_translation_quality_current_fails_when_reading_units_change(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(run_dir, source=source)
    reading_units = json.loads((run_dir / "reading-units.json").read_text(encoding="utf-8"))
    reading_units["document_fingerprint"] = "changed-fingerprint"
    (run_dir / "reading-units.json").write_text(json.dumps(reading_units), encoding="utf-8")
    with pytest.raises(ValueError, match="stale"):
        assert_translation_quality_current(run_dir)


def test_assert_translation_quality_current_fails_when_glossary_changes(
    tmp_path: Path, monkeypatch
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    source = (run_dir / "translation-input.md").read_text(encoding="utf-8")
    _write_passing_translation_outputs(run_dir, source=source)
    monkeypatch.setattr(
        "pdf_translator.translation_quality.glossary_fingerprint",
        lambda _run: "glossary-changed",
    )
    with pytest.raises(ValueError, match="stale"):
        assert_translation_quality_current(run_dir)


def test_unchanged_signature_does_not_drop_polish_cache(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_synthetic_reading_units(run_dir)
    signature = build_quality_signature(run_dir, text_operation="translate")
    polish_cache = run_dir / "polish-cache"
    polish_cache.mkdir()
    cache_file = polish_cache / "candidate.json"
    cache_file.write_text('{"suggestion": "活跃"}', encoding="utf-8")
    (run_dir / TRANSLATION_QUALITY_INDEX).write_text(
        json.dumps({"signature": signature}),
        encoding="utf-8",
    )
    removed = invalidate_stale_translation_postprocess(run_dir, text_operation="translate")
    assert removed == []
    assert cache_file.exists()
    assert json.loads(cache_file.read_text(encoding="utf-8"))["suggestion"] == "活跃"
