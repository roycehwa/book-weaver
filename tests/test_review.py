import json
from pathlib import Path

import pytest

from pdf_translator.review import (
    apply_review_state,
    build_aligned_review_segments,
    build_review_artifacts,
    create_review_state,
    review_project_from_run,
    rewrite_review_requests,
    translated_segments_to_chapters,
    write_versioned_outputs,
    _is_valid_rewrite_candidate,
    _looks_like_model_refusal,
    _rewrite_prompt,
)
from pdf_translator.translate import _chunk_cache_path
from pdf_translator.models import TranslationChunk


class EchoRewriteTranslator:
    name = "echo-rewrite"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def translate_chunk(self, chunk, source_language: str | None, target_language: str) -> str:
        self.prompts.append(chunk.markdown)
        return "这是模型根据意见生成的候选译文。"


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
    assert artifacts["pre_review"]["flagged_segments"] == len(artifacts["review_items"]["items"])
    assert artifacts["review_state"]["workflow"]["human_review_mode"] == "issues_only"


def test_review_chapter_marks_split_outline(tmp_path: Path) -> None:
    import json

    from pdf_translator.review import add_review_chapter_mark, build_chapter_groups_from_marks, write_review_artifacts

    artifacts = build_review_artifacts(
        source_path=Path("book.epub"),
        target_language="zh-CN",
        book=sample_book(),
        translated_chapters=[
            {
                "index": 1,
                "chapter_id": "ch-001-intro",
                "title": "Introduction",
                "markdown": "译文一。\n\n译文二。",
            }
        ],
    )
    write_review_artifacts(tmp_path, artifacts)
    segments = artifacts["segments"]["segments"]
    second_id = segments[1]["segment_id"]
    add_review_chapter_mark(
        run_dir=tmp_path,
        segments=segments,
        segment_id=second_id,
        chapter_title="第二章",
    )
    marks = json.loads((tmp_path / "review_chapter_marks.json").read_text(encoding="utf-8"))["marks"]
    groups = build_chapter_groups_from_marks(segments, marks)
    assert len(groups) == 1
    assert groups[0]["display_title"] == "第二章"
    assert groups[0]["first_segment_index"] == 1


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


def test_apply_review_state_does_not_export_unapproved_model_candidate() -> None:
    translated_segments = [
        {"segment_id": "s1", "translated_text": "旧译文。", "status": "needs_review"},
    ]
    state = {
        "decisions": {
            "s1": {
                "status": "candidate",
                "action": "model_rewrite",
                "approved_text": "模型候选译文。",
            }
        }
    }

    applied = apply_review_state(translated_segments, state)

    assert applied[0]["translated_text"] == "旧译文。"


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


def test_rewrite_review_requests_writes_candidate_for_model_decision(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    artifacts = build_review_artifacts(
        source_path=Path("book.epub"),
        target_language="zh-CN",
        book=sample_book(),
        translated_chapters=[
            {"index": 1, "chapter_id": "ch-001-intro", "title": "Introduction", "markdown": "旧译文。\n\n第二段旧译文。"},
            {"index": 2, "chapter_id": "ch-002-body", "title": "Body", "markdown": "第三段旧译文。"},
        ],
    )
    artifacts["review_state"]["decisions"] = {
        "ch-001-intro:s002": {
            "status": "open",
            "action": "model_rewrite",
            "reviewer_comment": "补译遗漏内容。",
        }
    }
    for name, payload in artifacts.items():
        (run_dir / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    translator = EchoRewriteTranslator()
    result = rewrite_review_requests(
        run_dir=run_dir,
        translator=translator,
        source_language="en",
        target_language="zh-CN",
    )

    state = json.loads((run_dir / "review_state.json").read_text(encoding="utf-8"))
    decision = state["decisions"]["ch-001-intro:s002"]
    assert result["rewritten_count"] == 1
    assert decision["status"] == "candidate"
    assert decision["approved_text"] == "这是模型根据意见生成的候选译文。"
    assert "补译遗漏内容" in translator.prompts[0]


def test_rewrite_prompt_for_missing_translation_uses_source_text() -> None:
    prompt = _rewrite_prompt(
        "A second paragraph was omitted by the translation model and must be restored.",
        "旧译文。",
        "请完整翻译成中文。",
    )
    assert "SOURCE TEXT:" in prompt
    assert "CURRENT TRANSLATION:" in prompt
    assert "[missing translation]" not in prompt


def test_is_valid_rewrite_candidate_rejects_placeholder() -> None:
    source = "A second paragraph was omitted by the translation model and must be restored."
    assert not _is_valid_rewrite_candidate(source, "[missing translation]", "zh-CN")
    assert not _is_valid_rewrite_candidate(
        source,
        "您未在消息中提供需要翻译的 Markdown 内容。请提供您希望翻译成简体中文的 Markdown 文档。",
        "zh-CN",
    )
    assert _looks_like_model_refusal("您未在消息中提供需要翻译的 Markdown 内容。")
    assert _is_valid_rewrite_candidate(source, "第二段被翻译模型遗漏，现已补译恢复。", "zh-CN")


def test_rewrite_review_requests_skips_invalid_candidate_for_missing_translation(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    artifacts = build_review_artifacts(
        source_path=Path("book.epub"),
        target_language="zh-CN",
        book=sample_book(),
        translated_chapters=[
            {"index": 1, "chapter_id": "ch-001-intro", "title": "Introduction", "markdown": "第一段。\n\n"},
            {"index": 2, "chapter_id": "ch-002-body", "title": "Body", "markdown": ""},
        ],
    )
    artifacts["review_state"]["decisions"] = {
        "ch-001-intro:s002": {
            "status": "open",
            "action": "model_rewrite",
            "reviewer_comment": "请完整补译。",
        }
    }
    for name, payload in artifacts.items():
        (run_dir / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    class BadRewriteTranslator:
        name = "bad-rewrite"

        def translate_chunk(self, chunk, source_language: str | None, target_language: str) -> str:
            return "[missing translation]"

    result = rewrite_review_requests(
        run_dir=run_dir,
        translator=BadRewriteTranslator(),
        source_language="en",
        target_language="zh-CN",
    )

    state = json.loads((run_dir / "review_state.json").read_text(encoding="utf-8"))
    decision = state["decisions"]["ch-001-intro:s002"]
    assert result["rewritten_count"] == 0
    assert decision["status"] == "open"
    assert decision["rewrite_error"]


def test_build_aligned_review_segments_pairs_translation_cache(tmp_path: Path) -> None:
    from pdf_translator.chunking import split_markdown_into_chunks
    from pdf_translator.translate import _chapter_markdown_for_translation, _split_markdown_media_segments

    cache_dir = tmp_path / "translation-cache"
    cache_dir.mkdir()
    book = sample_book()
    chapter_markdown = _chapter_markdown_for_translation(book["chapters"][0])
    chunk_markdown = ""
    for segment_kind, segment_markdown in _split_markdown_media_segments(chapter_markdown):
        if segment_kind != "text":
            continue
        chunks = split_markdown_into_chunks(segment_markdown, 9000)
        assert chunks
        chunk_markdown = chunks[0].markdown
        break
    assert chunk_markdown
    cache_path = _chunk_cache_path(cache_dir, TranslationChunk(index=0, markdown=chunk_markdown))
    cache_path.write_text("这是与原文 chunk 对齐的中文译文。\n", encoding="utf-8")

    source_segments, translated_segments = build_aligned_review_segments(
        book,
        source_path=Path("book.epub"),
        target_language="zh-CN",
        cache_dir=cache_dir,
        max_chunk_chars=9000,
    )

    intro = next(segment for segment in source_segments if segment["chapter_id"] == "ch-001-intro")
    translated = next(segment for segment in translated_segments if segment["segment_id"] == intro["segment_id"])
    assert "This is a long English paragraph" in intro["source_text"]
    assert "中文译文" in translated["translated_text"]
    assert "This is a long English paragraph" not in translated["translated_text"]


def test_read_chunk_cache_falls_back_to_index_only_filename(tmp_path: Path) -> None:
    from pdf_translator.chunking import split_markdown_into_chunks
    from pdf_translator.translate import _chapter_markdown_for_translation, _read_chunk_cache, _split_markdown_media_segments

    cache_dir = tmp_path / "translation-cache"
    cache_dir.mkdir()
    book = sample_book()
    chapter_markdown = _chapter_markdown_for_translation(book["chapters"][0])
    chunk_markdown = ""
    for segment_kind, segment_markdown in _split_markdown_media_segments(chapter_markdown):
        if segment_kind != "text":
            continue
        chunks = split_markdown_into_chunks(segment_markdown, 9000)
        chunk_markdown = chunks[0].markdown
        break
    assert chunk_markdown
    # Simulate an older cache entry whose content hash no longer matches the current chunk markdown.
    legacy_path = cache_dir / "chunk-000000-deadbeefdeadbeef.md"
    legacy_path.write_text("旧缓存里的中文译文。\n", encoding="utf-8")

    translated = _read_chunk_cache(cache_dir, TranslationChunk(index=0, markdown=chunk_markdown))
    assert "旧缓存" in translated
