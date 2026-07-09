from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf_translator.config import RunSettings
from pdf_translator.glossary import apply_glossary_decision, extract_glossary_candidates, glossary_status, load_active_glossary
from pdf_translator.pipeline import run_intake_pipeline, run_translation_pipeline
from pdf_translator.workflow import (
    GlossaryNotReadyError,
    STAGE_AWAITING_GLOSSARY,
    STAGE_GLOSSARY_READY,
    begin_translation,
    glossary_ready_summary,
    load_workflow,
    mark_glossary_ready,
    require_glossary_ready,
)


def _patch_intake_dependencies(monkeypatch, *, markdown: str = "# Chapter\n\nShareholder Primacy matters.\n") -> None:
    from pdf_translator import pipeline as pipeline_module

    class FakeNormalized:
        raw_markdown = markdown
        reconstructed_markdown = markdown
        detected_language = "en"
        images_dir = None
        structured = {"pages": []}

    class FakePreflight:
        def as_dict(self) -> dict:
            return {"ingest_page_count": 1, "size_mb": 0.1}

    book = {
        "metadata": {"schema": "book_v1", "chapter_source": "test"},
        "chapters": [
            {
                "index": 1,
                "chapter_id": "ch-001",
                "title": "Shareholder Primacy",
                "markdown": markdown,
                "source_pages": [1],
                "translate": True,
            }
        ],
    }

    monkeypatch.setattr(
        pipeline_module,
        "ingest_pdf_guarded",
        lambda *args, **kwargs: (FakeNormalized(), FakePreflight()),
    )
    monkeypatch.setattr(
        pipeline_module,
        "build_document_profile",
        lambda *args, **kwargs: {"profile": "book"},
    )
    monkeypatch.setattr(pipeline_module, "build_book_reconstruction", lambda *args, **kwargs: book)


def test_extract_leaves_active_empty_until_user_applies(tmp_path: Path) -> None:
    run_dir = tmp_path / "book-run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "chapter_id": "ch-001",
                        "markdown": "Shareholder Primacy and corporate governance shaped policy.",
                    },
                    {
                        "chapter_id": "ch-002",
                        "markdown": "Shareholder Primacy continued across federal incorporation rules.",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    extract_glossary_candidates(run_dir)
    status = glossary_status(run_dir)
    assert status["candidate_count"] >= 1
    assert status["active_count"] == 0


def test_translate_from_run_blocked_without_glossary_ready(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "book-run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "mode": "intake",
                "source_pdf": str(tmp_path / "book.epub"),
                "source_language": "en",
                "preflight": {},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "book.json").write_text(
        json.dumps({"chapters": [{"chapter_id": "ch-001", "markdown": "Body", "translate": True}]}),
        encoding="utf-8",
    )
    (run_dir / "translation-input.md").write_text("# Chapter\n\nBody\n", encoding="utf-8")
    extract_glossary_candidates(run_dir)

    settings = RunSettings(
        source_pdf=tmp_path / "book.epub",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="mock",
        max_chunk_chars=9000,
        existing_run_dir=run_dir,
        require_glossary_ready=True,
    )
    with pytest.raises(GlossaryNotReadyError):
        run_translation_pipeline(settings)


def test_intake_writes_awaiting_glossary_workflow(tmp_path: Path, monkeypatch) -> None:
    _patch_intake_dependencies(monkeypatch)
    source = tmp_path / "book.epub"
    source.write_text("placeholder", encoding="utf-8")
    settings = RunSettings(
        source_pdf=source,
        output_dir=tmp_path / "runs",
        target_language="en",
        source_language="en",
        translator="none",
        max_chunk_chars=9000,
        output_format="none",
    )
    artifacts = run_intake_pipeline(settings)
    workflow = load_workflow(artifacts.output_dir)
    assert workflow is not None
    assert workflow["stage"] == STAGE_AWAITING_GLOSSARY
    assert (artifacts.output_dir / "glossary" / "candidates.json").exists()


def test_glossary_ready_then_translate_from_run(tmp_path: Path, monkeypatch) -> None:
    _patch_intake_dependencies(monkeypatch)
    source = tmp_path / "book.epub"
    source.write_text("placeholder", encoding="utf-8")
    intake_settings = RunSettings(
        source_pdf=source,
        output_dir=tmp_path / "runs",
        target_language="en",
        source_language="en",
        translator="none",
        max_chunk_chars=9000,
        output_format="none",
    )
    artifacts = run_intake_pipeline(intake_settings)
    run_dir = artifacts.output_dir
    apply_glossary_decision(
        run_dir,
        source="Shareholder Primacy",
        target="股东至上",
        term_type="name_or_key_term",
        status="active",
        decided_by="user",
    )
    mark_glossary_ready(run_dir)
    assert glossary_ready_summary(run_dir)["is_ready"]

    translate_settings = RunSettings(
        source_pdf=source,
        output_dir=tmp_path / "runs",
        target_language="zh-CN",
        source_language="en",
        translator="mock",
        max_chunk_chars=9000,
        output_format="none",
        existing_run_dir=run_dir,
        require_glossary_ready=True,
    )
    translated = run_translation_pipeline(translate_settings)
    manifest = json.loads(translated.manifest_path.read_text(encoding="utf-8"))
    assert manifest["mode"] == "translate"
    assert (run_dir / "pre_review.json").exists()
    workflow = load_workflow(run_dir)
    assert workflow["stage"] == "awaiting_human_review"


def test_mark_glossary_ready_requires_active_target(tmp_path: Path) -> None:
    run_dir = tmp_path / "book-run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(json.dumps({"chapters": []}), encoding="utf-8")
    extract_glossary_candidates(run_dir)
    with pytest.raises(GlossaryNotReadyError):
        mark_glossary_ready(run_dir)
    apply_glossary_decision(
        run_dir,
        source="Good Company",
        target="好公司",
        term_type="name_or_key_term",
        status="active",
        decided_by="user",
    )
    workflow = mark_glossary_ready(run_dir)
    assert workflow["stage"] == STAGE_GLOSSARY_READY
    require_glossary_ready(run_dir)


def test_begin_translation_does_not_auto_confirm_pending_terms(tmp_path: Path) -> None:
    run_dir = tmp_path / "book-run"
    glossary_dir = run_dir / "glossary"
    glossary_dir.mkdir(parents=True)
    (run_dir / "book.json").write_text(json.dumps({"chapters": []}), encoding="utf-8")
    (glossary_dir / "candidates.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "source": "Shareholder Primacy",
                        "type": "concept",
                        "target_suggestion": "股东至上",
                    },
                    {
                        "source": "Corporate Governance",
                        "type": "concept",
                        "target_suggestion": "公司治理",
                    },
                    {"source": "Rejected Term", "type": "concept", "target_suggestion": "应拒绝"},
                ]
            }
        ),
        encoding="utf-8",
    )
    apply_glossary_decision(
        run_dir,
        source="Shareholder Primacy",
        target="股东至上",
        term_type="concept",
        status="active",
        decided_by="user",
    )
    apply_glossary_decision(
        run_dir,
        source="Rejected Term",
        target=None,
        term_type="concept",
        status="rejected",
        decided_by="user",
    )
    mark_glossary_ready(run_dir)
    workflow = begin_translation(run_dir)
    active = load_active_glossary(run_dir)
    by_source = {entry["source"]: entry for entry in active["entries"]}
    assert workflow["stage"] == "translating"
    assert workflow["glossary_finalized_by_user"] is True
    assert "glossary_auto_confirmed" not in workflow
    assert by_source["Shareholder Primacy"]["status"] == "active"
    assert "Corporate Governance" not in by_source
    assert "Rejected Term" not in by_source
