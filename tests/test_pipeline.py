from pathlib import Path

import pytest

from pdf_translator.config import RunSettings
from pdf_translator.guardrails import PdfPreflight
from pdf_translator.models import NormalizedDocument
from pdf_translator.pipeline import build_artifacts, safe_delivery_file_stem
from pdf_translator.pipeline import run_translation_pipeline, should_skip_translation


def test_safe_delivery_file_stem_uses_source_title_and_language() -> None:
    stem = safe_delivery_file_stem(Path('A:B "Book"? -- Author.epub'), "zh/CN")

    assert stem == "A B Book -- Author (zh CN)"


def test_build_artifacts_names_user_visible_outputs_from_source_title(tmp_path: Path) -> None:
    artifacts = build_artifacts(tmp_path / "run", Path("Sample Book.epub"), "zh-CN")

    assert artifacts.translated_markdown_path.name == "translated.md"
    assert artifacts.translated_epub_path.name == "Sample Book (zh-CN).epub"
    assert artifacts.translated_pdf_path.name == "Sample Book (zh-CN).pdf"


def test_should_skip_translation_for_chinese_source_to_chinese_target() -> None:
    assert should_skip_translation("zh-CN", "zh-CN")
    assert should_skip_translation("Chinese", "zh")
    assert not should_skip_translation("en", "zh-CN")


def test_pipeline_skips_translation_for_chinese_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "中文书.pdf"
    source.write_bytes(b"%PDF")
    preflight = PdfPreflight(
        source_pdf=source,
        profile_name="book",
        page_count=1,
        file_size_bytes=4,
        warn_page_count=None,
        max_page_count=None,
        warn_file_size_mb=None,
        max_file_size_mb=None,
    )
    normalized = NormalizedDocument(
        source_pdf=source,
        raw_markdown="# 第一章\n\n正文。",
        reconstructed_markdown="# 第一章\n\n正文。",
        structured={"pages": []},
        detected_language="zh-CN",
    )

    monkeypatch.setattr("pdf_translator.pipeline.ingest_pdf_guarded", lambda *args, **kwargs: (normalized, preflight))
    monkeypatch.setattr(
        "pdf_translator.pipeline.build_document_profile",
        lambda *args, **kwargs: {"profile": "magazine"},
    )
    monkeypatch.setattr(
        "pdf_translator.pipeline.build_translator",
        lambda name: (_ for _ in ()).throw(AssertionError("translator should not be built")),
    )

    def fake_render_epub_from_book(**kwargs: object) -> None:
        Path(kwargs["output_path"]).write_bytes(b"epub")

    monkeypatch.setattr("pdf_translator.pipeline.render_epub_from_book", fake_render_epub_from_book)
    monkeypatch.setattr("pdf_translator.pipeline.validate_epub_internal_hrefs", lambda path: {"total_internal_hrefs": 0})

    artifacts = run_translation_pipeline(
        RunSettings(
            source_pdf=source,
            output_dir=tmp_path / "runs",
            target_language="zh-CN",
            source_language="zh-CN",
            translator="minimax",
            max_chunk_chars=9000,
            profile_name="book",
        )
    )

    manifest = __import__("json").loads(artifacts.manifest_path.read_text(encoding="utf-8"))
    assert manifest["translation_mode"] == "skipped_same_language"
    assert manifest["translator"] == "skipped"
    assert manifest["chunk_count"] == 0
    assert "segments" in manifest["files"]
    assert "translated_segments" in manifest["files"]
    assert "review_items" in manifest["files"]
    assert "review_state" in manifest["files"]
    review_items = __import__("json").loads(Path(manifest["files"]["review_items"]).read_text(encoding="utf-8"))
    assert review_items["schema"] == "translation_review_items_v1"
    assert artifacts.translated_markdown_path.read_text(encoding="utf-8") == "# 第一章\n\n正文。\n"
