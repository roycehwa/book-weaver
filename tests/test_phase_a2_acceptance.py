"""Phase A2 acceptance tests — maps to workstation design § Phase A2 acceptance block."""

from __future__ import annotations

import json
from pathlib import Path

from pdf_translator import pipeline as pipeline_module
from pdf_translator.config import RunSettings
from pdf_translator.glossary import (
    GLOSSARY_SCHEMA,
    apply_glossary_decision,
    extract_glossary_candidates,
    glossary_manifest_files,
    load_active_glossary,
    select_glossary_entries_for_text,
)
from pdf_translator.models import TranslationChunk
from pdf_translator.translate import build_translation_prompt, _chunk_input_hash
from tests.test_phase_a1_acceptance import _english_settings, _patch_book_intake


def test_a2_pipeline_creates_glossary_artifacts_during_translation(tmp_path: Path, monkeypatch) -> None:
    markdown = (
        "This historical biography recounts how the Yellow Emperor shaped ancient civilization. "
        "Yellow Emperor visited the Ritual Office.\n"
    )
    _patch_book_intake(monkeypatch, markdown=markdown, title="The Yellow Emperor")

    def fake_book_with_two_chapters(*args, **kwargs):
        return {
            "metadata": {"chapter_source": "test", "title": "The Yellow Emperor"},
            "render_policy": {"figures": "preserve"},
            "chapters": [
                {
                    "index": 1,
                    "chapter_id": "ch-001",
                    "title": "The Yellow Emperor",
                    "markdown": markdown,
                    "source_pages": [1],
                    "page_start": 1,
                    "page_end": 1,
                    "toc": True,
                },
                {
                    "index": 2,
                    "chapter_id": "ch-002",
                    "title": "Ritual Office",
                    "markdown": markdown,
                    "source_pages": [2],
                    "page_start": 2,
                    "page_end": 2,
                    "toc": True,
                },
            ],
        }

    monkeypatch.setattr(pipeline_module, "build_book_reconstruction", fake_book_with_two_chapters)
    artifacts = pipeline_module.run_translation_pipeline(_english_settings(tmp_path))
    run_dir = artifacts.output_dir

    for name in ("candidates.json", "active.json", "decisions.jsonl"):
        assert (run_dir / "glossary" / name).is_file(), name

    candidates = json.loads((run_dir / "glossary" / "candidates.json").read_text(encoding="utf-8"))
    assert candidates["schema"] == GLOSSARY_SCHEMA
    assert any(item["source"] == "Yellow Emperor" for item in candidates["candidates"])


def test_a2_extraction_is_deterministic_from_book_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    book = {
        "metadata": {"title": "The Yellow Emperor", "subtitle": "A Historical Biography"},
        "chapters": [
            {
                "chapter_id": "ch-001",
                "title": "The Yellow Emperor",
                "markdown": (
                    "Yellow Emperor met Huangdi in the Ritual Office. "
                    "This chapter recounts ancient civilization and dynasty history."
                ),
            }
        ],
    }
    (run_dir / "book.json").write_text(json.dumps(book), encoding="utf-8")

    first = extract_glossary_candidates(run_dir)
    second = extract_glossary_candidates(run_dir)

    assert [item["source"] for item in first["candidates"]] == [item["source"] for item in second["candidates"]]


def test_a2_manifest_lists_glossary_pointers_after_translation(tmp_path: Path, monkeypatch) -> None:
    _patch_book_intake(
        monkeypatch,
        markdown="# Ritual Office\n\nYellow Emperor opened the Ritual Office.\n",
    )
    artifacts = pipeline_module.run_translation_pipeline(_english_settings(tmp_path))
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))

    for key in ("glossary_active", "glossary_candidates", "glossary_decisions"):
        assert key in manifest["files"], key
        assert Path(manifest["files"][key]).is_file()

    pointers = glossary_manifest_files(artifacts.output_dir)
    assert {
        "glossary_active",
        "glossary_candidates",
        "glossary_decisions",
        "glossary_extraction_policy",
        "workflow",
    }.issubset(set(pointers))


def test_a2_prompt_injection_bounded_to_relevant_active_terms() -> None:
    entries = [
        {"source": "Yellow Emperor", "target": "黄帝", "status": "active", "evidence": ["ch-001"]},
        {"source": "Ritual Office", "target": "礼官署", "status": "active", "evidence": ["ch-002"]},
        {"source": "Unused Term", "target": "未用", "status": "active", "evidence": ["ch-099"]},
    ]
    selected = select_glossary_entries_for_text(
        "The Yellow Emperor speaks.",
        entries,
        chapter_id="ch-001",
        limit=20,
    )
    assert [entry["source"] for entry in selected] == ["Yellow Emperor"]

    prompt = build_translation_prompt(
        "The Yellow Emperor speaks.",
        source_language="en",
        target_language="zh-CN",
        glossary_entries=selected,
    )
    assert "Yellow Emperor => 黄帝" in prompt
    assert "Ritual Office" not in prompt
    assert "Unused Term" not in prompt


def test_a2_glossary_revision_changes_chunk_cache_identity() -> None:
    markdown = "The Yellow Emperor speaks."
    base = TranslationChunk(index=0, markdown=markdown, glossary_entries=None)
    with_glossary = TranslationChunk(
        index=0,
        markdown=markdown,
        glossary_entries=[{"source": "Yellow Emperor", "target": "黄帝", "status": "active"}],
    )
    assert _chunk_input_hash(base) != _chunk_input_hash(with_glossary)


def test_a2_user_decision_is_auditable_in_decisions_jsonl(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps({"chapters": [{"chapter_id": "ch-001", "markdown": "Yellow Emperor speaks."}]}),
        encoding="utf-8",
    )
    extract_glossary_candidates(run_dir)
    apply_glossary_decision(
        run_dir,
        source="Yellow Emperor",
        target="黄帝",
        term_type="cultural_term",
        status="active",
        decided_by="user",
    )

    active = load_active_glossary(run_dir)
    entry = next(item for item in active["entries"] if item["source"] == "Yellow Emperor")
    assert entry["target"] == "黄帝"
    assert entry["status"] == "active"
    assert entry["updated_by"] == "user"

    log_lines = (run_dir / "glossary" / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(log_lines) == 1
    decision = json.loads(log_lines[0])
    assert decision["event"] == "glossary_decision"
    assert decision["source"] == "Yellow Emperor"
    assert decision["target"] == "黄帝"
