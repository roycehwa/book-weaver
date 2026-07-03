import json
from pathlib import Path

import pytest

from pdf_translator.review import (
    apply_review_state,
    build_aligned_review_segments,
    build_review_artifacts,
    create_review_state,
    detect_review_items,
    review_project_from_run,
    rewrite_review_requests,
    merge_reviewed_chapters_with_resources,
    translated_segments_to_chapters,
    write_versioned_outputs,
    _is_valid_rewrite_candidate,
    restore_review_chapter_apparatus,
    _looks_like_model_refusal,
    _rewrite_prompt,
)


def test_build_review_artifacts_adds_system_owned_ocr_issue(tmp_path: Path) -> None:
    book = {
        "chapters": [],
        "semantic_content": {
            "ocr_quarantine": [
                {
                    "quarantine_id": "ocr-quarantine-a",
                    "source_page": 6,
                    "raw_text": "1:79. 2- 80 - - 3291/.",
                    "reason_codes": ["symbol_density", "fragmented_tokens"],
                    "score": 0.9,
                    "disposition": "suspect_ocr",
                    "evidence_asset": "assets/page-0006.png",
                }
            ]
        },
    }

    artifacts = build_review_artifacts(
        source_path=tmp_path / "source.pdf",
        target_language="zh-CN",
        book=book,
        translated_chapters=[],
    )

    item = artifacts["review_items"]["items"][0]
    assert item["issue_type"] == "suspect_ocr"
    assert item["responsibility"] == "system"
    assert item["source_location"]["page"] == 6
    assert item["evidence"]["asset"] == "assets/page-0006.png"
    assert "/Users/" not in json.dumps(item)
    system_segment = artifacts["segments"]["segments"][0]
    assert system_segment["segment_id"] == item["segment_id"]
    assert system_segment["source_text"].startswith("1:79")
    assert system_segment["translate"] is False
from pdf_translator.translate import _chunk_cache_path
from pdf_translator.models import TranslationChunk


class EchoRewriteTranslator:
    name = "echo-rewrite"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def translate_chunk(self, chunk, source_language: str | None, target_language: str) -> str:
        self.prompts.append(chunk.markdown)
        return "这是模型根据意见生成的候选译文。"


def test_restore_review_chapter_apparatus_appends_base_notes() -> None:
    reviewed = [
        {
            "index": 1,
            "title": "Chapter",
            "markdown": (
                "# 章节\n\n审阅后的正文。\n\n### 注释\n\n"
                "- [**99.**](OPS/c01.xhtml#R_c01-note-0099) Incomplete note."
            ),
        }
    ]
    base = [
        {
            "index": 1,
            "source_internal_path": "OPS/c01.xhtml",
            "markdown": (
                "# Chapter\n\nBase body.[1](OPS/c01.xhtml#c01-note-0001)\n\n"
                "### Notes\n\n"
                "- [**1.**](OPS/c01.xhtml#R_c01-note-0001) Preserved note."
            ),
        }
    ]

    restored = restore_review_chapter_apparatus(reviewed, base)

    assert "审阅后的正文" in restored[0]["markdown"]
    assert "Preserved note" in restored[0]["markdown"]
    assert "Incomplete note" not in restored[0]["markdown"]
    assert restored[0]["source_internal_path"] == "OPS/c01.xhtml"
    assert restored[0]["markdown"].count("###") == 1


def test_restore_review_chapter_apparatus_handles_notes_without_heading() -> None:
    reviewed = [
        {
            "index": 1,
            "title": "Chapter",
            "markdown": (
                "# 章节\n\n审阅后的正文。\n\n"
                "- [**1.**](OPS/c01.xhtml#R_c01-note-0001) Partial note."
            ),
        }
    ]
    base = [
        {
            "index": 1,
            "source_internal_path": "OPS/c01.xhtml",
            "markdown": (
                "# Chapter\n\nBase body.[1](OPS/c01.xhtml#c01-note-0001)\n\n"
                "- [**1.**](OPS/c01.xhtml#R_c01-note-0001) First note.\n\n"
                "- [**2.**](OPS/c01.xhtml#R_c01-note-0002) Second note."
            ),
        }
    ]

    restored = restore_review_chapter_apparatus(reviewed, base)

    assert "审阅后的正文" in restored[0]["markdown"]
    assert "Partial note" not in restored[0]["markdown"]
    assert "First note" in restored[0]["markdown"]
    assert "Second note" in restored[0]["markdown"]


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
                "markdown": "这是第一段译文。\n\n这一段 mixed english phrase remains 仍然没有翻译。",
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


def test_preserve_review_uses_content_integrity_checks_not_translation_checks() -> None:
    book = sample_book()
    artifacts = build_review_artifacts(
        source_path=Path("book.epub"),
        target_language="zh-CN",
        text_operation="preserve",
        book=book,
        translated_chapters=[
            {
                "index": chapter["index"],
                "chapter_id": chapter["chapter_id"],
                "title": chapter["title"],
                "markdown": chapter["markdown"],
            }
            for chapter in book["chapters"]
        ],
    )

    assert artifacts["review_items"]["text_operation"] == "preserve"
    assert artifacts["review_items"]["items"] == []
    assert artifacts["pre_review"]["method"] == "preserve_integrity_v1"
    assert artifacts["pre_review"]["flagged_segments"] == 0


def test_mixed_english_ignores_bibliographic_text_in_footnotes() -> None:
    items = detect_review_items(
        [
            {
                "segment_id": "ch-001:r001",
                "chapter_id": "ch-001",
                "chapter_index": 1,
                "chapter_title": "History",
                "block_index": 1,
                "source_text": (
                    "The translated body explains the argument in detail.\n\n---\n\n"
                    "24 William Byrd, \"The Anshan Iron and Steel Company,\" 327."
                ),
                "translate": True,
            }
        ],
        [
            {
                "segment_id": "ch-001:r001",
                "translated_text": (
                    "正文已经完整翻译并详细说明了论点。\n\n---\n\n"
                    "24 参见 William Byrd, \"The Anshan Iron and Steel Company,\" "
                    "Cambridge University Press, 327."
                ),
            }
        ],
        target_language="zh-CN",
    )

    assert items == []


def test_mixed_english_flags_untranslated_explanatory_footnote() -> None:
    items = detect_review_items(
        [
            {
                "segment_id": "ch-001:r001",
                "source_text": "正文。\n\n---\n\n24 This footnote explains the historical context in full.",
                "translate": True,
            }
        ],
        [
            {
                "segment_id": "ch-001:r001",
                "translated_text": "正文已翻译。\n\n---\n\n24 This footnote explains the historical context in full.",
            }
        ],
        target_language="zh-CN",
    )

    assert items[0]["issue_type"] == "mixed_english"


def test_review_skips_map_frontmatter_with_figure_captions() -> None:
    from pdf_translator.review import detect_review_items

    map_segment = (
        "# 地图\n\n"
        "## 地图\n\n"
        "![Figure 241.1: Map 1](/tmp/book-images/figure-p0241-01.png)\n\n"
        "> 地图 1. 1515 年米列（Milliet）与当泽尔（Donzel）的债务人\n\n"
        "![Figure 241.1: Map 1](/tmp/book-images/figure-p0241-01.png)\n\n"
        "> 地图 1. 1515年米列（Milliet）和栋泽尔（Donzel）的债务人"
    )
    items = detect_review_items(
        [
            {
                "segment_id": "ch-maps:c001",
                "source_text": map_segment,
                "translate": True,
            }
        ],
        [
            {
                "segment_id": "ch-maps:c001",
                "translated_text": map_segment,
            }
        ],
        target_language="zh-CN",
        glossary_entries=[
            {"source": "Geneva and Savoy", "target": "日内瓦与萨瓦", "status": "active"},
        ],
    )

    assert items == []


def test_glossary_drift_is_owned_by_system_repair() -> None:
    items = detect_review_items(
        [
            {
                "segment_id": "ch-001:r001",
                "chapter_id": "ch-001",
                "chapter_index": 1,
                "chapter_title": "History",
                "block_index": 1,
                "source_text": "The Soviet Union shaped policy.",
                "translate": True,
                "glossary_entries": [
                    {"source": "Soviet Union", "target": "苏联", "status": "active"}
                ],
            }
        ],
        [
            {
                "segment_id": "ch-001:r001",
                "translated_text": "苏维埃联盟影响了政策。",
            }
        ],
        target_language="zh-CN",
        glossary_entries=[
            {"source": "Soviet Union", "target": "苏联", "status": "active"},
            {"source": "Shareholder Primacy", "target": "股东至上", "status": "active"},
        ],
    )

    assert items[0]["issue_type"] == "glossary_drift"
    assert items[0]["responsibility"] == "system"
    assert items[0]["suggested_action"] == "auto_retranslate"


def test_glossary_drift_uses_segment_snapshot_terms_only() -> None:
    items = detect_review_items(
        [
            {
                "segment_id": "ch-001:r001",
                "source_text": "The Soviet Union shaped policy.",
                "translate": True,
                "glossary_entries": [
                    {"source": "Soviet Union", "target": "苏联", "status": "active"},
                ],
            }
        ],
        [
            {
                "segment_id": "ch-001:r001",
                "translated_text": "苏维埃联盟影响了政策。",
            }
        ],
        target_language="zh-CN",
        glossary_entries=[
            {"source": "Soviet Union", "target": "苏联", "status": "active"},
            {"source": "Shareholder Primacy", "target": "股东至上", "status": "active"},
        ],
    )

    assert len(items) == 1
    assert items[0]["issue_type"] == "glossary_drift"
    assert items[0]["evidence"]["missing_glossary_terms"] == [
        {"source": "Soviet Union", "target": "苏联"}
    ]


def test_review_does_not_infer_glossary_drift_without_translation_snapshot(
    tmp_path: Path,
) -> None:
    glossary_dir = tmp_path / "glossary"
    glossary_dir.mkdir()
    (glossary_dir / "active.json").write_text(
        json.dumps(
            {
                "schema": "phase_a_glossary_v1",
                "entries": [
                    {"source": "Soviet Union", "target": "苏联", "status": "active"}
                ],
            }
        ),
        encoding="utf-8",
    )
    book = {
        "chapters": [
            {
                "index": 1,
                "chapter_id": "ch-001",
                "title": "History",
                "markdown": "The Soviet Union shaped policy.",
            }
        ]
    }

    artifacts = build_review_artifacts(
        source_path=Path("book.pdf"),
        target_language="zh-CN",
        book=book,
        translated_chapters=[
            {
                "index": 1,
                "chapter_id": "ch-001",
                "title": "History",
                "markdown": "苏维埃联盟影响了政策。",
            }
        ],
        run_dir=tmp_path,
    )

    assert not any(
        item["issue_type"] == "glossary_drift"
        for item in artifacts["review_items"]["items"]
    )


def test_translation_review_skips_preserved_resource_chapters() -> None:
    book = {
        "chapters": [
            {
                "index": 1,
                "chapter_id": "ch-001-cover",
                "title": "Cover",
                "markdown": "![Cover](cover.png)",
                "translate": False,
                "preserve_original": True,
            },
            {
                "index": 2,
                "chapter_id": "ch-002-body",
                "title": "Body",
                "markdown": "English body text that needs translation.",
                "translate": True,
            },
        ]
    }

    artifacts = build_review_artifacts(
        source_path=Path("book.epub"),
        target_language="zh-CN",
        book=book,
        translated_chapters=[
            {
                "index": 1,
                "chapter_id": "ch-001-cover",
                "title": "Cover",
                "markdown": "![Cover](cover.png)",
            },
            {
                "index": 2,
                "chapter_id": "ch-002-body",
                "title": "Body",
                "markdown": "这是已经翻译完成的中文正文。",
            },
        ],
    )

    assert artifacts["review_items"]["items"] == []


def test_review_skips_map_resource_ocr_even_when_it_contains_glossary_terms() -> None:
    items = detect_review_items(
        [
            {
                "segment_id": "ch-map:r001",
                "chapter_id": "ch-map",
                "chapter_title": "Maps",
                "source_text": (
                    "Common Lordships of Bern and Fribourg\n\n"
                    "r\n\ne\n\nd\n\n![Map](map.png)"
                ),
                "translate": True,
                "glossary_entries": [
                    {
                        "source": "Common Lordships",
                        "target": "共同领主辖地",
                        "status": "active",
                    }
                ],
            }
        ],
        [
            {
                "segment_id": "ch-map:r001",
                "translated_text": "伯尔尼和弗里堡的领地\n\nr\n\ne\n\nd",
            }
        ],
        target_language="zh-CN",
    )

    assert items == []


def test_review_items_ignore_media_and_image_only_segments() -> None:
    items = detect_review_items(
        [
            {
                "segment_id": "ch-001:r001",
                "chapter_id": "ch-001",
                "chapter_index": 1,
                "chapter_title": "Figures",
                "block_index": 1,
                "source_text": "![Figure 36.1: Figure on page 36](book-images/figure-p0036-01.png)",
                "translate": True,
            }
        ],
        [
            {
                "segment_id": "ch-001:r001",
                "translated_text": "![Figure 36.1: Figure on page 36](book-images/figure-p0036-01.png)",
            }
        ],
        target_language="zh-CN",
        text_operation="translate",
    )

    assert items == []


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


def test_write_versioned_outputs_records_approval_status(tmp_path: Path) -> None:
    version = write_versioned_outputs(
        run_dir=tmp_path,
        version_name="final",
        target_language="zh-CN",
        translated_segments=[],
        approval_status="approved",
    )

    manifest = json.loads(Path(version["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["approval_status"] == "approved"


def test_write_versioned_outputs_accepts_complete_delivery_markdown(tmp_path: Path) -> None:
    version = write_versioned_outputs(
        run_dir=tmp_path,
        version_name="complete",
        target_language="zh-CN",
        translated_segments=[],
        translated_markdown_override="# Reviewed\n\n译文\n\n![Original page 2](p2.png)\n",
    )

    translated = Path(version["translated_markdown_path"]).read_text(encoding="utf-8")
    assert "Original page 2" in translated


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


def test_merge_reviewed_chapters_adds_only_uncovered_resources() -> None:
    reviewed = [
        {
            "chapter_id": "body",
            "page_start": 1,
            "source_pages": [1],
            "markdown": "译文",
        }
    ]
    book = {
        "chapters": [
            {
                "chapter_id": "body",
                "page_start": 1,
                "source_pages": [1],
                "markdown": "Source",
            },
            {
                "chapter_id": "index",
                "page_start": 2,
                "source_pages": [2],
                "markdown": "![Original page 2](/tmp/p2.png)",
                "resource_only": True,
                "preserve_original": True,
            },
        ]
    }

    merged = merge_reviewed_chapters_with_resources(reviewed, book)

    assert [chapter["source_pages"] for chapter in merged] == [[1], [2]]
    assert merged[1]["preserve_original"] is True


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


def test_rewrite_review_requests_injects_and_validates_active_glossary(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    artifacts = build_review_artifacts(
        source_path=Path("book.epub"),
        target_language="zh-CN",
        book={
            "chapters": [
                {
                    "index": 1,
                    "chapter_id": "ch-001",
                    "title": "History",
                    "markdown": "The Soviet Union shaped policy.",
                }
            ]
        },
        translated_chapters=[
            {
                "index": 1,
                "chapter_id": "ch-001",
                "title": "History",
                "markdown": "苏维埃联盟影响了政策。",
            }
        ],
    )
    artifacts["review_state"]["decisions"] = {
        "ch-001:s001": {
            "status": "open",
            "action": "model_rewrite",
            "reviewer_comment": "请重新翻译。",
        }
    }
    for name, payload in artifacts.items():
        (run_dir / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
    glossary_dir = run_dir / "glossary"
    glossary_dir.mkdir()
    (glossary_dir / "active.json").write_text(
        json.dumps(
            {
                "schema": "phase_a_glossary_v1",
                "entries": [
                    {"source": "Soviet Union", "target": "苏联", "status": "active"}
                ],
            }
        ),
        encoding="utf-8",
    )

    class GlossaryAwareTranslator:
        name = "glossary-aware"

        def __init__(self) -> None:
            self.prompts: list[str] = []

        def translate_chunk(self, chunk, source_language: str | None, target_language: str) -> str:
            self.prompts.append(chunk.markdown)
            if len(self.prompts) == 1:
                return "苏维埃联盟影响了政策。"
            return "苏联影响了政策。"

    translator = GlossaryAwareTranslator()
    result = rewrite_review_requests(
        run_dir=run_dir,
        translator=translator,
        source_language="en",
        target_language="zh-CN",
    )

    assert result["rewritten_count"] == 1
    assert len(translator.prompts) == 2
    assert "Soviet Union => 苏联" in translator.prompts[0]
    state = json.loads((run_dir / "review_state.json").read_text(encoding="utf-8"))
    assert state["decisions"]["ch-001:s001"]["approved_text"] == "苏联影响了政策。"


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


def test_build_aligned_review_segments_uses_persisted_glossary_constraints_for_cache_key(
    tmp_path: Path,
) -> None:
    from pdf_translator.chunking import split_markdown_into_chunks
    from pdf_translator.translate import _chapter_markdown_for_translation, _split_markdown_media_segments

    cache_dir = tmp_path / "translation-cache"
    cache_dir.mkdir()
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    book = sample_book()
    chapter_markdown = _chapter_markdown_for_translation(book["chapters"][0])
    text = next(
        segment_markdown
        for segment_kind, segment_markdown in _split_markdown_media_segments(chapter_markdown)
        if segment_kind == "text"
    )
    chunk = split_markdown_into_chunks(text, 9000)[0]
    glossary_entries = [{"source": "steelworks", "target": "钢铁厂"}]
    constrained_chunk = TranslationChunk(
        index=0,
        markdown=chunk.markdown,
        glossary_entries=glossary_entries,
    )
    _chunk_cache_path(cache_dir, constrained_chunk).write_text(
        "这是带术语约束缓存中的完整中文译文。\n",
        encoding="utf-8",
    )
    (cache_dir / "chunk-000000-stale-cache.md").write_text(
        "不应读取的旧缓存。\n",
        encoding="utf-8",
    )
    (jobs_dir / "glossary-constraints.json").write_text(
        json.dumps(
            {
                "schema": "translation_glossary_constraints_v1",
                "chunks": [{"chunk_index": 0, "terms": glossary_entries}],
            }
        ),
        encoding="utf-8",
    )

    _source_segments, translated_segments = build_aligned_review_segments(
        book,
        source_path=Path("book.pdf"),
        target_language="zh-CN",
        cache_dir=cache_dir,
        max_chunk_chars=9000,
    )

    assert any(
        "完整中文译文" in segment["translated_text"]
        for segment in translated_segments
    )


def test_aligned_review_segments_keep_media_and_part_pairs(tmp_path: Path) -> None:
    cache_dir = tmp_path / "translation-cache"
    cache_dir.mkdir()
    book = {
        "chapters": [
            {
                "index": 1,
                "chapter_id": "ch-001",
                "title": "Figures",
                "markdown": (
                    "![Figure 1](figure.png)\n\n"
                    "> Figure 1 Industrial output.\n\n"
                    "Body paragraph."
                ),
            }
        ]
    }
    from pdf_translator.chunking import split_markdown_into_chunks
    text = "> Figure 1 Industrial output.\n\nBody paragraph."
    chunk = split_markdown_into_chunks(text, 9000)[0]
    _chunk_cache_path(cache_dir, TranslationChunk(index=0, markdown=chunk.markdown)).write_text(
        "> 图1 工业产出。\n\n正文段落。\n",
        encoding="utf-8",
    )

    source_segments, _ = build_aligned_review_segments(
        book,
        source_path=Path("book.pdf"),
        target_language="zh-CN",
        cache_dir=cache_dir,
        max_chunk_chars=9000,
    )

    segment = source_segments[0]
    parts = segment["aligned_parts"]
    media_index = next(
        index for index, part in enumerate(parts)
        if part["source"].startswith("![Figure 1]")
    )
    assert parts[media_index]["translation"].startswith("![Figure 1]")
    assert "Figure 1 Industrial output" in parts[media_index + 1]["source"]


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
