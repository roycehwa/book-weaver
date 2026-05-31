import json
from pathlib import Path

import pytest

from pdf_translator.review import (
    apply_review_state,
    build_review_artifacts,
    create_review_state,
    review_project_from_run,
    translated_segments_to_chapters,
    write_versioned_outputs,
)


def sample_book() -> dict:
    return {
        "metadata": {"schema": "book_rebuild_v1", "chapter_source": "epub_spine"},
        "chapters": [
            {
                "index": 1,
                "chapter_id": "ch-001-intro",
                "title": "Introduction",
                "page_start": 1,
                "page_end": 2,
                "source_pages": [1, 2],
                "source_internal_path": "OEBPS/intro.xhtml",
                "markdown": "This is a long English paragraph about democratic institutions and public trust.\n\nA second paragraph explains how institutions fail when people cannot review outcomes.",
            },
            {
                "index": 2,
                "chapter_id": "ch-002-body",
                "title": "Body",
                "page_start": 3,
                "page_end": 4,
                "source_pages": [3, 4],
                "markdown": "This paragraph is completely missing from the translated output.",
            },
        ],
    }


def test_build_review_artifacts_creates_segments_and_initial_queue() -> None:
    artifacts = build_review_artifacts(
        source_path=Path("book.epub"),
        target_language="zh-CN",
        book=sample_book(),
        translated_chapters=[
            {
                "index": 1,
                "chapter_id": "ch-001-intro",
                "title": "Introduction",
                "page_start": 1,
                "page_end": 2,
                "source_pages": [1, 2],
                "source_internal_path": "OEBPS/intro.xhtml",
                "markdown": "这是第一段译文。\n\n这一段 mixed English phrase 仍然没有翻译。",
            }
        ],
    )

    assert [segment["segment_id"] for segment in artifacts["segments"]["segments"]] == [
        "ch-001-intro:s001",
        "ch-001-intro:s002",
        "ch-002-body:s001",
    ]
    assert artifacts["translated_segments"]["segments"][0]["segment_id"] == "ch-001-intro:s001"
    assert artifacts["translated_segments"]["segments"][1]["segment_id"] == "ch-001-intro:s002"
    assert artifacts["translated_segments"]["segments"][1]["source_location"]["source_internal_path"] == "OEBPS/intro.xhtml"

    issue_types = {item["issue_type"] for item in artifacts["review_items"]["items"]}
    assert "mixed_english" in issue_types
    assert "missing_translation" in issue_types
    assert artifacts["review_state"]["summary"]["total_items"] == len(artifacts["review_items"]["items"])


def test_apply_review_state_updates_segment_text_and_approval() -> None:
    artifacts = build_review_artifacts(
        source_path=Path("book.pdf"),
        target_language="zh-CN",
        book=sample_book(),
        translated_chapters=[
            {
                "index": 1,
                "chapter_id": "ch-001-intro",
                "title": "Introduction",
                "markdown": "旧译文。\n\n第二段旧译文。",
            },
            {
                "index": 2,
                "chapter_id": "ch-002-body",
                "title": "Body",
                "markdown": "第三段旧译文。",
            },
        ],
    )
    state = create_review_state(artifacts["review_items"])
    state["decisions"] = {
        "ch-001-intro:s002": {
            "status": "approved",
            "reviewer_comment": "补完整。",
            "approved_text": "第二段已经补全后的译文。",
        }
    }

    applied = apply_review_state(artifacts["translated_segments"], state)

    updated = next(segment for segment in applied if segment["segment_id"] == "ch-001-intro:s002")
    untouched = next(segment for segment in applied if segment["segment_id"] == "ch-001-intro:s001")
    assert updated["translated_text"] == "第二段已经补全后的译文。"
    assert updated["status"] == "approved"
    assert untouched["translated_text"] == "旧译文。"


def test_write_versioned_outputs_preserves_parent_and_manifest(tmp_path: Path) -> None:
    artifacts = build_review_artifacts(
        source_path=Path("book.epub"),
        target_language="zh-CN",
        book=sample_book(),
        translated_chapters=[
            {"index": 1, "chapter_id": "ch-001-intro", "title": "Introduction", "markdown": "第一段。\n\n第二段。"},
            {"index": 2, "chapter_id": "ch-002-body", "title": "Body", "markdown": "第三段。"},
        ],
    )
    version = write_versioned_outputs(
        run_dir=tmp_path,
        version_name="v2",
        target_language="zh-CN",
        translated_segments=artifacts["translated_segments"],
        parent_version="v1",
    )

    manifest = json.loads((tmp_path / "versions" / "v2" / "version-manifest.json").read_text(encoding="utf-8"))
    translated_md = (tmp_path / "versions" / "v2" / "translated.md").read_text(encoding="utf-8")

    assert manifest["parent_version"] == "v1"
    assert manifest["target_language"] == "zh-CN"
    assert version["translated_markdown_path"].endswith("translated.md")
    assert "# Introduction" in translated_md
    assert "第三段。" in translated_md


def test_write_versioned_outputs_rejects_unsafe_version_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="version"):
        write_versioned_outputs(
            run_dir=tmp_path,
            version_name="../outside",
            target_language="zh-CN",
            translated_segments=[],
        )


def test_translated_segments_to_chapters_preserves_chapter_metadata() -> None:
    artifacts = build_review_artifacts(
        source_path=Path("book.epub"),
        target_language="zh-CN",
        book=sample_book(),
        translated_chapters=[
            {"index": 1, "chapter_id": "ch-001-intro", "title": "Introduction", "markdown": "第一段。\n\n第二段。"},
            {"index": 2, "chapter_id": "ch-002-body", "title": "Body", "markdown": "第三段。"},
        ],
    )

    chapters = translated_segments_to_chapters(artifacts["translated_segments"])

    assert chapters[0]["chapter_id"] == "ch-001-intro"
    assert chapters[0]["title"] == "Introduction"
    assert chapters[0]["markdown"] == "第一段。\n\n第二段。\n"
    assert chapters[1]["chapter_id"] == "ch-002-body"


def test_review_project_from_run_loads_json_contract(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    artifacts = build_review_artifacts(
        source_path=Path("book.epub"),
        target_language="zh-CN",
        book=sample_book(),
        translated_chapters=[
            {"index": 1, "chapter_id": "ch-001-intro", "title": "Introduction", "markdown": "第一段。\n\n第二段。"},
            {"index": 2, "chapter_id": "ch-002-body", "title": "Body", "markdown": "第三段。"},
        ],
    )
    for name, payload in artifacts.items():
        (run_dir / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    project = review_project_from_run(run_dir)

    assert project["run_dir"] == str(run_dir)
    assert project["segments"][0]["source_text"].startswith("This is")
    assert project["review_state"]["schema"] == "translation_review_state_v1"
