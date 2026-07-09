from pathlib import Path
import json

import pytest

from pdf_translator import pipeline as pipeline_module
from pdf_translator import polish as polish_module
from pdf_translator.config import RunSettings
from pdf_translator.models import NormalizedDocument
from pdf_translator.pdf_text_repair import IngestQualityError
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
    page_ledger_path = artifacts.output_dir / "page-ledger.json"
    assert page_ledger_path.exists()
    page_ledger = json.loads(page_ledger_path.read_text(encoding="utf-8"))
    assert page_ledger["summary"]["required_coverage_ratio"] == 1.0
    integrity_ledger_path = artifacts.output_dir / "integrity-ledger.json"
    assert integrity_ledger_path.exists()
    integrity_ledger = json.loads(integrity_ledger_path.read_text(encoding="utf-8"))
    assert integrity_ledger["schema"] == "integrity_ledger_v1"
    assert not (artifacts.output_dir / "translation-cache").exists()


def test_translation_pipeline_writes_user_chapter_segment_plan_before_translation(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    source = tmp_path / "source.epub"
    source.write_text("placeholder", encoding="utf-8")
    book = {
        "metadata": {"chapter_source": "automatic"},
        "pages": [
            {"page_no": 1, "has_content": True},
            {"page_no": 2, "has_content": True},
        ],
        "chapters": [
            {
                "index": 1,
                "chapter_id": "auto-001",
                "title": "Automatic",
                "page_start": 1,
                "page_end": 2,
                "source_pages": [1, 2],
                "markdown": "## First Idea\n\nAlpha paragraph.\n\n## Second Idea\n\nBeta paragraph.",
                "trace_markdown": "[[page: 1]]\n\n## First Idea\n\nAlpha paragraph.\n\n[[page: 2]]\n\n## Second Idea\n\nBeta paragraph.",
                "translate": True,
                "toc": True,
            }
        ],
    }
    canonical = {
        "schema": "bookmate_canonical_chapters_v1",
        "chapters": [
            {
                "title": "User Chapter",
                "page_start": 1,
                "page_end": 2,
                "source_pages": [1, 2],
            }
        ],
    }
    (run_dir / "book.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
    canonical_path = run_dir / "canonical-chapters.json"
    canonical_path.write_text(json.dumps(canonical, ensure_ascii=False), encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "mode": "intake",
                "source_pdf": str(source),
                "source_language": "en",
                "target_language": None,
                "preflight": {"page_count": 2},
                "files": {"book_json": str(run_dir / "book.json")},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    artifacts = pipeline_module.run_translation_pipeline(
        RunSettings(
            source_pdf=source,
            output_dir=tmp_path / "runs",
            target_language="fr",
            source_language="en",
            translator="mock",
            max_chunk_chars=10_000,
            output_format="none",
            processing_mode="translate",
            existing_run_dir=run_dir,
            canonical_chapters_path=canonical_path,
        )
    )

    segment_path = artifacts.output_dir / "chapter-segments.json"
    assert segment_path.exists()
    payload = json.loads(segment_path.read_text(encoding="utf-8"))
    assert payload["schema"] == "bookweaver_chapter_segments_v1"
    assert [segment["chapter_title"] for segment in payload["segments"]] == [
        "User Chapter",
        "User Chapter",
    ]
    assert [segment["section_title"] for segment in payload["segments"]] == [
        "First Idea",
        "Second Idea",
    ]
    progress = json.loads((artifacts.output_dir / "jobs" / "progress.json").read_text(encoding="utf-8"))
    assert progress["total_chunks"] == 2


def test_epub_intake_does_not_apply_pdf_space_merging_to_normal_prose(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_intake_dependencies(monkeypatch)
    monkeypatch.setattr(
        pipeline_module,
        "build_book_reconstruction",
        lambda *_args, **_kwargs: {
            "metadata": {"chapter_source": "epub_spine", "outline_entry_count": 1},
            "render_policy": {"figures": "preserve"},
            "full_markdown": "# Preface\n\nI set off as a student a few years ago.\n",
            "chapters": [
                {
                    "index": 1,
                    "chapter_id": "chapter-001",
                    "title": "Preface",
                    "markdown": "I set off as a student a few years ago.\n",
                    "source_pages": [1],
                    "page_start": 1,
                    "page_end": 1,
                    "toc": True,
                }
            ],
        },
    )
    settings = RunSettings(
        source_pdf=tmp_path / "english-book.epub",
        output_dir=tmp_path / "runs",
        target_language="zh-CN",
        source_language="en",
        translator="mock",
        max_chunk_chars=9000,
        profile_name="book",
        output_format="none",
    )

    artifacts = pipeline_module.run_intake_pipeline(settings)
    translation_input = artifacts.translation_input_markdown_path.read_text(encoding="utf-8")

    assert "I set off as a student a few years ago." in translation_input
    assert "Isetoff" not in translation_input
    assert "astudent" not in translation_input


def test_epub_intake_blocks_before_glossary_when_character_corruption_remains(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _patch_intake_dependencies(monkeypatch)
    monkeypatch.setattr(
        pipeline_module,
        "build_book_reconstruction",
        lambda *_args, **_kwargs: {
            "metadata": {"chapter_source": "epub_spine", "outline_entry_count": 1},
            "render_policy": {"figures": "preserve"},
            "full_markdown": "# Preface\n\nDamaged \ufffd prose.\n",
            "chapters": [],
        },
    )
    settings = RunSettings(
        source_pdf=tmp_path / "damaged.epub",
        output_dir=tmp_path / "runs",
        target_language="zh-CN",
        source_language="en",
        translator="mock",
        max_chunk_chars=9000,
        profile_name="book",
        output_format="none",
    )

    with pytest.raises(IngestQualityError, match="replacement_character"):
        pipeline_module.run_intake_pipeline(settings)

    report_path = tmp_path / "runs" / "damaged" / "ingest-quality-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["acceptable"] is False
    assert report["blocking_issues"][0]["code"] == "replacement_character"


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


def test_translate_pipeline_writes_review_artifacts_for_real_translation(tmp_path: Path, monkeypatch) -> None:
    _patch_intake_dependencies(monkeypatch)
    settings = RunSettings(
        source_pdf=tmp_path / "english-book.epub",
        output_dir=tmp_path / "runs",
        target_language="zh-CN",
        source_language="en",
        translator="mock",
        max_chunk_chars=9000,
        profile_name="book",
        output_format="none",
    )

    artifacts = pipeline_module.run_translation_pipeline(settings)
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))

    assert manifest["translation"]["mode"] == "translated"
    assert manifest["files"]["translated_chapters"].endswith("translated-chapters.json")
    for key in ["segments", "translated_segments", "review_items", "review_state", "pre_review", "chapter_marks"]:
        assert key in manifest["files"]
        assert Path(manifest["files"][key]).exists()
    review_state = json.loads(Path(manifest["files"]["review_state"]).read_text(encoding="utf-8"))
    assert review_state["schema"] == "translation_review_state_v1"


def test_translate_pipeline_records_no_candidates_polish_outcome(tmp_path: Path, monkeypatch) -> None:
    _patch_intake_dependencies(monkeypatch)
    monkeypatch.setattr(polish_module, "scan_polish_candidates", lambda _text: [])
    stages: list[tuple[str, dict[str, object]]] = []
    settings = RunSettings(
        source_pdf=tmp_path / "english-book.epub",
        output_dir=tmp_path / "runs",
        target_language="zh-CN",
        source_language="en",
        translator="mock",
        max_chunk_chars=9000,
        profile_name="book",
        output_format="none",
    )

    pipeline_module.run_translation_pipeline(
        settings,
        lambda stage, data: stages.append((stage, data)),
    )

    assert ("polishing", {"stage_percent": 0}) in stages
    assert ("polishing", {"stage_percent": 100, "polish_outcome": "no_candidates"}) in stages


def test_translate_pipeline_does_not_hide_polish_failure(tmp_path: Path, monkeypatch) -> None:
    _patch_intake_dependencies(monkeypatch)
    monkeypatch.setattr(polish_module, "scan_polish_candidates", lambda _text: [object()])

    def fail_polish(**_kwargs):
        raise RuntimeError("polish provider unavailable")

    monkeypatch.setattr(polish_module, "run_polish", fail_polish)
    settings = RunSettings(
        source_pdf=tmp_path / "english-book.epub",
        output_dir=tmp_path / "runs",
        target_language="zh-CN",
        source_language="en",
        translator="mock",
        max_chunk_chars=9000,
        profile_name="book",
        output_format="none",
    )

    with pytest.raises(RuntimeError, match="polish provider unavailable"):
        pipeline_module.run_translation_pipeline(settings)


def test_translate_pipeline_writes_translation_job_files(tmp_path: Path, monkeypatch) -> None:
    _patch_intake_dependencies(monkeypatch)
    settings = RunSettings(
        source_pdf=tmp_path / "english-book.epub",
        output_dir=tmp_path / "runs",
        target_language="zh-CN",
        source_language="en",
        translator="mock",
        max_chunk_chars=9000,
        profile_name="book",
        output_format="none",
    )

    artifacts = pipeline_module.run_translation_pipeline(settings)
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))

    for key in ["translation_job", "translation_progress", "translation_events"]:
        assert key in manifest["files"]
        assert Path(manifest["files"][key]).exists()
    assert (artifacts.output_dir / "jobs" / "glossary-constraints.json").exists()
    assert not (settings.output_dir / "jobs" / "glossary-constraints.json").exists()

    progress = json.loads(Path(manifest["files"]["translation_progress"]).read_text(encoding="utf-8"))
    assert progress["status"] == "completed"
    assert progress["total_chunks"] >= 1
    assert progress["completed_chunks"] == progress["total_chunks"]


def test_translate_pipeline_ignore_cache_removes_stale_cache(tmp_path: Path, monkeypatch) -> None:
    _patch_intake_dependencies(monkeypatch)
    run_dir = tmp_path / "runs" / "english-book"
    stale_cache = run_dir / "translation-cache"
    stale_cache.mkdir(parents=True)
    (stale_cache / "chunk-000000-stale.md").write_text("bad stale cache\n", encoding="utf-8")

    settings = RunSettings(
        source_pdf=tmp_path / "english-book.epub",
        output_dir=tmp_path / "runs",
        target_language="zh-CN",
        source_language="en",
        translator="mock",
        max_chunk_chars=9000,
        profile_name="book",
        output_format="none",
        ignore_translation_cache=True,
    )

    artifacts = pipeline_module.run_translation_pipeline(settings)
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))

    cache_dir = Path(manifest["files"]["translation_cache_dir"])
    assert cache_dir.exists()
    assert not (cache_dir / "chunk-000000-stale.md").exists()
    assert manifest["translation"]["ignore_cache"] is True


def test_pipeline_manifest_includes_existing_glossary_files(tmp_path: Path, monkeypatch) -> None:
    _patch_intake_dependencies(monkeypatch)
    run_dir = tmp_path / "runs" / "english-book"
    glossary_dir = run_dir / "glossary"
    glossary_dir.mkdir(parents=True)
    (glossary_dir / "active.json").write_text('{"schema":"phase_a_glossary_v1","entries":[]}', encoding="utf-8")

    settings = RunSettings(
        source_pdf=tmp_path / "english-book.epub",
        output_dir=tmp_path / "runs",
        target_language="zh-CN",
        source_language="en",
        translator="mock",
        max_chunk_chars=9000,
        profile_name="book",
        output_format="none",
    )
    artifacts = pipeline_module.run_translation_pipeline(settings)
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))

    assert "glossary_active" in manifest["files"]
