from pathlib import Path
import json

from pdf_translator import pipeline as pipeline_module
from pdf_translator.config import RunSettings
from pdf_translator.models import NormalizedDocument
from pdf_translator.pipeline import build_artifacts, safe_delivery_file_stem


class _Preflight:
    def as_dict(self) -> dict[str, object]:
        return {
            "page_count": 3,
            "ingest_page_count": 3,
            "file_size_mb": 1.0,
            "profile_name": "book",
            "warnings": [],
        }


def test_safe_delivery_file_stem_uses_source_title_and_language() -> None:
    stem = safe_delivery_file_stem(Path('A:B "Book"? -- Author.epub'), "zh/CN")

    assert stem == "A B Book -- Author (zh CN)"


def test_build_artifacts_names_user_visible_outputs_from_source_title(tmp_path: Path) -> None:
    artifacts = build_artifacts(tmp_path / "run", Path("Sample Book.epub"), "zh-CN")

    assert artifacts.translated_markdown_path.name == "translated.md"
    assert artifacts.translated_epub_path.name == "Sample Book (zh-CN).epub"
    assert artifacts.translated_pdf_path.name == "Sample Book (zh-CN).pdf"


def _patch_intake_dependencies(monkeypatch) -> None:
    def fake_ingest(*args, **kwargs):
        source_pdf = args[0]
        return (
            NormalizedDocument(
                source_pdf=source_pdf,
                raw_markdown="# Raw\n",
                reconstructed_markdown="# Reconstructed\n",
                structured={"pages": []},
                detected_language="zh-CN",
                images_dir=None,
            ),
            _Preflight(),
        )

    def fake_profile(*args, **kwargs):
        return {"profile": "book"}

    def fake_book(*args, **kwargs):
        return {
            "metadata": {"chapter_source": "test", "outline_entry_count": 1},
            "render_policy": {"figures": "preserve"},
            "chapters": [
                {
                    "index": 1,
                    "chapter_id": "chapter-001",
                    "title": "第一章",
                    "markdown": "# 第一章\n\n正文。\n",
                    "source_pages": [1, 2],
                    "page_start": 1,
                    "page_end": 2,
                    "toc": True,
                }
            ],
        }

    monkeypatch.setattr(pipeline_module, "ingest_pdf_guarded", fake_ingest)
    monkeypatch.setattr(pipeline_module, "build_document_profile", fake_profile)
    monkeypatch.setattr(pipeline_module, "build_book_reconstruction", fake_book)


def test_run_intake_pipeline_writes_bookir_without_translation_cache(tmp_path: Path, monkeypatch) -> None:
    _patch_intake_dependencies(monkeypatch)
    settings = RunSettings(
        source_pdf=tmp_path / "中文书.epub",
        output_dir=tmp_path / "runs",
        target_language="source",
        source_language=None,
        translator="none",
        max_chunk_chars=9000,
        profile_name="book",
        output_format="none",
    )

    artifacts = pipeline_module.run_intake_pipeline(settings)
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))

    assert manifest["mode"] == "intake"
    assert manifest["translation"]["mode"] == "not_requested"
    assert manifest["files"]["book_json"].endswith("book.json")
    assert artifacts.book_json_path and artifacts.book_json_path.exists()
    assert not (artifacts.output_dir / "translation-cache").exists()


def test_translate_pipeline_skips_model_for_same_chinese_language(tmp_path: Path, monkeypatch) -> None:
    _patch_intake_dependencies(monkeypatch)

    def fail_build_translator(*args, **kwargs):
        raise AssertionError("translator should not be built for same-language Chinese runs")

    monkeypatch.setattr(pipeline_module, "build_translator", fail_build_translator)
    settings = RunSettings(
        source_pdf=tmp_path / "中文书.epub",
        output_dir=tmp_path / "runs",
        target_language="zh-CN",
        source_language="zh-CN",
        translator="minimax",
        max_chunk_chars=9000,
        profile_name="book",
        output_format="none",
    )

    artifacts = pipeline_module.run_translation_pipeline(settings)
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))

    assert manifest["translation"]["mode"] == "skipped_same_language"
    assert manifest["chunk_count"] == 0
    assert manifest["files"]["translation_cache_dir"] is None
    assert "正文。" in artifacts.translated_markdown_path.read_text(encoding="utf-8")
