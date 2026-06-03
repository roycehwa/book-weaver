from __future__ import annotations

from pathlib import Path
import json
import shutil

from pdf_translator.knowledge import (
    apply_user_review,
    build_knowledge_extraction,
    build_knowledge_package,
    build_knowledge_plan,
    build_metadata_prior,
    build_reader_brief,
    build_suitability_report,
    emit_mindmap_mermaid_from_book,
    emit_wiki_outline_from_book,
    ingest_reader_feedback,
    parse_user_review_answers,
)


def test_emit_wiki_outline_writes_index_and_chapter_stubs(tmp_path: Path) -> None:
    book = {
        "chapters": [
            {"index": 1, "title": "Alpha", "markdown": "x"},
            {"index": 2, "title": "Beta", "markdown": "y"},
        ]
    }
    out = tmp_path / "wiki"
    emit_wiki_outline_from_book(book, out)
    assert (out / "index.md").is_file()
    assert (out / "001-alpha.md").is_file()
    assert "Alpha" in (out / "001-alpha.md").read_text(encoding="utf-8")


def test_emit_mindmap_mermaid_writes_fenced_block(tmp_path: Path) -> None:
    book = {"chapters": [{"index": 1, "title": "Only", "markdown": "m"}]}
    p = tmp_path / "mm.md"
    emit_mindmap_mermaid_from_book(book, p)
    text = p.read_text(encoding="utf-8")
    assert "```mermaid" in text
    assert "flowchart TB" in text
    assert "Only" in text


def test_build_knowledge_package_writes_deterministic_core_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    book = {
        "chapters": [
            {
                "index": 1,
                "chapter_id": "ch-001-alpha",
                "title": "Alpha",
                "page_start": 3,
                "page_end": 4,
                "source_pages": [3, 4],
                "markdown": "# Alpha\n\nFirst paragraph.\n\n![Fig](images/f1.png)\n",
            },
            {
                "index": 2,
                "title": "Beta",
                "page_start": 5,
                "page_end": 5,
                "source_pages": [5],
                "markdown": "# Beta\n\n- Item\n",
            },
        ],
        "assets": [{"kind": "figure", "path": "images/f1.png", "page_no": 3}],
    }
    (run_dir / "book.json").write_text(json.dumps(book), encoding="utf-8")
    (run_dir / "translated.md").write_text("# 阿尔法\n\n第一段。\n\n![Fig](images/f1.png)\n\n# 贝塔\n\n- 条目\n", encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps({"source_pdf": "/books/a.pdf", "source_language": "en", "target_language": "zh-CN"}),
        encoding="utf-8",
    )

    paths = build_knowledge_package(run_dir)

    assert paths["manifest"].is_file()
    assert paths["bilingual_input_markdown"].is_file()
    chapters = json.loads(paths["chapters"].read_text(encoding="utf-8"))
    units = json.loads(paths["semantic_units"].read_text(encoding="utf-8"))
    bilingual = json.loads(paths["bilingual_input"].read_text(encoding="utf-8"))
    assets = json.loads(paths["assets"].read_text(encoding="utf-8"))
    source_map = json.loads(paths["source_map"].read_text(encoding="utf-8"))
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))

    assert chapters[0]["chapter_id"] == "ch-001-alpha"
    assert chapters[1]["chapter_id"].startswith("ch-002-beta")
    assert [unit["kind"] for unit in units] == ["heading", "paragraph", "image", "heading", "list"]
    assert units[1]["text_translated"] == "第一段。"
    assert units[1]["language_mode"] == "bilingual"
    assert units[1]["translation_alignment"] == "block_index"
    assert units[1]["text_original_hash"]
    assert units[1]["text_translated_hash"]
    assert units[1]["source_pages"] == [3, 4]
    assert bilingual["mode"] == "bilingual"
    assert bilingual["alignment"]["unit_levels"]["block_index"] == 2
    assert bilingual["chapters"][0]["translated_markdown"].startswith("# 阿尔法")
    assert assets == [{"kind": "figure", "path": "images/f1.png", "page_no": 3}]
    assert source_map["semantic_units"]["ch-001-alpha-u0002"]["page_start"] == 3
    assert manifest["counts"] == {"chapters": 2, "semantic_units": 5, "assets": 1}
    assert manifest["language"]["mode"] == "bilingual"
    assert "bilingual_input" in manifest["files"]
    bilingual_md = paths["bilingual_input_markdown"].read_text(encoding="utf-8")
    assert "Bilingual Knowledge Input" in bilingual_md
    assert "Content Samples" in bilingual_md
    assert "First paragraph." in bilingual_md


def test_build_knowledge_package_does_not_force_unsafe_translation_alignment(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {"index": 1, "title": "A", "markdown": "# A\n\nOne.\n\nTwo.\n"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "translated.md").write_text("# 甲\n\n只有一段。\n", encoding="utf-8")

    paths = build_knowledge_package(run_dir)
    units = json.loads(paths["semantic_units"].read_text(encoding="utf-8"))

    bilingual = json.loads(paths["bilingual_input"].read_text(encoding="utf-8"))

    assert all(unit["text_translated"] is None for unit in units)
    assert all(unit["translation_alignment"] == "chapter_only" for unit in units)
    assert bilingual["chapters"][0]["translated_markdown"] == "# 甲\n\n只有一段。"
    assert bilingual["chapters"][0]["unit_alignment"] == "chapter_only"


def test_build_knowledge_package_marks_original_only_for_chinese_or_untranslated_input(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {"index": 1, "chapter_id": "ch-001-a", "title": "A", "markdown": "# A\n\n原文。\n"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "book.md").write_text("# A\n\n原文。\n", encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps({"source_language": "zh-CN", "translation": {"mode": "not_requested"}}),
        encoding="utf-8",
    )

    paths = build_knowledge_package(run_dir)
    units = json.loads(paths["semantic_units"].read_text(encoding="utf-8"))
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    bilingual = json.loads(paths["bilingual_input"].read_text(encoding="utf-8"))

    assert manifest["language"]["mode"] == "monolingual_original"
    assert units[1]["translation_alignment"] == "original_only"
    assert units[1]["text_translated"] is None
    assert bilingual["mode"] == "monolingual_original"
    assert bilingual["chapters"][0]["translated_markdown"] is None


def test_build_suitability_report_detects_argumentative_book(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "chapter_id": "ch-001-introduction",
                        "title": "Introduction",
                        "markdown": (
                            "# Introduction\n\n"
                            "This book argues that political theory requires a new concept of evidence.\n\n"
                            "The argument contrasts earlier theory with new interpretation.\n\n"
                            "However, the author critiques this framework because it hides the central claim.\n\n"
                            "Therefore the chapter develops a theory of institutional evidence.\n"
                        ),
                    }
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )

    paths = build_suitability_report(run_dir)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    md = paths["markdown"].read_text(encoding="utf-8")

    assert report["profile"] == "argumentative"
    assert report["network_suitability"] in {"high", "medium"}
    assert "claim" in report["extractable_objects"]
    assert report["chapters"][0]["action"] == "extract"
    assert "Knowledge Suitability Report" in md
    assert "argument_map" in md


def test_build_suitability_report_detects_practical_book(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "title": "A Practical Strategy Guide",
                        "markdown": (
                            "# A Practical Strategy Guide\n\n"
                            "This guide provides a method and a checklist for teams.\n\n"
                            "- Step one: define the problem.\n\n"
                            "- Step two: choose the tool.\n\n"
                            "A case study illustrates when the principle applies.\n"
                        ),
                    }
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )

    paths = build_suitability_report(run_dir)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))

    assert report["profile"] == "practical"
    assert "playbook" in report["recommended_outputs"]
    assert "action" in report["extractable_objects"]


def test_build_suitability_report_flags_technical_visual_risk(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "title": "Formula and Data Appendix",
                        "markdown": (
                            "# Formula and Data Appendix\n\n"
                            "The theorem follows from the equation and proof.\n\n"
                            "![Figure](fig1.png)\n\n"
                            "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
                            "![Figure](fig2.png)\n\n"
                            "| C | D |\n| --- | --- |\n| 3 | 4 |\n"
                        ),
                    }
                ],
                "assets": [{"kind": "figure", "path": "fig1.png"}],
            }
        ),
        encoding="utf-8",
    )

    paths = build_suitability_report(run_dir)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))

    assert report["profile"] == "technical_lite"
    assert any(risk["risk"] == "visual_or_table_heavy" for risk in report["risks"])
    assert "automatic_formula_semantics" in report["do_not_extract"]


def test_build_knowledge_plan_selects_argument_network(tmp_path: Path) -> None:
    run_dir = tmp_path / "True Conservatism"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {"index": 1, "title": "Copyright Page", "markdown": "# Copyright Page\n\nCopyright.\n"},
                    {
                        "index": 2,
                        "title": "1. Our Prejudices",
                        "markdown": (
                            "# 1. Our Prejudices\n\n"
                            "This book argues that reason, theory, and humanity require a concept of judgment.\n\n"
                            "The claim is developed through critique and evidence.\n\n"
                            "However, the author contrasts this framework with liberal assumptions.\n\n"
                            "Therefore the argument is about political philosophy and conservative thought.\n"
                        ),
                    },
                    {
                        "index": 3,
                        "title": "2. The Sufficiency of Reason",
                        "markdown": (
                            "# 2. The Sufficiency of Reason\n\n"
                            "The chapter develops a major claim about reason.\n\n"
                            "It provides evidence and responds to objections.\n\n"
                            "The concept of excellence is revised.\n\n"
                            "The argument contrasts rival theories.\n"
                        ),
                    },
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )

    paths = build_knowledge_plan(run_dir)
    plan = json.loads(paths["plan"].read_text(encoding="utf-8"))
    md = paths["markdown"].read_text(encoding="utf-8")

    assert plan["final_plan"]["primary_network_model"] == "argument_network"
    assert plan["final_plan"]["chapter_roles"][0]["role"] == "skip"
    assert plan["final_plan"]["chapter_roles"][1]["role"] == "extract"
    assert "claims" in md


def test_build_knowledge_plan_skips_chinese_navigation_chapters(tmp_path: Path) -> None:
    run_dir = tmp_path / "逻辑哲学论"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {"index": 1, "title": "书 名 页", "markdown": "# 书 名 页\n\n逻辑哲学论。\n"},
                    {"index": 2, "title": "版 权 页", "markdown": "# 版 权 页\n\n版权信息。\n"},
                    {"index": 3, "title": "目 录", "markdown": "# 目 录\n\n正文 1。\n"},
                    {
                        "index": 4,
                        "title": "逻辑哲学论",
                        "markdown": "# 逻辑哲学论\n\n谨以此书纪念我的朋友。\n\n格言：凡能说的，都可说清。\n",
                    },
                    {
                        "index": 5,
                        "title": "正文",
                        "markdown": (
                            "# 正文\n\n"
                            "1 世界是一切发生的事情。\n\n"
                            "1.1 世界是事实的总和。\n\n"
                            "2 发生的事情即事实的存在。\n\n"
                            "2.01 事态是对象的结合。\n"
                        ),
                    },
                    {"index": 6, "title": "索 引", "markdown": "# 索 引\n\n世界，1。\n"},
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )

    paths = build_knowledge_plan(run_dir)
    plan = json.loads(paths["plan"].read_text(encoding="utf-8"))
    roles = {chapter["title"]: chapter["role"] for chapter in plan["final_plan"]["chapter_roles"]}

    assert roles["书 名 页"] == "skip"
    assert roles["版 权 页"] == "skip"
    assert roles["目 录"] == "skip"
    assert roles["逻辑哲学论"] == "skip"
    assert roles["正文"] == "extract"
    assert roles["索 引"] == "skip"


def test_build_knowledge_plan_selects_playbook_network_and_preserves_use_case_appendix(tmp_path: Path) -> None:
    run_dir = tmp_path / "AI Use Cases for Diplomats"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "title": "Chapter 1 AI and Consular Affairs: Optimizing Visa Services",
                        "markdown": (
                            "# Chapter 1 AI and Consular Affairs: Optimizing Visa Services\n\n"
                            "This guide provides a practical AI use case for diplomats.\n\n"
                            "- Step one: define the use case.\n\n"
                            "- Step two: choose the tool.\n\n"
                            "A case study explains the management practice.\n"
                        ),
                    },
                    {
                        "index": 2,
                        "title": "Appendix A: 100 AI Use Cases for Diplomats",
                        "markdown": (
                            "# Appendix A: 100 AI Use Cases for Diplomats\n\n"
                            "Use case 1 applies AI to consular operations.\n\n"
                            "Use case 2 applies AI to economic affairs.\n\n"
                            "Use case 3 applies AI to public affairs.\n"
                        ),
                    },
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )

    paths = build_knowledge_plan(run_dir)
    plan = json.loads(paths["plan"].read_text(encoding="utf-8"))
    roles = {chapter["title"]: chapter["role"] for chapter in plan["final_plan"]["chapter_roles"]}

    assert plan["final_plan"]["primary_network_model"] == "playbook_network"
    assert roles["Appendix A: 100 AI Use Cases for Diplomats"] == "preserve"
    assert "action" in plan["final_plan"]["recommended_extractors"]


def test_build_knowledge_plan_selects_event_timeline_network(tmp_path: Path) -> None:
    run_dir = tmp_path / "In Search of National Ancestors"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "title": "2 Locating Religious Revivals in China",
                        "markdown": (
                            "# 2 Locating Religious Revivals in China\n\n"
                            "The historical revival of ceremonies changed the place of heritage.\n\n"
                            "The event involved local actors and a ceremony around ancestors.\n\n"
                            "This chapter explains time, place, and cultural identity.\n\n"
                            "The historical narrative links the revival to later practices.\n"
                        ),
                    },
                    {
                        "index": 2,
                        "title": "3 The Search for a Common Ancestor",
                        "markdown": (
                            "# 3 The Search for a Common Ancestor\n\n"
                            "The chapter follows a historical search for ancestor ceremonies.\n\n"
                            "Actors used heritage branding in different places.\n\n"
                            "The event sequence creates a timeline of cultural revival.\n\n"
                            "The interpretation connects national identity and place.\n"
                        ),
                    },
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )

    paths = build_knowledge_plan(run_dir)
    plan = json.loads(paths["plan"].read_text(encoding="utf-8"))

    assert plan["final_plan"]["primary_network_model"] == "event_timeline_network"
    assert "event" in plan["final_plan"]["recommended_extractors"]


def test_build_metadata_prior_uses_external_records_as_weak_prior(tmp_path: Path) -> None:
    run_dir = tmp_path / "AI Use Cases for Diplomats -- Donald Kilburg"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"source_pdf": "/books/AI Use Cases for Diplomats -- Donald Kilburg.epub"}),
        encoding="utf-8",
    )

    def fake_fetcher(query: str, timeout_seconds: float) -> list[dict]:
        assert "AI Use Cases for Diplomats" in query
        return [
            {
                "source": "test",
                "title": "AI Use Cases for Diplomats",
                "authors": ["Donald Kilburg"],
                "publisher": "Taylor & Francis",
                "description": "A practical guide with real-world examples, use cases, tools, and strategy.",
                "categories": ["Artificial Intelligence", "Diplomacy", "Technology"],
                "url": "https://example.test/book",
            }
        ]

    paths = build_metadata_prior(run_dir, fetcher=fake_fetcher)
    prior = json.loads(paths["prior"].read_text(encoding="utf-8"))
    md = paths["markdown"].read_text(encoding="utf-8")

    assert prior["primary_network_model"] == "playbook_network"
    assert prior["network_scores"]["playbook_network"] > prior["network_scores"]["event_timeline_network"]
    assert "AI Use Cases for Diplomats" in md


def test_build_knowledge_plan_can_use_cached_metadata_prior(tmp_path: Path) -> None:
    run_dir = tmp_path / "Ambiguous Book"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "title": "A Framework for Action",
                        "markdown": (
                            "# A Framework for Action\n\n"
                            "This chapter discusses a framework.\n\n"
                            "The text uses examples and a guide for practice.\n\n"
                            "The method applies to teams.\n\n"
                            "The strategy becomes an action plan.\n"
                        ),
                    }
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )
    knowledge_dir = run_dir / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "metadata-prior.json").write_text(
        json.dumps(
            {
                "schema": "book_weaver_metadata_prior_v1",
                "query": {"query": "Ambiguous Book", "title_hint": "Ambiguous Book"},
                "primary_network_model": "playbook_network",
                "secondary_network_models": [],
                "confidence": 0.8,
                "network_scores": {
                    "argument_network": 0,
                    "concept_network": 0,
                    "event_timeline_network": 0,
                    "playbook_network": 120,
                    "narrative_network": 0,
                    "faceted_index_network": 0,
                },
                "records": [],
            }
        ),
        encoding="utf-8",
    )

    paths = build_knowledge_plan(run_dir, metadata_prior="auto")
    plan = json.loads(paths["plan"].read_text(encoding="utf-8"))

    assert plan["metadata_prior"]["primary_network_model"] == "playbook_network"
    assert plan["algorithm_candidate"]["metadata_prior_mode"] == "auto"
    assert plan["algorithm_candidate"]["network_scores"]["playbook_network"] > 0


def test_parse_user_review_answers_extracts_structural_inputs() -> None:
    answers = """
    组织方式：混合，偏 event_timeline_network + concept_network
    必须保留：chronology, glossary, appendix, illustrations, tables
    可以跳过：copyright, index, publisher pages
    参考材料：
    这本书的一篇书评认为它讨论民族祖先、文化遗产建构和地方竞争。
    https://example.test/review
    """

    review = parse_user_review_answers(answers)

    assert review["network_override"] == "event_timeline_network"
    assert "concept_network" in review["secondary_network_models"]
    assert {"chronology", "glossary", "appendix", "illustrations", "tables"}.issubset(
        set(review["preserve_content_types"])
    )
    assert {"copyright", "index", "publisher_pages"}.issubset(set(review["skip_content_types"]))
    assert len(review["references"]) == 2


def test_apply_user_review_updates_plan_without_user_editing_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "Review Book"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "title": "Chapter 1 Practical Guide",
                        "markdown": "# Chapter 1 Practical Guide\n\nA guide with a tool.\n\nA practice.\n\nA case.\n\nAn action.\n",
                    },
                    {
                        "index": 2,
                        "title": "Appendix: Chronology",
                        "markdown": "# Appendix: Chronology\n\n1900 event.\n\n1910 event.\n",
                    },
                    {"index": 3, "title": "Index", "markdown": "# Index\n\nA 1\n"},
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )
    build_knowledge_plan(run_dir)
    answers = tmp_path / "answers.txt"
    answers.write_text(
        """
        organization: event_timeline_network
        preserve: appendix, chronology
        skip: index
        references:
        External review says the chronology is central context, not a throwaway appendix.
        """,
        encoding="utf-8",
    )

    paths = apply_user_review(run_dir, answers)
    plan = json.loads(paths["plan"].read_text(encoding="utf-8"))
    review = json.loads(paths["review"].read_text(encoding="utf-8"))
    roles = {chapter["title"]: chapter["role"] for chapter in plan["final_plan"]["chapter_roles"]}
    md = paths["markdown"].read_text(encoding="utf-8")

    assert review["network_override"] == "event_timeline_network"
    assert plan["final_plan"]["primary_network_model"] == "event_timeline_network"
    assert roles["Appendix: Chronology"] == "preserve"
    assert roles["Index"] == "skip"
    assert (run_dir / "knowledge" / "reference-prior.json").exists()
    assert "User Review" in md


def test_build_reader_brief_writes_user_facing_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "Brief Book"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "chapter_id": "ch-001-main",
                        "title": "1. Main Argument",
                        "markdown": (
                            "# 1. Main Argument\n\n"
                            "This book argues that institutions need evidence and judgment.\n\n"
                            "Therefore the chapter develops a claim about public reason.\n"
                        ),
                    },
                    {"index": 2, "chapter_id": "ch-002-index", "title": "Index", "markdown": "# Index\n\nA 1.\n"},
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )
    build_knowledge_plan(run_dir)

    paths = build_reader_brief(run_dir)
    md = paths["markdown"].read_text(encoding="utf-8")
    html = paths["html"].read_text(encoding="utf-8")
    template = paths["template"].read_text(encoding="utf-8")

    assert "Reader Brief" in md
    assert "Book Frame" in md
    assert "Chapter Cards" in md
    assert "ch-001-main" in md
    assert "This book argues" in md
    assert "<html" in html
    assert "Reader Feedback" in template


def test_ingest_reader_feedback_preserves_and_aligns_feedback(tmp_path: Path) -> None:
    run_dir = tmp_path / "Feedback Book"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "chapter_id": "ch-001-main",
                        "title": "Main Argument",
                        "markdown": (
                            "# Main Argument\n\n"
                            "This book argues that public reason needs evidence.\n\n"
                            "A court report supports the institutional claim.\n"
                        ),
                    }
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )
    build_knowledge_package(run_dir)
    feedback = tmp_path / "feedback.md"
    feedback.write_text(
        """
        # Reader Feedback

        ## Reading Goals

        - Focus on the institutional claim.

        ## Highlights

        - Chapter: ch-001-main
          Excerpt: This book argues that public reason needs evidence.
          Note: This should seed the first claim.

        ## External References

        - https://example.test/review
        """,
        encoding="utf-8",
    )

    paths = ingest_reader_feedback(run_dir, feedback)
    raw = json.loads(paths["raw"].read_text(encoding="utf-8"))
    aligned = json.loads(paths["aligned"].read_text(encoding="utf-8"))

    assert paths["raw_markdown"].is_file()
    assert raw["schema"] == "book_weaver_reader_feedback_raw_v1"
    assert {obj["kind"] for obj in raw["objects"]}.issuperset({"reading_goal", "highlight", "external_reference"})
    assert aligned["summary"]["aligned"] >= 1
    highlight = next(obj for obj in aligned["objects"] if obj["kind"] == "highlight")
    assert highlight["alignment"]["status"] == "aligned"
    assert "ch-001-main" in highlight["alignment"]["chapter_ids"]
    assert "ch-001-main-u0002" in highlight["alignment"]["unit_ids"]


def test_ingest_reader_feedback_absorbs_structural_review_inputs(tmp_path: Path) -> None:
    run_dir = tmp_path / "Feedback Review Book"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "title": "Chapter 1 Practical Guide",
                        "markdown": "# Chapter 1 Practical Guide\n\nA guide with a tool.\n\nA practice.\n\nA case.\n\nAn action.\n",
                    },
                    {
                        "index": 2,
                        "title": "Appendix: Chronology",
                        "markdown": "# Appendix: Chronology\n\n1900 event.\n\n1910 event.\n",
                    },
                    {"index": 3, "title": "Index", "markdown": "# Index\n\nA 1\n"},
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )
    build_knowledge_plan(run_dir)
    feedback = tmp_path / "feedback.md"
    feedback.write_text(
        """
        # Reader Feedback

        ## Frame Corrections

        - This should be event_timeline_network plus concept_network, not only a playbook.

        ## Preserve

        - Preserve the chronology appendix.

        ## Skip

        - Skip the index and publisher pages.

        ## Book-Level Insights

        - The chronology is central context for the book's argument.

        ## External References

        - https://example.test/review
        - External review says the chronology is not a disposable appendix.
        """,
        encoding="utf-8",
    )

    paths = ingest_reader_feedback(run_dir, feedback)
    raw = json.loads(paths["raw"].read_text(encoding="utf-8"))
    plan = json.loads(paths["plan"].read_text(encoding="utf-8"))
    review = json.loads(paths["review"].read_text(encoding="utf-8"))
    roles = {chapter["title"]: chapter["role"] for chapter in plan["final_plan"]["chapter_roles"]}
    aligned = json.loads(paths["aligned"].read_text(encoding="utf-8"))

    assert raw["structural_review"]["network_override"] == "event_timeline_network"
    assert "concept_network" in raw["structural_review"]["secondary_network_models"]
    assert review["network_override"] == "event_timeline_network"
    assert plan["final_plan"]["primary_network_model"] == "event_timeline_network"
    assert roles["Appendix: Chronology"] == "preserve"
    assert roles["Index"] == "skip"
    assert paths["reference_prior"].exists()
    assert any(obj["kind"] == "book_level_user_insight" for obj in aligned["objects"])


def test_phase_b1_1_example_demo_is_runnable(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    source_run = repo_root / "examples" / "phase_b1_1" / "run"
    source_feedback = repo_root / "examples" / "phase_b1_1" / "feedback.md"
    run_dir = tmp_path / "run"
    shutil.copytree(source_run, run_dir)

    build_knowledge_plan(run_dir)
    brief_paths = build_reader_brief(run_dir)
    feedback_paths = ingest_reader_feedback(run_dir, source_feedback)

    plan = json.loads((run_dir / "knowledge" / "plan.json").read_text(encoding="utf-8"))
    review = json.loads(feedback_paths["review"].read_text(encoding="utf-8"))
    aligned = json.loads(feedback_paths["aligned"].read_text(encoding="utf-8"))
    roles = {chapter["title"]: chapter["role"] for chapter in plan["final_plan"]["chapter_roles"]}

    assert brief_paths["markdown"].is_file()
    assert brief_paths["html"].is_file()
    assert plan["final_plan"]["primary_network_model"] == "event_timeline_network"
    assert "concept_network" in plan["final_plan"]["secondary_network_models"]
    assert review["preserve_content_types"] == ["appendix", "chronology"]
    assert review["skip_content_types"] == ["index", "publisher_pages"]
    assert roles["Appendix: Chronology"] == "preserve"
    assert roles["Index"] == "skip"
    assert aligned["summary"]["total"] > 0


def test_build_knowledge_extraction_runs_argument_network_only(tmp_path: Path) -> None:
    run_dir = tmp_path / "Argument Book"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {"index": 1, "title": "Copyright Page", "markdown": "# Copyright Page\n\nCopyright.\n"},
                    {
                        "index": 2,
                        "chapter_id": "ch-002-argument",
                        "title": "1. Why Institutions Matter",
                        "markdown": (
                            "# 1. Why Institutions Matter\n\n"
                            "This chapter argues that political theory requires a concept of institutional evidence.\n\n"
                            "For example, court records and policy reports show how the claim works in practice.\n\n"
                            "However, the older framework cannot explain the same evidence.\n\n"
                            "Therefore the chapter develops an argument about institutional judgment.\n\n"
                            "In 2024, a university report documented a court case involving institutional judgment.\n"
                        ),
                    },
                    {
                        "index": 3,
                        "chapter_id": "ch-003-related",
                        "title": "2. Institutional Judgment Elsewhere",
                        "markdown": (
                            "# 2. Institutional Judgment Elsewhere\n\n"
                            "This chapter argues that institutional judgment also shapes later policy.\n\n"
                            "The evidence from another court report supports this claim.\n\n"
                            "A 2025 policy case shows the institution changing its rule.\n\n"
                            "Therefore institutional evidence travels across chapters.\n"
                        ),
                    },
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )

    paths = build_knowledge_extraction(run_dir, network_model="argument_network")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    nodes = json.loads(paths["nodes"].read_text(encoding="utf-8"))
    edges = json.loads(paths["edges"].read_text(encoding="utf-8"))
    report = paths["report"].read_text(encoding="utf-8")

    assert manifest["status"] == "completed"
    assert manifest["network_model"] == "argument_network"
    node_types = {node["node_type"] for node in nodes}
    assert node_types.issuperset({"question", "claim", "evidence", "concept"})
    assert node_types & {"fact", "data_point", "case"}
    assert any(edge["edge_type"] == "supports" for edge in edges)
    assert any(edge["edge_type"] in {"cross_supports", "relates_to_concept"} for edge in edges)
    assert all(node["source"]["chapter_id"] for node in nodes)
    assert "Knowledge Extraction Report" in report
    assert "Chapter Argument Maps" in report
    assert "Claims and Support" in report
    assert "Facts / Data / Cases" in report
    assert "Cross-Chapter Links" in report
    assert "Edge Index" in report


def test_build_knowledge_extraction_does_not_apply_argument_logic_to_other_models(tmp_path: Path) -> None:
    run_dir = tmp_path / "Historical Book"
    run_dir.mkdir()
    (run_dir / "book.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "index": 1,
                        "title": "A Historical Event",
                        "markdown": "# A Historical Event\n\nThe event happened in 1900.\n\nActors moved across the empire.\n",
                    }
                ],
                "assets": [],
            }
        ),
        encoding="utf-8",
    )

    paths = build_knowledge_extraction(run_dir, network_model="event_timeline_network")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    nodes = json.loads(paths["nodes"].read_text(encoding="utf-8"))
    report = paths["report"].read_text(encoding="utf-8")

    assert manifest["status"] == "unsupported"
    assert nodes == []
    assert "no extractor yet" in report
